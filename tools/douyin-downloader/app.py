"""Standalone loopback-only download UI; entirely separate from the video workbench."""
from __future__ import annotations

import argparse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse

import downloader

BASE = Path(__file__).resolve().parent
DATA = BASE / 'data'
STATE = DATA / 'state.json'
APP_ID = 'douyin-local-downloader-1'
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.RLock()
PICKER_LOCK = threading.Lock()
WORK = queue.Queue()
JOBS = []
DEFAULT_FOLDER = str(BASE / '下载的视频')


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def save_state():
    DATA.mkdir(exist_ok=True)
    temporary = DATA / 'state.next'
    temporary.write_text(json.dumps({'folder': DEFAULT_FOLDER, 'jobs': JOBS}, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, STATE)


def load_state():
    global DEFAULT_FOLDER
    if not STATE.exists():
        return
    try:
        saved = json.loads(STATE.read_text(encoding='utf-8'))
        DEFAULT_FOLDER = saved.get('folder') or DEFAULT_FOLDER
        JOBS.extend(j for j in saved.get('jobs', []) if isinstance(j, dict))
        for job in JOBS:
            if job['status'] in ('queued', 'resolving', 'downloading'):
                job.update(status='interrupted', error='工具曾关闭，未完成的文件已保留。点击重试可重新下载。')
    except (ValueError, OSError, KeyError):
        # Preserve the unreadable file, including its original bytes.
        STATE.rename(DATA / ('state-unreadable-' + secrets.token_hex(4) + '.json'))


def update(job, **values):
    with LOCK:
        job.update(values)
        job['updated'] = now()
        save_state()


def work_loop():
    while True:
        job = WORK.get()
        try:
            if job.get('cancel'):
                update(job, status='cancelled', error='已取消排队。')
                continue
            update(job, status='resolving', note='正在读取抖音官方视频数据…')
            info = downloader.resolve(job['source'], lambda: job.get('cancel', False))
            # Identical links, different short links and restart retries do not overwrite files.
            with LOCK:
                duplicate = next((j for j in JOBS if j is not job and j.get('video_id') == info['video_id'] and j.get('status') == 'completed' and Path(j.get('path', '')).is_file() and Path(j['path']).parent == Path(job['folder']).resolve()), None)
            update(job, title=info['title'], video_id=info['video_id'], author=info['author'], duration=info['duration'])
            if duplicate:
                downloader.check_mp4(Path(duplicate['path']))
                update(job, status='completed', path=duplicate['path'], size=duplicate.get('size', 0), width=duplicate.get('width', 0), height=duplicate.get('height', 0), note='已下载过，沿用原文件。', already_exists=True)
                continue
            update(job, status='downloading', note='下载平台提供的最高可用画质…')
            last = [0.0]

            def progress(**values):
                if time.monotonic() - last[0] >= .3 or 'note' in values:
                    update(job, **values)
                    last[0] = time.monotonic()

            result = downloader.download(info, job['folder'], job['id'], progress, lambda: job.get('cancel', False))
            update(job, **result, status='completed', received=result['size'], total=result['size'], note='已下载过，沿用原文件。' if result['already_exists'] else '下载完成 · 原始播放文件，未转码')
        except downloader.Cancelled as error:
            update(job, status='cancelled', error=str(error), note='')
        except Exception as error:
            update(job, status='failed', error=downloader.safe_error(error), note='')
        finally:
            WORK.task_done()


def destination(value):
    value = str(value or '').strip()
    path = Path(value).expanduser()
    if not value or not path.is_absolute() or value.startswith(('\\\\', '//')) or not path.drive:
        raise downloader.DownloadError('请选择本机磁盘上的完整保存目录。')
    if path.exists() and not path.is_dir():
        raise downloader.DownloadError('保存位置必须是文件夹，不是文件。')
    return str(path.resolve())


class Server(ThreadingHTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # No personal share links or signed media URLs in server logs.

    def respond(self, data, code=200):
        content = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(content)

    def allowed_host(self):
        return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

    def do_GET(self):
        if not self.allowed_host():
            return self.respond({'error': '仅供本机访问'}, 403)
        route = urllib.parse.urlsplit(self.path).path
        if route == '/api/health':
            return self.respond({'app': APP_ID, 'version': '1.0'})
        if route == '/api/state':
            with LOCK:
                return self.respond({'folder': DEFAULT_FOLDER, 'jobs': JOBS[-200:], 'version': '1.0'})
        if route == '/':
            html = (BASE / 'index.html').read_text(encoding='utf-8').replace('__APP_TOKEN__', TOKEN).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(html)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            return self.wfile.write(html)
        self.respond({'error': '页面不存在'}, 404)

    def do_POST(self):
        global DEFAULT_FOLDER
        origins = (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}')
        if not self.allowed_host() or self.headers.get('Origin', '') not in ('', *origins) or not secrets.compare_digest(self.headers.get('X-App-Token', ''), TOKEN):
            return self.respond({'error': '页面验证失效，请刷新下载器页面。'}, 403)
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 <= length <= 50000:
                raise downloader.DownloadError('请求过大。')
            body = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(body, dict):
                raise downloader.DownloadError('请求格式无效。')
            route = urllib.parse.urlsplit(self.path).path
            if route == '/api/download':
                links = downloader.extract_links(body.get('text', ''))
                folder = destination(body.get('folder'))
                with LOCK:
                    if sum(j['status'] in ('queued', 'resolving', 'downloading') for j in JOBS) + len(links) > 40:
                        raise downloader.DownloadError('队列最多40条，请等当前任务完成。')
                    added, skipped = [], 0
                    DEFAULT_FOLDER = folder
                    for link in links:
                        if any(j['source'] == link and j['folder'] == folder and j['status'] in ('queued', 'resolving', 'downloading') for j in JOBS):
                            skipped += 1
                            continue
                        job = {'id': secrets.token_hex(8), 'source': link, 'folder': folder, 'status': 'queued', 'title': '等待解析视频', 'created': now(), 'received': 0, 'total': 0}
                        JOBS.append(job)
                        added.append(job['id'])
                        WORK.put(job)
                    save_state()
                return self.respond({'added': added, 'skipped': skipped})
            if route in ('/api/cancel', '/api/retry', '/api/open-file'):
                with LOCK:
                    job = next((j for j in JOBS if j['id'] == body.get('id')), None)
                    if not job:
                        raise downloader.DownloadError('没有找到这个任务。')
                    if route == '/api/cancel':
                        if job['status'] in ('queued', 'resolving', 'downloading'):
                            job['cancel'] = True
                            job['note'] = '正在停止…'
                            save_state()
                    elif route == '/api/retry':
                        if job['status'] not in ('failed', 'cancelled', 'interrupted'):
                            raise downloader.DownloadError('当前任务不需要重试。')
                        retry = {'id': secrets.token_hex(8), 'source': job['source'], 'folder': job['folder'], 'status': 'queued', 'title': job['title'], 'created': now(), 'received': 0, 'total': 0}
                        JOBS.append(retry)
                        save_state()
                        WORK.put(retry)
                    else:
                        path = Path(job.get('path', ''))
                        if job['status'] != 'completed' or path.suffix.lower() != '.mp4' or not path.is_file():
                            raise downloader.DownloadError('视频文件不存在，请重新下载。')
                        os.startfile(str(path))
                return self.respond({'ok': True})
            if route == '/api/open-folder':
                folder = destination(body.get('folder', DEFAULT_FOLDER))
                Path(folder).mkdir(parents=True, exist_ok=True)
                os.startfile(folder)
                return self.respond({'ok': True})
            if route == '/api/choose-folder':
                if not PICKER_LOCK.acquire(blocking=False):
                    raise downloader.DownloadError('文件夹选择窗口已经打开，请先完成选择。')
                try:
                    result = subprocess.check_output([sys.executable, '-B', str(BASE / 'folder_picker.py'), DEFAULT_FOLDER], timeout=180, creationflags=0x08000000)
                    folder = json.loads(result.decode('utf-8'))
                    if folder:
                        with LOCK:
                            DEFAULT_FOLDER = destination(folder)
                            save_state()
                    return self.respond({'folder': folder})
                finally:
                    PICKER_LOCK.release()
            if route == '/api/shutdown':
                with LOCK:
                    if any(j['status'] in ('queued', 'resolving', 'downloading') for j in JOBS):
                        raise downloader.DownloadError('仍有下载任务，请完成或停止后再退出。')
                self.respond({'ok': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            self.respond({'error': '接口不存在'}, 404)
        except (ValueError, downloader.DownloadError, OSError, subprocess.SubprocessError) as error:
            self.respond({'error': str(error) if isinstance(error, downloader.DownloadError) else downloader.safe_error(error)}, 400)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8872)
    args = parser.parse_args()
    server = Server(('127.0.0.1', args.port), Handler)
    load_state()
    threading.Thread(target=work_loop, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
