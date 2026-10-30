"""CC1101 작업대 시험 (Radxa) — 배선 확인, 버드뷰 리모컨 수신·송신.

    /opt/rvnode/venv/bin/python /opt/rvnode/src/tools/cc1101_test.py probe
    … listen [초]                 리모컨 노브를 돌리고 누르면 패킷·RSSI를 찍는다
    … send left|right|click [횟수] [--dbm 10]   리모컨 흉내 (rvnode가 CC1101을 쓰지 않을 때만)
rvnode와 같은 rvnode.cc1101 드라이버를 쓴다. 사용자는 spidev·gpio 그룹이면 된다.
"""

import argparse
import sys
import time

from rvnode.cc1101 import Cc1101, Cc1101Error, Config
from rvnode.frames import birdview_rf as bv


def open_radio(dbm: int) -> Cc1101:
    c = Cc1101()
    c.begin(Config(freq_hz=bv.FREQ_HZ, baud=bv.BAUD, deviation_hz=bv.DEVIATION_HZ, sync=bv.sync_word(),
                   packet_len=bv.PAYLOAD, preamble_bytes=bv.PREAMBLE_BYTES, tx_dbm=dbm))
    return c


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("probe", "listen", "send"))
    ap.add_argument("arg", nargs="?")
    ap.add_argument("count", nargs="?", type=int, default=1)
    ap.add_argument("--dbm", type=int, default=10)
    a = ap.parse_args()
    try:
        c = open_radio(a.dbm)
    except (Cc1101Error, OSError) as e:
        print("CC1101:", e)
        return 1
    print(f"CC1101 ver 0x{c.version:02X}, 433.92MHz 2-FSK ±25kHz {bv.BAUD}baud, sync 0x{bv.sync_word():04X}, RX")
    try:
        if a.cmd == "listen":
            end = time.monotonic() + float(a.arg or 30)
            n = 0
            while time.monotonic() < end:
                p = c.poll()
                if not p:
                    time.sleep(0.002)
                    continue
                d = bv.decode(p.data)
                when = time.strftime("%H:%M:%S")
                if d:
                    n += 1
                    print(f"{when} seq {d[0]:02X} {d[1]:02X} {bv.describe(d[1]):14s} RSSI {p.rssi_dbm:6.1f} dBm "
                          f"LQI {p.lqi:3d} 주파수 오차 {p.freq_off_hz / 1000:+.1f} kHz")
                else:
                    print(f"{when} (해석 안 됨) {p.data.hex()} RSSI {p.rssi_dbm:.1f}")
            print(f"받은 패킷 {n}개")
        elif a.cmd == "send":
            action = bv.ACTIONS.get(a.arg or "") if not (a.arg or "").startswith("0x") else int(a.arg, 16)
            if action is None:
                print("동작: left right click long_start long_end 또는 0xDE 같은 바이트")
                return 2
            seq = 0x1E
            for _ in range(a.count):
                reps = 2 if action in (bv.CLICK, bv.LONG_START, bv.LONG_END) else 1  # 누르기류는 같은 패킷 두 번
                for _ in range(reps):
                    ok = c.send(bv.payload(seq, action))
                    time.sleep(0.022)
                print(f"보냄 seq {seq:02X} {action:02X} {bv.describe(action)} {'ok' if ok else 'FAIL'}")
                seq = bv.next_seq(seq)
                time.sleep(0.3)
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
