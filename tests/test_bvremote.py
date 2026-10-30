"""버드뷰 리모컨 장치 — CC1101을 가짜로 바꿔 보내는 패킷·seq·두 번 보내기를 본다."""

import asyncio

from rvnode.config import parse
from rvnode.frames import birdview_rf as bv
from rvnode.ha import all_configs
from rvnode.node import Node

from .conftest import FakeBus


class FakeRadio:
    def __init__(self):
        self.sent = []

    def send(self, data):
        self.sent.append(data)
        return True

    def strobe(self, cmd):
        self.strobes = getattr(self, "strobes", []) + [cmd]


def make():
    node = Node(parse({"node": {"device_id": "9FCEE4", "base": "rv/radxa"},
                       "devices": [{"type": "bvremote", "slug": "bvremote", "label": "Bird view remote"}]}))
    node.bus = FakeBus()
    return node.devices[0]


def test_buttons_send_remote_packets():
    d = make()
    d.radio = FakeRadio()

    async def go():
        assert await d.write_control("left", "PRESS")
        assert await d.write_control("click", "PRESS")
        assert not await d.write_control("nope", "PRESS")

    asyncio.run(go())
    sent = d.radio.sent
    assert len(sent) == 3                                   # 왼쪽 1번 + 누르기 2번
    assert bv.decode(sent[0]) == (0x1E, bv.LEFT)
    assert bv.decode(sent[1]) == bv.decode(sent[2]) == (0x1D, bv.CLICK)   # seq는 송신(버튼)마다 하나씩 준다
    assert d.radio.strobes[-1] == 0x36                      # 보낸 뒤 IDLE (수신 FIFO가 넘치지 않게)
    st = d.state()
    assert st["sent"] == 2 and st["last"]["action"] == "click" and st["last"]["ok"] and st["id"] == "A48A5E05"


def test_no_radio_fails_and_discovery_has_buttons():
    d = make()
    assert not asyncio.run(d.write_control("left", "PRESS"))
    topics = [t for t, _ in all_configs("homeassistant", d.node.info, d)]
    for n in ("left", "right", "click", "long_press"):
        assert f"homeassistant/button/9FCEE4_bvremote/{n}/config" in topics
