"""Start the frozen workbench using its bundled Python; no model inference."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8853)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if not 1024 <= args.port <= 65535:
        raise SystemExit('Invalid port')
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1', args.port)) == 0:
            raise SystemExit(f'Port {args.port} is already in use. Close the other workbench first.')
    env = os.environ.copy()
    for name in tuple(env):
        if name.startswith('V41_') or name in ('PYTHONHOME', 'PYTHONPATH', 'DOUYIN_DOWNLOADER_ROOT'):
            env.pop(name)
    env.update(V41_PORT=str(args.port), V41_NO_BROWSER='1', PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    local_bin = root / 'runtime' / 'ffmpeg' / 'bin'
    env['PATH'] = str(local_bin) + os.pathsep + env.get('PATH', '')
    config = root / 'config.json'
    if not config.exists():
        config.write_text(json.dumps({
            'llama_server_exe': '', 'model_gguf': '', 'mmproj_gguf': '',
            'llama_log': '', 'llama_err_log': '', 'comfy_dir': '',
            'comfy_output': '', 'h3_root': '', 'legacy_root': ''
        }, indent=2), encoding='utf-8')
    logs = root / 'cache'
    logs.mkdir(exist_ok=True)
    python = root / 'runtime' / 'python' / 'python.exe'
    if not python.exists():
        python = Path(sys.executable)
    with (logs / 'portable-start.log').open('ab') as log:
        proc = subprocess.Popen([str(python), '-E', '-s', '-B', str(root / 'server.py')],
                                cwd=root, env=env, stdout=log, stderr=log,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    (logs / 'portable-server.json').write_text(json.dumps({'pid': proc.pid, 'port': args.port}), encoding='utf-8')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = f'http://127.0.0.1:{args.port}/'
    for _ in range(60):
        if proc.poll() is not None:
            raise SystemExit('Startup failed; see cache/portable-start.log')
        try:
            with opener.open(url + 'api/stories', timeout=1) as reply:
                if reply.status == 200:
                    if not args.no_browser:
                        webbrowser.open(url)
                    print('READY ' + url)
                    return
        except OSError:
            pass
        time.sleep(0.5)
    raise SystemExit('Startup timed out; see cache/portable-start.log. The server may still be starting.')


if __name__ == '__main__':
    main()
