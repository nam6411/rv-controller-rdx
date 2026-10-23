"""하드웨어 없이 MQTT·HA 흐름을 확인하는 장치. 새 장치 모듈을 만들 때 본보기로도 쓴다.

params:
  interval_s  발행 주기 (기본 5)
  fail_every  N번째 읽기마다 실패를 흉내 낸다 (0 = 안 함). availability 확인용
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from ..device import Control, Device, Event, Sensor, Switch


class Dummy(Device):
    type = "dummy"
    sensors = (
        Sensor("counter", "Counter", state_class="measurement"),
        Sensor("temp", "Temperature", "°C", "temperature", "measurement", path="env.temp"),
    )
    switches = (Switch("led", "LED"),)
    controls = (
        Control("level", "Level", "number", path="level", min=0, max=10, step=1),
        Control("mode", "Mode", "select", path="mode", options=("off", "low", "high")),
        Control("ping", "Ping", "button"),
    )
    events = (Event("pinged", "Pinged", ("ping",)),)
    key_sensors = "temp,counter"

    def __init__(self, cfg, node):
        super().__init__(cfg, node)
        self.interval = float(self.params.get("interval_s", 5))
        self.fail_every = int(self.params.get("fail_every", 0))
        self.counter = 0
        self.temp = 20.0
        self.led: bool | None = None
        self.level = 0
        self.mode = "off"

    async def run(self) -> None:
        reads = 0
        while True:
            reads += 1
            if self.fail_every and reads % self.fail_every == 0:
                await self.error("simulated failure")
            else:
                self.counter += 1
                self.temp = round(self.temp + random.uniform(-0.3, 0.3), 1)
                if self.led is None:
                    self.led = False  # 첫 읽기 전에는 모름 → switch state를 내지 않는다
                await self.ok()
                await self.publish_state()
            await asyncio.sleep(self.interval)

    def state(self) -> dict[str, Any]:
        return {"counter": self.counter, "env": {"temp": self.temp}, "level": self.level, "mode": self.mode}

    def switch_state(self, name: str) -> bool | None:
        return self.led if name == "led" else None

    async def write_switch(self, name: str, on: bool) -> bool:
        self.led = on
        return True

    async def write_control(self, name: str, value: str) -> bool:
        if name == "level":
            try:
                self.level = max(0, min(10, int(float(value))))
            except ValueError:
                return False
            return True
        if name == "mode":
            if value not in ("off", "low", "high"):
                return False
            self.mode = value
            return True
        if name == "ping":
            await self.emit_event("pinged", "ping", counter=self.counter)
            return True
        return False
