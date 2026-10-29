"""HA MQTT discovery 페이로드. ESP src/core/HaDiscovery.cpp와 같은 모양이어야 한다 — uniq_id·def_ent_id·토픽이
같아야 ESP에서 넘어와도 HA 엔티티·기록·대시보드가 이어진다 (docs/29 §3.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .device import BinarySensor, Control, Device, Event, Sensor, Switch


@dataclass(frozen=True)
class DeviceInfo:
    device_id: str
    base: str  # <base>/status가 노드 전체 availability
    sw: str
    mac: str = ""
    model: str = "rvnode"


def display_precision(s: Sensor) -> int | None:
    """단위로 표시 자릿수를 정한다 (HaDiscovery::displayPrecision). None = HA 기본."""
    u = s.unit
    if u == "V":
        return 3 if s.key.startswith("cell_") else 2
    if u in ("A", "Ah", "kWh"):
        return 2
    if u in ("°C", "Hz"):
        return 1
    if u in ("W", "%"):
        return 0
    return None


def _dev_block(info: DeviceInfo, dev: Device) -> dict[str, Any]:
    d: dict[str, Any] = {"ids": [info.device_id]}
    if info.mac:
        d["cns"] = [["mac", info.mac]]
    d.update({"name": dev.label, "mf": "nam6411", "mdl": info.model, "sw": info.sw})
    return d


def _avty(info: DeviceInfo, dev: Device, avail: str | None) -> list[dict[str, str]]:
    a = [{"t": f"{info.base}/status"}, {"t": f"{dev.prefix}/availability"}]
    if avail:
        a.append({"t": f"{dev.prefix}/state", "val_tpl": f"{{{{ 'online' if value_json.{avail} else 'offline' }}}}"})
    return a


def _common(info: DeviceInfo, dev: Device, component: str, name: str, label: str) -> dict[str, Any]:
    return {
        "uniq_id": f"{info.device_id}_{dev.slug}_{name}",
        "def_ent_id": f"{component}.{dev.slug}_{name}",
        "name": label,
    }


def _finish(doc: dict[str, Any], info: DeviceInfo, dev: Device, avail: str | None) -> dict[str, Any]:
    doc["avty"] = _avty(info, dev, avail)
    doc["avty_mode"] = "all"
    doc["dev"] = _dev_block(info, dev)
    return doc


def sensor(info: DeviceInfo, dev: Device, s: Sensor) -> dict[str, Any]:
    doc = _common(info, dev, "sensor", s.key, s.label)
    doc["stat_t"] = f"{dev.prefix}/state"
    doc["val_tpl"] = f"{{{{ value_json.{s.json_path} }}}}"
    if s.unit:
        doc["unit_of_meas"] = s.unit
    prc = display_precision(s)
    if prc is not None:
        doc["sug_dsp_prc"] = prc
    if s.dev_class:
        doc["dev_cla"] = s.dev_class
    if s.state_class:
        doc["stat_cla"] = s.state_class
    return _finish(doc, info, dev, s.avail)


def binary_sensor(info: DeviceInfo, dev: Device, b: BinarySensor) -> dict[str, Any]:
    """ESP에는 없던 종류 — 켜짐/꺼짐 입력(시동·깜빡이·후진 …). state JSON의 bool을 ON/OFF로 바꿔 읽는다."""
    doc = _common(info, dev, "binary_sensor", b.key, b.label)
    doc["stat_t"] = f"{dev.prefix}/state"
    doc["val_tpl"] = f"{{{{ 'ON' if value_json.{b.json_path} else 'OFF' }}}}"
    if b.dev_class:
        doc["dev_cla"] = b.dev_class
    return _finish(doc, info, dev, None)


def switch(info: DeviceInfo, dev: Device, s: Switch) -> dict[str, Any]:
    doc = _common(info, dev, "switch", s.name, s.label)
    doc["stat_t"] = f"{dev.prefix}/switch/{s.name}/state"
    doc["cmd_t"] = f"{dev.prefix}/switch/{s.name}/set"
    doc["pl_on"] = "ON"
    doc["pl_off"] = "OFF"
    return _finish(doc, info, dev, s.avail)


def control(info: DeviceInfo, dev: Device, c: Control) -> dict[str, Any]:
    doc = _common(info, dev, c.kind, c.name, c.label)
    if c.path:
        doc["stat_t"] = f"{dev.prefix}/state"
        doc["val_tpl"] = f"{{{{ value_json.{c.path} }}}}"
    doc["cmd_t"] = f"{dev.prefix}/control/{c.name}/set"
    if c.kind == "number":
        doc.update({"min": c.min, "max": c.max, "step": c.step})
        if c.unit:
            doc["unit_of_meas"] = c.unit
    elif c.kind == "select":
        doc["ops"] = list(c.options)
    elif c.kind == "button":
        doc["pl_prs"] = "PRESS"
    elif c.kind == "text":
        doc["max"] = 23  # ESP 명령 버퍼와 같게 — 같은 엔티티를 넘겨받을 때 모양이 바뀌지 않도록
    return _finish(doc, info, dev, c.avail)


def event(info: DeviceInfo, dev: Device, e: Event) -> dict[str, Any]:
    doc = _common(info, dev, "event", e.name, e.label)
    doc["stat_t"] = f"{dev.prefix}/event/{e.name}"
    doc["evt_typ"] = list(e.types)
    return _finish(doc, info, dev, None)


def config_topic(prefix: str, component: str, info: DeviceInfo, dev: Device, entity: str) -> str:
    return f"{prefix}/{component}/{info.device_id}_{dev.slug}/{entity}/config"


def all_configs(discovery_prefix: str, info: DeviceInfo, dev: Device) -> list[tuple[str, dict[str, Any]]]:
    """(토픽, 페이로드) 목록 — 장치 하나의 엔티티 전부."""
    out = []
    for s in dev.sensors:
        out.append((config_topic(discovery_prefix, "sensor", info, dev, s.key), sensor(info, dev, s)))
    for b in dev.binary_sensors:
        out.append((config_topic(discovery_prefix, "binary_sensor", info, dev, b.key), binary_sensor(info, dev, b)))
    for sw in dev.switches:
        out.append((config_topic(discovery_prefix, "switch", info, dev, sw.name), switch(info, dev, sw)))
    for c in dev.controls:
        if not c.discover:
            continue
        out.append((config_topic(discovery_prefix, c.kind, info, dev, c.name), control(info, dev, c)))
    for e in dev.events:
        out.append((config_topic(discovery_prefix, "event", info, dev, e.name), event(info, dev, e)))
    for comp, name, extra in dev.ha_extra(info):
        doc = {**_common(info, dev, comp, name, extra.pop("name", name)), **extra}
        out.append((config_topic(discovery_prefix, comp, info, dev, name), _finish(doc, info, dev, None)))
    return out
