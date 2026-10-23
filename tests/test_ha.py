import json

from rvnode import ha
from rvnode.device import Control, Sensor, Switch


def test_sensor_payload_matches_esp(node):
    dev = node.devices[1]  # tpms → rv/mach/tpms
    s = Sensor("caravan_left_pressure", "Caravan left pressure", "bar", "pressure", "measurement", path="caravan_left.pressure")
    doc = ha.sensor(node.info, dev, s)
    assert doc["uniq_id"] == "9FCEE4_tpms_caravan_left_pressure"
    assert doc["def_ent_id"] == "sensor.tpms_caravan_left_pressure"
    assert doc["stat_t"] == "rv/mach/tpms/state"
    assert doc["val_tpl"] == "{{ value_json.caravan_left.pressure }}"
    assert doc["avty"] == [{"t": "rv/mach/status"}, {"t": "rv/mach/tpms/availability"}]
    assert doc["avty_mode"] == "all"
    assert doc["dev"]["ids"] == ["9FCEE4"]
    assert doc["dev"]["name"] == "TPMS"
    assert "sug_dsp_prc" not in doc


def test_partial_availability_and_precision(node):
    dev = node.devices[0]
    s = Sensor("ac_temp", "AC temp", "°C", "temperature", "measurement", path="ac.temp", avail="ac.ok")
    doc = ha.sensor(node.info, dev, s)
    assert doc["sug_dsp_prc"] == 1
    assert doc["avty"][2] == {"t": "rv/mach/state", "val_tpl": "{{ 'online' if value_json.ac.ok else 'offline' }}"}
    assert ha.display_precision(Sensor("cell_v_1", "", "V")) == 3
    assert ha.display_precision(Sensor("pack_v", "", "V")) == 2


def test_switch_and_controls(node):
    dev = node.devices[0]
    sw = ha.switch(node.info, dev, Switch("int_light", "Interior light"))
    assert sw["cmd_t"] == "rv/mach/switch/int_light/set"
    assert sw["stat_t"] == "rv/mach/switch/int_light/state"
    assert (sw["pl_on"], sw["pl_off"]) == ("ON", "OFF")

    sel = ha.control(node.info, dev, Control("alde_water", "Water", "select", path="alde.water", options=("off", "normal", "boost")))
    assert sel["def_ent_id"] == "select.mach_alde_water"
    assert sel["ops"] == ["off", "normal", "boost"]
    assert sel["cmd_t"] == "rv/mach/control/alde_water/set"

    btn = ha.control(node.info, dev, Control("bv_left", "Left", "button"))
    assert btn["pl_prs"] == "PRESS"
    assert "stat_t" not in btn


def test_all_configs_topics(node):
    dev = node.devices[0]
    topics = [t for t, _ in ha.all_configs("homeassistant", node.info, dev)]
    assert "homeassistant/sensor/9FCEE4_mach/counter/config" in topics
    assert "homeassistant/switch/9FCEE4_mach/led/config" in topics
    assert "homeassistant/number/9FCEE4_mach/level/config" in topics
    assert "homeassistant/event/9FCEE4_mach/pinged/config" in topics


async def test_publish_all_retained(node):
    await node.publish_all()
    p, retain = node.bus.last("homeassistant/sensor/9FCEE4_mach/counter/config")
    assert retain and json.loads(p)["uniq_id"] == "9FCEE4_mach_counter"
    p, retain = node.bus.last("rv/mach/tpms/meta")
    assert retain
    meta = json.loads(p)
    assert meta["type"] == "dummy" and meta["label"] == "TPMS"
    assert meta["metrics"][0] == {"l": "Temperature", "u": "°C", "p": "env.temp"}
    assert node.bus.last("rv/mach/availability") == ("offline", True)
    assert node.bus.last("rv/mach/state") is None  # 아직 읽은 적 없으면 state를 내지 않는다
