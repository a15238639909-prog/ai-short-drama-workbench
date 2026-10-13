# -*- coding: utf-8 -*-
"""store.py — 统一 JSON 文件存储与稳定 ID 生成（V4.1 clean-room）。"""
import json, os, re, threading, time, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
for d in ("stories", "narrative", "states", "assets", "profiles", "tasks", "manifests"):
    (DATA / d).mkdir(parents=True, exist_ok=True)

_LOCK = threading.Lock()

def atomic_write(path, data):
    with _LOCK:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(p) + ".tmp." + str(threading.get_ident())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(8):
            try:
                os.replace(tmp, str(p))
                break
            except PermissionError as error:
                # Windows readers/antivirus can briefly deny replacement.
                # Keep atomic replacement; never fall back to truncating p.
                if getattr(error, "winerror", None) not in (5, 32) or attempt == 7:
                    raise
                time.sleep(0.02 * (attempt + 1))

def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
            if default is None:
                return d if isinstance(d, dict) else default
            return d if isinstance(d, type(default)) else default
    except Exception:
        return default

def save_json(path, data):
    atomic_write(path, data)

def new_id(prefix):
    return "%s_%s" % (prefix, uuid.uuid4().hex[:8].upper())

def seq_id(prefix, seq):
    return "%s_%05d" % (prefix, int(seq))
