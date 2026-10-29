"""Radxa ZERO 3W 베이스보드 — 그림 페이지(docs/artifacts/radxa-baseboard.html)의 배치 데이터를 검사하고 docs/18 §12.4 표를 만든다.

    python3 tools/radxa_baseboard.py            # 검사 + §12.4 표(마크다운)를 표준 출력으로
    python3 tools/radxa_baseboard.py --check    # 검사만

배치의 원본은 그림 페이지 안의 `const D = {...}`(parts·pinnet·runs)다. 배치를 고치면 그림 페이지를 고치고
사본을 docs/artifacts에 다시 받은 뒤 이 스크립트로 표를 다시 만든다.
검사: 한 구멍에 한 핀·한 네트, 맨선이 다른 네트 구멍을 지나지 않음, 네트마다 핀이 모두 이어짐, 판 범위.
점퍼(jumpers)는 피복선이라 지나가는 구멍을 차지하지 않고 두 끝만 잇는다.
구멍 이름은 판 실크 그대로 열 글자-행 글자, 예: K-L. 행은 위에서부터 A~Z, 열은 오른쪽 끝부터 A~Z 다음
두 번째 바퀴 A′~E′(실크는 A~E를 다시 찍었다). 좌표 (col,row)는 1부터, col 1이 윗면 왼쪽 끝.
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "docs", "artifacts", "radxa-baseboard.html")

html = open(SRC, encoding="utf-8").read()
D = json.loads(re.search(r"const D = (\{.*?\});\n", html).group(1))
NET = {k: (label, grp) for k, label, grp in
       re.findall(r'"([\w+]+)":\["([^"]+)","(\w+)"\]', re.search(r"const NET = \{(.*?)\n\};", html, re.S).group(1))}
COLS, ROWS, RN = D["cols"], D["rows"], D["rowNames"]
CSEQ = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + [x + "′" for x in "ABCDE"]
cn = lambda c: CSEQ[COLS - c]
hn = lambda c, r: f"{cn(c)}-{RN[r-1]}"

errors = []

# ---- 핀 → 구멍
pin_at = {}                                  # (c,r) → "REF.PIN"
for p in D["parts"]:
    for pin, (c, r) in p["pins"].items():
        key = f'{p["ref"]}.{pin}'
        if not (1 <= c <= COLS and 1 <= r <= ROWS):
            errors.append(f"{key}: 판 밖 {c},{r}")
        if (c, r) in pin_at:
            errors.append(f"{hn(c, r)}: {pin_at[(c, r)]}와 {key}가 겹친다")
        pin_at[(c, r)] = key
net_of_pin = D["pinnet"]
for key in net_of_pin:
    if key not in pin_at.values():
        errors.append(f"pinnet {key}: 그런 핀이 없다")

# ---- 맨선이 지나는 구멍
hole_net = {}
for (c, r), key in pin_at.items():
    if key in net_of_pin:
        hole_net[(c, r)] = net_of_pin[key]
def cells(run):
    (ac, ar), (bc, br) = run["a"], run["b"]
    if ac != bc and ar != br:
        errors.append(f'{hn(ac, ar)}–{hn(bc, br)}: 가로·세로가 아니다')
    return [(c, r) for c in range(min(ac, bc), max(ac, bc) + 1) for r in range(min(ar, br), max(ar, br) + 1)]
for run in D["runs"]:
    for h in cells(run):
        if h in pin_at and pin_at[h] not in net_of_pin:
            errors.append(f'{run["net"]}: {hn(*h)}의 연결 없는 핀 {pin_at[h]}을 지난다')
        if hole_net.setdefault(h, run["net"]) != run["net"]:
            errors.append(f'{run["net"]}: {hn(*h)}에서 {hole_net[h]}와 겹친다')

# ---- 네트마다 이어졌는지 (맨선 구멍 + 핀 구멍, 맨선 안에서만 연결)
parent = {}
def find(x):
    while parent.setdefault(x, x) != x:
        parent[x] = parent[parent[x]]; x = parent[x]
    return x
for run in D["runs"]:
    cs = cells(run)
    for h in cs[1:]:
        parent[find(h)] = find(cs[0])
for j in D["jumpers"]:
    for end in (j["a"], j["b"]):
        h = tuple(end)
        if hole_net.get(h) != j["net"]:
            errors.append(f'점퍼 {j["net"]}: {hn(*h)}가 같은 네트의 핀·맨선이 아니다')
    parent[find(tuple(j["b"]))] = find(tuple(j["a"]))
# 부품 안에서 이미 이어진 핀 — Radxa의 GND·5V 핀
INTERNAL = [["J5.9", "J5.14", "J5.20", "J5.25", "J5.34"], ["J5.2", "J5.4"]]
hole_of = {k: h for h, k in pin_at.items()}
for group in INTERNAL:
    for k in group[1:]:
        parent[find(hole_of[k])] = find(hole_of[group[0]])
for net in sorted(set(net_of_pin.values())):
    holes = [h for h, k in pin_at.items() if net_of_pin.get(k) == net]
    roots = {find(h) for h in holes}
    if len(roots) > 1:
        groups = {}
        for h in holes: groups.setdefault(find(h), []).append(pin_at[h])
        errors.append(f"{net}: 끊겼다 — " + " / ".join(" ".join(g) for g in groups.values()))

if errors:
    print("\n".join("오류: " + e for e in errors), file=sys.stderr)
    sys.exit(1)
print(f"검사 통과: 부품 {len(D['parts'])}개, 네트 {len(set(net_of_pin.values()))}개, 맨선 {len(D['runs'])}가닥, 점퍼 {len(D['jumpers'])}개",
      file=sys.stderr)
if "--check" in sys.argv:
    sys.exit(0)

# ---- 배치도 (윗면에서 본 다리 위치)
SHORT = {"J4": {"RX": "4rx", "TX": "4tx", "GND": "4g"},
         "J7": {"CSN": "7cs", "VCC": "7vc", "MOSI": "7mo", "MISO": "7mi", "SCK": "7sk", "GND": "7g"},
         "J8": {"SCL": "8cl", "SDA": "8da"},
         "J6": {"5V": "6v", "GND": "6g", "TX": "6tx", "RX": "6rx"},
         "J1": {"+12V": "1+", "GND": "1g"},
         "J3": {"G13": "3g", "L": "3L", "R": "3R", "REV": "3rv", "CAR": "3c"},
         "J2": {"REAR": "2re", "LEFT": "2le", "RIGHT": "2ri", "AVM12V": "2av", "ACC": "2ac"},
         "PS1": {"OUT+": "o+", "OUT-": "o-", "IN+": "i+", "IN-": "i-"}}
def short(key):
    ref, pin = key.split(".")
    if ref == "J5": return f"#{pin}"
    if ref in SHORT: return SHORT[ref][pin]
    if ref.startswith("U"): return ref + pin
    return ref
out = ["```",
       " 표기: #n = J5 소켓 n번 핀,  U41 = U4의 1번 핀 (PC817: 1 A, 2 K, 3 E, 4 C),",
       "       7cs/7vc/7mo/7mi/7sk/7g = J7 CSN/VCC/MOSI/MISO/SCK/GND,  8cl/8da = J8 SCL/SDA,  4rx/4tx/4g = J4,  6v/6g/6tx/6rx = J6 5V/GND/TX/RX,",
       "       1+/1g = J1 +12V/GND,  3g/3L/3R/3rv/3c = J3 G13/L/R/REV/CAR,  2re/2le/2ri/2av/2ac = J2 REAR/LEFT/RIGHT/AVM12V/ACC,",
       "       o+/o-/i+/i- = PS1 OUT+/OUT−/IN+/IN−.  저항·다이오드·콘덴서는 다리 두 구멍에 같은 기호",
       "     " + "".join(f"{cn(c):>4}" for c in range(1, COLS + 1))]
for r in range(1, ROWS + 1):
    out.append(f"  {RN[r-1]}  " + "".join(f"{short(pin_at[(c, r)]) if (c, r) in pin_at else '·':>4}" for c in range(1, COLS + 1)) + f" {RN[r-1]}")
out.append("```")

# ---- 부품표
out += ["", "| 부품 | 값 | 다리 위치 |", "|---|---|---|"]
for p in D["parts"]:
    if p["ref"] == "J5":
        holes = "홀수 핀 1·3…39 = E-E→X-E, 짝수 핀 2·4…40 = E-F→X-F"
    else:
        holes = " ".join(f"{pin} `{hn(c, r)}`" for pin, (c, r) in p["pins"].items())
    out.append(f'| {p["ref"]} | {p["value"]} | {holes} |')

# ---- 배선 목록 (그림 페이지 순서표와 같은 순서: NET 순, 그 안에서 runs 순)
n_j = len(D["jumpers"])
out += ["", f"#### 배선 목록 (맨선 {len(D['runs'])}가닥" + (f" + 피복선 점퍼 {n_j}가닥)" if n_j else ")"), "", "| 네트 | 가닥 | 시작 | 끝 |", "|---|---|---|---|"]
def end(c, r):
    return f"`{hn(c, r)}`" + (f" {pin_at[(c, r)]}" if (c, r) in pin_at else "")
for net in NET:
    for run in (x for x in D["runs"] if x["net"] == net):
        (ac, ar), (bc, br) = run["a"], run["b"]
        dirn = "가로" if ar == br else "세로"
        out.append(f"| {NET[net][0]} | {dirn} {abs(ac - bc) + abs(ar - br)} | {end(ac, ar)} | {end(bc, br)} |")
    for j in (x for x in D["jumpers"] if x["net"] == net):
        out.append(f"| {NET[net][0]} | 피복선 | {end(*j['a'])} | {end(*j['b'])} |")
print("\n".join(out))
