"""버드뷰 전원·시점 — rv-controller docs/18 §12.2 "AVM 켜짐", 트리거 출력, 13핀 입력.

입력 (옵토 출력이 GPIO를 Low로 당긴다 → active_low):
  ign      핀 37  차 시동 (U8)
  left     핀 22  13핀 좌 깜빡이 (U4)
  right    핀 28  13핀 우 깜빡이 (U5)
  reverse  핀 32  13핀 후진 (U6)
출력 (High = 켬):
  power    핀 35  버드뷰 ACC (U7 → J2 ACC)
  rear/left_out/right_out  핀 36·38·40  시점 트리거 (U1~U3)

전원 = 차 시동 OR 수동 스위치 OR 다른 모듈의 요청(원격 영상 등). 시동이 꺼지면 off_delay_s 동안 더 켜 둔다.
**인터록**: J2-1(트리거 전원)이 버드뷰 상시 전원이라, 버드뷰가 꺼져 있을 때 트리거에 12V를 넣으면 역급전된다.
그래서 트리거는 전원 출력이 켜져 있을 때만 켜고, 끌 때는 트리거부터 내린다.

params:
  backend         gpiod | fake (기본 gpiod)
  pins            {ign, left, right, reverse, power, rear, left_out, right_out}: {chip, line, active_low?, bias?}
  off_delay_s     시동이 꺼진 뒤 더 켜 둘 시간 (기본 30)
  blink_hold_s    깜빡이 마지막 펄스 뒤 그쪽 시점을 유지할 시간 (기본 1.5)
  poll_ms         입력 읽는 주기 (기본 20)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from ..device import BinarySensor, Control, Device, Sensor, Switch
from ..gpio import LineCfg, Lines, open_lines

INPUTS = ("ign", "left", "right", "reverse")
OUTPUTS = ("power", "rear", "left_out", "right_out")
TRIGGERS = ("rear", "left_out", "right_out")
MODES = ("auto", "rear", "left", "right", "none")
VIEW_TO_OUT = {"rear": "rear", "left": "left_out", "right": "right_out"}


@dataclass
class Logic:
    """입력·명령 → 출력. 시간은 밖에서 넣는다(테스트하기 쉽게)."""

    off_delay_s: float = 30.0
    blink_hold_s: float = 1.5
    manual: bool = False
    mode: str = "auto"
    requests: dict[str, bool] = field(default_factory=dict)
    ign_last: float | None = None
    left_last: float | None = None
    right_last: float | None = None

    def update(self, now: float, ign: bool, left: bool, right: bool, reverse: bool) -> tuple[bool, str]:
        if ign:
            self.ign_last = now
        if left:
            self.left_last = now
        if right:
            self.right_last = now
        ign_hold = self.ign_last is not None and now - self.ign_last < self.off_delay_s
        power = ign or ign_hold or self.manual or any(self.requests.values())
        if not power:
            return False, "none"
        if self.mode != "auto":
            return True, self.mode
        left_on = self.left_last is not None and now - self.left_last <= self.blink_hold_s
        right_on = self.right_last is not None and now - self.right_last <= self.blink_hold_s
        if reverse:
            return True, "rear"
        if left_on and not right_on:
            return True, "left"
        if right_on and not left_on:
            return True, "right"
        return True, "none"  # 비상등(양쪽)·평상시 — 버드뷰 기본 화면


class Birdview(Device):
    type = "birdview"
    binary_sensors = (
        BinarySensor("powered", "Power", dev_class="power"),
        BinarySensor("ignition", "Car ignition", "in.ign", "power"),
        BinarySensor("turn_left", "Turn left", "in.left"),
        BinarySensor("turn_right", "Turn right", "in.right"),
        BinarySensor("reverse", "Reverse", "in.reverse"),
    )
    sensors = (Sensor("view", "View"),)
    switches = (Switch("power", "Keep on"),)
    controls = (Control("mode", "View mode", "select", path="mode", options=MODES),)
    key_sensors = "view"

    def __init__(self, cfg, node, clock=time.monotonic):
        super().__init__(cfg, node)
        p = self.params
        pins = p.get("pins") or {}
        self.in_cfg = {n: LineCfg.parse(n, pins.get(n)) for n in INPUTS}
        self.out_cfg = {n: LineCfg.parse(n, pins.get(n)) for n in OUTPUTS}
        self.backend = str(p.get("backend", "gpiod"))
        self.poll = float(p.get("poll_ms", 20)) / 1000
        self.logic = Logic(off_delay_s=float(p.get("off_delay_s", 30)), blink_hold_s=float(p.get("blink_hold_s", 1.5)))
        self.clock = clock
        self.lines: Lines | None = None
        self.inputs = {n: False for n in INPUTS}
        self.out = {n: False for n in OUTPUTS}
        self.view = "none"

    # ---- 다른 모듈이 부른다 (예: 원격 영상 video 모듈) ----

    def request_power(self, source: str, on: bool) -> None:
        if on:
            self.logic.requests[source] = True
        else:
            self.logic.requests.pop(source, None)

    # ---- 출력 ----

    def _apply(self, power: bool, view: str) -> None:
        """인터록 순서로 출력을 바꾼다: 끌 때는 트리거 → 전원, 켤 때는 전원 → 트리거."""
        want = {t: power and VIEW_TO_OUT.get(view) == t for t in TRIGGERS}
        lines = self.lines
        assert lines is not None
        for t in TRIGGERS:
            if self.out[t] and not want[t]:
                lines.set(t, False)
                self.out[t] = False
        if self.out["power"] != power:
            lines.set("power", power)
            self.out["power"] = power
        if self.out["power"]:
            for t in TRIGGERS:
                if want[t] and not self.out[t]:
                    lines.set(t, True)
                    self.out[t] = True
        self.view = view if power else "none"

    def step(self) -> bool:
        """입력을 한 번 읽고 출력을 맞춘다. state가 바뀌었으면 True."""
        assert self.lines is not None
        before = self.state()
        self.inputs = {n: self.lines.get(n) for n in INPUTS}
        power, view = self.logic.update(self.clock(), **self.inputs)
        self._apply(power, view)
        return self.state() != before

    def open(self) -> None:
        self.lines = open_lines(self.backend, self.in_cfg, self.out_cfg)
        self.out = {n: False for n in OUTPUTS}  # 출력은 꺼진 값으로 열린다

    def close(self) -> None:
        if self.lines is None:
            return
        try:
            self._apply(False, "none")  # 트리거 → 전원 순서로 끈다
        finally:
            self.lines.close()
            self.lines = None

    async def run(self) -> None:
        try:
            self.open()
        except Exception as e:
            await self.error(f"gpio: {e}")
            raise
        try:
            await self.ok()
            self.step()
            await self.publish_state()
            last_pub = self.clock()
            while True:
                await asyncio.sleep(self.poll)
                changed = self.step()
                if changed or self.clock() - last_pub > 30:
                    await self.publish_state()
                    last_pub = self.clock()
        finally:
            self.close()

    # ---- state·명령 ----

    def state(self) -> dict[str, Any]:
        return {
            "powered": self.out["power"],
            "view": self.view,
            "mode": self.logic.mode,
            "keep_on": self.logic.manual,
            "requests": sorted(k for k, v in self.logic.requests.items() if v),
            "in": dict(self.inputs),
            "out": {t: self.out[t] for t in TRIGGERS},
        }

    def switch_state(self, name: str) -> bool | None:
        return self.logic.manual if name == "power" else None

    async def write_switch(self, name: str, on: bool) -> bool:
        if name != "power":
            return False
        self.logic.manual = on
        if self.lines is not None:
            self.step()
        return True

    async def write_control(self, name: str, value: str) -> bool:
        if name != "mode" or value not in MODES:
            return False
        self.logic.mode = value
        if self.lines is not None:
            self.step()
        return True
