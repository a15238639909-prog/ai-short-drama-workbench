# -*- coding: utf-8 -*-
"""节奏档：日常 / 推进 / 高潮。故事层和导演层的守卫按档位开关。

设计见 docs/故事结构设计_v2.md 第三节。要点：
· 「影片类型」不再决定每拍发生什么；每一话按内容判一个节奏档，守卫按档位开关。
· 日常档**放开**：对手必填、冲突计数、冷读「谁是敌人」。
· 日常档**不放开**（2026-08-28 教训，防"整话零阻力"）：每拍可见动作、每话≥1 变化、结尾钩子、台词数。
· 全是可数指标，判断题给代码。
"""
import re

PACES = ("日常", "推进", "高潮")

# 判档关键词（一句话 / 事件清单里出现）
_HIGH = re.compile(r"追[杀捕击逐]|逃[亡跑命]|杀|营救|救出|突袭|交锋|决战|巢穴|围攻|反击|血战|厮杀|冲进|炸|爆")
_MID = re.compile(r"商量|决定|准备|补给|打听|交易|谈判|讨价|分歧|争执|查|调查|寻访|找到|发现|得知|真相|来龙去脉|拒绝|不肯|拦|报警|求救|在哭|哭声|没人应|不应|坏了|失踪|不见了")
_LOW = re.compile(r"旅行|赶路|沿路|聊天|闲聊|日常|歇脚|住宿|客栈|风景|进村|到了.{0,3}村|吃饭|散步|玩|逛|做饭|洗衣|睡|谈论|闲谈|种菜|做饭|偷吃|喂|钓鱼|采药|扫地|晒|玩耍|嬉闹|逗|哄|洗澡|梳头|后山|院子|灶")
# 冲突只认**主角做的动作**（动词）。「怪物」「敌」「刀」是名词——听到别人谈论怪物 ≠ 打怪物
# （2026-09-05 实测：115 第一话因为一句话里有"怪物"两个字被判成推进，还硬塞了对手）。
_CONFLICT = re.compile(r"打[了起斗架]|厮杀|追[杀捕击]|逃[亡跑命]|抢[走夺劫]|夺[过走]|砸[向碎]|踹|刺[向出]|劈[向下]|撞[向上]|扑[向上]|开火|中弹|拔[刀剑]|出[刀剑]")

# 「对峙而不打」：面对负心人/凡人，法力全开却不能动手
_NO_STRIKE = re.compile(r"不能动手|不许动手|忍住|忍着|凡人|不能伤|按下怒火|一根手指都不能")


def detect(text):
    """从这一话的一句话（或事件清单）判节奏档。判不出默认推进。"""
    t = str(text or "")
    if _HIGH.search(t):
        return "高潮"
    low = len(_LOW.findall(t))
    mid = len(_MID.findall(t))
    # 日常标志多、又没有主角出手的动词 → 日常（哪怕提到了怪物/敌人这些名词）
    if low >= 2 and not _CONFLICT.search(t) and low >= mid:
        return "日常"
    if mid or _CONFLICT.search(t):
        return "推进"
    if low:
        return "日常"
    return "推进"


def no_strike(text):
    return bool(_NO_STRIKE.search(str(text or "")))


_RULES = {
    "日常": dict(
        need_foe=False, conflict_min=0, change_min=1,
        dialogue_min=14, dialogue_max=18, micro_per_kind=3,
        seg_seconds="10-14", skip_enemy_question=True,
        shot_bias="全景占一半，人在环境里是小小的；双人中景聊天为主；跟拍要慢；风景和天气是角色，不是背景板。",
        fill_hint="闲聊、风景、歇脚、路上小事（递水、修鞍、问路、逗对方）",
        act_extra=r"骑|走|赶路|递|指|看向|望|笑|喝|吃|坐下|下马|牵马|敲门|推门|放下|拿起|回头|停下|张望",
    ),
    "推进": dict(
        need_foe=False, conflict_min=1, change_min=1,
        dialogue_min=12, dialogue_max=16, micro_per_kind=2,
        seg_seconds="8-12", skip_enemy_question=False,
        shot_bias="中景为主，一次运镜带动机；对谈用双人中景和过肩；每段一个能看清地形的全景。",
        fill_hint="一处阻力、一次小对立（价钱、方向、规矩、误会）、打听到一个新事实",
        act_extra=r"递|指|看向|按住|拦|摊开|推过去|接过|转身|起身",
    ),
    "高潮": dict(
        need_foe=True, conflict_min=2, change_min=1,
        dialogue_min=8, dialogue_max=14, micro_per_kind=2,
        seg_seconds="6-10", skip_enemy_question=False,
        shot_bias="短镜头、低角度、纵深；每段一个远景把敌我和中间的距离框进来。",
        fill_hint="一次交锋、一次反击、一次代价",
        act_extra="",
    ),
}


def rules(pace):
    return dict(_RULES.get(pace if pace in PACES else "推进"))


def legacy_genre_default(genre):
    """老项目已选的影片类型 → 默认节奏（兼容）。"""
    g = str(genre or "")
    if "公路" in g:
        return "日常"
    if "冒险" in g or "犯罪" in g:
        return "高潮"
    if "爽文" in g:
        return "推进"
    return ""


def resolve(settings, one_line=""):
    """这一话的节奏档：设置里手选的 > 从一句话判的 > 老类型映射 > 推进。"""
    s = settings or {}
    p = str(s.get("pace") or "").strip()
    if p in PACES:
        return p
    if str(one_line or "").strip():
        return detect(one_line)
    return legacy_genre_default(s.get("genre")) or "推进"
