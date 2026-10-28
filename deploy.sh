#!/bin/sh
# Radxa로 배포: 코드를 rsync하고, 의존성을 venv에 깔고, 서비스를 다시 띄운다.
#   ./deploy.sh [host]          (기본 host = radxa, ~/.ssh/config의 이름 또는 radxa@192.168.1.150)
#   ./deploy.sh radxa --setup   처음 한 번: rvnode 계정·venv·mosquitto 기본 설정·sudo 규칙·설정 파일 자리
set -eu

HOST=${1:-radxa}
SETUP=${2:-}
DEST=/opt/rvnode

cd "$(dirname "$0")"

if [ "$SETUP" = "--setup" ]; then
    ssh "$HOST" "sh -eu" <<EOF
id rvnode >/dev/null 2>&1 || sudo useradd --system --home $DEST --shell /usr/sbin/nologin rvnode
for g in bluetooth gpio spi i2c dialout video; do getent group \$g >/dev/null || sudo groupadd --system \$g; sudo usermod -aG \$g rvnode; done
sudo mkdir -p $DEST
sudo chown \$(id -un): $DEST
python3 -m venv $DEST/venv
# GPIO·SPI·I2C 장치를 그룹으로 열 수 있게 (Radxa OS 기본은 root만)
printf '%s\n' 'SUBSYSTEM=="gpio", KERNEL=="gpiochip*", GROUP="gpio", MODE="0660"' \
  'SUBSYSTEM=="spidev", GROUP="spi", MODE="0660"' 'SUBSYSTEM=="i2c-dev", GROUP="i2c", MODE="0660"' \
  | sudo tee /etc/udev/rules.d/60-rvnode.rules >/dev/null
sudo udevadm control --reload && sudo udevadm trigger
# 웹 설정이 브리지를 바꾸면 mosquitto만 다시 시작할 수 있게
echo 'rvnode ALL=(root) NOPASSWD: /usr/bin/systemctl restart mosquitto' | sudo tee /etc/sudoers.d/91-rvnode >/dev/null
sudo chmod 440 /etc/sudoers.d/91-rvnode
# gpsd가 GPS(UART4 = /dev/ttyS4, 115200)를 쥔다 — rvnode gps 장치는 2947 소켓만 읽는다 (docs/24 §4.1)
sudo DEBIAN_FRONTEND=noninteractive apt-get -y -q install gpsd gpsd-clients >/dev/null
printf '%s\n' 'START_DAEMON="true"' 'USBAUTO="false"' 'DEVICES="/dev/ttyS4"' 'GPSD_OPTIONS="-n -s 115200"' | sudo tee /etc/default/gpsd >/dev/null
sudo systemctl enable gpsd.socket gpsd >/dev/null 2>&1; sudo systemctl restart gpsd
EOF
fi

# 옵토 입력 핀 풀업 오버레이 (Rockchip GPIO 드라이버가 gpiod의 bias 요청을 적용하지 않는다). 처음 한 번, 재부팅 후 적용
scp -q dts/rvnode-inputs-pullup.dts "$HOST:/tmp/"
ssh "$HOST" "sh -eu" <<'EOF'
if [ ! -e /boot/dtbo/rvnode-inputs-pullup.dtbo ]; then
  dtc -@ -q -I dts -O dtb -o /tmp/rvnode-inputs-pullup.dtbo /tmp/rvnode-inputs-pullup.dts
  sudo install -m 644 /tmp/rvnode-inputs-pullup.dtbo /boot/dtbo/rvnode-inputs-pullup.dtbo.disabled
  (cd /boot/dtbo && sudo rsetup enable_overlays rvnode-inputs-pullup.dtbo >/dev/null)
  echo "rvnode-inputs-pullup overlay enabled — reboot to apply"
fi
# Radxa 패키지 업데이트가 오버레이 폴더를 새로 만들면 켜 둔 것이 풀린다(2026-10-29) — 빠진 것을 다시 켠다
cd /boot/dtbo
NEED=""
for o in radxa-zero3-external-antenna rk3568-spi3-m1-cs0-spidev rk3568-uart3-m0 rk3568-uart4-m1 rk3568-i2c5-m0 rvnode-inputs-pullup; do
  [ -e $o.dtbo ] || NEED="$NEED $o.dtbo"
done
if [ -n "$NEED" ]; then sudo rsetup enable_overlays $NEED >/dev/null && echo "re-enabled:$NEED — reboot to apply"; fi
EOF

rsync -az --delete \
    --exclude .git --exclude .venv --exclude __pycache__ --exclude '*.egg-info' --exclude .pytest_cache \
    --exclude config.yaml \
    ./ "$HOST:$DEST/src/"

ssh "$HOST" "sh -eu" <<EOF
$DEST/venv/bin/pip install --quiet --upgrade "$DEST/src[board]"
sudo install -m 644 $DEST/src/systemd/rvnode.service /etc/systemd/system/rvnode.service
sudo install -m 644 $DEST/src/mosquitto/rv-local.conf /etc/mosquitto/conf.d/rv-local.conf
# 브리지 파일은 rvnode가 웹 설정으로 다시 쓴다 — rvnode 소유, mosquitto가 읽음
[ -f /etc/mosquitto/conf.d/rv-bridge.conf ] || { echo '# rvnode: 외부 브리지 꺼짐' | sudo tee /etc/mosquitto/conf.d/rv-bridge.conf >/dev/null; }
sudo chown rvnode:mosquitto /etc/mosquitto/conf.d/rv-bridge.conf && sudo chmod 640 /etc/mosquitto/conf.d/rv-bridge.conf
# 설정 파일은 웹에서 고치므로 rvnode가 쓸 수 있어야 한다 (비밀번호가 있어 다른 사람은 못 읽게)
[ -f /etc/rvnode.yaml ] || { sudo install -m 600 $DEST/src/config.example.yaml /etc/rvnode.yaml; echo "edit /etc/rvnode.yaml"; }
sudo chown rvnode:rvnode /etc/rvnode.yaml && sudo chmod 600 /etc/rvnode.yaml
sudo systemctl daemon-reload
sudo systemctl enable --now mosquitto >/dev/null 2>&1
sudo systemctl restart mosquitto
sudo systemctl enable rvnode >/dev/null 2>&1
sudo systemctl restart rvnode
# 전원이 곧 끊겨도 방금 쓴 유닛·설정이 빈 파일로 남지 않게 (2026-10-29: 배포 직후 전원이 나가 0바이트가 됐다)
sync
sleep 3
systemctl --no-pager --lines=12 status rvnode || true
EOF
