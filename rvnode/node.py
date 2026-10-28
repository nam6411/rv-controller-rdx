"""노드: 설정·MQTT·장치들을 묶는다. ESP main.cpp의 노드 역할(구독·명령 분배·discovery·sys/cmd·sys/info)과 같다."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from pathlib import Path
from typing import Any

from . import __version__, ha
from .config import Config
from .device import Device
from .devices import create
from .mqtt import Bus

log = logging.getLogger("rvnode")

SYS_INFO_INTERVAL_S = 60
RESTART_DELAY_S = 5


def _read(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def _wifi_mac() -> str:
    return _read("/sys/class/net/wlan0/address").upper()


class Node:
    def __init__(self, cfg: Config, only: set[str] | None = None, cfg_path: str = "/etc/rvnode.yaml"):
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.base = cfg.node.base
        self.bus = Bus(
            host=cfg.mqtt.host,
            port=cfg.mqtt.port,
            client_id=cfg.client_id(),
            status_topic=f"{self.base}/status",
            username=cfg.mqtt.username,
            password=cfg.mqtt.password,
            keepalive=cfg.mqtt.keepalive,
        )
        self.bus.on_connect = self._on_connect
        self.bus.on_message = self._on_message
        self.info = ha.DeviceInfo(device_id=cfg.node.device_id, base=self.base, sw=__version__, mac=_wifi_mac())
        self.devices: list[Device] = []
        for d in cfg.devices:
            if not d.enabled or (only and d.slug not in only):
                continue
            self.devices.append(create(d, self))
        self.started = time.monotonic()
        self.exit_code: int | None = None
        self._stop = asyncio.Event()

    def device_by_type(self, type_: str) -> Device | None:
        """다른 장치를 찾는다 (예: 원격 영상이 버드뷰 전원을 요청)."""
        return next((d for d in self.devices if d.type == type_), None)

    # ---- 구독·명령 ----

    def _subscribe_all(self) -> None:
        self.bus.subscribe(f"{self.cfg.mqtt.discovery_prefix}/status")
        self.bus.subscribe(f"{self.base}/sys/cmd")
        for d in self.devices:
            if d.switches:
                self.bus.subscribe(f"{d.prefix}/switch/+/set")
            if d.controls:
                self.bus.subscribe(f"{d.prefix}/control/+/set")

    async def _on_connect(self) -> None:
        await self.publish_all()

    async def _on_message(self, topic: str, payload: str) -> None:
        if topic == f"{self.cfg.mqtt.discovery_prefix}/status":
            if payload == "online":
                log.info("HA birth received, republishing discovery")
                await self.publish_all()
            return
        if topic == f"{self.base}/sys/cmd":
            await self._sys_cmd(payload.strip())
            return
        if not topic.endswith("/set"):
            return
        for d in self.devices:
            for kind in ("switch", "control"):
                p = f"{d.prefix}/{kind}/"
                if topic.startswith(p):
                    name = topic[len(p) : -len("/set")]
                    if "/" in name:
                        continue  # 더 긴 prefix를 가진 다른 장치의 토픽 (rv/mach + rv/mach/tpms)
                    try:
                        d.commands.put_nowait((kind, name, payload))
                    except asyncio.QueueFull:
                        log.warning("command queue full, dropped %s", topic)
                    return
        log.warning("no device for %s", topic)

    async def _sys_cmd(self, cmd: str) -> None:
        if cmd == "restart":
            log.warning("restart requested over MQTT")
            self.stop(1)  # systemd Restart=always가 다시 띄운다
        elif cmd == "rediscover":
            await self.publish_all()
        else:
            log.warning("sys/cmd: unsupported '%s'", cmd)

    # ---- 발행 ----

    async def publish_discovery(self, dev: Device) -> None:
        if not self.cfg.mqtt.discovery or not dev.cfg.discovery:
            return
        for topic, payload in ha.all_configs(self.cfg.mqtt.discovery_prefix, self.info, dev):
            await self.bus.publish(topic, json.dumps(payload, ensure_ascii=False, separators=(",", ":")), retain=True)

    async def publish_all(self) -> None:
        for d in self.devices:
            await self.publish_discovery(d)
            await d.publish_all()
        await self.publish_sys_info()

    def sys_info(self) -> dict[str, Any]:
        temp = _read("/sys/class/thermal/thermal_zone0/temp")
        try:
            load1 = os.getloadavg()[0]
        except OSError:
            load1 = None
        return {
            "device_id": self.cfg.node.device_id,
            "fw": __version__,
            "role": "radxa",
            "uptime_s": int(time.monotonic() - self.started),
            "hostname": socket.gethostname(),
            "ip": self.bus.local_ip(),
            "cpu_temp": round(int(temp) / 1000, 1) if temp.isdigit() else None,
            "load1": load1,
            "mqtt_broker": f"{self.cfg.mqtt.host}:{self.cfg.mqtt.port}",
            "base_topic": self.base,
            "mqtt_published": self.bus.published,
            "mqtt_failed": self.bus.failed,
            "devices": [
                {"slug": d.slug, "type": d.type, "online": d.online, "errors": d.errors, "last_error": d.last_error}
                for d in self.devices
            ],
        }

    async def publish_sys_info(self) -> None:
        await self.bus.publish(f"{self.base}/sys/info", json.dumps(self.sys_info(), separators=(",", ":")))

    # ---- 실행 ----

    def stop(self, code: int = 0) -> None:
        self.exit_code = code
        self._stop.set()

    async def _supervised(self, name: str, fn) -> None:
        """한 태스크가 죽어도 다른 장치는 계속 돈다. 죽은 태스크는 잠시 뒤 다시 시작."""
        while True:
            try:
                await fn()
                log.warning("%s: returned, restart in %ds", name, RESTART_DELAY_S)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("%s: crashed, restart in %ds", name, RESTART_DELAY_S)
            await asyncio.sleep(RESTART_DELAY_S)

    async def _sys_info_loop(self) -> None:
        while True:
            await asyncio.sleep(SYS_INFO_INTERVAL_S)
            await self.publish_sys_info()

    async def run(self) -> int:
        self._subscribe_all()
        log.info("rvnode %s: %d device(s): %s", __version__, len(self.devices), ", ".join(f"{d.slug}({d.type})" for d in self.devices) or "-")
        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(self._supervised("mqtt", self.bus.run), name="mqtt"),
                tg.create_task(self._supervised("sysinfo", self._sys_info_loop), name="sysinfo"),
            ]
            if self.cfg.web.enabled:
                from . import web

                tasks.append(tg.create_task(self._supervised("web", lambda: web.serve(self)), name="web"))
            for d in self.devices:
                tasks.append(tg.create_task(self._supervised(d.slug, d.run), name=d.slug))
                tasks.append(tg.create_task(self._supervised(f"{d.slug}/cmd", d.handle_commands), name=f"{d.slug}/cmd"))
            await self._stop.wait()
            for t in tasks:
                t.cancel()
        return self.exit_code or 0
