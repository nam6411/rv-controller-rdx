import math

from rvnode.devices.gps import DEFAULT_CAL, GpsFix, distance_m, heading_deg


def test_tpv_sky_feed_and_no_fix_keeps_position():
    g = GpsFix()
    g.feed({"class": "SKY", "uSat": 9, "nSat": 21, "hdop": 0.91}, 1)
    g.feed({"class": "TPV", "mode": 3, "lat": 37.5665, "lon": 126.978, "altMSL": 38.4, "speed": 2.5, "track": 271.6,
            "time": "2026-10-29T01:00:00.000Z"}, 2)
    assert (g.fix, g.lat, g.lon, g.alt_m, g.speed_kmh, g.course, g.sats, g.sats_seen, g.hdop) == \
        (True, 37.5665, 126.978, 38.4, 9.0, 272, 9, 21, 0.91)
    assert g.utc == "2026-10-29T01:00:00Z"
    g.feed({"class": "TPV", "mode": 1, "time": "2026-10-29T01:00:05.000Z"}, 3)
    assert g.fix is False and g.lat == 37.5665          # 마지막 위치를 둔다


def test_sky_counts_from_satellite_list():
    g = GpsFix()
    g.feed({"class": "SKY", "satellites": [{"used": True}, {"used": False}, {"used": True}]}, 1)
    assert (g.sats, g.sats_seen) == (2, 3)


def test_heading_matches_bench_mapping():
    # docs/24 §7.3: 칩 각도 265° → 화살표 북(0°), 181° → 동쪽(84°). 편각 0으로 확인
    cal = {"cx": 0, "cy": 0, "rx": 1, "ry": 1}
    def at(chip):
        return heading_deg(math.cos(math.radians(chip)), math.sin(math.radians(chip)), cal, 265, 0)
    assert round(at(265)) % 360 == 0
    assert round(at(181)) == 84
    assert round(heading_deg(351 + 377, -284, DEFAULT_CAL, 265, -8.5)) == round((265 - 0 - 8.5) % 360)


def test_distance():
    assert 49 < distance_m(37.5665, 126.978, 37.56695, 126.978) < 51


def test_short_address_korean_order():
    from rvnode.devices.gps import short_address
    doc = {"display_name": "123, 동탄대로, 오산동, 화성시, 경기도, 18469, 대한민국",
           "address": {"house_number": "123", "road": "동탄대로", "quarter": "오산동", "city": "화성시",
                       "province": "경기도", "postcode": "18469", "country": "대한민국"}}
    assert short_address(doc) == "경기도 화성시 오산동 동탄대로 123"
    assert short_address({"display_name": "X", "address": {}}) == "X"


def test_device_tracker_discovery():
    from rvnode import ha
    from rvnode.config import parse
    from rvnode.node import Node

    from .conftest import FakeBus
    node = Node(parse({"node": {"device_id": "9FCEE4", "base": "rv/radxa"},
                       "devices": [{"type": "gps", "slug": "gps", "params": {"compass": False}}]}))
    node.bus = FakeBus()
    cfgs = dict(ha.all_configs("homeassistant", node.info, node.devices[0]))
    doc = cfgs["homeassistant/device_tracker/9FCEE4_gps/location/config"]
    assert doc["uniq_id"] == "9FCEE4_gps_location" and doc["def_ent_id"] == "device_tracker.gps_location"
    assert doc["json_attributes_topic"] == "rv/radxa/gps/location" and doc["source_type"] == "gps"
    assert doc["name"] == "Location" and "dev" in doc and doc["avty"]
    assert "homeassistant/sensor/9FCEE4_gps/address/config" in cfgs


def test_last_address_survives_restart(tmp_path, monkeypatch):
    import asyncio

    from rvnode.config import parse
    from rvnode.devices import gps as gm
    from rvnode.node import Node

    from .conftest import FakeBus

    def make():
        node = Node(parse({"node": {"device_id": "9FCEE4", "base": "rv/radxa"},
                           "devices": [{"type": "gps", "slug": "gps", "params": {"compass": False}}]}))
        node.bus = FakeBus()
        return node.devices[0]

    monkeypatch.setenv("STATE_DIRECTORY", str(tmp_path))
    d = make()
    assert d.state()["address"] is None

    class Resp:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def raise_for_status(self): pass
        async def json(self):
            return {"display_name": "182, 동탄대로, 화성시", "address": {"province": "경기도", "city": "화성시", "road": "동탄대로",
                                                                    "house_number": "182"}}

    class Session:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def get(self, *a, **kw): return Resp()

    import aiohttp
    monkeypatch.setattr(aiohttp, "ClientSession", Session)
    asyncio.run(d._geocode(37.19, 127.10))
    assert (tmp_path / "gps.json").exists()

    d2 = make()                                    # 재시작 — 위성 없이도 주소가 있다
    st = d2.state()
    assert st["fix"] is False and st["address"] == d.address and st["address_at"]
    assert d2._geo_pos == (37.19, 127.10)          # 같은 자리면 다시 묻지 않는다
    monkeypatch.delenv("STATE_DIRECTORY")
    assert make().state_file is None
    assert gm.short_address({"address": {"road": "x"}}) == "x"
