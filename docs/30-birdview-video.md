# 30. 버드뷰 화면 원격 보기 — AHD → USB 캡처 → Radxa → Lightsail → HA (계획)

> 상태: **계획 (2026-10-21).** 캡처 모듈은 샀고, 아직 연결하지 않았다. 노드 하드웨어는 [18-wiring.md](18-wiring.md) §12,
> 노드 소프트웨어는 [29-radxa-node.md](29-radxa-node.md). **§9의 확인 항목은 작업대에서 먼저 확인한다.**

버드뷰 본체(AVM3D)의 영상 출력을 Radxa가 USB 캡처 모듈로 받는다. **누군가 HA에서 화면을 열면** Radxa가 H.264로 줄여
Lightsail 서버(`ls`, HA가 있는 곳)로 밀어 넣고, 닫으면 멈춘다. 차 밖에서 보는 것이 목적이다.

## 1. 결정 (2026-10-21)

| # | 항목 | 결정 |
|---|---|---|
| 1 | 영상 소스 | 버드뷰 본체의 영상 출력 (시점이 바뀐 합성 화면 그대로) |
| 2 | 보는 곳 | **원격** — 폰 HA 앱 (어디서나) |
| 3 | 언제 내보내나 | 보는 사람이 화면을 열 때만. 평소에는 아무것도 보내지 않는다 |
| 4 | 송출 시간 | **라이브는 한 번에 1분까지.** 1분이 지나면 일시정지 화면으로 바뀌고 **이어보기**를 누르면 1분 더. LTE는 정액제라 하루 합계 제한은 두지 않는다 |

## 2. 경로

현장은 LTE(CGNAT)라 밖에서 Radxa로 들어올 수 없다(docs/17). 그래서 **영상은 Radxa가 밖으로 접속해 밀어 넣고**,
"보기 시작/그만"은 이미 있는 MQTT 브리지로 Radxa에 알린다.

```
 [차]                                                    [Lightsail ls]
 버드뷰 AHD ─동축─ 캡처 모듈 ─USB─ Radxa                   go2rtc (컨테이너)          HA (컨테이너)
                                  go2rtc ── /dev/video0       stream "birdview"  ◀── generic camera
                                  (MJPEG 그대로, 로컬)          │ 보는 쪽이 붙으면       (rtsp://…:8554/birdview)
                                     │                         ▼ exec: bv-request         ▲
                                  rvnode video ◀─MQTT 브리지─ mosquitto_pub start/keepalive │
                                     │ ffmpeg: MJPEG → H.264 (rkmpp)                       │
                                     └──RTSP 송출(TCP, LTE 바깥으로)──▶ :8554/<요청 경로> ─┘
                                                                                 폰 HA 앱 ◀─ HA (WebRTC/HLS)
```

1. 폰에서 카메라를 연다 → HA가 Lightsail go2rtc의 `birdview`를 읽기 시작한다.
2. go2rtc는 `birdview`에 보는 쪽이 생기면 소스 `exec:bv-request`를 실행한다. 이 스크립트가 MQTT로
   `start`(송출할 경로·계정 포함)를 보내고, 10초마다 `keepalive`를 보낸다.
3. 브리지가 명령을 차로 내린다(`rv/+/control/+/set`은 이미 `in_topics`에 있다). Radxa rvnode가 ffmpeg를 띄워
   로컬 go2rtc의 MJPEG를 H.264로 바꿔 Lightsail로 RTSP 송출한다.
4. 1분이 지나면 라이브를 멈추고 **일시정지 화면**("이어보기를 누르세요")을 1초에 한 장 보낸다. 송출 연결은 끊지 않는다.
   HA 화면의 **이어보기**를 누르면 MQTT `extend`가 내려가 다시 1분 라이브.
5. 모두 닫으면 go2rtc가 스크립트를 끝낸다 → keepalive가 끊긴다 → Radxa가 **30초 안에** ffmpeg를 끈다.
   스크립트가 `stop`을 보내면 바로 끈다(신호를 못 받고 죽어도 keepalive로 끝난다).

- 열 때 처음 화면까지 **5~10초**(MQTT 왕복 + 카메라 열기 + 인코더 + 첫 키프레임)를 예상한다 — §9에서 잰다.
- **1분 뒤 연결을 끊지 않는 이유**: 송출이 끊기면 HA가 스트림을 다시 열려고 재시도하고, 그때 go2rtc가 스크립트를 새로 띄워
  새 `start`가 나간다 → 저절로 다시 켜진다. 연결을 유지한 채 그림만 정지 화면으로 바꾸면 이 고리가 생기지 않고,
  이어보기도 재접속 없이 바로 된다. 정지 화면은 같은 그림이 반복돼 수 kbps밖에 쓰지 않는다.
- Radxa의 로컬 go2rtc가 캡처 장치를 쥔다. UVC 장치는 한 프로그램만 열 수 있으므로 rvnode는 장치 대신 로컬 go2rtc에서 읽는다.
  덕분에 차 안 Wi-Fi에서는 LTE 없이 Radxa에 직접 붙어서도 볼 수 있다(§7.3).

## 3. 캡처 모듈

| 항목 | 내용 |
|---|---|
| 입력 | AHD 720p/1080p (PAL·NTSC 자동) + 오디오. 1.25mm 4핀: 왼쪽부터 **5V 출력**·GND·영상·오디오 |
| 출력 | USB Type-C, UVC/UAC 표준 — Linux 드라이버 필요 없음 (`uvcvideo`) |
| 형식 | **MJPEG** 1920×1080 30fps, 1280×720 60fps, 그 아래 여러 해상도. YUY2는 1080p 5fps·720p 10fps뿐 → MJPEG를 쓴다 |
| 소비 전력 | 3W 미만 (5V 0.6A 안) |
| 크기 | 19 × 45mm, 10g |

- **4핀의 5V는 카메라에 전원을 주는 출력이다. 버드뷰 본체 쪽에는 잇지 않는다** — 영상(심선)·GND(차폐)만 잇는다.
- 오디오는 쓰지 않는다.
- 버드뷰의 출력이 AHD인지 CVBS인지, 720p인지 1080p인지 먼저 확인한다(본체 매뉴얼·출력 커넥터). 모듈은 AHD 720p/1080p만
  사양에 있다. 본체 보드에는 VGA DAC(GM7123C)도 있어 출력이 여러 개일 수 있다.
- 버드뷰가 화면을 내지 않는 상태(대기·시점 없음)면 검은 화면이나 "신호 없음"이 나온다. 원격에서 시점을 바꾸려면
  트리거(docs/18 §12.1 핀 36·38·40)를 HA 버튼으로 같이 둔다(§6).

### 3.1 영상 결선 — 분배기 → RCA → 캡처 모듈

```
 버드뷰 AHD 출력 ── AHD 분배기 ──┬── 출력 1 → 기존 모니터 (그대로)
                                └── 출력 2 → RCA 수(플러그) ── 동축 ── 캡처 모듈 1.25mm 4핀
                                              심선 ─────────────────── 3번 영상
                                              바깥(차폐) ───────────── 2번 GND
                                                                       1번 5V 출력 — 잘라서 절연, 잇지 않는다
                                                                       4번 오디오  — 잘라서 절연
```

| 캡처 모듈 4핀 (왼쪽부터) | RCA | 처리 |
|---|---|---|
| 1 — 5V 출력 | — | **끝을 잘라 수축 튜브로 절연.** 카메라 전원용 출력이라 분배기·버드뷰에 닿으면 안 된다 |
| 2 — GND | 바깥 통(슬리브) | 동축 차폐 |
| 3 — 영상 | 가운데 핀(팁) | 동축 심선 |
| 4 — 오디오 | — | 잘라서 절연 |

- **4핀 쪽 색은 실물로 확인한다.** 판매처 기준 "왼쪽부터"는 모듈 커넥터를 앞에서 본 방향이다. 납땜 전에 멀티미터로
  모듈을 USB에 꽂은 상태에서 5V가 나오는 선을 먼저 찾아 1번을 확정한다.
- 판매처 안내대로 커넥터를 떼고 모듈 패드에 바로 납땜해도 된다. 그때도 5V 패드는 비운다.
- **동축을 쓴다.** 모듈에 딸린 1.25mm 4선은 짧은 비차폐 선이다. RCA에서 모듈까지 20cm를 넘으면 75Ω 동축(RG-174 등)으로
  늘리고, 4선 부분은 모듈 쪽 5cm 안으로 줄인다. AHD는 아날로그 고주파 신호라 비차폐 선이 길면 무늬·번짐이 생긴다.
- RCA 플러그는 금속 몸체형(납땜식)으로. 심선은 가운데 핀, 차폐는 몸체 클램프에 납땜하고 수축 튜브로 덮는다.
- **분배기는 능동(전원 있는) 분배기를 권한다.** 수동 Y 분배는 75Ω 부하 두 개가 병렬이 되어 신호가 절반으로 줄고 반사가 생긴다 —
  모니터·캡처 둘 다 화면이 어두워지거나 AHD 동기를 놓칠 수 있다. 쓰려는 분배기가 수동이면 §9 1번에서 두 출력을 동시에 띄워 확인한다.
- **분배기 전원**이 필요하면 버드뷰와 같은 전원(J2 AVM_12V, 판이 J1 +12V에서 점퍼로 주는 상시 12V)에서 받는다. 상시라 버드뷰가 꺼져도 분배기는 켜져 있다 — 꺼지게 하려면 J2 ACC에서 받는다(ACC_IN 전류에 분배기 전류가 더해지므로 그때는 §12.2 B안 릴레이).
- GND: 버드뷰·분배기·Radxa는 같은 차체 GND다(docs/18 §12.5). 동축 차폐가 GND 하나로 이어지므로 따로 GND선을 끌지 않는다.

## 4. Radxa

### 4.1 연결
- **USB-C ↔ USB-C 짧은 케이블**(10~20cm)로 Radxa **Host USB-C 포트**와 모듈을 잇는다.
  - BLE는 내장 AIC8800으로 먼저 하므로(docs/18 §12) Host 포트가 비어 있다. Host 포트는 늘 호스트라 VBUS 5V를 내보낸다 — 모듈 전원 걱정이 없다.
  - **데이터선**이어야 한다(충전 전용 C-C 케이블은 데이터 선이 없다). USB 2.0이면 충분하다. 직각(ㄱ자) 플러그면 함체 안에서 덜 꺾인다.
  - 모듈은 USB 2.0 장치라 USB 3.0 SuperSpeed 선을 쓰지 않는다 — USB 3.0 잡음(docs/18 §12.5)은 작다. 1080p30 MJPEG도 USB 2.0으로 충분하다.
- **나중에 BT 동글을 달게 되면**: 모듈을 OTG USB-C 포트로 옮긴다. 이때는 OTG 포트가 호스트 모드로 서고 VBUS 5V를 내보내는지
  확인해야 한다(Radxa OS 기본 역할, 오버레이·`dr_mode`). 안 되면 Host 포트에 전원 있는 USB 2.0 허브를 달고 동글과 모듈을 같이 꽂는다.
- 5V: 캡처 모듈 0.6A가 더해진다. 인코딩 중 Radxa 소비도 는다. MP1584(3A) 안이지만 전부 켠 상태에서 한 번 잰다.
- 장치 이름은 `/dev/v4l/by-id/usb-…-video-index0`로 고정한다.

### 4.2 로컬 go2rtc — 장치를 쥐는 쪽

> **2026-10-29 바뀜:** 캡처 장치는 go2rtc 대신 **rvnode `video` 장치가 쥔다**(radxa `rvnode/devices/video.py`).
> 보는 쪽이 있을 때만 `v4l2-ctl --stream-to=-`로 모듈의 MJPEG를 받아 다시 인코딩하지 않고 나눠 준다 —
> 차 안 웹은 `http://<radxa>/api/video/video.mjpeg`(한 장은 `.jpg`). 원격 송출(§4.3)은 로컬 go2rtc 대신 이 프레임을
> ffmpeg 표준 입력으로 넣는다. 작업대 확인: 1280×720 MJPEG 10fps, 장치 열기 약 2.3초, 신호 없을 때 프레임 19KB.
> 아래 go2rtc 설정은 쓰지 않는다(참고로 남긴다).
```yaml
# /etc/go2rtc.yaml (Radxa)
streams:
  birdview: v4l2:device?video=/dev/v4l/by-id/usb-XXXX-video-index0&input_format=mjpeg&video_size=1280x720
api:
  listen: ":1984"         # 차 안 LAN에서만. 공유기에 포트 포워딩 없음
rtsp:
  listen: ""              # 쓰지 않는다 — rvnode는 HTTP MJPEG(`/api/stream.mjpeg`)로 읽는다
```
- 보는 쪽(rvnode 또는 차 안 브라우저)이 붙을 때만 장치를 연다. 일시정지 동안 rvnode가 연결을 닫으므로 캡처 장치도 닫힌다.

### 4.3 rvnode `devices/video.py` — 송출

> **2026-10-30 구현 (radxa `rvnode/devices/video_push.py`) — 아래 계획과 다른 점:**
> - **송출은 Lightsail에 직접 RTSP(TCP 8554).** Lightsail 방화벽에서 8554를 열었다(2026-10-30). Cloudflare 터널
>   (`cloudflared access tcp`)도 시험했으나 쓰지 않는다 — 터널로 들어온 접속은 go2rtc에 127.0.0.1로 보이고, go2rtc는
>   localhost에는 RTSP 계정을 묻지 않아 Access 같은 앞단 인증이 따로 필요하다. 직접 접속은 go2rtc가 Basic 인증을 요구한다
>   (계정 없이 `401` 확인). 영상·계정은 평문 — §7.2 "1차는 평문 + 계정"대로.
> - **명령은 `rv/radxa/video/control/push/set`**, `start`에 `url` 대신 `path`·`user`·`pass`. 송출 주소의 호스트는
>   Radxa 설정 `push.url`(기본 `rtsp://mqtt.nam6411.com:8554`)로 고정이라 `allowed_host`가 필요 없다. 경로는 `[A-Za-z0-9_-]`만.
>   숨은 명령(HA에 안 올림)이라 로그에 내용을 남기지 않는다.
> - 프레임은 로컬 go2rtc 대신 rvnode `Video.frames()`(§4.2 바뀜). 인코더는 Debian ffmpeg의 **libx264**
>   (rkmpp 빌드 없음): 720p 10fps 1.5 Mbps에서 CPU 약 0.55코어. fps는 캡처와 같은 10.
> - HA 엔티티: `binary_sensor` 송출 중(`push.streaming`), `sensor` 모드(`live`/`paused`/`off`)·라이브 남은 초,
>   `button` 이어보기(`control/extend/set` PRESS).
> - 실제 확인(2026-10-30): ls go2rtc에서 열면 첫 화면까지 **1.9초**, HA `camera.birdview` 스냅샷 7초. 나가면 송출 끝.
>   HA는 generic camera `rtsp://172.17.0.1:8554/birdview`(계정은 ls `/opt/go2rtc/secrets.env`), 대시보드 하위 화면 `/caravan/birdview`.
> - 작업대 확인: ffmpeg RTSP 수신(`-rtsp_flags listen`)으로 받아 1280×720 H.264, 1분 뒤 일시정지 화면
>   (Noto Sans CJK 글자) + 캡처 장치 닫힘, 이어보기로 라이브 복귀, keepalive·stop 확인.
- 명령 토픽 `rv/<radxa>/control/video/set` (JSON):
  - `{"cmd":"start","url":"rtsp://<id>:<pw>@<ls>:8554/<경로>","id":"<세션>"}` — 송출 시작(이미 송출 중이면 keepalive로 본다)
  - `{"cmd":"keepalive","id":"<세션>"}` — 10초마다. **30초 동안 없으면 멈춘다**
  - `{"cmd":"stop","id":"<세션>"}` — 바로 멈춘다
  - `{"cmd":"extend"}` — **이어보기.** 송출 중인 세션을 라이브로 1분 더(라이브 중이면 남은 시간을 1분으로 되돌린다).
    세션이 없으면 무시한다. HA `button`으로 낸다(§6). 세션 `id`는 Radxa가 기억하므로 HA는 몰라도 된다.
  - `url`은 허용 목록(설정의 `video.allowed_host`)의 호스트만 받는다 — MQTT로 아무 데나 보내게 하지 않는다.
- **프레임은 rvnode가 넣는다.** ffmpeg 하나를 세션 내내 띄워 두고 표준 입력으로 JPEG를 받는다. 그래서 라이브 ↔ 일시정지를
  바꿔도 ffmpeg·송출 연결이 다시 시작되지 않는다.
  - 라이브: 로컬 go2rtc `http://127.0.0.1:1984/api/stream.mjpeg?src=birdview`의 JPEG를 그대로 넘긴다
  - 일시정지: 그 연결을 닫고(캡처 장치가 쉰다), 미리 만든 정지 화면 JPEG를 1초에 한 장 넘긴다
- ffmpeg (Rockchip 하드웨어 인코더가 든 빌드 — jellyfin-ffmpeg 또는 Radxa 저장소의 ffmpeg-rockchip):

```
ffmpeg -f mjpeg -use_wallclock_as_timestamps 1 -i pipe:0 \
  -an -vf fps=15 -c:v h264_rkmpp -b:v 1500k -maxrate 2000k -g 30 -profile:v main \
  -f rtsp -rtsp_transport tcp "<url>"
```
  - `fps=15`가 들어오는 간격과 상관없이 15fps로 맞춘다(일시정지 때는 같은 그림을 반복 — 거의 0 바이트).
  - 720p 15fps 1.5 Mbps에서 시작한다. 버드뷰 화면은 움직임이 적어 더 낮춰도 된다.
  - 하드웨어 인코더가 안 되면 `-c:v libx264 -preset ultrafast -tune zerolatency`, 640×360으로 줄인다(CPU 4코어 A55).
- **라이브 상한**: 라이브는 `video.live_s`(기본 60초)마다 일시정지로 바뀐다. 이어보기(`extend`) 또는 창을 닫았다 열기(새 `start`)로 다시 라이브.
  - 정지 화면: 1280×720, 마지막 라이브 프레임을 어둡게 깔고 "일시정지 — 이어보기를 누르세요"(한글 글꼴 `fonts-nanum`).
  - 창을 잊고 열어 둬도 1분 뒤에는 정지 화면만 간다. 창을 닫으면 keepalive가 끊겨 30초 안에 송출이 끝난다.
  - 하루 합계 제한은 없다(정액제, 결정 4).
- state `rv/<radxa>/video/state` (retain):
  `{"streaming":true,"mode":"live","since":"…","left_s":42,"capture":"ok"}`  (`mode`: `live` / `paused`, 송출이 없으면 `streaming:false`)
  HA에 `binary_sensor`(송출 중)·`sensor`(모드, 라이브 남은 초)·`button`(이어보기 → `{"cmd":"extend"}`)으로 낸다.

## 5. Lightsail (`ls`)

> **2026-10-30:** 설치는 radxa 저장소 `ls/go2rtc/setup.sh`(ls에서 `sudo sh setup.sh`). 요청 스크립트는 셸 대신
> `bv_request.py` — go2rtc 이미지(Alpine)에 `mosquitto_pub`가 없어 MQTT를 표준 라이브러리로 직접 보낸다.
> 비밀값은 `/opt/go2rtc/secrets.env`(RTSP 계정, MQTT 계정 `go2rtc`). 컨테이너는 host 네트워크라 HA는 `172.17.0.1:8554`.

### 5.1 go2rtc 컨테이너
HA 컨테이너와 같은 서버에 따로 띄운다(HA에 내장된 go2rtc는 설정을 바꿀 수 없다).

```yaml
# go2rtc.yaml (Lightsail)
streams:
  birdview: exec:/config/bv-request.sh {output}#killsignal=15
rtsp:
  listen: ":8554"
  username: "<송출·읽기 계정>"
  password: "<…>"
api:
  listen: "127.0.0.1:1984"   # 밖에 열지 않는다
```
- `exec:` 소스는 보는 쪽이 생기면 실행되고, 모두 떠나면 끝난다. `{output}`은 go2rtc가 이 세션에 내준 RTSP 주소 —
  스크립트는 그 **경로**를 Radxa에 보내고, Radxa는 공개 주소의 같은 경로로 송출한다.
- 방화벽: **TCP 8554만** 연다(Radxa 송출용). Radxa는 LTE라 주소가 바뀌어 IP로 막을 수 없다 — 계정과 세션마다 바뀌는 경로로 막는다.

### 5.2 `bv-request.sh`
```sh
#!/bin/sh
# go2rtc가 보는 쪽이 생기면 실행한다. $1 = rtsp://127.0.0.1:8554/<경로>
path="${1##*/}"; id="$(date +%s)-$$"
topic="<remote_prefix>/rv/<radxa>/control/video/set"
pub() { mosquitto_pub -h <broker> -u <user> -P <pw> -t "$topic" -m "$1"; }
trap 'pub "{\"cmd\":\"stop\",\"id\":\"$id\"}"; exit 0' TERM INT
pub "{\"cmd\":\"start\",\"id\":\"$id\",\"url\":\"rtsp://<id>:<pw>@<ls 공개 주소>:8554/$path\"}"
while :; do sleep 10; pub "{\"cmd\":\"keepalive\",\"id\":\"$id\"}"; done
```
- `<remote_prefix>`는 브리지 설정과 같게 (docs/17 §3). 브로커가 같은 서버면 `-h localhost`.
- go2rtc가 송출을 기다리는 시간(첫 화면까지 5~10초)보다 짧게 끊으면 실패한다 — §9.

### 5.3 HA
- **Generic camera**: 스트림 주소 `rtsp://<계정>:<pw>@<go2rtc 컨테이너>:8554/birdview`, **정지 화면 주소는 비운다.**
- **주의: 썸네일이 송출을 켠다.** 대시보드 카드(picture-entity·picture-glance)는 카메라 정지 화면을 10초마다 받는데,
  정지 화면 주소가 없으면 HA가 스트림을 열어 프레임을 뜬다 → 대시보드를 열어 두기만 해도 송출이 켜진다.
  그래서 대시보드 첫 화면에는 카메라 이미지를 두지 않고, **버튼으로 버드뷰 전용 하위 화면(subview)에 들어가게** 한다.
  하위 화면의 카메라 카드는 `camera_view: live`라 정지 화면을 받지 않고, 화면에 있는 동안만 스트림을 연다 — 들어가면 송출, 나오면 끝.
  more-info 창 대신 하위 화면을 쓰는 이유: 라이브를 보면서 **이어보기·시점 버튼을 같이** 눌러야 한다.
- 카메라 엔티티 설정의 '스트림 미리 불러오기'(`preload_stream`)는 끈다 — 켜면 HA가 늘 스트림을 열어 둔다.

## 6. HA 화면 (안)

첫 화면(`caravan` 대시보드)에는 들어가는 버튼만:

```yaml
type: button
name: 버드뷰 보기
icon: mdi:car-back
tap_action: { action: navigate, navigation_path: /caravan/birdview }
```

하위 화면 `birdview` (`subview: true`, 뒤로 가기 = 송출 끝):

```yaml
type: vertical-stack
cards:
  - type: picture-entity
    entity: camera.birdview
    camera_view: live          # 화면에 있는 동안만 스트림
    show_state: false
  - type: horizontal-stack
    cards:
      - type: button
        name: 이어보기 (1분)
        icon: mdi:play-circle
        entity: button.radxa_video_extend
        tap_action: { action: perform-action, perform_action: button.press, target: { entity_id: button.radxa_video_extend } }
      - type: glance
        show_name: false
        entities:
          - sensor.radxa_video_mode        # live / paused
          - sensor.radxa_video_left_s      # 라이브 남은 초
  - type: horizontal-stack     # 시점 바꾸기 (docs/29 §6에서 이름 정함)
    cards:
      - { type: button, name: 후방, entity: button.birdview_rear }
      - { type: button, name: 왼쪽, entity: button.birdview_left }
      - { type: button, name: 오른쪽, entity: button.birdview_right }
```
- 이어보기는 MQTT로 차까지 왕복하므로 누른 뒤 라이브가 돌아오기까지 1~2초(+ 다음 키프레임 최대 2초).

## 7. 데이터·보안

### 7.1 LTE 데이터
정액제라 요금 걱정은 없다. 대역(LTE 업로드)과 화면이 끊기지 않는지가 기준이다.

| 설정 | 대역 | 1회(1분) |
|---|---|---|
| 720p 15fps H.264 1.5 Mbps | 업로드 1.5~2 Mbps | 약 11 MB |
| 640×360 15fps 0.6 Mbps | 업로드 0.6 Mbps | 약 4.5 MB |
| (참고) MJPEG 720p 그대로 | 업로드 8 Mbps 이상 | 쓰지 않는다 — LTE 업로드가 버티지 못한다 |

### 7.2 보안
- 영상은 RTSP 평문으로 인터넷을 지난다. 계정·경로가 세션마다 바뀌어 남이 끼어들기는 어렵지만 엿볼 수는 있다.
  필요하면 RTSPS(go2rtc TLS) 또는 Radxa→Lightsail SSH 포워딩으로 바꾼다 — 1차에서는 평문 + 계정.
- MQTT `start`의 `url`은 허용 호스트만 받는다(§4.3). 브리지 외부 브로커 계정이 털리면 송출을 켤 수는 있지만 다른 곳으로 보낼 수는 없다.
- Radxa go2rtc의 웹 UI(`:1984`)는 차 안 LAN에만 있다. 공유기에 포트 포워딩을 두지 않는다.

### 7.3 차 안에서
차 안 Wi-Fi에서는 LTE를 쓰지 않고 Radxa에 직접 붙어 볼 수 있다: `http://<Radxa IP>:1984/stream.html?src=birdview&mode=mjpeg`
(MJPEG 그대로, 지연 짧음). HA에 iframe 카드로 둘 수 있지만 차 밖에서는 빈 카드가 된다. 선택.

## 8. 설정 (Radxa `rvnode` YAML)

| 키 | 기본 | 용도 |
|---|---|---|
| `video.enabled` | true | 송출 명령을 받는다 |
| `video.source` | `rtsp://127.0.0.1:8554/birdview` | 로컬 go2rtc |
| `video.allowed_host` | (Lightsail 주소) | 송출 허용 호스트 |
| `video.encoder` | `h264_rkmpp` | 안 되면 `libx264` |
| `video.size` / `video.fps` / `video.kbps` | 1280×720 / 15 / 1500 | |
| `video.keepalive_s` | 30 | 이 시간 동안 keepalive가 없으면 멈춤 |
| `video.live_s` | 60 | 라이브 한 번 길이 (이어보기도 이만큼) |
| `video.pause_fps` | 1 | 일시정지 화면을 넣는 간격 |

## 9. 순서와 확인 항목

1. **작업대 PC**: 캡처 모듈로 버드뷰 출력이 보이는지 (OBS·VLC). 출력 형식(AHD/CVBS)·해상도
2. **Radxa**: Host 포트에 C-C 케이블로 모듈이 잡히는지(`lsusb`), `v4l2-ctl --list-formats-ext`로 MJPG 확인, 로컬 go2rtc로 브라우저에서 보이는지
3. **인코더**: `h264_rkmpp`가 되는지, 720p15에서 CPU·온도. 안 되면 libx264 360p
4. **Lightsail**: go2rtc 컨테이너, 8554 열기. 먼저 Radxa에서 손으로 ffmpeg 송출 → VLC로 받아 보기
5. **요청 흐름**: `exec` 스크립트 → MQTT → rvnode → 송출. 확인할 것:
   - go2rtc `exec`가 `{output}` 경로로 외부 송출을 받아 주는지, 송출을 기다리는 제한 시간(첫 화면 5~10초보다 긴지)
   - `#killsignal=15`로 스크립트가 `stop`을 보내는지 (안 되면 keepalive 30초로 끝나는지)
   - 첫 화면까지 걸린 시간
6. **HA**: generic camera, 버튼 → 하위 화면에서 라이브. 첫 화면만 열어 두었을 때 송출이 켜지지 않는지, 하위 화면에서 나오면 끝나는지
7. **라이브 상한·이어보기**: 1분에 정지 화면으로 바뀌는지(HA 쪽 스트림이 끊기지 않는지), 이어보기로 바로 라이브가 돌아오는지,
   일시정지 동안 캡처 장치가 닫히는지(`fuser`), 정지 화면 대역, LTE를 끊었다 붙였을 때 ffmpeg가 남지 않는지

## 출처
- go2rtc: https://github.com/AlexxIT/go2rtc (v4l2 소스, `exec` 소스·`{output}`·`killsignal`, RTSP 서버, `stream.html`)
- 캡처 모듈: 판매 페이지 설명 (2026-10-21 구매)
