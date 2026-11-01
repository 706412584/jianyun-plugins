#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""frpc 内网穿透插件 —— 常驻服务：HTTP 控制台 + 探活接口。

监听 127.0.0.1:18899（manifest 里的 servicePort），宿主靠 /health 判存活。
隧道子进程由 supervisor 单线程托管（单实例，绝不并发），本文件只管展示与控制。
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common      # noqa: E402
import supervisor  # noqa: E402

ST = supervisor.ST

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>frpc 隧道控制台</title>
<style>
  :root{
    --bg:#070b12; --panel:rgba(18,26,40,.72); --line:rgba(90,180,255,.16);
    --fg:#e6f1ff; --dim:#7f93ad; --accent:#22d3ee; --accent2:#38bdf8;
    --ok:#34d399; --warn:#fbbf24; --err:#f87171;
  }
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;background:
      radial-gradient(900px 500px at 12% -8%,rgba(34,211,238,.14),transparent 60%),
      radial-gradient(800px 460px at 96% 4%,rgba(56,189,248,.12),transparent 62%),
      linear-gradient(180deg,#070b12,#050810 60%,#04070d);
    color:var(--fg);font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
    padding:20px 16px 44px;overflow-x:hidden}
  body::before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.35;
    background-image:linear-gradient(rgba(90,180,255,.05) 1px,transparent 1px),
                     linear-gradient(90deg,rgba(90,180,255,.05) 1px,transparent 1px);
    background-size:44px 44px;mask-image:radial-gradient(ellipse at 50% 0%,#000 25%,transparent 78%)}
  .wrap{max-width:980px;margin:0 auto;position:relative;z-index:1}
  header{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:18px}
  .logo{width:44px;height:44px;border-radius:13px;flex:0 0 auto;display:grid;place-items:center;
    background:linear-gradient(145deg,rgba(34,211,238,.22),rgba(56,189,248,.06));
    border:1px solid rgba(34,211,238,.34);box-shadow:0 0 26px rgba(34,211,238,.22) inset,0 0 18px rgba(34,211,238,.12)}
  .logo svg{width:24px;height:24px;stroke:var(--accent);fill:none;stroke-width:1.8;stroke-linecap:round}
  h1{font-size:19px;margin:0;letter-spacing:.4px;font-weight:650}
  .sub{color:var(--dim);font-size:12px;margin-top:2px;letter-spacing:.3px}
  .grid{display:grid;gap:14px}
  .cards{grid-template-columns:repeat(auto-fit,minmax(210px,1fr))}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:15px 16px;
    backdrop-filter:blur(10px);box-shadow:0 10px 30px rgba(0,0,0,.35)}
  .panel h2{margin:0 0 12px;font-size:12px;letter-spacing:1.6px;color:var(--dim);font-weight:600;text-transform:uppercase}
  .badge{display:inline-flex;align-items:center;gap:8px;padding:7px 14px;border-radius:999px;
    font-size:13px;font-weight:600;letter-spacing:.4px;border:1px solid transparent}
  .dot{width:8px;height:8px;border-radius:50%;background:currentColor;box-shadow:0 0 10px currentColor}
  .live .dot{animation:pulse 1.5s ease-in-out infinite}
  @keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.35;transform:scale(.7)}}
  .st-running{color:var(--ok);background:rgba(52,211,153,.1);border-color:rgba(52,211,153,.32)}
  .st-reconnecting{color:var(--warn);background:rgba(251,191,36,.1);border-color:rgba(251,191,36,.32)}
  .st-error{color:var(--err);background:rgba(248,113,113,.1);border-color:rgba(248,113,113,.32)}
  .st-stopped,.st-starting{color:var(--dim);background:rgba(127,147,173,.1);border-color:rgba(127,147,173,.28)}
  .kv{display:grid;grid-template-columns:auto 1fr;gap:7px 14px;font-size:13px}
  .kv dt{color:var(--dim)}
  .kv dd{margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .kv dd.acc{color:var(--accent)}
  .stat{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:26px;
    color:var(--accent);text-shadow:0 0 18px rgba(34,211,238,.4);letter-spacing:.5px}
  .lbl{color:var(--dim);font-size:12px;letter-spacing:.6px}
  .logs{background:rgba(4,8,14,.82);border:1px solid var(--line);border-radius:14px;
    padding:13px 14px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
    font-size:12px;line-height:1.75;max-height:340px;overflow:auto;white-space:pre-wrap;word-break:break-all}
  .logs i{font-style:normal;color:var(--dim)}
  .logs .l-err{color:var(--err)}
  .logs .l-ok{color:var(--ok)}
  .logs .l-warn{color:var(--warn)}
  .err{margin-top:12px;padding:11px 13px;border-radius:12px;font-size:13px;
    color:#fecaca;background:rgba(248,113,113,.1);border:1px solid rgba(248,113,113,.3)}
  .btns{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}
  button{cursor:pointer;font:inherit;font-size:13px;padding:9px 17px;border-radius:11px;
    color:var(--fg);background:rgba(34,211,238,.1);border:1px solid rgba(34,211,238,.34);
    transition:.18s;letter-spacing:.3px}
  button:hover{background:rgba(34,211,238,.2);box-shadow:0 0 18px rgba(34,211,238,.22)}
  button:active{transform:translateY(1px)}
  footer{color:var(--dim);font-size:11.5px;text-align:center;margin-top:20px;letter-spacing:.4px}
  .hint{color:var(--dim);font-size:12px;margin-top:9px}
  .mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo"><svg viewBox="0 0 24 24"><path d="M4 12h4l2.5-6 3 12L16 12h4"/></svg></div>
    <div>
      <h1>frpc 隧道控制台</h1>
      <div class="sub" id="sub">frp 反向代理 · 标准协议</div>
    </div>
  </header>

  <div class="grid cards">
    <div class="panel">
      <h2>隧道状态</h2>
      <div id="badge" class="badge st-starting"><span class="dot"></span><span id="badgetext">启动中</span></div>
      <div class="hint" id="statemsg">正在启动…</div>
      <div class="btns">
        <button onclick="act('/api/restart')">重启隧道</button>
        <button onclick="act('/api/reload')">重载配置</button>
      </div>
    </div>
    <div class="panel">
      <h2>运行时长</h2>
      <div class="stat" id="uptime">—</div>
      <div class="lbl" id="pidline">进程号 —</div>
      <div class="lbl" id="restarts" style="margin-top:6px">重连次数 0</div>
    </div>
    <div class="panel">
      <h2>服务端</h2>
      <dl class="kv">
        <dt>地址</dt><dd class="acc" id="k-server">—</dd>
        <dt>用户</dt><dd id="k-user">—</dd>
        <dt>代理名</dt><dd id="k-name">—</dd>
        <dt>类型</dt><dd id="k-type">—</dd>
      </dl>
    </div>
    <div class="panel">
      <h2>转发规则</h2>
      <dl class="kv">
        <dt>本地</dt><dd class="acc" id="k-local">—</dd>
        <dt>远程端口</dt><dd id="k-remote">—</dd>
        <dt>子域名</dt><dd id="k-sub">—</dd>
        <dt>域名</dt><dd id="k-domains">—</dd>
      </dl>
    </div>
  </div>

  <div class="panel" style="margin-top:14px">
    <h2>运行环境</h2>
    <dl class="kv">
      <dt>frpc 版本</dt><dd id="k-bin">—</dd>
      <dt>配置校验</dt><dd id="k-verify">—</dd>
      <dt>配置文件</dt><dd class="mono" id="k-confpath">—</dd>
    </dl>
    <div class="hint">标准 frps 服务端：连它的 <span class="mono">bindPort</span>（宝塔面板里叫「服务器端口」），不是 SSH 网关端口。</div>
  </div>

  <div class="panel" style="margin-top:14px">
    <h2>实时日志</h2>
    <div class="logs" id="logs"><i>等待日志…</i></div>
    <div id="errbox"></div>
  </div>

  <footer>frpc 插件 · 控制台 127.0.0.1:<span id="port">18899</span> · <span id="tick">—</span></footer>
</div>

<script>
const M = {
  starting:["启动中","st-starting"], running:["隧道运行中","st-running live"],
  reconnecting:["重连中","st-reconnecting live"], error:["配置/启动异常","st-error"],
  stopped:["已停止","st-stopped"]
};
const esc = s => (s||"").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const fmtUp = s => {
  if(!s) return "—";
  const d=Math.floor(s/86400), h=Math.floor(s%86400/3600), m=Math.floor(s%3600/60), x=s%60;
  return (d?d+"d ":"") + (h||d?h+"h ":"") + m + "m " + x + "s";
};
async function act(url){
  try{ await fetch(url, {method:"POST"}); }catch(e){}
  setTimeout(refresh, 400);
}
async function refresh(){
  let d;
  try{ d = await (await fetch("/api/status", {cache:"no-store"})).json(); }
  catch(e){ document.getElementById("statemsg").textContent = "状态接口不可用"; return; }
  const st = d.state, meta = M[st] || M.stopped;
  const b = document.getElementById("badge");
  b.className = "badge " + meta[1];
  document.getElementById("badgetext").textContent = meta[0];
  document.getElementById("statemsg").textContent = d.message || "";
  document.getElementById("uptime").textContent = fmtUp(d.uptime);
  document.getElementById("pidline").textContent = "进程号 " + (d.pid || "—");
  document.getElementById("restarts").textContent = "重连次数 " + (d.restarts||0) +
      (d.last_exit===null||d.last_exit===undefined ? "" : " · 上次退出码 " + d.last_exit);
  const p = d.proxy || {};
  const set = (id, v) => document.getElementById(id).textContent = (v===""||v===undefined||v===null) ? "—" : v;
  set("k-server", p.server); set("k-user", p.user); set("k-name", p.name); set("k-type", p.type);
  set("k-local", p.local); set("k-remote", p.remote);
  set("k-sub", p.subdomain); set("k-domains", p.domains);
  set("k-bin", d.bin_version);
  set("k-verify", d.verify_note ? d.verify_note : "通过");
  document.getElementById("k-confpath").textContent = d.conf_path || "—";
  document.getElementById("port").textContent = d.port;
  const logs = (d.logs||[]).map(l => {
    const cls = /ERROR|error|failed|错误/i.test(l) ? "l-err"
              : /success|start to run|accept|login to server/i.test(l) ? "l-ok"
              : /WARN|warn/i.test(l) ? "l-warn" : "";
    return '<span class="' + cls + '">' + esc(l) + '</span>';
  }).join("\n");
  const box = document.getElementById("logs");
  const atBottom = box.scrollTop + box.clientHeight > box.scrollHeight - 30;
  box.innerHTML = logs || "<i>暂无日志</i>";
  if(atBottom) box.scrollTop = box.scrollHeight;
  document.getElementById("errbox").innerHTML =
      d.config_error ? '<div class="err">配置问题：' + esc(d.config_error) + '</div>' : "";
  document.getElementById("tick").textContent = "更新于 " + new Date().toLocaleTimeString();
}
const sub = document.getElementById("sub");
fetch("/api/status").then(r=>r.json()).then(d=>{
  sub.textContent = "frp 反向代理 · " + (d.proxy && d.proxy.server ? d.proxy.server : "未配置服务端");
}).catch(()=>{});
refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "frpc-console/1.0"

    def log_message(self, *args):        # 静音访问日志，避免污染隧道日志
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            snap = ST.snapshot()
            self._send(200, json.dumps({
                "status": "ok",
                "plugin": "frpc",
                "state": snap["state"],
                "pid": snap["pid"],
                "server": snap["proxy"].get("server", ""),
            }, ensure_ascii=False))
        elif path == "/api/status":
            snap = ST.snapshot()
            snap.pop("conf_text", None)
            self._send(200, json.dumps(snap, ensure_ascii=False))
        elif path == "/api/config":
            self._send(200, json.dumps({"frpc.toml": ST.snapshot()["conf_text"]}, ensure_ascii=False))
        elif path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        else:
            self._send(404, json.dumps({"error": "not found"}, ensure_ascii=False))

    def do_POST(self):
        path = self.path.split("?")[0]
        if path in ("/api/restart", "/api/reload"):
            supervisor.log_line("收到%s请求" % ("重启" if path.endswith("restart") else "重载配置"))
            supervisor.restart()
            self._send(200, json.dumps({"ok": True}, ensure_ascii=False))
        else:
            self._send(404, json.dumps({"error": "not found"}, ensure_ascii=False))


def main():
    settings = common.load_settings()
    with ST.lock:
        ST.proxy_desc = supervisor.summarize(settings)
        ST.bin_version = common.binary_version()
    supervisor.start_worker()
    srv = ThreadingHTTPServer(("127.0.0.1", common.PORT), Handler)
    supervisor.log_line("控制台已监听 127.0.0.1:%d" % common.PORT)
    srv.serve_forever()


if __name__ == "__main__":
    main()
