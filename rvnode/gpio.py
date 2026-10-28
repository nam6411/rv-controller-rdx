"""GPIO 입출력. 보드에서는 libgpiod v2(파이썬 `gpiod` 2.x), Mac·테스트에서는 FakeLines.

선 하나는 이름 → (칩, 라인, active_low, bias)로 정한다. 값은 언제나 논리값이다(active_low면 Low가 True).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class LineCfg:
    chip: str  # "gpiochip3" 또는 "/dev/gpiochip3"
    line: int
    active_low: bool = False
    bias: str = "as-is"  # as-is | pull-up | pull-down | disabled

    @classmethod
    def parse(cls, name: str, raw: Any) -> LineCfg:
        if not isinstance(raw, dict) or "chip" not in raw or raw.get("line") is None:
            raise ValueError(f"gpio '{name}': chip·line을 정해야 한다 (보드에서 gpioinfo로 확인)")
        return cls(str(raw["chip"]), int(raw["line"]), bool(raw.get("active_low", False)), str(raw.get("bias", "as-is")))

    @property
    def path(self) -> str:
        return self.chip if self.chip.startswith("/") else f"/dev/{self.chip}"


class Lines(Protocol):
    def get(self, name: str) -> bool: ...
    def set(self, name: str, value: bool) -> None: ...
    def close(self) -> None: ...


class FakeLines:
    """하드웨어 없이 돌린다. 테스트는 values를 직접 바꾼다."""

    def __init__(self, inputs: dict[str, LineCfg], outputs: dict[str, LineCfg]):
        self.values = {n: False for n in [*inputs, *outputs]}
        self.writes: list[tuple[str, bool]] = []

    def get(self, name: str) -> bool:
        return self.values[name]

    def set(self, name: str, value: bool) -> None:
        self.values[name] = value
        self.writes.append((name, value))

    def close(self) -> None:
        pass


class GpiodLines:
    """libgpiod v2. 칩마다 요청 하나. 출력은 처음에 꺼진 값(논리 False)으로 연다."""

    def __init__(self, inputs: dict[str, LineCfg], outputs: dict[str, LineCfg]):
        import gpiod
        from gpiod.line import Bias, Direction, Value

        self._V = Value
        bias = {"as-is": Bias.AS_IS, "pull-up": Bias.PULL_UP, "pull-down": Bias.PULL_DOWN, "disabled": Bias.DISABLED}
        per_chip: dict[str, dict[int, Any]] = {}
        self._where: dict[str, tuple[str, int]] = {}
        for name, c in inputs.items():
            per_chip.setdefault(c.path, {})[c.line] = gpiod.LineSettings(
                direction=Direction.INPUT, bias=bias[c.bias], active_low=c.active_low
            )
            self._where[name] = (c.path, c.line)
        for name, c in outputs.items():
            per_chip.setdefault(c.path, {})[c.line] = gpiod.LineSettings(
                direction=Direction.OUTPUT, output_value=Value.INACTIVE, active_low=c.active_low
            )
            self._where[name] = (c.path, c.line)
        self._req = {}
        try:
            for path, cfg in per_chip.items():
                self._req[path] = gpiod.request_lines(path, consumer="rvnode", config=cfg)
        except Exception:
            self.close()
            raise

    def get(self, name: str) -> bool:
        path, line = self._where[name]
        return self._req[path].get_value(line) == self._V.ACTIVE

    def set(self, name: str, value: bool) -> None:
        path, line = self._where[name]
        self._req[path].set_value(line, self._V.ACTIVE if value else self._V.INACTIVE)

    def close(self) -> None:
        for r in self._req.values():
            try:
                r.release()
            except Exception:
                pass
        self._req = {}


def open_lines(backend: str, inputs: dict[str, LineCfg], outputs: dict[str, LineCfg]) -> Lines:
    if backend == "fake":
        return FakeLines(inputs, outputs)
    if backend == "gpiod":
        return GpiodLines(inputs, outputs)
    raise ValueError(f"unknown gpio backend '{backend}' (gpiod | fake)")
