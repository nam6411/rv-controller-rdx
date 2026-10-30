"""버드뷰 노브 리모컨 무선 패킷 — ESP src/modules/irrf/IrRfCode.h `birdview`를 옮겼다 (git 75da6e0^, docs/25 §10).

433.92MHz 2-FSK 편이 25kHz, 칩 51.6us(≈19.39kbaud), 맨체스터(1 = 칩 10, 0 = 칩 01), 프리앰블 칩 10 × 32,
이어서 7바이트(비트는 LSB 먼저):  ID 4바이트(A4 8A 5E 05) | seq | 동작 | CRC-8(poly 0x2F, init 0x83, 앞 6바이트)
- seq: 송신마다 아래 4비트가 1씩 준다
- 동작: 위 4비트 방향(D 왼쪽, E 오른쪽, 누른 채면 5·6), 아래 4비트 = 15 − 칸 수. FF 짧게 누르기, FE 길게 시작, FC 길게 뗌
CC1101 패킷 모드: 프리앰블(0xAA × 8)과 동기어(= 첫 ID 바이트의 칩 16개)는 칩이 붙이고, 나머지 6바이트의 칩 12바이트가 FIFO.
"""

from __future__ import annotations

DEFAULT_ID = 0xA48A5E05
LEFT, RIGHT, CLICK, LONG_START, LONG_END = 0xDE, 0xEE, 0xFF, 0xFE, 0xFC
BAUD = 19390
DEVIATION_HZ = 25_000
FREQ_HZ = 433_920_000
PAYLOAD = 12
PREAMBLE_BYTES = 8

ACTIONS = {"left": LEFT, "right": RIGHT, "click": CLICK, "long_start": LONG_START, "long_end": LONG_END}


def crc8(data: bytes) -> int:
    c = 0x83
    for b in data:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ 0x2F) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c


def packet(ident: int, seq: int, action: int) -> bytes:
    p = ident.to_bytes(4, "big") + bytes([seq & 0xFF, action & 0xFF])
    return p + bytes([crc8(p)])


def chips(b: int) -> int:
    """바이트 하나(LSB 먼저) → 칩 16개. 처음 보내는 칩이 bit15 (CC1101은 MSB부터 보낸다)."""
    c = 0
    for i in range(8):
        c = (c << 2) | (0b10 if (b >> i) & 1 else 0b01)
    return c


def unchips(c: int) -> int | None:
    """칩 16개 → 바이트. 맨체스터가 깨진 쌍(00·11)이 있으면 None."""
    b = 0
    for i in range(8):
        pair = (c >> (14 - 2 * i)) & 0b11
        if pair == 0b10:
            b |= 1 << i
        elif pair != 0b01:
            return None
    return b


def sync_word(ident: int = DEFAULT_ID) -> int:
    return chips(ident >> 24)


def payload(seq: int, action: int, ident: int = DEFAULT_ID) -> bytes:
    pkt = packet(ident, seq, action)
    return b"".join(chips(b).to_bytes(2, "big") for b in pkt[1:])


def decode(data: bytes, ident: int = DEFAULT_ID) -> tuple[int, int] | None:
    """받은 칩 12바이트(동기어 다음부터) → (seq, 동작). ID 나머지 3바이트와 CRC가 맞아야 한다."""
    if len(data) < PAYLOAD:
        return None
    pkt = bytearray([ident >> 24])
    for i in range(6):
        b = unchips(int.from_bytes(data[2 * i : 2 * i + 2], "big"))
        if b is None:
            return None
        pkt.append(b)
    if bytes(pkt[:4]) != ident.to_bytes(4, "big") or crc8(bytes(pkt[:6])) != pkt[6]:
        return None
    return pkt[4], pkt[5]


def next_seq(seq: int) -> int:
    """리모컨처럼: 아래 4비트만 1씩 줄고 위 4비트는 그대로."""
    return (seq & 0xF0) | ((seq - 1) & 0x0F)


def describe(action: int) -> str:
    if action == CLICK:
        return "click"
    if action == LONG_START:
        return "long press"
    if action == LONG_END:
        return "long release"
    d = {0xD: "left", 0xE: "right", 0x5: "left (pressed)", 0x6: "right (pressed)"}.get(action >> 4, f"0x{action:02X}")
    return f"{d} ×{15 - (action & 0x0F)}"
