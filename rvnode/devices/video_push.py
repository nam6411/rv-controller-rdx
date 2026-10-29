"""원격 송출 (docs/30 §4.3) — Lightsail go2rtc가 보는 사람이 생겼다고 MQTT로 알리면 프레임을 H.264로 바꿔 RTSP로 밀어 넣는다.

ffmpeg 하나를 세션 내내 띄워 두고 표준 입력으로 JPEG를 넣는다. 라이브 ↔ 일시정지를 바꿔도 ffmpeg·송출 연결은 그대로다.
- 라이브: Video.frames()의 JPEG를 그대로 넘긴다 (그동안 캡처 장치가 열린다)
- 일시정지: frames()를 닫고(장치가 쉰다) 마지막 프레임을 어둡게 한 안내 화면을 pause_fps로 넘긴다
송출 주소의 호스트는 Radxa 설정(push.url — ls go2rtc, Lightsail 방화벽 TCP 8554)으로 고정하고, MQTT로는 경로·계정만 받는다.

명령 (control/push/set, JSON): start {id, path, user, pass} · keepalive {id} · stop {id} · extend
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

if TYPE_CHECKING:
    from .video import Video

PATH_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
PAUSED_TEXT = "일시정지 — 이어보기를 누르세요"
NO_SIGNAL_TEXT = "영상 없음"


@dataclass
class Session:
    id: str
    url: str
    started: float = field(default_factory=time.time)
    keepalive: float = field(default_factory=time.monotonic)
    live_until: float = 0.0
    mode: str = "live"
    stop: asyncio.Event = field(default_factory=asyncio.Event)


def push_url(base: str, path: str, user: str, pw: str) -> str:
    """rtsp://<ls>:8554 + 경로 + 계정. 경로는 go2rtc가 만든 무작위 이름만 받는다."""
    if not PATH_RE.match(path):
        raise ValueError(f"bad path {path!r}")
    scheme, rest = base.rstrip("/").split("://", 1)
    auth = f"{quote(user, safe='')}:{quote(pw, safe='')}@" if user else ""
    return f"{scheme}://{auth}{rest}/{path}"


def ffmpeg_cmd(url: str, fps: int, kbps: int, encoder: str) -> list[str]:
    enc = (["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-pix_fmt", "yuv420p"]
           if encoder == "libx264" else ["-c:v", encoder])
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats",
            "-f", "mjpeg", "-use_wallclock_as_timestamps", "1", "-i", "pipe:0", "-an",
            "-vf", f"fps={fps}", *enc, "-b:v", f"{kbps}k", "-maxrate", f"{kbps * 4 // 3}k",
            "-bufsize", f"{kbps * 2}k", "-g", str(fps * 2),
            "-f", "rtsp", "-rtsp_transport", "tcp", url]


async def card(base: bytes | None, text: str, width: int, height: int) -> bytes | None:
    """안내 화면 JPEG — base(마지막 프레임)를 어둡게 깔고 가운데에 글자. 실패하면 None."""
    font = f"fontfile={FONT}:" if os.path.exists(FONT) else ""
    vf = (f"scale={width}:{height},eq=brightness=-0.25:saturation=0.4,"
          f"drawtext={font}text='{text}':fontcolor=white:fontsize={height // 15}:"
          "x=(w-tw)/2:y=(h-th)/2:box=1:boxcolor=black@0.55:boxborderw=24")
    src = ["-f", "mjpeg", "-i", "pipe:0"] if base else ["-f", "lavfi", "-i", f"color=c=0x202020:s={width}x{height}"]
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", *src, "-frames:v", "1", "-vf", vf,
            "-q:v", "5", "-f", "mjpeg", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(base or b""), 10)
        return out if proc.returncode == 0 and out.startswith(b"\xff\xd8") else None
    except (OSError, asyncio.TimeoutError):
        return None


class Pusher:
    def __init__(self, video: Video, params: dict[str, Any]):
        self.video = video
        self.log = video.log
        self.base = str(params.get("url", "rtsp://mqtt.nam6411.com:8554"))
        self.encoder = str(params.get("encoder", "libx264"))
        self.fps = int(params.get("fps", video.fps))
        self.kbps = int(params.get("kbps", 1500))
        self.keepalive_s = float(params.get("keepalive_s", 30))
        self.live_s = float(params.get("live_s", 60))
        self.pause_fps = float(params.get("pause_fps", 1))
        self.session: Session | None = None
        self.task: asyncio.Task | None = None
        self.error = ""
        self._cards: dict[tuple[int, str], bytes | None] = {}

    # ---- 명령 ----

    async def command(self, value: str) -> bool:
        try:
            msg = json.loads(value) if value.strip().startswith("{") else {"cmd": value.strip()}
        except ValueError:
            self.log.warning("push: bad command %r", value[:80])
            return False
        cmd, sid, s = msg.get("cmd"), str(msg.get("id", "")), self.session
        if cmd == "extend":
            return self.extend()
        if cmd == "start":
            if s and s.id == sid:
                s.keepalive = time.monotonic()
                return True
            try:
                url = push_url(self.base, str(msg.get("path", "")), str(msg.get("user", "")), str(msg.get("pass", "")))
            except ValueError as e:
                self.log.warning("push: %s", e)
                return False
            await self.stop()
            self.session = Session(sid, url, live_until=time.monotonic() + self.live_s)
            self.task = asyncio.create_task(self._run(self.session), name=f"{self.video.slug}/push")
            self.log.info("push start id=%s path=%s", sid, msg.get("path"))
            return True
        if not s or s.id != sid:
            return False  # 지난 세션의 늦은 명령
        if cmd == "keepalive":
            s.keepalive = time.monotonic()
            return True
        if cmd == "stop":
            await self.stop()
            return True
        return False

    def extend(self) -> bool:
        if not self.session:
            return False
        self.session.live_until = time.monotonic() + self.live_s
        return True

    async def stop(self) -> None:
        s, t = self.session, self.task
        if s:
            s.stop.set()
        if t:
            try:
                await asyncio.wait_for(t, 10)
            except (asyncio.TimeoutError, Exception):
                t.cancel()
        self.session = self.task = None

    def state(self) -> dict[str, Any]:
        s = self.session
        if not s:
            return {"streaming": False, "mode": "off", "left_s": 0, "error": self.error}
        left = max(0, round(s.live_until - time.monotonic())) if s.mode == "live" else 0
        return {"streaming": True, "mode": s.mode, "left_s": left,
                "since": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(s.started)), "error": self.error}

    # ---- 송출 ----

    def _expired(self, s: Session) -> bool:
        return s.stop.is_set() or time.monotonic() - s.keepalive > self.keepalive_s

    async def _run(self, s: Session) -> None:
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd(s.url, self.fps, self.kbps, self.encoder),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        self.error = ""
        await self.video.publish_state()
        try:
            while not self._expired(s):
                if proc.returncode is not None:
                    raise RuntimeError("ffmpeg exited")
                if time.monotonic() < s.live_until:
                    await self._live(s, proc)
                else:
                    await self._paused(s, proc)
        except Exception as e:
            err = (await _stderr_tail(proc)) or str(e) or type(e).__name__
            self.error = err
            self.log.warning("push: %s", err)
        finally:
            if proc.returncode is None:
                try:
                    proc.stdin.close()
                    await asyncio.wait_for(proc.wait(), 3)
                except (asyncio.TimeoutError, OSError, ConnectionError):
                    proc.kill()
            if self.session is s:
                self.session = None
            self.log.info("push end id=%s (%s)", s.id, "stop" if s.stop.is_set() else "keepalive timeout" if not self.error else "error")
            await self.video.publish_state()

    async def _feed(self, proc, jpg: bytes | None) -> None:
        if not jpg:
            return
        proc.stdin.write(jpg)
        await asyncio.wait_for(proc.stdin.drain(), 10)

    async def _live(self, s: Session, proc) -> None:
        s.mode = "live"
        await self.video.publish_state()
        agen = self.video.frames()
        last_pub = time.monotonic()
        try:
            while time.monotonic() < s.live_until and not self._expired(s):
                try:
                    jpg = await asyncio.wait_for(agen.__anext__(), 5)
                except (asyncio.TimeoutError, StopAsyncIteration):
                    # 캡처가 안 나온다 — 연결이 끊기지 않게 안내 화면을 넣고 다시 시도
                    await self._feed(proc, await self._card(None, NO_SIGNAL_TEXT))
                    await agen.aclose()
                    agen = self.video.frames()
                    continue
                await self._feed(proc, jpg)
                if time.monotonic() - last_pub >= 5:
                    last_pub = time.monotonic()
                    await self.video.publish_state()
        finally:
            await agen.aclose()

    async def _paused(self, s: Session, proc) -> None:
        s.mode = "paused"
        await self.video.publish_state()
        jpg = await self._card(self.video.frame, PAUSED_TEXT)
        while time.monotonic() >= s.live_until and not self._expired(s):
            await self._feed(proc, jpg)
            await asyncio.sleep(1 / self.pause_fps)

    async def _card(self, base: bytes | None, text: str) -> bytes | None:
        key = (id(base), text)
        if key not in self._cards:  # 마지막 하나만 기억한다
            self._cards = {key: await card(base, text, self.video.width, self.video.height) or base}
        return self._cards[key]


async def _stderr_tail(proc) -> str:
    try:
        data = await asyncio.wait_for(proc.stderr.read(4096), 1) if proc.stderr else b""
    except asyncio.TimeoutError:
        return ""
    lines = [x for x in data.decode(errors="replace").splitlines() if x.strip()]
    return lines[-1][:200] if lines else ""
