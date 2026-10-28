"""장치 종류 → 클래스. 새 장치는 여기에 한 줄 더한다."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..device import Device
from .birdview import Birdview
from .dummy import Dummy
from .gps import Gps

if TYPE_CHECKING:
    from ..config import DeviceConfig
    from ..node import Node

TYPES: dict[str, type[Device]] = {
    Birdview.type: Birdview,
    Dummy.type: Dummy,
    Gps.type: Gps,
}


def create(cfg: DeviceConfig, node: Node) -> Device:
    cls = TYPES.get(cfg.type)
    if cls is None:
        raise ValueError(f"device '{cfg.slug}': unknown type '{cfg.type}' (known: {', '.join(sorted(TYPES))})")
    return cls(cfg, node)
