#!/bin/sh
# Radxa로 배포: 코드를 rsync하고, 의존성을 venv에 깔고, 서비스를 다시 띄운다.
#   ./deploy.sh [host]          (기본 host = radxa, ~/.ssh/config의 이름)
#   ./deploy.sh radxa --setup   처음 한 번: rvnode 계정·venv·systemd 유닛·설정 파일 자리
set -eu

HOST=${1:-radxa}
SETUP=${2:-}
DEST=/opt/rvnode

cd "$(dirname "$0")"

if [ "$SETUP" = "--setup" ]; then
    ssh "$HOST" "sh -eu" <<EOF
id rvnode >/dev/null 2>&1 || sudo useradd --system --home $DEST --shell /usr/sbin/nologin rvnode
sudo mkdir -p $DEST
sudo chown \$(id -un): $DEST
command -v python3 >/dev/null
python3 -m venv $DEST/venv
EOF
fi

rsync -az --delete \
    --exclude .git --exclude .venv --exclude __pycache__ --exclude '*.egg-info' --exclude .pytest_cache \
    --exclude config.yaml \
    ./ "$HOST:$DEST/src/"

ssh "$HOST" "sh -eu" <<EOF
$DEST/venv/bin/pip install --quiet --upgrade "$DEST/src[board]"
sudo install -m 644 $DEST/src/systemd/rvnode.service /etc/systemd/system/rvnode.service
[ -f /etc/rvnode.yaml ] || { sudo install -m 640 -g rvnode $DEST/src/config.example.yaml /etc/rvnode.yaml; echo "edit /etc/rvnode.yaml"; }
sudo systemctl daemon-reload
sudo systemctl enable rvnode >/dev/null 2>&1
sudo systemctl restart rvnode
sleep 2
systemctl --no-pager --lines=10 status rvnode || true
EOF
