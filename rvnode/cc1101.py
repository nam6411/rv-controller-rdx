"""CC1101 드라이버 (Linux spidev + CSN GPIO) — ESP src/modules/irrf/Cc1101.cpp를 옮겼다 (git 75da6e0^).

배선 (docs/18 §12): SPI3(핀 19·21·23) /dev/spidev3.0, CSN = 핀 15(gpiochip3 line 8)를 소프트웨어로 내린다.
GDO 선은 잇지 않았다 — 수신은 RXBYTES를 보고 FIFO에서 꺼낸다(고정 길이 패킷 + 상태 2바이트).
spidev 파이썬 모듈 없이 표준 라이브러리 ioctl로 SPI를 쓴다. 호출은 모두 동기(수 ms) — asyncio에서는 to_thread로 부른다.
"""

from __future__ import annotations

import array
import ctypes
import fcntl
import os
import time
from dataclasses import dataclass

FXOSC = 26_000_000

SPI_IOC_WR_MODE = 0x40016B01
SPI_IOC_WR_MAX_SPEED_HZ = 0x40046B04
SPI_IOC_MESSAGE_1 = 0x40206B00

SRES, SRX, STX, SIDLE, SFRX, SFTX = 0x30, 0x34, 0x35, 0x36, 0x3A, 0x3B
PARTNUM, VERSION, FREQEST, MARCSTATE, RXBYTES = 0x30, 0x31, 0x32, 0x35, 0x3B
PATABLE, FIFO = 0x3E, 0x3F
ST_IDLE, ST_RX, ST_RXFIFO_OVERFLOW, ST_TX, ST_TX_END, ST_TXFIFO_UNDERFLOW = 0x01, 0x0D, 0x11, 0x13, 0x14, 0x16


class Cc1101Error(Exception):
    pass


# ---- 레지스터 값 계산 (ESP irrf::cc1101calc) ----

def freq_word(hz: int) -> int:
    return round(hz * 65536 / FXOSC)


def data_rate(baud: float) -> tuple[int, int]:
    """속도 = (256 + M) × 2^E × fxosc / 2^28 → (E, M)"""
    best = None
    for e in range(16):
        m = round(baud * 2**28 / (FXOSC * 2**e) - 256)
        if 0 <= m <= 255:
            err = abs((256 + m) * 2**e * FXOSC / 2**28 - baud)
            if best is None or err < best[0]:
                best = (err, e, m)
    assert best
    return best[1], best[2]


def deviatn(hz: float) -> int:
    return min(((e << 4 | m) for e in range(8) for m in range(8)),
               key=lambda v: abs(FXOSC / 131072 * (8 + (v & 7)) * 2 ** (v >> 4) - hz))


def chan_bw(hz: float) -> int:
    """요청값 이상인 수신 대역폭 중 가장 좁은 것 → MDMCFG4[7:4]"""
    ok = [(FXOSC / (8 * (4 + m) * 2**e), e << 2 | m) for e in range(4) for m in range(4)]
    ok = [x for x in ok if x[0] + 1 >= hz]
    return min(ok)[1] if ok else 0


def preamble_code(n: int) -> int:
    for i, v in enumerate((2, 3, 4, 6, 8, 12, 16, 24)):
        if n <= v:
            return i
    return 7


def pa_table_433(dbm: int) -> int:
    for d, v in ((10, 0xC0), (7, 0xC8), (5, 0x84), (0, 0x60), (-10, 0x34), (-15, 0x1D), (-20, 0x0E), (-30, 0x12)):
        if dbm >= d:
            return v
    return 0x12


def rssi_dbm(raw: int) -> float:
    return (raw - 256 if raw >= 128 else raw) / 2 - 74


def freq_offset_hz(raw: int) -> int:
    return (raw - 256 if raw >= 128 else raw) * FXOSC // 16384


@dataclass
class Config:
    freq_hz: int = 433_920_000
    baud: int = 19390
    deviation_hz: int = 25_000
    rx_bw_hz: int = 100_000
    sync: int = 0
    packet_len: int = 12
    preamble_bytes: int = 8
    tx_dbm: int = 10


@dataclass
class Packet:
    data: bytes
    rssi_dbm: float
    lqi: int
    freq_off_hz: int


class _Transfer(ctypes.Structure):
    _fields_ = [("tx_buf", ctypes.c_uint64), ("rx_buf", ctypes.c_uint64), ("len", ctypes.c_uint32),
                ("speed_hz", ctypes.c_uint32), ("delay_usecs", ctypes.c_uint16), ("bits_per_word", ctypes.c_uint8),
                ("cs_change", ctypes.c_uint8), ("tx_nbits", ctypes.c_uint8), ("rx_nbits", ctypes.c_uint8),
                ("word_delay_usecs", ctypes.c_uint8), ("pad", ctypes.c_uint8)]


class Cc1101:
    def __init__(self, spi: str = "/dev/spidev3.0", csn_chip: str = "gpiochip3", csn_line: int = 8, hz: int = 500_000):
        import gpiod
        from gpiod.line import Direction, Value

        self._value = Value
        self.hz = hz
        self.fd = os.open(spi, os.O_RDWR)
        fcntl.ioctl(self.fd, SPI_IOC_WR_MODE, array.array("B", [0]))
        fcntl.ioctl(self.fd, SPI_IOC_WR_MAX_SPEED_HZ, array.array("I", [hz]))
        self.csn_line = csn_line
        self.req = gpiod.request_lines(f"/dev/{csn_chip}", consumer="rvnode-cc1101",
                                       config={csn_line: gpiod.LineSettings(direction=Direction.OUTPUT,
                                                                             output_value=Value.ACTIVE)})
        self.cfg = Config()
        self.version = 0

    def close(self) -> None:
        try:
            self.strobe(SIDLE)
        except OSError:
            pass
        self.req.release()
        os.close(self.fd)

    # ---- SPI ----

    def _csn(self, low: bool) -> None:
        self.req.set_value(self.csn_line, self._value.INACTIVE if low else self._value.ACTIVE)

    def xfer(self, data: bytes | list[int]) -> bytes:
        data = bytes(data)
        tx = ctypes.create_string_buffer(data, len(data))
        rx = ctypes.create_string_buffer(len(data))
        t = _Transfer(tx_buf=ctypes.addressof(tx), rx_buf=ctypes.addressof(rx), len=len(data),
                      speed_hz=self.hz, bits_per_word=8)
        self._csn(True)
        time.sleep(0.0001)  # CSn을 내린 뒤 칩 준비(SO Low)까지 — SO를 따로 볼 수 없어 넉넉히 기다린다
        try:
            fcntl.ioctl(self.fd, SPI_IOC_MESSAGE_1, t)
        finally:
            self._csn(False)
        return rx.raw

    def strobe(self, cmd: int) -> int:
        return self.xfer([cmd])[0]

    def read(self, addr: int) -> int:
        return self.xfer([addr | 0x80, 0])[1]

    def status(self, addr: int) -> int:
        return self.xfer([addr | 0xC0, 0])[1]

    def write(self, addr: int, v: int) -> None:
        self.xfer([addr, v])

    def write_burst(self, addr: int, data: bytes) -> None:
        self.xfer([addr | 0x40, *data])

    def read_burst(self, addr: int, n: int) -> bytes:
        return self.xfer([addr | 0xC0, *([0] * n)])[1:]

    def marc_state(self) -> int:
        return self.status(MARCSTATE) & 0x1F

    def wait_state(self, state: int, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while self.marc_state() != state:
            if time.monotonic() > end:
                return False
            time.sleep(0.0002)
        return True

    # ---- 설정 ----

    def begin(self, cfg: Config) -> None:
        self._csn(True); time.sleep(0.00001); self._csn(False); time.sleep(0.00005)
        self.strobe(SRES)
        time.sleep(0.001)
        part, self.version = self.status(PARTNUM), self.status(VERSION)
        if part != 0x00 or self.version in (0x00, 0xFF):
            raise Cc1101Error(f"not a CC1101 (part {part:02X} ver {self.version:02X})")
        self.cfg = cfg
        f = freq_word(cfg.freq_hz)
        e, m = data_rate(cfg.baud)
        # SmartRF Studio 433MHz 2-FSK 값을 바탕으로 패킷 형식만 바꿨다 (ESP와 같다)
        regs = {
            0x00: 0x29, 0x01: 0x2E, 0x02: 0x2E,          # IOCFG2..0: GDO 쓰지 않음
            0x03: 0x47,                                  # FIFOTHR
            0x04: cfg.sync >> 8 & 0xFF, 0x05: cfg.sync & 0xFF,
            0x06: cfg.packet_len,                        # PKTLEN
            0x07: 0x04,                                  # PKTCTRL1: 상태 2바이트(RSSI·LQI) 붙임
            0x08: 0x00,                                  # PKTCTRL0: 고정 길이, CRC·화이트닝 없음
            0x09: 0x00, 0x0A: 0x00, 0x0B: 0x06, 0x0C: 0x00,
            0x0D: f >> 16 & 0xFF, 0x0E: f >> 8 & 0xFF, 0x0F: f & 0xFF,
            0x10: chan_bw(cfg.rx_bw_hz) << 4 | e, 0x11: m,
            0x12: 0x02,                                  # MDMCFG2: 2-FSK, 동기어 16/16, 맨체스터 끔(칩을 직접 만든다)
            0x13: preamble_code(cfg.preamble_bytes) << 4 | 0x02,
            0x14: 0xF8, 0x15: deviatn(cfg.deviation_hz),
            0x16: 0x07, 0x17: 0x0F,                      # MCSM1: 받은 뒤·보낸 뒤 모두 수신으로
            0x18: 0x18,                                  # MCSM0: IDLE에서 나갈 때 자동 보정
            0x19: 0x16, 0x1A: 0x6C, 0x1B: 0x43, 0x1C: 0x40, 0x1D: 0x91,
            0x21: 0x56, 0x22: 0x10,
            0x23: 0xE9, 0x24: 0x2A, 0x25: 0x00, 0x26: 0x1F, 0x29: 0x59,
            0x2C: 0x81, 0x2D: 0x35, 0x2E: 0x09,
        }
        self.strobe(SIDLE)
        for a, v in regs.items():
            self.write(a, v)
        self.write_burst(PATABLE, bytes([pa_table_433(cfg.tx_dbm)]))
        if [self.read(a) for a in (0x0D, 0x0E, 0x0F)] != [f >> 16 & 0xFF, f >> 8 & 0xFF, f & 0xFF]:
            raise Cc1101Error("readback mismatch (SPI wiring?)")
        self.restart_rx()
        if not self.wait_state(ST_RX, 0.02):
            raise Cc1101Error(f"did not enter RX (state {self.marc_state():02X})")

    def restart_rx(self) -> None:
        self.strobe(SIDLE)
        self.wait_state(ST_IDLE, 0.005)
        self.strobe(SFRX)
        self.strobe(SRX)

    # ---- 송수신 ----

    def send(self, data: bytes) -> bool:
        if len(data) != self.cfg.packet_len:
            raise ValueError("packet length")
        self.strobe(SIDLE)
        self.wait_state(ST_IDLE, 0.005)
        self.strobe(SFTX)
        self.write_burst(FIFO, data)
        self.strobe(STX)
        # IDLE → 보정(≈0.8ms) → 송신 → MCSM1에 따라 수신. 송신에 들어간 것을 본 뒤에 끝을 기다린다
        end = time.monotonic() + 0.02 + (len(data) + 12) * 8 / self.cfg.baud
        saw_tx, st = False, 0
        while time.monotonic() < end:
            st = self.marc_state()
            if st in (ST_TX, ST_TX_END):
                saw_tx = True
            elif st == ST_TXFIFO_UNDERFLOW or (saw_tx and st in (ST_RX, ST_IDLE)):
                break
            time.sleep(0.0001)
        ok = saw_tx and st in (ST_RX, ST_IDLE)
        if st != ST_RX:
            self.strobe(SIDLE)
            self.wait_state(ST_IDLE, 0.005)
            self.strobe(SFTX)
            self.restart_rx()
        return ok

    def poll(self) -> Packet | None:
        # RXBYTES는 바뀌는 중에 읽으면 틀릴 수 있어 두 번 같게 읽힐 때까지 (에라타 SPI read sync)
        n = self.status(RXBYTES)
        for _ in range(4):
            again = self.status(RXBYTES)
            if again == n:
                break
            n = again
        if n & 0x80:
            self.restart_rx()
            return None
        need = self.cfg.packet_len + 2
        if (n & 0x7F) < need:
            if not n and self.marc_state() in (ST_IDLE, ST_RXFIFO_OVERFLOW):
                self.restart_rx()
            return None
        fe = self.status(FREQEST)
        buf = self.read_burst(FIFO, need)
        return Packet(buf[: self.cfg.packet_len], rssi_dbm(buf[-2]), buf[-1] & 0x7F, freq_offset_hz(fe))
