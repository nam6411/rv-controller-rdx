# 24. GPS와 현 위치 날씨 (계획)

> 상태: **계획 — 모듈 실측 완료(2026-10-20·21, §7), 노드 구현 전.** GPS·나침반·날씨는 **Radxa 통합 노드**가 맡는다
> (2026-10-20 결정, [29-radxa-node.md](29-radxa-node.md) §1·§2.1). 옮기는 순서의 2단계(충돌 없는 새 기능)다. §5 순서로 구현한다.

Radxa 통합 노드에 GPS를 달아 현재 위치를 알고, 그 위치의 날씨와 지명을 디스플레이와 HA에 보인다.
위치는 HA 지도(`device_tracker`)에도 보낸다.

## 1. 구성

```
BE-880 GPS ─UART4─┐
 (QMC5883 나침반) ─I2C5─ Radxa ZERO 3W (카라반 고정·상시 전원)
                    │  gpsd → rvnode gps   ── rv/<radxa>/gps (위치·방위, retain)
                    │  rvnode weather ─HTTPS─ Open-Meteo (날씨), Nominatim (지명)
                    │                     ── rv/weather (retain)
                    ▼
              디스플레이 화면 3 · HA
```

- **GPS는 Radxa에**: 배선은 [18-wiring.md](18-wiring.md) §12 (UART4 = 핀 16·18, 나침반 I2C5 = 핀 29·31, 커넥터 J6·J8).
  Radxa는 RTC 전지가 없어 GPS 시각을 `gpsd` + `chrony`로 시스템 시계에도 쓴다 — LTE가 끊겨도 시각이 맞는다.
  같은 보드의 버드뷰 시점 트리거가 GPS 속력으로 주행을 판단한다 (docs/29 §1 결정 5).
- **날씨·지명도 Radxa가 받는다**: Linux라 HTTPS에 메모리 제약이 없다. ESP 브로커가 받던 안(내부 RAM 수십 KB)은 쓰지 않는다.
  디스플레이(CrowPanel)는 내부 RAM이 30 KB대로 떨어지면 MQTT가 끊긴 적이 있어(docs/15 §3.3) 그대로 `rv/weather`만 받는다.
- 공유기가 인터넷(LTE 등)에 연결돼 있어야 날씨·지명을 받는다. 연결이 없으면 마지막 값과 받은 시각을 그대로 보인다.
  폴백 AP 상태(docs/29 §3.4)에서도 GPS 발행은 계속된다.
- (예전 안, 2026-10-12) GPS를 디스플레이 노드 뒷면 UART 커넥터(IO43·44)에 달고 날씨는 ESP 브로커가 받는 안이었다.
  Radxa로 옮기면서 접었다. 토픽·JSON은 그때 정한 것을 그대로 쓴다.

## 2. GPS 모듈 — Beitian BE-880

| 항목 | 내용 |
|---|---|
| 칩 | u-blox M10 계열 |
| 크기 | 28 × 28 × 11 mm — 후보(BE-122·182·220·252i·880) 중 가장 크다 = 안테나가 커서 실내 수신에 유리 |
| 위성 | GPS + BeiDou + Galileo, QZSS·SBAS(WAAS·EGNOS·MSAS·GAGAN) |
| 나침반 | QMC5883L 내장 (I2C 0x0D) — Radxa I2C5(핀 29·31)에 잇는다(docs/18 §12). 실내 금속 근처라 설치 후 보정 필요 (§7.3) |
| 커넥터 | 6핀, 실크 `C V R T G D` = SCL · VCC · RX · TX · GND · SDA (§7.1). C·D는 나침반 I2C |
| 백업 전지 | `BT1` — 전원이 잠깐 끊겨도 위성 정보를 기억해 재시작 시 1~2초 만에 위치를 잡는다 |
| LED | TX = 데이터 출력 중, PPS = 위치 잡힘 |
| 전원 | 5V로 동작 확인 (작업대 NodeMCU VIN, Radxa에서는 J6 5V). 사양 3.6~5.5V. **VCC 5V일 때 TX High = 3.28V** (2026-10-21 실측) → Radxa 3.3V GPIO에 분압 없이 바로 잇는다 |
| 기본 보레이트 | **115200** (실측). UBX `CFG-UART1-BAUDRATE`로 바꿀 수 있다 |

고르지 않은 이유: BE-122·182는 안테나가 작아 하늘이 트인 드론용, BE-220은 무난하지만 안테나가 더 작다, BE-252i는 사양 미확인.

## 3. 배선

자세한 회로·기판은 [18-wiring.md](18-wiring.md) §12.1·§12.2·§12.5. BE-880 케이블 6가닥을 두 플러그로 나눈다.

| BE-880 (실크) | 커넥터 | Radxa 핀 | 비고 |
|---|---|---|---|
| V (VCC) | J6 5V | 2·4 (5V) | |
| G (GND) | J6 GND | 14 (GND) | 나침반도 이 GND를 같이 쓴다 |
| T (TX) | J6 TX | 16 (UART4_RX_M1) | 3.28V 출력 — 바로 잇는다 |
| R (RX) | J6 RX | 18 (UART4_TX_M1) | 설정 변경(UBX)용 |
| C (SCL) | J8 SCL | 29 (I2C5_SCL_M0) | 4.7kΩ 풀업 R19 |
| D (SDA) | J8 SDA | 31 (I2C5_SDA_M0) | 4.7kΩ 풀업 R20 |

- 작업대에서 이 모듈은 **C·D가 실크와 반대로 동작**했다(§7.1). 나침반이 `i2cdetect -y 5`에 안 보이면 J8 두 선을 바꾼다.
- 케이블 1~3m, I2C는 2m 안이 좋다. GPS 쪽은 SH1.0 6핀 케이블을 잘라 J6(XH 4P)·J8(Molex 5051 2P)에 단다.
- 설치: 하늘이 보이는 창가나 FRP 지붕(천창) 바로 아래. 금속 판·강압 모듈·Radxa·USB 3.0 포트에서 떨어뜨린다(§7.2, docs/18 §12.5).

## 4. 소프트웨어 설계

Radxa 쪽 데몬 `rvnode`(Python asyncio, docs/29 §4)의 `devices/gps.py`·`devices/weather.py`.

### 4.1 GPS 수신 — gpsd

- `rsetup` 오버레이 **UART4-M1** → 장치 이름은 `dmesg | grep tty`로 확인(`/dev/ttyS4` 예상). 보레이트 **115200**(기본값, §7.1).
  하드웨어 UART라 ESP8266에서처럼 38400으로 낮출 필요가 없다.
- `gpsd`가 포트를 쥐고, `chrony`는 gpsd의 SHM 시각을 참조 시계로 쓴다(PPS 선은 잇지 않았다 — NMEA 시각만, 수십 ms 정도면 충분).
  `rvnode`는 gpsd 소켓(2947)의 TPV·SKY 보고서를 받는다. NMEA를 직접 해석하지 않는다.
- 발행 `rv/<radxa>/gps` (retain, 10초마다 + 50m 넘게 움직이면 바로):

```json
{"fix":true,"lat":37.5665,"lon":126.9780,"alt_m":38,"sats":14,"hdop":0.9,"speed_kmh":0.0,"course":0,"heading":72,"utc":"2026-10-12T06:30:00Z"}
```

- `heading`은 나침반 방위(진북 기준, °). 정차 중에도 차가 어느 쪽을 보는지 안다. 주행 중 방향은 `course`(GPS 진행 방향)가 더 정확하다.
- fix가 없으면 마지막 위치를 그대로 두고 `fix:false`. 시각만 잡히고 위치가 없는 동안(콜드 스타트 수 분, §7.2)도 `fix:false`.
- HA: `device_tracker`(위치, `json_attributes`로 lat·lon·gps_accuracy) + 위성 수·속도·방위 센서.

### 4.2 나침반 — QMC5883L

- `/dev/i2c-5`, 주소 0x0D. 설정: 연속, 200Hz, ±8G, OSR 512 (§7.3과 같게). 1초 평균.
- 화살표 기준 방위 = **265° − 칩 각도** (§7.3, 칩 축이 모듈 화살표와 90° 돌고 방향이 반대). 진북 = 자북 − 편각(한국 약 8.5°, 설정값).
- 하드·소프트 아이언 보정값(중심·반지름)은 설정에 둔다. 설치 자리에서 차를 한 바퀴 돌려 다시 잡는다 — 차체 철물로 중심이 크게 바뀐다.
  보정 명령: `rv/<radxa>/sys/cmd` `compass_cal_start` / `compass_cal_stop` (돌리는 동안 최소·최대를 모아 저장).
- 기울기 보정이 없으므로 차가 기울면 방위가 틀어진다. MACH 수평값(`level.pitch`·`roll`)으로 보정할 수 있지만 1차에서는 하지 않는다.

### 4.3 날씨·지명 — `rv/weather`

- 자기 GPS 위치를 쓴다(`rv/+/gps`를 구독해 다른 노드의 위치도 받을 수 있게 둔다). 위치가 없으면 설정 `weather.lat/lon`(선택).
- [Open-Meteo](https://open-meteo.com/) Forecast API — 무료, API 키 없음. 30분마다, 또는 위치가 5 km 넘게 바뀌면 다시 받는다.
  - `current`: 기온, 체감 온도, 습도, 날씨 코드(WMO), 풍속, 강수량
  - `daily`(3일): 날씨 코드, 최고·최저, 강수 확률
- 지명: Nominatim `reverse` — 규칙은 [docs/28](https://github.com/nam6411/rv-controller/blob/master/docs/28-display-ui.md) §6.1 (영어, `zoom=14`, 위치가 1 km 넘게 바뀔 때만, 식별 가능한 User-Agent).
- 발행 `rv/weather` (retain). 디스플레이 retain 한도(512바이트)를 넘지 않게 화면·HA에 쓰는 값만 담는다. 화면이 쓰는 키는 docs/28 §6.2:

```json
{"t":"2026-10-12T15:30","temp":18.2,"feels":17.0,"code":3,"wind_kmh":12,"rain_mm":0.0,
 "place":"Daegwallyeong, Pyeongchang",
 "days":[{"d":"Today","code":3,"max":19,"min":9,"pop":20},{"d":"Sat","code":61,"max":16,"min":8,"pop":70},{"d":"Sun","code":0,"max":21,"min":7,"pop":10}],
 "lat":37.57,"lon":126.98}
```

- HA: 기온·날씨 상태 센서 (HA 쪽 날씨 통합을 따로 써도 된다).
- 인터넷이 없으면 다음 주기에 다시 시도하고, 마지막 값은 retain으로 남긴다. `t`가 3시간 넘게 지나면 디스플레이가 시각을 함께 보인다.

### 4.4 디스플레이 — 표시

> **결정 (2026-10-16): 화면 3(실내)에 현재 위치 지명 한 줄 + 3일 예보.** [docs/28](https://github.com/nam6411/rv-controller/blob/master/docs/28-display-ui.md) §5·§6.
> 디스플레이(ESP) 펌웨어가 `rv/weather`의 `days[3]`·`place`를 읽는다. 받을 곳만 바뀌었고 화면 쪽 일은 그대로다.

### 4.5 설정 (Radxa `rvnode` YAML)

| 키 | 기본 | 용도 |
|---|---|---|
| `gps.enabled` | true | gpsd에서 위치를 받아 발행 |
| `gps.gpsd` | `127.0.0.1:2947` | gpsd 주소 |
| `compass.enabled` | true | QMC5883L 방위 |
| `compass.bus` / `compass.addr` | 5 / 0x0D | I2C |
| `compass.cal` | §7.3 값 | 중심·반지름 (보정 명령이 고쳐 쓴다) |
| `compass.declination` | −8.5 | 편각(°, 서편 −) |
| `weather.enabled` | true | 날씨·지명을 받는다 |
| `weather.lat` / `weather.lon` | 비어 있음 | GPS 위치가 없을 때 쓸 좌표 |
| `weather.interval_min` | 30 | 갱신 주기 |

## 5. 구현 순서

1. Radxa 보드 준비 (docs/29 §5 1단계) — 오버레이 UART4-M1·I2C5-M0, `dmesg`로 tty 이름, `i2cdetect -y 5`에 0x0D
2. `gpsd` + `chrony` — `cgps`로 위치, `chronyc sources`에 GPS 시계
3. `rvnode` GPS·나침반 발행, HA `device_tracker`·센서
4. 날씨·지명 → `rv/weather`
5. 디스플레이 화면 3 표시 (docs/28 §7)
6. 설치 자리에서 나침반 보정, 4방향 확인 (§7.3에서 못 한 것)

## 6. 정할 것

- ~~화면 형태~~ → docs/28 화면 3
- ~~커넥터 VCC 전압·핀 순서~~ → 5V, `C V R T G D` (§7.1)
- ~~GPS를 디스플레이 노드에 달지 Radxa에 달지~~ → Radxa (2026-10-20)
- Radxa 노드의 토픽 접두어 `<radxa>` — MACH·JBD·TPMS는 ESP의 `9FCEE4`·`rv/mach` 등을 이어받지만(docs/29 §3.1) GPS는 새 기능이라 자유. 예: `rv/radxa`
- 실내 첫 위치를 빠르게: AssistNow Autonomous(칩 설정만) 또는 AssistNow Online(Radxa가 받아 넣음, u-blox 토큰 필요) (§7.2)

## 7. 작업대 실측 (2026-10-20·21)

도구: [tools/gps-test-8266](https://github.com/nam6411/rv-controller/tree/master/tools/gps-test-8266) — NodeMCU(ESP-12F)에서 NMEA를 받아 그대로 넘기고 1초마다 요약
(`# … | fix | used / in view | hdop | 위도, 경도 alt`), 나침반은 0.5초마다 `# mag … | heading …`. 시리얼 `r` = NMEA 원문 끄기/켜기, `c` = 나침반 보정 지우기.

### 7.1 배선·통신
| BE-880 | NodeMCU | 비고 |
|---|---|---|
| V | VIN (5V) | |
| G | GND | |
| T | D1 (GPIO5) | 소프트웨어 시리얼 수신 |
| R | D4 (GPIO2) | 보레이트 변경용 |
| D · C | D2 · D5 | 나침반 I2C. 이 작업대에서는 **SDA·SCL이 반대로 동작**했다(D5 = SDA) — 펌웨어가 두 방향을 다 스캔한다 |

- 기본 115200bps. ESP8266 소프트웨어 시리얼은 115200에서 글자를 놓쳐 GGA가 잘렸다 → UBX-CFG-VALSET으로 RAM에만 38400으로 낮췄다
  (`B5 62 06 8A 0C 00 00 01 00 00 01 00 52 40 <보레이트 4바이트 LE> CK_A CK_B`). 전원을 끄면 115200으로 돌아간다. 하드웨어 UART면 그대로 115200을 쓴다.
- 1초에 문장 12개: `GNRMC·GNVTG·GNGGA·GNGSA(×4)·GPGSV·GAGSV·GBGSV·GQGSV·GNGLL`. 체크섬 오류 0 (38400).
- 위성 조합 기본값: **GPS·Galileo·BeiDou·QZSS** (GSA 시스템 ID 1·3·4·5). GLONASS(`GL`)는 꺼져 있다.

### 7.2 위치
- 실내 창가(북동쪽 창), 처음 켠 콜드 스타트: 시각은 1분 안에 잡히고(`utc`), **위치는 약 5분**.
- 위성이 보여도 한쪽(북동~동, 방위 44~91°)에 몰리면 PDOP 33 — u-blox 기본 PDOP 한도(25)를 넘어 위치를 내지 않는다(`GSA` 모드 1).
  북서쪽 높은 BeiDou 하나가 더해지자 PDOP 5.6·HDOP 3.7, 3D 위치(6개). **머리 위가 트인 자리가 창 한쪽보다 낫다.**
- 위치를 잡은 뒤 NodeMCU만 리셋하면(GPS 전원 유지) 몇 초 만에 다시 잡는다. 위성 6~7개, HDOP 3.4~3.8 → 수평 오차 대략 10~20m.
- 좌표 역지오코딩(OSM Nominatim)이 실제 동(화성시 방교동)과 맞았다.
- 신호가 30dB-Hz 이상인 위성이 여럿이라 모듈·안테나는 정상. 금속·콘크리트는 막고 유리·플라스틱·FRP는 통과한다 — 캠핑카에서는 천창 아래가 좋다.
- 실내 첫 위치를 빠르게 하려면: AssistNow Autonomous(칩이 궤도 예측, 인터넷 없음) 또는 AssistNow Online(Radxa가 궤도 데이터를 받아 넣음). 미적용.

### 7.3 나침반 (QMC5883L, I2C 0x0D)
- 설정: 연속, 200Hz, ±8G(3000 LSB/G), OSR 512. 지구 자기장 세기는 1000~1200 LSB로 읽혔다.
- **바닥에 바로 두면 철근 등으로 크게 틀어진다** — 세기 2330(지구 자기장의 1.5배), 눕혔는데 Z가 0. 나무·플라스틱 위로 30cm 띄우니
  Z가 가장 크고(−760~−1020, 아래쪽) 돌려도 거의 일정, X·Y 범위가 같아졌다(754·777).
- 보정(하드 아이언): 중심 (351, −284), 반지름 377·388 → 펌웨어 시작값. X·Y를 각자 반지름으로 나눠 원으로 맞춘다(간단한 소프트 아이언).
- **칩 축이 모듈 화살표와 90° 돌아가 있고 회전 방향도 반대**: 화살표 북 → 칩 각도 265°, 화살표 동 → 181°.
  화살표 기준 방위 = **265° − 칩 각도** (자북 기준, 한국 편각 ≈ 서편 8~9° — 진북 = 자북 방위 − 8.5°).
- 정확도: 북에 맞춘 식으로 같은 자리의 동쪽이 84°(정답 90°). 펌웨어를 다시 올린 뒤에는 68°로 읽혀(모듈이 그대로 동쪽이었다면) 약 20° 차이 —
  주변 물건·선 위치에 10~20°는 흔들린다. **4방향 확인은 하지 않았다.**
- 설치 자리에서는 반드시 다시 보정한다(차체 철물로 중심이 바뀐다). 기울기 보정이 없으므로 차가 기울면 방위가 틀어진다.

## 출처

- Elecrow CrowPanel 2.1 wiki: https://elecrow.com/wiki/CrowPanel_2.1inch-HMI_ESP32_Rotary_Display_480_IPS_Round_Touch_Knob_Screen.html
- CrowPanel Rotary 2.1 핀: https://www.espboards.dev/esp32/elecrow-crowpanel-rotary-2-1/
- Beitian BE-122 (BE 시리즈 공통 사양): https://www.beitian.com/en/sys-pd/524.html
- BE-880 사양 이미지: https://ae-pic-a1.aliexpress-media.com/kf/Sc28785fc4426466788ca11716aab0791a.jpg
- Open-Meteo: https://open-meteo.com/
