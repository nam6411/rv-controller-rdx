import json

import pytest

from rvnode.config import parse
from rvnode.devices.birdview import Birdview, Logic
from rvnode.node import Node

from .conftest import FakeBus

PINS = {n: {"chip": "gpiochip3", "line": i} for i, n in enumerate(
    ("ign", "left", "right", "reverse", "power", "rear", "left_out", "right_out"))}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def bv():
    cfg = parse({"node": {"device_id": "9FCEE4", "base": "rv/mach"},
                 "devices": [{"type": "birdview", "slug": "birdview",
                              "params": {"backend": "fake", "off_delay_s": 30, "blink_hold_s": 1.5, "pins": PINS}}]})
    node = Node(cfg)
    node.bus = FakeBus()
    dev = node.devices[0]
    dev.clock = Clock()
    dev.open()
    return dev


def outs(dev):
    return {n: dev.lines.values[n] for n in ("power", "rear", "left_out", "right_out")}


def test_ignition_turns_power_on_with_off_delay(bv):
    bv.lines.values["ign"] = True
    bv.step()
    assert outs(bv)["power"] is True
    bv.lines.values["ign"] = False
    bv.clock.t += 29
    bv.step()
    assert outs(bv)["power"] is True          # 시동을 끈 뒤 30초는 더 켠다
    bv.clock.t += 2
    bv.step()
    assert outs(bv)["power"] is False


def test_power_is_or_of_ignition_switch_and_requests(bv):
    bv.step()
    assert outs(bv)["power"] is False
    bv.request_power("video", True)
    bv.step()
    assert outs(bv)["power"] is True
    bv.request_power("video", False)
    bv.step()
    assert outs(bv)["power"] is False         # 요청만 있었으면 지연 없이 끈다
    bv.logic.manual = True
    bv.step()
    assert outs(bv)["power"] is True


def test_triggers_only_while_powered_and_off_order(bv):
    bv.lines.values["reverse"] = True
    bv.step()
    assert outs(bv) == {"power": False, "rear": False, "left_out": False, "right_out": False}  # 버드뷰가 꺼져 있으면 트리거도 안 켠다
    bv.logic.manual = True
    bv.lines.writes.clear()
    bv.step()
    assert bv.lines.writes == [("power", True), ("rear", True)]   # 켤 때는 전원 → 트리거
    bv.logic.manual = False
    bv.lines.writes.clear()
    bv.step()
    assert bv.lines.writes == [("rear", False), ("power", False)]  # 끌 때는 트리거 → 전원


def test_turn_signal_hold_and_hazard(bv):
    bv.logic.manual = True
    bv.lines.values["left"] = True
    bv.step()
    assert bv.view == "left" and outs(bv)["left_out"]
    bv.lines.values["left"] = False            # 깜빡이 꺼진 반 주기
    bv.clock.t += 1.0
    bv.step()
    assert bv.view == "left"
    bv.clock.t += 1.0                          # 마지막 펄스 뒤 2초 > 1.5초
    bv.step()
    assert bv.view == "none" and not outs(bv)["left_out"]
    bv.lines.values["left"] = bv.lines.values["right"] = True   # 비상등
    bv.step()
    assert bv.view == "none"
    bv.lines.values["reverse"] = True          # 후진이 깜빡이보다 먼저
    bv.step()
    assert bv.view == "rear"


def test_manual_mode_overrides_auto():
    lg = Logic()
    lg.manual = True
    lg.mode = "right"
    assert lg.update(0, ign=False, left=True, right=False, reverse=True) == (True, "right")
    lg.mode = "none"
    assert lg.update(0, ign=False, left=False, right=False, reverse=True) == (True, "none")


async def test_commands_and_state(bv):
    assert await bv.write_switch("power", True)
    assert outs(bv)["power"] is True
    assert await bv.write_control("mode", "left")
    assert outs(bv)["left_out"] is True
    assert not await bv.write_control("mode", "sideways")
    await bv.publish_state()
    st = json.loads(bv.node.bus.last("rv/mach/birdview/state")[0])
    assert st["powered"] and st["view"] == "left" and st["mode"] == "left" and st["keep_on"]
    assert bv.node.bus.last("rv/mach/birdview/switch/power/state") == ("ON", True)


def test_close_turns_everything_off(bv):
    bv.logic.manual = True
    bv.lines.values["reverse"] = True
    bv.step()
    lines = bv.lines
    lines.writes.clear()
    bv.close()
    assert lines.writes == [("rear", False), ("power", False)]


def test_discovery_has_binary_sensors(bv):
    from rvnode import ha
    topics = dict(ha.all_configs("homeassistant", bv.node.info, bv))
    ign = topics["homeassistant/binary_sensor/9FCEE4_birdview/ignition/config"]
    assert ign["val_tpl"] == "{{ 'ON' if value_json.in.ign else 'OFF' }}"
    assert ign["def_ent_id"] == "binary_sensor.birdview_ignition"
    assert "homeassistant/select/9FCEE4_birdview/mode/config" in topics


def test_missing_line_is_a_config_error():
    cfg = parse({"node": {"device_id": "X", "base": "rv/x"},
                 "devices": [{"type": "birdview", "slug": "birdview",
                              "params": {"backend": "fake", "pins": {**PINS, "power": {"chip": "gpiochip3", "line": None}}}}]})
    with pytest.raises(ValueError, match="gpioinfo"):
        Node(cfg)
