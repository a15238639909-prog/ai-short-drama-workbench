# -*- coding: utf-8 -*-
"""已定决策自检（docs/已定决策.md 的代码面）。

用户 2026-09-07 定：我们已经拍板的事不得私自改。这里把能用代码验的决策写成检查项，
工作台启动时跑一遍，不通过的在日志里报「⚠ 违反已定决策」；验收脚本也用它判「对照版有没有」。
只报不改——改决策要用户点头并在 docs/已定决策.md 登记。
"""
import io
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel):
    try:
        return io.open(ROOT / rel, encoding="utf-8").read()
    except Exception:
        return ""


def _check_d1():
    from cores import authoring
    return (not authoring._story_v2()), "D1 故事层默认走老路（_story_v2 关）"


def _check_d2():
    s = _read("cores/authoring.py")
    # 只查真正的不变量：有事实单那一支里不许出现「模型再改一遍」的那几道
    # （原来这条按一行源码原文比对，P151/P154 加了删泄漏句、削外貌句之后就一直误报——
    #  校验器自己错了比产物错更坑，见 2026-09-07 教训）
    i = s.find("P138 减法：有结构表只做确定性清理")
    j = s.find("    else:", i) if i > 0 else -1
    seg = s[i:j] if i > 0 and j > i else ""
    ok = bool(seg) and not any(x in seg for x in (
        "expand_if_short", "condense_if_long", "replace_bad_sentences",
        "story_event_pass", "story_pace_pass"))
    return ok, "D2 有结构表时正文只做确定性清理"


def _check_d3():
    s = _read("cores/authoring.py")
    ok = ('D3：有结构表时保真问题只记录不重写' in s and '输出损坏' in s)
    return ok, "D3 改剧本有结构表只写一次（例外：输出损坏重写，2026-09-07 登记）"


def _check_d4():
    s = _read("presets/instructions/第一话原文_指令词.txt")
    return (0 < len(s) <= 3000 and "硬规矩" in s and "摄影机" not in s), "D4 正文指令词短版（≤3000 字、含硬规矩、无摄影机限制）"


def _check_d5():
    s = _read("presets/instructions/场景图_指令词.txt")
    return ("三层楼" not in s and "进深约四十米" not in s), "D5 场景图指令词无范文"


def _check_d6():
    from cores import authoring
    fake = {"plan_rows": [{"one_line": "甲在乙处做丙", "pace": "日常", "cast": "甲", "place": "乙处", "landing": "甲坐下", "change": "到了乙处"}],
            "plan_people": [{"name": "甲", "role": "主角", "relation": "主角"}]}
    b = authoring.row_boundary_block(fake, 1)
    return ("人物表" in b and "边界" in b and "甲坐下" in b), "D6 正文提示带事实单（人物表+边界）"


def _check_d8():
    s = _read("web/pages/story.js")
    return ('id="sgProse"' in s and 'id="sgProse" style="display:none"' not in s), "D8 剧本页有可见的本话正文栏"


def _check_d10():
    a = _read("cores/authoring.py")
    g = _read("api/saga_api.py")
    return ("def ensure_cards_for_cast" in a and "ensure_cards_for_cast(sid, _s2" in g), "D10 人设卡随结构表建，接在一键生成的框架之后"


def _check_d12():
    a = _read("cores/asset_core.py")
    # P218：只查人名和称呼是不够的。「中央是三人围坐的圆桌」两样都不是，
    # 一路漏到图里真坐了三个人，D12 当时还是满分——体检分数只说明"我想到的没犯"。
    ok = ("_PERSON_RE" in a and "def strip_person_clauses" in a
          and "def strip_headcount" in a
          and a.count("strip_headcount(") >= 4          # 定义 + 三个入口
          and "strip_headcount(prompt)" in a            # scene_prompt_repair
          and a.find("scene_prompt_repair(prompt, _cast)", a.find("scene_prompt_fix_material(prompt, P)")) > 0)
    return ok, "D12 底图一个人都不许有：人物正则+人数词+设计稿删人+过滤放在所有注入之后"


def _check_d17():
    from cores.age_policy import assert_allowed, AgePolicyError
    from cores.authoring import sanitize_card_fields
    card = {"name": "测试人物", "age": "12", "clothing": "普通运动服"}
    original = dict(card)
    assert_allowed(cards=(card,), settings={})
    stopped = False
    try:
        assert_allowed(cards=(card,), settings={"content_tendencies": ["成人向"]})
    except AgePolicyError:
        stopped = True
    return (stopped and card == original and sanitize_card_fields(card) == original,
            "D17 保留真实年龄和外观；共享规则停止未成年敏感内容")


def _check_d13():
    a = _read("cores/authoring.py")
    return ("def ensure_scene_head_fields" in a
            and "ensure_scene_head_fields(best" in a), "D13 剧本场景头四行缺了就按确定信息补"


def _check_d14():
    """行为检查：用合成数据直接问 episode_source，不读项目、不调模型（P166）。"""
    from cores import authoring as au
    st = {"plan_version": 2, "plan_rows": [{"one_line": "死士带密信出城"}]}
    same = au.episode_source("_", 1, st, {"no": 1, "plan_version": 2, "brief": "死士带密信出城"})[0]
    old_ = au.episode_source("_", 1, st, {"no": 1, "plan_version": 1, "brief": "死士带密信出城"})[0]
    guess = au.episode_source("_", 1, st, {"no": 1, "brief": "天台上的红裙女人"})[0]
    byrow = au.episode_source("_", 1, st, {"no": 1, "brief": "死士带密信出城"})[0]
    return (same == "current" and old_ == "old" and guess == "unknown" and byrow == "current"),         "D14 稿子的来源按版本号和内容判（对不上就是 old/unknown，不猜成当前版本）"


def _check_d15():
    """启动自检只查不变量落在代码里；真实行为由 tests/test_上下游对齐.py 跑六个场景验。"""
    a = _read("cores/authoring.py")
    g = _read("api/saga_api.py")
    return ("def screenplay_stale" in a and "def refresh_upstream" in a
            and 'setdefault("versions", []).append({"at": time.time(), "body": _old})' in a
            and "结构没通过，这一轮到此为止" in g), "D15 上下游过期只标不删，结构没通过不启动下游"


def _check_d16():
    """行为检查：默认配置下模型自查是关的，确定性检查只跑那三条（P188）。"""
    import os as _os
    from cores import universal_writer as _uw, consistency as _cc
    _keep = _os.environ.pop("V41_MODEL_REVIEW", None)
    try:
        off = not _uw.model_review_on()
    finally:
        if _keep is not None:
            _os.environ["V41_MODEL_REVIEW"] = _keep
    # 两个路程距离只提示不拦；时间矛盾照拦
    quiet = not _cc.prose_problems("这条小路三公里，绕过去那条只有一公里。", canon="")
    loud = bool(_cc.prose_problems("每晚零点零零分收到信号。", canon="每晚三点整都有一辆白车过站。"))
    return (off and quiet and loud), "D16 文字层按草稿交付：模型自查默认关，确定性检查只留能确定的几条"


CHECKS = [_check_d1, _check_d2, _check_d3, _check_d4, _check_d5, _check_d6, _check_d8, _check_d10,
          _check_d12, _check_d13, _check_d14, _check_d15, _check_d16]


def run(log=None):
    """跑全部检查。返回 [(ok, 说明)]；log 给一个 callable 就把不通过的报出去。"""
    out = []
    for fn in CHECKS:
        try:
            ok, msg = fn()
        except Exception as ex:
            ok, msg = False, "%s（检查本身出错：%s）" % (fn.__name__, str(ex)[:80])
        out.append((bool(ok), msg))
        if not ok and log:
            log("⚠ 违反已定决策：" + msg)
    return out


def acceptance_complete(round_dir):
    """D7：一轮验收目录里必须有对照版（我先写的那一版）才算完成。"""
    try:
        names = os.listdir(str(round_dir))
    except Exception:
        return False
    return any(n.startswith("对照_") and n.endswith(".md") for n in names)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(ROOT))
    sys.stdout.reconfigure(encoding="utf-8")
    bad = 0
    for ok, msg in run():
        print(("✅ " if ok else "❌ ") + msg)
        bad += 0 if ok else 1
    sys.exit(1 if bad else 0)
