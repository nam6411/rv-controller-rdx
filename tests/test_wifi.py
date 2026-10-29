import pytest

from rvnode import wifi
from rvnode.config import ConfigError, FallbackConfig, parse


class FakeNm:
    """nmcli 대신. mode·보이는 SSID·공유기 접속 성공 여부를 시험이 바꾼다."""

    def __init__(self, mode="down", visible=(), saved=("Home",), joins=True, ap_scan=True):
        self.mode, self.visible, self.joins, self.ap_scan = mode, set(visible), joins, ap_scan
        self.saved_list = [{"name": s, "ssid": s} for s in saved]
        self.calls: list[str] = []

    async def status(self):
        con = {"ap": wifi.AP_CON, "sta": "Home"}.get(self.mode, "")
        return {"mode": self.mode, "connection": con, "ip": "192.168.4.1" if self.mode == "ap" else ""}

    async def saved(self):
        return self.saved_list

    async def scan(self, ap_mode):
        if ap_mode and not self.ap_scan:
            return None
        return [{"ssid": s, "signal": 50} for s in self.visible]

    async def ensure_ap(self, cfg):
        self.calls.append("ensure_ap")

    async def up_ap(self):
        self.calls.append("up_ap")
        self.mode = "ap"

    async def leave_ap(self, name=""):
        self.calls.append(f"leave_ap:{name}")
        self.mode = "sta" if self.joins and (self.visible or name) else "down"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(nm, **cfg):
    clock = Clock()
    w = wifi.Wifi(FallbackConfig(enabled=True, **cfg), nm=nm, clock=clock)
    return w, clock


async def test_boot_without_router_goes_ap_after_grace():
    nm = FakeNm(mode="down", visible=())
    w, clock = make(nm)
    await w.tick()
    assert w.state == "start" and "up_ap" not in nm.calls      # NM이 붙어 볼 시간
    clock.t += wifi.BOOT_GRACE_S
    await w.tick()
    assert w.state == "ap" and nm.mode == "ap"


async def test_boot_with_router_visible_waits_instead():
    nm = FakeNm(mode="down", visible=("Home",))
    w, clock = make(nm)
    clock.t += wifi.BOOT_GRACE_S
    await w.tick()
    assert w.state == "sta" and nm.mode == "down"


async def test_sta_loss_goes_ap_after_after_s():
    nm = FakeNm(mode="sta")
    w, clock = make(nm, after_s=60)
    await w.tick()
    assert w.state == "sta"
    nm.mode = "down"
    await w.tick()
    clock.t += 59
    await w.tick()
    assert w.state == "sta"
    clock.t += 1
    await w.tick()
    assert w.state == "ap" and nm.mode == "ap"


async def test_sta_back_before_after_s_resets_timer():
    nm = FakeNm(mode="sta")
    w, clock = make(nm, after_s=60)
    await w.tick()
    nm.mode = "down"
    await w.tick()
    clock.t += 50
    nm.mode = "sta"
    await w.tick()
    nm.mode = "down"
    clock.t += 20
    await w.tick()
    assert w.state == "sta" and w.down_since == clock.t


async def test_ap_returns_to_router_when_visible():
    nm = FakeNm(mode="ap", visible=())
    w, clock = make(nm, rescan_s=30)
    await w.tick()
    assert w.state == "ap"
    nm.visible = {"Home"}
    clock.t += 29
    await w.tick()
    assert w.state == "ap"                       # 아직 스캔할 때가 아니다
    clock.t += 1
    await w.tick()
    assert w.state == "joining" and "leave_ap:" in nm.calls
    await w.tick()
    assert w.state == "sta"


async def test_failed_join_goes_back_to_ap():
    nm = FakeNm(mode="ap", visible=("Home",), joins=False)
    w, clock = make(nm, rescan_s=30)
    await w.tick()
    clock.t += 30
    await w.tick()
    assert w.state == "joining" and nm.mode == "down"
    clock.t += wifi.JOIN_TIMEOUT_S
    await w.tick()
    assert w.state == "ap" and nm.mode == "ap"


async def test_no_ap_scan_falls_back_to_blind_retry():
    nm = FakeNm(mode="ap", visible=("Home",), ap_scan=False)
    w, clock = make(nm, rescan_s=30, sta_retry_s=300)
    await w.tick()
    clock.t += 30
    await w.tick()
    assert w.state == "ap"                       # 스캔이 안 되면 바로는 안 내려간다
    clock.t += 270
    await w.tick()
    assert w.state == "joining"


async def test_no_saved_router_stays_ap():
    nm = FakeNm(mode="ap", visible=("Other",), saved=())
    w, clock = make(nm, rescan_s=30)
    await w.tick()
    clock.t += 300
    await w.tick()
    assert w.state == "ap" and not any(c.startswith("leave_ap") for c in nm.calls)


async def test_forced_ap_holds_while_router_visible():
    nm = FakeNm(mode="sta", visible=("Home",))
    w, clock = make(nm, rescan_s=30)
    await w.tick()
    await w.force_ap()
    assert w.state == "ap"
    clock.t += 60
    await w.tick()
    assert w.state == "ap"                       # 설정하는 사람이 끊기지 않게
    clock.t += wifi.HOLD_S
    await w.tick()
    assert w.state == "joining"


async def test_force_sta_uses_profile_for_ssid():
    nm = FakeNm(mode="ap", visible=())
    w, clock = make(nm)
    await w.tick()
    await w.force_sta("Home")
    assert nm.calls[-1] == "leave_ap:Home" and w.state == "joining"


async def test_disabled_never_switches_by_itself():
    nm = FakeNm(mode="down", visible=())
    w = wifi.Wifi(FallbackConfig(enabled=False), nm=nm, clock=Clock())
    w.clock.t += 10_000
    await w.tick()
    await w.tick()
    assert nm.calls == []


def test_nmcli_parsers():
    assert wifi.split_terse(r"My\:Net:72:WPA2") == ["My:Net", "72", "WPA2"]
    f = wifi.parse_fields("GENERAL.STATE:100 (connected)\nGENERAL.CONNECTION:rv-fallback\nIP4.ADDRESS[1]:192.168.4.1/24\nIP4.ADDRESS[2]:10.0.0.1/8")
    assert f == {"GENERAL.STATE": "100 (connected)", "GENERAL.CONNECTION": "rv-fallback", "IP4.ADDRESS": "192.168.4.1/24"}
    iw = "BSS aa:bb(on wlan0)\n\tsignal: -48.00 dBm\n\tSSID: Home\nBSS cc:dd(on wlan0)\n\tsignal: -80.00 dBm\n\tSSID: \n"
    assert wifi.parse_iw_scan(iw) == [{"ssid": "Home", "signal": -48}]


def test_check_network():
    assert wifi.check_network("Home", "") == ""
    assert wifi.check_network("Home", "short")
    assert wifi.check_network("", "12345678")


def test_config_validates_fallback_and_broker():
    base = {"node": {"device_id": "X", "base": "rv/x"}}
    cfg = parse({**base, "fallback": {"enabled": True, "password": "12341234"}, "broker": {"mdns_name": "Broker"}})
    assert cfg.fallback.address == "192.168.4.1/24" and cfg.broker.mdns_name == "broker"
    with pytest.raises(ConfigError):
        parse({**base, "fallback": {"password": "short"}})
    with pytest.raises(ConfigError):
        parse({**base, "fallback": {"address": "192.168.4.1"[:-2] + "x"}})
    with pytest.raises(ConfigError):
        parse({**base, "broker": {"mdns_name": "broker.local"}})
