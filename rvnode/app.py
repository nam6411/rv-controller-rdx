"""진입점: python -m rvnode [-c 설정] [--only slug,...] [--log-level debug]"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

from . import config
from .node import Node

DEFAULT_CONFIG = "/etc/rvnode.yaml"


def _setup_logging(level: str) -> None:
    # systemd journal이 시각을 붙이므로 서비스로 돌 때는 시각을 뺀다
    fmt = "%(levelname)s %(name)s: %(message)s" if os.environ.get("INVOCATION_ID") else "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format=fmt)


async def _main(node: Node) -> int:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, node.stop, 0)
    return await node.run()


def cli() -> None:
    ap = argparse.ArgumentParser(prog="rvnode")
    ap.add_argument("-c", "--config", default=os.environ.get("RVNODE_CONFIG", DEFAULT_CONFIG))
    ap.add_argument("--only", help="이 장치(slug)만 켠다, 쉼표 구분 — 작업대 시험용")
    ap.add_argument("--log-level", help="설정의 log_level 대신")
    args = ap.parse_args()

    try:
        cfg = config.load(args.config)
    except (OSError, config.ConfigError) as e:
        print(f"rvnode: config {args.config}: {e}", file=sys.stderr)
        sys.exit(2)
    _setup_logging(args.log_level or cfg.log_level)
    only = set(filter(None, (args.only or "").split(","))) or None
    sys.exit(asyncio.run(_main(Node(cfg, only))))
