"""BLE 장치들이 함께 쓰는 것. BlueZ는 스캔·연결이 겹치면 InProgress로 거절하므로 한 번에 하나만 한다."""

from __future__ import annotations

import asyncio

connect_lock = asyncio.Lock()
