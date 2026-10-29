"""BLE 장치들이 함께 쓰는 것. BlueZ는 같은 프로세스의 스캔이 겹치거나 연결이 겹치면 InProgress로 거절한다 —
스캐너는 하나만 켜 두고(TPMS는 상시, JBD·MACH는 찾는 동안만 구독) 연결은 한 번에 하나씩 한다.

이 보드(Rockchip 커널 6.1.84 + BlueZ 5.66, AIC8800 UART)는 LE 연결을 만들 때 스캔을 끄고 다시 켜지 않는다.
BlueZ는 계속 Discovering = yes라고 답해서 광고가 그냥 끊긴다 (2026-10-29 btmon으로 확인: 연결 뒤 120초 광고 0개).
그래서 연결이 끝나면(성공·실패 모두) 스캔을 껐다 켜고, 스캔 중 SILENT_S 동안 광고가 하나도 없으면 다시 켠다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Callable

log = logging.getLogger("rvnode.ble")

connect_lock = asyncio.Lock()
_now = time.monotonic  # 시험에서 바꾼다
SILENT_S = 30      # 스캔 중 이만큼 광고가 없으면 스캔을 다시 켠다
WATCH_S = 10
Listener = Callable[[Any, Any], None]  # (BLEDevice, AdvertisementData)


class SharedScanner:
    def __init__(self) -> None:
        self._listeners: set[Listener] = set()
        self._scanner = None
        self._lock = asyncio.Lock()
        self._watch_task: asyncio.Task | None = None
        self.last_advert = 0.0      # monotonic
        self.adverts = 0
        self.restarts = 0
        self._quiet_restarts = 0    # 광고 없이 연달아 다시 켠 횟수 — 로그를 줄이려고

    @property
    def running(self) -> bool:
        return self._scanner is not None

    def _dispatch(self, dev, adv) -> None:
        self.last_advert = _now()
        self.adverts += 1
        self._quiet_restarts = 0
        for cb in list(self._listeners):
            try:
                cb(dev, adv)
            except Exception:
                log.exception("advert listener failed")

    async def add(self, cb: Listener) -> None:
        self._listeners.add(cb)
        await self.ensure()

    async def remove(self, cb: Listener) -> None:
        self._listeners.discard(cb)
        if not self._listeners:
            await self._stop()

    async def ensure(self) -> None:
        """구독자가 있는데 꺼져 있으면 켠다. 실패하면 예외 — 부른 쪽이 다음에 다시 부른다."""
        async with self._lock:
            if self._scanner is not None or not self._listeners:
                return
            from bleak import BleakScanner

            # 같은 광고가 되풀이돼도 매번 받는다 (TPMS 값이 그대로여도 수신 시각을 갱신하려고)
            s = BleakScanner(self._dispatch, bluez={"filters": {"DuplicateData": True}})
            await s.start()
            self._scanner = s
            self.last_advert = _now()  # 막 켰다 — SILENT_S를 여기서부터 잰다
            if self._watch_task is None or self._watch_task.done():
                self._watch_task = asyncio.get_running_loop().create_task(self._watch(), name="ble-scan-watch")
            log.info("scanning")

    async def _stop(self) -> None:
        async with self._lock:
            s, self._scanner = self._scanner, None
            if s is not None:
                try:
                    await s.stop()
                except Exception as e:
                    log.warning("scan stop: %s", e)
                log.info("scan stopped")

    async def restart(self, why: str) -> None:
        """스캔을 껐다 켠다 — BlueZ가 StartDiscovery를 다시 받아야 컨트롤러 스캔이 다시 켜진다."""
        if not self._listeners:
            return
        async with self._lock:
            s, self._scanner = self._scanner, None
            if s is not None:
                try:
                    await s.stop()
                except Exception as e:
                    log.debug("scan stop: %s", e)
        self.restarts += 1
        quiet = why.startswith("no adverts")
        self._quiet_restarts = self._quiet_restarts + 1 if quiet else 0
        (log.debug if self._quiet_restarts > 1 else log.info)("scan restart: %s", why)
        try:
            await self.ensure()
        except Exception as e:
            log.warning("scan restart failed: %s", e)  # 감시가 다음에 다시 켠다

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(WATCH_S)
            if not self._listeners or connect_lock.locked():
                continue  # 연결하는 동안은 건드리지 않는다 — 끝나면 connecting()이 다시 켠다
            if self._scanner is None:
                await self.restart("scanner was off")
            elif _now() - self.last_advert >= SILENT_S:
                await self.restart(f"no adverts for {SILENT_S}s")

    def info(self) -> dict[str, Any]:
        return {"scanning": self.running, "listeners": len(self._listeners), "adverts": self.adverts,
                "last_advert_s": round(_now() - self.last_advert, 1) if self.last_advert else None,
                "restarts": self.restarts}

    async def find(self, match: Callable[[Any, Any], bool], timeout: float):
        """광고 중 match가 참인 첫 장치(BLEDevice). 없으면 None."""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()

        def cb(dev, adv):
            if not fut.done() and match(dev, adv):
                fut.set_result(dev)

        await self.add(cb)
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            await self.remove(cb)


scanner = SharedScanner()


@asynccontextmanager
async def connecting():
    """연결은 한 번에 하나씩. 끝나면(성공·실패 모두) 스캔을 다시 켠다 — 모듈 설명의 커널 문제."""
    async with connect_lock:
        try:
            yield
        finally:
            await scanner.restart("after connect")
