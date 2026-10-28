"""ESP test/test_frames/test_main.cpp의 JBD 벡터를 옮겼다."""

import pytest

from rvnode.frames import jbd


def test_build_requests():
    assert jbd.build_request(jbd.READ, jbd.CMD_BASIC) == bytes.fromhex("dda50300fffd77")
    assert jbd.build_request(jbd.READ, jbd.CMD_CELLS) == bytes.fromhex("dda50400fffc77")
    # docs/04 §3.3: DD 5A E1 02 00 <VAL> FF <0x1D-VAL> 77
    assert jbd.build_request(jbd.WRITE, jbd.CMD_MOSFET, bytes([0, 2])) == bytes.fromhex("dd5ae1020002ff1b77")
    assert jbd.mosfet_request(True, False) == bytes.fromhex("dd5ae1020002ff1b77")


def test_verify_roundtrip():
    f = bytearray([0xDD, 0x03, 0x00, 0x02, 0x12, 0x34])
    chk = jbd.checksum(f, 2, 6)
    f += bytes([chk >> 8, chk & 0xFF, 0x77])
    assert jbd.verify_response(bytes(f)) == bytes([0x12, 0x34])
    f[4] ^= 0xFF
    with pytest.raises(jbd.VerifyError, match="checksum"):
        jbd.verify_response(bytes(f))


def test_ble_reassembly_and_parse_real_4s():
    # 2026-10-04 집 4S BMS: BLE 알림 조각을 이어 붙인 응답 (앞에 잡음 1바이트)
    buf = bytes.fromhex("00dd03001d052e00002eb62ee0083f2b3b00000000000080640104030b9b0b980b96fa3b77")
    assert jbd.find_response(buf[:-5]) is None
    s, n = jbd.find_response(buf)
    assert (s, n) == (1, 36)
    data = jbd.verify_response(buf[s : s + n])
    assert len(data) == 29
    st = jbd.JbdState()
    st.parse_basic(data)
    assert st.pack_v == pytest.approx(13.26)
    assert st.current == 0 and st.soc == 100 and st.cell_count == 4
    assert st.remain_ah == pytest.approx(119.58) and st.full_ah == pytest.approx(120.0)
    assert st.chg_fet and not st.dis_fet
    assert st.ntc == [24.0, 23.7, 23.5]   # 0x0B9B 0x0B98 0x0B96 − 2731, ÷10


def test_parse_cells():
    st = jbd.JbdState()
    st.parse_cells(bytes.fromhex("0cf40cf00cf20cee"))
    assert st.cell_v == [3.316, 3.312, 3.314, 3.310]
    assert st.cell_diff == 0.006
