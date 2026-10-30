"""UVC 캡처(버드뷰 영상) — 보는 사람이 있을 때만 장치를 열어 MJPEG 프레임을 그대로 나눠 준다 (docs/30).

캡처 모듈(MACROSILICON AFN_CAP)은 MJPEG를 내므로 다시 인코딩하지 않는다: v4l2-ctl이 장치 버퍼를 표준 출력으로 쏟고,
여기서 JPEG(FFD8…FFD9) 단위로 잘라 마지막 프레임을 구독자(웹 /api/video/<slug>.mjpeg 등)에게 돌린다.
UVC 장치는 한 프로그램만 열 수 있으므로 원격 송출(docs/30 §4.3)도 이 프레임을 받아 쓴다.

params:
  device       /dev/v4l/by-id/usb-…-video-index0 (기본: 첫 by-id video-index0)
  width/height 1280×720
  fps          10 (장치가 가까운 값으로 맞춘다 — 720p는 60·50·30·20·10)
  idle_s       마지막 구독자가 떠난 뒤 이만큼 지나면 장치를 닫는다 (기본 5)
  push         원격 송출 (video_push.py): url rtsp://mqtt.nam6411.com:8554 (ls go2rtc), encoder libx264,
               fps(기본 위 fps), kbps 1500, keepalive_s 30, live_s 60, pause_fps 1
"""

from __future__ import annotations

import asyncio
import glob
import os
import time
from typing import Any

from ..device import BinarySensor, Control, Device, Sensor
from .video_push import Pusher

SOI, EOI = b"\xff\xd8\xff", b"\xff\xd9"
MAX_FRAME = 2 * 1024 * 1024


def split_jpegs(buf: bytearray) -> list[bytes]:
    """buf 앞에서 완성된 JPEG들을 떼어 낸다. 남은 조각은 buf에 남는다. 엔트로피 데이터 안의 FF는 FF00으로 채워지므로
    FFD9는 끝에서만 나온다."""
    out = []
    while True:
        s = buf.find(SOI)
        if s < 0:
            del buf[: max(0, len(buf) - 2)]
            return out
        e = buf.find(EOI, s + 3)
        if e < 0:
            del buf[:s]
            if len(buf) > MAX_FRAME:
                buf.clear()
            return out
        out.append(bytes(buf[s : e + 2]))
        del buf[: e + 2]


def default_device() -> str:
    found = sorted(glob.glob("/dev/v4l/by-id/*-video-index0"))
    return found[0] if found else "/dev/video0"


class Video(Device):
    type = "video"
    binary_sensors = (BinarySensor("capturing", "Capturing", "capturing", "running"),
                      BinarySensor("streaming", "Remote streaming", "push.streaming", "running"))
    sensors = (Sensor("push_mode", "Remote view", path="push.mode"),
               Sensor("push_left_s", "Live left", "s", path="push.left_s"))
    controls = (Control("push", "Push", "text", discover=False),  # Lightsail go2rtc의 bv_request가 보낸다
                Control("extend", "Extend live", "button"))

    def __init__(self, cfg, node):
        super().__init__(cfg, node)
        p = self.params
        self.device = str(p.get("device") or default_device())
        self.width = int(p.get("width", 1280))
        self.height = int(p.get("height", 720))
        self.fps = int(p.get("fps", 10))
        self.idle_s = float(p.get("idle_s", 5))
        self.frame: bytes | None = None
        self.frame_no = 0
        self.frame_at = 0.0
        self.viewers = 0
        self.measured_fps = 0.0
        self._new = asyncio.Condition()
        self._wanted = asyncio.Event()
        self._proc: asyncio.subprocess.Process | None = None
        self.capture_error = ""
        self.pusher = Pusher(self, p.get("push") or {})

    async def turn_on_birdview(self) -> None:
        """원격 송출이 시작되면 버드뷰가 꺼져 있을 때만 전원 스위치를 켠다 — 사용자가 전원 버튼을 한 번 누른 것과 같다.
        이미 켜져 있으면(시동·스위치) 그대로 두고, 송출이 끝나도 끄지 않는다."""
        for d in self.node.devices:
            if d.type == "birdview" and d.online and not d.state().get("powered"):
                self.log.info("bird view off — switching it on for the remote view")
                await d.write_switch("power", True)
                await d.publish_switch("power")
                await d.publish_state()

    async def write_control(self, name: str, value: str) -> bool:
        if name == "push":
            return await self.pusher.command(value)
        if name == "extend":
            return self.pusher.extend()
        return False

    # ---- 구독 (웹 등) ----

    async def frames(self):
        """새 프레임마다 JPEG를 내준다. 쓰는 동안 장치가 열려 있다."""
        self.viewers += 1
        self._wanted.set()
        last = self.frame_no  # 지난 세션의 프레임이 아니라 새로 찍힌 것부터
        try:
            while True:
                async with self._new:
                    await asyncio.wait_for(self._new.wait_for(lambda: self.frame_no != last), 10)
                last = self.frame_no
                yield self.frame
        finally:
            self.viewers -= 1

    async def snapshot(self, timeout: float = 5) -> bytes | None:
        agen = self.frames()
        try:
            return await asyncio.wait_for(agen.__anext__(), timeout)
        except (asyncio.TimeoutError, StopAsyncIteration):
            return None
        finally:
            await agen.aclose()

    # ---- 캡처 ----

    async def _capture(self) -> None:
        """구독자가 있는 동안 v4l2-ctl을 돌린다."""
        cmd = ["v4l2-ctl", "-d", self.device,
               f"--set-fmt-video=width={self.width},height={self.height},pixelformat=MJPG",
               f"--set-parm={self.fps}", "--stream-mmap", "--stream-to=-"]
        self._proc = proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        self.log.info("capture started %s %dx%d@%d", self.device, self.width, self.height, self.fps)
        buf, count, t0 = bytearray(), 0, time.monotonic()
        idle_since: float | None = None
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(proc.stdout.read(65536), 5)
                except asyncio.TimeoutError:
                    raise RuntimeError("no data from capture") from None
                if not chunk:
                    raise RuntimeError(f"capture exited ({await proc.wait()})")
                buf.extend(chunk)
                for f in split_jpegs(buf):
                    async with self._new:
                        self.frame, self.frame_no, self.frame_at = f, self.frame_no + 1, time.time()
                        self._new.notify_all()
                    count += 1
                now = time.monotonic()
                if now - t0 >= 2:
                    self.measured_fps, count, t0 = round(count / (now - t0), 1), 0, now
                if self.viewers:
                    idle_since = None
                elif idle_since is None:
                    idle_since = now
                elif now - idle_since >= self.idle_s:
                    return
        finally:
            self._proc = None
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 3)
                except asyncio.TimeoutError:
                    proc.kill()
            self.measured_fps = 0.0
            self.log.info("capture stopped")

    async def run(self) -> None:
        while True:
            present = os.path.exists(self.device)
            if present:
                await self.ok()
            else:
                await self.error(f"{self.device} not found")
            await self.publish_state()
            if not present:
                await asyncio.sleep(10)
                continue
            try:
                await asyncio.wait_for(self._wanted.wait(), 60)
            except asyncio.TimeoutError:
                continue
            self._wanted.clear()
            if not self.viewers:
                continue
            await self.publish_state()
            try:
                await self._capture()
                self.capture_error = ""
            except Exception as e:
                self.capture_error = str(e) or type(e).__name__
                await self.error(self.capture_error)
                await asyncio.sleep(3)
            if self.viewers:
                self._wanted.set()

    def state(self) -> dict[str, Any]:
        return {"capturing": self._proc is not None, "viewers": self.viewers, "fps": self.measured_fps,
                "width": self.width, "height": self.height, "device": self.device,
                "frame_bytes": len(self.frame) if self.frame else 0, "error": self.capture_error,
                "push": self.pusher.state()}
