"""BLE 장치들이 함께 쓰는 것. BlueZ는 같은 프로세스의 스캔이 겹치거나 연결이 겹치면 InProgress로 거절한다 —
스캐너는 하나만 켜 두고(TPMS는 상시, JBD·MACH는 찾는 동안만 구독) 연결은 한 번에 하나씩 한다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

log = logging.getLogger("rvnode.ble")

connect_lock = asyncio.Lock()
Listener = Callable[[Any, Any], None]  # (BLEDevice, AdvertisementData)


class SharedScanner:
    def __init__(self) -> None:
        self._listeners: set[Listener] = set()
        self._scanner = None
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._scanner is not None

    def _dispatch(self, dev, adv) -> None:
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
