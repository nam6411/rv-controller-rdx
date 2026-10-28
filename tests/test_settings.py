import pytest
import yaml

from rvnode import settings
from rvnode.config import ConfigError, parse

BASE = {"node": {"device_id": "9FCEE4", "base": "rv/radxa"},
        "mqtt": {"host": "localhost", "password": "local-secret"},
        "bridge": {"enabled": True, "host": "mqtt.nam6411.com", "port": 8883, "tls": True,
                   "username": "radxa", "password": "bridge-secret"},
        "devices": []}


def test_redact_hides_passwords():
    r = settings.redact(BASE)
    assert r["bridge"]["password"] == "" and r["bridge"]["password_set"] is True
    assert r["mqtt"]["password"] == "" and r["mqtt"]["password_set"] is True
    assert BASE["bridge"]["password"] == "bridge-secret"   # 원본은 그대로


def test_merge_keeps_secret_when_blank_and_replaces_when_given():
    up = settings.redact(BASE)
    up["bridge"]["port"] = 1883
    m = settings.merge_update(BASE, up)
    assert m["bridge"]["password"] == "bridge-secret" and m["bridge"]["port"] == 1883
    assert "password_set" not in m["bridge"]
    up["bridge"]["password"] = "new"
    assert settings.merge_update(BASE, up)["bridge"]["password"] == "new"


def test_save_validates_and_rewrites(tmp_path):
    p = tmp_path / "rvnode.yaml"
    p.write_text(yaml.safe_dump(BASE))
    bad = dict(BASE, bridge={"enabled": True, "host": ""})
    with pytest.raises(ConfigError):
        settings.save_raw(p, bad)
    assert yaml.safe_load(p.read_text())["bridge"]["host"] == "mqtt.nam6411.com"  # 실패하면 그대로
    new = dict(BASE, log_level="debug")
    settings.save_raw(p, new)
    assert yaml.safe_load(p.read_text())["log_level"] == "debug"


def test_bridge_conf():
    cfg = parse(BASE)
    text = settings.bridge_conf(cfg.bridge, "9FCEE4")
    assert "address mqtt.nam6411.com:8883" in text
    assert "remote_username radxa" in text and "remote_password bridge-secret" in text
    assert "bridge_capath /etc/ssl/certs" in text
    assert "topic rv/# out 0" in text and "topic homeassistant/status in 0" in text
    assert "topic rv/+/control/+/set in 0" in text
    assert "notification_topic rv/broker/bridge" in text
    cfg.bridge.remote_prefix = "site573"
    assert 'topic rv/# out 0 "" site573/' in settings.bridge_conf(cfg.bridge, "9FCEE4")
    cfg.bridge.enabled = False
    assert settings.bridge_conf(cfg.bridge, "9FCEE4").startswith("#")


def test_apply_bridge_only_when_changed(tmp_path):
    cfg = parse(BASE)
    f = tmp_path / "rv-bridge.conf"
    assert settings.apply_bridge(cfg, f, restart=False) is True
    assert settings.apply_bridge(cfg, f, restart=False) is False


async def test_web_roundtrip(tmp_path, aiohttp_client=None):
    from aiohttp.test_utils import TestClient, TestServer

    from rvnode.node import Node
    from rvnode.web import make_app

    from .conftest import FakeBus

    p = tmp_path / "rvnode.yaml"
    p.write_text(yaml.safe_dump(BASE))
    node = Node(parse(BASE), cfg_path=str(p))
    node.bus = FakeBus()
    node.bus.connected = True
    settings.BRIDGE_CONF = tmp_path / "rv-bridge.conf"
    async with TestClient(TestServer(make_app(node))) as c:
        r = await c.get("/api/config")
        got = await r.json()
        assert got["bridge"]["password"] == "" and got["bridge"]["password_set"]
        assert (await c.get("/")).status == 200
        st = await (await c.get("/api/status")).json()
        assert st["sys"]["device_id"] == "9FCEE4"
