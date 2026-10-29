import sys
import types

import pytest

from rvnode import ble


class FakeScanner:
    started = 0
    stopped = 0

    def __init__(self, cb, **kw):
        self.cb = cb

    async def start(self):
        FakeScanner.started += 1

    async def stop(self):
        FakeScanner.stopped += 1


@pytest.fixture
def scanner(monkeypatch):
    monkeypatch.setitem(sys.modules, "bleak", types.SimpleNamespace(BleakScanner=FakeScanner))
    FakeScanner.started = FakeScanner.stopped = 0
    s = ble.SharedScanner()
    monkeypatch.setattr(ble, "scanner", s)
    yield s
    if s._watch_task:
        s._watch_task.cancel()


async def test_connecting_restarts_scan_for_listeners(scanner):
    await scanner.add(lambda d, a: None)          # TPMS처럼 상시 구독
    assert FakeScanner.started == 1
    async with ble.connecting():
        pass
    assert FakeScanner.stopped == 1 and FakeScanner.started == 2 and scanner.running


async def test_connecting_restarts_even_when_connect_fails(scanner):
    await scanner.add(lambda d, a: None)
    with pytest.raises(ConnectionError):
        async with ble.connecting():
            raise ConnectionError("timeout")
    assert FakeScanner.started == 2


async def test_no_listeners_no_restart(scanner):
    async with ble.connecting():
        pass
    assert FakeScanner.started == 0


async def test_watch_restarts_silent_scan(scanner, monkeypatch):
    import asyncio

    now = [1000.0]
    monkeypatch.setattr(ble, "_now", lambda: now[0])
    monkeypatch.setattr(ble, "WATCH_S", 0.001)
    await scanner.add(lambda d, a: None)
    now[0] += ble.SILENT_S - 1
    scanner._dispatch(types.SimpleNamespace(), types.SimpleNamespace())   # 광고가 들어오는 동안은 그대로
    now[0] += ble.SILENT_S - 1
    await asyncio.sleep(0.02)
    assert FakeScanner.started == 1
    now[0] += 1
    await asyncio.sleep(0.02)
    assert FakeScanner.started >= 2 and scanner.restarts >= 1


async def test_watch_leaves_scan_alone_while_connecting(scanner, monkeypatch):
    import asyncio

    now = [1000.0]
    monkeypatch.setattr(ble, "_now", lambda: now[0])
    monkeypatch.setattr(ble, "WATCH_S", 0.001)
    await scanner.add(lambda d, a: None)
    async with ble.connect_lock:
        now[0] += 10 * ble.SILENT_S
        await asyncio.sleep(0.02)
        assert FakeScanner.started == 1
