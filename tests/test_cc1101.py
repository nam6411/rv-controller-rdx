"""버드뷰 리모컨 패킷과 CC1101 레지스터 계산 — 실측 패킷(docs/25 §10)과 ESP 값으로 맞춘다."""

from rvnode import cc1101 as cc
from rvnode.frames import birdview_rf as bv

CAPTURED = {  # rv-controller docs/25 §10 실측
    "left": bytes.fromhex("A48A5E051DDE6F"),
    "right": bytes.fromhex("A48A5E0513EE1F"),
    "click": bytes.fromhex("A48A5E051AFFF2"),
    "long": bytes.fromhex("A48A5E051EFE08"),
}


def test_packets_match_captures():
    for p in CAPTURED.values():
        assert bv.crc8(p[:6]) == p[6]
        assert bv.packet(bv.DEFAULT_ID, p[4], p[5]) == p


def test_payload_roundtrip_and_sync():
    for p in CAPTURED.values():
        chips = bv.payload(p[4], p[5])
        assert len(chips) == bv.PAYLOAD
        assert bv.decode(chips) == (p[4], p[5])
    assert bv.sync_word() == bv.chips(0xA4)
    assert bv.chips(0x01) == 0b10_01_01_01_01_01_01_01   # LSB 먼저, 1 = 10
    bad = bytearray(bv.payload(0x1D, bv.LEFT))
    bad[3] ^= 0x01                                         # 맨체스터 쌍이 깨지거나 CRC가 틀린다
    assert bv.decode(bytes(bad)) is None


def test_seq_and_describe():
    assert bv.next_seq(0x10) == 0x1F and bv.next_seq(0x1D) == 0x1C
    assert bv.describe(0xDE) == "left ×1" and bv.describe(0xED) == "right ×2" and bv.describe(0xFF) == "click"


def test_register_calc_matches_esp():
    assert cc.freq_word(433_920_000) == 0x10B071
    assert cc.data_rate(19390) == (9, 135)
    assert cc.deviatn(25_000) == 0x40
    assert cc.chan_bw(100_000) == 0x0C
    assert cc.preamble_code(8) == 4
    assert cc.rssi_dbm(0x80) == -138 and cc.rssi_dbm(0x10) == -66
