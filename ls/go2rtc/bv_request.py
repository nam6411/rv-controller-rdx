"""go2rtc exec 소스 (docs/30 §5.2) — 버드뷰를 보는 사람이 생기면 go2rtc가 실행하고, 모두 떠나면 SIGTERM으로 끝낸다.

argv[1] = go2rtc가 내준 {output} (rtsp://127.0.0.1:8554/<무작위 경로>). 그 경로와 송출 계정을 MQTT로 차에 보내고
10초마다 keepalive. Radxa rvnode가 같은 경로로 송출해 오면 go2rtc가 그 스트림을 보는 사람에게 넘긴다.
go2rtc 이미지(Alpine)에 mosquitto_pub가 없어 MQTT 3.1.1 CONNECT/PUBLISH만 직접 쓴다 (QoS 0, 표준 라이브러리만).

환경: MQTT_HOST(기본 127.0.0.1) MQTT_PORT MQTT_USER MQTT_PASS RTSP_USER RTSP_PASS BV_TOPIC
"""

import json
import os
import signal
import socket
import struct
import sys
import time

TOPIC = os.environ.get("BV_TOPIC", "rv/radxa/video/control/push/set")
KEEPALIVE_S = 10


def _str(s: str) -> bytes:
    b = s.encode()
    return struct.pack("!H", len(b)) + b


def _packet(kind: int, body: bytes) -> bytes:
    n, rem = len(body), b""
    while True:
        byte, n = n % 128, n // 128
        rem += bytes([byte | (0x80 if n else 0)])
        if not n:
            return bytes([kind]) + rem + body


class Mqtt:
    def __init__(self):
        self.sock = socket.create_connection((os.environ.get("MQTT_HOST", "127.0.0.1"),
                                              int(os.environ.get("MQTT_PORT", "1883"))), timeout=10)
        user, pw = os.environ.get("MQTT_USER", ""), os.environ.get("MQTT_PASS", "")
        flags = 0x02 | (0x80 if user else 0) | (0x40 if pw else 0)  # clean session
        body = _str("MQTT") + bytes([4, flags]) + struct.pack("!H", 60) + _str(f"bv-request-{os.getpid()}")
        body += (_str(user) if user else b"") + (_str(pw) if pw else b"")
        self.sock.sendall(_packet(0x10, body))
        ack = self.sock.recv(4)
        if len(ack) < 4 or ack[0] != 0x20 or ack[3] != 0:
            raise SystemExit(f"bv_request: MQTT CONNACK refused {ack.hex()}")

    def publish(self, topic: str, payload: dict) -> None:
        self.sock.sendall(_packet(0x30, _str(topic) + json.dumps(payload, separators=(",", ":")).encode()))

    def close(self) -> None:
        try:
            self.sock.sendall(b"\xe0\x00")  # DISCONNECT
            self.sock.close()
        except OSError:
            pass


def main() -> None:
    path = sys.argv[1].split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    sid = f"{int(time.time())}-{os.getpid()}"
    mqtt = Mqtt()

    def stop(*_):
        try:
            mqtt.publish(TOPIC, {"cmd": "stop", "id": sid})
        finally:
            mqtt.close()
            os._exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    mqtt.publish(TOPIC, {"cmd": "start", "id": sid, "path": path,
                         "user": os.environ.get("RTSP_USER", ""), "pass": os.environ.get("RTSP_PASS", "")})
    print(f"bv_request: start {sid} path={path}", file=sys.stderr, flush=True)
    while True:
        time.sleep(KEEPALIVE_S)
        mqtt.publish(TOPIC, {"cmd": "keepalive", "id": sid})


if __name__ == "__main__":
    main()
