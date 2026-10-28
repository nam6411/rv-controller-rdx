"""설정 YAML 읽기. 구조는 config.example.yaml."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SLUG_RE = re.compile(r"^[a-z0-9_]+$")


class ConfigError(ValueError):
    pass


@dataclass
class NodeConfig:
    device_id: str
    base: str
    label: str = "RV node"


@dataclass
class MqttConfig:
    host: str = "broker.local"
    port: int = 1883
    username: str = ""
    password: str = ""
    client_id: str = ""
    keepalive: int = 30
    discovery: bool = True
    discovery_prefix: str = "homeassistant"


# ls 브로커로 나가는 mosquitto 브리지 (docs/17, docs/29 §3.3). 값은 웹 설정에서 바꾼다.
BRIDGE_OUT = "rv/#,homeassistant/#"
BRIDGE_IN = ("rv/+/switch/+/set,rv/+/+/switch/+/set,rv/+/control/+/set,rv/+/+/control/+/set,"
             "rv/+/sys/cmd,rv/+/+/sys/cmd,homeassistant/status")


@dataclass
class BridgeConfig:
    enabled: bool = False
    host: str = ""
    port: int = 8883
    tls: bool = True
    username: str = ""
    password: str = ""
    remote_prefix: str = ""
    out_topics: str = BRIDGE_OUT
    in_topics: str = BRIDGE_IN


@dataclass
class WebConfig:
    enabled: bool = True
    port: int = 80
    bind: str = "0.0.0.0"
    password: str = ""  # 비우면 인증 없음 (차 안 LAN 전용)


@dataclass
class DeviceConfig:
    type: str
    slug: str
    label: str = ""
    prefix: str = ""
    enabled: bool = True
    discovery: bool = True
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Config:
    node: NodeConfig
    mqtt: MqttConfig
    devices: list[DeviceConfig]
    log_level: str = "info"
    bridge: BridgeConfig = field(default_factory=BridgeConfig)
    web: WebConfig = field(default_factory=WebConfig)

    def client_id(self) -> str:
        return self.mqtt.client_id or f"essio-radxa-{self.node.device_id}"


def slot_prefix(base: str, slug: str) -> str:
    """ESP Scheduler::slotPrefix와 같다 — base가 /<slug>로 끝나면 base 그대로 (rv/mach + mach → rv/mach)."""
    if base.endswith("/" + slug):
        return base
    return f"{base}/{slug}"


def _take(cls, raw: dict[str, Any] | None, where: str):
    raw = dict(raw or {})
    known = cls.__dataclass_fields__.keys()
    unknown = set(raw) - set(known)
    if unknown:
        raise ConfigError(f"{where}: unknown keys {sorted(unknown)}")
    try:
        return cls(**raw)
    except TypeError as e:
        raise ConfigError(f"{where}: {e}") from None


def parse(raw: dict[str, Any]) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")
    node = _take(NodeConfig, raw.get("node"), "node")
    node.base = node.base.rstrip("/")
    mqtt = _take(MqttConfig, raw.get("mqtt"), "mqtt")
    devices = []
    seen = set()
    for i, d in enumerate(raw.get("devices") or []):
        dev = _take(DeviceConfig, d, f"devices[{i}]")
        if not SLUG_RE.match(dev.slug):
            raise ConfigError(f"devices[{i}]: slug '{dev.slug}' must be [a-z0-9_]")
        if dev.slug in seen:
            raise ConfigError(f"devices[{i}]: duplicate slug '{dev.slug}'")
        seen.add(dev.slug)
        dev.label = dev.label or dev.slug
        dev.prefix = dev.prefix.rstrip("/") or slot_prefix(node.base, dev.slug)
        devices.append(dev)
    bridge = _take(BridgeConfig, raw.get("bridge"), "bridge")
    if bridge.enabled and not bridge.host:
        raise ConfigError("bridge: enabled인데 host가 비었다")
    web = _take(WebConfig, raw.get("web"), "web")
    unknown = set(raw) - {"node", "mqtt", "devices", "log_level", "bridge", "web"}
    if unknown:
        raise ConfigError(f"unknown top-level keys {sorted(unknown)}")
    return Config(node=node, mqtt=mqtt, devices=devices, log_level=raw.get("log_level", "info"), bridge=bridge, web=web)


def load_raw(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load(path: str | Path) -> Config:
    return parse(load_raw(path))
