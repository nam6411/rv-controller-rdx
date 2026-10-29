import json

from rvnode import broker
from rvnode.config import BrokerConfig

IP_JSON = json.dumps([
    {"ifname": "lo", "flags": ["LOOPBACK", "UP"], "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8}]},
    {"ifname": "wlan0", "flags": ["BROADCAST", "UP"],
     "addr_info": [{"family": "inet", "local": "192.168.1.150", "prefixlen": 24, "broadcast": "192.168.1.255"}]},
    {"ifname": "usb0", "flags": ["UP"], "addr_info": [{"family": "inet", "local": "10.0.0.2", "prefixlen": 30}]},
])


def test_beacon_matches_esp_format():
    # 노드: sscanf(buf, "essio-broker %32s %u") 후 이름이 mqtt.mdns_name과 같아야 받는다
    assert broker.beacon_message("Broker", 1883) == b"essio-broker broker 1883"


def test_parse_ip_json_skips_loopback_and_fills_broadcast():
    assert broker.parse_ip_json(IP_JSON) == [
        ("wlan0", "192.168.1.150", "192.168.1.255"),
        ("usb0", "10.0.0.2", "10.0.0.3"),
    ]


def test_pick_ip_prefers_wireless():
    addrs = broker.parse_ip_json(IP_JSON)
    assert broker.pick_ip(addrs, "wlan0") == "192.168.1.150"
    assert broker.pick_ip(addrs, "eth0") == "192.168.1.150"
    assert broker.pick_ip([], "wlan0") == ""


def test_avahi_alias_keeps_hostname():
    cmds = broker.avahi_commands(BrokerConfig(enabled=True), "192.168.4.1")
    assert cmds[0] == ["avahi-publish-address", "-R", "broker.local", "192.168.4.1"]
    assert cmds[1][:3] == ["avahi-publish-service", "-H", "broker.local"] and cmds[1][-2:] == ["_mqtt._tcp", "1883"]
