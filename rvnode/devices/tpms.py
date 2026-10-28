"""BLE TPMS 수동 스캔 — ESP src/modules/tpms/TpmsBle.cpp를 옮겼다. 키·state JSON이 같아야 ESP 9FCEE4_tpms_*를
넘겨받을 때 HA 엔티티가 이어진다 (docs/23, docs/29 §3).

params.sensors[] = {mac, name, label, unit(bar|psi|kPa)}: 등록한 센서만 state·HA에 위치 이름으로 나온다.
등록되지 않은 TPMS는 state.unassigned에 MAC·번호·공기압과 함께 보인다 — 여기서 MAC을 골라 등록한다.
값 표시만 한다. 경보는 HA 자동화에서.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any

from ..ble import scanner
from ..device import Device, Sensor
from ..frames import tpms

MAX_SENSORS, MAX_SEEN = 6, 10
NAME_RE = re.compile(r"^[a-z0-9_]{1,16}$")


@dataclass
class Configured:
    mac: str  # "" = 아직 등록 전 (자리만)
    name: str
    label: str
    unit: str


@dataclass
class Seen:
    r: tpms.Reading
    rssi: int
    last: float
    count: int = 0


def in_unit(kpa: float, unit: str) -> float:
    if unit == "psi":
        return tpms.kpa_to_psi(kpa)
    if unit == "kPa":
        return kpa
    return tpms.kpa_to_bar(kpa)


class Tpms(Device):
    type = "tpms"

    def __init__(self, cfg, node):
        super().__init__(cfg, node)
        self.cfg_sensors: list[Configured] = []
        for v in (self.params.get("sensors") or [])[:MAX_SENSORS]:
            name = str(v.get("name", ""))
            if not NAME_RE.match(name):
                self.log.warning("skipping sensor with invalid name '%s'", name)
                continue
            unit = str(v.get("unit", "bar"))
            self.cfg_sensors.append(Configured(tpms.parse_mac(str(v.get("mac", ""))) or "", name,
                                               str(v.get("label") or name), unit if unit in ("psi", "kPa") else "bar"))
        self.stale_s = float(self.params.get("stale_s", 900))
        self.interval = float(self.params.get("interval_s", 10))
        sensors = []
        for c in self.cfg_sensors:
            sensors += [Sensor(f"{c.name}_pressure", f"{c.label} Pressure", c.unit, "pressure", "measurement", f"{c.name}.pressure"),
                        Sensor(f"{c.name}_temp_c", f"{c.label} Temperature", "°C", "temperature", "measurement", f"{c.name}.temp_c"),
                        Sensor(f"{c.name}_battery", f"{c.label} Battery", "%", "battery", "measurement", f"{c.name}.battery")]
        self.sensors = tuple(sensors)
        self.key_sensors = ",".join(f"{c.name}_pressure" for c in self.cfg_sensors)
        self.seen: dict[str, Seen] = {}
        self.adverts = 0
        self.clock = time.monotonic

    def on_advert(self, dev, adv) -> None:
        r = tpms.from_manufacturer_data(adv.manufacturer_data or {})
        if r is None:
            return
        self.adverts += 1
        now = self.clock()
        s = self.seen.get(r.mac)
        if s is None:
            if len(self.seen) >= MAX_SEEN:  # 가득 차면 가장 오래 소식 없는 것을 버린다 (지나가는 차의 센서 등)
                del self.seen[min(self.seen, key=lambda k: self.seen[k].last)]
            s = self.seen[r.mac] = Seen(r, adv.rssi, now)
        s.r, s.rssi, s.last = r, adv.rssi, now
        s.count += 1

    async def run(self) -> None:
        await scanner.add(self.on_advert)
        try:
            while True:
                try:
                    await scanner.ensure()
                    await self.ok()
                    await self.publish_state()
                except Exception as e:
                    await self.error(str(e) or type(e).__name__)
                await asyncio.sleep(self.interval)
        finally:
            await scanner.remove(self.on_advert)

    def state(self) -> dict[str, Any]:
        now = self.clock()
        out: dict[str, Any] = {"adverts": self.adverts}
        used = set()
        for c in self.cfg_sensors:
            o: dict[str, Any] = {"mac": c.mac or "00:00:00:00:00:00"}
            out[c.name] = o
            s = self.seen.get(c.mac) if c.mac else None
            if s is None:
                o["online"] = False
                continue
            used.add(c.mac)
            age = now - s.last
            o.update(online=age < self.stale_s, age_s=int(age), pressure=round(in_unit(s.r.kpa, c.unit), 3),
                     unit=c.unit, kpa=round(s.r.kpa, 3), bar=round(tpms.kpa_to_bar(s.r.kpa), 4),
                     psi=round(tpms.kpa_to_psi(s.r.kpa), 2), temp_c=s.r.temp_c, battery=s.r.battery,
                     no_pressure=s.r.no_pressure, sensor_no=s.r.sensor_no, rssi=s.rssi)
        out["unassigned"] = [
            {"mac": mac, "sensor_no": s.r.sensor_no, "bar": round(tpms.kpa_to_bar(s.r.kpa), 4),
             "psi": round(tpms.kpa_to_psi(s.r.kpa), 2), "rssi": s.rssi, "age_s": int(now - s.last)}
            for mac, s in self.seen.items() if mac not in used
        ]
        return out
