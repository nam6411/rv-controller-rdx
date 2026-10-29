# 31. MQTT 엔티티 전체 목록

> **2026-10-29 기준, 생성한 문서** — `.venv/bin/python tools/mqtt_inventory.py > docs/31-mqtt-entities.md`로 다시 만든다. 손으로 고치지 않는다.
> 출처: ls 브로커(HA가 읽는 곳)에 보관된 HA discovery. HA에 올리지 않는 Radxa 장치(discovery: false)는 보드 설정으로
> rvnode 코드가 낼 엔티티를 붙였다. 토픽 규칙의 근거는 rv-controller docs/07 §B.

HA 엔티티 **165개**, HA 미등록 Radxa 장치까지 **221개**.

## 토픽 규칙

| 토픽 | 방향 | 내용 |
|---|---|---|
| `<base>/status` | 노드 → | 노드 전체 `online` / `offline` (LWT, retain) |
| `<prefix>/state` | 노드 → | 측정값 JSON 한 덩어리 (retain). **센서는 따로 토픽이 없고 이 JSON의 경로를 읽는다** |
| `<prefix>/availability` | 노드 → | 장치별 `online` / `offline` (3회 연속 실패 시 offline) |
| `<prefix>/meta` | 노드 → | 디스플레이용 종류·이름·스위치 목록·대표 센서 |
| `<prefix>/switch/<이름>/state` | 노드 → | `ON` / `OFF` (retain) |
| `<prefix>/switch/<이름>/set` | → 노드 | `ON` 켜기, 그 밖은 끄기. 보관하지 않는다 |
| `<prefix>/control/<이름>/set` | → 노드 | number = 숫자, select = 선택지, button = `PRESS`, text. 현재 값은 state JSON |
| `<prefix>/event/<이름>` | 노드 → | `{"event_type": …}` 순간 이벤트. 보관하지 않는다 |
| `<base>/sys/cmd` | → 노드 | `restart` / `rediscover` |
| `homeassistant/<종류>/<장치ID>_<slug>/<이름>/config` | 노드 → | HA discovery. **HA ID(uniq_id) = `<장치ID>_<slug>_<이름>`** |

## 노드

| 노드 | base | 상태 | 하드웨어 | 장치 ID | State | Switch | Control |
|---|---|---|---|---|---|---|---|
| [인버터 노드](#rvupower) | `rv/upower` | online | ESP32-S3 · UPower UP5000 (RS485) | A0CA48 | 48 | 4 | 0 |
| [스위치 노드](#rvrelay) | `rv/relay` | online | ESP32 릴레이 4CH | 8D2724 | 0 | 4 | 0 |
| [IR 노드](#rvremote) | `rv/remote` | online | ESP32-S3 · MaxxFan 유선 IR | A15134 | 2 | 1 | 9 |
| [MACH 노드 (ESP)](#rvmach) | `rv/mach` | online | ESP32-S3 · MACH·JBD·TPMS (BLE) — Radxa로 옮길 예정 (docs/29) | 9FCEE4 | 61 | 8 | 6 |
| [Radxa 노드](#rvradxa) | `rv/radxa` | online | Radxa ZERO 3W · rvnode (이 저장소) | 9FCEE4 | 59 | 9 | 9 |
| [ESP 브로커](#rvbroker) | `rv/broker` | online | ESP32-S3 · 노드들의 브로커 + ls 브리지 | — | 0 | 0 | 0 |

엔티티가 없는 노드(status만): `rv/bms` (offline), `rv/display-71CF1C` (online), `rv/lin` (online), `rv/node-A15134` (online)

## 1. 인버터 노드 — `rv/upower` <a id="rvupower"></a>

ESP32-S3 · UPower UP5000 (RS485). 노드 상태 `rv/upower/status` = online.

### `rv/upower` — Inverter

availability = online.

**State** — `rv/upower/state` JSON

| HA ID | 이름 | 종류 | 경로 | 단위 |
|---|---|---|---|---|
| `A0CA48_upower_alarm_count` | Alarm Count | 센서 | `alarm_count` |  |
| `A0CA48_upower_alarms` | Alarms | 센서 | `alarms` |  |
| `A0CA48_upower_bat_soc` | Battery SOC | 센서 | `bat.soc` | % |
| `A0CA48_upower_bat_state` | Battery State | 센서 | `bat.state` |  |
| `A0CA48_upower_bat_status` | Battery Status | 센서 | `bat.status_text` |  |
| `A0CA48_upower_bat_temp` | Battery Temperature | 센서 | `bat.temp` | °C |
| `A0CA48_upower_bat_v` | Battery Voltage | 센서 | `bat.v` | V |
| `A0CA48_upower_bypass_a` | Bypass Current | 센서 | `bypass.a` | A |
| `A0CA48_upower_bypass_v` | Bypass Voltage | 센서 | `bypass.v` | V |
| `A0CA48_upower_bypass_w` | Bypass Power | 센서 | `bypass.w` | W |
| `A0CA48_upower_grid_chg_a` | Grid Charge Current | 센서 | `grid.chg_a` | A |
| `A0CA48_upower_grid_chg_v` | Grid Charge Voltage | 센서 | `grid.chg_v` | V |
| `A0CA48_upower_grid_chg_w` | Grid Charge Power | 센서 | `grid.chg_w` | W |
| `A0CA48_upower_grid_in_a` | Grid In Current | 센서 | `grid.in_a` | A |
| `A0CA48_upower_grid_in_v` | Grid In Voltage | 센서 | `grid.in_v` | V |
| `A0CA48_upower_grid_in_w` | Grid In Power | 센서 | `grid.in_w` | W |
| `A0CA48_upower_grid_kwh` | Grid Energy | 센서 | `grid.kwh` | kWh |
| `A0CA48_upower_grid_status` | Grid Charger Status | 센서 | `grid.status_text` |  |
| `A0CA48_upower_grid_temp` | Grid Temperature | 센서 | `grid.temp` | °C |
| `A0CA48_upower_inv_hz` | Inverter Frequency | 센서 | `inv.hz` | Hz |
| `A0CA48_upower_inv_in_v` | Inverter In Voltage | 센서 | `inv.in_v` | V |
| `A0CA48_upower_inv_out_a` | Inverter Out Current | 센서 | `inv.out_a` | A |
| `A0CA48_upower_inv_out_v` | Inverter Out Voltage | 센서 | `inv.out_v` | V |
| `A0CA48_upower_inv_out_w` | Inverter Out Power | 센서 | `inv.out_w` | W |
| `A0CA48_upower_inv_status` | Inverter Status | 센서 | `inv.status_text` |  |
| `A0CA48_upower_pv_chg_a` | PV Charge Current | 센서 | `pv.chg_a` | A |
| `A0CA48_upower_pv_chg_v` | PV Charge Voltage | 센서 | `pv.chg_v` | V |
| `A0CA48_upower_pv_chg_w` | PV Charge Power | 센서 | `pv.chg_w` | W |
| `A0CA48_upower_pv_in_a` | PV In Current | 센서 | `pv.in_a` | A |
| `A0CA48_upower_pv_in_v` | PV In Voltage | 센서 | `pv.in_v` | V |
| `A0CA48_upower_pv_in_w` | PV In Power | 센서 | `pv.in_w` | W |
| `A0CA48_upower_pv_kwh` | PV Energy | 센서 | `pv.kwh` | kWh |
| `A0CA48_upower_pv_state` | PV Charge State | 센서 | `pv.state` |  |
| `A0CA48_upower_pv_status` | PV Charger Status | 센서 | `pv.status_text` |  |
| `A0CA48_upower_pv_temp` | PV Temperature | 센서 | `pv.temp` | °C |
| `A0CA48_upower_set_aux_resume_v` | Aux Charge Resume Voltage | 센서 | `set.aux_resume_v` | V |
| `A0CA48_upower_set_aux_stop_v` | Aux Charge Stop Voltage | 센서 | `set.aux_stop_v` | V |
| `A0CA48_upower_set_battery_type` | Battery Type | 센서 | `set.battery_type` |  |
| `A0CA48_upower_set_boost_reconnect_v` | Boost Reconnect Voltage | 센서 | `set.boost_reconnect_v` | V |
| `A0CA48_upower_set_boost_v` | Boost Charging Voltage | 센서 | `set.boost_v` | V |
| `A0CA48_upower_set_capacity_ah` | Battery Capacity | 센서 | `set.capacity_ah` | Ah |
| `A0CA48_upower_set_charge_mode` | Charging Mode | 센서 | `set.charge_mode` |  |
| `A0CA48_upower_set_dry_off_v` | Dry Contact Off Voltage | 센서 | `set.dry_off_v` | V |
| `A0CA48_upower_set_dry_on_v` | Dry Contact On Voltage | 센서 | `set.dry_on_v` | V |
| `A0CA48_upower_set_float_v` | Float Charging Voltage | 센서 | `set.float_v` | V |
| `A0CA48_upower_set_inv_lvd_v` | Inverter Low Voltage Disconnect | 센서 | `set.inv_lvd_v` | V |
| `A0CA48_upower_set_inv_lvr_v` | Inverter Low Voltage Reconnect | 센서 | `set.inv_lvr_v` | V |
| `A0CA48_upower_set_total_charge_a` | Total Charge Current | 센서 | `set.total_charge_a` | A |

**Switch** — `ON` / `OFF`

| HA ID | 이름 | state 토픽 | set 토픽 |
|---|---|---|---|
| `A0CA48_upower_grid_charge` | Grid Charge | `rv/upower/switch/grid_charge/state` | `rv/upower/switch/grid_charge/set` |
| `A0CA48_upower_gridout_prio` | Grid Output Priority | `rv/upower/switch/gridout_prio/state` | `rv/upower/switch/gridout_prio/set` |
| `A0CA48_upower_inverter` | Inverter | `rv/upower/switch/inverter/state` | `rv/upower/switch/inverter/set` |
| `A0CA48_upower_solar_charge` | Solar Charge | `rv/upower/switch/solar_charge/state` | `rv/upower/switch/solar_charge/set` |

## 2. 스위치 노드 — `rv/relay` <a id="rvrelay"></a>

ESP32 릴레이 4CH. 노드 상태 `rv/relay/status` = online.

### `rv/relay` — Switch

availability = online.

**Switch** — `ON` / `OFF`

| HA ID | 이름 | state 토픽 | set 토픽 |
|---|---|---|---|
| `8D2724_relay_ch1` | Mover | `rv/relay/switch/ch1/state` | `rv/relay/switch/ch1/set` |
| `8D2724_relay_ch2` | Drain | `rv/relay/switch/ch2/state` | `rv/relay/switch/ch2/set` |
| `8D2724_relay_ch3` | Drain-Plumbing | `rv/relay/switch/ch3/state` | `rv/relay/switch/ch3/set` |
| `8D2724_relay_ch4` | Fill | `rv/relay/switch/ch4/state` | `rv/relay/switch/ch4/set` |

## 3. IR 노드 — `rv/remote` <a id="rvremote"></a>

ESP32-S3 · MaxxFan 유선 IR. 노드 상태 `rv/remote/status` = online.

### `rv/remote` — Remote

availability = online.

**State** — `rv/remote/state` JSON

| HA ID | 이름 | 종류 | 경로 | 단위 |
|---|---|---|---|---|
| `A15134_remote_fan_auto_room` | Fan Auto Room Temperature | 센서 | `maxxfan.auto.room` | °C |
| `A15134_remote_last_ir` | Last IR Code | 센서 | `rx.ir[:250]` |  |

**Switch** — `ON` / `OFF`

| HA ID | 이름 | state 토픽 | set 토픽 |
|---|---|---|---|
| `A15134_remote_maxxfan` | MaxxFan | `rv/remote/switch/maxxfan/state` | `rv/remote/switch/maxxfan/set` |

**Control** — number = 숫자, select = 선택지, button = `PRESS`, text = 23자 이내

| HA ID | 이름 | 종류 | set 토픽 | 현재 값 (state 경로) | 범위 · 선택지 |
|---|---|---|---|---|---|
| `A15134_remote_fan_auto` | Fan Auto | select | `rv/remote/control/fan_auto/set` | `maxxfan.auto.mode` | off · on |
| `A15134_remote_fan_auto_temp` | Fan Auto Temp | number | `rv/remote/control/fan_auto_temp/set` | `maxxfan.auto.set` | 15 ~ 35, 0.5씩 °C |
| `A15134_remote_fan_dir` | Fan Direction | select | `rv/remote/control/fan_dir/set` | `maxxfan.dir` | exhaust · intake |
| `A15134_remote_fan_flow` | Fan Flow | number | `rv/remote/control/fan_flow/set` | `maxxfan.flow` | -100 ~ 100, 10씩 % |
| `A15134_remote_fan_lid` | Fan Lid | select | `rv/remote/control/fan_lid/set` | `maxxfan.lid` | open · closed |
| `A15134_remote_fan_resend` | Fan Resend | button | `rv/remote/control/fan_resend/set` | — |  |
| `A15134_remote_fan_speed` | Fan Speed | number | `rv/remote/control/fan_speed/set` | `maxxfan.speed` | 10 ~ 100, 10씩 % |
| `A15134_remote_replay_ir` | Replay Last IR | button | `rv/remote/control/replay_ir/set` | — |  |
| `A15134_remote_send` | Send Code | text | `rv/remote/control/send/set` | — |  |

**Event** — 보관하지 않는다

| HA ID | 이름 | 토픽 | 이벤트 |
|---|---|---|---|
| `A15134_remote_maxxfan_remote` | MaxxFan Remote | `rv/remote/event/maxxfan_remote` | changed · same |

## 4. MACH 노드 (ESP) — `rv/mach` <a id="rvmach"></a>

ESP32-S3 · MACH·JBD·TPMS (BLE) — Radxa로 옮길 예정 (docs/29). 노드 상태 `rv/mach/status` = online.

### `rv/mach` — MACH

availability = online.

**State** — `rv/mach/state` JSON

| HA ID | 이름 | 종류 | 경로 | 단위 |
|---|---|---|---|---|
| `9FCEE4_mach_ac_room` | AC Room Temperature | 센서 | `ac.room_c` | °C |
| `9FCEE4_mach_level_pitch` | Level Front-Back | 센서 | `accel.pitch` | ° |
| `9FCEE4_mach_level_pitch_text` | Level Front-Back (low side) | 센서 | `accel.pitch_text` |  |
| `9FCEE4_mach_level_roll` | Level Left-Right | 센서 | `accel.roll` | ° |
| `9FCEE4_mach_level_roll_text` | Level Left-Right (low side) | 센서 | `accel.roll_text` |  |
| `9FCEE4_mach_alde_electric` | Alde Electric Level | 센서 | `alde.electric` |  |
| `9FCEE4_mach_alde_error` | Alde Error | 센서 | `alde.error` |  |
| `9FCEE4_mach_alde_heating` | Alde Heating | 센서 | `alde.heating` |  |
| `9FCEE4_mach_alde_outside` | Alde Outside Temperature | 센서 | `alde.outside` | °C |
| `9FCEE4_mach_alde_set` | Alde Set Temperature | 센서 | `alde.set` | °C |
| `9FCEE4_mach_alde_sofa` | Alde Room Temperature | 센서 | `alde.sofa` | °C |
| `9FCEE4_mach_alde_water` | Alde Hot Water | 센서 | `alde.water_text` |  |
| `9FCEE4_mach_ebl_bat_a` | Living Battery Current | 센서 | `ebl.bat_a` | A |
| `9FCEE4_mach_ebl_bat_v` | Living Battery Voltage | 센서 | `ebl.bat_v` | V |
| `9FCEE4_mach_ebl_bat_w` | Living Battery Power | 센서 | `ebl.bat_w` | W |
| `9FCEE4_mach_ebl_engine` | Engine Running | 센서 | `ebl.engine` |  |
| `9FCEE4_mach_ebl_fresh` | Fresh Water | 센서 | `ebl.fresh` | % |
| `9FCEE4_mach_ebl_inside` | Inside Temperature (EBL) | 센서 | `ebl.inside` | °C |
| `9FCEE4_mach_ebl_mains` | Mains (230V) | 센서 | `ebl.mains` |  |
| `9FCEE4_mach_ebl_outside` | Outside Temperature (EBL) | 센서 | `ebl.outside` | °C |
| `9FCEE4_mach_ebl_soc` | Living Battery SOC (voltage) | 센서 | `ebl.soc` | % |
| `9FCEE4_mach_ebl_waste` | Waste Water | 센서 | `ebl.waste` | % |

**Switch** — `ON` / `OFF`

| HA ID | 이름 | state 토픽 | set 토픽 |
|---|---|---|---|
| `9FCEE4_mach_ac` | Air Conditioner | `rv/mach/switch/ac/state` | `rv/mach/switch/ac/set` |
| `9FCEE4_mach_ext_light` | Exterior Light | `rv/mach/switch/ext_light/state` | `rv/mach/switch/ext_light/set` |
| `9FCEE4_mach_heater` | Heater (Gas) | `rv/mach/switch/heater/state` | `rv/mach/switch/heater/set` |
| `9FCEE4_mach_int_light` | Interior Light | `rv/mach/switch/int_light/state` | `rv/mach/switch/int_light/set` |
| `9FCEE4_mach_panel` | Main Panel | `rv/mach/switch/panel/state` | `rv/mach/switch/panel/set` |
| `9FCEE4_mach_water_pump` | Water Pump | `rv/mach/switch/water_pump/state` | `rv/mach/switch/water_pump/set` |

**Control** — number = 숫자, select = 선택지, button = `PRESS`, text = 23자 이내

| HA ID | 이름 | 종류 | set 토픽 | 현재 값 (state 경로) | 범위 · 선택지 |
|---|---|---|---|---|---|
| `9FCEE4_mach_ac_fan` | AC Fan | select | `rv/mach/control/ac_fan/set` | `ac.fan` | low · mid · high · night |
| `9FCEE4_mach_ac_mode` | AC Mode | select | `rv/mach/control/ac_mode/set` | `ac.mode` | off · fan · cool · heat · auto |
| `9FCEE4_mach_ac_set_temp` | AC Set Temperature | number | `rv/mach/control/ac_set_temp/set` | `ac.set_c` | 16 ~ 31, 1씩 °C |
| `9FCEE4_mach_alde_energy` | Alde Energy Source | select | `rv/mach/control/alde_energy/set` | `alde.energy` | off · gas · el1 · el2 · el3 · el1_and_gas · el2_and_gas · el3_and_gas |
| `9FCEE4_mach_alde_set_temp` | Alde Set Temperature | number | `rv/mach/control/alde_set_temp/set` | `alde.set` | 5 ~ 30, 1씩 °C |
| `9FCEE4_mach_alde_water` | Alde Hot Water | select | `rv/mach/control/alde_water/set` | `alde.water_text` | off · normal · boost |

### `rv/mach/bms` — BMS

availability = online.

**State** — `rv/mach/bms/state` JSON

| HA ID | 이름 | 종류 | 경로 | 단위 |
|---|---|---|---|---|
| `9FCEE4_bms_balance` | Balance Bits | 센서 | `balance` |  |
| `9FCEE4_bms_cell_diff` | Cell Voltage Diff | 센서 | `cell_diff` | V |
| `9FCEE4_bms_cell_1` | Cell 1 | 센서 | `cell_v[0]` | V |
| `9FCEE4_bms_cell_11` | Cell 11 | 센서 | `cell_v[10]` | V |
| `9FCEE4_bms_cell_12` | Cell 12 | 센서 | `cell_v[11]` | V |
| `9FCEE4_bms_cell_13` | Cell 13 | 센서 | `cell_v[12]` | V |
| `9FCEE4_bms_cell_14` | Cell 14 | 센서 | `cell_v[13]` | V |
| `9FCEE4_bms_cell_15` | Cell 15 | 센서 | `cell_v[14]` | V |
| `9FCEE4_bms_cell_16` | Cell 16 | 센서 | `cell_v[15]` | V |
| `9FCEE4_bms_cell_2` | Cell 2 | 센서 | `cell_v[1]` | V |
| `9FCEE4_bms_cell_3` | Cell 3 | 센서 | `cell_v[2]` | V |
| `9FCEE4_bms_cell_4` | Cell 4 | 센서 | `cell_v[3]` | V |
| `9FCEE4_bms_cell_5` | Cell 5 | 센서 | `cell_v[4]` | V |
| `9FCEE4_bms_cell_6` | Cell 6 | 센서 | `cell_v[5]` | V |
| `9FCEE4_bms_cell_7` | Cell 7 | 센서 | `cell_v[6]` | V |
| `9FCEE4_bms_cell_8` | Cell 8 | 센서 | `cell_v[7]` | V |
| `9FCEE4_bms_cell_9` | Cell 9 | 센서 | `cell_v[8]` | V |
| `9FCEE4_bms_cell_10` | Cell 10 | 센서 | `cell_v[9]` | V |
| `9FCEE4_bms_current` | Current | 센서 | `current` | A |
| `9FCEE4_bms_cycles` | Cycles | 센서 | `cycles` |  |
| `9FCEE4_bms_full_ah` | Full Capacity | 센서 | `full_ah` | Ah |
| `9FCEE4_bms_ntc_1` | NTC 1 | 센서 | `ntc[0]` | °C |
| `9FCEE4_bms_ntc_2` | NTC 2 | 센서 | `ntc[1]` | °C |
| `9FCEE4_bms_ntc_3` | NTC 3 | 센서 | `ntc[2]` | °C |
| `9FCEE4_bms_ntc_4` | NTC 4 | 센서 | `ntc[3]` | °C |
| `9FCEE4_bms_pack_v` | Pack Voltage | 센서 | `pack_v` | V |
| `9FCEE4_bms_power` | Power | 센서 | `power` | W |
| `9FCEE4_bms_protection` | Protection Bits | 센서 | `protection` |  |
| `9FCEE4_bms_remain_ah` | Remaining Capacity | 센서 | `remain_ah` | Ah |
| `9FCEE4_bms_soc` | SOC | 센서 | `soc` | % |

**Switch** — `ON` / `OFF`

| HA ID | 이름 | state 토픽 | set 토픽 |
|---|---|---|---|
| `9FCEE4_bms_charge_fet` | Charge MOSFET | `rv/mach/bms/switch/charge_fet/state` | `rv/mach/bms/switch/charge_fet/set` |
| `9FCEE4_bms_discharge_fet` | Discharge MOSFET | `rv/mach/bms/switch/discharge_fet/state` | `rv/mach/bms/switch/discharge_fet/set` |

### `rv/mach/tpms` — TPMS

availability = online.

**State** — `rv/mach/tpms/state` JSON

| HA ID | 이름 | 종류 | 경로 | 단위 |
|---|---|---|---|---|
| `9FCEE4_tpms_caravan_left_battery` | Caravan Left Tyre Battery | 센서 | `caravan_left.battery` | % |
| `9FCEE4_tpms_caravan_left_pressure` | Caravan Left Tyre Pressure | 센서 | `caravan_left.pressure` | bar |
| `9FCEE4_tpms_caravan_left_temp_c` | Caravan Left Tyre Temperature | 센서 | `caravan_left.temp_c` | °C |
| `9FCEE4_tpms_caravan_right_battery` | Caravan Right Tyre Battery | 센서 | `caravan_right.battery` | % |
| `9FCEE4_tpms_caravan_right_pressure` | Caravan Right Tyre Pressure | 센서 | `caravan_right.pressure` | bar |
| `9FCEE4_tpms_caravan_right_temp_c` | Caravan Right Tyre Temperature | 센서 | `caravan_right.temp_c` | °C |
| `9FCEE4_tpms_tow_air_battery` | Tow Air Suspension Battery | 센서 | `tow_air.battery` | % |
| `9FCEE4_tpms_tow_air_pressure` | Tow Air Suspension Pressure | 센서 | `tow_air.pressure` | psi |
| `9FCEE4_tpms_tow_air_temp_c` | Tow Air Suspension Temperature | 센서 | `tow_air.temp_c` | °C |

## 5. Radxa 노드 — `rv/radxa` <a id="rvradxa"></a>

Radxa ZERO 3W · rvnode (이 저장소). 노드 상태 `rv/radxa/status` = online.

엔티티가 아닌 토픽: `rv/radxa/gps/location`

### `rv/radxa/birdview` — Bird view

availability = online.

**State** — `rv/radxa/birdview/state` JSON

| HA ID | 이름 | 종류 | 경로 | 단위 |
|---|---|---|---|---|
| `9FCEE4_birdview_view` | View | 센서 | `view` |  |
| `9FCEE4_birdview_ignition` | Car ignition | 입력 | `in.ign` |  |
| `9FCEE4_birdview_turn_left` | Turn left | 입력 | `in.left` |  |
| `9FCEE4_birdview_reverse` | Reverse | 입력 | `in.reverse` |  |
| `9FCEE4_birdview_turn_right` | Turn right | 입력 | `in.right` |  |
| `9FCEE4_birdview_powered` | Power | 입력 | `powered` |  |

**Switch** — `ON` / `OFF`

| HA ID | 이름 | state 토픽 | set 토픽 |
|---|---|---|---|
| `9FCEE4_birdview_power` | Keep on | `rv/radxa/birdview/switch/power/state` | `rv/radxa/birdview/switch/power/set` |

**Control** — number = 숫자, select = 선택지, button = `PRESS`, text = 23자 이내

| HA ID | 이름 | 종류 | set 토픽 | 현재 값 (state 경로) | 범위 · 선택지 |
|---|---|---|---|---|---|
| `9FCEE4_birdview_mode` | View mode | select | `rv/radxa/birdview/control/mode/set` | `mode` | auto · rear · left · right · none |

### `rv/radxa/bmstest` — BMS (test) (HA 미등록)

availability = online. discovery: false — HA에 올리지 않는다. 켜면 아래 ID로 올라간다.

**State** — `rv/radxa/bmstest/state` JSON

| HA ID | 이름 | 종류 | 경로 | 단위 |
|---|---|---|---|---|
| `9FCEE4_bmstest_balance` | Balance Bits | 센서 | `balance` |  |
| `9FCEE4_bmstest_cell_diff` | Cell Voltage Diff | 센서 | `cell_diff` | V |
| `9FCEE4_bmstest_current` | Current | 센서 | `current` | A |
| `9FCEE4_bmstest_cycles` | Cycles | 센서 | `cycles` |  |
| `9FCEE4_bmstest_full_ah` | Full Capacity | 센서 | `full_ah` | Ah |
| `9FCEE4_bmstest_pack_v` | Pack Voltage | 센서 | `pack_v` | V |
| `9FCEE4_bmstest_power` | Power | 센서 | `power` | W |
| `9FCEE4_bmstest_protection` | Protection Bits | 센서 | `protection` |  |
| `9FCEE4_bmstest_remain_ah` | Remaining Capacity | 센서 | `remain_ah` | Ah |
| `9FCEE4_bmstest_soc` | SOC | 센서 | `soc` | % |

**Switch** — `ON` / `OFF`

| HA ID | 이름 | state 토픽 | set 토픽 |
|---|---|---|---|
| `9FCEE4_bmstest_charge_fet` | Charge MOSFET | `rv/radxa/bmstest/switch/charge_fet/state` | `rv/radxa/bmstest/switch/charge_fet/set` |
| `9FCEE4_bmstest_discharge_fet` | Discharge MOSFET | `rv/radxa/bmstest/switch/discharge_fet/state` | `rv/radxa/bmstest/switch/discharge_fet/set` |

### `rv/radxa/gps` — GPS

availability = online.

**State** — `rv/radxa/gps/state` JSON

| HA ID | 이름 | 종류 | 경로 | 단위 |
|---|---|---|---|---|
| `9FCEE4_gps_address` | Address | 센서 | `address` |  |
| `9FCEE4_gps_alt_m` | Altitude | 센서 | `alt_m` | m |
| `9FCEE4_gps_course` | Course | 센서 | `course` | ° |
| `9FCEE4_gps_hdop` | HDOP | 센서 | `hdop` |  |
| `9FCEE4_gps_heading` | Heading | 센서 | `heading` | ° |
| `9FCEE4_gps_lat` | Latitude | 센서 | `lat` | ° |
| `9FCEE4_gps_lon` | Longitude | 센서 | `lon` | ° |
| `9FCEE4_gps_sats` | Satellites | 센서 | `sats` |  |
| `9FCEE4_gps_speed_kmh` | Speed | 센서 | `speed_kmh` | km/h |
| `9FCEE4_gps_fix` | GPS fix | 입력 | `fix` |  |
| `9FCEE4_gps_location` | Location | 위치 | `rv/radxa/gps/location` (속성) |  |

**Control** — number = 숫자, select = 선택지, button = `PRESS`, text = 23자 이내

| HA ID | 이름 | 종류 | set 토픽 | 현재 값 (state 경로) | 범위 · 선택지 |
|---|---|---|---|---|---|
| `9FCEE4_gps_compass_cal_start` | Compass calibration start | button | `rv/radxa/gps/control/compass_cal_start/set` | — |  |
| `9FCEE4_gps_compass_cal_stop` | Compass calibration stop | button | `rv/radxa/gps/control/compass_cal_stop/set` | — |  |

### `rv/radxa/machtest` — MACH (test) (HA 미등록)

availability = offline. discovery: false — HA에 올리지 않는다. 켜면 아래 ID로 올라간다.

**State** — `rv/radxa/machtest/state` JSON

| HA ID | 이름 | 종류 | 경로 | 단위 |
|---|---|---|---|---|
| `9FCEE4_machtest_ac_room` | AC Room Temperature | 센서 | `ac.room_c` | °C |
| `9FCEE4_machtest_level_pitch` | Level Front-Back | 센서 | `accel.pitch` | ° |
| `9FCEE4_machtest_level_pitch_text` | Level Front-Back (low side) | 센서 | `accel.pitch_text` |  |
| `9FCEE4_machtest_level_roll` | Level Left-Right | 센서 | `accel.roll` | ° |
| `9FCEE4_machtest_level_roll_text` | Level Left-Right (low side) | 센서 | `accel.roll_text` |  |
| `9FCEE4_machtest_alde_electric` | Alde Electric Level | 센서 | `alde.electric` |  |
| `9FCEE4_machtest_alde_error` | Alde Error | 센서 | `alde.error` |  |
| `9FCEE4_machtest_alde_heating` | Alde Heating | 센서 | `alde.heating` |  |
| `9FCEE4_machtest_alde_outside` | Alde Outside Temperature | 센서 | `alde.outside` | °C |
| `9FCEE4_machtest_alde_set` | Alde Set Temperature | 센서 | `alde.set` | °C |
| `9FCEE4_machtest_alde_sofa` | Alde Room Temperature | 센서 | `alde.sofa` | °C |
| `9FCEE4_machtest_alde_water` | Alde Hot Water | 센서 | `alde.water_text` |  |
| `9FCEE4_machtest_ebl_bat_a` | Living Battery Current | 센서 | `ebl.bat_a` | A |
| `9FCEE4_machtest_ebl_bat_v` | Living Battery Voltage | 센서 | `ebl.bat_v` | V |
| `9FCEE4_machtest_ebl_bat_w` | Living Battery Power | 센서 | `ebl.bat_w` | W |
| `9FCEE4_machtest_ebl_engine` | Engine Running | 센서 | `ebl.engine` |  |
| `9FCEE4_machtest_ebl_fresh` | Fresh Water | 센서 | `ebl.fresh` | % |
| `9FCEE4_machtest_ebl_inside` | Inside Temperature (EBL) | 센서 | `ebl.inside` | °C |
| `9FCEE4_machtest_ebl_mains` | Mains (230V) | 센서 | `ebl.mains` |  |
| `9FCEE4_machtest_ebl_outside` | Outside Temperature (EBL) | 센서 | `ebl.outside` | °C |
| `9FCEE4_machtest_ebl_soc` | Living Battery SOC (voltage) | 센서 | `ebl.soc` | % |
| `9FCEE4_machtest_ebl_waste` | Waste Water | 센서 | `ebl.waste` | % |

**Switch** — `ON` / `OFF`

| HA ID | 이름 | state 토픽 | set 토픽 |
|---|---|---|---|
| `9FCEE4_machtest_ac` | Air Conditioner | `rv/radxa/machtest/switch/ac/state` | `rv/radxa/machtest/switch/ac/set` |
| `9FCEE4_machtest_ext_light` | Exterior Light | `rv/radxa/machtest/switch/ext_light/state` | `rv/radxa/machtest/switch/ext_light/set` |
| `9FCEE4_machtest_heater` | Heater (Gas) | `rv/radxa/machtest/switch/heater/state` | `rv/radxa/machtest/switch/heater/set` |
| `9FCEE4_machtest_int_light` | Interior Light | `rv/radxa/machtest/switch/int_light/state` | `rv/radxa/machtest/switch/int_light/set` |
| `9FCEE4_machtest_panel` | Main Panel | `rv/radxa/machtest/switch/panel/state` | `rv/radxa/machtest/switch/panel/set` |
| `9FCEE4_machtest_water_pump` | Water Pump | `rv/radxa/machtest/switch/water_pump/state` | `rv/radxa/machtest/switch/water_pump/set` |

**Control** — number = 숫자, select = 선택지, button = `PRESS`, text = 23자 이내

| HA ID | 이름 | 종류 | set 토픽 | 현재 값 (state 경로) | 범위 · 선택지 |
|---|---|---|---|---|---|
| `9FCEE4_machtest_ac_fan` | AC Fan | select | `rv/radxa/machtest/control/ac_fan/set` | `ac.fan` | low · mid · high · night |
| `9FCEE4_machtest_ac_mode` | AC Mode | select | `rv/radxa/machtest/control/ac_mode/set` | `ac.mode` | off · fan · cool · heat · auto |
| `9FCEE4_machtest_ac_set_temp` | AC Set Temperature | number | `rv/radxa/machtest/control/ac_set_temp/set` | `ac.set_c` | 16 ~ 31, 1씩 °C |
| `9FCEE4_machtest_alde_energy` | Alde Energy Source | select | `rv/radxa/machtest/control/alde_energy/set` | `alde.energy` | off · gas · el1 · el2 · el3 · el1_and_gas · el2_and_gas · el3_and_gas |
| `9FCEE4_machtest_alde_set_temp` | Alde Set Temperature | number | `rv/radxa/machtest/control/alde_set_temp/set` | `alde.set` | 5 ~ 30, 1씩 °C |
| `9FCEE4_machtest_alde_water` | Alde Hot Water | select | `rv/radxa/machtest/control/alde_water/set` | `alde.water_text` | off · normal · boost |

### `rv/radxa/tpmstest` — TPMS (test) (HA 미등록)

availability = online. discovery: false — HA에 올리지 않는다. 켜면 아래 ID로 올라간다.

**State** — `rv/radxa/tpmstest/state` JSON

| HA ID | 이름 | 종류 | 경로 | 단위 |
|---|---|---|---|---|
| `9FCEE4_tpmstest_caravan_left_battery` | Caravan Left Tyre Battery | 센서 | `caravan_left.battery` | % |
| `9FCEE4_tpmstest_caravan_left_pressure` | Caravan Left Tyre Pressure | 센서 | `caravan_left.pressure` | bar |
| `9FCEE4_tpmstest_caravan_left_temp_c` | Caravan Left Tyre Temperature | 센서 | `caravan_left.temp_c` | °C |
| `9FCEE4_tpmstest_caravan_right_battery` | Caravan Right Tyre Battery | 센서 | `caravan_right.battery` | % |
| `9FCEE4_tpmstest_caravan_right_pressure` | Caravan Right Tyre Pressure | 센서 | `caravan_right.pressure` | bar |
| `9FCEE4_tpmstest_caravan_right_temp_c` | Caravan Right Tyre Temperature | 센서 | `caravan_right.temp_c` | °C |
| `9FCEE4_tpmstest_tow_air_battery` | Tow Air Suspension Battery | 센서 | `tow_air.battery` | % |
| `9FCEE4_tpmstest_tow_air_pressure` | Tow Air Suspension Pressure | 센서 | `tow_air.pressure` | psi |
| `9FCEE4_tpmstest_tow_air_temp_c` | Tow Air Suspension Temperature | 센서 | `tow_air.temp_c` | °C |

### `rv/radxa/video` — Bird view video (HA 미등록)

availability = online. discovery: false — HA에 올리지 않는다. 켜면 아래 ID로 올라간다.

**State** — `rv/radxa/video/state` JSON

| HA ID | 이름 | 종류 | 경로 | 단위 |
|---|---|---|---|---|
| `9FCEE4_video_capturing` | Capturing | 입력 | `capturing` |  |

## 6. ESP 브로커 — `rv/broker` <a id="rvbroker"></a>

ESP32-S3 · 노드들의 브로커 + ls 브리지. 노드 상태 `rv/broker/status` = online.

엔티티가 아닌 토픽: `rv/broker/bridge`, `rv/broker/diag/info`

HA 엔티티를 내지 않는다.

