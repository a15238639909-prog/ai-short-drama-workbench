# -*- coding: utf-8 -*-
"""character_setup.py — 从 8848 app.py 搬来的人物卡→三视图提示词与「生成全部设定」。

唯一职责：拼装人物卡/整部作品设定 → 画图/生成所需的文字与模型调用包装；
AI 调用一律走 cores/premise_guard.expand_with_guard（偏题自动重来一次，两次不行才交人，不抛异常）。
"""
from . import premise_guard, story_core

GEN_ENABLED = True  # 一句话生成已接 Qwen，解除门控


def _profile_text(p):
    if not p:
        return ""
    if isinstance(p, str):
        return p
    if p.get("look"):
        return str(p["look"])
    return "；".join(str(p.get(k) or "") for k in ("designDirection", "world", "characters") if p.get(k))


def build_charsheet_messages(chars_text, art, world_visual_profile=None, premise=""):
    """搬运自 8848 app.py:1736 gen_charsheet 的提示词组装（不调用模型，原文照抄）。"""
    world_text = _profile_text(world_visual_profile)
    system = (
        "你是漫画角色设定师。把用户给的小说人物，转成【画图专用的角色外观卡】。"
        "人物外观必须从给定的题材、时代、文明、技术、材料、服装体系、阶层和职业推导，"
        "不能脱离世界观套用现代战术人员、古代侠客或其他常见模板。"
        "第一行人物按主角处理：用户没有明确要求普通、丑陋、病态或反传统外形时，"
        "自动采用电影主演级协调五官、修长匀称比例、挺拔体态和鲜明主角气质；"
        "服装在世界工艺与人物身份允许范围内采用华丽繁复的主角级设计：至少内中外三层轮廓，"
        "明确高级材质、领肩袖腰结构、精细滚边或刺绣、稳定主辅色和一处身份纹样。"
        "配角也采用精致好看的完整服装，但轮廓和装饰层级低于主角。"
        "用户明确要求朴素、贫穷、破旧或制服时才降低装饰程度。"
        "少年魔法师应清俊灵动并有法术学徒或施法者的服装语言，不能写成现代便装路人；"
        "战士、侦探、学者等则按身份自动换成相应的主角美术方向。"
        "每个主要角色独占一行，格式：名字—外观。外观只写画图需要、镜头能看见的固定特征，采用【"
        + (art or "写实真人") + "】画风的描述，依次包含：性别与年龄段、脸型与一个关键五官、发型发色、"
        "正常可信的肤色体型、完整上装、完整下装、鞋靴、必要外层服装、最多一处固定疤痕或配饰。"
        "每人只保留3—5个能在远景或剪影里一眼认出的视觉锚点；"
        "不同角色的轮廓、主色、体态和标志物要有明显区分。若原文有专属武器或道具，写清其形状、尺度、材质、颜色和固定携带位置。"
        "用户明确空手或不携带武器时，整行省略武器与道具字段，不写任何武器词。"
        "不要写性格、目标、剧情、心理、当前伤势、血污、破损衣服、临时变身、当前手持物或某章换装；"
        "每人一行，不要编号、不要多余的话。"
    )
    user = (
        "【题材与世界视觉基准】\n" + (world_text or "按人物原始设定采用统一、可信的时代与服装体系") +
        "\n\n【用户原句事实】\n" + str(premise or "").strip()[:800] +
        "\n\n【故事人物档案】\n" + str(chars_text or "").strip()[:3000]
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def generate_all_settings(story_id, model=None):
    """「✨ 按以上选择生成全部设定」后端。

    AI 未启用（本轮）→ 明确返回门控；启用后 → 走 premise_guard.expand_with_guard
    （偏题自动重来一次，两次都不行才把结论交给人，不抛异常）。
    """
    story = story_core.get_story(story_id)
    if not story:
        raise ValueError("故事不存在")
    if not GEN_ENABLED:
        return {"gated": True, "changed": False, "message": "生成轮启用后可用"}

    def call(extra):
        if model is None:
            raise RuntimeError("生成模型未接入")
        return str(model(extra) or "")

    # 一句话故事是事实源；“不可更改的故事要求”只是附加约束，不能顶替原始故事。
    premise = story.get("one_line") or (story.get("project_settings") or {}).get("one_line") or ""
    return {"gated": False, "changed": True,
            **premise_guard.expand_with_guard(premise, call)}
