"""Double-click entry. Uses existing Python and never starts an AI model."""
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import webbrowser

BASE = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:8872/'


def running():
    try:
        with urllib.request.urlopen(URL + 'api/health', timeout=1) as r:
            return json.load(r).get('app') == 'douyin-local-downloader-1'
    except Exception:
        return False


if not running():
    with (BASE / 'startup.log').open('ab') as log:
        subprocess.Popen([sys.executable, '-B', str(BASE / 'app.py')], cwd=str(BASE), stdout=log, stderr=log, creationflags=0x08000000)
    for _ in range(40):
        if running():
            break
        time.sleep(.2)
if running():
    webbrowser.open(URL)
else:
    import tkinter.messagebox
    tkinter.messagebox.showerror('下载器未启动', '启动失败，可能8872端口被其他程序占用。请查看同目录的 startup.log。现有工作台不受影响。')
