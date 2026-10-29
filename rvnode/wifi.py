"""Wi-Fi: 공유기(STA) ↔ 폴백 AP. ESP 브로커와 같은 규칙 (docs/29 §3.4, rv-controller docs/15 §5·§6).

- 부팅 때 저장된 공유기가 하나도 안 보이면 바로 AP. 공유기가 `after_s`(60초) 넘게 끊기면 AP.
- AP 상태에서 `rescan_s`(30초)마다 공유기를 찾아 보이면 AP를 내리고 공유기로 붙는다. 못 붙으면 AP로 돌아온다.
  드라이버가 AP 중 스캔을 못 하면 `sta_retry_s`마다 공유기에 그냥 붙어 본다.
- AP와 STA를 동시에 띄우지 않는다. 노드는 폴백 SSID에 붙으면 게이트웨이(address)를 브로커로 쓴다.
- 웹에서 AP를 직접 켜면 HOLD_S 동안은 공유기가 보여도 내려가지 않는다 — 설정하는 사람이 끊기지 않게.

NetworkManager(nmcli)로 한다. AP 프로필은 `rv-fallback`(ipv4.method shared = NM이 DHCP를 준다).
공유기 비밀번호는 NM 프로필에만 두고 rvnode 설정 파일에는 넣지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

from .config import FallbackConfig

log = logging.getLogger("rvnode.wifi")

AP_CON = "rv-fallback"
TICK_S = 5
BOOT_GRACE_S = 20     # 부팅 직후 NM이 공유기에 붙어 볼 시간
JOIN_TIMEOUT_S = 40   # AP를 내리고 공유기에 붙기까지 기다리는 시간
HOLD_S = 600          # 웹에서 AP를 켠 뒤 자동으로 내려가지 않는 시간


class WifiUnavailable(RuntimeError):
    """nmcli가 없다 (Mac 작업대) 또는 NM이 돌지 않는다."""


def split_terse(line: str) -> list[str]:
    """nmcli -t 한 줄을 ':'로 나눈다. 값 안의 ':'·'\\'는 '\\:'·'\\\\'로 이스케이프돼 있다."""
    out, cur, esc = [], [], False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def parse_fields(text: str) -> dict[str, str]:
    """nmcli -m multiline -t (`이름:값` 줄들) → dict. 같은 이름이 여러 번이면 첫 값 (IP4.ADDRESS[1] 등)."""
    d: dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            d.setdefault(k.split("[")[0], v.replace("\\:", ":").replace("\\\\", "\\"))
    return d


def parse_iw_scan(text: str) -> list[dict[str, Any]]:
    """`iw dev <if> scan` → [{ssid, signal}] (dBm)."""
    nets: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for line in text.splitlines():
        s = line.strip()
        if line.startswith("BSS "):
            cur = {"ssid": "", "signal": None}
            nets.append(cur)
        elif cur is not None and s.startswith("SSID:"):
            cur["ssid"] = s[5:].strip()
        elif cur is not None and s.startswith("signal:"):
            try:
                cur["signal"] = round(float(s.split()[1]))
            except (IndexError, ValueError):
                pass
    return [n for n in nets if n["ssid"]]


def check_network(ssid: str, password: str) -> str:
    if not 1 <= len(ssid.encode()) <= 32:
        return "SSID는 1~32바이트"
    if password and not 8 <= len(password) <= 63:
        return "비밀번호는 8~63자 (열린 망이면 비운다)"
    return ""


class Nmcli:
    def __init__(self, iface: str):
        self.iface = iface

    async def run(self, *args: str, timeout: float = 30, sudo: bool = False) -> str:
        cmd = (["sudo", "-n"] if sudo else []) + list(args)
        try:
            p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except FileNotFoundError as e:
            raise WifiUnavailable(f"{args[0]} 없음") from e
        try:
            out, err = await asyncio.wait_for(p.communicate(), timeout)
        except asyncio.TimeoutError:
            p.kill()
            raise RuntimeError(f"{' '.join(args[:3])}: timeout") from None
        if p.returncode:
            raise RuntimeError((err or out).decode(errors="replace").strip() or f"{args[0]} exit {p.returncode}")
        return out.decode(errors="replace")

    async def status(self) -> dict[str, str]:
        """{mode: sta|ap|connecting|down, connection, ip}"""
        f = parse_fields(await self.run("nmcli", "-m", "multiline", "-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS",
                                        "device", "show", self.iface, timeout=10))
        code = int((f.get("GENERAL.STATE") or "0").split()[0] or 0)
        con = f.get("GENERAL.CONNECTION", "")
        con = "" if con == "--" else con
        if code == 100:
            mode = "ap" if con == AP_CON else "sta"
        elif 40 <= code < 100:
            mode = "connecting"
        else:
            mode = "down"
        return {"mode": mode, "connection": con, "ip": f.get("IP4.ADDRESS", "").split("/")[0]}

    async def saved(self) -> list[dict[str, str]]:
        """저장된 공유기 프로필 [{name, ssid}] — AP 프로필은 뺀다."""
        out = []
        for line in (await self.run("nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", timeout=10)).splitlines():
            parts = split_terse(line)
            if len(parts) < 2 or parts[1] != "802-11-wireless" or parts[0] == AP_CON:
                continue
            f = parse_fields(await self.run("nmcli", "-m", "multiline", "-t", "-f", "802-11-wireless.ssid,802-11-wireless.mode",
                                            "connection", "show", parts[0], timeout=10))
            if f.get("802-11-wireless.mode", "infrastructure") in ("infrastructure", ""):
                out.append({"name": parts[0], "ssid": f.get("802-11-wireless.ssid", "")})
        return out

    async def scan(self, ap_mode: bool) -> list[dict[str, Any]] | None:
        """주변 Wi-Fi [{ssid, signal, security}]. AP 상태에서 못 찾으면 None."""
        if ap_mode:
            # NM은 AP 모드 장치의 스캔을 받지 않는다 — iw ap-force (sudo 규칙은 deploy.sh)
            try:
                return parse_iw_scan(await self.run("iw", "dev", self.iface, "scan", "ap-force", timeout=20, sudo=True))
            except (RuntimeError, WifiUnavailable) as e:
                log.debug("scan in AP mode failed: %s", e)
                return None
        try:
            text = await self.run("nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "ifname", self.iface,
                                  "--rescan", "yes", timeout=20)
        except RuntimeError:  # 방금 스캔했으면 거절한다 — 캐시로
            text = await self.run("nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "ifname", self.iface,
                                  "--rescan", "no", timeout=10)
        nets: dict[str, dict[str, Any]] = {}
        for line in text.splitlines():
            p = split_terse(line)
            if len(p) < 3 or not p[0]:
                continue
            sig = int(p[1]) if p[1].isdigit() else 0
            if p[0] not in nets or sig > nets[p[0]]["signal"]:
                nets[p[0]] = {"ssid": p[0], "signal": sig, "security": p[2]}
        return sorted(nets.values(), key=lambda n: -n["signal"])

    @staticmethod
    def _security(password: str) -> list[str]:
        if not password:
            return []
        return ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password]

    async def ensure_ap(self, cfg: FallbackConfig) -> None:
        props = ["connection.autoconnect", "no", "802-11-wireless.ssid", cfg.ssid, "802-11-wireless.mode", "ap",
                 "802-11-wireless.band", "bg", "802-11-wireless.channel", "1",
                 "ipv4.method", "shared", "ipv4.addresses", cfg.address, "ipv6.method", "disabled"]
        if cfg.password:  # ESP 노드에 맞춰 WPA2-CCMP만
            props += self._security(cfg.password) + ["wifi-sec.proto", "rsn", "wifi-sec.pairwise", "ccmp", "wifi-sec.group", "ccmp"]
        names = [split_terse(l)[0] for l in (await self.run("nmcli", "-t", "-f", "NAME", "connection", "show", timeout=10)).splitlines()]
        if AP_CON in names:
            await self.run("nmcli", "connection", "modify", AP_CON, *props)
            if not cfg.password:
                try:
                    await self.run("nmcli", "connection", "modify", AP_CON, "remove", "802-11-wireless-security")
                except RuntimeError:
                    pass  # 원래 없었다
        else:
            await self.run("nmcli", "connection", "add", "type", "wifi", "ifname", self.iface, "con-name", AP_CON, *props)

    async def up_ap(self) -> None:
        await self.run("nmcli", "connection", "up", AP_CON, timeout=40)

    async def leave_ap(self, name: str = "") -> None:
        """AP를 내리고 공유기로. name이 있으면 그 프로필, 없으면 NM이 고른다."""
        try:
            await self.run("nmcli", "connection", "down", AP_CON, timeout=20)
        except RuntimeError:
            pass  # AP가 떠 있지 않았다
        if name:
            await self.run("nmcli", "connection", "up", name, timeout=JOIN_TIMEOUT_S)
        else:
            await self.run("nmcli", "device", "connect", self.iface, timeout=JOIN_TIMEOUT_S)

    async def save_network(self, ssid: str, password: str) -> str:
        """공유기 프로필을 만들거나 비밀번호를 바꾼다. 프로필 이름을 돌려준다."""
        same = [n for n in await self.saved() if n["ssid"] == ssid]
        if same:
            name = same[0]["name"]
            await self.run("nmcli", "connection", "modify", name, "connection.autoconnect", "yes", *(self._security(password)))
            if not password:
                try:
                    await self.run("nmcli", "connection", "modify", name, "remove", "802-11-wireless-security")
                except RuntimeError:
                    pass
            return name
        await self.run("nmcli", "connection", "add", "type", "wifi", "ifname", self.iface, "con-name", ssid, "ssid", ssid,
                       "connection.autoconnect", "yes", *self._security(password))
        return ssid

    async def forget(self, ssid: str) -> int:
        n = 0
        for c in await self.saved():
            if c["ssid"] == ssid:
                await self.run("nmcli", "connection", "delete", c["name"])
                n += 1
        return n


class Wifi:
    """공유기 ↔ 폴백 AP 전환. tick()을 TICK_S마다 부른다. 시각은 clock으로 넣어 시험한다."""

    def __init__(self, cfg: FallbackConfig, nm: Nmcli | None = None, clock=time.monotonic):
        self.cfg = cfg
        self.nm = nm or Nmcli(cfg.iface)
        self.clock = clock
        self.available = True
        self.error = ""
        self.state = "start"           # start | sta | ap | joining
        self.status: dict[str, str] = {"mode": "down", "connection": "", "ip": ""}
        self.saved_ssids: dict[str, str] = {}  # 프로필 이름 → SSID
        self.events: deque[tuple[float, str]] = deque(maxlen=12)
        self.started = clock()
        self.down_since: float | None = None
        self.ap_since = 0.0
        self.next_scan = 0.0
        self.next_blind = 0.0
        self.join_deadline = 0.0
        self.hold_until = 0.0
        self.last_scan: list[dict[str, Any]] | None = None
        self._lock = asyncio.Lock()

    def _event(self, msg: str) -> None:
        log.warning("wifi: %s", msg)
        self.events.append((time.time(), msg))

    async def _refresh_saved(self) -> set[str]:
        self.saved_ssids = {c["name"]: c["ssid"] for c in await self.nm.saved()}
        return set(self.saved_ssids.values())

    async def _go_ap(self, why: str) -> None:
        await self.nm.ensure_ap(self.cfg)
        await self.nm.up_ap()
        now = self.clock()
        self.state, self.ap_since = "ap", now
        self.next_scan = now + self.cfg.rescan_s
        self.next_blind = now + self.cfg.sta_retry_s
        self._event(f"폴백 AP {self.cfg.ssid} 켬 — {why}")

    async def _join(self, why: str, name: str = "") -> None:
        self._event(f"공유기로 — {why}")
        self.state = "joining"
        self.join_deadline = self.clock() + JOIN_TIMEOUT_S
        try:
            await self.nm.leave_ap(name)
        except RuntimeError as e:
            log.info("wifi: join: %s", e)  # 결과는 다음 tick이 상태로 판단한다

    async def tick(self) -> None:
        async with self._lock:
            await self._tick()

    async def _tick(self) -> None:
        try:
            self.status = st = await self.nm.status()
        except WifiUnavailable as e:
            self.available, self.error = False, str(e)
            return
        self.available, self.error = True, ""
        now, auto = self.clock(), self.cfg.enabled
        mode = st["mode"]

        if self.state == "start":
            if mode in ("sta", "ap"):
                self.state = mode
                if mode == "ap":
                    self.ap_since, self.next_scan = now, now + self.cfg.rescan_s
                    self.next_blind = now + self.cfg.sta_retry_s
            elif auto and now - self.started >= BOOT_GRACE_S:
                saved = await self._refresh_saved()
                vis = {n["ssid"] for n in (await self.nm.scan(False) or [])}
                if not saved & vis:
                    await self._go_ap("부팅 때 저장된 공유기가 안 보임" if saved else "저장된 공유기 없음")
                else:
                    self.state, self.down_since = "sta", now
            return

        if self.state == "joining":
            if mode == "sta":
                self.state, self.down_since = "sta", None
                self._event(f"공유기 {self.saved_ssids.get(st['connection'], st['connection'])} 연결됨 ({st['ip']})")
            elif now >= self.join_deadline:
                if auto:
                    await self._go_ap("공유기에 붙지 못함")
                else:
                    self.state = "sta"
                    self._event("공유기에 붙지 못함 (폴백 꺼짐)")
            return

        if self.state == "sta":
            if mode == "ap":
                self.state, self.ap_since, self.next_scan = "ap", now, now + self.cfg.rescan_s
                return
            if mode == "sta":
                self.down_since = None
                return
            self.down_since = self.down_since if self.down_since is not None else now
            if auto and now - self.down_since >= self.cfg.after_s:
                await self._go_ap(f"공유기가 {self.cfg.after_s}초 넘게 끊김")
            return

        # ap
        if mode != "ap":
            if mode == "sta":
                self.state, self.down_since = "sta", None
            elif auto or now < self.hold_until:
                self._event("AP가 내려가 있음 — 다시 켠다")
                await self._go_ap("AP 복구")
            return
        if not auto or now < self.hold_until or now < self.next_scan:
            return
        self.next_scan = now + self.cfg.rescan_s
        saved = await self._refresh_saved()
        if not saved:
            return  # 붙을 공유기가 없다 — 웹에서 넣을 때까지 AP
        vis = await self.nm.scan(True)
        if vis is not None:
            self.last_scan = vis
            seen = saved & {n["ssid"] for n in vis}
            if seen:
                await self._join(f"{', '.join(sorted(seen))} 보임")
        elif self.cfg.sta_retry_s and now >= self.next_blind:
            self.next_blind = now + self.cfg.sta_retry_s
            await self._join("AP 중 스캔이 안 돼 그냥 붙어 봄")

    # ---- 웹에서 ----

    async def force_ap(self) -> None:
        async with self._lock:
            await self._go_ap("웹에서 켬")
            self.hold_until = self.clock() + HOLD_S

    async def force_sta(self, ssid: str = "") -> None:
        async with self._lock:
            await self._refresh_saved()
            name = next((n for n, s in self.saved_ssids.items() if s == ssid), "") if ssid else ""
            self.hold_until = 0.0
            await self._join(f"웹에서 {ssid or '공유기'} 연결", name)

    async def scan(self) -> list[dict[str, Any]] | None:
        async with self._lock:
            vis = await self.nm.scan(self.status.get("mode") == "ap")
            if vis is not None:
                self.last_scan = vis
            return vis

    async def networks(self) -> list[str]:
        try:
            return sorted(await self._refresh_saved())
        except WifiUnavailable:
            return []

    def info(self) -> dict[str, Any]:
        now = self.clock()
        st = self.status
        return {
            "available": self.available,
            "error": self.error,
            "enabled": self.cfg.enabled,
            "iface": self.cfg.iface,
            "state": self.state,
            "mode": st.get("mode"),
            "ssid": self.cfg.ssid if st.get("mode") == "ap" else self.saved_ssids.get(st.get("connection", ""), st.get("connection", "")),
            "ip": st.get("ip"),
            "ap_ssid": self.cfg.ssid,
            "ap_address": self.cfg.address.split("/")[0],
            "down_s": int(now - self.down_since) if self.down_since is not None else None,
            "hold_s": max(0, int(self.hold_until - now)),
            "events": [{"t": t, "msg": m} for t, m in self.events],
        }

    async def run(self) -> None:
        if self.cfg.enabled:
            try:
                await self.nm.ensure_ap(self.cfg)
            except WifiUnavailable as e:
                self.available, self.error = False, str(e)
                log.warning("wifi: %s — 폴백 AP 없이 돈다", e)
            except RuntimeError as e:
                log.warning("wifi: AP 프로필을 만들지 못함: %s", e)
        try:
            await self._refresh_saved()
        except (WifiUnavailable, RuntimeError):
            pass
        while True:
            try:
                await self.tick()
            except RuntimeError as e:
                log.warning("wifi: %s", e)
            await asyncio.sleep(TICK_S if self.available else 60)
