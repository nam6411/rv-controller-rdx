"""웹 설정에서 쓰는 저장·적용. 설정 원본은 YAML 한 파일(/etc/rvnode.yaml)이다.

- 비밀번호 칸은 밖으로 내보낼 때 비우고(`*_set` 표시만), 들어온 값이 비어 있으면 기존 값을 그대로 둔다.
- 브리지 설정은 mosquitto 설정 파일로 옮겨 쓴다(/etc/mosquitto/conf.d/rv-bridge.conf). 바뀌었을 때만 mosquitto를 재시작한다.
"""

from __future__ import annotations

import copy
import os
import subprocess
from pathlib import Path
from typing import Any

import yaml

from .config import BridgeConfig, Config, parse

SECRETS = (("mqtt", "password"), ("bridge", "password"), ("web", "password"), ("fallback", "password"))
BRIDGE_CONF = Path(os.environ.get("RVNODE_BRIDGE_CONF", "/etc/mosquitto/conf.d/rv-bridge.conf"))


def redact(raw: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(raw)
    for sec, key in SECRETS:
        part = out.get(sec)
        if isinstance(part, dict) and key in part:
            part[f"{key}_set"] = bool(part.get(key))
            part[key] = ""
    return out


def merge_update(current: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """웹에서 온 설정(통째) + 기존 비밀번호. 빈 비밀번호 = 그대로 둠. *_set 표시는 버린다."""
    new = copy.deepcopy(update)
    for sec, key in SECRETS:
        part = new.get(sec)
        if not isinstance(part, dict):
            continue
        part.pop(f"{key}_set", None)
        if not part.get(key):
            old = (current.get(sec) or {}).get(key)
            if old:
                part[key] = old
            else:
                part.pop(key, None)
    return new


def save_raw(path: str | Path, raw: dict[str, Any]) -> Config:
    """검증한 뒤 같은 파일에 덮어쓴다(파일 권한·소유를 그대로 두려고 제자리에 쓴다)."""
    cfg = parse(copy.deepcopy(raw))
    text = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False)
    with open(path, "r+", encoding="utf-8") as f:
        f.seek(0)
        f.write(text)
        f.truncate()
    return cfg


def bridge_conf(b: BridgeConfig, device_id: str) -> str:
    """mosquitto 브리지 블록. 꺼져 있으면 주석 한 줄."""
    if not b.enabled:
        return "# rvnode: 외부 브리지 꺼짐 (웹 설정에서 켠다)\n"
    rp = b.remote_prefix.strip("/")
    rp = f"{rp}/" if rp else ""
    lines = [
        "# rvnode가 만든 파일 — 웹 설정(/etc/rvnode.yaml의 bridge)을 바꾸면 다시 쓴다. 손으로 고치지 않는다.",
        "connection rv-bridge",
        f"address {b.host}:{b.port}",
        f"remote_clientid rv-radxa-{device_id}",
    ]
    if b.username:
        lines.append(f"remote_username {b.username}")
    if b.password:
        lines.append(f"remote_password {b.password}")
    if b.tls:
        lines += ["bridge_capath /etc/ssl/certs", "bridge_insecure false"]
    lines += [
        "cleansession true",
        "try_private true",
        "start_type automatic",
        "restart_timeout 5 60",
        "notifications true",
        f"notification_topic {rp}rv/broker/bridge",
    ]

    def topics(csv: str, direction: str) -> None:
        for t in filter(None, (x.strip() for x in csv.split(","))):
            lines.append(f'topic {t} {direction} 0 "" {rp}' if rp else f"topic {t} {direction} 0")

    topics(b.out_topics, "out")
    topics(b.in_topics, "in")
    return "\n".join(lines) + "\n"


def apply_bridge(cfg: Config, path: Path = BRIDGE_CONF, restart: bool = True) -> bool:
    """브리지 파일을 다시 쓰고, 내용이 바뀌었으면 mosquitto를 재시작한다. 바뀌었으면 True."""
    text = bridge_conf(cfg.bridge, cfg.node.device_id)
    try:
        old = path.read_text(encoding="utf-8")
    except OSError:
        old = None
    if old == text:
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    if restart:
        subprocess.run(["sudo", "-n", "/usr/bin/systemctl", "restart", "mosquitto"], check=True, timeout=30)
    return True
