# -*- coding: utf-8 -*-
"""人物设计系统 v1（独立核心，仅供新版测试版使用）。

职责（单一来源）：
1. 人物原型库 v1：原型标签 -> 具体身体/脸部/神态结构展开。
2. 视觉强度：只放大当前原型方向，不重新设计人物。
3. Character Design Contract：把展开结果 + 用户明确要求收口成一份契约。
4. 统一 16:9 Character Master Sheet 提示词编译。
5. 纯表现画风注入 + 最终人物核心结构锚定。

漫画与高质量画册的人物设定图必须调用同一个编译函数：
  compile_master_sheet_prompt()
（comic / album 两个入口名指向同一函数对象。）

设计边界：
- 原型名称（如“病弱纤骨型”）只用于 UI 和内部规划，
  最终发给 Krea 的提示词只出现展开后的具体结构。
- 提示词顺序固定：版式 -> 身体 -> 脸部 -> 神态 -> 发型 -> 服装 -> 识别点
  -> 纯表现画风 -> 人物核心结构重新锚定。
- 不使用 Markdown 加粗星号；不使用大量“不要/禁止”类抽象提示。
- 本模块不调用模型、不调用 Krea、不接入视觉审美验收。
"""
from __future__ import annotations

import re

# ============================================================
# 一、人物原型库 v1
# ============================================================
CHARACTER_ARCHETYPES = {
    # ---------------- 女性 ----------------
    "长腿神族型": {
        "sex": "女",
        "height": "约196cm，8.8头身",
        "body": (
            "头极小，颈部修长，躯干偏短，髋点很高，双腿异常修长笔直，"
            "肩窄，胸腔窄，腰细，骨架整体纤长轻盈，整体垂直感极强"
        ),
        "face": (
            "窄长神性脸，额头高，面中略长，下颌收窄但不尖，"
            "眼裂细长，外眼角微微上挑，鼻梁细直，嘴唇薄，神情高冷克制"
        ),
        "posture": "站姿垂直挺拔，动作幅度小，姿态高冷克制",
        "amplify": "腿长与纵向比例、头身比、整体纤长轻盈感",
        "anchor": (
            "人物最终结构锚点：极小头、长颈、短躯干与异常修长笔直的双腿，"
            "窄肩窄胸细腰，整体极高、极直、极长腿；窄长神性脸，细长微挑眼，"
            "薄唇；银白长直发与垂坠长袍强化纵向轮廓，保持神性与高冷。"
        ),
        "default_hairstyle": "银白色长直发，长度到大腿中段",
        "default_clothing": "神族礼装：贴身抹胸与高开衩修身内裙，外层冰白到淡金渐变无袖长外袍，前部深开露出双腿，淡金织带，浅银薄底软靴",
    },
    "极娇小成年女性型": {
        "sex": "女",
        "height": "约150cm，6.9–7.1头身",
        "body": (
            "非常小型的成年女性骨架，窄肩，小胸廓，小骨盆，纤细四肢，"
            "手和脚明显偏小，身体已发育完成，胸腰具有成年女性的自然体积与曲线轮廓"
        ),
        "face": (
            "小型成年鹅蛋脸，面中长度正常，眼睛为中等大小的细杏仁眼，"
            "鼻梁与鼻尖结构明确，嘴唇具有成年女性轮廓，是成年女性的脸而不是儿童的脸"
        ),
        "posture": "轻巧利落，动作干脆，整体气质成熟有自我意识",
        "amplify": "整体体积感进一步缩小、四肢与手脚更纤细、头身相对更突出",
        "anchor": (
            "人物最终结构锚点：约150cm、6.9–7.1头身，非常小巧的成年女性骨架，"
            "窄肩小胸廓小骨盆，纤细四肢与小手脚，身体已发育完成；"
            "小型成年鹅蛋脸，中等细杏仁眼，成年面中与鼻唇结构；"
            "成熟利落的小体型服装衬托小身体，保持娇小、轻巧、成年女性感。"
        ),
        "default_hairstyle": "浅金色中长发，低位半扎",
        "default_clothing": "成熟利落的小体型旅行法师装：合身短上衣、短斗篷、细腰带、短裙与轻便短靴，剪裁成人化",
    },
    "病弱纤骨型": {
        "sex": "女",
        "height": "约166cm，7.7头身",
        "body": (
            "骨架很窄，窄肩，胸腔小而薄，腰细但不强调曲线，低肌肉量，"
            "低脂肪体积，上臂、小腿、手腕与脚踝明显纤细，"
            "锁骨和肩峰、肩胛骨等骨点清楚，整体视觉体积低，"
            "给人长期营养不足、轻而薄的感觉"
        ),
        "face": (
            "窄小脸，脸长略大于脸宽，面中略长，低眼睑，眼下轻微阴影，"
            "鼻梁细直，薄唇，低血色苍白肤色"
        ),
        "posture": "肩部自然略收，动作幅度低，站姿缺少力量感，带易疲惫感",
        "amplify": "胸廓更薄、四肢更细、骨点更明显、整体更轻更弱",
        "anchor": (
            "人物最终结构锚点：窄小骨架，窄肩，小而扁薄的胸廓，极低肌肉和脂肪体积，"
            "极细上臂、小腿、手腕与脚踝，明显锁骨，长而薄的四肢；"
            "窄小病弱脸，低眼睑，轻微眼下阴影，苍白低血色皮肤，薄唇；"
            "轻薄垂坠服装顺着身体下落，整个角色保持薄、弱、轻、苍白的身体轮廓。"
        ),
        "default_hairstyle": "黑色长直发，发量不厚，贴头皮，长度到腰",
        "default_clothing": "灰白色高腰长裙，布料薄而轻，腰部不强调曲线，外搭极薄长袖外层，脖颈与锁骨区域保持清楚，浅灰薄底鞋",
    },
    "妖姬沙漏型": {
        "sex": "女",
        "height": "约168cm，7.6头身",
        "body": (
            "胸廓与骨盆明显扩张，腰部突然极度收窄，形成强烈沙漏曲线，"
            "胸部饱满，臀部饱满，大腿上段有体积感，小腿细长，肩略窄"
        ),
        "face": (
            "极尖V型妖异脸，颧骨线条清楚但不宽，下半脸迅速收尖，"
            "眼睛细长上挑，眉眼距离略近，鼻梁高挺纤细，唇形饱满，"
            "嘴角天然带若有若无的笑意"
        ),
        "posture": "身体略前倾，肩膀微侧，动作柔中带主动，姿态妩媚",
        "amplify": "腰臀胸反差、曲线强度、妖异气质",
        "anchor": (
            "人物最终结构锚点：胸廓与骨盆扩张、腰部突然极度收窄的强烈沙漏曲线，"
            "胸臀饱满、大腿上段有体积、小腿细长；极尖V型妖异脸，细长上挑眼，"
            "饱满唇形；轻薄披挂与高开衩裙强化曲线轮廓，保持妖异与沙漏反差。"
        ),
        "default_hairstyle": "黑色长卷发，长度过腰，发尾轻微散开",
        "default_clothing": "黑红妖姬装：贴身低V连体衣，半透明黑纱大袖披挂只从肩部垂下，高开衩黑裙与暗红内衬，及膝高跟长靴",
    },
    # ---------------- 男性 ----------------
    "墙壁霸主型": {
        "sex": "男",
        "height": "约218cm，7.4头身",
        "body": (
            "头偏小，脖子短粗，肩极宽，胸腔像一整块巨大的横向结构，"
            "背部宽厚，腰没有明显纤细感，骨盆稳重，双腿粗壮结实，"
            "整体像一堵沉重的人形高墙"
        ),
        "face": (
            "方下颌战士脸，额头宽，眉骨明显，眼裂不大但压迫感强，"
            "鼻梁宽直，鼻翼厚，嘴唇薄，方下巴很重"
        ),
        "posture": "站姿稳重沉压，重心稳定，压迫感强",
        "amplify": "肩宽、胸廓厚度、骨架重量、横向体量",
        "anchor": (
            "人物最终结构锚点：头偏小、颈短粗、肩极宽、胸腔像巨大横向结构，"
            "背部宽厚、腰不纤细、双腿粗壮，像一堵人形高墙；"
            "方下颌战士脸，宽直鼻梁与重方下巴；"
            "厚重甲胄与横向肩甲强化压迫感，保持重、厚、城墙式轮廓。"
        ),
        "default_hairstyle": "黑色短发，向后梳理",
        "default_clothing": "黑红重甲霸主装：内层黑色紧身皮甲，外层厚重黑铁板甲，肩甲横向大幅外扩，腹胯多层重甲裙甲，暗红兽毛披挂不遮肩宽轮廓",
    },
    "猛兽拳手型": {
        "sex": "男",
        "height": "约195cm，7.1头身",
        "body": (
            "肩宽大但不如墙壁型，手臂异常长，前臂和手掌很大，"
            "胸腹厚实，腰相对收紧，骨盆稳定，大腿粗壮，小腿紧凑，"
            "整体重心明显向前，像随时准备扑击"
        ),
        "face": (
            "粗犷宽脸，眉骨突出，颧骨宽，鼻梁宽厚，嘴角偏平，"
            "下颌有力量感，可带旧疤"
        ),
        "posture": "重心略前压，站姿带扑击感，四肢蓄力",
        "amplify": "手臂长度、前臂与手掌体积、胸腹厚度、前压感",
        "anchor": (
            "人物最终结构锚点：肩宽大、手臂异常长、前臂与手掌巨大，"
            "胸腹厚实、腰收紧、大腿粗壮，重心明显向前；"
            "粗犷宽脸，眉骨突出、颧骨宽、下颌有力量感；"
            "无袖粗布上衣与宽腰带，露出长臂与前臂，保持猛兽拳手的前压野性。"
        ),
        "default_hairstyle": "黑色短硬发",
        "default_clothing": "深灰色贴身无袖上衣，宽厚深褐皮腰封，黑色紧身短裤，膝下绑带缠绕，手臂简单护腕，赤脚或极简足部护具",
    },
    "病弱贵族青年型": {
        "sex": "男",
        "height": "约181cm，7.8–8头身",
        "body": (
            "窄肩且肩部轻微内收，胸廓前后厚度低，低肌肉量，"
            "锁骨、腕骨、踝骨明显，四肢与手指细长，颈部略前倾，"
            "胸骨不前挺，膝盖自然微松，站姿带长期体力不足的松弛感"
        ),
        "face": (
            "窄长苍白脸，脸颊轻微内陷，眼睑低垂，眼下有青灰色阴影，"
            "鼻梁细，嘴唇为灰粉色淡唇，低血色冷白皮肤"
        ),
        "posture": "含胸微驼，肩背收着，动作轻缓，整体易碎疲惫",
        "amplify": "胸廓更薄、四肢更细、骨点更明显、病态苍白感",
        "anchor": (
            "人物最终结构锚点：窄肩肩微内收、胸廓前后厚度低、低肌肉量，"
            "明显锁骨腕骨踝骨、细长四肢与手指，颈部略前倾、膝部微松；"
            "脸颊轻微内陷、低眼睑、眼下青灰、低血色冷白皮肤、灰粉色淡唇；"
            "轻薄垂坠落肩的贵族服装，保持美、弱、薄、苍白、易碎。"
        ),
        "default_hairstyle": "细软略凌乱的银灰色中长发",
        "default_clothing": "贵族式病弱常服：月白与浅银灰高领内衫，长款修身开襟上衣，衣料轻薄柔软略带垮垂感，修身长裤与高筒靴",
    },
    "极瘦妖异青年型": {
        "sex": "男",
        "height": "约188cm，8.5头身",
        "body": (
            "头很小，脖子长，肩极窄，胸腔很薄，躯干像一张细长薄板，"
            "腰细，手臂和腿都极长极细，手指异常修长，关节骨感清楚，"
            "整体像病态、妖异、接近非人的纤长生物"
        ),
        "face": (
            "极窄长妖异脸，额头略高，面中偏长，下颌快速收窄，"
            "眼裂细长上挑，瞳孔感强，鼻梁极细，嘴唇薄，冷而异样的美感"
        ),
        "posture": "站立松弛笔直，动作轻缓，整体带非人感的冷寂",
        "amplify": "头身比、颈长、肩窄度、四肢纤细度、妖异感",
        "anchor": (
            "人物最终结构锚点：小头、长颈、极窄肩、极薄胸腔、极细腰，"
            "躯干像纵向细长薄板，手臂和腿异常修长纤细，手指很长，关节骨感清楚；"
            "脸极窄长，长眼、细鼻、薄唇；黑色窄长服装紧贴纵向轮廓，"
            "整个角色保持长、细、薄、妖异、接近非人的身体形态。"
        ),
        "default_hairstyle": "墨黑色长发，长度过肩，发丝直且薄",
        "default_clothing": "妖异神官式长装：黑色与深灰细长内袍，外层窄而长的开衩长衣，垂直感极强，肩部不做宽，腰部极细，袖子细长，黑色窄长靴",
    },
    # ---------------- 非人 ----------------
    "四足重甲巨兽型": {
        "sex": "非人",
        "height": "体长约6.5米",
        "body": (
            "四足着地，前肢比后肢粗壮，背部高耸，尾巴极长且末端膨大如重锤，"
            "覆盖厚重角质鳞甲，背脊从头后到尾根有一整排高棘刺，"
            "腹侧装甲较少并暴露暗红肌肉纹理，四肢关节方向清楚，爪子巨大"
        ),
        "face": (
            "厚重骨质头冠，两侧有向上弯曲的巨角，"
            "正面七只大小不一的复眼，中央横向巨口布满交错尖牙"
        ),
        "posture": "四肢着地匍匐前行，步伐沉重缓慢，尾部拖地",
        "amplify": "体量、甲壳厚度、角与棘刺尺度、压迫感",
        "anchor": (
            "人物最终结构锚点：四足着地、前肢粗于后肢、背部高耸、"
            "尾端重锤，黑褐色厚重角质鳞甲与整排背脊棘刺；"
            "骨质头冠、内弯巨角、七只复眼与横向巨口；"
            "骨板、锈铁护脊与锁链强化装甲堡垒感，保持沉重、古老、会移动的堡垒轮廓。"
        ),
        "default_hairstyle": "",
        "default_clothing": "深渊重装：肩部不规则骨板，背脊锈铁护脊，腰部粗大锁链，四肢关节旧铁护具与皮革束带",
    },
    "四臂机械战斗体": {
        "sex": "非人",
        "height": "约2.8米，肩宽1.2米",
        "body": (
            "直立人形机械结构，厚重上躯干，四条手臂：上方一对主力重装手臂，"
            "下方一对稍细的辅助机械臂，四只手都是五指机械爪，"
            "双腿为稳重人形机械腿，脚部为三趾金属爪足，"
            "尾巴为一条完整的长分节机械尾"
        ),
        "face": (
            "骨白色机械面甲头，额头有短角状结构，双眼是冷白发光传感器，"
            "两侧各有一个小型传感器模块"
        ),
        "posture": "警觉站立，四条手臂自然下垂但指爪微张，随时准备抓握",
        "amplify": "四臂体积、肩甲外扩、机械结构密度、机械尾长度",
        "anchor": (
            "人物最终结构锚点：直立人形机械体，厚重上躯干，四条手臂（上重下细）"
            "与五指机械爪，重型人形机械腿与三趾金属爪足，完整长分节机械尾；"
            "骨白机械面甲、冷白发光双眼与短角；灰蓝外骨骼装甲与黑色机械肌束，"
            "严格保持四臂、无翅、无多余背翼。"
        ),
        "default_hairstyle": "",
        "default_clothing": "灰蓝色外骨骼装甲，内层黑色柔性机械肌束，胸甲中央红色能量导槽，腰部实用挂带与工具袋",
    },
}

# 旧标签 -> 新原型 的轻量映射（兼容旧项目，不修改原文件）
LEGACY_BODY_TO_ARCHETYPE = {
    "极娇小纤弱": "极娇小成年女性型",
    "娇小匀称": "极娇小成年女性型",
    "少女纤薄": "病弱纤骨型",
    "极瘦高挑": "长腿神族型",
    "美型高挑": "长腿神族型",
    "超高挑夸张": "长腿神族型",
    "极致曲线": "妖姬沙漏型",
    "丰满曲线": "妖姬沙漏型",
    "娇小少年": "极娇小成年女性型",
    "极瘦纤长": "极瘦妖异青年型",
    "美型少年": "长腿神族型",
    "修长青年": "病弱贵族青年型",
    "宽肩窄腰": "墙壁霸主型",
    "强壮肌肉": "墙壁霸主型",
    "重型壮汉": "墙壁霸主型",
    "超高大夸张": "墙壁霸主型",
    "细长异形": "极瘦妖异青年型",
    "巨型怪物": "四足重甲巨兽型",
    "四臂多肢": "四臂机械战斗体",
}

# ============================================================
# 二、视觉强度：只放大当前原型方向
# ============================================================
VISUAL_STRENGTH_GUIDES = {
    "自然": (
        "视觉强度：自然。原型特征保持在真实可量化范围内，不额外放大，"
        "接近现实人体比例，五官与骨架不夸张。"
    ),
    "美型": (
        "视觉强度：美型。在真实基础上美化五官与比例，"
        "原型方向保持不变，不改变身体结构与特征类型。"
    ),
    "强设计": (
        "视觉强度：强设计。显著放大原型特征：%s；允许动漫化比例，"
        "但必须保持结构完整与原型方向。"
    ),
    "极端": (
        "视觉强度：极端。把原型特征放大到接近非现实极限：%s；"
        "保持结构完整、四肢数量正确，不改变原型方向。"
    ),
}

# ============================================================
# 三、纯表现画风（safe style / pure rendering）
# 只描述媒介、线条、着色、材质、光影、颗粒与色彩响应，
# 不描述人体比例、脸型、服装结构、场景内容。
# ============================================================
FACE_PROFILES = {
    "女": {
        "自动": "",
        "甜美": "短而柔和的脸，面中偏短，眼睛较大偏圆，鼻子小嘴小，五官紧凑，整体亲和可爱",
        "清冷": "窄小脸，面中略长，眉眼间距开阔，眼裂细长，眼睑略低，鼻梁细直，嘴唇薄，肤色偏白",
        "明艳": "眉眼存在感强，眼窝略深，鼻部立体，嘴唇较饱满，五官对比强",
        "英气": "眉骨和下颌清楚，直眉，眼型锐利，鼻梁挺，整体利落",
        "成熟": "面部骨骼成熟，颧骨鼻梁更立体，眼睛大小克制，唇形明确",
        "妖异": "极窄脸，长眼明显上挑，极小鼻翼，非普通人比例的美感",
    },
    "男": {
        "自动": "",
        "美少年": "脸型偏窄，比例均衡，下颌柔和收窄，眉眼精致有神，鼻梁细直，五官耐看",
        "俊美": "窄长脸，眉眼有神，鼻梁高挺，唇形清楚，五官比例精致",
        "硬朗": "眉骨和下颌明显，剑眉，眼型锐利有神，鼻梁挺，面部棱角分明",
        "粗犷": "脸型偏宽，下颌宽大，眉骨粗，鼻梁宽直，面部线条粗",
        "阴柔": "脸窄长，下颌细，长眼略下垂，鼻梁细，气质偏柔",
        "妖异": "极窄长脸，眼裂细长上挑，瞳孔感强，鼻梁极细，带非人美感",
    },
    "非人": {
        "自动": "",
        "人形异族": "接近人形但五官比例异于人类，耳尖、瞳色或眉骨有异族特征",
        "兽性": "兽类头骨结构，吻部、獠牙或毛皮特征明确",
        "骸骨": "骷髅或亡灵头部结构，眼窝空洞，骨面纹理清楚",
        "机械": "机械面甲或金属头部结构，发光传感器与关节结构",
        "异形": "多目、口器、触角等明确的非人结构",
    },
}

BODY_PROFILES = {
    "女": {
        "自动": "",
        "娇小": "约150cm级，6.9–7.1头身，小型成年骨架，窄肩，小胸廓，小骨盆，小手小脚，身体已发育完成",
        "纤细": "约162–170cm，7.4–7.8头身，骨架偏窄，肩窄，四肢修长纤细，肌肉脂肪量低",
        "极瘦": "窄骨架，小而薄胸廓，低肌肉和脂肪体积，上臂和小腿明显细，腕踝细，锁骨较明显",
        "高挑": "约172–184cm，7.8–8.5头身，头较小，颈长，肩窄腰细，腿明显偏长",
        "匀称": "约160–170cm，7–7.6头身，比例均衡，曲线自然",
        "丰满": "约162–172cm，7.2–7.8头身，胸臀曲线明显，腰较细",
        "强壮": "约168–178cm，7.3–7.8头身，体脂低，肌肉线条清晰，肩背舒展",
    },
    "男": {
        "自动": "",
        "少年薄身": "约165–175cm，7.2–7.8头身，骨架小，肩窄，四肢纤细，肌肉量低",
        "修长": "约178–188cm，7.8–8.4头身，体型修长，肩背挺拔，四肢长，轮廓干净",
        "极瘦": "窄骨架，小胸廓，低肌肉量，上臂和小腿细，锁骨、腕骨、踝骨明显",
        "匀称": "约175–185cm，7.2–7.8头身，肩腰比例正常，四肢协调，肌肉脂肪均衡",
        "强壮": "约180–192cm，7.5–8头身，胸肩背肌肉块面明显，腰相对收，力量感强",
        "巨型": "大骨架，肩宽，胸廓厚，颈部粗，四肢粗壮，大手大脚，明显重量感",
    },
    "非人": {
        "自动": "",
        "人形": "直立人形结构，比例接近人类，可强化单一特征",
        "纤长": "体态纵向细长，四肢和颈部明显长于正常比例",
        "重型": "厚重躯干与四肢，甲壳或外骨骼体积明显",
        "巨型": "体型远超人类尺度，骨架和肢体粗壮",
        "四足": "四足着地，前肢与后肢分工明确，尾巴与背脊结构完整",
        "多肢": "额外肢体数量，四臂或多足，关节方向清楚",
    },
}

PERSONALITY_VISUALS = {
    "自动": "",
    "温柔": "眉眼舒展，嘴角轻微上扬，动作轻缓",
    "活泼": "表情明亮，嘴角上扬，手势多，动作幅度大",
    "冷静": "眉眼稳定，上眼睑自然，嘴角平稳，站姿稳定，动作幅度较小",
    "强势": "视线直接，眉形有力量，下巴略抬，肩背打开，站姿占空间明显",
    "阴郁": "低眼睑，视线略沉，嘴角放松偏低，肩部略收，动作幅度偏小",
    "狡黠": "眼神略侧，眉尾细微抬起，嘴角有轻微不对称变化",
    "疯狂": "瞳孔张力强，嘴角幅度不自然，姿态有失稳感",
}

FACE_LEGACY_MAP = {
    "幼态圆脸": "甜美", "精致鹅蛋脸": "清冷", "窄长美型脸": "清冷",
    "冷艳锐脸": "清冷", "明艳浓颜脸": "明艳", "英气骨相脸": "英气",
    "妖异美型脸": "妖异", "成熟立体脸": "成熟",
    "幼态少年脸": "美少年", "王道美型脸": "俊美", "阴柔窄脸": "阴柔",
    "冷峻锐脸": "硬朗", "英气硬朗脸": "硬朗", "粗犷宽脸": "粗犷",
    "危险反派脸": "妖异", "人形精灵脸": "人形异族", "恶魔角与异色眼": "妖异",
    "骷髅或亡灵头部": "骸骨", "兽人面部": "兽性", "龙人面部": "兽性",
    "机械头部": "机械", "无脸面具结构": "机械", "完全怪物头部": "异形",
}
BODY_LEGACY_MAP = {
    "极娇小纤弱": "娇小", "娇小匀称": "娇小", "少女纤薄": "纤细",
    "极瘦高挑": "高挑", "美型高挑": "高挑", "标准匀称": "匀称",
    "丰满曲线": "丰满", "极致曲线": "丰满", "运动紧实": "强壮",
    "高大强健": "强壮", "超高挑夸张": "高挑",
    "娇小少年": "少年薄身", "极瘦纤长": "极瘦", "美型少年": "修长",
    "修长青年": "修长", "宽肩窄腰": "强壮", "运动薄肌": "强壮",
    "强壮肌肉": "强壮", "重型壮汉": "巨型", "超高大夸张": "巨型",
    "接近人形": "人形", "细长异形": "纤长", "高大重型": "重型",
    "四臂多肢": "多肢", "兽形躯体": "四足", "机械躯体": "重型",
    "巨型怪物": "巨型",
}
POSE_LEGACY_MAP = {
    "松弛自然": "温柔", "安静冷淡": "冷静", "警觉克制": "冷静",
    "高傲从容": "强势", "活泼外放": "活泼", "妩媚主动": "狡黠",
    "阴沉危险": "阴郁", "英气利落": "强势", "胆怯拘谨": "温柔",
}

REFERENCE_BODY_ARCHETYPE = {
    ("女", "娇小"): "极娇小成年女性型",
    ("女", "极瘦"): "病弱纤骨型",
    ("女", "高挑"): "长腿神族型",
    ("女", "丰满"): "妖姬沙漏型",
    ("男", "巨型"): "墙壁霸主型",
    ("男", "极瘦"): "极瘦妖异青年型",
    ("男", "强壮"): "墙壁霸主型",
    ("男", "修长"): "病弱贵族青年型",
    ("非人", "四足"): "四足重甲巨兽型",
    ("非人", "多肢"): "四臂机械战斗体",
}


def _normalize_simple_key(sex, key, table, legacy_map, sex_grouped=True):
    """把用户选择（或旧标签）归一化到简单选项；未知时保留原文作为用户文字。"""
    value = str(key or "").strip()
    if not value or value in ("auto", "自动", "按人物原型"):
        return "自动", ""
    pool = table.get(sex, {}) if sex_grouped else table
    if value in pool:
        return value, pool[value]
    if value in legacy_map:
        simple = legacy_map[value]
        pool2 = table.get(sex, {}) if sex_grouped else table
        return simple, pool2.get(simple, "")
    return "自动", value


def expand_simple_character_options(options, visual_strength=None):
    """极简组合驱动：长相 + 身材 + 性格 + 视觉强度 + 用户文字 -> Character Contract。
    用户自由文字 > 明确服装 > 长相/身材/性格选择 > 视觉强度 > AI 补全。"""
    sex = str(options.get("sex") or "女").strip() or "女"
    age = str(options.get("age") or "").strip()
    strength = str(visual_strength or options.get("visual_strength") or "美型").strip()
    if strength in ("", "auto", "自动"):
        strength = "美型"
    if strength not in VISUAL_STRENGTH_GUIDES:
        strength = "美型"

    face_key, face_text = _normalize_simple_key(
        sex, options.get("face"), FACE_PROFILES, FACE_LEGACY_MAP)
    body_key, body_text = _normalize_simple_key(
        sex, options.get("body"), BODY_PROFILES, BODY_LEGACY_MAP)
    pose_key, pose_text = _normalize_simple_key(
        sex, options.get("personality"), PERSONALITY_VISUALS, POSE_LEGACY_MAP,
        sex_grouped=False)

    clothing = str(options.get("clothing") or "").strip()
    silhouette = str(options.get("clothing_silhouette") or "").strip()
    other = str(options.get("other") or "").strip()

    ref = REFERENCE_BODY_ARCHETYPE.get((sex, body_key))
    anchor = ""
    if ref and strength in ("强设计", "极端"):
        anchor = CHARACTER_ARCHETYPES.get(ref, {}).get("anchor", "")
    if not anchor:
        anchor = "人物最终结构锚点：%s；%s；%s。保持当前身体轮廓与识别特征。" % (
            body_text or "已设定身材", face_text or "已设定长相", pose_text or "已设定气质")

    amplify = {
        "娇小": "体积更小、四肢更纤细、头身相对更突出",
        "纤细": "四肢更修长纤细、骨架更窄",
        "极瘦": "胸廓更薄、四肢更细、骨点更明显、整体更轻更弱",
        "高挑": "腿长与纵向比例、头身比、纤长感",
        "匀称": "比例更精炼、线条更干净",
        "丰满": "胸腰臀曲线强度与体积",
        "强壮": "肩宽、胸廓厚度、肌肉块面与力量感",
        "少年薄身": "身形更薄、四肢更纤细",
        "修长": "纵向比例与四肢长度",
        "巨型": "肩宽、胸廓厚度、骨架重量与横向体量",
        "人形": "直立结构清晰度",
        "纤长": "四肢与颈部长度、整体纵向感",
        "重型": "甲壳与躯干体积、厚重感",
        "四足": "四足结构、背脊与尾锤尺度",
        "多肢": "额外肢体体积与关节结构",
    }.get(body_key, "当前身材特征")
    strength_text = VISUAL_STRENGTH_GUIDES[strength]
    if strength in ("强设计", "极端") and amplify:
        strength_text = strength_text % amplify

    return {
        "sex": sex,
        "age": age,
        "archetype": ref or "",
        "archetype_display": ref or "",
        "body_text": body_text,
        "face_text": face_text,
        "posture_text": pose_text,
        "hairstyle_text": "",
        "clothing_core_text": clothing,
        "clothing_silhouette_text": silhouette,
        "other_text": other,
        "strength_text": strength_text,
        "anchor_text": anchor,
        "style_key": "",
        "style_text": "",
        "layout_text": MASTER_SHEET_LAYOUT,
    }


PURE_STYLES = {
    "Krea2·中性角色设定测试": (
        "高质量中性角色设定渲染：以准确呈现人物设计信息为第一优先，"
        "均匀柔和棚拍光，版面干净克制，画风只负责渲染表现方式，"
        "不改变人物身体结构、脸部结构、服装轮廓与固定识别点。"
    ),
    "Krea2·电影级剧照": (
        "真人电影摄影式写实渲染，真实自然的皮肤细节，"
        "织物、头发与材料具有真实物理质感，明暗具有电影摄影级动态范围，"
        "细腻高光与暗部层次，轻微胶片式色调响应与自然颗粒。"
        "画风只改变图像的成像、材质、光影和色彩表现。"
    ),
    "Krea2·高质量日漫": (
        "现代日本高质量动画电影绘制方式，干净精确的角色线稿，"
        "精细赛璐璐着色，清楚的色块结构、阴影层次与高光处理，"
        "人物和服装具有剧场版动画级完成度。"
        "画风只改变线条、着色、光影和材质的绘制方式。"
    ),
    "Krea2·超仿真生活照": (
        "极致超仿真生活照成像：真实手机抓拍的成像质感，"
        "自然噪点与轻微曝光不均，真实色彩与光晕，粗粝真实的生活抓拍感。"
    ),
    "Krea2·80年代胶片电影": (
        "极致80年代胶片电影成像：真实老式胶片颗粒与银盐质感，"
        "浓郁复古色彩，暖红肤色，高光柔和泛光光晕，暗部偏青绿或暖棕，"
        "轻微褪色与划痕，老式钨丝灯与日光灯氛围。"
    ),
    "Krea2·复古赛璐璐80年代": (
        "极致80—90年代日本动画绘制方式：老式赛璐璐胶片质感，"
        "粗犷手绘线条，鲜明偏暖配色，硬朗的光影边界，"
        "手绘水彩渐变的背景氛围，昭和年代剧场版作画感。"
    ),
    "Krea2·黑白日漫": (
        "极致黑白日式漫画绘制方式：清晰有力的线条，"
        "排线与网点表现明暗，黑白对比强烈，单色画面干净利落，"
        "专业漫画家的分镜与笔触感。"
    ),
    "Krea2·厚涂插画": (
        "极致现代插画厚涂：扎实笔触与体积感，细腻光影过渡，"
        "颜色有厚度与层次，厚重画面质感，知名画师的商业插画笔法。"
    ),
    "Krea2·水彩油画手绘": (
        "极致油画水彩手绘：厚重鲜明的笔触，浓郁热烈的色彩，"
        "流动的印象派光影，明显的油画颜料质感，大师级原作感。"
    ),
    "Krea2·国风水墨写意": (
        "极致中国水墨画：宣纸质感，墨色浓淡干湿分明，"
        "书法运笔线条，大量留白，山石树木以皴法表现，"
        "黑白为主偶有淡彩晕染。"
    ),
    "Krea2·像素复古游戏": (
        "极致像素艺术：点阵像素构成画面，配色限定量少，"
        "明暗靠抖色与网格表现，16位或32位复古RPG过场质感。"
    ),
    "Krea2·3D游戏模型": (
        "极致3D游戏模型渲染：次世代CG渲染质感，"
        "皮肤、服装与材质有清晰真实的反射，光影写实，游戏过场动画精致感。"
    ),
    "Krea2·赛博霓虹蒸汽波": (
        "极致赛博霓虹蒸汽波：高饱和霓虹紫粉青，发光灯管与玻璃反射，"
        "胶片噪点、扫描线与光晕，潮湿迷幻失真的视觉质感。"
    ),
}

MASTER_SHEET_LAYOUT = (
    "单张16:9宽屏专业角色设定板，画面必须且只能包含四个部分："
    "同一角色的正面全身、严格90度标准侧面全身、背面全身三个等高等比例完整视图，"
    "以及一个正面脸部特写；三个全身视图从左到右并排排列，"
    "全部从头顶到脚底完整入镜，必须是同一角色，"
    "身高、头身比、骨相、发型、服装、配饰完全一致；"
    "右侧脸部特写只截取额头到下巴，清楚展示额头、眉骨、眼睛、鼻子、嘴唇、下颌，"
    "不含脖子以下；纯白或极浅灰背景，均匀柔和棚拍光，版面干净克制，"
    "以准确呈现人物设计信息为第一优先，不用戏剧化镜头，不用场景叙事，"
    "不额外改变人物本身设计。"
)

AUTO_FILL_TEXT = {
    "hairstyle": "发型：由当前角色设定自然补全，长度与轮廓与脸型协调，三个视图完全一致。",
    "clothing": (
        "服装：由角色身份自然补全，剪裁清楚，颜色材质明确，"
        "正面、侧面、背面结构一致，不改变已锁定的身体与脸部结构。"
    ),
}


# ============================================================
# 四、展开与契约
# ============================================================
def _clean(text, limit=None):
    value = " ".join(str(text or "").split()).strip("，。；; ")
    if limit and len(value) > limit:
        value = value[:limit]
    return value


def resolve_archetype(sex, archetype=None, legacy_body=None):
    """返回 (原型名, 原型定义)。兼容旧 body 标签映射。"""
    if archetype and archetype in CHARACTER_ARCHETYPES:
        name = archetype
    elif legacy_body and legacy_body in LEGACY_BODY_TO_ARCHETYPE:
        name = LEGACY_BODY_TO_ARCHETYPE[legacy_body]
    else:
        # 找不到匹配原型：退回到按性别最接近的中性原型
        name = {"女": "病弱纤骨型", "男": "病弱贵族青年型"}.get(sex, "四臂机械战斗体")
    return name, CHARACTER_ARCHETYPES[name]


def expand_character_design(options):
    """把 UI 选择展开为具体视觉结构（用户明确 > 单项选择 > 原型默认）。"""
    sex = str(options.get("sex") or "女").strip()
    age = str(options.get("age") or "").strip()
    archetype_key = str(options.get("archetype") or "").strip()
    legacy_body = str(options.get("legacy_body") or "").strip()
    strength = str(options.get("visual_strength") or "美型").strip()
    if strength not in VISUAL_STRENGTH_GUIDES:
        strength = "美型"

    name, arch = resolve_archetype(sex, archetype_key, legacy_body)

    body_override = str(options.get("body") or "").strip()
    face_override = str(options.get("face") or "").strip()
    posture_override = str(options.get("posture") or "").strip()
    hairstyle = str(options.get("hairstyle") or "").strip()
    clothing_core = str(options.get("clothing_core") or "").strip()
    clothing_silhouette = str(options.get("clothing_silhouette") or "").strip()
    other = str(options.get("other") or "").strip()

    body_text = body_override if body_override and body_override != "按原型" else arch["body"]
    face_text = face_override if face_override and face_override != "按原型" else arch["face"]
    posture_text = posture_override if posture_override and posture_override != "按原型" else arch["posture"]
    if not hairstyle:
        hairstyle = arch.get("default_hairstyle") or ""
    if not clothing_core:
        clothing_core = arch.get("default_clothing") or ""

    amplify = arch.get("amplify", "")
    strength_text = VISUAL_STRENGTH_GUIDES[strength]
    if strength in ("强设计", "极端") and amplify:
        strength_text = strength_text % amplify

    return {
        "sex": sex,
        "age": age,
        "archetype": name,
        "archetype_display": name,          # 仅 UI/规划用，不进入最终提示词
        "height": arch["height"],
        "body_text": _clean(body_text),
        "face_text": _clean(face_text),
        "posture_text": _clean(posture_text),
        "hairstyle_text": _clean(hairstyle),
        "clothing_core_text": _clean(clothing_core),
        "clothing_silhouette_text": _clean(clothing_silhouette),
        "other_text": _clean(other),
        "strength_text": strength_text,
        "anchor_text": _clean(arch["anchor"]),
    }


def build_character_contract(options):
    """生成 Character Design Contract（结构化，不含原型名称给 Krea 的依赖）。"""
    expanded = expand_character_design(options)
    style_key = str(options.get("style_key") or "Krea2·中性角色设定测试").strip()
    if style_key not in PURE_STYLES:
        style_key = "Krea2·中性角色设定测试"
    style_text_override = str(options.get("style_text_override") or "").strip()
    contract = dict(expanded)
    contract["style_key"] = style_key
    contract["style_text"] = (style_text_override or PURE_STYLES[style_key]).strip()
    contract["layout_text"] = MASTER_SHEET_LAYOUT
    return contract


# ============================================================
# 五、16:9 Character Master Sheet 提示词编译
# ============================================================
def compile_master_sheet_prompt(contract):
    """固定顺序：版式 -> 身体 -> 脸部 -> 神态 -> 发型 -> 服装 -> 识别点
    -> 纯表现画风 -> 人物核心结构重新锚定。
    返回 (完整提示词, 结构段文本)：
    - 完整提示词：最终发给 Krea 的原文（无 Markdown 星号）。
    - 结构段文本：去掉画风段后的部分，用于“换画风不换人”逐字对比。
    """
    parts = []
    layout = str(contract.get("layout_text") or MASTER_SHEET_LAYOUT)
    body = str(contract.get("body_text") or "")
    face = str(contract.get("face_text") or "")
    posture = str(contract.get("posture_text") or "")
    hairstyle = str(contract.get("hairstyle_text") or "").strip()
    clothing = str(contract.get("clothing_core_text") or "").strip()
    silhouette = str(contract.get("clothing_silhouette_text") or "").strip()
    other = str(contract.get("other_text") or "").strip()
    strength = str(contract.get("strength_text") or "").strip()
    anchor = str(contract.get("anchor_text") or "").strip()
    style = str(contract.get("style_text") or "").strip()
    sex = str(contract.get("sex") or "女性")
    age = str(contract.get("age") or "").strip()

    name_part = "人物：%s，%s岁。" % (sex, age) if age else "人物：%s。" % sex
    body_line = "身体结构：%s。" % body if body else ""
    face_line = "脸部结构：%s。" % face if face else ""
    posture_line = "神态体态：%s。" % posture if posture else ""
    hair_line = hairstyle or AUTO_FILL_TEXT["hairstyle"]
    if not str(hair_line).startswith("发型"):
        hair_line = "发型：%s。" % hair_line
    if clothing:
        cloth = clothing
        if silhouette:
            cloth = "%s；服装轮廓：%s" % (cloth, silhouette)
        cloth_line = "服装：%s。" % cloth
    elif silhouette:
        cloth_line = "服装：按角色身份自然补全，服装轮廓：%s。" % silhouette
    else:
        cloth_line = AUTO_FILL_TEXT["clothing"]
    identity_line = ""
    if other:
        identity_line = "用户明确要求（最高优先级，覆盖冲突的默认结构）：%s。" % other
    strength_line = "%s。" % strength if strength else ""

    structure = " ".join(x for x in [
        layout,
        name_part,
        body_line,
        face_line,
        posture_line,
        hair_line,
        cloth_line,
        identity_line,
        strength_line,
    ] if x)
    structure = re.sub(r"\s+", " ", structure).strip()
    structure = structure.replace("。 ，", "。").replace("。。", "。")

    final = structure + " " + style
    if anchor:
        final = final + " " + anchor
    final = re.sub(r"\s+", " ", final).strip()
    final = final.replace("**", "")
    return final, structure


# 漫画与高质量画册统一入口（同一函数对象）
comic_master_sheet_prompt = compile_master_sheet_prompt
album_master_sheet_prompt = compile_master_sheet_prompt


# ============================================================
# 六、必要程序检查（不做审美验收）
# ============================================================
def check_contract(contract):
    """只检查程序可确定的事项：字段缺失、结构是否展开、画风是否纯表现。"""
    problems = []
    for field in ("body_text", "face_text", "posture_text", "style_text", "anchor_text"):
        if not str(contract.get(field) or "").strip():
            problems.append("缺失字段：%s" % field)
    body = str(contract.get("body_text") or "")
    if len(body) < 20:
        problems.append("身体结构未展开（过短）")
    style = str(contract.get("style_text") or "")
    for bad in ("脸型", "身材", "头身比", "标准日漫人物造型", "构图有张力", "故事感"):
        if bad in style:
            problems.append("画风包含人物/构图干扰词：%s" % bad)
    return problems


def check_prompt(prompt):
    """提示词完整性：版式四视图关键字、画风段、锚点段、无 Markdown 星号。"""
    problems = []
    if "正面全身" not in prompt or "背面全身" not in prompt or "脸部特写" not in prompt:
        problems.append("Master Sheet 四视图结构不完整")
    if "画风只改变" not in prompt and "画风只负责" not in prompt:
        problems.append("缺少纯表现画风声明")
    if "结构锚点" not in prompt:
        problems.append("缺少人物核心结构重新锚定段")
    if "**" in prompt:
        problems.append("提示词包含 Markdown 加粗星号")
    return problems
