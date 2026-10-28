"""JBD 0xDD 프레임 — ESP src/modules/jbdbms/JbdFrame.h와 Jbdbms.cpp의 해석을 옮겼다 (rv-controller docs/04 §2·§3)."""

from __future__ import annotations

from dataclasses import dataclass, field

START, END = 0xDD, 0x77
READ, WRITE = 0xA5, 0x5A
CMD_BASIC, CMD_CELLS, CMD_MOSFET = 0x03, 0x04, 0xE1


def checksum(b: bytes | bytearray, frm: int, to: int) -> int:
    return (0x10000 - sum(b[frm:to])) & 0xFFFF


def build_request(rw: int, cmd: int, data: bytes = b"") -> bytes:
    f = bytearray([START, rw, cmd, len(data)]) + bytes(data)
    chk = checksum(f, 2, len(f))
    return bytes(f + bytes([chk >> 8, chk & 0xFF, END]))


def mosfet_request(charge: bool, discharge: bool) -> bytes:
    """§3.3 VAL bit0 = 충전 차단, bit1 = 방전 차단."""
    val = (0 if charge else 1) | (0 if discharge else 2)
    return build_request(WRITE, CMD_MOSFET, bytes([0x00, val]))


class VerifyError(ValueError):
    pass


def verify_response(f: bytes) -> bytes:
    """응답 검증 → DATA. 실패하면 VerifyError."""
    if len(f) < 7:
        raise VerifyError("too short")
    if f[0] != START:
        raise VerifyError("bad start")
    if f[-1] != END:
        raise VerifyError("bad end")
    if f[2] != 0x00:
        raise VerifyError(f"status {f[2]:02X}")
    if f[3] != len(f) - 7:
        raise VerifyError("bad length")
    chk = checksum(f, 2, len(f) - 3)
    if f[-3] != chk >> 8 or f[-2] != chk & 0xFF:
        raise VerifyError("bad checksum")
    return bytes(f[4 : 4 + f[3]])


def find_response(buf: bytes | bytearray) -> tuple[int, int] | None:
    """BLE 알림 조각을 이어 붙인 버퍼에서 완성된 응답 하나 → (start, length). 덜 왔으면 None."""
    n = len(buf)
    for i in range(n - 6):
        if buf[i] != START:
            continue
        total = 7 + buf[i + 3]
        if i + total > n:
            return None
        if buf[i + total - 1] == END:
            return i, total
    return None


def u16(d: bytes, i: int) -> int:
    return d[i] << 8 | d[i + 1]


def s16(d: bytes, i: int) -> int:
    v = u16(d, i)
    return v - 0x10000 if v & 0x8000 else v


@dataclass
class JbdState:
    pack_v: float = 0.0
    current: float = 0.0
    remain_ah: float = 0.0
    full_ah: float = 0.0
    cycles: int = 0
    balance: int = 0
    protection: int = 0
    sw_version: int = 0
    soc: int = 0
    chg_fet: bool = False
    dis_fet: bool = False
    cell_count: int = 0
    ntc: list[float] = field(default_factory=list)
    cell_v: list[float] = field(default_factory=list)
    cell_diff: float = 0.0

    def parse_basic(self, d: bytes) -> None:
        """§3.1"""
        if len(d) < 23:
            raise VerifyError("basic too short")
        self.pack_v = u16(d, 0) / 100
        self.current = s16(d, 2) / 100
        self.remain_ah = u16(d, 4) / 100
        self.full_ah = u16(d, 6) / 100
        self.cycles = u16(d, 8)
        self.balance = u16(d, 12) | (u16(d, 14) << 16)
        self.protection = u16(d, 16)
        self.sw_version = d[18]
        self.soc = d[19]
        self.chg_fet = bool(d[20] & 0x01)
        self.dis_fet = bool(d[20] & 0x02)
        self.cell_count = d[21]
        ntc_n = min(d[22], 8)
        self.ntc = [round((u16(d, 23 + i * 2) - 2731) / 10, 1) for i in range(ntc_n) if 23 + i * 2 + 1 < len(d)]

    def parse_cells(self, d: bytes) -> None:
        """§3.2"""
        self.cell_v = [u16(d, i * 2) / 1000 for i in range(min(len(d) // 2, 32))]
        self.cell_diff = round(max(self.cell_v) - min(self.cell_v), 3) if self.cell_v else 0.0

    def to_json(self) -> dict:
        return {
            "pack_v": round(self.pack_v, 2), "current": round(self.current, 2),
            "power": round(self.pack_v * self.current, 1), "remain_ah": round(self.remain_ah, 2),
            "full_ah": round(self.full_ah, 2), "soc": self.soc, "cycles": self.cycles,
            "cell_diff": self.cell_diff, "chg_fet": self.chg_fet, "dis_fet": self.dis_fet,
            "protection": self.protection, "balance": self.balance, "cell_count": self.cell_count,
            "cell_v": [round(v, 3) for v in self.cell_v], "ntc": self.ntc,
        }
