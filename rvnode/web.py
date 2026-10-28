"""웹 설정·상태 페이지 (aiohttp). http://radxa.local/

GET  /              페이지
GET  /api/status    노드·장치 상태
GET  /api/config    설정 (비밀번호는 비워서)
PUT  /api/config    설정 저장 → 브리지 파일 갱신(바뀌면 mosquitto 재시작) → rvnode 재시작(systemd가 다시 띄움)
POST /api/device/{slug}/{switch|control}/{name}   장치 명령 (MQTT …/set과 같은 큐). 본문 = 값 (ON/OFF, PRESS, 선택지)
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

from . import settings
from .config import ConfigError, load_raw

if TYPE_CHECKING:
    from .node import Node

log = logging.getLogger("rvnode.web")


def make_app(node: Node) -> web.Application:
    app = web.Application(middlewares=[_auth(node)], client_max_size=256 * 1024)
    app["node"] = node
    app.router.add_get("/", _index)
    app.router.add_get("/api/status", _status)
    app.router.add_get("/api/config", _get_config)
    app.router.add_put("/api/config", _put_config)
    app.router.add_post("/api/device/{slug}/{kind}/{name}", _command)
    return app


def _auth(node: Node):
    @web.middleware
    async def mw(request: web.Request, handler):
        pw = node.cfg.web.password
        if pw:
            hdr = request.headers.get("Authorization", "")
            ok = False
            if hdr.startswith("Basic "):
                try:
                    ok = base64.b64decode(hdr[6:]).decode().split(":", 1)[1] == pw
                except Exception:
                    ok = False
            if not ok:
                return web.Response(status=401, headers={"WWW-Authenticate": 'Basic realm="rvnode"'})
        return await handler(request)

    return mw


async def _index(request: web.Request) -> web.Response:
    return web.Response(text=PAGE, content_type="text/html", charset="utf-8")


async def _status(request: web.Request) -> web.Response:
    node: Node = request.app["node"]
    running = {d.slug: d for d in node.devices}
    devices = []
    for c in node.cfg.devices:  # 설정에서 꺼 둔 장치도 카드는 보인다 (값 없이)
        d = running.get(c.slug)
        if d is None:
            devices.append({"slug": c.slug, "type": c.type, "label": c.label or c.slug, "prefix": c.prefix,
                            "enabled": False, "online": False, "last_error": "", "state": {}})
        else:
            devices.append({"slug": d.slug, "type": d.type, "label": d.label, "prefix": d.prefix, "enabled": True,
                            "online": d.online, "last_error": d.last_error, "state": d.state()})
    return web.json_response({"sys": node.sys_info(), "mqtt_connected": node.bus.connected, "devices": devices})


async def _get_config(request: web.Request) -> web.Response:
    node: Node = request.app["node"]
    return web.json_response(settings.redact(load_raw(node.cfg_path)))


async def _put_config(request: web.Request) -> web.Response:
    node: Node = request.app["node"]
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "JSON이 아니다"}, status=400)
    current = load_raw(node.cfg_path)
    new = settings.merge_update(current, body)
    try:
        cfg = settings.save_raw(node.cfg_path, new)
    except (ConfigError, ValueError, TypeError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
    bridge_changed = False
    try:
        bridge_changed = settings.apply_bridge(cfg)
    except Exception as e:  # 설정은 저장됐다 — 브리지만 실패
        log.exception("bridge apply failed")
        return web.json_response({"ok": False, "error": f"설정은 저장했지만 브리지 적용 실패: {e}"}, status=500)
    log.warning("config saved via web (bridge %s) — restarting", "changed" if bridge_changed else "same")
    asyncio.get_running_loop().call_later(1.0, node.stop, 1)
    return web.json_response({"ok": True, "bridge_changed": bridge_changed, "restart": True})


async def _command(request: web.Request) -> web.Response:
    node: Node = request.app["node"]
    slug, kind, name = request.match_info["slug"], request.match_info["kind"], request.match_info["name"]
    dev = next((d for d in node.devices if d.slug == slug), None)
    if dev is None or kind not in ("switch", "control"):
        return web.json_response({"ok": False, "error": "그런 장치·명령이 없다"}, status=404)
    value = (await request.text()).strip() or "PRESS"
    try:
        dev.commands.put_nowait((kind, name, value))
    except asyncio.QueueFull:
        return web.json_response({"ok": False, "error": "명령 대기열이 꽉 찼다"}, status=503)
    return web.json_response({"ok": True})


async def serve(node: Node) -> None:
    w = node.cfg.web
    runner = web.AppRunner(make_app(node), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, w.bind, w.port)
    await site.start()
    log.info("web: http://%s:%d/", w.bind, w.port)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>rvnode 설정</title>
<style>
:root{--bg:#f4f5f2;--card:#fff;--fg:#1d211f;--muted:#66706b;--line:#d9ddd8;--accent:#1f6f5c;--bad:#b3261e;--ok:#2e7d32}
@media (prefers-color-scheme:dark){:root{--bg:#121514;--card:#1b1f1d;--fg:#e4e8e5;--muted:#9aa49f;--line:#2c322f;--accent:#58c4a6;--bad:#f28b82;--ok:#81c995}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Apple SD Gothic Neo",sans-serif}
main{max-width:980px;margin:0 auto;padding:20px 16px 60px;display:grid;gap:18px}
h1{font-size:22px;margin:0}h2{font-size:16px;margin:0 0 10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px;min-width:0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px 14px}
label{display:grid;gap:3px;font-size:13px;color:var(--muted)}
input,select,textarea{font:inherit;color:var(--fg);background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:6px 8px;width:100%}
textarea{font-family:ui-monospace,Menlo,monospace;font-size:12px;min-height:90px}
label.chk{display:flex;align-items:center;gap:8px;color:var(--fg)}label.chk input{width:auto}
table{border-collapse:collapse;width:100%;font-size:13px}td{border-bottom:1px solid var(--line);padding:4px 6px;vertical-align:top}td:first-child{color:var(--muted);width:40%}
.pill{display:inline-block;padding:0 8px;border-radius:99px;font-size:12px;border:1px solid currentColor}
.on{color:var(--ok)}.off{color:var(--bad)}
pre{margin:6px 0 0;font-size:12px;overflow:auto;max-height:240px;background:var(--bg);padding:8px;border-radius:6px}
button{font:inherit;background:var(--accent);color:#fff;border:0;border-radius:6px;padding:8px 16px;cursor:pointer}
button.sec{background:transparent;color:var(--accent);border:1px solid var(--accent)}
#msg{font-size:14px}.dev{border-top:1px solid var(--line);padding-top:12px;margin-top:12px}.dev.offline .kv,.dev.offline .btns{opacity:.45}.dev.offline button{pointer-events:none}
.hint{font-size:12px;color:var(--muted)}
.sens{display:grid;grid-template-columns:minmax(0,1fr) 170px;gap:16px;align-items:start}
@media (max-width:560px){.sens{grid-template-columns:minmax(0,1fr)}}
.kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px}
.kv div{background:var(--bg);border-radius:6px;padding:6px 8px}.kv b{display:block;font-size:18px;font-variant-numeric:tabular-nums}
.kv span{font-size:12px;color:var(--muted)}
.btns{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.btns button{padding:4px 10px;font-size:13px}
</style></head><body><main>
<header><h1>rvnode 설정</h1><div class="hint" id="sub"></div></header>
<section class="card"><h2>센서 <span class="hint" id="live"></span></h2><div id="sensors">불러오는 중…</div></section>
<section class="card"><h2>상태</h2><div id="status">불러오는 중…</div></section>
<section class="card"><h2>설정</h2><form id="f"></form>
<div style="display:flex;gap:10px;align-items:center;margin-top:14px;flex-wrap:wrap">
<button id="save" type="button">저장하고 다시 시작</button><button class="sec" id="reload" type="button">되돌리기</button><span id="msg"></span></div>
<p class="hint">저장하면 rvnode가 몇 초 동안 다시 시작한다. 브리지 값이 바뀌었으면 mosquitto도 다시 시작한다. 비밀번호 칸은 비워 두면 기존 값을 그대로 둔다.</p>
</section>
<section class="card"><details><summary>고급 — 설정 전체(JSON) 직접 편집</summary>
<textarea id="raw" style="min-height:320px;margin-top:10px"></textarea>
<div style="margin-top:8px"><button class="sec" id="saveraw" type="button">이 내용으로 저장</button></div></details></section>
</main>
<script>
const $=s=>document.querySelector(s);let cfg=null;
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function field(path,label,val,type="text",hint=""){
  if(type==="bool")return `<label class="chk"><input type="checkbox" data-p="${path}" data-t="bool" ${val?"checked":""}>${esc(label)}</label>`;
  if(type==="json")return `<label style="grid-column:1/-1">${esc(label)}<textarea data-p="${path}" data-t="json">${esc(JSON.stringify(val??{},null,2))}</textarea></label>`;
  if(type==="select")return `<label>${esc(label)}<select data-p="${path}">${hint.split(",").map(o=>`<option ${o===val?"selected":""}>${o}</option>`).join("")}</select></label>`;
  const ph=type==="password"?(val?"(설정됨 — 바꿀 때만 입력)":"(없음)"):"";
  return `<label>${esc(label)}<input type="${type==="password"?"password":type}" data-p="${path}" data-t="${type}" value="${type==="password"?"":esc(val)}" placeholder="${ph}"></label>`;
}
function render(c){
  const n=c.node||{},m=c.mqtt||{},b=c.bridge||{},w=c.web||{};let h="";
  h+=`<h2>노드</h2><div class="grid">${field("node.device_id","장치 ID (HA uniq_id 앞)",n.device_id)}${field("node.base","기본 토픽",n.base)}${field("node.label","이름",n.label)}${field("log_level","로그 수준",c.log_level||"info","select","debug,info,warning,error")}</div>`;
  h+=`<h2 style="margin-top:16px">로컬 MQTT</h2><div class="grid">${field("mqtt.host","브로커 주소",m.host)}${field("mqtt.port","포트",m.port??1883,"number")}${field("mqtt.username","사용자",m.username)}${field("mqtt.password","비밀번호",m.password_set,"password")}${field("mqtt.discovery","HA discovery 발행",m.discovery??true,"bool")}</div>`;
  h+=`<h2 style="margin-top:16px">외부 브리지 (ls)</h2><div class="grid">${field("bridge.enabled","브리지 켜기",b.enabled,"bool")}${field("bridge.host","외부 브로커",b.host)}${field("bridge.port","포트",b.port??8883,"number")}${field("bridge.tls","TLS",b.tls??true,"bool")}${field("bridge.username","사용자",b.username)}${field("bridge.password","비밀번호",b.password_set,"password")}${field("bridge.remote_prefix","원격 접두어 (보통 비움)",b.remote_prefix)}${field("bridge.out_topics","밖으로 보낼 토픽",b.out_topics)}${field("bridge.in_topics","안으로 받을 토픽",b.in_topics)}</div>`;
  h+=`<h2 style="margin-top:16px">웹</h2><div class="grid">${field("web.port","포트",w.port??80,"number")}${field("web.password","접속 비밀번호 (비우면 없음)",w.password_set,"password")}</div>`;
  h+=`<h2 style="margin-top:16px">장치</h2>`;
  (c.devices||[]).forEach((d,i)=>{
    h+=`<div class="dev"><b>${esc(d.label||d.slug)}</b> <span class="hint">${esc(d.type)} · ${esc(d.slug)}</span><div class="grid" style="margin-top:8px">`;
    h+=field(`devices.${i}.enabled`,"켜기",d.enabled??true,"bool")+field(`devices.${i}.discovery`,"HA에 올리기",d.discovery??true,"bool")+field(`devices.${i}.label`,"이름",d.label)+field(`devices.${i}.prefix`,"토픽 (비우면 기본)",d.prefix);
    const p=d.params||{};const nested={};
    for(const [k,v] of Object.entries(p)){
      if(v!==null&&typeof v==="object"){nested[k]=v;continue}
      h+=field(`devices.${i}.params.${k}`,k,v,typeof v==="boolean"?"bool":typeof v==="number"?"number":"text");
    }
    for(const [k,v] of Object.entries(nested))h+=field(`devices.${i}.params.${k}`,`${k} (JSON)`,v,"json");
    h+=`</div></div>`;
  });
  $("#f").innerHTML=h;$("#raw").value=JSON.stringify(c,null,2);
}
function collect(){
  const c=JSON.parse(JSON.stringify(cfg));
  document.querySelectorAll("#f [data-p]").forEach(el=>{
    const keys=el.dataset.p.split(".");let o=c;
    for(let i=0;i<keys.length-1;i++){const k=/^\d+$/.test(keys[i])?+keys[i]:keys[i];o[k]??={};o=o[k]}
    const last=keys[keys.length-1],t=el.dataset.t;let v;
    if(t==="bool")v=el.checked;else if(t==="number")v=el.value===""?null:Number(el.value);
    else if(t==="json"){v=JSON.parse(el.value||"{}")}else v=el.value;
    if(t==="number"&&v===null){delete o[last];return}
    o[last]=v;
  });
  return c;
}
async function load(){
  const r=await fetch("api/config");cfg=await r.json();render(cfg);
}
async function save(body){
  $("#msg").textContent="저장 중…";
  try{
    const r=await fetch("api/config",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const j=await r.json();
    if(!j.ok){$("#msg").innerHTML=`<span class="off">${esc(j.error)}</span>`;return}
    $("#msg").textContent=`저장했다${j.bridge_changed?" (브리지 바뀜, mosquitto 재시작)":""}. 다시 시작하는 중…`;
    setTimeout(async function wait(){try{await fetch("api/status");$("#msg").innerHTML='<span class="on">다시 시작했다.</span>';load();status()}catch(e){setTimeout(wait,1500)}},3000);
  }catch(e){$("#msg").innerHTML=`<span class="off">${esc(e.message)}</span>`}
}
const f=(v,d=1)=>v===null||v===undefined?"–":(typeof v==="number"?v.toFixed(d):esc(v));
async function cmd(slug,kind,name,val){await fetch(`api/device/${slug}/${kind}/${name}`,{method:"POST",body:val||"PRESS"});setTimeout(status,300)}
function compass(h){
  if(h===null||h===undefined)return `<svg viewBox="0 0 160 160" width="160" height="160"><circle cx="80" cy="80" r="70" fill="none" stroke="var(--line)" stroke-width="2"/><text x="80" y="86" text-anchor="middle" fill="var(--muted)" font-size="14">나침반 없음</text></svg>`;
  return `<svg viewBox="0 0 160 160" width="160" height="160"><circle cx="80" cy="80" r="70" fill="none" stroke="var(--line)" stroke-width="2"/>
  ${["N","E","S","W"].map((t,i)=>`<text x="${80+60*Math.sin(i*Math.PI/2)}" y="${85-60*Math.cos(i*Math.PI/2)}" text-anchor="middle" font-size="13" fill="${i?"var(--muted)":"var(--bad)"}" font-weight="700">${t}</text>`).join("")}
  <g transform="rotate(${h} 80 80)"><path d="M80 22 L92 84 L80 76 L68 84 Z" fill="var(--accent)"/><path d="M80 138 L90 84 L80 92 L70 84 Z" fill="var(--line)"/></g>
  <text x="80" y="156" text-anchor="middle" font-size="13" fill="var(--fg)" font-weight="700">${Math.round(h)}°</text></svg>`;
}
function conn(d){return d.enabled===false?["off","설정에서 꺼짐"]:d.online?["on","연결됨"]:["off","연결 안 됨"]}
function head(d){const [c,t]=conn(d);return `<div class="dev ${d.online?"":"offline"}"><b>${esc(d.label)}</b> <span class="pill ${c}">${t}</span>`}
function sensorCard(d){
  const s=d.online?(d.state||{}):{ble:(d.state||{}).ble};  // 연결 안 되면 지난 값 대신 –
  if(d.type==="gps"){
    const c=s.compass||{};
    return `${head(d)} <span class="pill ${s.fix?"on":"off"}">${s.fix?"위치 잡힘":"위치 없음"}</span> <span class="hint">${esc(s.utc||"")}</span>
    <div class="sens" style="margin-top:8px"><div class="kv">
      <div><span>위도</span><b>${f(s.lat,6)}</b></div><div><span>경도</span><b>${f(s.lon,6)}</b></div>
      <div><span>위성 (사용/보임)</span><b>${s.sats??0} / ${s.sats_seen??0}</b></div><div><span>HDOP</span><b>${f(s.hdop,2)}</b></div>
      <div><span>속도</span><b>${f(s.speed_kmh)} km/h</b></div><div><span>진행 방향</span><b>${f(s.course,0)}°</b></div>
      <div><span>고도</span><b>${f(s.alt_m)} m</b></div><div><span>자기장 세기</span><b>${f(c.strength,0)}</b></div>
    </div><div>${compass(s.heading)}</div></div>
    <div class="btns"><button class="sec" onclick="cmd('${d.slug}','control','compass_cal_start')">나침반 보정 시작</button>
    <button class="sec" onclick="cmd('${d.slug}','control','compass_cal_stop')">보정 끝(저장)</button>
    ${c.calibrating?'<span class="pill on">보정 중 — 차를 한 바퀴 천천히 돌린다</span>':""}</div></div>`;
  }
  if(d.type==="birdview"){
    const i=s.in||{},o=s.out||{};const p=(on,t)=>`<span class="pill ${on?"on":"off"}" style="margin-right:4px">${t}</span>`;
    return `${head(d)} ${p(s.powered,s.powered?"전원 켜짐":"전원 꺼짐")} <span class="hint">시점 ${esc(s.view)} · 모드 ${esc(s.mode)}</span>
    <div style="margin-top:8px">입력 ${p(i.ign,"시동")}${p(i.left,"좌 깜빡이")}${p(i.right,"우 깜빡이")}${p(i.reverse,"후진")}</div>
    <div style="margin-top:4px">출력 ${p(o.rear,"후방")}${p(o.left_out,"좌측")}${p(o.right_out,"우측")}</div>
    <div class="btns">${["auto","rear","left","right","none"].map(m=>`<button class="${m===s.mode?"":"sec"}" onclick="cmd('${d.slug}','control','mode','${m}')">${m}</button>`).join("")}
    <button class="${s.keep_on?"":"sec"}" onclick="cmd('${d.slug}','switch','power','${s.keep_on?"OFF":"ON"}')">계속 켜기 ${s.keep_on?"끄기":"켜기"}</button></div></div>`;
  }
  if(d.type==="jbd"){
    const b=s.ble||{},cells=s.cell_v||[],mn=Math.min(...cells),mx=Math.max(...cells);
    const bar=v=>{const pct=Math.max(0,Math.min(100,(v-2.8)/(3.65-2.8)*100));const hi=cells.length>1&&v===mx,lo=cells.length>1&&v===mn;
      return `<div style="display:grid;grid-template-columns:52px 1fr 64px;gap:6px;align-items:center;font-size:13px"><span class="hint">${cells.indexOf(v)+1}</span><div style="background:var(--bg);border-radius:4px;height:12px"><div style="width:${pct}%;height:12px;border-radius:4px;background:${hi?"var(--bad)":lo?"var(--muted)":"var(--accent)"}"></div></div><b style="font-variant-numeric:tabular-nums">${v.toFixed(3)} V</b></div>`};
    const cur=s.current??0;
    return `${head(d)} <span class="hint">${esc(b.name||"")} ${esc(b.address||"")}${d.last_error?" · "+esc(d.last_error):""}</span>
    <div class="sens" style="margin-top:8px"><div class="kv">
      <div><span>SOC</span><b>${f(s.soc,0)} %</b></div><div><span>전압</span><b>${f(s.pack_v,2)} V</b></div>
      <div><span>전류 (${cur>0?"충전":cur<0?"방전":"대기"})</span><b>${f(s.current,2)} A</b></div><div><span>전력</span><b>${f(s.power,0)} W</b></div>
      <div><span>남은 / 전체</span><b>${f(s.remain_ah,1)} / ${f(s.full_ah,0)} Ah</b></div><div><span>사이클</span><b>${f(s.cycles,0)}</b></div>
      <div><span>셀 편차</span><b>${f((s.cell_diff??0)*1000,0)} mV</b></div><div><span>온도</span><b>${(s.ntc||[]).map(t=>t.toFixed(1)).join(" · ")||"–"} °C</b></div>
      <div><span>보호 비트</span><b>0x${(s.protection??0).toString(16).padStart(4,"0")}</b></div><div><span>밸런싱</span><b>0x${(s.balance??0).toString(16)}</b></div>
    </div><div style="display:grid;gap:4px">${cells.map(bar).join("")||'<span class="hint">셀 값 없음</span>'}</div></div>
    <div class="btns">
      <button class="${s.chg_fet?"":"sec"}" onclick="if(confirmFet('충전',${!s.chg_fet}))cmd('${d.slug}','switch','charge_fet','${s.chg_fet?"OFF":"ON"}')">충전 FET ${s.chg_fet?"켜짐":"꺼짐"}</button>
      <button class="${s.dis_fet?"":"sec"}" onclick="if(confirmFet('방전',${!s.dis_fet}))cmd('${d.slug}','switch','discharge_fet','${s.dis_fet?"OFF":"ON"}')">방전 FET ${s.dis_fet?"켜짐":"꺼짐"}</button>
    </div></div>`;
  }
  if(d.type==="mach"){
    const b=s.ble||{},e=s.ebl||{},a=s.alde||{},ac=s.ac||{},g=s.accel||{},live=!!s.live;
    const sw=(n,on,t,ok=live)=>`<button class="${on?"":"sec"}" ${ok?"":"disabled"} onclick="cmd('${d.slug}','switch','${n}','${on?"OFF":"ON"}')">${t} ${on?"켜짐":"꺼짐"}</button>`;
    const opts=(n,cur,list,ok)=>list.map(o=>`<button class="${o===cur?"":"sec"}" ${ok?"":"disabled"} onclick="cmd('${d.slug}','control','${n}','${o}')">${o}</button>`).join("");
    const step=(n,v,dv,lo,hi,ok)=>`<button class="sec" ${ok&&v>lo?"":"disabled"} onclick="cmd('${d.slug}','control','${n}','${v-dv}')">−</button><button class="sec" ${ok&&v<hi?"":"disabled"} onclick="cmd('${d.slug}','control','${n}','${v+dv}')">+</button>`;
    const cur=e.bat_a??0,aok=live&&a.set!==undefined,cok=!!ac.ok;
    return `${head(d)} <span class="pill ${live?"on":"off"}">${live?"패널 켜짐":"패널 꺼짐"}</span> <span class="hint">${esc(b.name||"")} ${esc(b.address||"")}${d.last_error?" · "+esc(d.last_error):""}</span>
    <div class="kv" style="margin-top:8px">
      <div><span>배터리 SOC (전압)</span><b>${f(e.soc,0)} %</b></div><div><span>전압</span><b>${f(e.bat_v,2)} V</b></div>
      <div><span>전류 (${cur>0?"충전":cur<0?"방전":"대기"})</span><b>${f(e.bat_a,2)} A</b></div><div><span>전력</span><b>${f(e.bat_w,0)} W</b></div>
      <div><span>청수 / 오수</span><b>${f(e.fresh,0)} / ${f(e.waste,0)} %</b></div><div><span>실내 / 실외 (EBL)</span><b>${f(e.inside,0)} / ${f(e.outside,0)} °C</b></div>
      <div><span>230V / 엔진</span><b>${e.mains?"연결":"없음"} / ${e.engine?"가동":"정지"}</b></div>
      <div><span>수평 앞뒤</span><b style="font-size:15px">${esc(g.pitch_text||"–")}</b></div><div><span>수평 좌우</span><b style="font-size:15px">${esc(g.roll_text||"–")}</b></div>
    </div>
    <div class="btns">${sw("panel",e.panel,"메인 패널",!!b.connected)}${sw("int_light",e.int_light,"실내등")}${sw("ext_light",e.ext_light,"외부등")}${sw("water_pump",e.pump,"물펌프")}</div>
    <div style="margin-top:10px"><b>Alde 보일러</b> <span class="hint">실내 ${f(a.sofa,0)}°C · 실외 ${f(a.outside,0)}°C${a.heating?" · 가열 중":""}${a.error?" · 오류":""}</span></div>
    <div class="btns">${sw("heater",a.gas,"가스",aok)} 설정 <b>${f(a.set,0)}°C</b> ${step("alde_set_temp",a.set??20,1,5,30,aok)}</div>
    <div class="btns">에너지 ${opts("alde_energy",a.energy,["off","gas","el1","el2","el3","el1_and_gas","el2_and_gas","el3_and_gas"],aok)}</div>
    <div class="btns">온수 ${opts("alde_water",a.water_text,["off","normal","boost"],aok)}</div>
    <div style="margin-top:10px"><b>에어컨</b> <span class="hint">${cok?`실내 ${f(ac.room_c,1)}°C · 설정 ${f(ac.set_c,0)}°C`:"응답 없음 (230V 필요)"+(ac.reply?" · "+esc(ac.reply):"")}</span></div>
    <div class="btns">${sw("ac",cok&&ac.mode!=="off","에어컨",cok)} 설정 ${step("ac_set_temp",Math.round(ac.set_c??22),1,16,31,cok)}</div>
    <div class="btns">모드 ${opts("ac_mode",ac.mode,["off","fan","cool","heat","auto"],cok)}</div>
    <div class="btns">바람 ${opts("ac_fan",ac.fan,["low","mid","high","night"],cok)}</div></div>`;
  }
  return `${head(d)}<pre>${esc(JSON.stringify(s,null,2))}</pre></div>`;
}
let fetArm=null;
function confirmFet(what,on){const k=what+on;if(fetArm===k){fetArm=null;return true}fetArm=k;$("#live").textContent=`· ${what} FET를 ${on?"켜려면":"끄려면"} 한 번 더 누른다`;setTimeout(()=>{if(fetArm===k)fetArm=null},4000);return false}
async function status(){
  try{
    const s=await (await fetch("api/status")).json();const y=s.sys;
    $("#sub").textContent=`${y.hostname} · ${y.ip||"?"} · rvnode ${y.fw}`;
    let h=`<table><tr><td>MQTT</td><td><span class="pill ${s.mqtt_connected?"on":"off"}">${s.mqtt_connected?"연결됨":"끊김"}</span> ${esc(y.mqtt_broker)}</td></tr><tr><td>가동 시간</td><td>${Math.floor(y.uptime_s/60)}분</td></tr><tr><td>CPU 온도</td><td>${y.cpu_temp??"?"}°C</td></tr><tr><td>부하</td><td>${y.load1??"?"}</td></tr></table>`;
    for(const d of s.devices)h+=`<div class="dev"><b>${esc(d.label)}</b> <span class="pill ${conn(d)[0]}">${conn(d)[1]}</span> <span class="hint">${esc(d.prefix)}${d.last_error?" · "+esc(d.last_error):""}</span></div>`;
    $("#status").innerHTML=h;
    $("#sensors").innerHTML=s.devices.map(sensorCard).join("")||"장치 없음";
    $("#live").textContent="· "+new Date().toLocaleTimeString();
  }catch(e){$("#status").textContent="연결 안 됨"}
}
$("#save").onclick=()=>{try{save(collect())}catch(e){$("#msg").innerHTML=`<span class="off">JSON 칸 오류: ${esc(e.message)}</span>`}};
$("#saveraw").onclick=()=>{try{save(JSON.parse($("#raw").value))}catch(e){$("#msg").innerHTML=`<span class="off">${esc(e.message)}</span>`}};
$("#reload").onclick=load;
load();status();setInterval(status,1000);
</script></body></html>"""
