# rvnode — Radxa ZERO 3W 통합 노드

캠핑카의 Radxa ZERO 3W에서 도는 Python asyncio 데몬입니다. 문서는 `docs/`에 있습니다.

- [`docs/29-radxa-node.md`](docs/29-radxa-node.md) — 넘길 기능과 옮기는 순서
- [`docs/18-wiring.md`](docs/18-wiring.md) — 배선 §12 (핀 배정·회로도·부품·만능기판 배치·외부 결선)
- [`docs/24-gps-weather.md`](docs/24-gps-weather.md) — GPS·나침반·날씨
- [`docs/30-birdview-video.md`](docs/30-birdview-video.md) — 버드뷰 화면 원격 보기
- [`docs/artifacts/radxa-baseboard.html`](docs/artifacts/radxa-baseboard.html) — 베이스보드 회로도·기판 배치 페이지,
  [`docs/artifacts/avm-acc-or.html`](docs/artifacts/avm-acc-or.html) — AVM 켜짐(ACC OR) 회로

ESP 노드 쪽 문서(§0~§11 배선, MQTT 규약 `docs/07` 등)는 rv-controller 저장소에 있습니다.

ESP 노드와 **같은 MQTT·HA discovery 규약**을 씁니다(`docs/07` §B). 그래서 ESP `9FCEE4`(MACH+JBD+TPMS)를
넘겨받아도 HA 엔티티(`uniq_id = <device_id>_<slug>_<key>`)·기록·대시보드·디스플레이가 그대로 이어집니다.

## 구조

```
rvnode/
  app.py        진입점 (python -m rvnode -c /etc/rvnode.yaml [--only slug] [--log-level debug])
  config.py     설정 YAML → dataclass. prefix 규칙은 ESP Scheduler::slotPrefix와 같음
  mqtt.py       MQTT 연결 하나 (LWT <base>/status, 1→30초 백오프)
  node.py       구독, 명령 분배, discovery, sys/cmd·sys/info, 장치 태스크 감시
  ha.py         HA discovery 페이로드 (ESP HaDiscovery.cpp와 같은 모양)
  device.py     장치 기반 클래스 + 엔티티 정의 (Sensor·Switch·Control·Event)
  gpio.py       GPIO 선 (보드 = libgpiod v2, Mac·테스트 = fake)
  devices/      장치 모듈. dummy.py = 하드웨어 없는 본보기, birdview.py = 버드뷰 전원·시점
  frames/       프레임 해석 순수 함수 (ESP *Frame.h에서 옮김)
tests/          pytest
systemd/        rvnode.service
deploy.sh       rsync + venv + systemd 재시작
```

- 장치 하나 = ESP 슬롯 하나 = asyncio 태스크 하나. 한 장치가 죽거나 재연결 중이어도 다른 장치는 계속 돕니다. 죽은 태스크는 5초 뒤 다시 시작합니다.
- 명령(`<prefix>/switch/<n>/set`, `<prefix>/control/<n>/set`)은 장치마다 큐에 넣어 차례로 처리하고, 처리하면 state를 바로 발행합니다.
- 3회 연속 실패하면 `<prefix>/availability = offline`이 됩니다.
- `<base>/sys/cmd`: `restart`(프로세스가 끝나고 systemd가 다시 띄움), `rediscover`.
- `homeassistant/status = online`을 받으면 discovery·meta·state를 다시 발행합니다.

## 개발 (Mac)

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

로컬 브로커로 돌려 보기: `cp config.example.yaml config.yaml`에서 `mqtt.host`를 고친 뒤 `.venv/bin/python -m rvnode -c config.yaml`.
**집 브로커에 붙일 때는 `node.base`를 `rvtest/...`로 바꿉니다** — `rv/mach`로 두면 ESP `9FCEE4`의 `rv/mach/status`를 덮어씁니다.

## 장치: birdview

`docs/18` §12.2 "AVM 켜짐"과 트리거 출력을 맡는다. 설정은 `config.example.yaml`의 `birdview` 블록.

- 입력(옵토 → GPIO, Low = 켜짐): 차 시동(핀 37), 13핀 좌·우 깜빡이(22·28), 후진(32).
- 출력: 버드뷰 ACC(핀 35), 시점 트리거 후방·좌·우(36·38·40).
- 전원 = 차 시동 OR `switch.birdview_power` OR 다른 모듈의 `request_power()`(원격 영상 등). 시동을 끄면 `off_delay_s`(기본 30초) 뒤 끈다.
- 시점(`select.birdview_mode` = auto): 후진 → 후방, 한쪽 깜빡이 → 그쪽(마지막 펄스 뒤 `blink_hold_s` 유지), 비상등·평상시 → 기본 화면.
- **입력 풀업 오버레이**(`dts/rvnode-inputs-pullup.dts`): Rockchip GPIO 드라이버는 gpiod의 `bias=pull-up`을 적용하지 않아
  칩 기본값(풀다운)이 남는다 — 옵토가 꺼져 있어도 "켜짐"으로 읽혀 ACC·트리거가 켜진다. `deploy.sh`가 부팅 때 핀 22·28·32·37을
  풀업으로 거는 오버레이를 깔고 켠다(재부팅 후 적용). 확인: `/sys/kernel/debug/pinctrl/pinctrl-rockchip-pinctrl/pinconf-pins`.
- **인터록**: 트리거 전원(J2-1)이 버드뷰 상시 전원이라, 트리거는 핀 35가 켜져 있을 때만 켜고 끌 때는 트리거부터 내린다.
  데몬이 끝날 때도 트리거 → 전원 순서로 끈다.
- 보드에서 처음: `gpioinfo`로 핀 35(GPIO3_A4 = gpiochip3/4)·37(GPIO1_A4 = gpiochip1/4)이 맞는지 보고 `enabled: true`.
  부팅 중 핀 35가 High로 튀지 않는지 본다.
- 아직 없는 것: GPS로 주행을 판단해 후방으로 바꾸는 규칙([docs/29](docs/29-radxa-node.md) §1 결정 5) — gps 모듈이 생기면 붙인다.

## 장치 모듈 추가

1. `rvnode/devices/<type>.py`에 `Device`를 상속한 클래스를 만듭니다. `dummy.py`를 본보기로 씁니다.
   - `type`, `sensors`/`switches`/`controls`/`events`, `key_sensors`
   - `run()`: 장치 루프. 읽으면 `await self.ok()` + `await self.publish_state()`, 실패하면 `await self.error(msg)`
   - `state()`, `switch_state()`, `write_switch()`, `write_control()`
2. `rvnode/devices/__init__.py`의 `TYPES`에 등록합니다.
3. 프레임 해석은 `rvnode/frames/`에 순수 함수로 두고, ESP `test/test_frames`의 벡터로 테스트합니다.
4. ESP에서 넘겨받는 장치는 엔티티 키·라벨·state JSON 모양을 ESP 모듈과 똑같이 맞춥니다.

## 배포 (Radxa)

```sh
./deploy.sh radxa --setup   # 처음 한 번: rvnode 계정, /opt/rvnode/venv
./deploy.sh radxa           # 이후: rsync → pip install → systemctl restart
ssh radxa journalctl -u rvnode -f
```

설정은 `/etc/rvnode.yaml`입니다. 처음 배포할 때 `config.example.yaml`을 복사해 두므로 열어서 고칩니다.
