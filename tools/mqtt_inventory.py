"""MQTT 엔티티 전체 목록 → docs/31-mqtt-entities.md

ls 브로커(HA가 읽는 곳)에 보관된 HA discovery를 읽어 노드 → 토픽 접두 → state·switch·control 표로 쓴다.
ls 계정은 Radxa의 /etc/rvnode.yaml(bridge)에 있으므로 Radxa에서 mosquitto_sub를 돌린다 — 비밀번호는 보드 밖으로 나오지 않는다.
HA에 올리지 않는 Radxa 장치(discovery: false)는 보드 설정으로 이 저장소의 rvnode 코드가 낼 엔티티를 만들어 붙인다.

    .venv/bin/python tools/mqtt_inventory.py [radxa@192.168.1.150] > docs/31-mqtt-entities.md
"""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

HOST = sys.argv[1] if len(sys.argv) > 1 else "radxa@192.168.1.150"

# Radxa에서 돈다: ls에서 retained discovery·rv/# 값을 읽고, 보드 설정의 node·devices를 함께 돌려준다
REMOTE = r'''
import yaml, subprocess, json
c = yaml.safe_load(open("/etc/rvnode.yaml")); b = c["bridge"]
def sub(topics, cid):
    cmd = ["mosquitto_sub", "-h", b["host"], "-p", str(b["port"]), "-u", b["username"], "-P", b["password"],
           "-v", "--retained-only", "-W", "8", "-i", cid]
    for t in topics: cmd += ["-t", t]
    out = {}
    for line in subprocess.run(cmd, capture_output=True, text=True).stdout.splitlines():
        t, _, p = line.partition(" "); out[t] = p
    return out
print(json.dumps({"disc": sub(["homeassistant/#"], "rv-inventory-d"), "vals": sub(["rv/#"], "rv-inventory-v"),
                  "board": {"node": c["node"], "devices": c["devices"]}}, ensure_ascii=False))
'''

# 노드 이름·하드웨어 — 토픽만으로는 알 수 없는 것
NODES = [
    ("rv/upower", "인버터 노드", "ESP32-S3 · UPower UP5000 (RS485)"),
    ("rv/relay", "스위치 노드", "ESP32 릴레이 4CH"),
    ("rv/remote", "IR 노드", "ESP32-S3 · MaxxFan 유선 IR"),
    ("rv/mach", "MACH 노드 (ESP)", "ESP32-S3 · MACH·JBD·TPMS (BLE) — Radxa로 옮길 예정 (docs/29)"),
    ("rv/radxa", "Radxa 노드", "Radxa ZERO 3W · rvnode (이 저장소)"),
    ("rv/broker", "ESP 브로커", "ESP32-S3 · 노드들의 브로커 + ls 브리지"),
]
KIND = {"sensor": "센서", "binary_sensor": "입력", "switch": "스위치", "number": "number", "select": "select",
        "button": "button", "text": "text", "event": "이벤트", "device_tracker": "위치"}
CONTROLS = ("number", "select", "button", "text")


def remote() -> dict:
    p = subprocess.run(["ssh", HOST, "sudo python3 -"], input=REMOTE, capture_output=True, text=True, timeout=60)
    if p.returncode:
        sys.exit(f"ssh {HOST}: {p.stderr.strip()}")
    return json.loads(p.stdout)


def entity(comp: str, d: dict) -> dict:
    g = lambda *ks: next((d[k] for k in ks if d.get(k) not in (None, "")), "")
    tpl = g("val_tpl", "value_template")
    m = re.search(r"value_json\.([A-Za-z0-9_.:\[\]]+)", tpl)
    ops = g("ops", "options", "evt_typ")
    if comp == "number":
        ops = f"{d.get('min'):g} ~ {d.get('max'):g}, {d.get('step'):g}씩"
    elif isinstance(ops, list):
        ops = " · ".join(map(str, ops))
    return {"comp": comp, "uid": g("uniq_id", "unique_id"), "name": g("name"), "stat": g("stat_t", "state_topic"),
            "cmd": g("cmd_t", "command_topic"), "attr": g("json_attr_t", "json_attributes_topic"),
            "path": m.group(1) if m else "", "unit": g("unit_of_meas", "unit_of_measurement"), "ops": ops or "",
            "dev": (d.get("dev") or d.get("device") or {}).get("name", "")}


def prefix_of(e: dict) -> str:
    t = e["stat"] or e["cmd"] or e["attr"]
    m = re.match(r"(.+?)/(state|switch/[^/]+/(state|set)|control/[^/]+/set|event/[^/]+|location)$", t)
    return m.group(1) if m else t


def radxa_unregistered(board: dict) -> list[dict]:
    """discovery: false인 Radxa 장치 — rvnode가 discovery를 켜면 낼 엔티티."""
    from rvnode import ha
    from rvnode.config import parse
    from rvnode.node import Node

    raw = {"node": board["node"], "devices": [d for d in board["devices"] if d.get("discovery") is False and d.get("enabled", True)]}
    for d in raw["devices"]:
        if d["type"] == "birdview":
            d.setdefault("params", {})["backend"] = "fake"
    n = Node(parse(raw))
    out = []
    for dev in n.devices:
        for topic, p in ha.all_configs("homeassistant", n.info, dev):
            e = entity(topic.split("/")[1], p)
            e["dev"], e["unreg"] = dev.label, True
            out.append(e)
    return out


def esc(s) -> str:
    return str(s).replace("|", "\\|")


def tables(pre: str, rows: list[dict]) -> list[str]:
    L = []
    st = [r for r in rows if r["comp"] in ("sensor", "binary_sensor", "device_tracker")]
    sw = [r for r in rows if r["comp"] == "switch"]
    ct = [r for r in rows if r["comp"] in CONTROLS]
    ev = [r for r in rows if r["comp"] == "event"]
    if st:
        L += [f"**State** — `{pre}/state` JSON", "", "| HA ID | 이름 | 종류 | 경로 | 단위 |", "|---|---|---|---|---|"]
        for r in sorted(st, key=lambda r: (r["comp"] != "sensor", r["path"] or r["attr"])):
            where = f"`{r['path']}`" if r["path"] else f"`{r['attr']}` (속성)"
            L.append(f"| `{r['uid']}` | {esc(r['name'])} | {KIND[r['comp']]} | {where} | {esc(r['unit'])} |")
        L.append("")
    if sw:
        L += ["**Switch** — `ON` / `OFF`", "", "| HA ID | 이름 | state 토픽 | set 토픽 |", "|---|---|---|---|"]
        for r in sorted(sw, key=lambda r: r["cmd"]):
            L.append(f"| `{r['uid']}` | {esc(r['name'])} | `{r['stat']}` | `{r['cmd']}` |")
        L.append("")
    if ct:
        L += ["**Control** — number = 숫자, select = 선택지, button = `PRESS`, text = 23자 이내", "",
              "| HA ID | 이름 | 종류 | set 토픽 | 현재 값 (state 경로) | 범위 · 선택지 |", "|---|---|---|---|---|---|"]
        for r in sorted(ct, key=lambda r: r["cmd"]):
            cur = f"`{r['path']}`" if r["path"] else "—"
            rng = esc(r["ops"]) + (f" {esc(r['unit'])}" if r["unit"] else "")
            L.append(f"| `{r['uid']}` | {esc(r['name'])} | {r['comp']} | `{r['cmd']}` | {cur} | {rng} |")
        L.append("")
    if ev:
        L += ["**Event** — 보관하지 않는다", "", "| HA ID | 이름 | 토픽 | 이벤트 |", "|---|---|---|---|"]
        for r in ev:
            L.append(f"| `{r['uid']}` | {esc(r['name'])} | `{r['stat']}` | {esc(r['ops'])} |")
        L.append("")
    return L


def main() -> None:
    data = remote()
    vals = data["vals"]
    rows = []
    for topic, payload in data["disc"].items():
        if not topic.endswith("/config") or not payload.strip():
            continue
        try:
            rows.append(entity(topic.split("/")[1], json.loads(payload)))
        except ValueError:
            continue
    for e in rows:
        e["unreg"] = False
    rows += radxa_unregistered(data["board"])
    by_pre: dict[str, list[dict]] = defaultdict(list)
    for e in rows:
        by_pre[prefix_of(e)].append(e)

    today = datetime.date.today().isoformat()
    n_ha = sum(not e["unreg"] for e in rows)
    L = [
        "# 31. MQTT 엔티티 전체 목록",
        "",
        f"> **{today} 기준, 생성한 문서** — `.venv/bin/python tools/mqtt_inventory.py > docs/31-mqtt-entities.md`로 다시 만든다. 손으로 고치지 않는다.",
        "> 출처: ls 브로커(HA가 읽는 곳)에 보관된 HA discovery. HA에 올리지 않는 Radxa 장치(discovery: false)는 보드 설정으로",
        "> rvnode 코드가 낼 엔티티를 붙였다. 토픽 규칙의 근거는 rv-controller docs/07 §B.",
        "",
        f"HA 엔티티 **{n_ha}개**, HA 미등록 Radxa 장치까지 **{len(rows)}개**.",
        "",
        "## 토픽 규칙",
        "",
        "| 토픽 | 방향 | 내용 |",
        "|---|---|---|",
        "| `<base>/status` | 노드 → | 노드 전체 `online` / `offline` (LWT, retain) |",
        "| `<prefix>/state` | 노드 → | 측정값 JSON 한 덩어리 (retain). **센서는 따로 토픽이 없고 이 JSON의 경로를 읽는다** |",
        "| `<prefix>/availability` | 노드 → | 장치별 `online` / `offline` (3회 연속 실패 시 offline) |",
        "| `<prefix>/meta` | 노드 → | 디스플레이용 종류·이름·스위치 목록·대표 센서 |",
        "| `<prefix>/switch/<이름>/state` | 노드 → | `ON` / `OFF` (retain) |",
        "| `<prefix>/switch/<이름>/set` | → 노드 | `ON` 켜기, 그 밖은 끄기. 보관하지 않는다 |",
        "| `<prefix>/control/<이름>/set` | → 노드 | number = 숫자, select = 선택지, button = `PRESS`, text. 현재 값은 state JSON |",
        "| `<prefix>/event/<이름>` | 노드 → | `{\"event_type\": …}` 순간 이벤트. 보관하지 않는다 |",
        "| `<base>/sys/cmd` | → 노드 | `restart` / `rediscover` |",
        "| `homeassistant/<종류>/<장치ID>_<slug>/<이름>/config` | 노드 → | HA discovery. **HA ID(uniq_id) = `<장치ID>_<slug>_<이름>`** |",
        "",
        "## 노드",
        "",
        "| 노드 | base | 상태 | 하드웨어 | 장치 ID | State | Switch | Control |",
        "|---|---|---|---|---|---|---|---|",
    ]
    known = [b for b, _, _ in NODES]
    others = sorted({p.split("/")[0] + "/" + p.split("/")[1] for p in by_pre if "/".join(p.split("/")[:2]) not in known})
    nodes = NODES + [(b, b, "") for b in others]

    def node_rows(base):
        return [e for p, es in by_pre.items() if p == base or p.startswith(base + "/") for e in es]

    for base, name, hw in nodes:
        es = node_rows(base)
        ids = sorted({e["uid"].split("_")[0] for e in es})
        c = lambda f: sum(1 for e in es if f(e))
        L.append(f"| [{name}](#{base.replace('/', '')}) | `{base}` | {vals.get(base + '/status', '—')} | {hw} | {', '.join(ids) or '—'} | "
                 f"{c(lambda e: e['comp'] in ('sensor', 'binary_sensor', 'device_tracker'))} | {c(lambda e: e['comp'] == 'switch')} | "
                 f"{c(lambda e: e['comp'] in CONTROLS)} |")
    disp = sorted(t[: -len("/status")] for t in vals if t.endswith("/status") and t.count("/") == 2
                  and t[: -len("/status")] not in known and not node_rows(t[: -len("/status")]))
    if disp:
        L += ["", "엔티티가 없는 노드(status만): " + ", ".join(f"`{d}` ({vals[d + '/status']})" for d in disp)]
    L.append("")

    for i, (base, name, hw) in enumerate(nodes, 1):
        es = node_rows(base)
        L += [f"## {i}. {name} — `{base}` <a id=\"{base.replace('/', '')}\"></a>", ""]
        if hw:
            L += [f"{hw}. 노드 상태 `{base}/status` = {vals.get(base + '/status', '—')}.", ""]
        extra = sorted(t for t in vals if t.startswith(base + "/") and not re.search(r"/(state|availability|meta|status)$|/switch/", t))
        if extra:
            L += ["엔티티가 아닌 토픽: " + ", ".join(f"`{t}`" for t in extra), ""]
        if not es:
            L += ["HA 엔티티를 내지 않는다.", ""]
            continue
        for pre in sorted({prefix_of(e) for e in es}):
            pes = by_pre[pre]
            unreg = all(e["unreg"] for e in pes)
            label = pes[0]["dev"]
            av = vals.get(pre + "/availability", "—")
            L += [f"### `{pre}` — {label}" + (" (HA 미등록)" if unreg else ""), "",
                  f"availability = {av}" + (". discovery: false — HA에 올리지 않는다. 켜면 아래 ID로 올라간다" if unreg else "") + ".", ""]
            L += tables(pre, pes)
    print("\n".join(L).rstrip() + "\n")


if __name__ == "__main__":
    main()
