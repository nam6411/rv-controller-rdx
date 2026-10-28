"""GPS(gpsd) + 나침반(QMC5883L) — rv-controller docs/24 §4.1·§4.2·§7.3.

gpsd가 /dev/ttyS4를 쥐고, 여기서는 gpsd 소켓(2947)의 TPV·SKY 보고만 받는다(NMEA를 직접 해석하지 않는다).
나침반은 /dev/i2c-5 0x0D. 화살표 기준 자북 방위 = 265° − 칩 각도, 진북 = 자북 + 편각(서편 −).

발행 <prefix>/state (retain): 10초마다 + 50m 넘게 움직이면/fix가 바뀌면 바로. 웹은 state()를 1초마다 직접 읽는다.

params:
  gpsd            host:port (기본 127.0.0.1:2947)
  compass         true/false (기본 true)
  i2c_bus         5
  i2c_addr        0x0D
  declination     편각(°, 서편 −, 기본 −8.5)
  heading_offset  화살표 기준 = offset − 칩 각도 (기본 265)
  cal             {cx, cy, rx, ry} 하드·소프트 아이언 보정 (보정 버튼이 고쳐 쓴다)
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from typing import Any

from ..device import BinarySensor, Control, Device, Sensor

PUBLISH_EVERY_S = 10
MOVE_PUBLISH_M = 50
DEFAULT_CAL = {"cx": 351.0, "cy": -284.0, "rx": 377.0, "ry": 388.0}  # docs/24 §7.3 작업대 값


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def heading_deg(x: float, y: float, cal: dict[str, float], offset: float, declination: float) -> float:
    """보정한 X·Y → 진북 기준 방위(0~360)."""
    xn = (x - cal["cx"]) / (cal["rx"] or 1)
    yn = (y - cal["cy"]) / (cal["ry"] or 1)
    chip = math.degrees(math.atan2(yn, xn)) % 360
    return (offset - chip + declination) % 360


class GpsFix:
    """gpsd 보고서 → docs/24 §4.1 필드."""

    def __init__(self) -> None:
        self.fix = False
        self.mode = 0
        self.lat: float | None = None
        self.lon: float | None = None
        self.alt_m: float | None = None
        self.speed_kmh: float | None = None
        self.course: float | None = None
        self.utc: str | None = None
        self.sats = 0
        self.sats_seen = 0
        self.hdop: float | None = None
        self.last_report = 0.0

    def feed(self, msg: dict[str, Any], now: float) -> None:
        cls = msg.get("class")
        if cls == "TPV":
            self.last_report = now
            self.mode = int(msg.get("mode", 0))
            self.fix = self.mode >= 2 and "lat" in msg and "lon" in msg
            if self.fix:  # fix가 없으면 마지막 위치를 그대로 둔다
                self.lat, self.lon = float(msg["lat"]), float(msg["lon"])
                alt = msg.get("altMSL", msg.get("alt"))
                self.alt_m = round(float(alt), 1) if alt is not None else self.alt_m
                if "speed" in msg:
                    self.speed_kmh = round(float(msg["speed"]) * 3.6, 1)
                if "track" in msg:
                    self.course = round(float(msg["track"]))
            if "time" in msg:
                self.utc = str(msg["time"])[:19] + "Z"
        elif cls == "SKY":
            self.last_report = now
            sats = msg.get("satellites") or []
            if sats:
                self.sats_seen = len(sats)
                self.sats = sum(1 for s in sats if s.get("used"))
            if "uSat" in msg:
                self.sats = int(msg["uSat"])
            if "nSat" in msg:
                self.sats_seen = int(msg["nSat"])
            if "hdop" in msg:
                self.hdop = round(float(msg["hdop"]), 2)


class Qmc5883l:
    """QMC5883L: 연속, 200Hz, ±8G, OSR 512 (docs/24 §7.3)."""

    def __init__(self, bus: int, addr: int):
        from smbus2 import SMBus

        self.addr = addr
        self.bus = SMBus(bus)
        self.bus.write_byte_data(addr, 0x0B, 0x01)  # SET/RESET period
        self.bus.write_byte_data(addr, 0x09, 0x1D)  # OSR512 | 8G | 200Hz | continuous

    def read(self) -> tuple[int, int, int] | None:
        if not self.bus.read_byte_data(self.addr, 0x06) & 0x01:  # DRDY
            return None
        d = self.bus.read_i2c_block_data(self.addr, 0x00, 6)

        def s16(lo: int, hi: int) -> int:
            v = lo | (hi << 8)
            return v - 65536 if v & 0x8000 else v

        return s16(d[0], d[1]), s16(d[2], d[3]), s16(d[4], d[5])

    def close(self) -> None:
        self.bus.close()


class Gps(Device):
    type = "gps"
    sensors = (
        Sensor("lat", "Latitude", "°", path="lat"),
        Sensor("lon", "Longitude", "°", path="lon"),
        Sensor("alt_m", "Altitude", "m", "distance", "measurement"),
        Sensor("sats", "Satellites", "", "", "measurement"),
        Sensor("hdop", "HDOP", "", "", "measurement"),
        Sensor("speed_kmh", "Speed", "km/h", "speed", "measurement"),
        Sensor("course", "Course", "°", "", "measurement"),
        Sensor("heading", "Heading", "°", "", "measurement"),
    )
    binary_sensors = (BinarySensor("fix", "GPS fix"),)
    controls = (
        Control("compass_cal_start", "Compass calibration start", "button"),
        Control("compass_cal_stop", "Compass calibration stop", "button"),
    )
    key_sensors = "heading,sats,speed_kmh"

    def __init__(self, cfg, node, clock=time.monotonic):
        super().__init__(cfg, node)
        p = self.params
        host, _, port = str(p.get("gpsd", "127.0.0.1:2947")).partition(":")
        self.gpsd = (host or "127.0.0.1", int(port or 2947))
        self.use_compass = bool(p.get("compass", True))
        self.i2c_bus = int(p.get("i2c_bus", 5))
        self.i2c_addr = int(str(p.get("i2c_addr", 0x0D)), 0)
        self.declination = float(p.get("declination", -8.5))
        self.offset = float(p.get("heading_offset", 265))
        self.cal = {**DEFAULT_CAL, **(p.get("cal") or {})}
        self.clock = clock
        self.g = GpsFix()
        self.heading: float | None = None
        self.mag_strength: float | None = None
        self.compass_ok = False
        self.calibrating = False
        self._cal_min = [math.inf, math.inf]
        self._cal_max = [-math.inf, -math.inf]
        self._last_pub = 0.0
        self._last_pos: tuple[float, float] | None = None
        self._last_fix = False

    # ---- 발행 판단 ----

    def _should_publish(self, now: float) -> bool:
        if now - self._last_pub >= PUBLISH_EVERY_S or self.g.fix != self._last_fix:
            return True
        if self.g.fix and self._last_pos and self.g.lat is not None:
            return distance_m(*self._last_pos, self.g.lat, self.g.lon) > MOVE_PUBLISH_M
        return False

    async def _maybe_publish(self) -> None:
        now = self.clock()
        if self._should_publish(now):
            await self.publish_state()
            self._last_pub = now
            self._last_fix = self.g.fix
            if self.g.fix and self.g.lat is not None:
                self._last_pos = (self.g.lat, self.g.lon)

    # ---- 루프 ----

    async def run(self) -> None:
        tasks = [asyncio.create_task(self._gpsd_loop())]
        if self.use_compass:
            tasks.append(asyncio.create_task(self._compass_loop()))
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()

    async def _gpsd_loop(self) -> None:
        while True:
            try:
                reader, writer = await asyncio.open_connection(*self.gpsd)
                writer.write(b'?WATCH={"enable":true,"json":true};\n')
                await writer.drain()
                await self.ok()
                while True:
                    line = await asyncio.wait_for(reader.readline(), timeout=15)
                    if not line:
                        raise ConnectionError("gpsd closed")
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    self.g.feed(msg, self.clock())
                    if msg.get("class") in ("TPV", "SKY"):
                        await self._maybe_publish()
            except (OSError, ConnectionError, asyncio.TimeoutError) as e:
                await self.error(f"gpsd: {e}")
                await asyncio.sleep(5)

    async def _compass_loop(self) -> None:
        while True:
            try:
                q = await asyncio.to_thread(Qmc5883l, self.i2c_bus, self.i2c_addr)
            except Exception as e:
                self.compass_ok = False
                self.log.warning("compass: %s", e)
                await asyncio.sleep(10)
                continue
            self.compass_ok = True
            xs, ys, zs = [], [], []
            last = self.clock()
            try:
                while True:
                    r = await asyncio.to_thread(q.read)
                    if r:
                        x, y, z = r
                        xs.append(x), ys.append(y), zs.append(z)
                        if self.calibrating:
                            self._cal_min = [min(self._cal_min[0], x), min(self._cal_min[1], y)]
                            self._cal_max = [max(self._cal_max[0], x), max(self._cal_max[1], y)]
                    now = self.clock()
                    if now - last >= 1.0 and xs:  # 1초 평균
                        ax, ay, az = sum(xs) / len(xs), sum(ys) / len(ys), sum(zs) / len(zs)
                        self.heading = round(heading_deg(ax, ay, self.cal, self.offset, self.declination))
                        self.mag_strength = round(math.sqrt(ax * ax + ay * ay + az * az))
                        xs, ys, zs = [], [], []
                        last = now
                    await asyncio.sleep(0.02)
            except Exception as e:
                self.compass_ok = False
                self.log.warning("compass read: %s", e)
                await asyncio.to_thread(q.close)
                await asyncio.sleep(5)

    # ---- state·명령 ----

    def state(self) -> dict[str, Any]:
        g = self.g
        return {
            "fix": g.fix, "lat": g.lat, "lon": g.lon, "alt_m": g.alt_m, "sats": g.sats, "sats_seen": g.sats_seen,
            "hdop": g.hdop, "speed_kmh": g.speed_kmh, "course": g.course, "heading": self.heading, "utc": g.utc,
            "compass": {"ok": self.compass_ok, "strength": self.mag_strength, "calibrating": self.calibrating},
        }

    async def write_control(self, name: str, value: str) -> bool:
        if name == "compass_cal_start":
            self._cal_min, self._cal_max = [math.inf, math.inf], [-math.inf, -math.inf]
            self.calibrating = True
            self.log.info("compass calibration started — turn the vehicle a full circle")
            return True
        if name == "compass_cal_stop":
            if not self.calibrating:
                return False
            self.calibrating = False
            (x0, y0), (x1, y1) = self._cal_min, self._cal_max
            if not (x1 - x0 > 100 and y1 - y0 > 100):
                self.log.warning("compass calibration too small (x %s..%s, y %s..%s) — not saved", x0, x1, y0, y1)
                return False
            self.cal = {"cx": round((x0 + x1) / 2), "cy": round((y0 + y1) / 2),
                        "rx": round((x1 - x0) / 2), "ry": round((y1 - y0) / 2)}
            self.log.info("compass calibration: %s", self.cal)
            await asyncio.to_thread(self._save_cal)
            return True
        return False

    def _save_cal(self) -> None:
        from .. import settings
        from ..config import load_raw

        raw = load_raw(self.node.cfg_path)
        for d in raw.get("devices") or []:
            if d.get("slug") == self.slug:
                d.setdefault("params", {})["cal"] = dict(self.cal)
        settings.save_raw(self.node.cfg_path, raw)
