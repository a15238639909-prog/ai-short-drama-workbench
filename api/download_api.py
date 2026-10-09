"""本机视频下载入口：代理到同一独立下载器，避免两份队列写同一数据。"""
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from api import get, post

TOOL_ROOT = Path(os.environ.get("DOUYIN_DOWNLOADER_ROOT", str(
    Path(__file__).resolve().parents[1] / "tools" / "douyin-downloader")))
BASE = "http://127.0.0.1:8872"
APP_ID = "douyin-local-downloader-1"
PREFIX = "/download-tool"
POST_PATHS = {"/api/download", "/api/cancel", "/api/retry", "/api/open-file",
              "/api/open-folder", "/api/choose-folder"}
START_LOCK = threading.Lock()
_child = None


def local_request(h, write=False):
    try:
        addr = ipaddress.ip_address(h.client_address[0])
        addr = getattr(addr, "ipv4_mapped", None) or addr
        hosts = (f"127.0.0.1:{h.server.server_port}", f"localhost:{h.server.server_port}")
        return (addr.is_loopback and h.headers.get("Host") in hosts
                and (not write or h.headers.get("Origin") in tuple("http://" + x for x in hosts)))
    except ValueError:
        return False


def fail(message, status=503):
    return {"ok": False, "error": message, "_status": status}


def request(req, timeout=3):
    # 本机内部连接不经过系统 HTTP 代理。
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout)


def healthy():
    try:
        with request(BASE + "/api/health") as response:
            data = json.loads(response.read(4096))
    except urllib.error.HTTPError as error:
        raise RuntimeError("8872端口已被其他服务占用，请检查后重试。") from error
    except (urllib.error.URLError, OSError):
        return False
    except (ValueError, UnicodeError) as error:
        raise RuntimeError("8872端口返回的不是视频下载服务。") from error
    if not isinstance(data, dict) or data.get("app") != APP_ID:
        raise RuntimeError("8872端口返回的不是视频下载服务。")
    return True


def ensure_service():
    global _child
    with START_LOCK:
        if healthy():
            return
        if _child is None or _child.poll() is not None:
            with socket.socket() as probe:
                probe.settimeout(1)
                if probe.connect_ex(("127.0.0.1", 8872)) == 0:
                    raise RuntimeError("8872端口已有服务，但下载器没有响应，请稍后重试。")
            script = TOOL_ROOT / "app.py"
            if not script.is_file():
                raise RuntimeError("找不到视频下载器：" + str(script))
            with (TOOL_ROOT / "workbench-start.log").open("ab") as log:
                _child = subprocess.Popen([sys.executable, "-B", str(script)], cwd=TOOL_ROOT,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for _ in range(25):
            if healthy():
                return
            if _child.poll() is not None:
                break
            time.sleep(0.2)
        raise RuntimeError("下载器尚未启动成功，请重新连接；详情见下载器 workbench-start.log。")


@post("/api/downloads/open")
def open_downloads(h, path, body):
    if not local_request(h, write=True):
        return fail("请从本机工作台打开视频下载。", 403)
    try:
        ensure_service()
        return {"ok": True, "data": {"url": PREFIX + "/"}}
    except (OSError, RuntimeError) as error:
        return fail(str(error))


def proxy(h, path, body=None):
    write = body is not None
    if not local_request(h, write=write):
        return fail("仅供本机工作台访问。", 403)
    target = path[len(PREFIX):]
    allowed = POST_PATHS if write else {"/", "/api/state", "/api/health"}
    if target not in allowed:
        return fail("下载接口不存在。", 404)
    headers = {}
    payload = None
    if write:
        token = h.headers.get("X-App-Token", "")
        if not token:
            return fail("页面验证失效，请重新打开视频下载。", 403)
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if len(payload) > 50000:
            return fail("请求过大。", 400)
        headers = {"Content-Type": "application/json", "X-App-Token": token}
    try:
        req = urllib.request.Request(BASE + target, data=payload, headers=headers)
        try:
            response = request(req, timeout=185 if target == "/api/choose-folder" else 10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            content = response.read(8 * 1024 * 1024 + 1)
            if len(content) > 8 * 1024 * 1024:
                return fail("下载记录响应过大，请打开独立下载器。")
            status = response.code
            content_type = response.headers.get("Content-Type", "application/json; charset=utf-8")
    except (urllib.error.URLError, OSError):
        return fail("下载器连接中断，请重新打开左侧“视频下载”。")
    h.send_response(status)
    h.send_header("Content-Type", content_type)
    h.send_header("Content-Length", str(len(content)))
    h.send_header("Cache-Control", "no-store")
    h.send_header("X-Content-Type-Options", "nosniff")
    h.send_header("Referrer-Policy", "no-referrer")
    h.send_header("X-Frame-Options", "SAMEORIGIN")
    h.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                  "style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'self'; "
                  "base-uri 'none'; form-action 'self'")
    h.end_headers()
    h.wfile.write(content)


@get((PREFIX + "/", ""))
def get_downloads(h, path, query):
    return proxy(h, path)


@post((PREFIX + "/", ""))
def post_downloads(h, path, body):
    if not isinstance(body, dict):
        return fail("请求格式无效。", 400)
    return proxy(h, path, body)
