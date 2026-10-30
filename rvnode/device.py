"""장치 모듈의 기반. ESP IDeviceModule(src/modules/IDeviceModule.h)과 같은 엔티티 정의를 쓴다.

장치는 run()에서 자기 루프를 돈다(BLE 연결 유지, 스캔, 폴링 …). 값을 읽으면 ok() + publish_state(),
실패하면 error(). 명령은 노드가 장치마다 큐에 넣어 차례로 write_switch()/write_control()을 부른다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from .config import DeviceConfig
    from .node import Node

OFFLINE_AFTER_ERRORS = 3  # ESP Scheduler와 같다 — 3회 연속 실패하면 availability = offline


@dataclass(frozen=True)
class Sensor:
    key: str  # uniq_id 접미
    label: str
    unit: str = ""
    dev_class: str = ""
    state_class: str = ""
    path: str = ""  # state JSON 경로 (비우면 key)
    avail: str | None = None  # state JSON의 bool 경로. false면 HA에서 unavailable (예: "ac.ok")

    @property
    def json_path(self) -> str:
        return self.path or self.key


@dataclass(frozen=True)
class BinarySensor:
    key: str
    label: str
    path: str = ""  # state JSON의 bool 경로 (비우면 key)
    dev_class: str = ""  # HA device_class (power, motion …)

    @property
    def json_path(self) -> str:
        return self.path or self.key


@dataclass(frozen=True)
class Switch:
    name: str
    label: str
    avail: str | None = None


@dataclass(frozen=True)
class Control:
    name: str
    label: str
    kind: str  # number | select | button | text (= HA 컴포넌트)
    path: str = ""  # 현재 값 경로 (button·text는 "")
    min: float = 0
    max: float = 100
    step: float = 1
    unit: str = ""
    options: tuple[str, ...] = ()
    avail: str | None = None
    discover: bool = True  # False면 HA에 올리지 않는다 (다른 프로그램만 쓰는 명령 — 예: video push)


@dataclass(frozen=True)
class Event:
    name: str
    label: str
    types: tuple[str, ...]


class Device:
    """장치 하나 = ESP 슬롯 하나. 하위 클래스가 type·엔티티·run()·state()를 채운다."""

    type: ClassVar[str] = ""
    sensors: ClassVar[tuple[Sensor, ...]] = ()
    binary_sensors: ClassVar[tuple[BinarySensor, ...]] = ()
    switches: ClassVar[tuple[Switch, ...]] = ()
    controls: ClassVar[tuple[Control, ...]] = ()
    events: ClassVar[tuple[Event, ...]] = ()
    key_sensors: ClassVar[str] = ""  # meta.metrics — 디스플레이가 먼저 보여 줄 센서 키, 쉼표 구분
    display_ring: ClassVar[str] = ""  # meta.ring (예: "battery:soc,power")

    def __init__(self, cfg: DeviceConfig, node: Node):
        self.cfg = cfg
        self.node = node
        self.slug = cfg.slug
        self.label = cfg.label
        self.prefix = cfg.prefix
        self.params = cfg.params
        self.log = logging.getLogger(f"rvnode.{cfg.slug}")
        self.online = False
        self.errors = 0
        self.last_error = ""
        self.commands: asyncio.Queue[tuple[str, str, str]] = asyncio.Queue(maxsize=16)

    # ---- 하위 클래스가 구현 ----

    async def run(self) -> None:
        """장치 루프. 끝나거나 예외가 나면 노드가 잠시 뒤 다시 부른다."""
        raise NotImplementedError

    def state(self) -> dict[str, Any]:
        """<prefix>/state JSON (ESP toJson)."""
        return {}

    def switch_state(self, name: str) -> bool | None:
        """None = 아직 모름 — state를 발행하지 않는다 (모르는 값을 OFF로 보이지 않게)."""
        return None

    async def write_switch(self, name: str, on: bool) -> bool:
        return False

    async def write_control(self, name: str, value: str) -> bool:
        return False

    # ---- 장치가 부르는 도우미 ----

    async def ok(self) -> None:
        """한 번 읽기에 성공했다."""
        self.errors = 0
        self.last_error = ""
        if not self.online:
            self.online = True
            self.log.info("online")
            await self.publish_availability()

    async def error(self, msg: str) -> None:
        """한 번 실패했다. 3회 연속이면 offline."""
        self.errors += 1
        self.last_error = msg
        self.log.warning("error #%d: %s", self.errors, msg)
        if self.online and self.errors >= OFFLINE_AFTER_ERRORS:
            self.online = False
            self.log.warning("offline")
            await self.publish_availability()

    async def publish_state(self) -> None:
        await self.node.bus.publish(f"{self.prefix}/state", json.dumps(self.state(), separators=(",", ":")), retain=True)
        for sw in self.switches:
            await self.publish_switch(sw.name)

    async def publish_switch(self, name: str) -> None:
        on = self.switch_state(name)
        if on is None:
            return
        await self.node.bus.publish(f"{self.prefix}/switch/{name}/state", "ON" if on else "OFF", retain=True)

    async def publish_availability(self) -> None:
        await self.node.bus.publish(f"{self.prefix}/availability", "online" if self.online else "offline", retain=True)

    async def emit_event(self, name: str, event_type: str, **attrs: Any) -> None:
        """순간 이벤트 → <prefix>/event/<name> (retain 안 함)."""
        payload = {"event_type": event_type, **attrs}
        await self.node.bus.publish(f"{self.prefix}/event/{name}", json.dumps(payload, separators=(",", ":")))

    async def rediscover(self) -> None:
        """엔티티 목록이 바뀌었을 때 (예: 셀 수를 장치에서 알아냄)."""
        await self.node.publish_discovery(self)

    def ha_extra(self, info: Any) -> list[tuple[str, str, dict[str, Any]]]:
        """표준 목록(sensor·switch…)에 없는 HA 엔티티 — [(component, 이름, 페이로드 일부)]. ha.py가 uniq_id·avty·dev를 채운다."""
        return []

    # ---- 노드가 쓴다 ----

    def meta(self) -> dict[str, Any]:
        """<prefix>/meta — 디스플레이가 이것만 보고 화면을 만든다 (ESP Scheduler::publishMeta)."""
        by_key = {s.key: s for s in self.sensors}
        metrics = []
        for key in filter(None, self.key_sensors.split(",")):
            s = by_key.get(key.strip())
            if s:
                metrics.append({"l": s.label, "u": s.unit, "p": s.json_path})
        doc: dict[str, Any] = {"type": self.type, "label": self.label}
        if self.display_ring:
            doc["ring"] = self.display_ring
        doc["switches"] = [{"n": s.name, "l": s.label} for s in self.switches]
        doc["metrics"] = metrics
        return doc

    async def publish_all(self) -> None:
        """접속·HA 재시작·rediscover 때 (ESP Scheduler::publishAll)."""
        await self.node.bus.publish(f"{self.prefix}/meta", json.dumps(self.meta(), separators=(",", ":")), retain=True)
        await self.publish_availability()
        if self.online:
            await self.publish_state()

    async def handle_commands(self) -> None:
        """명령 큐를 차례로 처리한다. 처리 뒤 state를 바로 발행 (ESP와 같다)."""
        while True:
            kind, name, value = await self.commands.get()
            try:
                if kind == "switch":
                    if name not in {s.name for s in self.switches}:
                        self.log.warning("unknown switch '%s'", name)
                        continue
                    on = value.upper() == "ON" or value == "1" or value.lower() == "true"
                    ok = await self.write_switch(name, on)
                    self.log.info("%s -> %s %s", name, "ON" if on else "OFF", "ok" if ok else "FAILED")
                    await self.publish_switch(name)
                    await self.publish_state()  # 스위치에 딸린 값(예: 버드뷰 powered)도 바로 — 주기 발행을 기다리지 않게
                else:
                    c = next((c for c in self.controls if c.name == name), None)
                    if c is None:
                        self.log.warning("unknown control '%s'", name)
                        continue
                    ok = await self.write_control(name, value)
                    # 숨은 명령은 계정 같은 값을 실을 수 있어 내용을 남기지 않는다
                    self.log.log(logging.INFO if c.discover else logging.DEBUG, "%s -> %s %s", name,
                                 value if c.discover else "(hidden)", "ok" if ok else "FAILED")
                    await self.publish_state()
            except Exception:
                self.log.exception("command %s %s failed", kind, name)
