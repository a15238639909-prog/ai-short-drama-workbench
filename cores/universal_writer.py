"""Shared prose-first planning/writing. Pure prompt assembly, bounded model calls.

The old screenplay/keyword pipeline is available via V41_WRITING_FLOW=legacy.
Model review is an estimate with quoted evidence, never a human quality guarantee.
"""
import copy
import hashlib
import json
import os
import re
from pathlib import Path

INS = Path(__file__).resolve().parent.parent / "presets" / "instructions"
ROW_TEXT = ("one_line", "pace", "cast", "place", "resistance", "landing", "change", "start", "goal", "geometry")
# 整部规划的附加栏（P285）：原样带着、不进行指纹——改这些不算"用户改了正文依据"
ROW_EXTRA = ("uid", "title", "purpose", "scenes", "beats", "duration", "link_in", "link_out", "focus", "pace_why")


def enabled():
    return os.environ.get("V41_WRITING_FLOW", "universal").lower() != "legacy"


def signature(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def instruction(name):
    """指令词文件。V41_PROSE_TEMPLATE=B 时「小说正文」读 通用写作_小说正文_B.txt（P283 两种写法对照）；
    没有那个文件就退回默认的。"""
    var = str(os.environ.get("V41_PROSE_TEMPLATE") or "").strip()
    if var and name == "小说正文":
        alt = INS / ("通用写作_%s_%s.txt" % (name, var))
        if alt.exists():
            return alt.read_text(encoding="utf-8")
    var2 = str(os.environ.get("V41_SPLIT_TEMPLATE") or "").strip()      # 整部规划对照：分话结构的 B 版
    if var2 and name == "分话结构":
        alt = INS / ("通用写作_%s_%s.txt" % (name, var2))
        if alt.exists():
            return alt.read_text(encoding="utf-8")
    return (INS / ("通用写作_" + name + ".txt")).read_text(encoding="utf-8")


def source(one, settings):
    s = settings or {}
    # One authoritative user block. Do not import generated character cards or old briefs.
    _one = str(one or s.get("one_line") or "").strip()
    if not _one and isinstance(s.get("story_plan"), dict):
        _one = str(s["story_plan"].get("idea") or "").strip()            # P316：一句话框已删，想法在整部规划里；不能让写手拿到空的故事要求
    data = {"故事要求": _one}
    _pres = str((s.get("story_plan") or {}).get("presentation") or "").strip() if isinstance(s.get("story_plan"), dict) else ""
    if _pres:
        data["整体怎么展示（用户写的）"] = _pres
    for label, key in (("已确认事实", "story_canon"), ("特殊要求", "extra_requirements"),
                       ("世界", "world_type"), ("题材", "genre"), ("题材引擎", "genre_engine"),
                       ("视角", "pov"), ("自定尺度", "custom_content_scale")):
        if s.get(key):
            data[label] = copy.deepcopy(s[key])
    if s.get("content_tendencies"):
        from . import authoring
        data["内容尺度"] = authoring._scale_block(s, ("story",))
    return data


def dump(data):
    return json.dumps(data, ensure_ascii=False, indent=2)


def _json_candidates(text):
    """从模型输出里挑出可能是 JSON 的那几段，按靠谱程度排（P194）。

    原来只认"整段恰好是一个 ``` 围栏"这一种。模型多写一句"好的，这是分话结构："
    就 json.loads 抛异常、整篇故事没有了。这里不猜内容，只是把壳剥干净。
    """
    t = str(text or "").strip()
    out = []
    fence = re.findall(r"```(?:json|JSON)?\s*\n(.*?)```", t, re.S)
    out += [x.strip() for x in fence if x.strip()]
    if t.startswith("```") and t.endswith("```") and "\n" in t:
        out.append(t.split("\n", 1)[1].rsplit("```", 1)[0].strip())
    i, j = t.find("{"), t.rfind("}")
    if 0 <= i < j:
        out.append(t[i:j + 1])
    out.append(t)
    seen, uniq = set(), []
    for x in out:
        if x and x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


# 模型写秒数区间时常不加引号（"total_seconds": 150-220 / "seconds": 8-15），JSON 非法（P290 实测一份规划因此整个丢掉）。
# 只补引号，不改内容。
_BARE_RANGE = re.compile(r'(:\s*)(\d+\s*[-~～]\s*\d+)(\s*[,}\]\n])')


def repair_json_text(candidate):
    return _BARE_RANGE.sub(lambda m: '%s"%s"%s' % (m.group(1), m.group(2).replace(" ", ""), m.group(3)), candidate)


def parse_object(text):
    last = None
    for candidate in _json_candidates(text):
        _fixed = repair_json_text(candidate)
        for attempt in (candidate, re.sub(r",\s*([}\]])", r"\1", candidate), _fixed, re.sub(r",\s*([}\]])", r"\1", _fixed)):
            try:
                obj = json.loads(attempt)
            except Exception as error:
                last = error
                continue
            if isinstance(obj, dict):
                return obj
            last = ValueError("模型没有返回对象")
    raise ValueError("模型输出里取不出 JSON 对象：%s" % (last or "空内容"))


def call(system, user, mt, temperature, model=None, task="novel_chapter"):
    if model is None:
        from .authoring import _q
        model = _q
    from .reasoning_policy import get_policy
    policy = get_policy(task)
    text = model(system, user, mt=max(mt, policy["max_tokens"]), temperature=temperature,
                 _tries=1, reasoning=policy["enable_thinking"])
    if not str(text or "").strip():
        raise ValueError("模型返回空内容")
    return str(text).strip()


def row_signature(row):
    return signature({k: row.get(k) for k in ROW_TEXT + ("events",)})


# 分话表每一行的栏，分两组（P193／P194）：
# 硬的——不知道发生什么、不知道停在哪，这一话就写不出来，照旧拦；
# 软的——写作那一步有梗概和前文兜得住，只提醒，不因为一栏空就让整篇没有。
_ROW_HARD_FIELDS = (("events", "事件表"), ("landing", "落点"))
_ROW_SOFT_FIELDS = (("goal", "本话目的"), ("change", "变化"), ("place", "地点"), ("cast", "在场人物"))


def plan_problems(rows):
    """分话表真正写不下去的问题（P179／P193）：这一话和上一话演的是同一件事。

    「变化写得太薄」不在这里——那条只提醒不拦，见 consistency.row_thin_change。
    """
    from .consistency import row_no_progress
    return row_no_progress(rows or [])


def plan_hints(rows):
    """分话表只提醒、不拦的（P193）。"""
    from .consistency import row_thin_change
    return row_thin_change(rows or [])


# 模型偶尔把 one_line 这个键写错（实测出现过 one_name），那一行别的字段全是好的。
# 整行丢掉会让话数对不上，进而把**整份结构**判为不合格——一个错别字换来一个字都没有。
_ONE_LINE_KEYS = ("one_line", "one_name", "oneline", "one", "line", "summary", "brief", "text")


# 「秀英(回忆)」「苏奶奶（声音）」「老林[病中]」——名字后面挂的注释（P196）
_NAME_NOTE = re.compile(r"[（(\[【][^）)\]】]*[）)\]】]\s*$")


def _base_name(name):
    """剥掉名字尾巴上的括号注释。剥不出东西就原样返回。"""
    base = _NAME_NOTE.sub("", str(name or "")).strip()
    return base or str(name or "").strip()


def _one_line_of(raw):
    """这一行的一句话。正名没有就认几个常见的写错法（P192）。"""
    for key in _ONE_LINE_KEYS:
        v = str(raw.get(key) or "").strip()
        if v:
            return v, key
    return "", ""


def normalize_rows(rows):
    out = []
    for index, raw in enumerate(rows or [], 1):
        if not isinstance(raw, dict):
            continue
        one, used = _one_line_of(raw)
        if not one:
            continue
        raw = dict(raw, one_line=one) if used != "one_line" else raw
        row = {k: str(raw.get(k) or "").strip() for k in ROW_TEXT}
        row["no"] = index
        row["events"] = [str(x).strip() for x in raw.get("events", []) if isinstance(x, str) and x.strip()]
        prior = raw.get("writing_row_sig")
        if prior and prior != row_signature(row):
            # A text edit invalidates hidden generated instructions. Keep the user's row.
            for key in ("start", "goal", "resistance", "landing", "change"):
                row[key] = ""
            row["events"] = []
        row["writing_row_sig"] = row_signature(row)
        for key in ROW_EXTRA:
            if key in raw and raw.get(key) not in (None, ""):
                row[key] = raw.get(key)
        out.append(row)
    return out


def active_design(settings):
    s = settings or {}
    design = s.get("story_design") or {}
    if not isinstance(design, dict):
        return {}
    if design.get("source_sig") != signature(source(s.get("one_line"), s)):
        return {}
    if design.get("rows_sig") != signature(normalize_rows(s.get("plan_rows"))):
        return {}
    return design


def save_design(settings, rows, design=None):
    settings["plan_rows"] = normalize_rows(rows)
    design = copy.deepcopy(design or {})
    if design:
        fresh = signature(settings["plan_rows"])
        # 之前盖过章、而且和这批行对不上 —— 只有这一种情况算"用户手改过结构"。
        # 刚规划出来的 design 还没有 rows_sig（章就是这儿盖的），不能算改过，
        # 按"对不上就摘"写会把每篇新故事的梗概当场摘掉。
        if design.get("rows_sig") and design["rows_sig"] != fresh and design.get("synopsis"):
            # 梗概讲的还是旧情节，不能再当【完整故事梗概】喂给写作。
            # 人物表留着——手改结构时最不该丢的就是它（P187 那次连它一起丢了）。
            design["synopsis_dropped"] = "结构被手改过，原梗概已过期，只保留人物表"
            design["synopsis"] = ""
        elif design.get("synopsis"):
            design.pop("synopsis_dropped", None)
        # 保存这一刻，设计和行按上面的判断对齐：按**实际存下去的行**重算指纹。
        # 用调用方算好的那个，行只要被规整动过一个字，整份设计就会被丢掉（实测）。
        design["rows_sig"] = fresh
        design["source_sig"] = signature(source(settings.get("one_line"), settings))
    settings["story_design"] = design
    if not active_design(settings):
        settings.pop("story_design", None)
        settings.pop("plan_people", None)
    else:
        settings["plan_people"] = copy.deepcopy(design.get("people") or [])


def plan(one, settings=None, n_eps=0, notes="", log=None, model=None):
    s = settings or {}
    src = source(one, s)
    if notes:
        src["本次补充要求"] = str(notes)
    result = {"rows": [], "people": [], "tone": "", "problems": [], "hard": [],
              "warnings": [], "points": [], "calls": 0, "n": 0, "design": {},
              "source_sig": signature(src), "treatment_template_sig": signature(instruction("完整故事"))}
    try:
        want = int(n_eps or 0)
        if want < 0 or want > 24:
            raise ValueError("话数应为自动或 1～24 话")
        cached = s.get("plan_draft") or {}
        if (cached.get("resume_treatment") and cached.get("source_sig") == result["source_sig"]
                and cached.get("treatment_template_sig") == result["treatment_template_sig"] and cached.get("draft")):
            synopsis = str(cached["draft"])
            result["reused_treatment"] = True
        else:
            result["calls"] += 1
            synopsis = call(instruction("完整故事"), dump(src), 6400, 0.7, model, task="master_plan")
        result["draft"] = synopsis
        result["calls"] += 1
        task = {"用户依据": src, "完整故事梗概": synopsis, "话数": want or "按内容决定，1～8话"}
        raw = call(instruction("分话结构"), dump(task), 6500, 0.3, model, task="universal_split")
        result["structure_draft"] = raw
        # 不自动重试：取 JSON 的办法已经做扎实（P194 _json_candidates），
        # 真的取不出来就是模型给了别的东西，重试是碰运气。梗概已缓存，再点一次只花 1 次调用。
        obj = parse_object(raw)
        people = obj.get("people")
        rows = obj.get("rows")
        issues = obj.get("issues")
        if not isinstance(rows, list) or not rows or not isinstance(people, list):
            raise ValueError("结构缺少 rows 或 people，不能视为通过")
        if not isinstance(issues, list):
            # 没问题的时候模型很可能压根不写这个键。缺它不是结构坏了（P193）
            issues = []
        result["rows"] = normalize_rows(rows)
        result["people"] = people
        result["tone"] = str(obj.get("tone") or "")
        names = {p.get("name") for p in people if isinstance(p, dict) and isinstance(p.get("name"), str)}
        # P186：在场栏里冒出人物表没有的名字——先修，别整篇打回
        fixes = []
        source_text = str(synopsis or "") + str(source(one, s) or "")
        for _i, _raw in enumerate(rows, 1):
            if isinstance(_raw, dict) and not str(_raw.get("one_line") or "").strip():
                _v, _k = _one_line_of(_raw)
                if _v:
                    fixes.append("第%d话的「一句话」键名写成了 %s，已按 one_line 读取" % (_i, _k))
        for row in result["rows"]:
            kept = []
            for name in [x.strip() for x in str(row.get("cast") or "").split("、") if x.strip()]:
                if name in names:
                    kept.append(name)
                elif _base_name(name) in names:
                    # 「秀英(回忆)」「苏奶奶（声音）」——模型给名字加了注释。
                    # 剥掉注释就是人物表里的人，别把真在场的人整个删掉（P196）
                    base = _base_name(name)
                    kept.append(base)
                    fixes.append("在场栏的「%s」按人物表读成「%s」" % (name, base))
                elif name in source_text:
                    people.append({"name": name, "role": "（结构里出现，模型没写身份）",
                                   "relation": "", "unnamed": False})
                    names.add(name)
                    kept.append(name)
                    fixes.append("把「%s」补进人物表（梗概或你的要求里提到过）" % name)
                else:
                    fixes.append("「%s」只在在场栏出现、别处查不到，已从在场栏去掉" % name)
            if row["cast"] != "、".join(kept):
                row["cast"] = "、".join(kept)
                # 改完必须重盖行指纹，否则 normalize_rows 会把这一行当成"用户改过"，
                # 把 events 和结束状态全清掉，连带整份 design 被丢弃（花店第5话实测）
                row["writing_row_sig"] = row_signature(row)
        result["people"] = people
        errors = []
        # ── 人物表：重名去掉、没名字的丢掉，空了才算真坏（P193）──
        seen, kept_people = set(), []
        for p in people:
            if not isinstance(p, dict):
                fixes.append("人物表里有一条不是对象，已丢掉")
                continue
            nm = str(p.get("name") or "").strip()
            if not nm:
                fixes.append("人物表里有一条没写名字，已丢掉")
                continue
            if nm in seen:
                fixes.append("人物表里「%s」出现了两次，已合并" % nm)
                continue
            seen.add(nm)
            kept_people.append(p)
        people = result["people"] = kept_people
        names = set(seen)
        if not names:
            errors.append("人物表为空")
        # ── 话数：8 是给模型的提示，不是铁律；超过 12 才算跑飞 ──
        n_rows = len(result["rows"])
        if n_rows != len(rows):
            fixes.append("模型给了 %d 行，其中 %d 行没有一句话、已丢掉" % (len(rows), len(rows) - n_rows))
        if not n_rows:
            errors.append("一话都没解析出来")
        elif want and n_rows != want:
            fixes.append("你要 %d 话，模型排了 %d 话（结构本身可用，要改在下面直接改）" % (want, n_rows))
        elif not want and n_rows > 12:
            errors.append("自动模式排了 %d 话，超出上限 12 话" % n_rows)
        elif not want and n_rows > 8:
            fixes.append("自动模式排了 %d 话（提示词写的是 1～8 话，内容上没问题就照用）" % n_rows)
        # ── 逐行：缺什么能补的补，补不了的分清真坏还是提醒 ──
        for index, row in enumerate(result["rows"], 1):
            if rows[index - 1].get("no") != index:
                # normalize_rows 已经重新编号了，这条只提醒（P193：原来是硬问题，纯假阳性）
                fixes.append("第%d话的话号模型写成了 %s，已按顺序重编" % (index, rows[index - 1].get("no")))
            if not row.get("start"):
                # 上一话的落点就是这一话的开始状态——确定的推导，不是编
                prev_landing = result["rows"][index - 2].get("landing") if index > 1 else ""
                row["start"] = prev_landing or ("故事从头开始" if index == 1 else "")
                row["writing_row_sig"] = row_signature(row)
                fixes.append("第%d话没写开始状态，已按%s补上" % (index, "上一话的落点" if index > 1 else "故事开头"))
            # 不知道发生什么、或者不知道停在哪 —— 这一话写不出来，是真坏，照旧拦
            missing = [label for key, label in _ROW_HARD_FIELDS if not row.get(key)]
            if missing:
                errors.append("第%d话缺少开始状态、过程或结束结果：没有%s" % (index, "、".join(missing)))
            thin = [label for key, label in _ROW_SOFT_FIELDS if not row.get(key)]
            if thin:
                fixes.append("第%d话这几栏是空的：%s（写作时靠梗概和前文兜，可自己补）"
                             % (index, "、".join(thin)))
            if row["pace"] not in ("日常", "推进", "高潮", "舒缓", "适中", "紧凑"):         # P300：新节奏词也认
                row["pace"] = "适中"
                row["writing_row_sig"] = row_signature(row)
                fixes.append("第%d话的节奏标签不在三种之内，已按「推进」处理" % index)
        # 模型的自由文本意见不是可验证的硬错误；完整原始返回已保存在 structure_draft。
        result["model_notes"] = copy.deepcopy(issues)
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            why = str(issue.get("reason") or "").strip()
            if why:
                fixes.append("模型意见（未确认）：" + why)
            # reason 空 ＝ 它在说"这条核对过、没问题"（P192 实测它这么用），
            # 当成格式错会把整份好结构判死，所以什么都不做
        errors += plan_problems(result["rows"])          # P179：跟上一话演同一件事，真拦
        fixes += plan_hints(result["rows"])              # P193：变化栏写得薄，只提醒
        result["hard"] = list(dict.fromkeys(errors))
        result["problems"] = list(result["hard"])
        result["n"] = len(result["rows"])
        # Notes are carried in the design but the project source fingerprint stays stable.
        result["design"] = {"schema": 1, "synopsis": synopsis, "people": people, "notes": str(notes),
                            "source_sig": signature(source(one, s)),
                            "rows_sig": signature(result["rows"]),
                            "review_status": "structure_usable" if not errors else "needs_revision"}
        result["warnings"] = fixes + ["结构可用性检查已完成；故事质量仍需阅读正文判断"]
    except Exception as error:
        result["hard"] = result["problems"] = ["故事规划未通过：" + str(error)]
        result["resume_treatment"] = bool(result.get("draft"))
        if getattr(error, "partial_text", ""):
            result["truncated_draft"] = error.partial_text
    if log is not None:
        log.append("完整故事梗概→分话结构，调用 %d 次；无自动重写" % result["calls"])
    return result


def writing_input(settings, ep=1, previous=None, brief="", characters=None, brief_override=False):
    s = settings or {}
    single_mode = str(s.get("story_mode") or "").strip().lower() in ("single", "episode", "manual")
    rows = [] if single_mode else normalize_rows(s.get("plan_rows"))
    if ep < 1 or ((not single_mode) and rows and ep > len(rows)):
        raise ValueError("这一话不在当前结构表中")
    row = {"no": ep, "one_line": brief.strip() or s.get("one_line", "")}
    if (not single_mode) and rows:
        row = rows[ep - 1]
    if (brief_override or single_mode) and brief.strip():
        row = {"no": ep, "one_line": brief.strip()}
    data = {"用户依据": source(s.get("one_line"), s),
            "本话": ep, "本话安排": row,
            "篇幅": "约 %s 字" % s.get("prose_words", "3000～4000" if single_mode else "1500～2500"),
            "这是最后一话": True if single_mode else (ep == len(rows) if rows else True)}
    if single_mode:
        data["用户依据"].pop("整体怎么展示（用户写的）", None)
        data["用户依据"].pop("题材引擎", None)
        data["用户依据"]["项目初始事实（背景，不重演）"] = data["用户依据"].pop("故事要求", "")
        data["本话节奏"] = s.get("story_pace", "适中")
        data["本话补充要求"] = s.get("episode_notes", "")
        data["写作模式"] = "单话直写：本话构想里明确安排的事件全部写完；不自动分话，不创建整部世界观，不把事件留给后话。"
        data["允许补充"] = "只补充对白、动作、反应、环境和必要过渡；未指定的普通姓名或细节适量补足。"
        data["禁止擅改"] = "不改变人物身份、事件顺序、行动原因和结果；不新增庞大世界观、隐藏身世、新主线或无关冲突。"
        _cn = [str(x) for x in (s.get("_cast_names") or []) if str(x).strip()]
        if _cn:
            # P445②：人物表固定——口述里的「几个女人 / 男主 / 她们」就是表里的人；只有口述点了名的新人物才能出现
            data["人物表（固定）"] = _cn
            data["人物规则"] = ("正文里出现的人只能是人物表里的这 %d 个，名字一字不改；口述里没点名的称呼（几个女人、男主、她们）指的就是表里的人；"
                            "只有口述里点了名的新人物才能出现。" % len(_cn))
        if str(s.get("_ledger_prev") or "").strip():
            data["上一话结束时的状态（衣着、伤、手里的东西、称呼——照这个接）"] = str(s.get("_ledger_prev")).strip()
        # P366：口述剧情 → 事件卡进写手输入（编号、止于、四类补充、按事件数×节奏算篇幅）
        try:
            from . import oral_story as _os_
            _ev = s.get("_events") or _os_.event_card(brief)                                                # P454①：归并后的事件
            if _ev:
                data.update(_os_.writer_extras(_ev, s.get("story_pace", "适中"), prose_words=s.get("prose_words"),
                                               quotes=(s.get("_quotes") if s.get("_quotes") is not None else _os_.quoted_lines(brief))))   # P420；P455①
            if str(s.get("_writer_table") or "").strip():
                data[_os_._TABLE_KEY] = str(s.get("_writer_table")).strip()          # P429：写法 H 的状态+对白表
        except Exception:
            pass
    design = None if single_mode else active_design(s)
    if design and not single_mode:
        if design.get("synopsis"):
            data["完整故事梗概"] = design["synopsis"]
        elif design.get("synopsis_dropped"):
            # 手改过结构：不给旧梗概，也不装作有一份（P190）
            data["没有完整梗概"] = design["synopsis_dropped"] + "；这一话以本话安排和前文实际内容为准"
        data["统一人物表"] = design["people"]
        if design.get("notes"):
            data["本次补充要求"] = design["notes"]
    elif not rows and characters:
        data["已有主要人物"] = [{k: c.get(k, "") for k in ("name", "role", "relation")}
                              for c in characters if c.get("name")]
    if rows and not single_mode:
        data["分话概览（计划，尚未发生）"] = [r["one_line"] for r in rows]
    # P285：整部规划给这一话的细节——写作按段落快慢分配笔墨、按预计时长定篇幅、按承接开头收尾
    if (not single_mode) and isinstance(row, dict) and (row.get("beats") or row.get("purpose") or row.get("duration")):
        detail = {}
        if row.get("purpose"):
            detail["本话作用"] = row["purpose"]
        if row.get("scenes"):
            detail["场景顺序"] = list(row["scenes"])
        if row.get("beats"):
            detail["段落与快慢（重点段写足对白和动作，带过段一两句交代）"] = [
                {"段": b.get("text"), "写法": b.get("weight")} for b in row["beats"] if isinstance(b, dict)]
        # P300：写作不看预计时长、不按时长定篇幅（第 1 话曾被压成 299 字）。篇幅只按项目字数（参考）。
        _pace_mean = {"舒缓": "多展开互动、观察和情绪变化，给关键交流留空间", "适中": "交代、交流和事件推进相对均衡",
                      "紧凑": "过渡更简洁，行动和回应衔接更快，关键因果仍然完整",
                      "日常": "多展开互动、观察和情绪变化", "推进": "交代、交流和事件推进相对均衡", "高潮": "行动和回应衔接更快，关键因果完整"}
        if row.get("pace"):
            detail["节奏"] = "%s（%s）%s" % (row["pace"], _pace_mean.get(row["pace"], ""), ("——" + row["pace_why"]) if row.get("pace_why") else "")
        if row.get("events"):
            detail["主要过程（按顺序）"] = list(row["events"])
        if row.get("focus"):
            detail["表现重点（写足对白和动作，让读者看出变化）"] = list(row["focus"])
        if row.get("link_in"):
            detail["怎么进入本话"] = row["link_in"]
        if row.get("link_out"):
            detail["本话之后"] = row["link_out"]
        data["本话安排细节"] = detail
        later = [r for r in rows if int(r.get("no") or 0) > int(ep)]
        if later:
            data["后话内容（本话结束时这些还没发生，留给后话写）"] = [
                {"话": r.get("no"), "发生": str(r.get("one_line") or "")[:80]} for r in later]
    data["前文实际内容"] = previous or []
    data["核对目标"] = list(dict.fromkeys(row.get("events", []) + [row.get("landing") or row["one_line"]]))
    return data


def paragraphs(prose):
    return {i: text for i, text in enumerate((p.strip() for p in re.split(r"\n\s*\n", prose) if p.strip()), 1)}


def validate_review(obj, prose, targets):
    if not isinstance(obj, dict):
        raise ValueError("核对结果格式错误")
    obj = copy.deepcopy(obj)
    issues, outcomes, state, checks = obj.get("issues"), obj.get("outcomes"), obj.get("state"), obj.get("checks")
    if not all(isinstance(v, list) for v in (issues, outcomes, state, checks)):
        raise ValueError("核对结果字段不完整")
    if obj.get("verdict") not in ("pass", "fail", "unknown") or not isinstance(obj.get("summary"), str):
        raise ValueError("核对结论或实际摘要缺失")
    parts = paragraphs(prose)
    for item in issues + outcomes + state + checks:
        if not isinstance(item, dict):
            raise ValueError("核对证据格式错误")
        refs = item.get("evidence")
        if not isinstance(refs, list) or any(type(n) is not int or n not in parts for n in refs):
            raise ValueError("核对器引用了不存在的原文段落")
        item["quotes"] = [parts[n] for n in refs]
        item["quote"] = "\n\n".join(item["quotes"])
    if len(checks) != 4 or {x.get("aspect") for x in checks} != {"identity", "time_space", "causality", "boundary"}:
        raise ValueError("核对器没有完成四项独立检查")
    if any(x.get("status") not in ("pass", "fail", "unknown") or not x.get("detail") for x in checks):
        raise ValueError("独立检查结论不完整")
    for item in outcomes:
        no = item.get("target_id")
        if type(no) is not int or not 1 <= no <= len(targets):
            raise ValueError("核对目标编号不存在")
        item["target"] = targets[no - 1]
    if len(outcomes) != len(targets) or {x.get("target") for x in outcomes} != set(targets):
        raise ValueError("核对器没有逐项覆盖本话目标")
    if any(x.get("status") not in ("done", "missing", "uncertain") for x in outcomes):
        raise ValueError("核对状态无效")
    if any(x.get("status") == "done" and not x.get("quote") for x in outcomes):
        raise ValueError("完成判定没有正文证据")
    if any(not x.get("fact") or not x.get("quote") for x in state):
        raise ValueError("延续状态没有正文证据")
    if obj["verdict"] == "pass" and (issues or any(x["status"] != "done" for x in outcomes)
                                    or any(x["status"] != "pass" for x in checks)):
        raise ValueError("核对结果自相矛盾，不能标记通过")
    return dict(obj, prose_sig=signature(prose), checker="model_with_paragraph_evidence")


def story_props(data):
    """这个故事里已经点过名的道具：从用户要求、梗概、本话安排的**纯文本**里抓。

    原来直接扫 JSON 串，抽出了「牌的白车」「默沿主绳」这种跨引号的碎片，
    再拿它去比对，一话能报十几条假的（实测）。
    """
    from .consistency import _prop_words
    parts = []
    for key in ("用户依据", "完整故事梗概", "本次补充要求"):
        v = (data or {}).get(key)
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):
            parts += [str(x) for x in v.values() if isinstance(x, str)]
    row = (data or {}).get("本话安排") or {}
    row_text = "。".join([str(row.get(k) or "") for k in ("one_line", "landing", "change", "start", "goal")]
                        + [str(x) for x in (row.get("events") or []) if isinstance(x, str)])
    # 真道具会**反复出现**（救生衣、主绳）；「好主绳」「滑的伞绳」这种切歪的只会出现一次。
    # 所以：本话安排里点名 + 全故事文本里至少出现两次 + 有更长的同尾候选时取长的。
    whole = "。".join(parts + [row_text])
    keep = []
    for w in set(_prop_words(row_text)):
        if whole.count(w) >= 2:
            keep.append(w)
    out = [w for w in keep if not any(k != w and k.endswith(w) and k in keep for k in keep)]
    return sorted(out)


def merge_consistency(review, prose, data, previous=None):
    """把确定性检查的结论并进模型核对的结论（P179）。

    模型核对实测没有鉴别力（六次全 pass），所以这里**只加不减**：
    确定性检查报了问题，这一话就是 fail，走现有的一次局部修订；
    它没报问题，仍以模型结论为准，不替模型说"好"。
    """
    from . import consistency as cc
    canon = " ".join(str(x) for x in [
        (data or {}).get("用户依据"), (data or {}).get("完整故事梗概"),
        dump((data or {}).get("本话安排") or {})])
    # 前文条目的键随场合不同：「原文」「原文后5500字（截取）」「原文结尾（截取，非完整状态）」「正文」。
    # 原来只读「正文」，取不到任何东西，跨话比对一直是空跑（P187②）。
    history = []
    for x in (previous or []):
        if not isinstance(x, dict):
            continue
        for k, v in x.items():
            if isinstance(v, str) and v.strip() and ("原文" in k or "正文" in k):
                history.append(v)
    people = [str(p.get("name") or "") for p in ((data or {}).get("统一人物表") or [])
              if isinstance(p, dict) and p.get("name")]
    if not people:
        people = re.findall(r"[一-龥]{2,3}", str((data or {}).get("本话安排", {}).get("cast") or ""))
    det = cc.prose_problems(prose, canon=canon, props=story_props(data), history=history, cast=people)
    review = dict(review or {})
    review["deterministic"] = det
    hints = cc.prose_hints(prose)
    if hints:
        review["hints"] = hints          # 只提示不拦（P187④）
    if det:
        review["issues"] = list(review.get("issues") or []) + [
            {"kind": "consistency", "detail": d, "evidence": [], "quote": ""} for d in det]
        review["verdict"] = "fail"
    return review


def model_review_on():
    """模型核对开关。默认关——16 次实测它输出的是常量 pass，检出 0（P182）。"""
    return str(os.environ.get("V41_MODEL_REVIEW", "0")).strip() in ("1", "true", "on")


def review_prose(prose, data, model=None):
    if not model_review_on():
        # 不调模型，也不假装通过：结论是"没查"，真正的闸门是确定性一致性检查
        return {"verdict": "skipped", "issues": [], "outcomes": [], "state": [], "checks": [],
                "summary": "", "prose_sig": signature(prose), "checker": "disabled"}
    try:
        task = {"待核正文（编号不属于正文）": paragraphs(prose), "核对依据": data,
                "核对目标编号": {i: t for i, t in enumerate(data["核对目标"], 1)}}
        raw = call(instruction("正文核对"), dump(task), 7000, 0.1, model, task="state_commit")
        return validate_review(parse_object(raw), prose, data["核对目标"])
    except Exception as error:
        return {"verdict": "unknown", "issues": [{"detail": str(error)}], "outcomes": [],
                "state": [], "summary": "", "prose_sig": signature(prose), "checker": "model_with_paragraph_evidence"}


def apply_local_revision(prose, obj):
    parts = paragraphs(prose)
    changes = obj.get("patches")
    if not isinstance(changes, list) or not 1 <= len(changes) <= 4:
        raise ValueError("本次问题不能用四段以内的局部修订解决")
    edits = {}
    for item in changes:
        if not isinstance(item, dict):
            raise ValueError("修订格式不正确")
        no, text = item.get("paragraph_id"), item.get("text")
        if type(no) is not int or no not in parts or no in edits or not isinstance(text, str) or not text.strip():
            raise ValueError("修订段落不存在、重复或为空")
        edits[no] = text.strip()
    old_size = sum(len(parts[n]) for n in edits)
    if old_size > max(600, len(prose) * 0.4) or sum(map(len, edits.values())) > max(1000, old_size * 3):
        raise ValueError("修订超过局部范围，原文已保留")
    spans, cursor = [], 0
    for no, text in parts.items():
        start = prose.index(text, cursor)
        if no in edits:
            spans.append((start, start + len(text), edits[no]))
        cursor = start + len(text)
    revised = prose
    for start, end, text in reversed(spans):
        revised = revised[:start] + text + revised[end:]
    return revised, sorted(edits)


def write(settings, ep=1, previous=None, brief="", characters=None, model=None, on_step=None, brief_override=False):
    data = writing_input(settings, ep, previous, brief, characters, brief_override)
    if on_step:
        on_step("写第%d话正文" % ep)
    writer_data = dict(data)
    # P318：完整故事梗概**要给写手**——原来这里 pop 掉了，用户改了整部梗概（怀表背面有蓝鲸标记）写手根本收不到；
    # 「后话内容」那一栏已经把不该写的圈出来，不怕写手抢写后面
    writer_data.pop("分话概览（计划，尚未发生）", None)
    if str(settings.get("story_mode") or "").strip().lower() in ("single", "episode", "manual"):
        tail = ("\n\n现在写第%d话。把本话构想中明确安排的事情完整写完，写出人物行动、对白、原因、过程和结果；"
                "不要自动拆话，也不要为了留下悬念把本话事件截断。" % ep)
    else:
        tail = "\n\n现在只写第%d话，从本话开始状态写到本话结束状态。后续剧情留到后话。" % ep
    request = dump(writer_data) + tail
    write.last_request = request                                        # P318：实际发送内容留一份给验收看
    try:
        template = "逐话正文" if settings.get("story_mode") in ("single", "episode", "manual") else "小说正文"
        prose = call(instruction(template), request, 9000, 0.75, model, task="universal_prose")
    except Exception as error:
        if not getattr(error, "partial_text", ""):
            raise
        prose = error.partial_text
        return {"prose": prose, "review": {"verdict": "unknown", "issues": [{"detail": str(error)}],
                "summary": "", "outcomes": [], "state": [], "prose_sig": signature(prose)}}
    if on_step:
        on_step("核对第%d话事实、衔接和结果" % ep)
    review = review_prose(prose, data, model)
    review = merge_consistency(review, prose, data, previous)     # P179：确定性检查有一票否决权
    result = {"prose": prose, "review": review, "request": request}
    if review["verdict"] == "fail" and str(os.environ.get("V41_NO_REVISION") or "").strip() in ("1", "on", "true"):
        # P283：这轮不做隐藏修订（每一次真实请求都记在 12 次上限里），核对结果照记，交给人看
        result["revision_skipped"] = True
    elif review["verdict"] == "fail":
        # One targeted revision and one independent recheck, never an unbounded rewrite loop.
        try:
            if on_step:
                on_step("仅修第%d话的关键问题（本轮最多一次）" % ep)
            task = {"原文段落": paragraphs(prose), "故事依据": data, "核对结果": review}
            raw = call(instruction("局部修订"), dump(task), 7000, 0.5, model, task="universal_prose")
            revised, changed = apply_local_revision(prose, parse_object(raw))
            result["revision"] = {"original": prose, "original_review": review, "paragraphs": changed}
            result["prose"] = revised
            result["review"] = merge_consistency(review_prose(revised, data, model),
                                                 revised, data, previous)
            result["consistency_fixed"] = [d for d in (review.get("deterministic") or [])
                                           if d not in (result["review"].get("deterministic") or [])]
        except Exception as error:
            result["revision_error"] = str(error)
    return result


class DraftNotReady(RuntimeError):
    def __init__(self, result):
        self.result = result
        why = "；".join(str(x.get("detail") or "") for x in result["review"].get("issues", []))
        super().__init__("正文草稿已保留，核对未通过：" + (why[:220] or "本话目标尚未全部确认完成"))
