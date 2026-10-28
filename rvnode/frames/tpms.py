"""BLE TPMS 광고 해석 — ESP src/modules/tpms/TpmsFrame.h를 옮겼다 (docs/23).

TPMSII 앱 계열(ZEEPIN·TP630·"BLE TPMS V11")은 연결 없이 Manufacturer data로 값을 내보낸다.
  00 01 | 80 EA CA 10 8A 78 | P P P P | T T T T | B | A      (18바이트)
  0–1 제조사 ID, 2–7 센서 MAC(2번 = 0x80 + 센서 번호 − 1), 8–11 공기압 Pa(LE), 12–15 온도 0.01 °C(LE, 부호),
  16 배터리 %, 17 경보(01 = 압력 없음)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ZEEPIN_LEN = 18


@dataclass
class Reading:
    mac: str  # "80:ea:ca:10:8a:78"
    sensor_no: int
    kpa: float
    temp_c: float
    battery: int
    no_pressure: bool


def parse_zeepin(d: bytes) -> Reading | None:
    """d = Manufacturer data 전체(제조사 ID 2바이트 포함)."""
    if len(d) < ZEEPIN_LEN or d[0] != 0x00 or d[1] != 0x01 or d[2] & 0xF0 != 0x80:
        return None
    return Reading(mac=":".join(f"{b:02x}" for b in d[2:8]), sensor_no=(d[2] & 0x0F) + 1,
                   kpa=int.from_bytes(d[8:12], "little") / 1000,
                   temp_c=int.from_bytes(d[12:16], "little", signed=True) / 100,
                   battery=d[16], no_pressure=d[17] == 0x01)


def from_manufacturer_data(md: dict[int, bytes]) -> Reading | None:
    """bleak는 앞 2바이트를 제조사 ID(LE)로 떼어 dict로 준다 — 붙여서 해석한다."""
    for cid, data in md.items():
        r = parse_zeepin(cid.to_bytes(2, "little") + bytes(data))
        if r:
            return r
    return None


def kpa_to_bar(kpa: float) -> float:
    return kpa / 100


def kpa_to_psi(kpa: float) -> float:
    return kpa * 0.1450377


def parse_mac(s: str) -> str | None:
    """대소문자·구분자(: 또는 -) 상관없이 12자리 hex → 소문자 콜론 형식. 아니면 None."""
    h = re.sub(r"[:-]", "", s or "")
    if not re.fullmatch(r"[0-9a-fA-F]{12}", h):
        return None
    h = h.lower()
    return ":".join(h[i : i + 2] for i in range(0, 12, 2))
