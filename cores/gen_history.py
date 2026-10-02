# -*- coding: utf-8 -*-
"""生成历史：每次出图/出视频记一条（提示词 + 文件 + 来源 + 时间）。

全局一条流水，不按项目分——历史页要把 v41 流水线和 legacy 工具的产物一块看。
记录失败绝不能拖累出图主流程，所以全程 try/except 吞掉。
"""
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_FILE = ROOT / "data" / "gen_history.jsonl"


def _rel(path):
    """能转成相对 v41 根目录就转（前端用 /files/ 取）；转不了就原样存绝对路径。"""
    try:
        return str(Path(path).resolve().relative_to(ROOT)).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def record(kind, path, prompt="", source="v41", story_id="", extra=None):
    """kind: character/scene/shot/video；path: 出好的文件（绝对或相对都行）。"""
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "id": "H%d_%d" % (int(time.time() * 1000), abs(hash(str(path))) % 100000),
            "ts": time.time(), "kind": str(kind), "path": _rel(path),
            "prompt": str(prompt or ""), "source": str(source),
            "story_id": str(story_id or ""), "saved": False, "extra": extra or {},
        }
        with open(_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec
    except Exception:
        return None


def records():
    out = []
    try:
        if _FILE.exists():
            for line in _FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        pass
    return out


def mark_saved(path):
    """把某条（按 path 匹配）标成已保存。重写整个文件——历史量不大，够用。"""
    try:
        rel = _rel(path)
        recs = records()
        hit = False
        for r in recs:
            if r.get("path") == rel:
                r["saved"] = True
                hit = True
        if hit:
            with open(_FILE, "w", encoding="utf-8") as f:
                for r in recs:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return hit
    except Exception:
        return False
