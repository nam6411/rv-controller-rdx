"""ESP test_main.cpp의 TPMS 벡터(ra6070/BLE-TPMS 예시 패킷)와 장치 state."""

from types import SimpleNamespace

import pytest

from rvnode.config import parse
from rvnode.frames import tpms
from rvnode.node import Node

from .conftest import FakeBus

MD = bytes([0x00, 0x01, 0x80, 0xEA, 0xCA, 0x10, 0x8A, 0x78, 0xE3, 0x6D, 0x00, 0x00, 0xE6, 0x0A, 0x00, 0x00, 0x5B, 0x00])


def test_zeepin_parse():
    r = tpms.parse_zeepin(MD)
    assert (r.sensor_no, r.battery, r.no_pressure, r.mac) == (1, 91, False, "80:ea:ca:10:8a:78")
    assert r.kpa == pytest.approx(28.131) and r.temp_c == pytest.approx(27.9)
    md2 = bytearray(MD)
    md2[2] = 0x82
    md2[8:12] = (137895).to_bytes(4, "little")
    md2[12:16] = (-550).to_bytes(4, "little", signed=True)
    r = tpms.parse_zeepin(bytes(md2))
    assert r.sensor_no == 3 and tpms.kpa_to_psi(r.kpa) == pytest.approx(20.0, abs=0.05) and r.temp_c == -5.5
    md2[1] = 0x02
    assert tpms.parse_zeepin(bytes(md2)) is None
    assert tpms.parse_zeepin(MD[:10]) is None
    # bleak는 앞 2바이트를 제조사 ID(LE)로 떼어 준다: 00 01 → 0x0100
    assert tpms.from_manufacturer_data({0x0100: MD[2:]}).mac == "80:ea:ca:10:8a:78"


def test_parse_mac():
    assert tpms.parse_mac("80:EA:CA:10:8A:78") == "80:ea:ca:10:8a:78"
    assert tpms.parse_mac("80eaca108a78") == "80:ea:ca:10:8a:78"
    assert tpms.parse_mac("") is None
    assert tpms.parse_mac("80:ea:ca") is None
    assert tpms.parse_mac("80:ea:ca:10:8a:7g") is None


def test_device_state_matches_esp():
    cfg = parse({"node": {"device_id": "9FCEE4", "base": "rv/mach"},
                 "devices": [{"type": "tpms", "slug": "tpms", "params": {"sensors": [
                     {"mac": "80:EA:CA:10:8A:78", "name": "caravan_left", "label": "Caravan Left Tyre"},
                     {"mac": "", "name": "tow_air", "label": "Tow Air Suspension", "unit": "psi"},
                     {"mac": "x", "name": "Bad Name"}]}}]})
    node = Node(cfg)
    node.bus = FakeBus()
    d = node.devices[0]
    assert d.prefix == "rv/mach/tpms"
    assert [s.key for s in d.sensors] == ["caravan_left_pressure", "caravan_left_temp_c", "caravan_left_battery",
                                         "tow_air_pressure", "tow_air_temp_c", "tow_air_battery"]
    assert d.key_sensors == "caravan_left_pressure,tow_air_pressure"
    t = [100.0]
    d.clock = lambda: t[0]
    d.on_advert(None, SimpleNamespace(manufacturer_data={0x0100: MD[2:]}, rssi=-80))
    other = bytearray(MD)
    other[2] = 0x83
    d.on_advert(None, SimpleNamespace(manufacturer_data={0x0100: bytes(other[2:])}, rssi=-90))
    d.on_advert(None, SimpleNamespace(manufacturer_data={0x004C: b"\x02\x15"}, rssi=-50))  # 다른 광고는 무시
    t[0] = 130.0
    s = d.state()
    assert s["adverts"] == 2
    cl = s["caravan_left"]
    assert cl["online"] and cl["age_s"] == 30 and cl["unit"] == "bar" and cl["pressure"] == pytest.approx(0.281, abs=1e-3)
    assert (cl["battery"], cl["sensor_no"], cl["rssi"]) == (91, 1, -80)
    assert s["tow_air"] == {"mac": "00:00:00:00:00:00", "online": False}
    assert [u["sensor_no"] for u in s["unassigned"]] == [4]
    t[0] = 1100.0
    assert d.state()["caravan_left"]["online"] is False   # 15분 소식 없으면
