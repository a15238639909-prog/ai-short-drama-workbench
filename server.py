# -*- coding: utf-8 -*-
"""server.py — V4.1 新工作台 HTTP 服务（端口 8853，双栈）。Phase 1：Story/Narrative/State/Context/Canon。"""
import collections, json, os, re, sys, socket, threading, time, urllib.parse, webbrowser
# P449：控制台不是 UTF-8（Windows 默认 GBK）时，中文/表情日志会让服务在启动时炸掉；统一按 UTF-8 输出
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# 版本号：发布用。改功能就改这里，和 发布说明.md 对得上。
_VERSION = "v4.3.0"
PORT = int(os.environ.get("V41_PORT", "8853"))
WEB = ROOT / "web"

sys.path.insert(0, str(ROOT))
from cores import store, story_core, narrative_core, state_core, context_engine, asset_core, quality_core, runtime_core, profile_core
from cores.narrative_core import CoverageEngine
from models import gpu_manager

_LOG = collections.deque(maxlen=500)

import api
api.load_all()

# ===== 并入「本地模型工作台」(app.py，原端口 8848) 的四大模块 =====
# 用户要求：问AI / 虚拟人物 / 出图词 / 视频导演台 —— 前后端原封不动搬进 8853。
# 做法：直接 import app.py 复用它的全部后端；前端整页以 /legacy/ 前缀嵌入，
# 用 iframe 做隔离（app.py 有 7000 行共享 JS，直接混入会和 v41 冲突）。
# 关键：app.py 里所有绝对请求都是 /api/*，在 iframe 里劫持 fetch/XHR/媒体 src
# 把 /api/ 改写成 /legacy/api/，服务器再把 /legacy/* 反交给 app.Handler 处理，
# 从而与 v41 自己的 /api/* 完全不冲突。
_APP = None
try:
    # app.py 在哪：先听 config.json / 环境变量里的 legacy_root（独立包里放在
    # _legacy_8848/ 下），没配才退回父目录（原地部署时就是 D:\小说写作台）。
    # 2026-09-05：独立抽取时四大模块跟着一起走，不能再写死父目录。
    _APP_DIR = str(ROOT.parent)
    try:
        from cores import paths as _paths
        _cand = _paths.get("legacy_root")
        if _cand and os.path.exists(os.path.join(_cand, "app.py")):
            _APP_DIR = _cand
    except Exception:
        pass
    if _APP_DIR not in sys.path:
        sys.path.insert(0, _APP_DIR)
    import app as _APP
except Exception as _app_err:  # 导入失败不拖垮 v41，仅四大模块不可用
    # 旧版 8848 的四大工具是**可选**的：独立部署时根本没有 app.py，
    # 原来这里打印"导入失败"，新用户一开机就以为坏了（2026-09-05）。
    if isinstance(_app_err, ImportError) and "app" in str(_app_err):
        print("[提示] 未挂载旧版四大工具（可选，独立部署不需要）")
    else:
        print("[legacy] app.py 导入失败，四大模块暂不可用：", _app_err)
    _APP = None

# 注入 iframe 顶部的补丁：把所有 /xxx 绝对 URL 前缀成 /legacy/xxx，并隐藏 app 自带顶栏。
_LEGACY_SHIM = """<script>
(function(){
  var P='/legacy';
  function fix(u){
    if(typeof u!=='string') return u;
    if(u.charAt(0)==='/' && u.indexOf(P+'/')!==0) return P+u;
    return u;
  }
  var of=window.fetch;
  window.fetch=function(u,o){
    try{ if(u && typeof u==='object' && u.url){ u=new Request(fix(u.url),u); } else { u=fix(u); } }
    catch(e){ u=fix(u); }
    return of.call(this,u,o);
  };
  var xo=XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open=function(){ var a=[].slice.call(arguments); a[1]=fix(a[1]); return xo.apply(this,a); };
  // ① 属性赋值 el.src=/el.href=（覆盖大部分 JS 设的资源 URL）
  [window.HTMLImageElement,window.HTMLMediaElement,window.HTMLSourceElement,window.HTMLAnchorElement,window.HTMLIFrameElement].forEach(function(K){
    if(!K||!K.prototype) return;
    ['src','href'].forEach(function(prop){
      var d=Object.getOwnPropertyDescriptor(K.prototype,prop);
      if(!d||!d.set) return;
      Object.defineProperty(K.prototype,prop,{configurable:true,enumerable:d.enumerable,get:d.get,
        set:function(v){ d.set.call(this,fix(v)); }});
    });
  });
  // ② setAttribute('src'|'href'|'poster', ...)
  var attrs={src:1,href:1,poster:1};
  var sa=Element.prototype.setAttribute;
  Element.prototype.setAttribute=function(n,v){
    if(n&&attrs[n]) v=fix(v);
    return sa.call(this,n,v);
  };
  // ③ innerHTML 注入的 <img>/<video>/<a> 绕过①②——用 MutationObserver 兜底
  function fixEl(el){
    if(!el||el.nodeType!==1||!el.getAttribute) return;
    for(var a in attrs){
      if(el.hasAttribute(a)){
        var v=el.getAttribute(a);
        if(typeof v==='string'&&v.charAt(0)==='/'&&v.indexOf(P+'/')!==0) sa.call(el,a,P+v);
      }
    }
  }
  function sweep(root){
    if(!root) return; fixEl(root);
    if(root.querySelectorAll){ var ns=root.querySelectorAll('[src],[href],[poster]'); for(var i=0;i<ns.length;i++) fixEl(ns[i]); }
  }
  var mo=new MutationObserver(function(ms){
    for(var i=0;i<ms.length;i++){
      var m=ms[i];
      if(m.type==='attributes') fixEl(m.target);
      else for(var j=0;j<m.addedNodes.length;j++) sweep(m.addedNodes[j]);
    }
  });
  function startObs(){
    try{ mo.observe(document.documentElement,{subtree:true,childList:true,attributes:true,attributeFilter:['src','href','poster']}); }catch(e){}
    sweep(document.documentElement);
  }
  if(document.documentElement) startObs();
  else document.addEventListener('DOMContentLoaded',startObs);
})();
</script>
<style>header,#topbar{display:none!important}body{padding-top:0!important}</style>
"""

# 嵌入页加载后，按 ?tab= 切到对应板块（app.py 的 showTab 会各自初始化）。
_LEGACY_BOOT = """<script>(function(){
  var t=(new URLSearchParams(location.search)).get('tab')||'chat';
  function go(){ try{ if(window.showTab) showTab(t); }catch(e){} }
  window.addEventListener('load',function(){ setTimeout(go,30); });
})();</script>"""

_LEGACY_HTML_CACHE = None
def _legacy_html():
    """把 app.py 的整页 HTML 注入补丁后返回；只做一次字符串替换并缓存。"""
    global _LEGACY_HTML_CACHE
    if _LEGACY_HTML_CACHE is None:
        html = _APP.HTML
        html = html.replace('<head><meta charset="utf-8">',
                            '<head><meta charset="utf-8">' + _LEGACY_SHIM, 1)
        html = html.replace('</body></html>', _LEGACY_BOOT + '</body></html>', 1)
        _LEGACY_HTML_CACHE = html
    return _LEGACY_HTML_CACHE


def log(level, msg):
    """唯一负责：往运行日志环里追加一条真实事件（供 /api/logs 读取）。"""
    _LOG.append({"ts": time.time(), "level": level, "msg": str(msg)})

def _video_continuity(seq_id):
    """文字层连贯性检查：参考数/重复/时长/内部 ID（不分析画面，不调用视觉模型）。"""
    art, sid = None, None
    for fp in (store.DATA / "textchain").glob("*/artifacts.json"):
        a = store.load_json(fp, None)
        if a and any(s.get("sequence_id") == seq_id for s in (a.get("storyboard") or [])):
            art, sid = a, fp.parent.name
            break
    if art is None:
        return {"sequence_id": seq_id, "result": "FAIL",
                "checks": [{"item": "缺少该序列的文字链产物", "ok": False, "detail": "先跑 textchain"}]}
    rp = next((s for s in (art.get("ref_plan") or []) if s["sequence_id"] == seq_id), None)
    sb = next((s for s in (art.get("storyboard") or []) if s["sequence_id"] == seq_id), None)
    pics = (rp or {}).get("pictures") or []
    total = sum(float(s.get("duration") or 0) for s in ((sb or {}).get("shots") or []))
    seen, dup = set(), False
    for p in pics:
        src = str(p.get("source") or "")
        if src in seen:
            dup = True
        seen.add(src)
    prompts = store.load_json(store.DATA / "textchain" / sid / "prompts.json", {})
    from cores.prompt_hygiene import find_internal_ids
    ids = find_internal_ids((prompts.get("h3") or {}).get(seq_id) or "")
    checks = [
        {"item": "参考图数量 ≤3", "ok": len(pics) <= 3, "detail": "%d 张" % len(pics)},
        {"item": "参考图实体不重复", "ok": not dup, "detail": "无重复" if not dup else "重复实体"},
        {"item": "时长合约 ≤15s", "ok": total <= 15 + 1e-6, "detail": "%.1fs" % total},
        {"item": "最终提示词无内部 ID", "ok": not ids, "detail": "干净" if not ids else "；".join(ids)},
    ]
    return {"sequence_id": seq_id, "result": "PASS" if all(c["ok"] for c in checks) else "FAIL",
            "checks": checks}

def _json_resp(h, obj, status=200):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    h.send_response(status)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    h.wfile.write(body)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _read(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            return {}

    def _dec_path(self):
        raw = self.path.split("?")[0]
        try:
            raw = raw.encode("latin-1", "replace").decode("utf-8", "replace")
        except Exception:
            pass
        return urllib.parse.unquote(raw)

    def _delegate_legacy(self, method):
        """把 /legacy/* 请求剥掉前缀后，交给 app.py 的 Handler 原样处理。
        复用同一个 socket 的 rfile/wfile —— app.Handler 用的都是 BaseHTTPRequestHandler
        的标准方法，所以直接借 v41 的请求对象即可。"""
        if _APP is None:
            return _json_resp(self, {"ok": False, "error": "legacy app 未加载"}, 503)
        raw = self.path  # 原始路径，含 /legacy 前缀与查询串
        stripped = raw[len("/legacy"):] or "/"
        if not stripped.startswith("/"):
            stripped = "/" + stripped
        h = _APP.Handler.__new__(_APP.Handler)
        h.rfile = self.rfile
        h.wfile = self.wfile
        h.headers = self.headers
        h.path = stripped
        h.command = method
        h.client_address = getattr(self, "client_address", ("127.0.0.1", 0))
        h.server = self.server
        h.request_version = self.request_version
        h.requestline = getattr(self, "requestline", "")
        h.raw_requestline = getattr(self, "raw_requestline", b"")
        h.close_connection = True
        if method == "GET":
            h.do_GET()
        else:
            h.do_POST()

    def do_GET(self):
        path = self._dec_path()
        try:
            if path == "/legacy" or path.startswith("/legacy/"):
                sub = path[len("/legacy"):] or "/"
                if sub in ("", "/", "/index.html"):
                    if _APP is None:
                        return _json_resp(self, {"ok": False, "error": "legacy app 未加载"}, 503)
                    body = _legacy_html().encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store, must-revalidate")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                return self._delegate_legacy("GET")
            if path in ("/", "/index.html"):
                body = (WEB / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                # 本地工作台改前端很频繁：不禁缓存的话，改完看不到效果，
                # 还会误以为是代码没生效（实测踩过：进度条已修好但页面跑的还是旧 JS）。
                self.send_header("Cache-Control", "no-store, must-revalidate")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/app.js":
                body = (WEB / "app.js").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                # 本地工作台改前端很频繁：不禁缓存的话，改完看不到效果，
                # 还会误以为是代码没生效（实测踩过：进度条已修好但页面跑的还是旧 JS）。
                self.send_header("Cache-Control", "no-store, must-revalidate")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            # web/ 下的 .js/.css/.html 一律直接服务（原来是写死三个文件名的白名单，
            # 新加 ui_actions.js 时就漏了，导致整页 JS 报错、按钮全死）
            if (path.endswith((".js", ".css", ".html")) and ".." not in path
                    and (WEB / path.lstrip("/")).is_file()):
                fp = WEB / path.lstrip("/")
                if fp.is_file():
                    body = fp.read_bytes()
                    self.send_response(200)
                    self.send_header("Cache-Control", "no-store, must-revalidate")
                    self.send_header("Content-Type", ("text/html; charset=utf-8" if path.endswith(".html")
                                     else "text/css; charset=utf-8" if path.endswith(".css")
                                     else "application/javascript; charset=utf-8"))
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
            if path == "/api/debug_path":
                q = self.path.split("?")[1] if "?" in self.path else ""
                u = urllib.parse.unquote(q.split("u=")[1]) if "u=" in q else ""
                rl = u
                try:
                    rl = urllib.parse.unquote(u.encode("latin-1", "replace").decode("utf-8", "replace"))
                except Exception:
                    rl = urllib.parse.unquote(u)
                t = (ROOT / rl).resolve()
                return _json_resp(self, {"ok": True, "raw": self.path, "u": u, "decoded": rl,
                                         "exists": t.exists(), "target": str(t)})
            if path.startswith("/files/"):
                raw = self.path.split("?")[0][len("/files/"):]
                try:
                    rel = urllib.parse.unquote(raw.encode("latin-1", "replace").decode("utf-8", "replace"))
                except Exception:
                    rel = urllib.parse.unquote(raw)
                target = (ROOT / rel).resolve()
                if str(target).startswith(str(ROOT.resolve())) and target.is_file():
                    ext = target.suffix.lower()
                    ctype = "image/png" if ext == ".png" else ("image/jpeg" if ext in (".jpg", ".jpeg") else
                            ("video/mp4" if ext == ".mp4" else ("application/pdf" if ext == ".pdf" else "application/octet-stream")))
                    size = target.stat().st_size
                    # 【必须支持 Range】不支持的话浏览器认为这个源不可 seek，
                    # 视频进度条拖不动（实测 video.seekable 是 [0,0]）——P240。
                    rng = self.headers.get("Range") or ""
                    start, end = 0, size - 1
                    partial = False
                    m = re.match(r"bytes=(\d*)-(\d*)\s*$", rng.strip()) if rng else None
                    if m:
                        a, b = m.group(1), m.group(2)
                        if a == "" and b == "":
                            m = None
                        elif a == "":                      # bytes=-N：最后 N 字节
                            n = int(b)
                            start, end = max(0, size - n), size - 1
                            partial = True
                        else:
                            start = int(a)
                            end = int(b) if b else size - 1
                            partial = True
                        if partial and (start >= size or start > end):
                            self.send_response(416)
                            self.send_header("Content-Range", "bytes */%d" % size)
                            self.send_header("Accept-Ranges", "bytes")
                            self.end_headers()
                            return
                        end = min(end, size - 1)
                    length = end - start + 1
                    self.send_response(206 if partial else 200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Accept-Ranges", "bytes")
                    # 【不能缓存】单段重做会写回同一个文件名，缓存住的话
                    # 重做完页面上还是旧片子，用户以为没生效（P240）
                    self.send_header("Cache-Control", "no-cache, must-revalidate")
                    self.send_header("Last-Modified",
                                     __import__("email").utils.formatdate(target.stat().st_mtime,
                                                                          usegmt=True))
                    self.send_header("Content-Length", str(length))
                    if partial:
                        self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
                    self.end_headers()
                    with open(str(target), "rb") as _f:     # 按块读，别把整个片子读进内存
                        _f.seek(start)
                        left = length
                        while left > 0:
                            chunk = _f.read(min(262144, left))
                            if not chunk:
                                break
                            try:
                                self.wfile.write(chunk)
                            except Exception:               # 浏览器换段/关页会断连，正常
                                return
                            left -= len(chunk)
                    return
                return _json_resp(self, {"ok": False, "error": "file not found"}, 404)
            if path.startswith("/api/story/") and "/assets" in path:
                sid = path.split("/")[3]
                kind = path.split("assets")[-1].strip("/") or "characters"
                return _json_resp(self, {"ok": True, "items": asset_core.list_assets(sid, kind)})
            if path == "/data_productions":
                prods = []
                for fp in (store.DATA / "productions").glob("*.json"):
                    d = store.load_json(fp, None)
                    if d:
                        prods.append(d)
                return _json_resp(self, {"ok": True, "productions": prods})
            if path == "/api/recent":
                prods = [store.load_json(fp, None) for fp in (store.DATA / "productions").glob("*.json")]
                prods = [p for p in prods if p]
                tasks = runtime_core.list_tasks(20)
                rows = ([{"kind": "production", "id": p.get("production_id"), "title": p.get("production_id"), "created": p.get("created")} for p in prods] +
                        [{"kind": "task", "id": t.get("id"), "title": t.get("title"), "created": t.get("created")} for t in tasks])
                rows.sort(key=lambda x: x.get("created", 0), reverse=True)
                return _json_resp(self, {"ok": True, "recent": rows[:12]})
            if path == "/api/favorites":
                favs = [v for v in asset_core.list_assets("STORY_001", "visuals") if v.get("status") == "adopted"]
                return _json_resp(self, {"ok": True, "favorites": favs})
            if path == "/api/open-folder":
                q = self.path.split("?")
                pth = ""
                if len(q) > 1 and "path=" in q[1]:
                    pth = urllib.parse.unquote(q[1].split("path=")[1].split("&")[0])
                if pth and os.path.exists(pth):
                    import subprocess as _sp
                    _sp.Popen(["explorer", os.path.dirname(pth) if os.path.isfile(pth) else pth])
                    return _json_resp(self, {"ok": True})
                return _json_resp(self, {"ok": False, "error": "路径不存在"}, 404)
            if path == "/api/tasks":
                limit = 50
                q = self.path.split("?")[1] if "?" in self.path else ""
                if "limit=" in q:
                    try:
                        limit = int(q.split("limit=")[1].split("&")[0])
                    except Exception:
                        pass
                return _json_resp(self, {"ok": True, "data": {"tasks": runtime_core.list_tasks(limit)}})
            if path == "/api/tasks/active":
                active = [t for t in runtime_core.list_tasks(200)
                          if t.get("status") in ("running", "queued", "waiting_user")]
                return _json_resp(self, {"ok": True, "data": {"active": active}})
            if path == "/api/gpu":
                u = runtime_core.gpu_usage()
                return _json_resp(self, {"ok": True, "data": {
                    "used_mb": u.get("used_mb"), "total_mb": u.get("total_mb"),
                    "used_gb": round(u["used_mb"] / 1024.0, 1) if u.get("used_mb", -1) >= 0 else None,
                    "total_gb": round(u["total_mb"] / 1024.0, 1) if u.get("total_mb", -1) >= 0 else None,
                    "holder": "unknown",
                    "models": {"qwen": bool(gpu_manager.pid_on_port(8080)),
                               "comfyui": bool(gpu_manager.pid_on_port(8188)),
                               "h3": bool(gpu_manager.pid_on_port(8190))},
                    "game_paused": os.path.exists(os.path.join("cache", "game_mode.flag")),
                    # 正在生成哪一段——进度条要全页面可见，只反映点击者自己的
                    # 请求的话，别的窗口什么都看不到
                    "current_task": (json.load(open(os.path.join("cache", "current_task.json"),
                                                    encoding="utf-8"))
                                     if os.path.exists(os.path.join("cache", "current_task.json"))
                                     else None),
                    "at": time.time()}})
            if path == "/api/logs":
                q = self.path.split("?")[1] if "?" in self.path else ""
                since = 0.0
                filt = ""
                if "since=" in q:
                    try:
                        since = float(q.split("since=")[1].split("&")[0])
                    except Exception:
                        pass
                if "filter=" in q:
                    filt = urllib.parse.unquote(q.split("filter=")[1].split("&")[0])
                items = [e for e in _LOG if e["ts"] >= since]
                if filt == "image":
                    items = [e for e in items if "生图" in e["msg"] or "krea" in e["msg"].lower() or "comfy" in e["msg"].lower()]
                elif filt == "error":
                    items = [e for e in items if e["level"] == "error"]
                return _json_resp(self, {"ok": True, "data": {"since": since, "filter": filt, "logs": list(items)}})
            if path == "/api/search":
                q = urllib.parse.unquote((self.path.split("?")[1] if "?" in self.path else "").split("q=")[-1])
                q = q.strip().lower()
                rows = []
                if q:
                    for s in story_core.list_stories():
                        if q in str(s.get("title", "")).lower() or q in str(s.get("one_line", "")).lower():
                            rows.append({"kind": "story", "id": s.get("story_id"), "title": s.get("title"),
                                         "sub": s.get("one_line", "")[:40]})
                    for p in (store.DATA / "productions").glob("*.json"):
                        d = store.load_json(p, None)
                        if d and q in str(d.get("production_id", "")).lower():
                            rows.append({"kind": "production", "id": d.get("production_id"),
                                         "title": d.get("production_id"), "sub": d.get("kind", "")})
                    for t in runtime_core.list_tasks(100):
                        if q in str(t.get("title", "")).lower() or q in str(t.get("id", "")).lower():
                            rows.append({"kind": "task", "id": t.get("id"), "title": t.get("title", ""),
                                         "sub": t.get("status", "")})
                return _json_resp(self, {"ok": True, "data": {"q": q, "rows": rows[:20]}})
            if path.startswith("/api/textchain/"):
                sid = path.split("/")[3]
                from cores import textchain
                return _json_resp(self, {"ok": True, "data": {
                    "validation": textchain.get_validation(sid),
                    "trace": textchain.get_trace(sid)}})
            if path == "/api/chain/view":
                q = self.path.split("?")[1] if "?" in self.path else ""
                sid = urllib.parse.unquote(q.split("story_id=")[-1].split("&")[0]) if "story_id=" in q else ""
                if not sid:
                    return _json_resp(self, {"ok": False, "error": "缺少 story_id"}, 400)
                from cores import chain_core
                return _json_resp(self, {"ok": True, "data": chain_core.chain_view(sid)})
            if path == "/api/chain/presets":
                from cores import chain_core
                return _json_resp(self, {"ok": True, "data": {
                    "presets": chain_core.PRESETS, "speeds": chain_core.SPEEDS,
                    "behavior_promise": chain_core.BEHAVIOR_PROMISE}})
            if path == "/api/project/graph":
                q = self.path.split("?")[1] if "?" in self.path else ""
                sid = urllib.parse.unquote(q.split("story_id=")[-1].split("&")[0]) if "story_id=" in q else ""
                if not sid:
                    return _json_resp(self, {"ok": False, "error": "缺少 story_id"}, 400)
                from cores import graph_core
                return _json_resp(self, {"ok": True, "data": graph_core.build_graph(sid)})
            if path == "/api/assets":
                q = self.path.split("?")[1] if "?" in self.path else ""
                def qv(k, d=""):
                    return urllib.parse.unquote(q.split(k + "=")[-1].split("&")[0]) if k + "=" in q else d
                sid, kind, status = qv("story_id"), qv("kind", "all"), qv("status")
                if not sid:
                    return _json_resp(self, {"ok": False, "error": "缺少 story_id"}, 400)
                groups = {
                    "characters": asset_core.list_assets(sid, "characters"),
                    "scenes": asset_core.list_assets(sid, "scenes"),
                    "wardrobes": asset_core.list_assets(sid, "wardrobes"),
                    "visuals": asset_core.list_assets(sid, "visuals"),
                }
                if kind == "keyframes":
                    items = [v for v in groups["visuals"] if "keyframe" in str(v.get("kind", "")).lower()]
                    return _json_resp(self, {"ok": True, "data": {"items": items}})
                if kind == "candidate":
                    items = [v for v in groups["visuals"] if v.get("status") in ("candidate", "draft")]
                    return _json_resp(self, {"ok": True, "data": {"items": items}})
                if kind != "all":
                    items = groups.get(kind, [])
                    if status:
                        items = [x for x in items if x.get("status") == status]
                    return _json_resp(self, {"ok": True, "data": {"items": items}})
                return _json_resp(self, {"ok": True, "data": {"groups": groups}})
            if path.count("/") == 4 and path.startswith("/api/asset/") and path.endswith("/versions"):
                aid = path.split("/")[3]
                found = None
                for sid_dir in (store.DATA / "assets").iterdir():
                    if not sid_dir.is_dir():
                        continue
                    v = store.load_json(sid_dir / "visuals" / (aid + ".json"), None)
                    if v:
                        found = v
                        break
                if not found:
                    return _json_resp(self, {"ok": False, "error": "资产不存在"}, 404)
                versions = [x for x in asset_core.list_assets(found["story_id"], "visuals")
                            if x.get("owner_id") == found.get("owner_id") and x.get("kind") == found.get("kind")]
                versions.sort(key=lambda x: float(x.get("visual_version") or 0))
                return _json_resp(self, {"ok": True, "data": {"asset": found, "versions": versions}})
            if path == "/api/script":
                q = self.path.split("?")[1] if "?" in self.path else ""
                sid = urllib.parse.unquote(q.split("story_id=")[-1].split("&")[0]) if "story_id=" in q else ""
                ep_no = 1
                if "episode=" in q:
                    try:
                        ep_no = int(q.split("episode=")[1].split("&")[0])
                    except Exception:
                        pass
                if not sid:
                    return _json_resp(self, {"ok": False, "error": "缺少 story_id"}, 400)
                story = story_core.get_story(sid)
                if not story:
                    return _json_resp(self, {"ok": False, "error": "故事不存在"}, 404)
                ep = narrative_core.load_episode(sid, ep_no)
                return _json_resp(self, {"ok": True, "data": {
                    "story": story, "episode": ep, "previs": story.get("previs") or {},
                    "state": state_core.causal_state_package(sid)}})
            if path.startswith("/api/state/"):
                sid = path.split("/")[3]
                pkg = state_core.causal_state_package(sid)
                names = {}
                for c in asset_core.list_assets(sid, "characters"):
                    names[str(c.get("name", ""))] = c.get("name")
                lines = []
                if pkg:
                    clock = pkg.get("story_clock") or {}
                    lines.append("故事时间：第 %s 日 %s" % (clock.get("day", "?"), clock.get("time", "?")))
                    for name, slots in (pkg.get("clothing") or {}).items():
                        for slot, val in (slots or {}).items():
                            if val == "removed":
                                lines.append("%s 的%s已脱下" % (name, {"outerwear": "外套", "upper_body": "上衣", "lower_body": "下装", "underwear": "内层", "footwear": "鞋", "accessories": "配饰", "outer_cloak": "外披风"}.get(slot, slot)))
                            elif val == "worn" and slot in ("outerwear", "outer_cloak"):
                                lines.append("%s 的%s穿在身上" % (name, {"outerwear": "外套", "outer_cloak": "外披风"}[slot]))
                    for name, st in (pkg.get("anatomy") or {}).items():
                        for part, val in (st or {}).items():
                            if val == "missing":
                                lines.append("%s 的%s永久缺失" % (name, {"left_arm": "左臂", "right_arm": "右臂", "left_leg": "左腿", "right_leg": "右腿"}.get(part, part)))
                    for name, injs in (pkg.get("injuries") or {}).items():
                        for inj in (injs or []):
                            if isinstance(inj, dict):
                                lines.append("%s 有%s（%s，%s）" % (name, inj.get("type", "伤"), inj.get("severity", "轻"), inj.get("status", "治疗中")))
                    cons = pkg.get("consequences") or []
                    if isinstance(cons, dict):
                        for name, c in cons.items():
                            if c:
                                lines.append("%s：%s" % (name, c if isinstance(c, str)
                                                         else ("；".join(c) if isinstance(c, list) else str(c))))
                    elif isinstance(cons, list):
                        for c in cons:
                            if isinstance(c, dict):
                                text = c.get("text") or "；".join(str(v) for k, v in c.items() if v)
                                if text:
                                    lines.append(str(text)[:120])
                            elif isinstance(c, str) and c:
                                lines.append(c)
                return _json_resp(self, {"ok": True, "data": {"lines": lines, "package": pkg}})
            if path == "/api/video/plan":
                q = self.path.split("?")[1] if "?" in self.path else ""
                sid = urllib.parse.unquote(q.split("story_id=")[-1].split("&")[0]) if "story_id=" in q else ""
                if not sid:
                    return _json_resp(self, {"ok": False, "error": "缺少 story_id"}, 400)
                story = story_core.get_story(sid) or {}
                previs = story.get("previs") or {}
                shots = previs.get("shots") or []
                art = store.load_json(store.DATA / "textchain" / sid / "artifacts.json", {})
                ref_plan = art.get("ref_plan") or []
                takes_dir = store.DATA / "takes" / sid
                takes = {}
                if takes_dir.exists():
                    for fp in takes_dir.glob("*.json"):
                        t = store.load_json(fp, None)
                        if t:
                            takes[t.get("sequence_id", fp.stem)] = t
                videos = []
                for fp in (store.DATA / "productions").glob("*.json"):
                    p = store.load_json(fp, None)
                    if p and p.get("story_id") == sid and str(p.get("kind", "")).lower() == "video":
                        videos.append(p)
                videos.sort(key=lambda x: x.get("created", 0), reverse=True)
                return _json_resp(self, {"ok": True, "data": {
                    "story": story, "previs": previs, "shots": shots,
                    "ref_plan": ref_plan, "takes": takes,
                    "videos": videos}})
            if path.count("/") == 4 and path.startswith("/api/video/sequence/"):
                seq_id = path.split("/")[4]
                return _json_resp(self, {"ok": True, "data": {
                    "sequence_id": seq_id,
                    "continuity": _video_continuity(seq_id)}})
            if path.count("/") == 4 and path.startswith("/api/video/continuity/"):
                seq_id = path.split("/")[4]
                return _json_resp(self, {"ok": True, "data": _video_continuity(seq_id)})
            if path == "/api/productions":
                q = self.path.split("?")[1] if "?" in self.path else ""
                sid = urllib.parse.unquote(q.split("story_id=")[-1].split("&")[0]) if "story_id=" in q else ""
                kind = urllib.parse.unquote(q.split("kind=")[-1].split("&")[0]) if "kind=" in q else ""
                items = []
                for fp in (store.DATA / "productions").glob("*.json"):
                    p = store.load_json(fp, None)
                    if not p:
                        continue
                    if sid and p.get("story_id") != sid:
                        continue
                    if kind and str(p.get("kind", "")).lower() != kind.lower():
                        continue
                    if str(p.get("kind", "")).lower() == "video":
                        seqs = p.get("sequences") or []
                        title = str(p.get("production_id") or "")
                        if p.get("adopted"):
                            vc = "成品"
                        elif len(seqs) > 1:
                            vc = "拼接"
                        elif "测试" in title:
                            vc = "测试"
                        else:
                            vc = "单个"
                        p = dict(p)
                        p["video_class"] = vc
                    items.append(p)
                items.sort(key=lambda x: x.get("created", 0), reverse=True)
                return _json_resp(self, {"ok": True, "data": {"items": items}})
            if path.count("/") == 3 and path.startswith("/api/production/"):
                pid = path.split("/")[3]
                p = store.load_json(store.DATA / "productions" / (pid + ".json"), None)
                if not p:
                    return _json_resp(self, {"ok": False, "error": "成品不存在"}, 404)
                manifest = None
                for fp in (store.DATA / "manifests").glob("*.json"):
                    m = store.load_json(fp, None)
                    if m and m.get("story_id") == p.get("story_id") and \
                            str(m.get("production_type", "")).lower() in str(p.get("kind", "")).lower():
                        manifest = m
                        break
                excerpt = ""
                for f in (p.get("files") or []):
                    if str(f).lower().endswith((".md", ".txt")):
                        try:
                            excerpt = Path(f).read_text(encoding="utf-8", errors="ignore")[:200]
                            break
                        except Exception:
                            continue
                return _json_resp(self, {"ok": True, "data": {
                    "production": p, "manifest": manifest, "excerpt": excerpt}})
            if path.startswith("/api/tasks/"):
                tid = path.split("/")[3]
                t = runtime_core.load(tid)
                return _json_resp(self, {"ok": bool(t), "task": t})
            if path == "/api/problems":
                return _json_resp(self, {"ok": True, **runtime_core.problems()})
            if path == "/api/storage":
                return _json_resp(self, {"ok": True, "storage": runtime_core.storage_info()})
            if path == "/api/game_mode":
                return _json_resp(self, {"ok": True, "game_mode": runtime_core.gpu_usage()})
            if path == "/api/content_profile":
                return _json_resp(self, {"ok": True, "profile": profile_core.load_profile()})
            if path == "/api/stories":
                return _json_resp(self, {"ok": True, "stories": story_core.list_stories()})
            if path.startswith("/api/story/") and "/coverage" in path:
                sid = path.split("/")[3]
                ep = int(path.split("/coverage")[0].split("/")[-1]) if False else int(self.path.split("episode=")[1].split("&")[0]) if "episode=" in self.path else 1
                return _json_resp(self, {"ok": True, "report": CoverageEngine().report(sid, ep)})
            if path.startswith("/api/story/"):
                sid = path.split("/")[3]
                st = story_core.get_story(sid)
                if not st:
                    return _json_resp(self, {"ok": False, "error": "故事不存在"}, 404)
                ep = st.get("current_episode")
                episode = narrative_core.load_episode(sid, ep) if ep else None
                state = state_core.latest_state(sid)
                return _json_resp(self, {"ok": True, "story": st, "episode": episode, "state": state})
            # 兜底：GET 业务路由也交给 api/ 各域模块（@get 注册的）。放在写死分支之后，
            # 不影响上面任何既有 GET；域模块里返回 None 表示已自行写完响应（如文件流）。
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            r = api.dispatch(api.GET_ROUTES, self, path, q)
            if r is not False:
                if r is None:
                    return
                return _json_resp(self, r, r.pop("_status", 200))
            return _json_resp(self, {"ok": False, "error": "not found"}, 404)
        except Exception as e:
            return _json_resp(self, {"ok": False, "error": str(e)[:500]}, 500)

    def do_POST(self):
        path = self._dec_path()
        # /legacy/* 直接转交 app.py（必须在读取 body 之前分流，否则 rfile 会被提前消费）
        if path == "/legacy" or path.startswith("/legacy/"):
            try:
                return self._delegate_legacy("POST")
            except Exception as e:
                return _json_resp(self, {"ok": False, "error": str(e)[:500]}, 500)
        d = self._read()
        try:
            # 业务路由全部在 api/ 各域模块，server.py 只做分发
            r = api.dispatch(api.POST_ROUTES, self, path, d)
            if r is not False:
                if r is None:
                    return
                return _json_resp(self, r, r.pop("_status", 200))
            return _json_resp(self, {"ok": False, "error": "not found"}, 404)
        except Exception as e:
            log("error", "POST %s 出错：%s" % (path, e))
            return _json_resp(self, {"ok": False, "error": str(e)[:500]}, 500)


class DualStackHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6
    def server_bind(self):
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except Exception:
            pass
        super().server_bind()

def _decisions_selfcheck():
    """已定决策自检（docs/已定决策.md；用户 2026-09-07：拍板的事不得私自改）。只报不改。"""
    try:
        from cores import decisions
        bad = [m for ok, m in decisions.run() if not ok]
        if bad:
            for m in bad:
                print("⚠ 违反已定决策：" + m, flush=True)
        else:
            print("✅ 已定决策自检通过（%d 项）" % len(decisions.CHECKS), flush=True)
    except Exception as ex:
        print("已定决策自检没跑起来：" + str(ex)[:120], flush=True)


def main():
    _decisions_selfcheck()
    runtime_core.start_worker()
    url = "http://127.0.0.1:%d/" % PORT
    log("info", "AI视频工作台 V1.0 正式版 启动 " + url)
    try:
        # 公开发行版仅监听本机；当前服务没有多用户登录鉴权。
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        print("V4.1 端口被占用：", e)
        return
    print("=" * 50)
    print("  AI 创作工作台 " + _VERSION)
    print("  网址： " + url)
    print("  数据： " + str(ROOT / "data"))
    print("=" * 50)
    # 开机自检：缺库、缺 ffmpeg、服务没起、路径没配，当场说清楚，
    # 别等生成跑到一半才炸（上线标准第一条）。
    try:
        from cores import doctor as _doctor
        _doctor.report()
    except Exception as _e:
        print("  （开机自检跑不了：%s）" % str(_e)[:80])
    if not os.environ.get("V41_NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    srv.serve_forever()

if __name__ == "__main__":
    main()
