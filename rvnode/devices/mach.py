"""Adria MACH-BASIC (BLE) — ESP src/modules/mach/Mach.cpp를 옮겼다. 엔티티 키·state JSON이 같아야 ESP 9FCEE4_mach_*를
넘겨받을 때 HA 엔티티·디스플레이·UPower 인터록(ac.mode)이 이어진다 (rv-controller docs/19, docs/22, docs/29 §3).

장치: Schaudt CSV416(배터리·물탱크·조명·펌프, LIN 2), Alde 3020(보일러, LIN 1), Truma Aventa(에어컨, LIN 1),
MACH 내장 가속도계(수평). 패널이 꺼지면 패널 스위치 말고는 HA에서 unavailable(state.live), 에어컨은 230V가 있을 때만
답한다(state.ac.ok). 폰 앱과 동시에 연결된다.

BLE: Nordic UART(6E400001…)로 MUX/DLP 프레임, CB2D0004 서비스로 할당 ID 핸드셰이크. 쓰기는 MITM 페어링(PIN, 기본 123456)이 필요하다.
6E400004(NUS 부트로더)와 FE59(DFU)는 절대 쓰지 않는다.

params (ESP와 같은 이름):
  name / address / pin           BLE (기본 MACH-BASIC / "" / 123456)
  ebl, alde, aventa              {enabled, lin(1~3)}  aventa는 model·on_mode·default_set_c 도
  level                          {pitch_axis, pitch_sign, roll_axis, roll_sign, pitch_offset, roll_offset}
  interval_s                     폴링 주기 (기본 3)
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..ble import connecting, scanner
from ..device import Control, Device, Sensor, Switch
from ..frames import aventa as av
from ..frames import mach as m

NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca1e"
NUS_WRITE = "6e400002-b5a3-f393-e0a9-e50e24dcca1e"
NUS_NOTIFY = "6e400003-b5a3-f393-e0a9-e50e24dcca1e"
ID_WRITE = "cb2d0007-1138-42f4-8551-26e29ce1ec2e"
ID_NOTIFY = "cb2d0008-1138-42f4-8551-26e29ce1ec2e"
REPLY_TIMEOUT_S = 1.5

SWITCHES = (
    Switch("int_light", "Interior Light", "live"),
    Switch("ext_light", "Exterior Light", "live"),
    Switch("water_pump", "Water Pump", "live"),
    Switch("heater", "Heater (Gas)", "live"),
    Switch("panel", "Main Panel"),
    Switch("ac", "Air Conditioner", "ac.ok"),
)
EBL_SENSORS = (
    Sensor("ebl_bat_v", "Living Battery Voltage", "V", "voltage", "measurement", "ebl.bat_v", "live"),
    Sensor("ebl_bat_a", "Living Battery Current", "A", "current", "measurement", "ebl.bat_a", "live"),
    Sensor("ebl_bat_w", "Living Battery Power", "W", "power", "measurement", "ebl.bat_w", "live"),
    Sensor("ebl_soc", "Living Battery SOC (voltage)", "%", "battery", "measurement", "ebl.soc", "live"),
    Sensor("ebl_fresh", "Fresh Water", "%", "", "measurement", "ebl.fresh", "live"),
    Sensor("ebl_waste", "Waste Water", "%", "", "measurement", "ebl.waste", "live"),
    Sensor("ebl_inside", "Inside Temperature (EBL)", "°C", "temperature", "measurement", "ebl.inside", "live"),
    Sensor("ebl_outside", "Outside Temperature (EBL)", "°C", "temperature", "measurement", "ebl.outside", "live"),
    Sensor("ebl_mains", "Mains (230V)", "", "", "", "ebl.mains", "live"),
    Sensor("ebl_engine", "Engine Running", "", "", "", "ebl.engine", "live"),
)
ALDE_SENSORS = (
    Sensor("alde_sofa", "Alde Room Temperature", "°C", "temperature", "measurement", "alde.sofa", "live"),
    Sensor("alde_outside", "Alde Outside Temperature", "°C", "temperature", "measurement", "alde.outside", "live"),
    Sensor("alde_set", "Alde Set Temperature", "°C", "temperature", "measurement", "alde.set", "live"),
    Sensor("alde_electric", "Alde Electric Level", "", "", "measurement", "alde.electric", "live"),
    Sensor("alde_water", "Alde Hot Water", "", "", "", "alde.water_text", "live"),
    Sensor("alde_heating", "Alde Heating", "", "", "", "alde.heating", "live"),
    Sensor("alde_error", "Alde Error", "", "", "", "alde.error", "live"),
)
ACCEL_SENSORS = (
    Sensor("level_pitch", "Level Front-Back", "°", "", "measurement", "accel.pitch", "accel.ok"),
    Sensor("level_roll", "Level Left-Right", "°", "", "measurement", "accel.roll", "accel.ok"),
    Sensor("level_pitch_text", "Level Front-Back (low side)", "", "", "", "accel.pitch_text", "accel.ok"),
    Sensor("level_roll_text", "Level Left-Right (low side)", "", "", "", "accel.roll_text", "accel.ok"),
)
AC_SENSORS = (Sensor("ac_room", "AC Room Temperature", "°C", "temperature", "measurement", "ac.room_c", "ac.ok"),)
ALDE_CONTROLS = (
    Control("alde_set_temp", "Alde Set Temperature", "number", "alde.set", 5, 30, 1, "°C", avail="live"),
    Control("alde_energy", "Alde Energy Source", "select", "alde.energy", options=m.ALDE_ENERGY_NAMES, avail="live"),
    Control("alde_water", "Alde Hot Water", "select", "alde.water_text", options=m.ALDE_WATER_NAMES, avail="live"),
)
AC_CONTROLS = (
    Control("ac_set_temp", "AC Set Temperature", "number", "ac.set_c", 16, 31, 1, "°C", avail="ac.ok"),
    Control("ac_mode", "AC Mode", "select", "ac.mode", options=("off", "fan", "cool", "heat", "auto"), avail="ac.ok"),
    Control("ac_fan", "AC Fan", "select", "ac.fan", options=("low", "mid", "high", "night"), avail="ac.ok"),
)
AC_MODE_BY_NAME = {v: k for k, v in av.MODE_NAMES.items()}
AC_FAN_BY_NAME = {v: k for k, v in av.FAN_NAMES.items()}


class DeviceError(Exception):
    """"!id%E…" — 패널이 꺼져 LIN 버스가 잠들면 이렇게 온다."""


def _lin(v: Any, default: int) -> int:
    """사용자 설정 LIN 번호(1~3, 앱 표기) → MUX 인터페이스(0~2)."""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return n - 1 if 1 <= n <= 3 else default


class Mach(Device):
    type = "mach"
    switches = SWITCHES
    key_sensors = "ebl_soc,ebl_bat_v,ebl_bat_a,ebl_fresh,alde_sofa"
    display_ring = "battery:ebl.soc,ebl.bat_w"

    def __init__(self, cfg, node):
        super().__init__(cfg, node)
        p = self.params
        self.ble_name = str(p.get("name", "MACH-BASIC") or "MACH-BASIC")
        self.ble_address = str(p.get("address", "") or "")
        self.pin = int(p.get("pin", 123456))
        sub = lambda k: p.get(k) or {}  # noqa: E731
        self.use_ebl = bool(sub("ebl").get("enabled", True))
        self.use_alde = bool(sub("alde").get("enabled", True))
        self.use_ac = bool(sub("aventa").get("enabled", True))
        self.ebl_lin = _lin(sub("ebl").get("lin"), m.LIN_2)
        self.alde_lin = _lin(sub("alde").get("lin"), m.LIN_1)
        self.ac_lin = _lin(sub("aventa").get("lin"), m.LIN_1)
        self.ac_model = av.MODELS.get(str(sub("aventa").get("model", "comfort")), av.COMFORT)
        self.ac_on_mode = AC_MODE_BY_NAME.get(str(sub("aventa").get("on_mode", "auto")), av.AUTO)
        self.ac_default_set = int(sub("aventa").get("default_set_c", 22))
        lv = sub("level")
        axis = {"x": 0, "y": 1, "z": 2}
        self.pitch_axis = axis.get(str(lv.get("pitch_axis", "z")), 2)
        self.roll_axis = axis.get(str(lv.get("roll_axis", "y")), 1)
        self.pitch_sign = int(lv.get("pitch_sign", -1))
        self.roll_sign = int(lv.get("roll_sign", 1))
        self.pitch_offset = float(lv.get("pitch_offset", 0))
        self.roll_offset = float(lv.get("roll_offset", 0))
        self.interval = float(p.get("interval_s", 3))

        self.sensors = ACCEL_SENSORS + (EBL_SENSORS if self.use_ebl else ()) + \
            (ALDE_SENSORS if self.use_alde else ()) + (AC_SENSORS if self.use_ac else ())
        self.controls = (ALDE_CONTROLS if self.use_alde else ()) + (AC_CONTROLS if self.use_ac else ())

        self.ebl = m.Ebl()
        self.alde = m.Alde()
        self.ac: av.Status | None = None
        self.ac_raw = b""
        self.ac_result = ""
        self.accel: m.Accel | None = None
        self.accel_avg: list[float] | None = None
        self.accel_result = ""

        self.client = None
        self.assigned_id: int | None = None
        self.msg_id = 0
        self.peer = {"address": "", "name": ""}
        self._rx = bytearray()
        self._rx_event = asyncio.Event()
        self._id_event = asyncio.Event()
        self._lock = asyncio.Lock()

    # ---- BLE ----

    @property
    def connected(self) -> bool:
        return bool(self.client and self.client.is_connected and self.assigned_id is not None)

    def _on_nus(self, _c, data: bytearray) -> None:
        if len(self._rx) + len(data) > 512:
            self._rx.clear()
        self._rx.extend(data)
        self._rx_event.set()

    def _on_id(self, _c, data: bytearray) -> None:
        if data:
            self.assigned_id = data[0]
            self._id_event.set()

    async def _connect(self) -> None:
        from ..bluez_agent import ensure_agent

        await ensure_agent(self.pin)
        async with connecting():
            await self._connect_locked()

    async def _connect_locked(self) -> None:
        from bleak import BleakClient

        if self.ble_address:
            dev = await scanner.find(lambda d, adv: d.address.upper() == self.ble_address.upper(), timeout=15)
        else:
            dev = await scanner.find(lambda d, adv: (d.name or adv.local_name) == self.ble_name, timeout=15)
        if dev is None:
            raise ConnectionError(f"{self.ble_address or self.ble_name} not found")
        client = BleakClient(dev, timeout=20, disconnected_callback=lambda _c: self._rx_event.set())
        await client.connect()
        try:
            await client.pair()  # 본딩돼 있으면 바로 끝난다. 아니면 에이전트가 PIN을 넣는다
        except Exception as e:
            self.log.warning("pair: %s (already bonded?)", e)
        self.assigned_id = None
        self._id_event.clear()
        await client.start_notify(NUS_NOTIFY, self._on_nus)
        await client.start_notify(ID_NOTIFY, self._on_id)
        await asyncio.sleep(0.2)
        await client.write_gatt_char(ID_WRITE, b"\x01", response=True)  # 앱과 같은 핸드셰이크 → 1바이트 할당 ID
        try:
            await asyncio.wait_for(self._id_event.wait(), 2)
        except asyncio.TimeoutError:
            await client.disconnect()
            raise ConnectionError("no sender ID") from None
        self.client = client
        self.peer = {"address": dev.address, "name": dev.name or ""}
        self.log.info("connected %s, id %d", dev.address, self.assigned_id)

    async def _disconnect(self) -> None:
        c, self.client, self.assigned_id = self.client, None, None
        if c is not None:
            try:
                await c.disconnect()
            except Exception:
                pass

    async def _request(self, iface: int, cmd: str, payload: bytes, want_data: bool = True) -> bytes:
        async with self._lock:
            if not self.connected:
                raise ConnectionError("not connected")
            self.msg_id = self.msg_id % 99 + 1
            frame = m.build_request(iface, self.assigned_id, self.msg_id, cmd, payload)
            self._rx.clear()
            self._rx_event.clear()
            await self.client.write_gatt_char(NUS_WRITE, frame, response=True)
            loop = asyncio.get_running_loop()
            deadline, got_ok = loop.time() + REPLY_TIMEOUT_S, False
            while True:
                off = 0
                while (found := m.find_mux_frame(self._rx, off)) is not None:
                    s, n = found
                    dlp = m.unmux(bytes(self._rx[s : s + n]))
                    off = s + n
                    if not dlp:
                        continue
                    kind, data = m.parse_reply(dlp, self.msg_id)
                    if kind == m.REPLY_DATA:
                        return data
                    if kind == m.REPLY_TIMEOUT:
                        raise TimeoutError(f"LIN timeout ({cmd})")
                    if kind == m.REPLY_ERROR:
                        raise DeviceError(f"device error ({cmd})")
                    if kind == m.REPLY_OK:
                        got_ok = True
                if got_ok and not want_data:
                    return b""
                left = deadline - loop.time()
                if left <= 0 or not self.client.is_connected:
                    raise TimeoutError(f"{'no data' if got_ok else 'no reply'} ({cmd})")
                self._rx_event.clear()
                try:
                    await asyncio.wait_for(self._rx_event.wait(), left)
                except asyncio.TimeoutError:
                    pass

    async def _read_lin(self, iface: int, frame_id: int) -> bytes:
        return await self._request(iface, m.CMD_READ_LIN, m.read_lin_payload(frame_id))

    async def _write_lin(self, iface: int, frame_id: int, ctrl: bytes) -> None:
        await self._request(iface, m.CMD_WRITE_LIN, m.write_lin_payload(frame_id, ctrl), want_data=False)

    # ---- 읽기 ----

    async def _read_ebl(self, frame_id: int) -> None:
        r = await self._read_lin(self.ebl_lin, frame_id)
        was_mains, was_valid = self.ebl.mains, self.ebl.status_valid
        ok = m.decode_ebl_status(r, self.ebl) if frame_id == m.EBL_STATUS else m.decode_ebl_battery(r, self.ebl)
        if not ok:
            raise ValueError(f"EBL {frame_id:02X}: short reply {len(r)}")
        if frame_id == m.EBL_STATUS and (not was_valid or self.ebl.mains != was_mains):
            self.log.info("mains (230V) %s", "ON" if self.ebl.mains else "off")

    async def _read_alde(self) -> None:
        r = await self._read_lin(self.alde_lin, m.ALDE_STATUS)
        was = self.alde.raw if self.alde.valid else None
        if not m.decode_alde(r, self.alde):
            raise ValueError(f"Alde: short reply {len(r)}")
        if was != self.alde.raw:
            self.log.info("alde 0x1B [6..9] %s", self.alde.raw.hex(" "))

    async def _read_aventa(self) -> bool:
        """응답이 없어도 폴링 오류로 치지 않는다(에어컨만 unavailable)."""
        try:
            r = await self._read_lin(self.ac_lin, av.FRAME_STATUS)
            if len(r) < m.LIN_REPLY_LEN:
                raise ValueError(f"short reply {len(r)}")
        except Exception as e:
            err = str(e) or type(e).__name__
            if self.ac is not None or self.ac_result != err:
                self.log.info("aventa 0x17 %s", err)
            self.ac, self.ac_result = None, err
            return False
        if self.ac is None or bytes(r[: m.LIN_REPLY_LEN]) != self.ac_raw:
            self.log.info("aventa 0x17 reply %s", r[: m.LIN_REPLY_LEN].hex(" "))
        self.ac_raw = bytes(r[: m.LIN_REPLY_LEN])
        self.ac = av.parse_status(r[3:11])
        self.ac_result = "ok"
        return True

    async def _read_accel(self) -> None:
        try:
            r = await self._request(m.MISC, m.CMD_GET_ACCEL, b"\x00")
            a = m.decode_accel(r)
            if a is None:
                raise ValueError(f"accel: reply {len(r)} bytes")
        except Exception as e:
            err = str(e) or type(e).__name__
            if self.accel_result != err:
                self.log.info("accel %s", err)
            self.accel, self.accel_avg, self.accel_result = None, None, err
            return
        raw = [a.x, a.y, a.z]  # 1 LSB ≈ 0.22° — 지수 평균으로 다듬는다
        self.accel_avg = raw if self.accel_avg is None else [o * 0.7 + n * 0.3 for o, n in zip(self.accel_avg, raw)]
        self.accel, self.accel_result = a, "ok"

    def _drop(self, what: str) -> None:
        """패널 OFF면 0x16·0x1B 등이 장치 오류로 온다 — 그 값만 버리고 폴링은 성공으로 친다."""
        if what == "ebl_status":
            self.ebl.status_valid = True
            self.ebl.panel = self.ebl.int_light = self.ebl.ext_light = self.ebl.aux = self.ebl.pump = False
        elif what == "ebl_battery":
            self.ebl.battery_valid = False
        elif what == "alde":
            self.alde.valid = False

    async def _poll_once(self) -> None:
        steps = []
        if self.use_ebl:
            steps += [("ebl_status", lambda: self._read_ebl(m.EBL_STATUS)),
                      ("ebl_battery", lambda: self._read_ebl(m.EBL_BATTERY))]
        if self.use_alde:
            steps.append(("alde", self._read_alde))
        for name, fn in steps:
            try:
                await fn()
            except DeviceError:
                self._drop(name)
        if self.use_ac:
            await self._read_aventa()
        await self._read_accel()

    async def run(self) -> None:
        try:
            while True:
                if not self.connected:
                    await self._disconnect()
                    try:
                        await self._connect()
                    except Exception as e:
                        await self.error(str(e) or type(e).__name__)
                        await asyncio.sleep(10)
                        continue
                try:
                    await self._poll_once()
                    await self.ok()
                    await self.publish_state()
                except Exception as e:
                    await self.error(str(e) or type(e).__name__)
                    if not (self.client and self.client.is_connected):
                        await self._disconnect()
                await asyncio.sleep(self.interval)
        finally:
            await self._disconnect()

    # ---- state ----

    @property
    def live(self) -> bool:
        return not self.use_ebl or (self.ebl.status_valid and self.ebl.panel)

    def state(self) -> dict[str, Any]:
        out: dict[str, Any] = {"live": self.live,
                               "ble": {"connected": self.connected, **self.peer, "param": "address"}}
        e = self.ebl
        if self.use_ebl and (e.status_valid or e.battery_valid):
            j: dict[str, Any] = {}
            if e.battery_valid and e.battery_installed:
                j.update(bat_v=round(e.battery_v, 2), bat_a=round(e.battery_a, 2),
                         bat_w=round(e.battery_v * e.battery_a), soc=m.ebl_soc_from_voltage(e.battery_v))
            if e.status_valid:
                j.update(fresh=e.fresh_pct, waste=e.waste_pct)
                if e.inside_ok:
                    j["inside"] = e.inside_c
                if e.outside_ok:
                    j["outside"] = e.outside_c
                j.update(int_light=e.int_light, ext_light=e.ext_light, pump=e.pump, mains=int(e.mains),
                         engine=int(e.engine), panel=e.panel, fresh_defect=e.fresh_defect, waste_defect=e.waste_defect)
            out["ebl"] = j
        if self.use_ac:
            v: dict[str, Any] = {"ok": self.ac is not None and self.live}
            if self.ac is not None:
                v.update(raw=self.ac_raw.hex(), room_c=round(self.ac.room_c, 1), set_c=round(self.ac.set_c, 1),
                         mode=av.MODE_NAMES.get(self.ac.mode, "unknown"), fan=av.FAN_NAMES.get(self.ac.fan, "unknown"),
                         manual=self.ac.manual, error=self.ac.error)
            elif self.ac_result:
                v["reply"] = self.ac_result
            out["ac"] = v
        g: dict[str, Any] = {"ok": self.accel is not None}
        if self.accel is not None and self.accel_avg:
            a = self.accel
            up, deg = m.tilt(tuple(self.accel_avg))
            g.update(x=round(a.x, 3), y=round(a.y, 3), z=round(a.z, 3), scale=a.scale, up="xyz"[up])
            for k in range(3):
                if k != up:
                    g[f"tilt_{'xyz'[k]}"] = round(deg[k], 1)
            pitch = round(self.pitch_sign * deg[self.pitch_axis] - self.pitch_offset, 1)
            roll = round(self.roll_sign * deg[self.roll_axis] - self.roll_offset, 1)
            g.update(pitch=pitch, roll=roll, pitch_text=m.low_side(pitch, "뒤가", "앞이"),
                     roll_text=m.low_side(roll, "좌측이", "우측이"))
        elif self.accel_result:
            g["reply"] = self.accel_result
        out["accel"] = g
        a = self.alde
        if self.use_alde and a.valid:
            al: dict[str, Any] = {}
            if a.sofa_ok:
                al["sofa"] = a.sofa_c
            if a.bed_ok:
                al["bed"] = a.bed_c
            if a.outside_ok:
                al["outside"] = a.outside_c
            al.update(set=a.set_c, gas=a.gas, electric=a.electric, prio="gas" if a.prio_gas else "electric",
                      water=a.water, water_text=("off", "normal", "boost", "?")[a.water & 3],
                      energy=m.ALDE_ENERGY_NAMES[m.alde_energy_index(a)], panel=a.panel, auto=a.auto_mode,
                      heating=int(a.heating), error=int(a.error))
            out["alde"] = al
        return out

    def switch_state(self, name: str) -> bool | None:
        if name == "heater":
            return self.alde.gas if self.use_alde and self.alde.valid else None
        if name == "ac":
            return (self.ac.mode != av.OFF) if self.use_ac and self.ac is not None else None
        if not (self.use_ebl and self.ebl.status_valid):
            return None
        return {"int_light": self.ebl.int_light, "ext_light": self.ebl.ext_light, "water_pump": self.ebl.pump,
                "panel": self.ebl.panel}.get(name)

    # ---- 명령 (쓰고 나서 다시 읽어 반영됐는지 본다) ----

    def _ac_set_c(self) -> int:
        c = self.ac.set_c if self.ac else 0
        return round(c) if av.SET_MIN_C <= c <= av.SET_MAX_C else self.ac_default_set

    async def _write_ac(self, ctrl: bytes) -> bool:
        ok = 0
        for _ in range(2):  # 앱(Z3 S0)처럼 두 번 보낸다
            try:
                await self._write_lin(self.ac_lin, av.FRAME_CONTROL, ctrl)
                ok += 1
            except Exception as e:
                self.log.warning("ac write: %s", e)
        if not ok:
            return False
        await asyncio.sleep(1)
        return await self._read_aventa()

    async def _write_alde(self, ctrl: bytes) -> bool:
        await self._write_lin(self.alde_lin, m.ALDE_CONTROL, ctrl)
        await asyncio.sleep(1)
        await self._read_alde()
        return True

    async def _read_status_after_write(self) -> None:
        try:
            await self._read_ebl(m.EBL_STATUS)
        except DeviceError:
            self._drop("ebl_status")  # 패널을 끄면 버스가 잠든다 — 꺼진 것으로 본다

    async def write_switch(self, name: str, on: bool) -> bool:
        if not self.connected:
            return False
        if name != "panel" and not self.live:
            self.log.warning("%s: panel off", name)
            return False
        try:
            if name == "ac":
                if not self.use_ac or self.ac is None or not await self._read_aventa():
                    return False
                ctrl = av.power(self.ac, self.ac_model, on, self.ac_on_mode, self._ac_set_c())
                if ctrl is None:
                    self.log.warning("ac: mode not supported by this model")
                    return False
                await self._write_ac(ctrl)
            elif name == "heater":
                if not self.use_alde:
                    return False
                await self._read_alde()
                await self._write_alde(m.alde_set_gas(self.alde, on))
            else:
                if not self.use_ebl:
                    return False
                item = {"int_light": m.EBL_INT_LIGHT, "ext_light": m.EBL_EXT_LIGHT, "water_pump": m.EBL_PUMP,
                        "panel": m.EBL_PANEL}[name]
                await self._write_lin(self.ebl_lin, m.EBL_CONTROL, m.ebl_control(item, on))
                await asyncio.sleep(1)
                await self._read_status_after_write()
                for _ in range(5):  # 패널 끄기 직후 1초는 아직 켜진 값이 올 수 있다
                    if not (name == "panel" and not on and self.ebl.panel):
                        break
                    await asyncio.sleep(1)
                    await self._read_status_after_write()
        except Exception as e:
            self.log.warning("%s write: %s", name, e)
            return False
        return self.switch_state(name) == on

    async def write_control(self, name: str, value: str) -> bool:
        if not self.connected or not self.live:
            return False
        try:
            if name.startswith("ac_"):
                if not self.use_ac or self.ac is None or not await self._read_aventa():
                    return False
                if name == "ac_set_temp":
                    t = round(float(value))
                    if not av.SET_MIN_C <= t <= av.SET_MAX_C:
                        return False
                    return await self._write_ac(av.set_temp(self.ac, self.ac_model, t)) and round(self.ac.set_c) == t
                if name == "ac_mode":
                    mode = AC_MODE_BY_NAME.get(value)
                    ctrl = None if mode is None else av.set_mode(self.ac, self.ac_model, mode, self._ac_set_c())
                    return ctrl is not None and await self._write_ac(ctrl) and self.ac.mode == mode
                if name == "ac_fan":
                    fan = AC_FAN_BY_NAME.get(value)
                    ctrl = None if fan is None else av.set_fan(self.ac, self.ac_model, fan, self._ac_set_c())
                    return ctrl is not None and await self._write_ac(ctrl) and self.ac.fan == fan
                return False
            if not self.use_alde:
                return False
            await self._read_alde()
            if name == "alde_set_temp":
                c = round(float(value))
                if not 5 <= c <= 30:
                    return False
                return await self._write_alde(m.alde_set_temp(self.alde, c)) and self.alde.set_c == c
            if name == "alde_energy" and value in m.ALDE_ENERGY_NAMES:
                k = m.ALDE_ENERGY_NAMES.index(value)
                return await self._write_alde(m.alde_set_energy(self.alde, k)) and m.alde_energy_index(self.alde) == k
            if name == "alde_water" and value in m.ALDE_WATER_NAMES:
                k = m.ALDE_WATER_NAMES.index(value)
                return await self._write_alde(m.alde_set_water(self.alde, k)) and self.alde.water == k
        except Exception as e:
            self.log.warning("%s write: %s", name, e)
        return False
