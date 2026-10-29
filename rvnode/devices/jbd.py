"""JBD BMS (BLE, 서비스 FF00) — ESP src/modules/jbdbms/ 와 같은 엔티티 키 (rv-controller docs/04 §7, docs/29 §2.1).

5초마다 기본 정보(0x03)와 셀 전압(0x04)을 읽는다. 셀·NTC 수는 BMS가 알려 주는 대로 엔티티를 만들고, 바뀌면 discovery를 다시 낸다.
접속 직후 첫 요청은 BMS가 깨어나느라 답하지 않는다(ESP 실측) — 한 번 버린다.

params:
  ble_address     MAC (예: A4:C1:37:19:9C:10). 비우면 ble_name, 그것도 비우면 FF00을 광고하는 첫 장치
  ble_name        BLE 이름 (예: powerbank)
  interval_s      폴링 주기 (기본 5)
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..ble import connecting, scanner
from ..device import Device, Sensor, Switch
from ..frames import jbd

SERVICE = "0000ff00-0000-1000-8000-00805f9b34fb"
NOTIFY = "0000ff01-0000-1000-8000-00805f9b34fb"
WRITE = "0000ff02-0000-1000-8000-00805f9b34fb"
REPLY_TIMEOUT_S = 2.0

SCALARS = (
    Sensor("pack_v", "Pack Voltage", "V", "voltage", "measurement"),
    Sensor("current", "Current", "A", "current", "measurement"),
    Sensor("power", "Power", "W", "power", "measurement"),
    Sensor("remain_ah", "Remaining Capacity", "Ah", "", "measurement"),
    Sensor("full_ah", "Full Capacity", "Ah", "", "measurement"),
    Sensor("soc", "SOC", "%", "battery", "measurement"),
    Sensor("cycles", "Cycles", "", "", "total_increasing"),
    Sensor("cell_diff", "Cell Voltage Diff", "V", "voltage", "measurement"),
    Sensor("protection", "Protection Bits"),
    Sensor("balance", "Balance Bits"),
)


class Jbd(Device):
    type = "jbd"
    switches = (Switch("charge_fet", "Charge MOSFET"), Switch("discharge_fet", "Discharge MOSFET"))
    key_sensors = "soc,power,pack_v"
    display_ring = "battery:soc,power"

    def __init__(self, cfg, node):
        super().__init__(cfg, node)
        p = self.params
        self.address = str(p.get("ble_address", "") or "")
        self.name = str(p.get("ble_name", "") or "")
        self.interval = float(p.get("interval_s", 5))
        self.st = jbd.JbdState()
        self.known = False
        self.client = None
        self.peer = {"address": "", "name": "", "rssi": None}
        self._rx = bytearray()
        self._rx_event = asyncio.Event()
        self._lock = asyncio.Lock()
        self.sensors = SCALARS  # 셀·NTC는 처음 읽은 뒤 붙인다
        self._shape = (0, 0)

    # ---- 엔티티 모양 ----

    def _rebuild_sensors(self) -> bool:
        shape = (len(self.st.cell_v), len(self.st.ntc))
        if shape == self._shape:
            return False
        self._shape = shape
        cells = tuple(Sensor(f"cell_{i + 1}", f"Cell {i + 1}", "V", "voltage", "measurement", path=f"cell_v[{i}]")
                      for i in range(shape[0]))
        ntcs = tuple(Sensor(f"ntc_{i + 1}", f"NTC {i + 1}", "°C", "temperature", "measurement", path=f"ntc[{i}]")
                     for i in range(shape[1]))
        self.sensors = SCALARS + cells + ntcs
        return True

    # ---- BLE ----

    def _on_notify(self, _char, data: bytearray) -> None:
        if len(self._rx) + len(data) > 512:
            self._rx.clear()
        self._rx.extend(data)
        self._rx_event.set()

    async def _connect(self) -> None:
        async with connecting():
            await self._connect_locked()

    async def _connect_locked(self) -> None:
        from bleak import BleakClient

        def match(d, adv):
            if self.address:
                return d.address.upper() == self.address.upper()
            if self.name:
                return (d.name or adv.local_name) == self.name
            return SERVICE in (adv.service_uuids or [])

        dev = await scanner.find(match, timeout=15)
        if dev is None:
            raise ConnectionError(f"BMS {self.address or self.name or '(FF00)'} not found")
        client = BleakClient(dev, timeout=15, disconnected_callback=lambda _c: self._rx_event.set())
        await client.connect()
        await client.start_notify(NOTIFY, self._on_notify)
        self.client = client
        self.peer = {"address": dev.address, "name": dev.name or "", "rssi": getattr(dev, "rssi", None)}
        self.log.info("BLE connected %s (%s)", self.peer["name"], self.peer["address"])

    async def _disconnect(self) -> None:
        c, self.client = self.client, None
        if c is not None:
            try:
                await c.disconnect()
            except Exception:
                pass

    async def _request(self, frame: bytes) -> bytes:
        """요청 하나 → 검증한 DATA. 다른 명령의 응답(폰 앱 등)이 섞이면 버린다."""
        async with self._lock:
            c = self.client
            if c is None or not c.is_connected:
                raise ConnectionError("BLE not connected")
            self._rx.clear()
            self._rx_event.clear()
            await c.write_gatt_char(WRITE, frame, response=False)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + REPLY_TIMEOUT_S
            while True:
                found = jbd.find_response(self._rx)
                if found:
                    s, n = found
                    f = bytes(self._rx[s : s + n])
                    if f[1] != frame[2]:
                        raise jbd.VerifyError(f"got reply for {f[1]:02X}")
                    return jbd.verify_response(f)
                left = deadline - loop.time()
                if left <= 0 or not c.is_connected:
                    raise TimeoutError(f"cmd {frame[2]:02X}: {'partial ' + self._rx[:4].hex() if self._rx else 'no reply'}")
                self._rx_event.clear()
                try:
                    await asyncio.wait_for(self._rx_event.wait(), left)
                except asyncio.TimeoutError:
                    pass

    # ---- 루프 ----

    async def run(self) -> None:
        try:
            while True:
                if self.client is None or not self.client.is_connected:
                    await self._disconnect()
                    try:
                        await self._connect()
                    except Exception as e:
                        await self.error(str(e) or type(e).__name__)
                        await asyncio.sleep(10)
                        continue
                    try:  # 첫 요청은 버린다
                        await self._request(jbd.build_request(jbd.READ, jbd.CMD_BASIC))
                    except Exception:
                        pass
                try:
                    self.st.parse_basic(await self._request(jbd.build_request(jbd.READ, jbd.CMD_BASIC)))
                    self.st.parse_cells(await self._request(jbd.build_request(jbd.READ, jbd.CMD_CELLS)))
                    self.known = True
                    await self.ok()
                    if self._rebuild_sensors():
                        self.log.info("%d cells, %d NTC — republishing discovery", *self._shape)
                        await self.rediscover()
                    await self.publish_state()
                except Exception as e:
                    await self.error(str(e) or type(e).__name__)
                await asyncio.sleep(self.interval)
        finally:
            await self._disconnect()

    # ---- state·명령 ----

    def state(self) -> dict[str, Any]:
        s = self.st.to_json()
        s["ble"] = {"connected": bool(self.client and self.client.is_connected), **self.peer}
        return s

    def switch_state(self, name: str) -> bool | None:
        if not self.known:
            return None
        return {"charge_fet": self.st.chg_fet, "discharge_fet": self.st.dis_fet}.get(name)

    async def write_switch(self, name: str, on: bool) -> bool:
        chg, dis = self.st.chg_fet, self.st.dis_fet
        if name == "charge_fet":
            chg = on
        elif name == "discharge_fet":
            dis = on
        else:
            return False
        try:
            await self._request(jbd.mosfet_request(chg, dis))
        except Exception as e:
            self.log.warning("mosfet write failed: %s", e)
            return False
        self.st.chg_fet, self.st.dis_fet = chg, dis
        return True
