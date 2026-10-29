"""웹에서 BLE 장치를 찾아 고른다 — 주변 광고를 몇 초 모아 종류를 짐작하고, 고른 주소(ID)를 설정에 넣는다.

- JBD BMS: 서비스 FF00 → params.ble_address
- MACH:    이름 MACH-… 또는 Nordic UART 서비스 → params.address
- TPMS:    제조사 데이터가 TPMS 형식 → params.sensors[name].mac (BLE 주소가 아니라 광고 안의 센서 ID)

연결 중인 BLE 장치는 광고를 내지 않으므로 스캔에 안 보인다 — 웹은 지금 설정된 주소를 따로 보여 준다.
"""

from __future__ import annotations

import asyncio
import copy
import re
from typing import Any

from .ble import scanner
from .devices.jbd import SERVICE as JBD_SERVICE
from .devices.mach import NUS_SERVICE
from .frames import tpms

BLE_TYPES = ("jbd", "mach", "tpms")
ADDR_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")
# 장치 종류 → 주소를 넣는 params 키
ADDR_PARAM = {"jbd": "ble_address", "mach": "address"}


def classify(dev: Any, adv: Any) -> dict[str, Any]:
    name = dev.name or getattr(adv, "local_name", None) or ""
    uuids = [u.lower() for u in (getattr(adv, "service_uuids", None) or [])]
    kinds = []
    if JBD_SERVICE in uuids:
        kinds.append("jbd")
    if NUS_SERVICE in uuids or name.upper().startswith("MACH"):
        kinds.append("mach")
    out: dict[str, Any] = {"address": dev.address.upper(), "name": name, "rssi": getattr(adv, "rssi", None), "kinds": kinds}
    r = tpms.from_manufacturer_data(getattr(adv, "manufacturer_data", None) or {})
    if r is not None:
        kinds.append("tpms")
        out["tpms"] = {"id": r.mac, "sensor_no": r.sensor_no, "bar": round(tpms.kpa_to_bar(r.kpa), 2),
                       "temp_c": r.temp_c, "battery": r.battery}
    return out


async def scan(seconds: float) -> list[dict[str, Any]]:
    """seconds 동안 들은 장치. 같은 장치는 마지막 값, RSSI는 가장 센 값."""
    seen: dict[str, dict[str, Any]] = {}

    def cb(dev, adv) -> None:
        c = classify(dev, adv)
        old = seen.get(c["address"])
        if old is not None:
            rs = [x for x in (old["rssi"], c["rssi"]) if x is not None]
            c["rssi"] = max(rs) if rs else None
            c["name"] = c["name"] or old["name"]
            c["kinds"] = sorted(set(c["kinds"]) | set(old["kinds"]))
            c.setdefault("tpms", old.get("tpms"))
            c["count"] = old["count"] + 1
        else:
            c["count"] = 1
        seen[c["address"]] = c

    await scanner.add(cb)
    try:
        await asyncio.sleep(seconds)
    finally:
        await scanner.remove(cb)
    # 짐작한 종류가 있는 것 먼저, 그다음 신호가 센 순
    return sorted(seen.values(), key=lambda c: (not c["kinds"], -(c["rssi"] if c["rssi"] is not None else -999)))


def current(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """설정에 있는 BLE 장치와 지금 넣어 둔 주소(ID)."""
    out = []
    for d in raw.get("devices") or []:
        t, p = d.get("type"), d.get("params") or {}
        if t not in BLE_TYPES:
            continue
        item = {"slug": d.get("slug"), "type": t, "label": d.get("label") or d.get("slug"), "enabled": d.get("enabled", True)}
        if t == "tpms":
            item["sensors"] = [{"name": s.get("name", ""), "label": s.get("label") or s.get("name", ""), "id": s.get("mac", "")}
                               for s in p.get("sensors") or []]
        else:
            item["address"] = p.get(ADDR_PARAM[t], "") or ""
            item["name"] = p.get("ble_name" if t == "jbd" else "name", "") or ""
        out.append(item)
    return out


def assign(raw: dict[str, Any], slug: str, value: str, sensor: str = "") -> dict[str, Any]:
    """slug 장치의 주소(ID)를 value로 바꾼 설정 사본. value가 비면 지운다. 잘못되면 ValueError."""
    new = copy.deepcopy(raw)
    dev = next((d for d in new.get("devices") or [] if d.get("slug") == slug), None)
    if dev is None or dev.get("type") not in BLE_TYPES:
        raise ValueError(f"BLE 장치 '{slug}'가 없다")
    p = dev.setdefault("params", {})
    value = value.strip()
    if dev["type"] == "tpms":
        mac = tpms.parse_mac(value) if value else ""
        if mac is None:
            raise ValueError(f"TPMS ID '{value}'는 12자리 16진수여야 한다")
        sensors = p.get("sensors") or []
        target = next((s for s in sensors if s.get("name") == sensor), None)
        if target is None:
            raise ValueError(f"TPMS 센서 자리 '{sensor}'가 없다 — 설정의 sensors에 먼저 만든다")
        for s in sensors:  # 한 센서 ID는 한 자리에만
            if mac and s is not target and tpms.parse_mac(str(s.get("mac", ""))) == mac:
                s["mac"] = ""
        target["mac"] = mac
    else:
        addr = value.upper()
        if addr and not ADDR_RE.match(addr):
            raise ValueError(f"BLE 주소 '{value}'는 AA:BB:CC:DD:EE:FF 꼴이어야 한다")
        p[ADDR_PARAM[dev["type"]]] = addr
    return new
