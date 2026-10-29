#!/bin/sh
# Lightsail(ls)에 버드뷰 원격 보기용 go2rtc를 세운다 (docs/30 §5). ls에서 한 번 실행: sudo sh setup.sh
# 다시 실행해도 된다 — 비밀값은 처음 만든 것을 쓰고 설정·컨테이너만 새로 쓴다.
#
#   Radxa ─RTSP(TCP, 계정)─▶ mqtt.nam6411.com:8554 go2rtc ◀─ HA (172.17.0.1:8554)
#
# - 컨테이너는 host 네트워크. Lightsail 방화벽에서 TCP 8554를 연다.
# - go2rtc는 localhost(127.0.0.1)에서 온 접속에는 계정을 묻지 않는다 — 8554 앞에 같은 서버의 프록시·터널을 두지 않는다.
# - api(1984)는 127.0.0.1에만. WebRTC는 쓰지 않는다 (HA가 RTSP로 받아 자기 WebRTC/HLS로 낸다).
# - MQTT 계정 go2rtc를 mosquitto 비밀번호 파일에 더한다 (bv_request.py가 송출 요청을 보낸다).
set -eu
cd "$(dirname "$0")"
DIR=/opt/go2rtc
IMAGE=alexxit/go2rtc:1.9.9
PASSWD=/mosquitto/config/passwd

mkdir -p $DIR
rnd() { head -c 18 /dev/urandom | base64 | tr -d '+/=\n'; }
if [ ! -f $DIR/secrets.env ]; then
  umask 077
  cat > $DIR/secrets.env <<EOF
RTSP_USER=bv$(head -c 4 /dev/urandom | od -An -tx1 | tr -d ' \n')
RTSP_PASS=$(rnd)
MQTT_USER=go2rtc
MQTT_PASS=$(rnd)
EOF
  echo "secrets: $DIR/secrets.env (new)"
fi
chmod 600 $DIR/secrets.env
. $DIR/secrets.env

# MQTT 계정 — 이미 있으면 비밀번호만 맞춘다
cp -p $PASSWD "$PASSWD.bak-$(date +%Y%m%d%H%M%S)"
mosquitto_passwd -b $PASSWD "$MQTT_USER" "$MQTT_PASS"
chown mosquitto:mosquitto $PASSWD
systemctl reload mosquitto 2>/dev/null || kill -HUP "$(cat /run/mosquitto/mosquitto.pid)"

install -m 644 bv_request.py $DIR/bv_request.py
cat > $DIR/go2rtc.yaml <<'EOF'
# setup.sh가 쓴다 — 손으로 고치지 않는다 (rv-controller-rdx ls/go2rtc)
streams:
  # 보는 사람이 생기면 bv_request.py가 차에 송출을 요청하고, Radxa가 {output} 경로로 밀어 넣는다
  birdview: exec:python3 /config/bv_request.py {output}#killsignal=15#killtimeout=5
rtsp:
  listen: ":8554"
  username: ${RTSP_USER}
  password: ${RTSP_PASS}
api:
  listen: "127.0.0.1:1984"
webrtc:
  listen: ""
log:
  level: info
  exec: debug
EOF

docker rm -f go2rtc >/dev/null 2>&1 || true
docker run -d --name go2rtc --restart unless-stopped --network host \
  --env-file $DIR/secrets.env -v $DIR:/config \
  --log-opt max-size=5m --log-opt max-file=2 \
  $IMAGE >/dev/null
sleep 2
docker ps --filter name=go2rtc --format '{{.Names}} {{.Status}}'
echo "HA generic camera: rtsp://\$RTSP_USER:\$RTSP_PASS@172.17.0.1:8554/birdview (values in $DIR/secrets.env)"
