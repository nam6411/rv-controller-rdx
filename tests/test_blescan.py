import types

import pytest
import yaml
from aiohttp.test_utils import TestClient, TestServer

from rvnode import blescan, web
from rvnode.config import parse
from rvnode.node import Node

from .conftest import FakeBus

RAW = {
    "node": {"device_id": "9FCEE4", "base": "rv/radxa"},
    "devices": [
        {"type": "jbd", "slug": "bmstest", "label": "BMS (test)", "params": {"ble_address": "A4:C1:37:19:9C:10"}},
        {"type": "mach", "slug": "machtest", "label": "MACH (test)", "enabled": False, "params": {"name": "MACH-BASIC", "address": ""}},
        {"type": "tpms", "slug": "tpmstest", "label": "TPMS", "enabled": False, "params": {"sensors": [
            {"mac": "80:ea:ca:13:b3:61", "name": "tow_air", "label": "Tow Air"},
            {"mac": "", "name": "caravan_left", "label": "Caravan Left"}]}},
        {"type": "dummy", "slug": "demo"},
    ],
}
# 제조사 ID 0x0100(LE 00 01) + 센서 ID 81:ea:ca:23:b9:f4 + 압력 250000(250 kPa) + 온도 2150 + 배터리 90 + 0
TPMS_MD = {0x0100: bytes.fromhex("81eaca23b9f4") + (250000).to_bytes(4, "little") + (2150).to_bytes(4, "little") + bytes([90, 0])}


def adv(**kw):
    return types.SimpleNamespace(**{"local_name": None, "service_uuids": [], "manufacturer_data": {}, "rssi": -60, **kw})


def dev(addr, name=None):
    return types.SimpleNamespace(address=addr, name=name)


def test_classify_kinds():
    assert blescan.classify(dev("a4:c1:37:00:00:01", "xiaoxiang"), adv(service_uuids=[blescan.JBD_SERVICE.upper()]))["kinds"] == ["jbd"]
    assert blescan.classify(dev("C0:00:00:00:00:02", "MACH-BASIC"), adv())["kinds"] == ["mach"]
    t = blescan.classify(dev("C0:00:00:00:00:03"), adv(manufacturer_data=TPMS_MD))
    assert t["kinds"] == ["tpms"] and t["tpms"]["id"] == "81:ea:ca:23:b9:f4" and t["tpms"]["bar"] == 2.5
    assert blescan.classify(dev("C0:00:00:00:00:04", "phone"), adv())["kinds"] == []


def test_current_lists_ble_devices_only():
    cur = blescan.current(RAW)
    assert [c["slug"] for c in cur] == ["bmstest", "machtest", "tpmstest"]
    assert cur[0]["address"] == "A4:C1:37:19:9C:10" and cur[1]["name"] == "MACH-BASIC"
    assert cur[2]["sensors"][0] == {"name": "tow_air", "label": "Tow Air", "id": "80:ea:ca:13:b3:61"}


def test_assign_address_and_tpms_id():
    new = blescan.assign(RAW, "machtest", "c0:00:00:00:00:02")
    assert new["devices"][1]["params"]["address"] == "C0:00:00:00:00:02"
    assert RAW["devices"][1]["params"]["address"] == ""          # 원본은 그대로
    new = blescan.assign(RAW, "tpmstest", "80EACA13B361", "caravan_left")
    s = new["devices"][2]["params"]["sensors"]
    assert s[1]["mac"] == "80:ea:ca:13:b3:61" and s[0]["mac"] == ""   # 같은 ID는 한 자리에만
    assert blescan.assign(RAW, "bmstest", "")["devices"][0]["params"]["ble_address"] == ""


@pytest.mark.parametrize("slug,value,sensor", [
    ("bmstest", "not-a-mac", ""), ("tpmstest", "123", "tow_air"), ("tpmstest", "80eaca13b361", "nope"), ("demo", "", ""), ("x", "", "")])
def test_assign_rejects(slug, value, sensor):
    with pytest.raises(ValueError):
        blescan.assign(RAW, slug, value, sensor)


async def test_ble_api(tmp_path, monkeypatch):
    p = tmp_path / "rvnode.yaml"
    p.write_text(yaml.safe_dump(RAW))
    node = Node(parse(yaml.safe_load(p.read_text())), only={"demo"}, cfg_path=str(p))
    node.bus = FakeBus()
    stops = []
    monkeypatch.setattr(node, "stop", lambda code=0: stops.append(code))

    async def fake_scan(secs):
        return [blescan.classify(dev("C0:00:00:00:00:02", "MACH-BASIC"), adv())]

    monkeypatch.setattr(blescan, "scan", fake_scan)
    async with TestClient(TestServer(web.make_app(node))) as c:
        j = await (await c.get("/api/ble")).json()
        assert [d["slug"] for d in j["devices"]] == ["bmstest", "machtest", "tpmstest"] and j["devices"][0]["connected"] is False
        j = await (await c.get("/api/ble/scan?s=3")).json()
        assert j["ok"] and j["devices"][0]["kinds"] == ["mach"]
        r = await c.put("/api/ble/machtest", json={"address": "bad"})
        assert r.status == 400
        r = await c.put("/api/ble/machtest", json={"address": "C0:00:00:00:00:02"})
        assert (await r.json())["ok"]
    saved = yaml.safe_load(p.read_text())
    assert saved["devices"][1]["params"]["address"] == "C0:00:00:00:00:02"
