# -*- coding: utf-8 -*-
"""config_api —— 设置页后端：可视化编辑给 Qwen 的指令词 + 全局默认设置。

指令词就是 presets/instructions/*.txt。以前只能在硬盘上改，这里给个安全的读写口：
只认这个目录下的 .txt/.md，写前自动备份，防目录穿越。
"""
import os
import time
from pathlib import Path

from api import get, post
from api._shared import _RESP, ctx
globals().update(ctx())

ROOT = Path(__file__).resolve().parent.parent
INS = ROOT / "presets" / "instructions"
_BAK = INS / "_bak"
_DEFAULTS = ROOT / "data" / "global_defaults.json"

# 新流水线当前在用的指令词——列表里置顶，好找。其余的（备份/案例/旧流程）折叠在后面。
# ──────── 指令词清单（2026-09-01 重排）────────
# 原来这里是旧流程的 9 份，新链路的三份没列，用户在设置页看不到真正在跑的。
# 现在按链路分组：一组 = 工作流里的一个阶段，顺序就是跑的顺序。
# 每条三个字段：文件名、这一步是谁、这一步干什么。
_CHAIN = [
    ("故事", [
        ("第一话简介_指令词.txt", "简介", "一句话 → 第一话讲什么"),
        ("第一话原文_指令词.txt", "小说家", "一句话/简介 → 第一话正文，戏剧结构在这一层长出来"),
        ("AI扩写_指令词.txt", "扩写", "把用户选中的一段写长，只扩不改原意"),
        ("第一话简介提炼_指令词.txt", "反推简介", "改完正文，倒推出新的简介"),
        ("续写简介_指令词.txt", "续写", "上一话 → 下一话的简介"),
    ]),
    ("人物与场景", [
        ("正文提取人物场景_指令词.txt", "提取", "正文 → 有哪些人、哪些地方（只抄不编）"),
        ("人物设定_指令词.txt", "角色设计师", "把人物卡的空字段设计出来"),
        ("脸部设计_指令词.txt", "脸部设计", "全组人各定一张结构不同的脸（九项可数填空）"),
        ("人物补全_指令词.txt", "补全", "只补还空着的字段，已填的不动"),
        ("人物设定图_用户版_指令词.txt", "人设图", "人物卡 → 出图提示词"),
        ("Anima_英文词组.txt", "Anima普通出图", "Anima动漫场景和普通插画的英文词组"),
        ("Anima_人物三视图.txt", "Anima人物三视图", "人设板专用：正侧背全身及可选单张特写，最终英文词组仍由千问生成"),
        ("场景图_指令词.txt", "场景设计师", "场景卡 → 出图提示词"),
    ]),
    ("剧本与视频", [
        ("正文改画面稿_指令词.txt", "编剧", "正文 → 剧本：镜头能拍出来的画面，一段不漏"),
        ("画面稿改H3提示词_指令词.txt", "视频提示词", "剧本 → MiniMax H3 的分段提示词"),
    ]),
    ("故事骨架（题材引擎两步法）", [
        ("故事_故事核_指令词.txt", "故事核", "一句话 → 这故事的张力是什么"),
        ("故事_骨架_指令词.txt", "骨架", "故事核 → 分几场、每场干什么"),
        ("故事_正文_按骨架_指令词.txt", "按骨架写", "骨架 → 正文"),
        ("故事_完整正文.txt", "整篇正文", "一步写完整篇"),
    ]),
    ("长篇", [
        ("长篇_故事圣经.txt", "故事圣经", "多话次共用的设定总纲"),
        ("长篇_重新规划后续.txt", "重规划", "改了设定之后重排后面的话"),
    ]),
    ("分镜层（场景/节拍）", [
        ("场景_节拍拆解.txt", "节拍", "一场戏拆成几个节拍"),
        ("场景_导演分镜.txt", "场景分镜", "节拍 → 镜头"),
        ("场景_文戏单元.txt", "文戏单元", "对话戏的单元写法"),
        ("节拍_分镜.txt", "节拍分镜", "节拍 → 分镜"),
        ("视频导演_总规则.txt", "总规则", "视频层通用规则"),
    ]),
    ("旧流程（V41_PIPELINE=old 才走）", [
        ("原文改剧本_指令词.txt", "旧·编剧", "正文 → 剧本（旧写法）"),
        ("导演_分镜_指令词.txt", "旧·分镜导演", "剧本 → 分镜"),
        ("视频_MiniMax单元_指令词.txt", "旧·视频单元", "分镜 → 视频提示词"),
    ]),
    ("其他", [
        ("正文_提取设定.txt", "提取设定", "正文 → 世界设定"),
        ("通用_扩写.txt", "通用扩写", "任意文本扩写"),
    ]),
]
_ACTIVE = [f for _, items in _CHAIN for f, _l, _d in items]
_LABELS = {f: l for _, items in _CHAIN for f, l, _d in items}
_DESCS = {f: d for _, items in _CHAIN for f, _l, d in items}
_GROUP = {f: g for g, items in _CHAIN for f, _l, _d in items}

# 占位符 → 它把哪张预设表填进指令词。
# 照 authoring._fill 的实现列的：_fill 就是按这些占位符做替换，
# 所以「这份指令词吃哪些预设」＝「文件里出现了哪些占位符」，不是猜的。
_PH_PRESET = {
    "【WORLD】": ["世界类型（选项和内容）", "世界补充设定·每种的设定"],
    "【STRENGTH】": ["视觉设计强度（选项和内容）"],
    "【STYLE】": ["画风（选项和内容）"],
    "【GENRE】": ["影片类型·每种怎么拍"],
    "【LOOK】": ["角色审美·每种长什么样"],
    "【POV】": ["视点·每种的机位规则"],
    "【SCALE】": ["内容尺度（选项和内容）"],
    "【CHARACTERS】": ["人物种族·每种的骨骼特征", "脸型·每种的定义", "身材·每种的定义",
                      "服装款式·每种的定义", "姿态·每种的定义", "颜值档·每档的写法"],
}


# 占位符本身是干什么的——按钮上直接写这句，不用去正文里找
_PH_NOTE = {
    "【WORLD】": "世界设定",
    "【STRENGTH】": "视觉设计强度",
    "【STYLE】": "画风",
    "【GENRE】": "影片类型",
    "【LOOK】": "角色审美",
    "【POV】": "视点（摄影机）",
    "【SCALE】": "内容尺度",
    "【CHARACTERS】": "人物卡",
}


def _file_presets(path):
    """这份指令词有哪些占位符、每个占位符会填进哪些预设表。

    按占位符出，不按表出——用户要的是「指令词里的【WORLD】对应哪些东西」，
    一个占位符可能带好几张表。
    """
    try:
        txt = path.read_text(encoding="utf-8")
    except Exception:
        return []
    out = []
    for ph, tables in _PH_PRESET.items():
        if ph in txt:
            out.append({"ph": ph, "note": _PH_NOTE.get(ph, ""), "tables": tables})
    return out


def _safe(name):
    name = os.path.basename(str(name or ""))
    if not name or name.startswith(".") or "/" in name or "\\" in name:
        return None
    if not (name.endswith(".txt") or name.endswith(".md")):
        return None
    return INS / name


@get("/api/instructions/list")
def ins_list(h, path, q):
    files = []
    for f in sorted(INS.glob("*.txt")) + sorted(INS.glob("*.md")):
        try:
            st = f.stat()
            files.append({"name": f.name, "active": f.name in _ACTIVE,
                          "label": _LABELS.get(f.name, ""),
                          "desc": _DESCS.get(f.name, ""),
                          "group": _GROUP.get(f.name, "没在用（备份/案例/旧版）"),
                          "presets": _file_presets(f),
                          "size": st.st_size, "mtime": st.st_mtime})
        except Exception:
            pass
    # 在用的按 _ACTIVE 顺序置顶，其余按名字
    order = {n: i for i, n in enumerate(_ACTIVE)}
    files.sort(key=lambda x: (0, order[x["name"]]) if x["active"] else (1, x["name"]))
    return _RESP({"ok": True, "data": {
        "files": files,
        "groups": [g for g, _ in _CHAIN] + ["没在用（备份/案例/旧版）"]}})


@get("/api/instructions/read")
def ins_read(h, path, q):
    import urllib.parse
    qs = urllib.parse.parse_qs(q or "")
    fp = _safe(qs.get("name", [""])[0])
    if not fp or not fp.exists():
        return _RESP({"ok": False, "error": "指令词不存在"}, 404)
    return _RESP({"ok": True, "data": {"name": fp.name,
                  "content": fp.read_text(encoding="utf-8", errors="replace")}})


@post("/api/instructions/save")
def ins_save(h, path, d):
    fp = _safe(d.get("name"))
    if not fp or not fp.exists():
        return _RESP({"ok": False, "error": "只能编辑已有的指令词文件"}, 400)
    content = d.get("content")
    if not isinstance(content, str) or not content.strip():
        return _RESP({"ok": False, "error": "内容为空，没保存"}, 400)
    # 写前备份旧版到 _bak/
    try:
        _BAK.mkdir(parents=True, exist_ok=True)
        old = fp.read_text(encoding="utf-8", errors="replace")
        (_BAK / ("%s.%d.bak" % (fp.name, int(time.time())))).write_text(old, encoding="utf-8")
    except Exception:
        pass
    fp.write_text(content, encoding="utf-8")
    return _RESP({"ok": True, "data": {"name": fp.name, "bytes": len(content)}})


@get("/api/config/defaults")
def cfg_get(h, path, q):
    data = {}
    try:
        if _DEFAULTS.exists():
            import json
            data = json.loads(_DEFAULTS.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    return _RESP({"ok": True, "data": {"defaults": data}})


@post("/api/config/defaults/save")
def cfg_save(h, path, d):
    import json
    keep = ("world_type", "style", "visual_strength", "content_tendencies",
            "custom_content_scale", "extra_requirements", "pov",
            "video_size_tier", "video_steps")
    # 设置页仍只显示原有控件；保存时保留未显示的视点、分辨率和采样默认值。
    data = {}
    if _DEFAULTS.exists():
        saved = json.loads(_DEFAULTS.read_text(encoding="utf-8"))
        data = {k: v for k, v in saved.items() if k in keep}
    data.update({k: d.get(k) for k in keep if d.get(k) is not None})
    try:
        _DEFAULTS.parent.mkdir(parents=True, exist_ok=True)
        _DEFAULTS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        return _RESP({"ok": False, "error": str(e)[:120]}, 500)
    return _RESP({"ok": True, "data": {"defaults": data}})
