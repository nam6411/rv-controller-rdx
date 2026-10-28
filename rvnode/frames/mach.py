"""Adria MACH-BASIC BLE 프로토콜 — MUX/DLP 프레임과 장치 해석.
ESP src/modules/mach/MachFrame.h를 옮겼다 (rv-controller docs/19, 공식 앱 디컴파일 + 현장 검증).
"""

from __future__ import annotations

import base64
import binascii
import math
from dataclasses import dataclass, field

# MUX 인터페이스 번호 (앱 I3/f)
LIN_1, LIN_2, MISC = 0, 1, 3
SOH, EOT = 0x01, 0x04
CMD_READ_LIN, CMD_WRITE_LIN, CMD_GET_ACCEL = "C", "B", "@"
LIN_CHECKSUM = 0x14
LIN_TIMEOUT_MS = 350
LIN_REPLY_LEN = 11

EBL_STATUS, EBL_BATTERY, EBL_CONTROL = 0x10, 0x16, 0x0F
ALDE_STATUS, ALDE_CONTROL = 0x1B, 0x1A

ALDE_ENERGY_NAMES = ("off", "gas", "el1", "el2", "el3", "el1_and_gas", "el2_and_gas", "el3_and_gas")
ALDE_WATER_NAMES = ("off", "normal", "boost")


def crc8(d: bytes | bytearray) -> int:
    """CRC-8, 다항식 0xD9, 초기값 0 (앱 G4/a)."""
    c = 0
    for x in d:
        b = x ^ c
        for _ in range(8):
            b = ((b << 1) ^ 0xD9) & 0xFF if b & 0x80 else (b << 1) & 0xFF
        c = b
    return c


def build_request(iface: int, assigned_id: int, msg_id: int, cmd: str, payload: bytes) -> bytes:
    """요청 = MUX( DLP "#<id>%<len>$<cmd>:<base64>\\r" ). assigned_id는 접속 때 CB2D0008로 받은 값."""
    b64 = base64.b64encode(payload)
    dlp = f"#{msg_id % 100}%{len(b64) + 2}${cmd}:".encode() + b64 + b"\r"
    flags, ln = (iface << 1) & 0xFF, len(dlp)
    if assigned_id & 4:
        flags |= 0x01
    if assigned_id & 2:
        ln |= 0x80
    if assigned_id & 1:
        ln |= 0x40
    head = bytes([SOH, flags, ln])
    return head + bytes([crc8(head)]) + dlp + bytes([crc8(dlp), EOT])


def read_lin_payload(frame_id: int) -> bytes:
    return bytes([frame_id, 8, LIN_CHECKSUM, LIN_TIMEOUT_MS >> 8, LIN_TIMEOUT_MS & 0xFF])


def write_lin_payload(frame_id: int, data: bytes) -> bytes:
    return bytes([frame_id, len(data)]) + bytes(data) + bytes([LIN_CHECKSUM, LIN_TIMEOUT_MS >> 8, LIN_TIMEOUT_MS & 0xFF])


def unmux(f: bytes) -> bytes | None:
    """MUX 프레임 하나 → DLP 바이트. 형식·CRC 오류면 None."""
    n = len(f)
    if n < 6 or f[0] != SOH or f[-1] != EOT or f[1] & 0x80:
        return None
    ln = f[2] & 0x3F
    if ln != n - 6 or f[3] != crc8(f[:3]) or f[-2] != crc8(f[4 : 4 + ln]):
        return None
    return bytes(f[4 : 4 + ln])


def find_mux_frame(buf: bytes | bytearray, off: int = 0) -> tuple[int, int] | None:
    """알림 조각을 이어 붙인 버퍼에서 완성된 MUX 프레임 하나 → (start, length). 덜 왔으면 None."""
    n = len(buf)
    for i in range(off, n - 5):
        if buf[i] != SOH:
            continue
        ln = buf[i + 2] & 0x3F
        if i + 6 + ln > n:
            return None
        if buf[i + 5 + ln] == EOT and buf[i + 3] == crc8(buf[i : i + 3]):
            return i, 6 + ln
    return None


REPLY_NONE, REPLY_OK, REPLY_TIMEOUT, REPLY_ERROR, REPLY_DATA = "none", "ok", "timeout", "error", "data"


def parse_reply(d: bytes, msg_id: int) -> tuple[str, bytes]:
    """DLP에서 msg_id 응답: "!id%OK\\r", "!id%TO\\r", "!id%E…\\r", "#id%len$c:base64\\r". 한 DLP에 OK와 Data가 함께 오면 Data가 우선."""
    ident = f"{msg_id % 100}%".encode()
    best = REPLY_NONE
    i, n = 0, len(d)
    while i < n:
        c = d[i]
        if c not in (0x21, 0x23) or d[i + 1 : i + 1 + len(ident)] != ident:
            i += 1
            continue
        p = i + 1 + len(ident)
        end = d.find(b"\r", p)
        if end < 0:
            break
        body = d[p:end]
        if c == 0x23:  # '#'
            colon = body.find(b":")
            if colon < 0:
                i = end + 1
                continue
            try:
                raw = body[colon + 1 :]
                return REPLY_DATA, base64.b64decode(raw + b"=" * (-len(raw) % 4))
            except (binascii.Error, ValueError):
                return REPLY_ERROR, b""
        if body == b"OK":
            best = REPLY_OK
        elif body == b"TO":
            return REPLY_TIMEOUT, b""
        elif body[:1] == b"E":
            return REPLY_ERROR, b""
        i = end + 1
    return best, b""


# ---- 가속도계 (앱 J3/a): X·Y·Z int16 BE + 배율 1바이트(7) 또는 2바이트(8). 값 = raw × 1e-6 × 배율 [g] ----

@dataclass
class Accel:
    valid: bool = False
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    scale: int = 0


def decode_accel(d: bytes) -> Accel | None:
    if len(d) not in (7, 8):
        return None

    def s16(i: int) -> int:
        v = d[i] << 8 | d[i + 1]
        return v - 0x10000 if v & 0x8000 else v

    scale = s16(6) if len(d) == 8 else d[6]
    return Accel(True, s16(0) * 1e-6 * scale, s16(2) * 1e-6 * scale, s16(4) * 1e-6 * scale, scale)


# ---- Schaudt CSV416 (앱 T3/c) — 응답 11바이트, 인덱스는 docs/19 표 기준 ----

def temp_valid(b: int) -> bool:
    return b <= 240


@dataclass
class Ebl:
    status_valid: bool = False
    battery_valid: bool = False
    inside_ok: bool = False
    outside_ok: bool = False
    inside_c: int = 0
    outside_c: int = 0
    fresh_pct: int = 0
    waste_pct: int = 0
    int_light: bool = False
    ext_light: bool = False
    aux: bool = False
    engine: bool = False
    mains: bool = False
    pump: bool = False
    panel: bool = False
    fresh_defect: bool = False
    waste_defect: bool = False
    battery_installed: bool = False
    battery_v: float = 0.0
    battery_a: float = 0.0


def _level(b: int) -> int:
    return min(b & 0x7F, 100)


def decode_ebl_status(r: bytes, e: Ebl) -> bool:
    if len(r) < LIN_REPLY_LEN:
        return False
    e.inside_ok, e.outside_ok = temp_valid(r[3]), temp_valid(r[4])
    if e.inside_ok and r[3] != 250:
        e.inside_c = int((r[3] - 80) / 2)
    if e.outside_ok and r[4] != 250:
        e.outside_c = int((r[4] - 80) / 2)
    e.fresh_pct, e.waste_pct = _level(r[5]), _level(r[6])
    e.int_light, e.ext_light, e.aux = bool(r[8] & 1), bool(r[8] & 2), bool(r[8] & 4)
    e.engine, e.mains, e.pump = bool(r[9] & 0x10), bool(r[9] & 0x08), bool(r[9] & 0x04)
    e.panel = bool(r[9] & 0x02) and not (r[9] & 0x01)
    e.fresh_defect, e.waste_defect = bool(r[10] & 1), bool(r[10] & 2)
    e.status_valid = True
    return True


def decode_ebl_battery(r: bytes, e: Ebl) -> bool:
    if len(r) < LIN_REPLY_LEN:
        return False
    e.battery_installed = not (r[5] == 0xFF and r[6] == 0xFF)
    if e.battery_installed:
        e.battery_v = (r[5] | r[6] << 8) * 0.01 - 327.67
        if r[7] != 0xFF and r[8] != 0xFF:
            e.battery_a = (r[7] | r[8] << 8) * 0.01 - 327.67
    e.battery_valid = True
    return True


def ebl_soc_from_voltage(v: float) -> int:
    """앱의 전압 기반 SOC: 11.0 V = 0 %, 12.8 V = 100 % (G4/c.a)."""
    if v >= 12.8:
        return 100
    if v <= 11.0:
        return 0
    return max(0, min(100, int((v - 11.0) / 1.8 * 100 + 0.5)))


EBL_INT_LIGHT, EBL_EXT_LIGHT, EBL_PUMP, EBL_PANEL = "int_light", "ext_light", "pump", "panel"


def ebl_control(item: str, on: bool) -> bytes:
    """제어 0x0F: 템플릿 FF FF FF FF FF 00 02 FF, 항목 2비트에 켜기 10 / 끄기 01. 패널 끄기는 조명·펌프도 끈다."""
    out = bytearray([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x02, 0xFF])
    code = 0x02 if on else 0x01
    if item == EBL_INT_LIGHT:
        out[5] |= code
    elif item == EBL_EXT_LIGHT:
        out[5] |= code << 2
    elif item == EBL_PUMP:
        out[6] |= code << 2
    elif item == EBL_PANEL and not on:
        out[5] = out[6] = 0x05
    return bytes(out)


# ---- Alde 3020 (앱 K3/a·K3/e) ----

@dataclass
class Alde:
    valid: bool = False
    sofa_ok: bool = False
    bed_ok: bool = False
    outside_ok: bool = False
    sofa_c: int = 0
    bed_c: int = 0
    outside_c: int = 0
    set_c: int = 0
    gas: bool = False
    electric: int = 0
    prio_gas: bool = False
    panel: bool = False
    error: bool = False
    auto_mode: bool = False
    acc: bool = False
    water: int = 0
    heating: bool = False
    raw: bytes = field(default=bytes(4))


def decode_alde(r: bytes, a: Alde) -> bool:
    if len(r) < LIN_REPLY_LEN:
        return False
    a.sofa_ok, a.bed_ok, a.outside_ok = r[3] < 251, r[4] < 251, r[5] < 251
    a.sofa_c, a.bed_c, a.outside_c = r[3] // 2 - 42, r[4] // 2 - 42, r[5] // 2 - 42
    a.set_c = int((r[6] & 0x3F) * 0.5 + 5.0)
    a.gas, a.prio_gas = bool(r[6] & 0x40), bool(r[6] & 0x80)
    a.electric = r[7] >> 6
    a.panel, a.error = bool(r[8] & 0x01), bool(r[8] & 0x04)
    a.water = (r[8] >> 3) & 0x03
    a.acc, a.auto_mode = bool(r[8] & 0x20), bool(r[8] & 0x40)
    a.heating = a.panel and (a.auto_mode or a.gas or bool(a.electric))
    a.raw = bytes(r[6:10])
    a.valid = True
    return True


def _alde_base(a: Alde) -> bytearray:
    return bytearray([0, 0, 0]) + bytearray(a.raw) + bytearray([0])


def alde_set_gas(a: Alde, on: bool) -> bytes:
    out = _alde_base(a)
    out[3] = (out[3] & ~0x40 & 0xFF) | (0x40 if on else 0)
    if on:
        out[5] |= 0x01
    return bytes(out)


def alde_energy_index(a: Alde) -> int:
    if not a.gas and not a.electric:
        return 0
    if a.gas and not a.electric:
        return 1
    return a.electric + 4 if a.gas else a.electric + 1


def alde_set_energy(a: Alde, index: int) -> bytes | None:
    if not 0 <= index <= 7:
        return None
    gas = index == 1 or index >= 5
    el = index - 4 if index >= 5 else (index - 1 if index >= 2 else 0)
    out = _alde_base(a)
    out[3] = (out[3] & ~0x40 & 0xFF) | (0x40 if gas else 0)
    out[4] = (out[4] & 0x3F) | (el << 6)
    if gas or el:
        out[5] |= 0x01
    return bytes(out)


def alde_set_water(a: Alde, mode: int) -> bytes | None:
    if not 0 <= mode <= 2:
        return None
    out = _alde_base(a)
    out[5] = (out[5] & ~0x18 & 0xFF) | (mode << 3)
    if mode:
        out[5] |= 0x01
    return bytes(out)


def alde_set_temp(a: Alde, c: int) -> bytes:
    c = max(5, min(30, c))
    out = _alde_base(a)
    out[3] = (out[3] & ~0x3F & 0xFF) | (((c - 5) * 2) & 0x3F)
    out[5] |= 0x01
    return bytes(out)


# ---- 수평 (ESP Mach::toJson) ----

LEVEL_FLAT_DEG = 0.3


def low_side(deg: float, plus_low: str, minus_low: str) -> str:
    if abs(deg) < LEVEL_FLAT_DEG:
        return "수평"
    return f"{plus_low if deg > 0 else minus_low} {abs(deg):.1f}° 낮음"


def tilt(avg: tuple[float, float, float]) -> tuple[int, list[float]]:
    """중력이 가장 크게 걸린 축을 수직으로 보고 나머지 두 축의 기울기 [°]."""
    up = max(range(3), key=lambda k: abs(avg[k]))
    deg = [0.0, 0.0, 0.0]
    for k in range(3):
        if k != up:
            deg[k] = math.degrees(math.atan2(avg[k], abs(avg[up])))
    return up, deg
