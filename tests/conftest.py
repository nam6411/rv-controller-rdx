import pytest

from rvnode.config import parse
from rvnode.node import Node


class FakeBus:
    """발행을 기록만 한다."""

    def __init__(self):
        self.sent: list[tuple[str, str, bool]] = []
        self.subs: list[str] = []
        self.published = 0
        self.failed = 0

    def local_ip(self):
        return "192.0.2.1"

    def subscribe(self, topic):
        self.subs.append(topic)

    async def publish(self, topic, payload, retain=False):
        self.sent.append((topic, payload, retain))
        return True

    def last(self, topic):
        for t, p, r in reversed(self.sent):
            if t == topic:
                return p, r
        return None


@pytest.fixture
def node():
    cfg = parse(
        {
            "node": {"device_id": "9FCEE4", "base": "rv/mach"},
            "devices": [
                {"type": "dummy", "slug": "mach", "label": "MACH"},
                {"type": "dummy", "slug": "tpms", "label": "TPMS"},
            ],
        }
    )
    n = Node(cfg)
    n.bus = FakeBus()
    return n
