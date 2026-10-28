"""Truma Aventa 에어컨 LIN (상태 0x17, 제어 0x08) — ESP src/modules/lin/LinFrame.h aventa를 옮겼다 (docs/22 §9.4, 앱 Z3).

데이터 8바이트(d[0..7])만 다룬다. MACH 응답 11바이트에서는 r[3:11]이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FRAME_STATUS, FRAME_CONTROL = 0x17, 0x08
OFF, FAN, COOL, HEAT, AUTO = 0, 4, 5, 6, 7
FAN_LOW, FAN_MID, FAN_HIGH, FAN_NIGHT = 0x71, 0x72, 0x73, 0x74
COMFORT, SAPHIR_COMPACT, ECO, SAPHIR_COMFORT_RC, COMPACT, COMPACT_PLUS = 0, 1, 4, 5, 6, 7
SET_MIN_C, SET_MAX_C = 16, 31

MODE_NAMES = {OFF: "off", FAN: "fan", COOL: "cool", HEAT: "heat", AUTO: "auto"}
FAN_NAMES = {FAN_LOW: "low", FAN_MID: "mid", FAN_HIGH: "high", FAN_NIGHT: "night"}
MODELS = {"comfort": COMFORT, "saphir_compact": SAPHIR_COMPACT, "eco": ECO, "saphir_comfort_rc": SAPHIR_COMFORT_RC,
          "compact": COMPACT, "compact_plus": COMPACT_PLUS}


def set_bits(v: int, bit: int, width: int, val: int) -> int:
    mask = ((1 << width) - 1) << bit
    return (v & ~mask & 0xFF) | ((val << bit) & mask)


def mode_allowed(m: int, mode: int) -> bool:
    if mode not in MODE_NAMES:
        return False
    if m in (COMPACT, ECO) and mode == HEAT:
        return False
    if m in (COMPACT_PLUS, SAPHIR_COMPACT) and mode in (HEAT, AUTO):
        return False
    return True


def fan_allowed(m: int, mode: int, fan: int) -> bool:
    if not FAN_LOW <= fan <= FAN_NIGHT:
        return False
    if fan == FAN_NIGHT:
        if mode in (OFF, FAN):
            return False
        if m in (COMFORT, SAPHIR_COMFORT_RC) and mode in (HEAT, AUTO):
            return False
    if fan == FAN_MID:
        if m == COMFORT and mode == HEAT:
            return False
        if m == SAPHIR_COMPACT and mode == FAN:
            return False
    return True


def uses_setpoint(mode: int) -> bool:
    return mode in (COOL, HEAT, AUTO)


def temp(b: int) -> float:
    return 0.0 if b == 0 else (b - 74) / 10 + 16


def temp_raw(c: int) -> int:
    return 74 + (max(16, min(31, c)) - 16) * 10


@dataclass
class Status:
    room_c: float = 0.0
    set_c: float = 0.0
    mode: int = OFF
    fan: int = FAN_LOW
    manual: bool = False
    error: bool = False
    raw: bytes = field(default=bytes(8))


def parse_status(d: bytes) -> Status:
    return Status(temp(d[0]), temp(d[2]), d[5] & 0x0F, d[4], bool(d[5] & 0x10), bool(d[5] & 0x20), bytes(d[:8]))


def control_base(s: Status, m: int) -> bytearray:
    """제어 = [d2, d3&0F, d4, d5&0F, FF, FF, d6(Comfort만), FF]. 상태의 패널 조작 비트(0x10)를 실으면 에어컨이 모드를 무시한다."""
    return bytearray([s.raw[2], s.raw[3] & 0x0F, s.raw[4], s.raw[5] & 0x0F, 0xFF, 0xFF,
                      s.raw[6] if m == COMFORT else 0, 0xFF])


def _put_setpoint(c: int, out: bytearray) -> None:
    out[0] = temp_raw(c)
    out[1] = 0x0B


def set_mode(s: Status, m: int, mode: int, set_c: int) -> bytes | None:
    if not mode_allowed(m, mode):
        return None
    out = control_base(s, m)
    out[3] = set_bits(out[3], 0, 4, mode)
    if mode == OFF:
        out[2] = FAN_LOW
        return bytes(out)
    if not fan_allowed(m, mode, out[2]):
        out[2] = FAN_LOW
    if uses_setpoint(mode):
        _put_setpoint(set_c, out)
    return bytes(out)


def power(s: Status, m: int, on: bool, mode: int, set_c: int) -> bytes | None:
    return set_mode(s, m, mode if on else OFF, set_c)


def set_temp(s: Status, m: int, c: int) -> bytes:
    out = control_base(s, m)
    _put_setpoint(c, out)
    if not uses_setpoint(s.mode):
        out[3] = set_bits(out[3], 0, 4, COOL)
        if not fan_allowed(m, COOL, out[2]):
            out[2] = FAN_LOW
    return bytes(out)


def set_fan(s: Status, m: int, fan: int, set_c: int) -> bytes | None:
    mode = COOL if s.mode == OFF else s.mode
    if not fan_allowed(m, mode, fan):
        return None
    out = control_base(s, m)
    out[2] = fan
    if s.mode == OFF:
        out[3] = set_bits(out[3], 0, 4, COOL)
        _put_setpoint(set_c, out)
    return bytes(out)
