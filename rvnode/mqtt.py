"""MQTT 연결 하나를 노드 전체가 같이 쓴다. ESP MqttManager와 같은 규칙 (docs/07 §B.2):
LWT <base>/status = offline(retain), 접속하면 online, 재접속은 1→2→…→30초 백오프.
끊겨 있을 때 발행은 버린다 — 다시 붙으면 노드가 전부 다시 발행한다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import aiomqtt

log = logging.getLogger("rvnode.mqtt")

OnConnect = Callable[[], Awaitable[None]]
OnMessage = Callable[[str, str], Awaitable[None]]


class Bus:
    def __init__(
        self,
        host: str,
        port: int,
        client_id: str,
        status_topic: str,
        username: str = "",
        password: str = "",
        keepalive: int = 30,
    ):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.status_topic = status_topic
        self.username = username or None
        self.password = password or None
        self.keepalive = keepalive
        self.on_connect: OnConnect | None = None
        self.on_message: OnMessage | None = None
        self._subs: list[str] = []
        self._client: aiomqtt.Client | None = None
        self.published = 0
        self.failed = 0

    @property
    def connected(self) -> bool:
        return self._client is not None

    def local_ip(self) -> str:
        """브로커 쪽으로 나가는 내 주소. 이름 풀이를 하지 않도록 이미 연결된 소켓에서 읽는다."""
        c = self._client
        try:
            sock = c._client.socket() if c else None  # paho 클라이언트
            return sock.getsockname()[0] if sock else ""
        except (AttributeError, OSError):
            return ""

    def subscribe(self, topic: str) -> None:
        """접속할 때마다 다시 구독한다. run() 전에 불러 둔다."""
        if topic not in self._subs:
            self._subs.append(topic)

    async def publish(self, topic: str, payload: str, retain: bool = False) -> bool:
        c = self._client
        if c is None:
            return False
        try:
            await c.publish(topic, payload, qos=0, retain=retain)
            self.published += 1
            return True
        except aiomqtt.MqttError as e:
            self.failed += 1
            log.debug("publish %s failed: %s", topic, e)
            return False

    async def run(self) -> None:
        backoff = 1
        while True:
            try:
                async with aiomqtt.Client(
                    hostname=self.host,
                    port=self.port,
                    identifier=self.client_id,
                    username=self.username,
                    password=self.password,
                    keepalive=self.keepalive,
                    will=aiomqtt.Will(self.status_topic, "offline", qos=1, retain=True),
                ) as client:
                    log.info("connected to %s:%d as %s", self.host, self.port, self.client_id)
                    self._client = client
                    backoff = 1
                    await client.publish(self.status_topic, "online", qos=1, retain=True)
                    for t in self._subs:
                        await client.subscribe(t)
                    if self.on_connect:
                        await self.on_connect()
                    async for msg in client.messages:
                        if self.on_message:
                            payload = msg.payload.decode(errors="replace") if isinstance(msg.payload, bytes) else str(msg.payload)
                            await self.on_message(str(msg.topic), payload)
            except aiomqtt.MqttError as e:
                log.warning("mqtt: %s — retry in %ds", e, backoff)
            finally:
                self._client = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
