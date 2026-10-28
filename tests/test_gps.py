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
