"""설정 YAML 읽기. 구조는 config.example.yaml."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SLUG_RE = re.compile(r"^[a-z0-9_]+$")
MDNS_RE = re.compile(r"^[a-z0-9-]{1,32}$")


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


# 노드가 이 보드를 브로커로 찾는 길 — ESP 브로커와 같은 UDP 비콘 + mDNS (rv-controller docs/15 §5, docs/29 §3.3).
# 브로커를 이 보드로 옮길 때(docs/29 §5 5단계) 켠다. 켜기 전에 ESP 브로커를 끈다 — 비콘·broker.local이 둘이 된다.
@dataclass
class BrokerConfig:
    enabled: bool = False
    mdns_name: str = "broker"      # 노드의 mqtt.mdns_name과 같게. 비콘 이름이자 mDNS 별칭(<이름>.local)
    port: int = 1883               # 로컬 mosquitto 포트 (비콘·_mqtt._tcp에 싣는다)
    beacon_port: int = 47300
    beacon_interval_s: float = 5


# 폴백 AP — 공유기가 없으면 이 보드가 RV-FALLBACK을 띄워 노드를 묶는다 (docs/29 §3.4). NetworkManager로 한다.
@dataclass
class FallbackConfig:
    enabled: bool = False
    iface: str = "wlan0"
    ssid: str = "RV-FALLBACK"      # 노드의 wifi.fallback_ssid와 같게
    password: str = ""             # 노드의 wifi.fallback_password와 같게 (8~63자, 비우면 열린 AP)
    address: str = "192.168.4.1/24"  # AP 게이트웨이 = 노드가 보는 브로커 주소
    after_s: int = 60              # 공유기가 이만큼 끊기면 AP로
    rescan_s: int = 30             # AP 상태에서 공유기를 다시 찾는 주기
    sta_retry_s: int = 300         # AP 상태에서 스캔이 안 되는 드라이버면 이 주기로 공유기에 그냥 붙어 본다 (0 = 안 함)


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
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    fallback: FallbackConfig = field(default_factory=FallbackConfig)

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
    broker = _take(BrokerConfig, raw.get("broker"), "broker")
    broker.mdns_name = broker.mdns_name.strip().lower()
    if not MDNS_RE.match(broker.mdns_name):
        raise ConfigError(f"broker: mdns_name '{broker.mdns_name}' must be [a-z0-9-], 1~32자")
    fallback = _take(FallbackConfig, raw.get("fallback"), "fallback")
    _check_fallback(fallback)
    unknown = set(raw) - {"node", "mqtt", "devices", "log_level", "bridge", "web", "broker", "fallback"}
    if unknown:
        raise ConfigError(f"unknown top-level keys {sorted(unknown)}")
    return Config(node=node, mqtt=mqtt, devices=devices, log_level=raw.get("log_level", "info"), bridge=bridge, web=web,
                  broker=broker, fallback=fallback)


def _check_fallback(f: FallbackConfig) -> None:
    if not 1 <= len(f.ssid.encode()) <= 32:
        raise ConfigError("fallback: ssid는 1~32바이트")
    if f.password and not 8 <= len(f.password) <= 63:
        raise ConfigError("fallback: password는 8~63자 (WPA2), 비우면 열린 AP")
    try:
        ipaddress.IPv4Interface(f.address)
    except ValueError:
        raise ConfigError(f"fallback: address '{f.address}'는 192.168.4.1/24 꼴이어야 한다") from None
    if f.after_s < 5 or f.rescan_s < 5 or f.sta_retry_s < 0:
        raise ConfigError("fallback: after_s·rescan_s는 5초 이상, sta_retry_s는 0 이상")


def load_raw(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load(path: str | Path) -> Config:
    return parse(load_raw(path))
