"""노드가 이 보드를 브로커로 찾게 한다 — ESP BrokerService와 같은 두 길 (rv-controller docs/15 §5).

- UDP 비콘: 붙은 망마다 5초에 한 번 `essio-broker <mdns_name> <port>`를 47300으로 브로드캐스트한다.
  노드는 이름이 자기 `mqtt.mdns_name`과 같을 때만 받고, 보낸 주소를 브로커로 쓴다. 공유기가 멀티캐스트를
  넘기지 않아 mDNS가 안 될 때의 길이라 꼭 필요하다.
- mDNS: 호스트명(radxa)은 그대로 두고 avahi로 `<mdns_name>.local` 별칭과 `_mqtt._tcp`를 더 알린다.
  주소가 바뀌면(공유기 ↔ 폴백 AP) 다시 알린다.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket

from .config import BrokerConfig

log = logging.getLogger("rvnode.broker")

MDNS_CHECK_S = 10


def beacon_message(name: str, port: int) -> bytes:
    """ESP BrokerService::sendBeacon과 같은 꼴. 노드는 sscanf("essio-broker %32s %u")로 읽는다."""
    return f"essio-broker {name.lower()} {port}".encode()


def parse_ip_json(text: str) -> list[tuple[str, str, str]]:
    """`ip -j -4 addr` → [(인터페이스, 주소, 브로드캐스트)]. 루프백은 뺀다."""
    out = []
    for link in json.loads(text or "[]"):
        if "LOOPBACK" in link.get("flags", []):
            continue
        for a in link.get("addr_info", []):
            if a.get("family") != "inet" or not a.get("local"):
                continue
            bcast = a.get("broadcast") or str(ipaddress.IPv4Interface(f"{a['local']}/{a.get('prefixlen', 24)}").network.broadcast_address)
            out.append((link.get("ifname", ""), a["local"], bcast))
    return out


async def iface_addrs() -> list[tuple[str, str, str]]:
    try:
        p = await asyncio.create_subprocess_exec("ip", "-j", "-4", "addr", "show",
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(p.communicate(), 5)
        return parse_ip_json(out.decode())
    except (OSError, asyncio.TimeoutError, ValueError):
        return []  # Mac 작업대에는 ip가 없다


def pick_ip(addrs: list[tuple[str, str, str]], prefer: str) -> str:
    """mDNS 별칭에 실을 주소 하나 — 무선(prefer)이 있으면 그것, 없으면 첫 주소."""
    for ifname, ip, _ in addrs:
        if ifname == prefer:
            return ip
    return addrs[0][1] if addrs else ""


async def beacon_loop(cfg: BrokerConfig) -> None:
    msg = beacon_message(cfg.mdns_name, cfg.port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setblocking(False)
    log.info("beacon: %s → udp/%d every %gs", msg.decode(), cfg.beacon_port, cfg.beacon_interval_s)
    sent_to: set[str] = set()
    try:
        while True:
            targets = {b for _, _, b in await iface_addrs()}
            for b in targets:
                try:
                    sock.sendto(msg, (b, cfg.beacon_port))
                except OSError as e:
                    log.debug("beacon to %s: %s", b, e)
            if targets != sent_to:
                log.info("beacon: broadcasting to %s", ", ".join(sorted(targets)) or "(no network)")
                sent_to = targets
            await asyncio.sleep(cfg.beacon_interval_s)
    finally:
        sock.close()


def avahi_commands(cfg: BrokerConfig, ip: str) -> list[list[str]]:
    host = f"{cfg.mdns_name}.local"
    return [
        # -R: 역방향(PTR)은 내지 않는다 — 그 주소의 이름은 원래 호스트명(radxa.local)으로 남긴다
        ["avahi-publish-address", "-R", host, ip],
        ["avahi-publish-service", "-H", host, f"RV broker ({cfg.mdns_name})", "_mqtt._tcp", str(cfg.port)],
    ]


async def mdns_loop(cfg: BrokerConfig, iface: str) -> None:
    """avahi-publish-* 프로세스를 띄워 두고, 주소가 바뀌거나 프로세스가 죽으면 다시 띄운다."""
    procs: list[asyncio.subprocess.Process] = []
    cur_ip = ""
    last_err = ""

    async def stop() -> None:
        for p in procs:
            if p.returncode is None:
                p.terminate()
                try:
                    await asyncio.wait_for(p.wait(), 3)
                except asyncio.TimeoutError:
                    p.kill()
        procs.clear()

    try:
        while True:
            ip = pick_ip(await iface_addrs(), iface)
            dead = [p for p in procs if p.returncode is not None]
            if ip != cur_ip or dead:
                if dead:
                    err = b""
                    for p in dead:
                        err += (await p.stderr.read()) if p.stderr else b""
                    msg = err.decode(errors="replace").strip() or f"exit {dead[0].returncode}"
                    if msg != last_err:  # ESP 브로커가 아직 broker.local을 쥐고 있으면 충돌로 계속 죽는다
                        log.warning("mdns: avahi-publish stopped: %s", msg)
                        last_err = msg
                await stop()
                cur_ip = ip
                if ip:
                    try:
                        for cmd in avahi_commands(cfg, ip):
                            procs.append(await asyncio.create_subprocess_exec(
                                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE))
                    except FileNotFoundError:
                        log.warning("mdns: avahi-utils가 없다 — mDNS 별칭 없이 비콘만 쓴다")
                        await stop()
                        await asyncio.Event().wait()
                    if not dead:
                        log.info("mdns: %s.local → %s, _mqtt._tcp %d", cfg.mdns_name, ip, cfg.port)
            await asyncio.sleep(MDNS_CHECK_S)
    finally:
        await stop()
