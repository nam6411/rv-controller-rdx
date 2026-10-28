"""ESP test/test_frames/test_main.cpp의 MACH·Aventa 벡터(현장에서 주고받은 프레임)를 옮겼다."""

import pytest

from rvnode.frames import aventa as av
from rvnode.frames import mach


def test_build_read_lin():
    # 2026-10-04 Mac 시험: 할당 ID 1, msg 40, LIN_2, frame 0x10
    f = mach.build_request(mach.LIN_2, 1, 40, mach.CMD_READ_LIN, mach.read_lin_payload(0x10))
    assert f == bytes.fromhex("0102522923343025313024433a454167554156343d0d3004")
    f = mach.build_request(mach.LIN_1, 1, 40, mach.CMD_READ_LIN, mach.read_lin_payload(0x1B))
    assert f == bytes.fromhex("010052f923343025313024433a477767554156343d0d3d04")


def test_parse_reply_and_ebl():
    f = bytes.fromhex("01426188213430254f4b0d233430253138244a3a434644566b49514141502f773476413d0d4704")
    assert mach.find_mux_frame(f) == (0, len(f))
    dlp = mach.unmux(f)
    assert dlp
    kind, data = mach.parse_reply(dlp, 40)
    assert kind == mach.REPLY_DATA and len(data) == 11
    e = mach.Ebl()
    assert mach.decode_ebl_status(data, e)
    assert (e.inside_c, e.outside_c, e.fresh_pct, e.int_light, e.pump, e.panel) == (32, 26, 0, False, False, True)
    assert mach.parse_reply(dlp, 41)[0] == mach.REPLY_NONE   # 다른 msgId는 무시


def test_reply_kinds():
    assert mach.parse_reply(b"!7%OK\r", 7)[0] == mach.REPLY_OK
    assert mach.parse_reply(b"!7%TO\r", 7)[0] == mach.REPLY_TIMEOUT
    assert mach.parse_reply(b"!7%E2\r", 7)[0] == mach.REPLY_ERROR


def test_ebl_battery_and_control():
    e = mach.Ebl()
    mach.decode_ebl_battery(bytes.fromhex("08d62c05085385987effff"), e)
    assert e.battery_installed
    assert e.battery_v == pytest.approx(13.64, abs=0.005)
    assert e.battery_a == pytest.approx(-3.59, abs=0.005)
    assert mach.ebl_control(mach.EBL_INT_LIGHT, True) == bytes.fromhex("ffffffffff0202ff")
    assert mach.ebl_control(mach.EBL_PUMP, False) == bytes.fromhex("ffffffffff0006ff")
    assert mach.ebl_control(mach.EBL_PANEL, True) == bytes.fromhex("ffffffffff0002ff")
    assert mach.ebl_control(mach.EBL_PANEL, False) == bytes.fromhex("ffffffffff0505ff")
    assert mach.ebl_soc_from_voltage(13.64) == 100 and mach.ebl_soc_from_voltage(11.9) == 50


def test_accel_decode():
    a = mach.decode_accel(bytes([0x00, 0x00, 0xFC, 0x18, 0x40, 0x00, 61]))
    assert a.scale == 61
    assert (a.x, a.y, a.z) == pytest.approx((0.0, -0.061, 0.999424), abs=1e-4)
    a = mach.decode_accel(bytes([0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00]))
    assert a.scale == 256 and a.x == pytest.approx(256e-6)
    assert mach.decode_accel(bytes(6)) is None


def test_alde_decode_and_control():
    a = mach.Alde()
    assert mach.decode_alde(bytes.fromhex("085b888cfe84a43e290000"), a)
    assert (a.sofa_c, a.bed_ok, a.outside_c, a.set_c) == (28, False, 24, 23)
    assert (a.gas, a.prio_gas, a.panel, a.water, a.heating) == (False, True, True, 1, False)
    on = bytes([0, 0, 0, 0xE4, 0x3E, 0x29, 0, 0])
    assert mach.alde_set_gas(a, True) == on                               # 현장에서 보일러를 켠 값
    assert mach.alde_set_gas(a, False) == bytes([0, 0, 0, 0xA4, 0x3E, 0x29, 0, 0])
    assert mach.alde_set_temp(a, 25)[3] == 0xA8
    assert mach.alde_energy_index(a) == 0
    assert mach.alde_set_energy(a, 1) == on
    c = mach.alde_set_energy(a, 6)
    assert (c[3], c[4]) == (0xE4, 0xBE)
    assert mach.alde_set_water(a, 2)[5] == 0x31
    assert mach.alde_set_water(a, 0)[5] == 0x21


def test_aventa():
    s = av.parse_status(bytes([0xA4, 0x0B, 0x86, 0x0B, 0x72, 0x05, 0x00, 0x00]))
    assert (s.room_c, s.set_c, s.mode, av.FAN_NAMES[s.fan]) == (25.0, 22.0, av.COOL, "mid")
    assert av.power(s, av.COMFORT, False, av.AUTO, 22) == bytes([0x86, 0x0B, 0x71, 0x00, 0xFF, 0xFF, 0x00, 0xFF])
    assert av.power(s, av.COMFORT, True, av.AUTO, 24) == bytes([0x9A, 0x0B, 0x72, 0x07, 0xFF, 0xFF, 0x00, 0xFF])


def test_aventa_settings():
    s = av.parse_status(bytes([0xA4, 0x0B, 0x86, 0x0B, 0x72, 0x05, 0x00, 0x00]))
    assert av.set_temp(s, av.COMFORT, 24) == bytes([0x9A, 0x0B, 0x72, 0x05, 0xFF, 0xFF, 0x00, 0xFF])
    assert av.set_mode(s, av.COMFORT, av.HEAT, 20) == bytes([0x72, 0x0B, 0x71, 0x06, 0xFF, 0xFF, 0x00, 0xFF])
    assert av.set_mode(s, av.COMPACT, av.HEAT, 20) is None
    assert av.set_mode(s, av.COMPACT_PLUS, av.AUTO, 20) is None
    out = av.set_fan(s, av.COMFORT, av.FAN_NIGHT, 22)
    assert (out[2], out[3]) == (av.FAN_NIGHT, av.COOL)
    a = av.Status(**{**s.__dict__, "mode": av.AUTO})
    assert av.set_fan(a, av.COMFORT, av.FAN_NIGHT, 22) is None
    assert av.set_fan(a, av.ECO, av.FAN_NIGHT, 22) is not None
    off = av.Status(**{**s.__dict__, "mode": av.OFF, "raw": s.raw[:5] + b"\x00" + s.raw[6:]})
    assert av.set_fan(off, av.COMFORT, av.FAN_HIGH, 23) == bytes([0x90, 0x0B, 0x73, 0x05, 0xFF, 0xFF, 0x00, 0xFF])
    c = av.Status(**{**s.__dict__, "raw": s.raw[:6] + b"\x5a" + s.raw[7:]})
    assert av.set_temp(c, av.COMFORT, 22)[6] == 0x5A
    assert av.set_temp(c, av.ECO, 22)[6] == 0x00


def test_level_text():
    assert mach.low_side(0.2, "뒤가", "앞이") == "수평"
    assert mach.low_side(1.25, "뒤가", "앞이") == "뒤가 1.2° 낮음" or mach.low_side(1.25, "뒤가", "앞이") == "뒤가 1.3° 낮음"
    up, deg = mach.tilt((0.017, -0.035, 0.999))
    assert up == 2 and deg[0] == pytest.approx(0.975, abs=0.01)
