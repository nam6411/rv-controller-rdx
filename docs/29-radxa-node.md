# 29. Radxa 통합 노드 — 넘길 기능과 옮기는 순서

> 상태: **계획 (2026-10-20).** 하드웨어(핀·기판·결선)는 [18-wiring.md](18-wiring.md) §12. 이 문서는 소프트웨어 쪽 —
> ESP에서 무엇을 넘기고 무엇을 남기는지, 옮겨도 바꾸면 안 되는 것, 옮기는 순서.

## 1. 결정 (2026-10-20)

| # | 항목 | 결정 |
|---|---|---|
| 1 | MQTT 브로커·외부 브리지 | **Radxa로 옮긴다** — mosquitto. 날씨·지명(HTTPS)도 Radxa에서 받는다 |
| 2 | 433MHz (CC1101, 버드뷰 노브 리모컨) | **Radxa에서 쓴다.** ESP 펌웨어에서는 뺐다(2026-10-20) — RF는 Radxa만 맡는다 |
| 3 | 소프트웨어 | **Python** (asyncio). 프레임 해석은 ESP의 C++ 헤더를 옮기고, native 테스트 벡터를 그대로 옮겨 맞춘다 |
| 4 | 폴백 AP `RV-FALLBACK` | **Radxa가 띄운다** — ESP 브로커와 같은 규칙 (§3.4) |
| 5 | 버드뷰 시점 | **GPS로 판단한다.** 주행을 시작하면 후방, 깜빡이를 넣으면 넣은 쪽. 세부 규칙은 나중에 (§6) |
| 6 | 빈 ESP 보드 | `9FCEE4`(MACH+JBD+TPMS)와 브로커 보드는 **옮긴 뒤 수거**한다 |

## 2. 기능 배치

### 2.1 Radxa로 넘기는 것

| 기능 | 지금 | ESP 쪽 기준 (옮길 원본) | Radxa에서 |
|---|---|---|---|
| MACH-BASIC (BLE) — Schaudt·Alde·Truma 에어컨·수평 | ESP32-S3 `9FCEE4` 슬롯 `mach` | `src/modules/mach/` (`MachFrame.h`, `MachBle.cpp`, `Mach.cpp`), `lin/LinFrame.h`, docs/19·22 | bleak, Nordic UART. 페어링 PIN 123456 |
| BLE TPMS (수동 스캔) | 같은 보드 슬롯 `tpms` | `src/modules/tpms/TpmsFrame.h`, docs/23 | bleak 수동 스캔. MACH 연결과 스캐너를 나눌 필요가 없다 |
| JBD BMS (BLE 동글) | 같은 보드 슬롯 `bms` | `src/modules/jbdbms/` (`JbdFrame.h`, `JbdBle.cpp`), docs/04 §7 | bleak, 서비스 FF00. 충전 제한(`charge_limit`)·0x2D 스위치 처리 포함 |
| MQTT 브로커 + 외부 브리지 | ESP 브로커 역할 (PicoMQTT + retained 직접 보관) | `src/core/BrokerService.*`, `MqttBridge.*`, docs/15 §3.1·§5, docs/17 | mosquitto (retained·브리지·TLS 기본) + 노드 탐색용 비콘·mDNS (§3.3) |
| 날씨·지명 | 미구현 | docs/24 §4.3, docs/28 §6.1 | `rv/weather` (Open-Meteo, Nominatim) |
| GPS·나침반 | 미구현 (작업대 실측 완료) | docs/24 §2~§4.2·§7 | gpsd (UART4) → `rv/<node>/gps`, chrony로 시스템 시계. 나침반 I2C5 |
| 버드뷰 시점 트리거 | 새 기능 | docs/18 §12.1·§12.5 | gpiod 출력 3개(후방·좌·우). GPS 속력으로 주행 판단 + 13핀 깜빡이·후진 입력으로 자동 전환 (§1 결정 5) |
| 버드뷰 전원 (ACC) | 새 기능 (2026-10-24) | docs/18 §12.2 "AVM 켜짐" | 차 시동 12V를 옵토로 핀 37에 읽고, 핀 35로 옵토를 켜 카라반 12V를 ACC_IN에. 켜는 조건(시동 OR HA 스위치 OR 원격 영상)은 rvnode가 정한다 |
| 버드뷰 노브 리모컨 (CC1101) | ESP에서 제거 (2026-10-20) | git `75da6e0^`의 `src/modules/irrf/Cc1101.*`, `IrRfCode.h` `birdview`, docs/25 §10 | spidev3.0 (CS0 = 빈 핀 24, CSN = 핀 15 GPIO로 직접 — `SPI_NO_CS`는 안 됨). 왼쪽·오른쪽·클릭 송신, 리모컨 수신 시 seq 맞춤 |
| 버드뷰 화면 (AHD → USB 캡처) | 새 기능 (모듈 구매 2026-10-21) | docs/30 | OTG USB-C, 로컬 go2rtc + rvnode `video`(ffmpeg H.264 → Lightsail RTSP). HA에서 열 때만 MQTT로 켠다 |
| 버드뷰 UART (JP5) | 프로토콜 조사 중 | 메모(19200 8N1, `2E type len data ~sum`) | 선택. 먼저 수신만 기록 |

### 2.2 ESP에 남는 것

| 기능 | 노드 | 이유 |
|---|---|---|
| UPower (RS485) | `A0CA48` `upower` | 인버터 옆 배선 |
| ANT BMS (UART) | (미설치) | 배터리 옆 배선 |
| 릴레이 4CH | `8D2724` `relay` | ESP32_Relay_X4 보드 자체 |
| MaxxFan (IR) | `remote` (미설치) | 천장 — 팬 보드 TSOP 자리에 유선 (docs/27, docs/18 §11) |
| 디스플레이 | CrowPanel 2.1 | 일체형 |

옮기고 나면 `9FCEE4`(MACH+JBD+TPMS)와 ESP 브로커 보드가 빈다 — 수거한다.

## 3. 옮겨도 바꾸면 안 되는 것

### 3.1 HA 엔티티 — `unique_id`
ESP 노드는 `uniq_id = <장치 ID>_<slug>_<키>`, 기기 `ids = [<장치 ID>]`로 discovery를 낸다 (`src/core/HaDiscovery.cpp`).
지금 HA에 등록된 MQTT 엔티티 (2026-10-20):

| 접두어 | 개수 | 넘김 |
|---|---|---|
| `9FCEE4_mach_` | 34 | Radxa |
| `9FCEE4_bms_` | 32 | Radxa |
| `9FCEE4_tpms_` | 9 | Radxa |
| `A0CA48_upower_` | 52 | ESP 그대로 |
| `8D2724_relay_` | 4 | ESP 그대로 |

**Radxa는 장치 ID `9FCEE4`와 slug `mach`·`bms`·`tpms`, 키 이름을 그대로 쓴다.** 그래야 엔티티 ID(`sensor.tpms_caravan_left_pressure`,
`sensor.mach_level_pitch` 등)·기록·대시보드·주행 카드가 이어진다. 장치 ID는 설정값으로 둔다(하드코딩하지 않는다).
옮긴 뒤 `9FCEE4` ESP는 켜지 않는다 — 같은 `uniq_id`가 둘이면 마지막에 낸 쪽이 덮는다.

### 3.2 MQTT 토픽·페이로드
| 토픽 | 읽는 쪽 | 지킬 것 |
|---|---|---|
| `rv/mach/state` | UPower 에어컨 인터록(`ac.mode`), 디스플레이(`ac.*`, `alde.set`, `ebl.fresh`·`inside`), MaxxFan 노드 자동 제어(설정 시) | 키 이름·단위·`ac.ok`·`live` 그대로 |
| `rv/mach/tpms/state`, `rv/bms/state` | 디스플레이(`bms:soc`·`power`), HA | 〃 |
| `<prefix>/meta`, `availability`, `switch/<n>/state` | 디스플레이 | meta JSON(`type`, `label`, `switches`, `metrics`, `ring`) 같은 모양 |
| `<prefix>/switch/<n>/set`, `control/<n>/set`, `sys/cmd` | HA·디스플레이 → 노드 | 명령 형식·응답(state 즉시 재발행) 같게 |
| `homeassistant/status` = `online` | 노드 | 받으면 discovery·state 재발행 |

- MQTT 규약 전체: docs/07. 슬롯 2개 이상 노드라 prefix는 `rv/mach`(MACH)·`rv/mach/tpms`(TPMS)다 — 지금과 같다. JBD는 `rv/bms`.
- ESP가 하던 동작을 같이 옮긴다: 장치가 3회 연속 실패하면 `availability = offline`, 명령을 받으면 바로 state 발행, BLE 재연결 백오프.

### 3.3 브로커가 하던 일
mosquitto가 기본으로 하지 않는 것만 따로 만든다.

| 기능 | ESP 브로커 | Radxa |
|---|---|---|
| retained 보관·재생 | 직접 구현 (128 토픽 × 1280B) | mosquitto 기본. **`cmd`·`set`은 retain하지 않는다** 규칙은 클라이언트 쪽이 이미 지킨다 |
| 노드 탐색 — mDNS | `broker.local`, `_mqtt._tcp` | avahi: 호스트명 `broker` 또는 별칭 + `_mqtt._tcp` 서비스 파일 |
| 노드 탐색 — UDP 비콘 | 5초마다 포트 47300 `essio-broker <host> <port>` | 작은 데몬(또는 rvnode 안). **공유기가 멀티캐스트를 막아도 노드가 찾는 길이라 꼭 필요** (docs/15 §5) |
| 외부 브리지 | out `rv/#,homeassistant/#`, in `…/switch/+/set`·`…/control/+/set`·`…/sys/cmd`·`homeassistant/status`, LWT `rv/broker/bridge` | mosquitto `connection` 블록 (topic out/in, `remote_prefix` → 토픽 접두어, TLS). 에코 필터는 방향을 나눠 두면 필요 없다 |
| 재접속 후 재동기화 | retained 재전송 + 로컬에 `homeassistant/status = online` | 브리지 상태 토픽(`notifications`)을 보고 같은 birth를 로컬에 낸다 |
| 자체 상태 | `rv/broker/status`, `rv/broker/diag/info` | 같은 토픽으로 (HA·디스플레이 개요가 본다) |
| 폴백 AP `RV-FALLBACK` | 공유기가 없으면 브로커가 AP를 띄워 노드를 묶는다 | Radxa가 띄운다 — §3.4 |

### 3.4 폴백 AP — ESP 브로커와 같은 규칙
ESP 노드는 바꾸지 않는다. 노드가 기대하는 것을 Radxa가 그대로 해 준다 (docs/15 §5·§6).

| 항목 | 값 |
|---|---|
| SSID / 비밀번호 | `RV-FALLBACK` / 노드의 `wifi.fallback_password`와 같게 (기본값은 Config.cpp, 저장소 밖 설정에 둔다) |
| 언제 | 부팅 때 공유기 SSID가 안 보이면 바로, STA가 60초(`wifi.ap.fallback_after_s`) 넘게 끊기면 AP로 내려간다 |
| 복귀 | AP 상태에서 30초마다 공유기를 다시 찾아 보이면 STA로 돌아간다. AP와 STA를 동시에 띄우지 않는다(무선 하나를 나눠 쓰면 둘 다 느려진다) |
| 주소 | Radxa = AP 게이트웨이(예: `192.168.4.1`), DHCP로 노드에 그 게이트웨이를 준다 — **노드는 폴백 SSID에 붙으면 게이트웨이를 브로커로 쓴다** |
| 탐색 | AP 위에서도 비콘(47300)·mDNS를 낸다 |
| 무선 | 내장 AIC8800이 Wi-Fi STA/AP와 BLE를 같이 맡는다 (2026-10-21, 동글 없이 먼저 시도). AP 모드 지원은 보드 준비 때 먼저 확인. AP·BLE를 같이 돌릴 때 BLE가 버티는지도 본다 |
| 구현 | NetworkManager의 AP 연결 프로필 + 상태를 보고 STA/AP를 바꾸는 작은 감시 태스크 (또는 hostapd + dnsmasq) |

- AP 상태에서는 LTE 공유기가 없으므로 외부 브리지·날씨는 멈춘다. 로컬(노드·디스플레이·스위치)은 그대로 동작해야 한다.
- 공유기가 돌아오면 노드들은 다시 공유기로 붙고 비콘·mDNS로 Radxa를 찾는다.

## 4. 소프트웨어 구성 (안)

```
radxa/
  rvnode/            Python 패키지, asyncio 데몬 하나
    mqtt.py          paho-mqtt. ESP 규약(state·meta·availability·discovery·set 명령) 공통 처리
    ha.py            discovery 생성 — uniq_id·키 이름은 ESP와 같게
    ble.py           bleak 어댑터 공유 (스캔 + 연결 여러 개)
    devices/         mach.py · jbd.py · tpms.py · birdview.py · cc1101.py · gps.py · weather.py
    frames/          MachFrame·JbdFrame·TpmsFrame·LinFrame(aventa 등)·birdview를 옮긴 순수 함수
  tests/             test/test_frames/test_main.cpp의 벡터를 옮긴 pytest
  systemd/           rvnode.service, essio-beacon(필요하면), mosquitto.conf, avahi 서비스
```
- 데몬 하나에 장치마다 태스크 — 한 장치가 끊겨도 다른 장치는 계속 돈다(ESP의 블로킹 재연결 문제가 없어진다).
- 설정은 YAML 한 파일(장치 ID, MAC·PIN, 버드뷰 핀·매핑, 브리지 계정). 비밀값은 저장소에 넣지 않는다.
- 배포: 저장소 `radxa/`를 rsync → systemd 재시작. OTA 대신 SSH.

## 5. 옮기는 순서

한 단계씩, 되돌릴 수 있게. 앞 단계가 안정되기 전에 다음으로 가지 않는다.

1. **보드 준비** — Radxa OS, rsetup 오버레이(SPI3-M1, UART3-M0, UART4-M1, I2C5-M0), Wi-Fi, **내장 BT**(`bluetoothctl`·bleak 스캔).
   내장 BLE로 MACH·JBD에 동시에 붙고 TPMS 광고를 받으면서 하루 버티는지 본다. 안 되면 USB BT 동글(RTL8761B)을 단다(docs/18 §12).
   이때 Radxa는 ESP 브로커에 붙는 클라이언트다.
2. **충돌 없는 새 기능부터** — GPS·나침반, 날씨·지명(`rv/weather`), 버드뷰 트리거·13핀 입력, CC1101 리모컨.
   지금 ESP가 하지 않는 것이라 병행 운용이 필요 없다.
3. **TPMS 그림자 운용** — 수동 스캔이라 ESP와 동시에 받을 수 있다. Radxa는 discovery를 내지 않고 다른 토픽(`rvtest/…`)에
   발행해 ESP state와 값을 비교한다. 같으면 ESP `tpms` 슬롯을 끄고 Radxa가 `9FCEE4_tpms_*`로 넘겨받는다.
4. **JBD → MACH 차례로 전환** — BLE 연결은 한쪽만 붙을 수 있어 그림자 운용이 안 된다. ESP 슬롯을 끄고 Radxa를 켠 뒤,
   전환 전에 저장해 둔 ESP state와 키·값을 비교한다. MACH는 UPower 인터록과 디스플레이 에어컨이 걸려 있어 마지막에 한다.
   다 넘어가면 `9FCEE4`를 끈다.
5. **브로커 전환** — mosquitto·avahi·비콘·브리지를 띄우고 ESP 브로커를 끈다. 노드들이 비콘·mDNS로 Radxa를 찾는지,
   원격 HA가 브리지로 다시 붙는지 확인한다. 문제가 있으면 ESP 브로커를 다시 켜면 원래대로 돌아간다.

## 6. 정할 것

- 버드뷰 시점 세부 규칙 — 정해진 것: GPS로 주행을 판단, 주행을 시작하면 후방, 깜빡이를 넣으면 넣은 쪽. 나중에 정할 것:
  주행 시작·정차의 속력 기준과 지연, 깜빡이 유지 시간(마지막 펄스 뒤 약 1.5초?), 후진 입력·정차 중일 때, GPS가 위치를 못 잡을 때,
  (깜빡이 유지 1.5초·후진 → 후방·비상등 → 기본 화면은 rvnode `birdview`에 들어갔다 — `blink_hold_s` 설정, 2026-10-26).
- 버드뷰 전원 — 원격 영상이 켤 때 버드뷰 부팅 대기 시간. (끄기 지연은 설정 `off_delay_s`, 기본 30초로 넣었다 — 실차에서 맞춘다.)
- ~~수동 엔티티 이름~~ → `birdview_*` (2026-10-26): `switch.birdview_power`(계속 켜기), `select.birdview_mode`(auto·rear·left·right·none),
  `sensor.birdview_view`, `binary_sensor.birdview_powered`·`_ignition`·`_turn_left`·`_turn_right`·`_reverse`. 토픽은 `rv/mach/birdview`.
- 버드뷰 리모컨 엔티티 — 예전 ESP의 `button.remote_bv_left` 등과 이름을 맞출지, Radxa 쪽 새 이름(`birdview_*`)으로 갈지.
- Radxa 전원 — 상시 전원(퓨즈 3A). 대기 전류와 배터리 보호 기준.
