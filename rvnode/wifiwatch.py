"""Wi-Fi 감시 (root 서비스 rv-wifi-watchdog) — 공유기가 보이는데 못 붙는 상태가 이어지면 드라이버를 다시 올리고, 그래도 안 되면 재부팅.

2026-10-31: AIC8800이 공유기와 한 번 끊기면 다시 붙지 못하고(인증 시간 초과 → ASSOC-REJECT 반복) 재부팅해야만 붙었다.
rvnode와 따로 root로 돈다 — rvnode가 죽어도 돌아야 하고, 드라이버·재부팅은 root가 필요하다.

- 정상: wlan0이 공유기에 붙어 게이트웨이 ping이 된다, 또는 폴백 AP가 떠 있다(rvnode가 일부러 켠 것).
- 저장된 공유기가 하나도 안 보이면 고칠 것이 없다 — 기다린다 (공유기가 꺼진 곳에서 재부팅을 되풀이하지 않게).
- 보이는데 STUCK_S 넘게 못 붙으면 드라이버(aic8800_fdrv)를 다시 올린다. 그 뒤로도 STUCK_S 넘게 못 붙으면 재부팅.
- 재부팅은 부팅 뒤 MIN_UPTIME_S가 지났고 지난 감시 재부팅에서 REBOOT_GAP_S가 지났을 때만.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from .wifi import AP_CON, Nmcli

log = logging.getLogger("rvnode.wifiwatch")

IFACE = "wlan0"
DRIVER = "aic8800_fdrv"            # 올라온 모듈 이름 (rmmod)
DRIVER_FILE = "aic8800_fdrv_sdio"  # dkms 파일 이름 (modprobe) — modprobe -r aic8800_fdrv는 "not found" (2026-10-31 실측)
TICK_S = 15
STUCK_S = 300
MIN_UPTIME_S = 600
REBOOT_GAP_S = 1800
STAMP = os.path.join(os.environ.get("STATE_DIRECTORY", "/var/lib/rv-wifi-watchdog"), "last_reboot")


class System:
    """실제 보드. 시험에서는 같은 메서드를 가진 가짜로 바꾼다."""

    def __init__(self, iface: str = IFACE):
        self.nm = Nmcli(iface)
        self.iface = iface

    async def state(self) -> str:
        """ok | absent (저장된 공유기가 안 보임) | stuck (보이는데 못 붙음)"""
        st = await self.nm.status()
        if st["mode"] == "ap":
            return "ok"
        if st["mode"] == "sta" and await self._gateway_ok():
            return "ok"
        saved = {c["ssid"] for c in await self.nm.saved()}
        vis = await self.nm.scan(False) or []
        return "stuck" if saved & {n["ssid"] for n in vis} else "absent"

    async def _gateway_ok(self) -> bool:
        out = await self.nm.run("ip", "-4", "route", "show", "default", "dev", self.iface, timeout=5)
        parts = out.split()
        if "via" not in parts:
            return False
        gw = parts[parts.index("via") + 1]
        for _ in range(3):
            try:
                await self.nm.run("ping", "-c", "1", "-W", "2", gw, timeout=5)
                return True
            except RuntimeError:
                pass
        return False

    async def reload_driver(self) -> None:
        await self.nm.run("rmmod", DRIVER, timeout=30)
        await asyncio.sleep(2)
        await self.nm.run("modprobe", DRIVER_FILE, timeout=30)

    def uptime(self) -> float:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])

    def last_reboot(self) -> float:
        try:
            with open(STAMP) as f:
                return float(f.read().strip() or 0)
        except (OSError, ValueError):
            return 0.0

    async def reboot(self) -> None:
        with open(STAMP, "w") as f:
            f.write(str(time.time()))
        os.sync()
        await self.nm.run("systemctl", "reboot", timeout=30)


class Watch:
    def __init__(self, sys: System, clock=time.monotonic, wall=time.time):
        self.sys, self.clock, self.wall = sys, clock, wall
        self.since: float | None = None   # 막힌 상태가 시작된 때 (드라이버를 다시 올리면 다시 센다)
        self.reloaded = False

    async def tick(self) -> None:
        try:
            st = await self.sys.state()
        except Exception as e:  # nmcli가 잠깐 멈춰도 감시는 계속
            log.warning("state: %s", e)
            return
        now = self.clock()
        if st != "stuck":
            if self.since is not None:
                log.warning("wifi: %s — 감시 초기화", "다시 붙음" if st == "ok" else "공유기가 안 보임")
            self.since, self.reloaded = None, False
            return
        if self.since is None:
            self.since = now
            log.warning("wifi: 공유기가 보이는데 못 붙음 — %d초 뒤 %s", STUCK_S, "재부팅" if self.reloaded else "드라이버 다시 올림")
            return
        if now - self.since < STUCK_S:
            return
        if not self.reloaded:
            log.warning("wifi: %d초째 못 붙음 — %s 다시 올림", now - self.since, DRIVER)
            self.reloaded, self.since = True, now
            try:
                await self.sys.reload_driver()
            except Exception as e:
                log.warning("reload: %s", e)
            return
        if self.sys.uptime() < MIN_UPTIME_S or self.wall() - self.sys.last_reboot() < REBOOT_GAP_S:
            return  # 재부팅을 되풀이하지 않는다 — 다음 기회까지 기다린다
        log.warning("wifi: 드라이버를 다시 올려도 %d초째 못 붙음 — 재부팅", now - self.since)
        await self.sys.reboot()


async def main() -> None:
    w = Watch(System())
    while True:
        await w.tick()
        await asyncio.sleep(TICK_S)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
