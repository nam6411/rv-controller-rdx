"""MACH 장치 모듈을 가짜 BLE 클라이언트(요청 프레임을 풀어 LIN 응답을 돌려줌)로 돌려 본다."""

import asyncio
import base64

import pytest

from rvnode.config import parse
from rvnode.devices import mach as md
from rvnode.frames import aventa as av
from rvnode.frames import mach as m
from rvnode.node import Node

from .conftest import FakeBus

EBL_STATUS = bytes.fromhex("01426188213430254f4b0d233430253138244a3a434644566b49514141502f773476413d0d4704")
EBL_BATTERY = bytes.fromhex("08d62c05085385987effff")
ALDE = bytes.fromhex("085b888cfe84a43e290000")
AVENTA = bytes([0, 0, 0, 0xA4, 0x0B, 0x86, 0x0B, 0x72, 0x05, 0x00, 0x00])
ACCEL = bytes([0x00, 0x00, 0xFC, 0x18, 0x40, 0x00, 61])


def mux(dlp: bytes) -> bytes:
    head = bytes([m.SOH, 0, len(dlp)])
    return head + bytes([m.crc8(head)]) + dlp + bytes([m.crc8(dlp), m.EOT])


def ebl_status_data() -> bytes:
    _, data = m.parse_reply(m.unmux(EBL_STATUS), 40)
    return data


class FakeMach:
    """write_gatt_char로 받은 요청에 응답 알림을 조각내어 보낸다. lin[(iface, frame)] = 데이터 또는 "E"/"TO"."""

    def __init__(self, dev):
        self.dev = dev
        self.is_connected = True
        self.lin = {(m.LIN_2, m.EBL_STATUS): ebl_status_data(), (m.LIN_2, m.EBL_BATTERY): EBL_BATTERY,
                    (m.LIN_1, m.ALDE_STATUS): ALDE, (m.LIN_1, av.FRAME_STATUS): AVENTA}
        self.writes: list[tuple[int, int, bytes]] = []

    async def write_gatt_char(self, uuid, frame, response=True):
        assert uuid == md.NUS_WRITE
        iface = frame[1] >> 1
        dlp = m.unmux(frame)
        ident = dlp[1 : dlp.index(b"%")].decode()
        cmd = dlp[dlp.index(b"$") + 1 : dlp.index(b"$") + 2].decode()
        payload = base64.b64decode(dlp[dlp.index(b":") + 1 : -1])
        if cmd == m.CMD_GET_ACCEL:
            reply = self.data(ident, ACCEL)
        elif cmd == m.CMD_WRITE_LIN:
            self.writes.append((iface, payload[0], payload[2 : 2 + payload[1]]))
            self.on_write(iface, payload[0], payload[2 : 2 + payload[1]])
            reply = f"!{ident}%OK\r".encode()
        else:
            v = self.lin.get((iface, payload[0]), "TO")
            reply = f"!{ident}%{v}\r".encode() if isinstance(v, str) else f"!{ident}%OK\r".encode() + self.data(ident, v)
        out = mux(reply)
        asyncio.get_running_loop().call_soon(self.dev._on_nus, None, bytearray(out[:7]))
        asyncio.get_running_loop().call_soon(self.dev._on_nus, None, bytearray(out[7:]))

    def data(self, ident: str, d: bytes) -> bytes:
        b = base64.b64encode(d)
        return f"#{ident}%{len(b) + 2}$C:".encode() + b + b"\r"

    def on_write(self, iface, frame, ctrl):
        pass

    async def disconnect(self):
        self.is_connected = False


@pytest.fixture
def dev(monkeypatch):
    async def no_sleep(_s):
        return None
    monkeypatch.setattr(md.asyncio, "sleep", no_sleep)
    cfg = parse({"node": {"device_id": "9FCEE4", "base": "rv/radxa"},
                 "devices": [{"type": "mach", "slug": "mach", "label": "MACH"}]})
    node = Node(cfg)
    node.bus = FakeBus()
    d = node.devices[0]
    d.client = FakeMach(d)
    d.assigned_id = 1
    return d


def test_entities_match_esp(dev):
    keys = {s.key for s in dev.sensors}
    assert {"ebl_bat_v", "ebl_soc", "alde_sofa", "level_pitch", "ac_room"} <= keys
    assert [s.name for s in dev.switches] == ["int_light", "ext_light", "water_pump", "heater", "panel", "ac"]
    assert {c.name for c in dev.controls} == {"alde_set_temp", "alde_energy", "alde_water",
                                              "ac_set_temp", "ac_mode", "ac_fan"}


def test_poll_builds_esp_state(dev):
    asyncio.run(dev._poll_once())
    s = dev.state()
    assert s["live"] is True
    assert s["ebl"]["bat_v"] == pytest.approx(13.64) and s["ebl"]["soc"] == 100
    assert (s["ebl"]["inside"], s["ebl"]["outside"], s["ebl"]["panel"]) == (32, 26, True)
    assert s["alde"]["sofa"] == 28 and s["alde"]["water_text"] == "normal" and s["alde"]["energy"] == "off"
    assert s["ac"]["ok"] and s["ac"]["mode"] == "cool" and s["ac"]["room_c"] == 25.0
    assert s["accel"]["ok"] and s["accel"]["up"] == "z" and s["accel"]["roll"] == pytest.approx(-3.5, abs=0.1)
    assert dev.switch_state("heater") is False and dev.switch_state("ac") is True


def test_panel_off_drops_values_but_poll_succeeds(dev):
    dev.client.lin[(m.LIN_2, m.EBL_BATTERY)] = "E2"
    dev.client.lin[(m.LIN_1, m.ALDE_STATUS)] = "E2"
    dev.client.lin[(m.LIN_1, av.FRAME_STATUS)] = "TO"
    asyncio.run(dev._poll_once())
    s = dev.state()
    assert "bat_v" not in s["ebl"] and "alde" not in s
    assert s["ac"] == {"ok": False, "reply": "LIN timeout (C)"}


async def poll_then(dev, fn, *args):
    await dev._poll_once()
    return await fn(*args)


def test_heater_write_rereads(dev):

    def on_write(iface, frame, ctrl):
        if frame == m.ALDE_CONTROL:
            r = bytearray(ALDE)
            r[6] = ctrl[3]
            dev.client.lin[(m.LIN_1, m.ALDE_STATUS)] = bytes(r)
    dev.client.on_write = on_write
    assert asyncio.run(poll_then(dev, dev.write_switch, "heater", True))
    assert dev.client.writes == [(m.LIN_1, m.ALDE_CONTROL, bytes([0, 0, 0, 0xE4, 0x3E, 0x29, 0, 0]))]


def test_ac_write_sent_twice(dev):
    asyncio.run(poll_then(dev, dev.write_switch, "ac", False))
    off = bytes([0x86, 0x0B, 0x71, 0x00, 0xFF, 0xFF, 0x00, 0xFF])
    assert dev.client.writes == [(m.LIN_1, av.FRAME_CONTROL, off)] * 2


def test_switch_rejected_when_panel_off(dev):
    async def go():
        await dev._poll_once()
        dev.ebl.panel = False
        return await dev.write_switch("int_light", True)
    assert not asyncio.run(go())
    assert dev.client.writes == []
