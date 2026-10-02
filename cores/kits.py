# -*- coding: utf-8 -*-
"""kits.py — 五维设定与套装库（2026-08-29 用户定）。

维度正交，各管一层：
    world 世界＝内容 / genre 影片类型＝叙事结构 / art 画风＝表现媒介
    look 角色审美＝人物长相体型 / pov 视点＝摄影机身份

**套装不是必须的**：智能配套是「Qwen 读一句话直接输出五维值」，
套装库只是加速和兜底——库里没有的题材照样能配，配完还能存成新套装。
用户明确说到的东西优先于套装（说了「第一人称」就用 POV，不许被套装覆盖）。
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT_FILE = ROOT / "presets" / "style_kits.json"
USER_KIT_FILE = ROOT / "presets" / "style_kits_user.json"   # 用户自己存的套装

DIMS = ("world", "genre", "art", "pov")    # look（角色审美）已撤，由画风推出（2026-09-04）
_CACHE = {"mtime": 0, "data": None}


def _load_raw():
    st = KIT_FILE.stat().st_mtime
    if _CACHE["data"] is None or _CACHE["mtime"] != st:
        _CACHE["data"] = json.loads(KIT_FILE.read_text(encoding="utf-8"))
        _CACHE["mtime"] = st
    return _CACHE["data"]


def data():
    """全量配置：内置 + 用户自存套装（用户的排前面，优先命中）。"""
    d = json.loads(json.dumps(_load_raw()))    # 深拷贝，别污染缓存
    if USER_KIT_FILE.exists():
        try:
            u = json.loads(USER_KIT_FILE.read_text(encoding="utf-8"))
            d["kits"] = list(u.get("kits") or []) + list(d.get("kits") or [])
        except Exception:
            pass
    return d


def world_options():
    """四界（2026-09-04 用户定）。旧的八种加补充世界由 style_presets.world_group 归并。"""
    from . import style_presets as _sp
    return list(_sp.WORLD_TYPES)


def art_options():
    """四风（2026-09-04 用户定）。旧画风名由 style_presets.style_group 归并。"""
    from . import style_presets as _sp
    return list(_sp.STYLE_NAMES)


def genre_group(value):
    """任意影片类型值 → 四型之一；认不出返回空。"""
    v = str(value or "").strip()
    d = _load_raw().get("dimensions") or {}
    opts = (d.get("genre") or {}).get("options") or {}
    if v in opts:
        return v
    return ((d.get("genre") or {}).get("legacy_map") or {}).get(v, "")


def genre_spec(value):
    """四型的完整规格（persona/structure/beats/turn/stakes/pace/shot_bias/engine）。"""
    g = genre_group(value)
    return spec("genre", g) if g else {}


def options(dim):
    d = _load_raw().get("dimensions") or {}
    if dim == "world":
        return world_options()
    if dim == "art":
        return art_options()
    return list((d.get(dim) or {}).get("options") or {})


def spec(dim, value):
    """取某个维度某个值的规则包。世界的补充项在 world_extra 里。"""
    d = _load_raw().get("dimensions") or {}
    if dim == "world":
        return ((d.get("world_extra") or {}).get("options") or {}).get(value) or {}
    return ((d.get(dim) or {}).get("options") or {}).get(value) or {}


def kits():
    return data().get("kits") or []


def find_kit(name):
    for k in kits():
        if str(k.get("name")) == str(name):
            return k
    return None


def guess_kit(text):
    """纯文本匹配套装：名字或别名出现在句子里就算命中。
    这是 Qwen 之前的快速通道，也是 Qwen 失败时的兜底。"""
    t = re.sub(r"\s+", "", str(text or ""))
    if not t:
        return None
    best, best_len = None, 0
    for k in kits():
        for key in [k.get("name")] + list(k.get("aka") or []):
            key = re.sub(r"\s+", "", str(key or ""))
            if key and key in t and len(key) > best_len:
                best, best_len = k, len(key)
    return best


def save_user_kit(name, dims, aka=None, desc=""):
    """存一个用户自己的套装。同名覆盖。"""
    cur = {"kits": []}
    if USER_KIT_FILE.exists():
        try:
            cur = json.loads(USER_KIT_FILE.read_text(encoding="utf-8"))
        except Exception:
            cur = {"kits": []}
    ks = [k for k in (cur.get("kits") or []) if str(k.get("name")) != str(name)]
    ks.insert(0, {"name": name, "aka": list(aka or []), "desc": desc,
                  "dims": {k: v for k, v in (dims or {}).items() if k in DIMS and v},
                  "user": True})
    cur["kits"] = ks
    USER_KIT_FILE.write_text(json.dumps(cur, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    return cur["kits"][0]


# ── 智能配套 ────────────────────────────────────────────────
_DETECT_SYS = """你是一个影视企划助手。用户会用一句话说他想看什么，你要判断这句话对应的五个设定维度，并从给定选项里各选一个。

【硬规矩】
1. 用户句子里**明说过的东西一律照他说的选**（说了「第一人称」就选第一视角POV，说了「厚涂」就选厚涂插画，说了「3D」就选3D游戏模型）。
2. 用户没说到的维度，按这个题材最常见、最合适的来选。
3. 只能从下面给出的选项里选，**一个字都不许改，不许自创**。
4. 内容尺度只在句子里有明确暗示时才选（黑暗、血腥、恐怖、成人、情欲这类词）。

【可选值】
世界类型：%(world)s（东方=古代修仙/道教/武侠；西方=中世纪骑士法师奇幻；未来=科幻/赛博朋克/机甲；现代=当代城市/校园/末日）
影片类型：%(genre)s
  · 冒险＝遇到危险、解决危险、一步步变强（闯秘境、打怪物、守隘口、救人、探险）
  · 爽文＝扮猪吃老虎，主角一开始被看轻、被羞辱，亮底牌后一次碾压（废物、打脸、逆袭、当众羞辱）
  · 犯罪＝主角为了钱或人去抢、偷、骗、劫、伤，**或者作案之后逃避追捕**（抢银行、劫案、越狱、被警察追、销赃、绑架）
  · 公路＝带着目标出发，一站一站到新地方遇新人（旅途、护送、搭车、找人、returning home）
  ★判别顺序：先看主角**在做什么违法的事或在躲谁**（有就是犯罪），再看**是不是被看轻后打脸**（是就是爽文），
    再看**是不是在打怪解决危险**（是就是冒险），最后才考虑公路。追车、逃跑、被警察追一律算犯罪，不是公路。
画风：%(art)s（电影质感=专门布光讲究构图的华丽真实；写实生活=像随手拍的现实照片；平面动漫=纯平面二维；3D游戏=虚幻5渲染感）
视点：%(pov)s
内容尺度（可多选，可为空）：黑暗（剧情绝望，总往坏的方向走）、血腥（断肢/内脏/大出血，可到最重）、成人向·情爱（诱惑/暧昧/亲密关系）、极致诱惑（卖肉向·不露点之外全露）

【已有套装（命中就直接用它的搭配，没命中就自己组一套）】
%(kits)s

【输出】只输出一个 JSON，不要任何解释文字：
{"world":"","genre":"","art":"","pov":"","scale":[],"kit":"命中的套装名，没有就空字符串","title":"给这个作品起的名字，四到八个字","why":"一句话说明你为什么这么配，二十字以内"}"""


def detect(one_line, call_model=None):
    """一句话 → 五维配置。返回 dict，带 _source 说明来源。

    先让 Qwen 判断（它能处理库里没有的题材）；失败或跑偏了就退回文本匹配套装；
    再不行给一套通用默认。任何情况下都返回一个可用的配置，不抛异常。
    """
    text = str(one_line or "").strip()
    if not text:
        raise ValueError("先写一句话")

    kit_lines = "\n".join(
        "- %s（%s）：世界=%s 类型=%s 画风=%s 视点=%s"
        % (k.get("name"), "／".join(list(k.get("aka") or [])[:3]),
           (k.get("dims") or {}).get("world", ""), (k.get("dims") or {}).get("genre", ""),
           (k.get("dims") or {}).get("art", ""), (k.get("dims") or {}).get("pov", ""))
        for k in kits())
    sysmsg = _DETECT_SYS % {
        "world": "、".join(options("world")),
        "genre": "、".join(options("genre")),
        "art": "、".join(options("art")),
        "pov": "、".join(options("pov")),
        "kits": kit_lines}

    got = {}
    if call_model is None:
        def call_model(s, u, **kw):
            from models import qwen_client
            return qwen_client.chat(s, u, max_tokens=700, reasoning=False, **kw)
    try:
        raw = call_model(sysmsg, "用户想看：" + text, temperature=0.2) or ""
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            got = json.loads(m.group(0))
    except Exception:
        got = {}

    out = {"_source": "qwen"}
    for d in DIMS:
        v = str(got.get(d) or "").strip()
        out[d] = v if v in options(d) else ""
    # 【类型关键词兜底】模型把"抢完银行逃逮捕"判成过公路片（2026-09-05）。一句话里的强信号优先。
    _kw = [("犯罪", r"抢劫|抢银行|抢完|打劫|劫案|偷|盗|越狱|通缉|逃避追捕|逃逮捕|躲警察|被警察|警车|追捕|销赃|绑架|勒索|贩毒|杀手接活"),
           ("爽文", r"废物|废柴|被看轻|被羞辱|当众羞辱|打脸|逆袭|扮猪吃虎|退婚|嘲笑他|瞧不起"),
           ("冒险", r"怪物|巨兽|魔物|妖兽|秘境|地下城|副本|闯进|探险|守住|挡住|封印|生存|丧尸|遗迹"),
           ("公路", r"上路|出发去|一路|旅途|搭车|护送|长途|返乡|找回家|沿途")]
    _hit = next((g for g, pat in _kw if re.search(pat, text)), "")
    if _hit and out.get("genre") != _hit:
        out["genre"] = _hit
        out["_source"] = (out.get("_source") or "") + "+类型关键词"

    # Qwen 没配全 → 用文本命中的套装补
    kit = find_kit(got.get("kit")) or guess_kit(text)
    if kit:
        for d in DIMS:
            if not out[d]:
                out[d] = (kit.get("dims") or {}).get(d, "")
        out["kit"] = kit.get("name")
        if out["_source"] == "qwen" and not got:
            out["_source"] = "kit"
    # 还有空的 → 通用默认
    _fallback = {"world": "现代", "genre": "公路",
                 "art": "电影质感", "pov": "电影第三人称"}
    for d in DIMS:
        if not out[d]:
            out[d] = _fallback[d]
            out["_source"] = out.get("_source", "") + "+默认"

    # 尺度：Qwen 给的 + 影片类型自带的，去重
    # 去重要认前缀：Qwen 可能只写「黑暗」，类型自带的是「黑暗（剧情绝望…）」，
    # 直接比字符串会当成两条，出现「黑暗、黑暗、血腥」
    def _key(s):
        return re.split(r"[（(]", str(s or ""), 1)[0].strip()

    _valid = {_key(x): x for x in
              ["黑暗（剧情绝望，总往坏的方向走）", "血腥（断肢/内脏/大出血，可到最重）",
               "成人向·情爱（诱惑/暧昧/亲密关系）", "极致诱惑（卖肉向·不露点之外全露）"]}
    scale, seen_k = [], set()
    for s in list(got.get("scale") or []) + list(spec("genre", out["genre"]).get("scale") or []):
        k = _key(s)
        if k in _valid and k not in seen_k:
            seen_k.add(k)
            scale.append(_valid[k])     # 一律用完整表述，和界面选项对得上
    out["scale"] = scale
    out["title"] = str(got.get("title") or "").strip()[:20]
    out["why"] = str(got.get("why") or "").strip()[:40]
    return out


# ── 把五维翻译成指令词用的文字块 ──────────────────────────
def persona(dim, value, role=""):
    """维度给的身份句片段。role 用于画风区分角色/场景设计师。"""
    sp = spec(dim, value)
    if dim == "art":
        return ""      # 画风的身份由 render_notes 那套负责
    return str(sp.get("persona") or "")


def genre_block(value):
    """影片类型 → 写故事/剧本时的叙事规则块（旧类型名自动归到四型）。"""
    g = genre_group(value)
    sp = spec("genre", g) if g else {}
    if not sp:
        return ""
    return ("【影片类型：%s】\n你的身份：%s。\n叙事结构：%s\n节奏：%s"
            % (g, sp.get("persona", ""), sp.get("structure", ""), sp.get("pace", "")))


def genre_shot_block(value):
    """影片类型 → 分镜时的镜头倾向块。"""
    g = genre_group(value)
    sp = spec("genre", g) if g else {}
    if not sp:
        return ""
    return ("【本片类型：%s】镜头倾向：%s 单段时长倾向：%s"
            % (g, sp.get("shot_bias", ""), sp.get("seg_seconds", "")))


def look_block(value):
    """角色审美 → 人设卡/人设图规则块。"""
    sp = spec("look", value)
    if not sp:
        return ""
    out = ("【角色审美：%s】\n脸：%s\n身形：%s\n夸张度：%s"
           % (value, sp.get("face", ""), sp.get("body", ""), sp.get("exaggeration", "")))
    # 身材是从固定选项挑标签的，光给文字描述 Qwen 会照旧选「极瘦高挑」——
    # 实测「游戏夸张体型」写了长腿宽胯，出来的卡还是纤细（2026-08-29）。
    if sp.get("build_hint"):
        out += "\n★身材标签就按这个挑：" + sp["build_hint"]
    return out


def pov_writing_block(value):
    """视点 → **写故事和剧本时**的人称与视角规则。

    2026-08-29 实测教训：视点只接分镜层没用——故事是第三人称写的
    （「埃里克踢开枯木」），导演照着剧本走，POV 段照样把主角拍进画面。
    视角必须从文字层就定下来。
    """
    v = str(value or "")
    if v == "第一视角POV":
        return ("【视角：第一人称主观】\n"
                "整篇用**第一人称「我」**写。只写「我」看得见、听得见、感觉得到的东西——"
                "别人的表情动作可以写（我看着他），但**绝不许写我自己的样子**"
                "（不写「我英俊的脸」「我的身影」这类我看不见的东西），"
                "也不许写我不在场时发生的事。别人对我说话时，写他看着我的眼睛说。"
                "我的身体只在我自己看得到的部分出现：我的手、我拿着的东西、我的脚。")
    if v == "自拍手持":
        return ("【视角：自拍】\n"
                "主角**自己举着相机在拍自己**，边拍边说。整篇要写出他对着镜头说话的样子——"
                "他跟镜头（也就是观众）讲话、介绍眼前的东西、突然把镜头转向别处再转回来。"
                "旁人入镜时他会介绍对方，对方也知道在被拍。"
                "写作时把「他举着相机」这个事实一直保持住，不要写成旁观者视角。")
    if v == "监控偷窥":
        return ("【视角：监控】\n"
                "像一台架在角落的摄像头在看：只写画面里发生的事，"
                "不写任何人的内心活动和摄像头拍不到的地方。人物可以走出画面，"
                "走出去之后发生了什么就不知道了。")
    if v == "纪录片跟拍":
        return ("【视角：纪录片跟拍】\n"
                "有一个摄影师在现场跟着他们。人物知道自己在被拍，"
                "偶尔会瞥镜头一眼、对镜头解释一句。写法保持在场感和临场的粗糙。")
    return ""


def pov_block(value):
    """视点 → 分镜的整组机位规则（替换第三人称那套锚）。"""
    sp = spec("pov", value)
    if not sp:
        return ""
    parts = ["【视点：%s】你的身份：%s" % (value, sp.get("persona", "")),
             "摄影机：" + sp.get("camera", ""),
             "机位与站位：" + sp.get("anchors", ""),
             "看镜头：" + sp.get("look_at_lens", ""),
             "运镜：" + sp.get("movement", ""),
             "过渡拍：" + sp.get("transition", "")]
    if sp.get("must"):
        parts.append("★必须遵守：" + sp["must"])
    return "\n".join(parts)


def world_block_extra(value):
    """补充世界类型（克系/武侠/蒸汽朋克）→ 世界规则块。原有八种由 authoring 负责。"""
    sp = spec("world", value)
    if not sp:
        return ""
    return ("【世界：%s】时代：%s\n世界规则：%s\n材料与器物：%s\n绝不能出现：%s"
            % (value, sp.get("era", ""), sp.get("rules", ""),
               sp.get("material", ""), sp.get("ban", "")))
