"""웹 설정·상태 페이지 (aiohttp). http://radxa.local/

GET  /              페이지
GET  /api/status    노드·장치 상태
GET  /api/config    설정 (비밀번호는 비워서)
PUT  /api/config    설정 저장 → 브리지 파일 갱신(바뀌면 mosquitto 재시작) → rvnode 재시작(systemd가 다시 띄움)
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
    devices = [
        {"slug": d.slug, "type": d.type, "label": d.label, "prefix": d.prefix, "online": d.online,
         "last_error": d.last_error, "state": d.state()}
        for d in node.devices
    ]
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
#msg{font-size:14px}.dev{border-top:1px solid var(--line);padding-top:12px;margin-top:12px}
.hint{font-size:12px;color:var(--muted)}
</style></head><body><main>
<header><h1>rvnode 설정</h1><div class="hint" id="sub"></div></header>
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
async function status(){
  try{
    const s=await (await fetch("api/status")).json();const y=s.sys;
    $("#sub").textContent=`${y.hostname} · ${y.ip||"?"} · rvnode ${y.fw}`;
    let h=`<table><tr><td>MQTT</td><td><span class="pill ${s.mqtt_connected?"on":"off"}">${s.mqtt_connected?"연결됨":"끊김"}</span> ${esc(y.mqtt_broker)}</td></tr><tr><td>가동 시간</td><td>${Math.floor(y.uptime_s/60)}분</td></tr><tr><td>CPU 온도</td><td>${y.cpu_temp??"?"}°C</td></tr><tr><td>부하</td><td>${y.load1??"?"}</td></tr></table>`;
    for(const d of s.devices)h+=`<div class="dev"><b>${esc(d.label)}</b> <span class="pill ${d.online?"on":"off"}">${d.online?"정상":"오프라인"}</span> <span class="hint">${esc(d.prefix)}${d.last_error?" · "+esc(d.last_error):""}</span><pre>${esc(JSON.stringify(d.state,null,2))}</pre></div>`;
    $("#status").innerHTML=h;
  }catch(e){$("#status").textContent="연결 안 됨"}
}
$("#save").onclick=()=>{try{save(collect())}catch(e){$("#msg").innerHTML=`<span class="off">JSON 칸 오류: ${esc(e.message)}</span>`}};
$("#saveraw").onclick=()=>{try{save(JSON.parse($("#raw").value))}catch(e){$("#msg").innerHTML=`<span class="off">${esc(e.message)}</span>`}};
$("#reload").onclick=load;
load();status();setInterval(status,5000);
</script></body></html>"""
