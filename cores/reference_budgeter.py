# -*- coding: utf-8 -*-
"""参考图预算：唯一决定每段用哪几张参考图的地方。

**依据：2026-08-19 本机实测，用户人眼判定（优先级高于任何模型判断）**

同一卧室、同一剧情、同一提示词、固定种子，跑 15 秒 + 15 秒两段做 A/B：

| 组 | 参考图 | 人眼结果 |
|---|---|---|
| A | 3 张：角色 A 身份 + 角色 B 身份 + 场景 Master | **第 15 秒接口处光线明显变化**，看得出是两段拼的 |
| B | 3 张 + 上一段结束帧 | **看不出拼接痕迹** |

结论：真正无缝续接的后一段**必须**带 previous_end_frame。纯文字写「同一盏台灯、
同一光线方向」锁不住光线，只有结束帧能锁住。

这条结论**推翻**了 V4.2.3 视觉门的旧记录（当时判定首帧模式「未优于普通参考」）——
那是本地视觉模型给的判断，不可靠，已作废。

同时**废弃** V4.2.2 的「按复杂度动态给 3/4/5/6/7/8/9 张」逻辑，以及它产出的
`character_current_look`、`secondary_scene_view`、`special_state` 三种角色：
参考图越多越乱，是需求总纲第 70 条明确列为废弃的思路。现在只有一条规则。
"""

__all__ = ["decide", "ROLE_ORDER", "BASE_ROLES"]

# 只剩三种合法职责，其余一律不允许出现在参考包里
ROLE_ORDER = ("character_identity", "scene_master", "previous_end_frame")
BASE_ROLES = ("character_identity", "scene_master")


def decide(ctx):
    """决定这一段用哪几张参考图。

    ctx:
      participant_count  本段出场角色数（每人一张身份图）
      continuous         本段是否是上一段的**无缝续接**（同场景、时间直接承接）
      previous_end_frame 上一段是否真的产出了可用的结束帧

    返回 {count, roles, reasons, rule}；roles 长度等于 count。
    """
    n = max(1, int(ctx.get("participant_count") or 1))
    roles = ["character_identity"] * n + ["scene_master"]
    reasons = ["每个出场角色 1 张身份图（%d 张）+ 当前场景 1 张" % n]

    if ctx.get("continuous") and ctx.get("previous_end_frame"):
        roles.append("previous_end_frame")
        reasons.append("无缝续接段 -> +1 张上一段结束帧（实测：不加会在接口处跳光）")
    elif ctx.get("continuous"):
        reasons.append("标为连续但上一段没有可用结束帧 -> 不加，先把结束帧补出来")
    else:
        reasons.append("新场景/新时间，不是无缝续接 -> 不加结束帧")

    return {"count": len(roles), "roles": roles, "reasons": reasons,
            "rule": "%d 张：每人身份图 + 场景，%s" %
                    (len(roles), "含结束帧" if "previous_end_frame" in roles else "不含结束帧")}
