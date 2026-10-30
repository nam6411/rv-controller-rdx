"""버드뷰 노브 리모컨 흉내 (CC1101, 433.92MHz 2-FSK) — ESP irrf `bv:` 송신을 옮겼다 (rv-controller docs/25 §10).

HA 버튼(왼쪽·오른쪽·누르기·길게 누르기)을 누르면 리모컨과 같은 패킷을 보낸다. seq는 리모컨처럼 송신마다 아래 4비트가 준다.
누르기류는 같은 패킷을 22ms 간격으로 두 번 보낸다(리모컨 실측).
배선은 rvnode.cc1101 (SPI3 + CSN 핀 15, GDO 없음). 수신 처리는 하지 않는다 — 칩은 송신 뒤 수신 상태로 둔다(MCSM1).

params:
  spi        /dev/spidev3.0
  csn        {chip: gpiochip3, line: 8}
  id         리모컨 ID (기본 0xA48A5E05)
  tx_dbm     송신 출력 (기본 10)
  check_s    칩 상태 확인 주기 (기본 30)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ..cc1101 import ST_IDLE, ST_RX, Cc1101, Cc1101Error, Config
from ..device import Control, Device, Sensor
from ..frames import birdview_rf as bv

BUTTONS = {"left": bv.LEFT, "right": bv.RIGHT, "click": bv.CLICK, "long_press": bv.LONG_START}
PRESS_GAP_S = 0.022


class BvRemote(Device):
    type = "bvremote"
    controls = (
        Control("left", "Knob left", "button"),
        Control("right", "Knob right", "button"),
        Control("click", "Knob press", "button"),
        Control("long_press", "Knob long press", "button"),
    )
    sensors = (Sensor("last", "Last sent", path="last.action"),)

    def __init__(self, cfg, node):
        super().__init__(cfg, node)
        p = self.params
        csn = p.get("csn") or {}
        self.spi = str(p.get("spi", "/dev/spidev3.0"))
        self.csn_chip = str(csn.get("chip", "gpiochip3"))
        self.csn_line = int(csn.get("line", 8))
        self.ident = int(str(p.get("id", hex(bv.DEFAULT_ID))), 0)
        self.tx_dbm = int(p.get("tx_dbm", 10))
        self.check_s = float(p.get("check_s", 30))
        self.radio: Cc1101 | None = None
        self.version = 0
        self.seq = 0x1E  # 쉬었다 누른 리모컨처럼 1E부터
        self.sent = 0
        self.last: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    # ---- 무선 ----

    def _open(self) -> Cc1101:
        r = Cc1101(self.spi, self.csn_chip, self.csn_line)
        try:
            r.begin(Config(freq_hz=bv.FREQ_HZ, baud=bv.BAUD, deviation_hz=bv.DEVIATION_HZ,
                           sync=bv.sync_word(self.ident), packet_len=bv.PAYLOAD,
                           preamble_bytes=bv.PREAMBLE_BYTES, tx_dbm=self.tx_dbm))
        except Exception:
            r.close()
            raise
        return r

    def _close(self) -> None:
        if self.radio:
            try:
                self.radio.close()
            except OSError:
                pass
            self.radio = None

    def _send_blocking(self, action: int) -> bool:
        r = self.radio
        assert r
        ok = True
        for i in range(2 if action in (bv.CLICK, bv.LONG_START, bv.LONG_END) else 1):
            if i:
                time.sleep(PRESS_GAP_S)
            ok &= r.send(bv.payload(self.seq, action, self.ident))
        return ok

    def _healthy(self) -> bool:
        r = self.radio
        return bool(r) and r.status(0x31) == self.version and r.marc_state() in (ST_RX, ST_IDLE)

    async def run(self) -> None:
        while True:
            async with self._lock:
                try:
                    if not self.radio:
                        self.radio = await asyncio.to_thread(self._open)
                        self.version = self.radio.version
                        self.log.info("CC1101 ver 0x%02X ready (433.92MHz 2-FSK)", self.version)
                    elif not await asyncio.to_thread(self._healthy):
                        raise Cc1101Error("chip not responding")
                    healthy = True
                except (Cc1101Error, OSError) as e:
                    self._close()
                    healthy = False
                    await self.error(f"CC1101: {e}")
            if healthy:
                await self.ok()
                await self.publish_state()
            await asyncio.sleep(self.check_s if healthy else 10)

    async def write_control(self, name: str, value: str) -> bool:
        action = BUTTONS.get(name)
        if action is None:
            return False
        async with self._lock:
            if not self.radio:
                return False
            seq = self.seq
            try:
                ok = await asyncio.to_thread(self._send_blocking, action)
            except OSError as e:
                self._close()
                await self.error(f"CC1101: {e}")
                return False
            self.seq = bv.next_seq(seq)
            self.sent += 1
            self.last = {"action": name, "seq": seq, "byte": f"{action:02X}", "ok": ok,
                         "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        return ok

    def state(self) -> dict[str, Any]:
        return {"ready": self.radio is not None, "version": self.version, "sent": self.sent, "last": self.last,
                "id": f"{self.ident:08X}"}
