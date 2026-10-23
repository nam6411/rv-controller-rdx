import pytest

from rvnode.config import ConfigError, parse, slot_prefix


def test_slot_prefix_matches_esp():
    # docs/07 §B.7, Scheduler::slotPrefix — MACH 노드 rv/mach에 TPMS를 더하면 mach는 rv/mach, tpms는 rv/mach/tpms
    assert slot_prefix("rv/mach", "mach") == "rv/mach"
    assert slot_prefix("rv/mach", "tpms") == "rv/mach/tpms"


def test_parse_defaults_and_prefix():
    cfg = parse(
        {
            "node": {"device_id": "9FCEE4", "base": "rv/mach/"},
            "devices": [
                {"type": "dummy", "slug": "mach"},
                {"type": "dummy", "slug": "tpms", "label": "TPMS"},
                {"type": "dummy", "slug": "bms", "prefix": "rv/bms"},
            ],
        }
    )
    assert cfg.node.base == "rv/mach"
    assert [d.prefix for d in cfg.devices] == ["rv/mach", "rv/mach/tpms", "rv/bms"]
    assert cfg.devices[0].label == "mach"
    assert cfg.client_id() == "essio-radxa-9FCEE4"
    assert cfg.mqtt.discovery_prefix == "homeassistant"


def test_rejects_unknown_keys_and_bad_slugs():
    with pytest.raises(ConfigError, match="unknown keys"):
        parse({"node": {"device_id": "X", "base": "rv/x", "typo": 1}})
    with pytest.raises(ConfigError, match="slug"):
        parse({"node": {"device_id": "X", "base": "rv/x"}, "devices": [{"type": "dummy", "slug": "Bad-Slug"}]})
    with pytest.raises(ConfigError, match="duplicate"):
        parse({"node": {"device_id": "X", "base": "rv/x"}, "devices": [{"type": "dummy", "slug": "a"}] * 2})
