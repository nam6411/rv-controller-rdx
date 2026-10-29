from rvnode.devices.video import split_jpegs


def test_split_jpegs_across_chunks():
    a = b"\xff\xd8\xff\xe0" + b"A" * 10 + b"\xff\x00" + b"\xff\xd9"
    b = b"\xff\xd8\xff\xdb" + b"B" * 5 + b"\xff\xd9"
    buf = bytearray(b"\x00\x00" + a + b[:6])
    assert split_jpegs(buf) == [a]
    assert bytes(buf) == b[:6]          # 덜 온 프레임은 남긴다
    buf.extend(b[6:])
    assert split_jpegs(buf) == [b] and buf == bytearray()
    junk = bytearray(b"xyz" * 10)
    assert split_jpegs(junk) == [] and len(junk) <= 2


import asyncio
import json

import pytest

from rvnode.config import parse
from rvnode.devices import video_push as vp
from rvnode.ha import all_configs
from rvnode.node import Node

from .conftest import FakeBus


def test_push_url_takes_only_plain_paths():
    assert vp.push_url("rtsp://127.0.0.1:8554/", "abc_12-X", "u", "p@ss") == "rtsp://u:p%40ss@127.0.0.1:8554/abc_12-X"
    for bad in ("", "../x", "a/b", "a?b", "x" * 65):
        with pytest.raises(ValueError):
            vp.push_url("rtsp://127.0.0.1:8554", bad, "u", "p")


def test_ffmpeg_cmd_libx264():
    cmd = vp.ffmpeg_cmd("rtsp://h/p", 10, 1500, "libx264")
    assert cmd[-1] == "rtsp://h/p" and "libx264" in cmd and "fps=10" in cmd and "-g" in cmd
    assert cmd[cmd.index("-g") + 1] == "20"


def make_video():
    cfg = parse({"node": {"device_id": "9FCEE4", "base": "rv/radxa"},
                 "devices": [{"type": "video", "slug": "video", "label": "Video",
                              "params": {"device": "/nonexistent", "push": {"live_s": 60}}}]})
    node = Node(cfg)
    node.bus = FakeBus()
    return node.devices[0]


def test_push_hidden_from_ha_extend_is_a_button():
    d = make_video()
    topics = [t for t, _ in all_configs("homeassistant", d.node.info, d)]
    assert not any("/push/" in t for t in topics)
    assert "homeassistant/button/9FCEE4_video/extend/config" in topics
    assert "homeassistant/sensor/9FCEE4_video/push_mode/config" in topics


def test_push_session_commands(monkeypatch):
    async def go():
        d = make_video()
        runs = []

        async def fake_run(self, s):
            runs.append(s)
            await s.stop.wait()
            if self.session is s:
                self.session = None

        monkeypatch.setattr(vp.Pusher, "_run", fake_run)
        p = d.pusher
        assert d.state()["push"]["mode"] == "off"
        start = {"cmd": "start", "id": "a", "path": "xyz", "user": "u", "pass": "p"}
        assert await d.write_control("push", json.dumps(start))
        await asyncio.sleep(0)
        assert runs[0].url == "rtsp://u:p@127.0.0.1:8554/xyz"
        st = d.state()["push"]
        assert st["streaming"] and st["mode"] == "live" and 58 <= st["left_s"] <= 60
        assert await d.write_control("push", json.dumps(start))           # 같은 id → keepalive, 새로 띄우지 않는다
        assert len(runs) == 1
        assert not await d.write_control("push", '{"cmd":"keepalive","id":"old"}')
        p.session.live_until = 0
        assert await d.write_control("extend", "PRESS")
        assert p.session.live_until > 0
        assert not await d.write_control("push", '{"cmd":"start","id":"b","path":"../etc"}')
        assert await d.write_control("push", '{"cmd":"start","id":"b","path":"p2"}')  # 새 세션이 앞 세션을 끝낸다
        await asyncio.sleep(0)
        assert runs[0].stop.is_set() and len(runs) == 2 and p.session.id == "b"
        assert await d.write_control("push", '{"cmd":"stop","id":"b"}')
        assert p.session is None and d.state()["push"]["streaming"] is False
        assert not await d.write_control("extend", "PRESS")

    asyncio.run(go())
