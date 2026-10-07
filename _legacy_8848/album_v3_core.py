# -*- coding: utf-8 -*-
"""
高质量画册 v3 核心模块（独立于 app.py 业务代码）。

六层逻辑：
  1. 画册预设系统
  2. 用户事实锁定
  3. 系列视觉说明书
  4. 整套规划与差异矩阵
  5. Krea2 提示词构建器（纯代码拼接，不再调用模型改写）
  6. 生成、恢复、重出、封面与 PDF 输出

对 app.py 的依赖全部采用“调用时再导入”，避免循环导入。
"""
import hashlib
import json
import os
import random
import re
import time


# ================== 1. 画册预设系统 ==================

ALBUM_V3_VERSION = 4

# 首页只呈现这四条高频工作流；其余旧预设继续保留，用于无损恢复历史项目。
ALBUM_PRIMARY_PRESETS = (
    "character_design",
    "character_photography",
    "world_character_album",
    "scene_concept",
)

SERIES_BIBLE_KEYS = (
    "subjectLock", "worldLock", "wardrobeLock", "paletteLock",
    "materialLock", "cameraLock", "styleLock", "forbiddenChanges",
)

ALBUM_ASPECT_SIZES = {
    "3:4": (1152, 1536),
    "4:3": (1536, 1152),
    "3:2": (1536, 1024),
    "16:9": (1536, 864),
    "9:16": (864, 1536),
    "1:1": (1024, 1024),
    # 旧项目兼容
    "2.35:1": (1536, 640),
    "4:5": (1024, 1280),
    "2:3": (1024, 1536),
}

# ================== 写真集摄影导演内核（固定层） ==================
# 题材可以无限替换，但摄影骨架不变：真实摄影优先于题材。
PHOTO_DIRECTOR_POST = (
    "电影负片式色彩响应，自然肤色，柔和高光滚降，黑色不完全死黑，中等微对比，"
    "适度色彩分离，非常细的摄影颗粒，真实镜头锐度而非数字锐化，肤质与布料纹理保留，不重磨皮"
)

PHOTO_LANG_CN = {
    "establishing": "建立镜头", "observational": "观察镜头",
    "transitional": "连接镜头", "hero": "主视觉镜头", "detail": "细节镜头",
}

PHOTO_DOF = {
    "24mm": "深景深，环境与结构全部清晰",
    "35mm": "中等景深，人物与环境都保持清楚",
    "50mm": "中等景深，主体清晰背景略虚",
    "85mm": "浅景深，背景明显虚化",
    "135mm": "浅景深，背景压缩虚化",
    "90mm": "极浅微距景深，只有焦平面清晰",
}

PHOTO_STORYBOARD_24 = [
    {"n": 1, "act": "进入世界", "type": "世界建立", "lens": "24mm", "aspect": "16:9",
     "shot": "远景", "lang": "establishing", "comp": "tiny_subject", "space": "环境留白40-70%",
     "action": "画面里没有人，只有太空站与地球",
     "scene": "巨大透明舷窗与太空站舱段占满画面，地球异常巨大贴近窗外，画面里没有人，只有环境和星球",
     "lensResult": "24mm广角大远景，从远处拍摄整条舱段，纯环境世界建立，画面里没有人",
     "surreal": 1, "surrealEvent": "地球异常巨大贴近舷窗"},
    {"n": 2, "act": "进入世界", "type": "环境人物", "lens": "35mm", "aspect": "3:2",
     "shot": "全身", "lang": "observational", "comp": "rule_of_thirds", "space": "视线方向留白",
     "action": "美咲第一次完整走进太空站舱段",
     "scene": "美咲第一次完整走进太空站舱段，站在钛合金舱壁、设备与织物软包之间，环境清楚",
     "lensResult": "35mm，环境人物，人物与空间关系清楚，叙事视角",
     "surreal": 0, "surrealEvent": ""},
    {"n": 3, "act": "进入世界", "type": "动态穿越", "lens": "35mm", "aspect": "3:2",
     "shot": "全身/七分身", "lang": "observational", "comp": "edge", "space": "行进方向留白",
     "action": "美咲在低重力走廊中向前行走，发丝和裙摆微微飘起",
     "scene": "美咲在低重力走廊中向前行走，发丝和裙摆微微飘起，她看向前方",
     "lensResult": "35mm跟拍，侧向移动，行进方向留白，抓拍感",
     "surreal": 0, "surrealEvent": ""},
    {"n": 4, "act": "进入世界", "type": "空镜", "lens": "50mm", "aspect": "3:2",
     "shot": "空镜", "lang": "detail", "comp": "negative_space", "space": "大留白",
     "action": "画面里没有人",
     "scene": "空镜：舱壁的机械接口、织物软包与漂浮的微尘，画面里没有人",
     "lensResult": "50mm，极简静物，大留白，呼吸感",
     "surreal": 0, "surrealEvent": ""},
    {"n": 5, "act": "认识人物", "type": "标准全身", "lens": "50mm", "aspect": "2:3",
     "shot": "全身", "lang": "hero", "comp": "central", "space": "头顶留空间",
     "action": "美咲完整站在舱段中央，从头到脚全部可见",
     "scene": "美咲完整站在舱段中央，从头到脚全部可见，双脚踩在地面上，白色针织上衣与浅蓝色短裙，姿态自然",
     "lensResult": "50mm，退远取景，完整全身从头到脚不裁切，中央构图，头顶留空间，稳定主视觉",
     "surreal": 0, "surrealEvent": ""},
    {"n": 6, "act": "认识人物", "type": "七分身", "lens": "50mm", "aspect": "4:5",
     "shot": "七分身", "lang": "hero", "comp": "edge", "space": "另一侧留白",
     "action": "美咲靠在舷窗边，侧身看向窗外",
     "scene": "美咲靠在舷窗边，七分身，侧身看向窗外的地球",
     "lensResult": "50mm，七分身商业主图，人物偏一侧，另一侧留白",
     "surreal": 0, "surrealEvent": ""},
    {"n": 7, "act": "认识人物", "type": "半身", "lens": "85mm", "aspect": "4:5",
     "shot": "半身", "lang": "hero", "comp": "rule_of_thirds", "space": "视线方向轻留白",
     "action": "美咲坐在餐桌前伸手轻触漂浮的水球",
     "scene": "美咲坐在餐桌前，一颗水滴在空气中缓慢漂浮成透明水球，她伸手轻触，神情好奇",
     "lensResult": "85mm，半身肖像，浅景深，背景虚化",
     "surreal": 1, "surrealEvent": "水滴在空气中形成漂浮水球"},
    {"n": 8, "act": "认识人物", "type": "近景", "lens": "85mm", "aspect": "4:5",
     "shot": "近景", "lang": "hero", "comp": "central", "space": "上方少量呼吸空间",
     "action": "美咲面向镜头，眼神安静",
     "scene": "美咲面向镜头，近景肖像，眼神安静清澈，背景完全虚化",
     "lensResult": "85mm，纯粹近景肖像，浅景深，背景虚化，只剩人物",
     "surreal": 0, "surrealEvent": ""},
    {"n": 9, "act": "认识人物", "type": "侧脸", "lens": "85mm", "aspect": "4:5",
     "shot": "半身/近景", "lang": "observational", "comp": "edge", "space": "侧脸方向留白",
     "action": "美咲侧脸望向舷窗，不看镜头",
     "scene": "美咲侧脸望向舷窗，窗外的地球与星空虚化成光斑",
     "lensResult": "85mm，侧脸情绪肖像，侧脸方向留白",
     "surreal": 0, "surrealEvent": ""},
    {"n": 10, "act": "认识人物", "type": "回头", "lens": "85mm", "aspect": "4:5",
     "shot": "七分身/半身", "lang": "observational", "comp": "rule_of_thirds", "space": "回头方向留白",
     "action": "美咲身体向前走，脸回头",
     "scene": "美咲身体向前走，脸回头看向身后，发丝随动作飘动",
     "lensResult": "85mm，回头经典镜头，浅景深，回头方向留白",
     "surreal": 0, "surrealEvent": ""},
    {"n": 11, "act": "人物属于世界", "type": "环境坐姿", "lens": "35mm", "aspect": "3:2",
     "shot": "中景/七分身", "lang": "observational", "comp": "frame_in_frame", "space": "人物周围保留环境",
     "action": "美咲坐在舱内软椅上，身体放松",
     "scene": "美咲坐在舱内软椅上，身体放松，身边是设备与织物，环境包裹人物",
     "lensResult": "35mm，环境坐姿，生活流，画面有呼吸空间",
     "surreal": 0, "surrealEvent": ""},
    {"n": 12, "act": "人物属于世界", "type": "人与空间", "lens": "50mm", "aspect": "3:2",
     "shot": "中景/全身", "lang": "observational", "comp": "negative_space", "space": "旋转方向留白",
     "action": "美咲站在正常重力的一端望向旋转的走廊",
     "scene": "走廊尽头重力方向旋转90度，美咲站在正常重力的这一端，望向旋转过去的走廊",
     "lensResult": "50mm，人与空间关系，纵深构图，环境留白",
     "surreal": 1, "surrealEvent": "走廊尽头重力方向旋转90度"},
    {"n": 13, "act": "人物属于世界", "type": "远距离观察", "lens": "135mm", "aspect": "3:2",
     "shot": "中景/七分身", "lang": "observational", "comp": "foreground", "space": "前景可虚化",
     "action": "美咲只是走廊最深处一个很小的人影",
     "scene": "画面90%以上是空旷的太空站走廊，美咲只是走廊最深处一个很小的人影，完整全身，高度约占画面10%，侧脸望向舷窗",
     "lensResult": "135mm长焦，人物高度约占画面10%，完整全身，大量空间包围，背景强烈压缩扁平，前景结构虚化，被观察感",
     "surreal": 0, "surrealEvent": ""},
    {"n": 14, "act": "人物属于世界", "type": "动态行为", "lens": "35mm", "aspect": "3:2",
     "shot": "全身/七分身", "lang": "observational", "comp": "edge", "space": "运动方向留白",
     "action": "美咲在舱内漂浮中转身抓住扶手",
     "scene": "美咲在低重力舱内漂浮中转身，伸手抓住扶手，发丝扬起，动态瞬间",
     "lensResult": "35mm，动态抓拍，动作有速度感，运动方向留白",
     "surreal": 0, "surrealEvent": ""},
    {"n": 15, "act": "人物属于世界", "type": "人物与道具", "lens": "50mm", "aspect": "2:3",
     "shot": "全身/七分身", "lang": "observational", "comp": "rule_of_thirds", "space": "道具方向留空间",
     "action": "美咲与一本漂浮的书互动，伸手翻页",
     "scene": "美咲与一本漂浮的书互动，伸手翻页，书页在低重力下微飘",
     "lensResult": "50mm，人物与道具，自然记录感",
     "surreal": 0, "surrealEvent": ""},
    {"n": 16, "act": "人物属于世界", "type": "情绪肖像", "lens": "85mm", "aspect": "4:5",
     "shot": "半身/近景", "lang": "observational", "comp": "edge", "space": "视线方向必须留空间",
     "action": "美咲坐在窗边低头沉思，不看镜头",
     "scene": "美咲坐在窗边低头沉思，光线从舷窗落在脸上，不看镜头",
     "lensResult": "85mm，情绪肖像，侧逆光，视线方向留白",
     "surreal": 0, "surrealEvent": ""},
    {"n": 17, "act": "视觉高潮", "type": "封面Hero", "lens": "50mm", "aspect": "4:5",
     "shot": "七分身", "lang": "hero", "comp": "central", "space": "一侧预留20-35%视觉净区",
     "action": "美咲站在星空倒影之间，稳定站姿",
     "scene": "星空倒映进入空间站内部，光斑与星云倒影铺满舱壁，美咲站在光影之间",
     "lensResult": "50mm，封面主视觉，中央构图，一侧留净区",
     "surreal": 1, "surrealEvent": "星空倒映进入空间站内部"},
    {"n": 18, "act": "视觉高潮", "type": "强眼神", "lens": "85mm", "aspect": "4:5",
     "shot": "近景", "lang": "hero", "comp": "central", "space": "少量留白，重点突出脸",
     "action": "美咲近景，强眼神直视镜头",
     "scene": "美咲近景，强眼神直视镜头，光线集中在眼睛",
     "lensResult": "85mm，强眼神近景，紧凑构图，浅景深",
     "surreal": 0, "surrealEvent": ""},
    {"n": 19, "act": "视觉高潮", "type": "极强空间", "lens": "24mm", "aspect": "2:3",
     "shot": "全身", "lang": "hero", "comp": "low_angle", "space": "头顶上方留更多空间",
     "action": "美咲站在巨大舱门前，低机位仰拍",
     "scene": "美咲站在巨大圆形舱门前，低机位仰拍全身，舱门与结构线向上汇聚",
     "lensResult": "24mm低机位仰拍，全身，强烈空间与力量感",
     "surreal": 0, "surrealEvent": ""},
    {"n": 20, "act": "视觉高潮", "type": "实验机位", "lens": "35mm", "aspect": "3:2",
     "shot": "中景", "lang": "hero", "comp": "high_angle", "space": "四周均衡呼吸空间",
     "action": "美咲躺在舱内床铺上",
     "scene": "美咲躺在舱内床铺上，高机位俯拍，光线柔和，私密安静",
     "lensResult": "35mm高机位俯拍，私密感，四周均衡留白",
     "surreal": 0, "surrealEvent": ""},
    {"n": 21, "act": "视觉高潮", "type": "特写细节", "lens": "90mm", "aspect": "4:5",
     "shot": "特写", "lang": "detail", "comp": "negative_space", "space": "可局部裁切",
     "action": "画面里只有材质，没有人",
     "scene": "特写：织物纤维与金属接缝占满画面，织物褶皱、金属划痕、灰尘、水珠清晰可见，画面里没有人、没有脸、没有手",
     "lensResult": "90mm微距，纯材质局部特写占画面70%以上，画面里没有人、没有脸、没有手",
     "surreal": 0, "surrealEvent": ""},
    {"n": 22, "act": "视觉高潮", "type": "超现实Hero", "lens": "50mm", "aspect": "16:9",
     "shot": "全景", "lang": "hero", "comp": "wide_perspective", "space": "环境大留白",
     "action": "美咲独自漂浮在巨大的透明观测穹顶中央",
     "scene": "美咲独自漂浮在巨大的透明观测穹顶中央，星云环绕，没有地面，只有她与星空",
     "lensResult": "50mm，超现实主视觉，广角透视，环境大留白，漂浮感",
     "surreal": 1, "surrealEvent": "独自漂浮在巨大透明观测穹顶"},
    {"n": 23, "act": "离开", "type": "人物变小", "lens": "35mm", "aspect": "16:9",
     "shot": "远景", "lang": "establishing", "comp": "tiny_subject", "space": "大留白",
     "action": "美咲站在巨大舷窗前看地球，人物变小",
     "scene": "美咲站在巨大舷窗前看地球，人物再次变小，情绪回落，空间辽阔",
     "lensResult": "35mm，远景，人物变小，大留白",
     "surreal": 0, "surrealEvent": ""},
    {"n": 24, "act": "离开", "type": "背影/空镜", "lens": "50mm", "aspect": "3:2",
     "shot": "全身/空镜", "lang": "transitional", "comp": "negative_space", "space": "前进方向或上方留白",
     "action": "舷窗边留下的书本与飘起的衣角，画面里没有人",
     "scene": "空镜收尾：舷窗边留下的书本与轻轻飘起的衣角，画面里没有人",
     "lensResult": "50mm，收尾空镜，极简，大留白",
     "surreal": 0, "surrealEvent": ""},
]

PHOTO_IDENTITY_BASE = {
    "focalLength": "50mm", "aspectRatio": "2:3", "shotSize": "中全景",
    "shotLanguage": "hero", "compositionType": "central",
    "negativeSpace": "头顶与两侧留适量空间", "actionHint": "标准站姿，清楚展示脸与身材比例",
    "lightTone": "A", "lookIndex": 1,
}


def album_storyboard_for_count(count):
    """从24镜头母版按数量取子集，第1张固定为身份基准图（标准全身主图）。"""
    count = max(2, min(int(count or 24), 24))
    if count >= 24:
        picked = [x for x in PHOTO_STORYBOARD_24 if x["n"] != 5]
        picked.insert(0, PHOTO_STORYBOARD_24[4])  # 母版05标准全身作为身份基准
        return picked[:24]
    plan = {
        4: [1, 5, 17, 24],
        6: [1, 5, 8, 17, 21, 24],
        8: [1, 2, 5, 8, 17, 19, 21, 23],
        12: [1, 2, 5, 7, 8, 11, 13, 17, 18, 19, 21, 23],
    }.get(count, [1, 5, 17, 24])
    by_index = {x["n"]: x for x in PHOTO_STORYBOARD_24}
    picked = [by_index[i] for i in plan if i in by_index and i != 5]
    picked.insert(0, PHOTO_STORYBOARD_24[4])  # 第1张身份基准=标准全身主图
    return picked[:count]


ALBUM_PDF_TEMPLATES = ("cinematic", "fashion", "setting", "catalog", "pure")

ALBUM_LEGACY_MODE_MAP = {
    "cinematic_album": "cinematic_story",
    "fixed_portrait": "character_photography",
    "themed_people": "world_character_album",
    "world_album": "scene_concept",
    "creature_codex": "creature_encyclopedia",
    "object_design": "weapon_prop",
    "character_design": "character_design",
}

ALBUM_PRESETS = {
    "cinematic_story": {
        "id": "cinematic_story", "name": "电影叙事画册", "kernel": "narrative",
        "description": "像同一部高预算电影的精选剧照，有开场、推进、核心画面、变化和余韵。",
        "allowedSubjects": ["电影剧照", "人物", "场景", "事件瞬间"],
        "forbiddenSubjects": [],
        "requiredPlanFields": ["storyBeat", "continuityFromPrevious", "pushToNext"],
        "sequenceRules": ["开场", "推进", "核心画面", "变化", "余韵", "主视觉", "收尾"],
        "diversityRules": {
            "存在开场、推进、主视觉和结尾", "主要事件不重复", "情绪或事件有递进",
            "核心高潮不能出现在每张图片",
        },
        "sampleRule": "能代表整套世界、人物和冲突的核心剧照",
        "promptRules": ["高预算电影剧照，决定性瞬间，人物调度与空间纵深清楚"],
        "pdfTemplate": "cinematic",
    },
    "character_photography": {
        "id": "character_photography", "name": "固定人物写真", "kernel": "character",
        "featured": True,
        "description": "同一个固定人物，整套锁定同一张脸和身材，只换姿势、场景和服装层。",
        "allowedSubjects": ["同一人物", "真实人物", "肖像", "全身", "环境人像"],
        "forbiddenSubjects": [],
        "requiredPlanFields": [],
        "sequenceRules": ["建立", "环境", "动作变化", "细节", "主视觉", "收尾"],
        "diversityRules": {
            "景别至少3种", "机位至少3种", "动作至少4种", "构图至少3种",
            "至少1张近景", "至少1张全身或中全身", "至少1张场景占比较大", "至少1张主视觉",
            "不能全部看镜头", "不能全部站立", "不能连续两张完全相同景别和机位",
        },
        "sampleRule": "清楚展示脸、身材和主要服装的中全身主视觉",
        "promptRules": ["高端人物编辑摄影，真实镜头成像，精确骨相与五官几何，细腻皮肤和发丝"],
        "pdfTemplate": "fashion",
    },
    "fashion_editorial": {
        "id": "fashion_editorial", "name": "时尚杂志大片", "kernel": "character",
        "description": "同一审美体系下的时尚编辑大片，强调服装、姿态、棚拍与杂志排版。",
        "allowedSubjects": ["模特", "服装", "造型", "棚拍"],
        "forbiddenSubjects": [],
        "requiredPlanFields": ["outfitFocus", "magazineSpread"],
        "sequenceRules": ["建立", "服装变化", "姿态变化", "细节", "主视觉", "收尾"],
        "diversityRules": {
            "景别至少3种", "机位至少3种", "动作至少4种", "构图至少3种",
            "服装或造型每张明显变化", "至少1张大留白版面", "至少1张主视觉",
        },
        "sampleRule": "最能展示服装与人物姿态的中全景主视觉",
        "promptRules": ["国际顶级时尚杂志编辑大片，精确面部骨相与身体比例，高级定制服装，摄影棚级控光"],
        "pdfTemplate": "fashion",
    },
    "lifestyle_photography": {
        "id": "lifestyle_photography", "name": "生活写真", "kernel": "character",
        "description": "同一人物在真实生活场景中的纪实写真，自然光、真实动作、连续生活片段。",
        "allowedSubjects": ["人物", "生活场景", "自然动作"],
        "forbiddenSubjects": [],
        "requiredPlanFields": ["dailyContext"],
        "sequenceRules": ["建立", "环境", "动作变化", "细节", "主视觉", "收尾"],
        "diversityRules": {
            "景别至少3种", "机位至少3种", "动作至少4种", "场景至少3种",
            "自然光为主", "至少1张近景", "至少1张主视觉",
        },
        "sampleRule": "自然光下最能代表人物日常状态的中景主视觉",
        "promptRules": ["真实生活纪实摄影，自然光，可信天气与空气状态，真实材质"],
        "pdfTemplate": "cinematic",
    },
    "character_design": {
        "id": "character_design", "name": "角色设定图", "kernel": "character",
        "featured": True,
        "description": "单张 16:9 专业角色设定板：严格正面、侧面、背面三视图与面部放大特写。",
        "allowedSubjects": ["角色设计", "设定页", "多视图", "表情"],
        "forbiddenSubjects": [],
        "requiredPlanFields": ["viewType", "designFocus", "detailLabels"],
        "sequenceRules": ["单张完整设定板", "严格正侧背三视图", "单个大面部特写"],
        "diversityRules": {
            "有正面全身", "有侧面或背面", "有头部角度", "有面部特写",
            "同一骨相、五官、体型与锚点完全统一",
        },
        "sampleRule": "完整三视图与面部特写都清楚可见的设定板",
        "promptRules": ["专业角色开发设定板，严格等比例正侧背三视图，面部多角度放大特写，清晰无裁切，无文字乱码"],
        "pdfTemplate": "setting",
    },
    "costume_design": {
        "id": "costume_design", "name": "服装设定集", "kernel": "character",
        "description": "同一服装体系的设计集：全身穿搭、面料细节、层穿结构、配饰与不同角度。",
        "allowedSubjects": ["服装设计", "穿搭", "面料", "配饰"],
        "forbiddenSubjects": [],
        "requiredPlanFields": ["viewType", "designFocus", "detailLabels"],
        "sequenceRules": ["全身穿搭", "面料细节", "层穿结构", "配饰", "主视觉", "收尾"],
        "diversityRules": {
            "有正面全身穿搭", "有侧面或背面", "有面料材质细节", "有配饰细节", "有主视觉",
        },
        "sampleRule": "正面全身穿搭主视觉",
        "promptRules": ["高级服装设计设定板，清晰版型与面料，摄影棚布光"],
        "pdfTemplate": "setting",
    },
    "character_group": {
        "id": "character_group", "name": "主题人物群像", "kernel": "character",
        "description": "同一世界观/服装主题下的不同人物，每张主动换脸、年龄、体型与职业。",
        "allowedSubjects": ["不同人物", "人物群像", "主题服装"],
        "forbiddenSubjects": [],
        "requiredPlanFields": ["personIdentity", "groupRole"],
        "sequenceRules": ["建立", "人物亮相", "关系推进", "细节", "主视觉", "收尾"],
        "diversityRules": {
            "每张人物身份不同", "脸型、年龄、体型、职业主动变化", "服装主题统一",
            "景别至少3种", "至少1张双人或多人互动", "至少1张主视觉",
        },
        "sampleRule": "最能代表主题服装与人物体系的主视觉",
        "promptRules": ["高端主题人物编辑摄影，鲜明人物轮廓，系列化服装与场景美术"],
        "pdfTemplate": "catalog",
    },
    "world_character_album": {
        "id": "world_character_album", "name": "世界观人物画册", "kernel": "character",
        "featured": True,
        "description": "先锁定一个世界的时代、文明、材质与光色，再生成身份、职业、脸型和体型各不相同的世界居民。",
        "allowedSubjects": ["不同人物", "世界居民", "职业肖像", "环境人像"],
        "forbiddenSubjects": [],
        "requiredPlanFields": ["personIdentity", "groupRole"],
        "sequenceRules": ["世界建立", "职业亮相", "身份变化", "环境关系", "主视觉", "收尾"],
        "diversityRules": {
            "每张必须是不同人物", "职业、年龄、脸型、体型和服装轮廓主动变化",
            "所有人物共享同一时代、文明、材质、色谱和摄影语言", "至少有一张环境占比较大的职业全身像",
        },
        "sampleRule": "最能同时说明世界规则与人物职业的环境全身像",
        "promptRules": ["高端世界观人物概念摄影，同一世界美术体系，不同身份不同脸，职业与环境关系可信"],
        "pdfTemplate": "catalog",
    },
    "worldbuilding": {
        "id": "worldbuilding", "name": "世界观设定集", "kernel": "setting",
        "description": "一个世界的设定图集：地理、文明、建筑、生态、器物与整体气质。",
        "allowedSubjects": ["地理", "文明", "建筑", "生态", "器物"],
        "forbiddenSubjects": [],
        "requiredPlanFields": ["viewType", "designFocus"],
        "sequenceRules": ["地理建立", "文明地标", "建筑空间", "生态细节", "主视觉", "收尾"],
        "diversityRules": {
            "有整体世界观图", "有文明地标", "有建筑或空间", "有生态或器物细节", "有主视觉",
        },
        "sampleRule": "能概括整个世界的宏大主视图",
        "promptRules": ["高预算影视世界观概念美术，宏大尺度，可信空间与材质"],
        "pdfTemplate": "setting",
    },
    "environment_concept": {
        "id": "environment_concept", "name": "场景概念集", "kernel": "setting",
        "description": "同一世界的场景概念图：整体氛围、关键地点、结构材质与光线。",
        "allowedSubjects": ["场景", "环境", "概念美术"],
        "forbiddenSubjects": ["人物", "人脸", "人像", "行人", "人群", "人体", "肖像", "面孔"],
        "requiredPlanFields": ["viewType", "designFocus"],
        "sequenceRules": ["整体氛围", "关键地点", "结构材质", "光线变化", "主视觉", "收尾"],
        "diversityRules": {
            "有整体环境", "有关键地点", "有结构或材质细节", "有光线变化", "有主视觉", "画面无人",
        },
        "sampleRule": "能代表整套世界氛围的整体环境主视图",
        "promptRules": ["纯场景概念美术，空旷环境，无人物，无文字"],
        "pdfTemplate": "cinematic",
    },
    "scene_concept": {
        "id": "scene_concept", "name": "场景概念图", "kernel": "setting",
        "featured": True,
        "description": "只需写题材、建筑语言与空间尺度；系统负责构图、材质、光线和尺度参照，可选极小背影人影。",
        "allowedSubjects": ["建筑", "城市", "室内", "遗迹", "自然场景", "巨构", "小型空间"],
        "forbiddenSubjects": ["正面人物", "人物肖像", "人群", "主体人物"],
        "requiredPlanFields": ["spaceType", "scaleReference", "structuralFocus"],
        "sequenceRules": ["远观整体", "空间关系", "结构材质", "光线气氛", "主视觉"],
        "diversityRules": {
            "主体始终是场景与建筑", "空间尺度必须可读", "结构、材质与环境关系可信",
            "若有人物，只能是远处极小背影剪影用于尺度参照",
        },
        "sampleRule": "最能表现空间尺度、建筑结构与整体美学的远观主视图",
        "promptRules": ["极致场景概念美术，建筑与空间为唯一主体，远观大构图，可信结构材质与尺度"],
        "pdfTemplate": "cinematic",
    },
    "architecture_space": {
        "id": "architecture_space", "name": "建筑与空间", "kernel": "setting",
        "description": "纯建筑环境图册：整体、入口、内部、结构、材质与环境关系，画面禁止人物。",
        "allowedSubjects": ["建筑", "城市", "街道", "室内", "遗迹", "桥梁", "地标"],
        "forbiddenSubjects": ["人物", "人脸", "人像", "行人", "人群", "人体", "肖像", "面孔", "模特"],
        "requiredPlanFields": ["spaceType", "scaleReference", "structuralFocus"],
        "sequenceRules": ["整体外观", "入口或过渡空间", "内部空间", "结构或材质细节", "主视觉", "收尾"],
        "diversityRules": {
            "有整体外观", "有入口或过渡空间", "有内部空间", "有结构或材质细节",
            "有空间与环境关系", "有主视觉", "画面严格无人",
        },
        "sampleRule": "整体空间主视图",
        "promptRules": ["高预算影视建筑概念图，纯建筑与环境构成，宏大尺度，可信空间与结构"],
        "pdfTemplate": "cinematic",
    },
    "travel_documentary": {
        "id": "travel_documentary", "name": "旅行纪实", "kernel": "setting",
        "description": "自然与人文旅行摄影集，真实地点、天气、光线和空间尺度。",
        "allowedSubjects": ["风景", "城市", "人文景观", "自然"],
        "forbiddenSubjects": [],
        "requiredPlanFields": ["spaceType", "scaleReference"],
        "sequenceRules": ["建立", "环境", "动作变化", "细节", "主视觉", "收尾"],
        "diversityRules": {
            "场景至少3种", "光线变化", "尺度参照清楚", "有主视觉",
        },
        "sampleRule": "最能代表旅行目的地的整体风光主视图",
        "promptRules": ["顶级自然与人文纪实摄影，真实环境中的自然光，可信天气与空气状态"],
        "pdfTemplate": "cinematic",
    },
    "creature_encyclopedia": {
        "id": "creature_encyclopedia", "name": "幻想生物图鉴", "kernel": "object",
        "description": "非人类生物独占画面，全身比例、局部结构、栖息环境、行动方式和物种变体。",
        "allowedSubjects": ["生物", "怪物", "幻想物种"],
        "forbiddenSubjects": ["普通人", "人类", "人脸", "女人脸", "男人脸", "美女", "模特", "骑手", "驯兽师"],
        "requiredPlanFields": ["displayMode", "structureFocus", "surfaceDetail"],
        "sequenceRules": ["完整全身", "侧面或结构", "局部细节", "材质展示", "功能或使用状态", "主视觉"],
        "diversityRules": {
            "有完整主体", "有侧面或结构", "有局部细节", "有材质展示",
            "有功能或使用状态", "有主视觉", "画面禁止人类",
        },
        "sampleRule": "完整全身和主要结构",
        "promptRules": ["电影生物概念设计图，单一生物占据视觉中心，可信体重与解剖结构"],
        "pdfTemplate": "catalog",
    },
    "weapon_prop": {
        "id": "weapon_prop", "name": "武器与道具", "kernel": "object",
        "description": "武器、道具、盔甲、载具等器物独立陈列，禁出现人物和手。",
        "allowedSubjects": ["武器", "道具", "盔甲", "载具", "机械"],
        "forbiddenSubjects": ["人物", "人脸", "人像", "女人", "男人", "人群", "手持", "双手", "穿戴者", "驾驶员", "骑手", "模特", "人体", "肖像", "面孔"],
        "requiredPlanFields": ["displayMode", "structureFocus", "surfaceDetail"],
        "sequenceRules": ["完整主体", "侧面或结构", "局部细节", "材质展示", "功能或使用状态", "主视觉"],
        "diversityRules": {
            "有完整主体", "有侧面或结构", "有局部细节", "有材质展示",
            "有功能或使用状态", "有主视觉", "画面禁止人物和手",
        },
        "sampleRule": "完整主体陈列图",
        "promptRules": ["器物独立完整陈列，画面只展示主体"],
        "pdfTemplate": "catalog",
    },
    "armor_vehicle": {
        "id": "armor_vehicle", "name": "盔甲与载具", "kernel": "object",
        "description": "盔甲、载具、机械的工业设计图鉴，强调结构、材质、展开状态与功能。",
        "allowedSubjects": ["盔甲", "载具", "机械", "装备"],
        "forbiddenSubjects": ["人物", "人脸", "人像", "女人", "男人", "人群", "手持", "双手", "穿戴者", "驾驶员", "骑手", "模特", "人体", "肖像", "面孔"],
        "requiredPlanFields": ["displayMode", "structureFocus", "surfaceDetail"],
        "sequenceRules": ["完整主体", "侧面或结构", "局部细节", "材质展示", "功能或使用状态", "主视觉"],
        "diversityRules": {
            "有完整主体", "有侧面或结构", "有局部细节", "有材质展示",
            "有展开或使用状态", "有主视觉", "画面禁止人物和手",
        },
        "sampleRule": "完整主体陈列图",
        "promptRules": ["器物独立完整陈列，完整外轮廓，清晰连接结构，真实制造材料"],
        "pdfTemplate": "catalog",
    },
    "product_advertising": {
        "id": "product_advertising", "name": "产品广告", "kernel": "object",
        "description": "产品广告图册，最能展示产品外观和用途的主视觉，允许环境化展示。",
        "allowedSubjects": ["产品", "商品", "包装", "广告摄影"],
        "forbiddenSubjects": ["人脸", "人像", "女人", "男人", "人群", "手持", "双手", "人体", "肖像", "面孔"],
        "requiredPlanFields": ["displayMode", "structureFocus", "surfaceDetail"],
        "sequenceRules": ["完整产品", "结构细节", "材质展示", "用途场景", "主视觉", "收尾"],
        "diversityRules": {
            "有完整产品", "有结构或材质细节", "有用途场景", "有主视觉",
        },
        "sampleRule": "最能展示产品外观和用途的主视觉",
        "promptRules": ["高端产品广告摄影，主体完整清楚，棚拍级控光，无文字"],
        "pdfTemplate": "fashion",
    },
    "surreal_art": {
        "id": "surreal_art", "name": "超现实艺术", "kernel": "narrative",
        "description": "博物馆级超现实艺术摄影集，真实材质与不可能空间严密结合，画面安静而陌生。",
        "allowedSubjects": ["超现实场景", "艺术装置", "幻想空间"],
        "forbiddenSubjects": [],
        "requiredPlanFields": ["storyBeat", "visualMetaphor"],
        "sequenceRules": ["建立", "变化", "核心画面", "细节", "主视觉", "收尾"],
        "diversityRules": {
            "每张一个主要视觉隐喻", "空间尺度大胆变化", "材质与光线统一", "有主视觉",
        },
        "sampleRule": "最能代表整套艺术语言的核心画面",
        "promptRules": ["博物馆级超现实艺术摄影，真实摄影材质与不可能空间严密结合"],
        "pdfTemplate": "cinematic",
    },
}


ALBUM_KERNEL_FINISH = {
    "character": "高端人物摄影成品，真实镜头成像，精确骨相与五官几何，细腻皮肤和发丝",
    "narrative": "高预算电影剧照成品，真实镜头成像，决定性瞬间，人物调度与空间纵深清楚，克制电影调色",
    "setting": "高预算影视概念美术成品，纯建筑与环境构成，宏大尺度，可信空间与结构",
    "object": "博物馆级电影道具与工业设计成品图，单一器物主体，精确结构与材质",
}


def album_get_presets():
    presets = []
    order = list(ALBUM_PRIMARY_PRESETS) + [
        key for key in sorted(ALBUM_PRESETS) if key not in ALBUM_PRIMARY_PRESETS
    ]
    for key in order:
        preset = dict(ALBUM_PRESETS[key])
        preset["featured"] = bool(preset.get("featured"))
        preset["diversityRules"] = sorted(preset.get("diversityRules") or [])
        presets.append(preset)
    return presets


def album_get_preset(preset_id):
    preset = ALBUM_PRESETS.get(preset_id or "")
    if not preset:
        raise RuntimeError("未知画册预设：" + str(preset_id))
    return preset


# ================== 通用工具 ==================

def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _clean(value, limit=None):
    s = " ".join(str(value or "").replace("\n", " ").split()).strip("，。；; ")
    if limit:
        s = s[:limit]
    return s


def _album_dup(items, value):
    v = str(value or "").strip()
    return any(v in str(x) or str(x) in v for x in items)


def album_project_abs(project):
    from app import COMIC_DIR, safe_name
    rel = str(project.get("dir") or "").replace("\\", "/")
    parts = [safe_name(x) for x in rel.split("/") if x]
    return os.path.join(COMIC_DIR, *parts) if parts else COMIC_DIR


def _album_set_dir(project):
    from app import safe_name
    preset = album_get_preset(project.get("preset") or "")
    rel = os.path.join("高质量画册", safe_name(preset["name"]),
                       safe_name(project.get("name") or "未命名画册")).replace("\\", "/")
    project["dir"] = rel
    return rel


# ================== 2. 用户事实锁定 ==================

def _rule_locked_facts(text):
    facts = []

    def add(f_type, value, source="用户输入"):
        value = _clean(value, 60)
        if value and not any(f["type"] == f_type and f["value"] == value for f in facts):
            facts.append({"type": f_type, "value": value, "source": source})

    for m in re.finditer(r"\d{1,3}\s*岁", text):
        add("age", m.group(0))
    for kw in ("女性", "男性", "女孩", "男孩", "少女", "少年", "女人", "男人"):
        if kw in text:
            add("gender", kw)
            break
    for kw in ("酒红色", "银白色", "冰蓝色", "深蓝色", "浅蓝色", "米白色", "奶白色",
               "暖金色", "冷灰色", "朱红色", "墨黑色", "月白色", "大红色", "粉红色",
               "紫红色", "金黄色", "银灰色", "黑色", "白色", "红色", "蓝色", "绿色",
               "黄色", "金色", "银色", "灰色", "青色", "紫色", "粉色", "棕色", "褐色"):
        if kw in text:
            if any(f["type"] == "color" and kw in f["value"] and kw != f["value"]
                   for f in facts):
                continue
            add("color", kw)
    for m in re.finditer(r"\d+(?:\.\d+)?\s*(?:米|cm|CM|kg|人|个|只|条|座|栋|层)", text):
        add("number", m.group(0))
    for kw in ("城市", "王城", "宫殿", "森林", "沙漠", "海洋", "太空", "飞船", "岛屿", "寺庙", "城堡", "街道", "遗迹"):
        if kw in text:
            add("place", kw)
            break
    return facts


def _model_locked_facts(ui):
    from app import chat_stream, _extract_json
    system = (
        "你是画册用户事实提取器。只提取用户明确写出的不可修改事实：年龄、性别、体型、"
        "脸型、眼睛、发型发色、服装、地点、时代、物种、数量、动作、材质。"
        "不要改写、补充或解释原文。只输出JSON数组，不要代码块："
        '[{"type":"age","value":"23岁","source":"用户主题"}]'
    )
    user = "\n\n".join(
        "%s：%s" % (label, str(ui.get(key) or "").strip())
        for label, key in (
            ("用户主题", "originalTheme"), ("固定人物外观", "fixedCharacter"),
            ("高级补充", "advancedRequirements"), ("必须出现", "mustInclude"),
            ("禁止出现", "mustExclude"),
        )
        if str(ui.get(key) or "").strip()
    )
    raw = chat_stream([{"role": "system", "content": system},
                       {"role": "user", "content": user}], 0.3, None, 1200).strip()
    obj = _extract_json(raw)
    if isinstance(obj, dict):
        obj = obj.get("lockedFacts")
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    return []


def album_extract_locked_facts(user_input):
    ui = dict(user_input or {})
    texts = [
        str(ui.get("originalTheme") or "").strip(),
        str(ui.get("fixedCharacter") or "").strip(),
        str(ui.get("advancedRequirements") or "").strip(),
    ]
    original_text = "\n\n".join(x for x in texts if x)
    must_include = [str(x).strip() for x in (ui.get("mustInclude") or []) if str(x).strip()]
    must_exclude = [str(x).strip() for x in (ui.get("mustExclude") or []) if str(x).strip()]
    facts = _rule_locked_facts(original_text)
    try:
        model_facts = _model_locked_facts(ui)
        if model_facts:
            seen = {(f.get("type"), _clean(f.get("value"))) for f in facts}
            for f in model_facts:
                key = (str(f.get("type") or ""), _clean(f.get("value")))
                if key[1] and key not in seen:
                    seen.add(key)
                    facts.append({
                        "type": str(f.get("type") or "fact"),
                        "value": _clean(f.get("value"), 60),
                        "source": str(f.get("source") or "用户输入"),
                    })
    except Exception:
        pass
    return {
        "originalText": original_text,
        "lockedFacts": facts,
        "mustInclude": must_include,
        "mustExclude": must_exclude,
    }


def album_expand_brief(user_input):
    """把用户的一两句话补成可编辑的专业创作说明；原文事实永远优先。"""
    from app import chat_stream
    ui = dict(user_input or {})
    preset = album_get_preset(str(ui.get("preset") or "character_design"))
    original = _clean(ui.get("originalTheme"), 1200)
    if not original:
        raise RuntimeError("请先写一句你想要的画面或人物")
    mode_rules = {
        "character_design": (
            "按以下顺序补齐：角色身份与年龄、体型与头身比、脸型与五官几何、发型发色、"
            "服装层次与材质、标志配饰/武器、主辅色。结尾明确单张正侧背三视图与一个正面面部特写"
            "（只截取额头到下巴，眼鼻唇占满画面），不要服装道具局部特写。"
        ),
        "character_photography": (
            "先补齐不会变化的人物身份证：年龄、骨相、五官距离、发型发色、肤色、身高体型和唯一辨识点；"
            "再补齐写真主题、可变化服装、姿势角度、场景与光线。强调始终同一张脸和同一身材。"
        ),
        "world_character_album": (
            "补齐时代/文明、魔法或科技规则、建筑语汇、服装体系、共同纹样、材料、色谱和自然光；"
            "再自动提出至少六种身份职业。人物必须年龄、脸型、体型和职业不同，但世界规则完全一致。"
        ),
        "scene_concept": (
            "补齐远观空间布局、建筑或自然结构、前中后景、核心材质、光线天气、色谱和尺度参照。"
            "严格按用户写的大/小空间，不擅自改变建筑方向；若允许人物，只使用远处极小无脸背影剪影。"
        ),
    }
    system = (
        "你是顶级艺术总监。把用户的简短想法扩写成一份可以直接修改的中文视觉创作说明。"
        "保留用户的每一条明确事实，不改变年龄、性别、外貌、时代、地点、颜色、数量和尺度；"
        "只补足用户没有说明但生成高质量图像必需的可见细节。不要空话、不要解释、不要标题、不要列表编号，"
        "写成180到360字的一段具体说明。当前类型是“%s”。%s" % (
            preset.get("name"), mode_rules.get(preset.get("id"), "补齐主体、空间、材质、光线、色谱和构图。"))
    )
    context = "用户原文：%s" % original
    for label, key in (("固定人物", "fixedCharacter"), ("固定世界", "fixedWorld"),
                       ("风格方向", "albumDirection"), ("空间尺度", "sceneScale"),
                       ("建筑语言", "architectureStyle")):
        value = _clean(ui.get(key), 500)
        if value:
            context += "\n%s：%s" % (label, value)
    try:
        raw = chat_stream([{"role": "system", "content": system},
                           {"role": "user", "content": context}],
                          0.55, None, 1500).strip()
        expanded = _clean(raw, 1400)
    except Exception:
        expanded = ""
    if expanded:
        return expanded
    fallback = {
        "character_design": "；严格制作同一人物正面、左侧面、背面全身三视图，补充一个正面面部特写（只截取额头到下巴，眼鼻唇占满画面），不要服装道具局部特写。",
        "character_photography": "；先建立清楚的同一人物身份基准，锁定骨相、五官距离、发型发色、身高和身材比例，再以不同服装、姿势、机位与环境延伸整套写真。",
        "world_character_album": "；统一时代文明、建筑服装语言、材料纹样、色谱与自然光规则，安排职业、年龄、脸型、体型各不相同的世界居民。",
        "scene_concept": "；补足远观空间布局、前中后景、结构材质、天气光线和尺度参照，建筑与场景保持绝对主体。",
    }
    return original + fallback.get(preset.get("id"), "；补足主体、空间、材质、光线、色谱和构图。")


# ================== 3/4. 规划上下文、整套规划与校验 ==================

ALBUM_PLAN_FIELDS = (
    "title", "role", "subject", "visibleAnchors", "wardrobe", "action", "bodyPose",
    "expression", "shotSize", "cameraAngle", "lensFeeling", "environment", "lighting",
    "color", "material", "composition", "uniquePoint", "caption", "sampleScore",
)


def album_sequence_roles(count):
    return {
        1: ["main_visual"],
        4: ["opening", "variation", "main_visual", "ending"],
        6: ["opening", "environment", "action", "detail", "main_visual", "ending"],
        8: ["opening", "environment", "mid", "action", "closeup", "detail", "main_visual", "ending"],
        12: ["opening", "opening", "development", "development", "action", "action",
             "main_visual", "detail", "detail", "variation", "ending", "ending"],
    }.get(int(count or 6), ["opening", "variation", "main_visual", "ending", "detail", "action"])


def album_project_count(project_or_preset, requested=None):
    """统一四条主工作流的数量规则，避免前端和后端各算一套。"""
    if isinstance(project_or_preset, dict):
        preset_id = str(project_or_preset.get("preset") or "")
        ui = project_or_preset.get("userInput") or {}
        value = ui.get("count") if requested is None else requested
    else:
        preset_id = str(project_or_preset or "")
        value = requested
    try:
        count = int(value or (1 if preset_id in ("character_design", "scene_concept") else 6))
    except (TypeError, ValueError):
        count = 6
    if preset_id == "character_design":
        return 1
    if preset_id == "scene_concept":
        return max(1, min(count, 8))
    if preset_id == "character_photography":
        return max(4, min(count, 24))
    if preset_id == "world_character_album":
        return max(4, min(count, 12))
    return max(2, min(count, 12))


def album_pick_aspect(preset_id, img):
    """自动设计画幅：先看镜头景别与职责，再按类型回退。"""
    shot = str(img.get("shotSize") or "")
    role = str(img.get("role") or "")
    comp = str(img.get("composition") or "")
    if preset_id == "character_design":
        return "16:9"
    if preset_id == "world_character_album":
        return "16:9"
    if preset_id == "scene_concept":
        if any(k in shot for k in ("远景", "全景", "大景", "远观")):
            return "16:9"
        if any(k in shot for k in ("特写", "近景", "局部")):
            return "3:4"
        return "16:9"
    # 人物写真类：宽屏给全景环境，竖屏给全身/半身/特写
    if any(k in shot for k in ("特写", "近景", "局部", "眼神", "眼睛")):
        return "9:16" if "9:16" in comp or "竖" in comp else "3:4"
    if any(k in shot for k in ("远景", "全景", "环境", "大景")):
        return "16:9"
    if any(k in shot for k in ("全身", "中全景", "七分身")):
        return "3:4"
    if role == "main_visual":
        return "3:4"
    return "3:4"


def album_resolve_aspect(project, img=None):
    """返回项目/单张实际使用的画幅；auto 时按镜头自动决定。"""
    ui = project.get("userInput", {})
    aspect = str(ui.get("aspectRatio") or "")
    preset_id = str(project.get("preset") or "")
    if aspect == "auto":
        if img is not None:
            preset_aspect = str(img.get("aspectRatio") or "")
            if preset_aspect in ALBUM_ASPECT_SIZES:
                return preset_aspect
            return album_pick_aspect(preset_id, img)
        return "16:9" if preset_id == "scene_concept" else "3:4"
    return aspect if aspect in ALBUM_ASPECT_SIZES else "16:9"


def album_sample_score(preset, img):
    img = img or {}
    kernel = preset.get("kernel")
    role = str(img.get("role") or "")
    shot = str(img.get("shotSize") or "")
    score = 0
    if kernel == "character":
        if role == "identity_reference":
            score += 95
        elif role == "main_visual":
            score += 20
        if any(k in shot for k in ("中全景", "中景", "七分身")):
            score += 25
        elif "全身" in shot:
            score += 15
        if _clean(img.get("wardrobe")):
            score += 10
        if _clean(img.get("subject")):
            score += 10
        if _clean(img.get("expression")):
            score += 5
        if "正面" in str(img.get("cameraAngle") or ""):
            score += 5
    elif kernel == "narrative":
        if role == "main_visual":
            score += 40
        if _clean(img.get("environment")):
            score += 20
        if _clean(img.get("subject")):
            score += 15
        if _clean(img.get("lighting")):
            score += 10
        if _clean(img.get("composition")):
            score += 15
    elif kernel == "setting":
        if role == "main_visual":
            score += 35
        if _clean(img.get("spaceType")) or "空间" in str(img.get("title") or ""):
            score += 20
        if _clean(img.get("scaleReference")):
            score += 15
        if _clean(img.get("structuralFocus")):
            score += 15
        if _clean(img.get("composition")):
            score += 15
    elif kernel == "object":
        dm = str(img.get("displayMode") or "")
        if dm and any(k in dm for k in ("完整", "全身", "整体", "陈列", "正面")):
            score += 30
        if _clean(img.get("structureFocus")):
            score += 20
        if _clean(img.get("surfaceDetail")):
            score += 15
        if _clean(img.get("material")):
            score += 15
        if _clean(img.get("composition")):
            score += 10
    return max(0, min(100, score + 10))


def album_normalize_plan(project, candidate):
    if not isinstance(candidate, dict):
        raise ValueError("规划不是JSON对象")
    preset = album_get_preset(project.get("preset") or "")
    count = album_project_count(project)
    ui = project.get("userInput", {}) or {}
    raw_images = candidate.get("images")
    if not isinstance(raw_images, list):
        raw_images = candidate.get("items")
    if not isinstance(raw_images, list) or len(raw_images) != count:
        raise ValueError("图片数量不正确：应为%d张" % count)
    default_roles = album_sequence_roles(count)
    storyboard = (album_storyboard_for_count(count)
                  if preset.get("id") == "character_photography" else [])
    bible_raw = candidate.get("seriesBible") or {}
    if not isinstance(bible_raw, dict):
        bible_raw = {}
    series_bible = {k: str(bible_raw.get(k) or "") for k in SERIES_BIBLE_KEYS}
    forbidden = bible_raw.get("forbiddenChanges") or []
    series_bible["forbiddenChanges"] = [str(x) for x in forbidden if str(x).strip()]
    seq_raw = candidate.get("sequenceDesign") or {}
    if not isinstance(seq_raw, dict):
        seq_raw = {}
    sequence_design = {
        k: str(seq_raw.get(k) or "") for k in
        ("overallGoal", "opening", "development", "mainVisual", "variation", "ending")
    }
    images = []
    for i, raw in enumerate(raw_images, 1):
        if not isinstance(raw, dict):
            raw = {}
        img = {}
        for k in ALBUM_PLAN_FIELDS:
            if k == "visibleAnchors":
                value = raw.get(k) or []
                img[k] = value if isinstance(value, (list, dict)) else []
            else:
                img[k] = str(raw.get(k) or "")
        img["index"] = i
        img["role"] = str(raw.get("role") or (
            default_roles[i - 1] if i <= len(default_roles) else "variation"))
        img["title"] = _clean(raw.get("title") or raw.get("name") or ("第%d张" % i), 40)
        if preset.get("id") == "character_design":
            img["role"] = "main_visual"
        elif preset.get("id") == "character_photography" and i == 1:
            img["role"] = "identity_reference"
            img["title"] = _clean(raw.get("title") or "人物身份基准图", 40)
        if preset.get("id") == "character_photography":
            story = storyboard[i - 1] if i <= len(storyboard) else PHOTO_STORYBOARD_24[i - 1]
            img["focalLength"] = str(raw.get("focalLength") or story["lens"])
            img["shotLanguage"] = str(raw.get("shotLanguage") or story["lang"])
            img["compositionType"] = str(raw.get("compositionType") or story["comp"])
            img["negativeSpace"] = str(raw.get("negativeSpace") or story["space"])
            img["lensResult"] = str(raw.get("lensResult") or story.get("lensResult") or "")
            img["surrealEvent"] = str(raw.get("surrealEvent") or story.get("surrealEvent") or "")
            img["lightTone"] = str(raw.get("lightTone") or "")
            img["lookIndex"] = raw.get("lookIndex") if raw.get("lookIndex") is not None else 1
            img["personScale"] = str(raw.get("personScale") or "")
            if not _clean(img.get("subject")):
                img["subject"] = str(story.get("scene") or "")
            if not _clean(img.get("environment")):
                img["environment"] = str(story.get("scene") or "")
            if not _clean(img.get("action")):
                img["action"] = str(story["action"])
            if not _clean(img.get("shotSize")):
                img["shotSize"] = str(story["shot"])
            if i == 1:
                for key, value in PHOTO_IDENTITY_BASE.items():
                    if key == "actionHint":
                        if not _clean(img.get("action")):
                            img["action"] = value
                    elif key == "aspectRatio":
                        if ui.get("aspectRatio") == "auto":
                            img[key] = value
                    elif not _clean(img.get(key)):
                        img[key] = value
                img["role"] = "identity_reference"
            if ui.get("aspectRatio") == "auto":
                img["aspectRatio"] = str(story["aspect"])
            img["surrealFlag"] = int(story.get("surreal") or 0)
        for extra in preset.get("requiredPlanFields", []):
            if extra not in img:
                img[extra] = str(raw.get(extra) or "")
        img["sampleScore"] = album_sample_score(preset, img)
        img["aspectRatio"] = album_resolve_aspect(project, img)
        images.append(img)
    plan = {
        "albumName": _clean(candidate.get("albumName") or candidate.get("title")
                            or project.get("name") or "未命名画册", 48),
        "lockedFacts": list(project.get("lockedFacts") or []),
        "seriesBible": series_bible,
        "sequenceDesign": sequence_design,
        "diversityMatrix": {
            "shotSizes": sorted({x.get("shotSize") for x in images if x.get("shotSize")}),
            "cameraAngles": sorted({x.get("cameraAngle") for x in images if x.get("cameraAngle")}),
            "compositions": sorted({x.get("composition") for x in images if x.get("composition")}),
            "actions": sorted({x.get("action") for x in images if x.get("action")}),
            "lightingChanges": sorted({x.get("lighting") for x in images if x.get("lighting")}),
            "sceneChanges": sorted({x.get("environment") for x in images if x.get("environment")}),
        },
        "images": images,
    }
    return plan


def album_validate_plan(project, plan):
    errors = []
    if not isinstance(plan, dict):
        return ["规划不是JSON对象"]
    preset = album_get_preset(project.get("preset") or "")
    count = album_project_count(project)
    images = plan.get("images") or []
    if len(images) != count:
        errors.append("图片数量应为%d，实际%d" % (count, len(images)))
    for i, img in enumerate(images, 1):
        idx = img.get("index")
        if idx != i:
            errors.append("图片索引不连续：第%d项索引为%s" % (i, idx))
        if not _clean(img.get("title")):
            errors.append("第%d张缺少标题" % i)
        if not _clean(img.get("subject")):
            errors.append("第%d张缺少明确主体" % i)
        if not (_clean(img.get("shotSize")) or _clean(img.get("composition"))):
            errors.append("第%d张缺少景别或构图" % i)
        concrete = any(_clean(img.get(k)) for k in ("action", "environment", "lighting", "uniquePoint"))
        if not concrete:
            errors.append("第%d张只有抽象描述，缺少具体画面" % i)
    text = json.dumps(plan, ensure_ascii=False)
    image_text = json.dumps({"images": images}, ensure_ascii=False)
    for term in preset.get("forbiddenSubjects", []):
        if term and term in image_text:
            errors.append("规划出现预设禁止内容“%s”" % term)
    for term in project.get("userInput", {}).get("mustExclude", []):
        if term and term in image_text:
            errors.append("违反用户禁止项“%s”" % term)
    for fact in project.get("lockedFacts", []):
        value = _clean(fact.get("value"))
        if value and value not in text:
            errors.append("锁定事实“%s”在规划中消失或改变" % value)
    for item in project.get("userInput", {}).get("mustInclude", []):
        if item and item not in text:
            errors.append("必须出现内容“%s”未安排" % item)
    if preset.get("kernel") == "character":
        bible = plan.get("seriesBible") or {}
        for key in ("subjectLock", "wardrobeLock", "paletteLock", "cameraLock", "styleLock"):
            if not _clean(bible.get(key)):
                errors.append("系列视觉说明书缺少%s" % key)
        for img in images:
            if not _clean(img.get("wardrobe")) and not _clean(bible.get("wardrobeLock")):
                errors.append("第%d张缺少服装描述" % img.get("index"))
    seen = {}
    for img in images:
        sig = "|".join(_clean(img.get(k)) for k in ("action", "shotSize", "cameraAngle", "environment"))
        if sig and sig in seen:
            errors.append("第%d张与第%d张动作、景别、场景完全重复" % (seen[sig], img.get("index")))
        seen[sig] = img.get("index")
    return errors


def album_validate_diversity(project, plan):
    errors = []
    if not isinstance(plan, dict):
        return ["规划不是JSON对象"]
    preset = album_get_preset(project.get("preset") or "")
    images = plan.get("images") or []
    kernel = preset.get("kernel")
    preset_id = preset.get("id")
    if preset_id == "character_design":
        text = json.dumps(images, ensure_ascii=False)
        for label, terms in (
                ("正面全身", ("正面",)), ("侧面全身", ("侧面",)),
                ("背面全身", ("背面",)), ("面部放大特写", ("面部", "脸部", "头像"))):
            if not any(term in text for term in terms):
                errors.append("角色设定图缺少%s" % label)
        return errors
    if preset_id == "world_character_album":
        identities = [str(x.get("personIdentity") or x.get("subject") or "").strip()
                      for x in images]
        if len(set(identities)) < len(images):
            errors.append("世界观人物画册必须每张都是不同身份的人物")
        roles = [str(x.get("groupRole") or "").strip() for x in images]
        if len({x for x in roles if x}) < min(4, len(images)):
            errors.append("世界观人物画册的职业或身份变化不足")
        if not all(_clean(x.get("environment")) for x in images):
            errors.append("世界观人物每张都必须展示场景")
        if not all(("全身" in str(x.get("shotSize") or "")
                    or "中全景" in str(x.get("shotSize") or "")) for x in images):
            errors.append("世界观人物每张都应是全身或中全景，人物居中做符合身份的事")
        return errors
    if preset_id == "scene_concept":
        text = json.dumps(images, ensure_ascii=False)
        for term in preset.get("forbiddenSubjects", []):
            if term and term in text:
                errors.append("场景概念图出现禁止内容“%s”" % term)
        if len(images) > 1:
            comps = {str(x.get("composition") or "") for x in images}
            if len(comps) < min(3, len(images)):
                errors.append("场景概念图的远观构图变化不足")
        return errors
    if kernel == "character":
        shots = {str(x.get("shotSize") or "") for x in images}
        angles = {str(x.get("cameraAngle") or "") for x in images}
        actions = {str(x.get("action") or "") for x in images}
        comps = {str(x.get("composition") or "") for x in images}
        if len(shots) < 3:
            errors.append("人物类景别至少3种")
        if len(angles) < 3:
            errors.append("人物类机位至少3种")
        if len(actions) < 4:
            errors.append("人物类动作至少4种")
        if len(comps) < 3:
            errors.append("人物类构图至少3种")
        roles = {str(x.get("role") or "") for x in images}
        if "main_visual" not in roles:
            errors.append("人物类缺少主视觉")
        if not any(("近景" in s or "特写" in s) for s in shots):
            errors.append("人物类至少1张近景")
        if not any(("全身" in s or "中全景" in s) for s in shots):
            errors.append("人物类至少1张全身或中全身")
        if not any(("远景" in s or "大景" in s) for s in shots):
            errors.append("人物类至少1张场景占比较大的远景")
        if all(("看镜头" in str(x.get("expression") or "") or "直视镜头" in str(x.get("bodyPose") or ""))
               for x in images):
            errors.append("人物类不能全部看镜头")
        if all("站立" in str(x.get("bodyPose") or "") for x in images):
            errors.append("人物类不能全部站立")
        for a, b in zip(images, images[1:]):
            if (_clean(a.get("shotSize")) and _clean(a.get("shotSize")) == _clean(b.get("shotSize"))
                    and _clean(a.get("cameraAngle")) == _clean(b.get("cameraAngle"))):
                errors.append("第%d张和第%d张连续完全相同景别和机位" % (a.get("index"), b.get("index")))
    elif kernel == "narrative":
        roles = {str(x.get("role") or "") for x in images}
        for need in ("opening", "main_visual", "ending"):
            if need not in roles:
                errors.append("叙事类缺少%s职责" % need)
        beats = [str(x.get("storyBeat") or x.get("title") or "").strip() for x in images]
        if len(set(beats)) < len(images):
            errors.append("叙事类主要事件重复")
        if all(("高潮" in str(x.get("storyBeat") or "") or x.get("role") == "main_visual")
               for x in images):
            errors.append("叙事类核心高潮不能出现在每张图片")
    elif kernel == "setting":
        all_text = " ".join(json.dumps(x, ensure_ascii=False) for x in images)
        need = [("整体外观", "外观"), ("入口或过渡空间", "入口"), ("内部空间", "内部"),
                ("结构或材质细节", "结构"), ("空间与环境关系", "环境"), ("主视觉", "主视觉")]
        for label, key in need:
            if key not in all_text and key not in {str(x.get("role") or "") for x in images}:
                errors.append("设定与空间类缺少%s" % label)
        image_text = json.dumps({"images": images}, ensure_ascii=False)
        for term in preset.get("forbiddenSubjects", []):
            if term and term in image_text:
                errors.append("设定与空间类出现禁止内容“%s”" % term)
    elif kernel == "object":
        modes = [str(x.get("displayMode") or "") for x in images]
        need = [("完整主体", ("完整", "全身", "整体", "陈列")),
                ("侧面或结构", ("侧面", "结构")),
                ("局部细节", ("局部", "细节", "特写")),
                ("材质展示", ("材质", "表面", "磨损")),
                ("功能或使用状态", ("功能", "使用", "展开", "状态")),
                ("主视觉", ("主视觉", "main_visual"))]
        for label, keys in need:
            if not any(any(k in m for k in keys) for m in modes):
                errors.append("对象图鉴类缺少%s" % label)
        image_text = json.dumps({"images": images}, ensure_ascii=False)
        for term in preset.get("forbiddenSubjects", []):
            if term and term in image_text:
                errors.append("对象图鉴类出现禁止内容“%s”" % term)
    return errors


def album_validate_director_kernel(project, plan):
    """写真集质量检查器：固定24镜头骨架在8张以上时强制执行。"""
    errors = []
    preset = album_get_preset(project.get("preset") or "")
    if preset.get("id") != "character_photography":
        return errors
    images = plan.get("images") or []
    count = len(images)
    if count < 8:
        return errors
    aspects = [str(x.get("aspectRatio") or "") for x in images]
    if aspects.count("16:9") < 2:
        errors.append("写真集需要至少2张16:9环境或高潮镜头")
    if aspects.count("4:5") < max(3, count // 3):
        errors.append("写真集4:5肖像画幅不足")
    if aspects.count("2:3") < 2:
        errors.append("写真集2:3全身画幅不足")
    lenses = [str(x.get("focalLength") or "") for x in images]
    lens_text = " ".join(lenses)
    for need in ("24mm", "35mm", "50mm", "85mm"):
        if need not in lens_text:
            errors.append("写真集缺少%s焦段" % need)
    if count >= 12 and "135mm" not in lens_text:
        errors.append("写真集缺少135mm远距离观察焦段")
    if count >= 12 and "90mm" not in lens_text:
        errors.append("写真集缺少90mm特写细节焦段")
    langs = [str(x.get("shotLanguage") or "") for x in images]
    lang_text = " ".join(langs)
    for need in ("establishing", "hero", "detail"):
        if need not in lang_text:
            errors.append("写真集缺少%s镜头语言" % need)
    comps = [str(x.get("compositionType") or "") for x in images]
    non_central = sum(1 for c in comps if "central" not in c)
    if non_central < max(3, count // 4):
        errors.append("写真集非中央构图数量不足（至少%d张）" % max(3, count // 4))
    tiny = sum(1 for x in images
               if str(x.get("shotLanguage") or "") == "establishing"
               or "远景" in str(x.get("shotSize") or "")
               or "全景" in str(x.get("shotSize") or "")
               or "30%" in str(x.get("personScale") or ""))
    if tiny < max(2, count // 8):
        errors.append("写真集环境大景/人物占比较小的镜头不足")
    tones = {str(x.get("lightTone") or "") for x in images}
    tones.discard("")
    if len(tones) > 3:
        errors.append("写真集光线种类超过3套主光")
    looks = {str(x.get("lookIndex") or "") for x in images}
    looks.discard("")
    if len(looks) > 3:
        errors.append("写真集服装Look超过3套")
    expressions = [str(x.get("expression") or "") for x in images]
    looking = sum(1 for e in expressions
                  if "看镜头" in e or "直视镜头" in e or "正对镜头" in e)
    if looking > max(2, count // 2):
        errors.append("写真集看镜头的图片过多，应少于一半")
    poses = [str(x.get("bodyPose") or "") for x in images]
    standing = sum(1 for p in poses if "站立" in p)
    if standing > max(2, count // 2):
        errors.append("写真集站立摆拍过多")
    return errors


def album_build_planner_context(project):
    preset = album_get_preset(project.get("preset") or "")
    ui = project.get("userInput", {})
    count = album_project_count(project)
    facts = project.get("lockedFacts") or []
    fact_text = json.dumps(facts, ensure_ascii=False) if facts else "（暂无）"
    preset_text = json.dumps({
        "id": preset["id"], "name": preset["name"], "kernel": preset["kernel"],
        "allowedSubjects": preset["allowedSubjects"],
        "forbiddenSubjects": preset["forbiddenSubjects"],
        "requiredPlanFields": preset["requiredPlanFields"],
        "sequenceRules": preset["sequenceRules"],
        "diversityRules": sorted(preset["diversityRules"]),
        "sampleRule": preset["sampleRule"],
    }, ensure_ascii=False)
    extra_fields = "\n".join("'%s':'字符串，本张该字段的可见内容'" % x
                             for x in preset["requiredPlanFields"])
    system = """你是高质量AI画册总导演和摄影规划师。

你的任务不是生成若干互不相关的图片，而是规划一本风格统一、内容有变化、阅读有节奏的完整画册。

用户原始输入属于不可修改事实。用户明确写出的主体、年龄、性别、外貌、服装、地点、时代、动作、颜色、材质、数量、氛围和禁止内容不能删除、改写、替换或冲突，只能补充必要的视觉细节。

你必须先建立整套系列视觉说明书（seriesBible），再安排整套画面节奏（sequenceDesign），最后逐张规划（images）。

整套必须做到：
1. 该统一的内容保持统一。
2. 该变化的内容必须真正变化。
3. 每张图片承担不同职责。
4. 不得仅用轻微换角度伪装成不同图片。
5. 每张只保留一个主要视觉目标。
6. 画面必须可以直接转化为Krea2提示词。
7. 不要写抽象文学句子，要写能够实际看到的主体、动作、场景、光线和构图。
8. 严格遵守当前画册预设的允许内容、禁止内容和结构要求。
9. 输出数量必须与用户选择完全一致：%d张。
10. 画风与美学体系是最高优先级：先确定唯一主画风并始终放在最前，再安排题材、主体、镜头、光线与构图；不得混入多个互相冲突的画风。
11. 只输出合法JSON，不要解释，不要代码块标记。

当前预设：
%s

用户不可变事实：
%s

输出JSON结构：
{
  "albumName": "12字以内画册名",
  "seriesBible": {
    "subjectLock": "整套主体固定特征",
    "worldLock": "时代、地点、世界规则",
    "wardrobeLock": "服装体系和变化规则",
    "paletteLock": "主色与辅助色",
    "materialLock": "核心材质和表面特征",
    "cameraLock": "统一摄影语言",
    "styleLock": "统一画风和真实程度",
    "forbiddenChanges": ["禁止改变年龄", "禁止改变发色"]
  },
  "sequenceDesign": {
    "overallGoal": "", "opening": "", "development": "", "mainVisual": "", "variation": "", "ending": ""
  },
  "images": [
    {
      "index": 1,
      "title": "本张短标题",
      "role": "opening|environment|action|detail|main_visual|ending",
      "subject": "本张唯一主体及准确可见特征",
      "visibleAnchors": ["本张可见的固定人物锚点，非人物类型可省略"],
      "wardrobe": "本张服装或外观",
      "action": "一个动作、姿态或展示状态",
      "bodyPose": "身体姿势，人物类填写",
      "expression": "表情或视线方向，人物类填写",
      "shotSize": "远景|全景|中全景|中景|近景|特写",
      "cameraAngle": "平视|低机位|俯视|斜侧|侧面|背面",
      "lensFeeling": "镜头感，如35mm电影镜头",
      "environment": "本张必要场景",
      "lighting": "主光源、方向、明暗关系",
      "color": "2—4种主色及冷暖关系",
      "material": "关键材质与表面状态",
      "composition": "构图方式",
      "uniquePoint": "本张与其他张不同的唯一亮点",
      "caption": "一句话画面说明"
      %s
    }
  ]
}

请先完整理解用户原文，再做减法：每张只保留一个主体、一个清楚姿态或状态、一个必要环境。""" % (count, preset_text, fact_text,
        ((",\n      " + extra_fields) if extra_fields else ""))
    mode_directives = {
        "character_design": (
            "只规划1张完整角色设定板。画面只包含两部分：同一角色的正面全身、严格90度侧面全身、"
            "严格背面全身三视图和一个正面面部特写：三个全身像从左到右等大并排，朝向分别是正前、"
            "左侧面、正后，各只有一个，绝不允许出现第二个背面或第四个全身像；右侧面部特写只截取"
            "额头到下巴，眼睛、鼻子、嘴唇占满画面，不含脖子以下。"
            "不要服装材质道具局部特写、不要场景背景。禁止把三视图画成三个不同人物。"
        ),
        "character_photography": (
            "这是固定24镜头摄影母版下的国际人物写真集。先满足真实摄影体系，再谈题材："
            "真实摄影机、真实镜头、明确机位、人物真的站在空间里、环境有真实尺度、光线有明确来源、"
            "材质对光线真实反应、景深符合焦段、人物与背景处于同一个曝光世界。"
            "摄影顺序固定为：摄影体系→写真集叙事→人物→空间→光→镜头→题材世界观→超现实元素→后期。"
            "第1张必须是身份基准图：清楚呈现同一人物的脸、身材比例、发型和基础服装，role写identity_reference。"
            "后续每张锁死同一骨相、五官距离、身材与辨识锚点，只改变服装、姿势、机位和环境；"
            "每张必须能明显看出是同一个人。整套对标摄影大赛获奖作品：真实光影、镜头语言、艺术构图、"
            "胶片质感与材质细节；允许眼睛、唇、手、锁骨、服装局部特写，镜头可以自由切换，"
            "即使只露一只眼睛也要能认出是同一个人。"
            "每张必须填写：focalLength（24mm/35mm/50mm/85mm/135mm/90mm）、"
            "shotLanguage（establishing/observational/transitional/hero/detail）、"
            "compositionType（central/rule_of_thirds/edge/negative_space/frame_in_frame/foreground/"
            "low_angle/high_angle/compression/wide_perspective/tiny_subject）、"
            "negativeSpace（视线/运动方向留白）、lightTone（A/B/C，整套最多3种）、"
            "lookIndex（1-3，整套最多3套服装）。"
            "整套构图至少覆盖中央、三分、边缘、负空间、框中框、前景遮挡、低机位、高机位、"
            "长焦压缩、广角透视、人物极小环境极大；非中央构图至少占四分之一；"
            "至少4张人物占画面不足30%（24张时）；不能全部看镜头、不能全部站立；"
            "135mm用于远距离观察，90mm用于材质细节；后期统一轻胶片、低饱和、自然肤色、不重磨皮。"
        ),
        "world_character_album": (
            "每张是同一世界里的不同居民：职业、姓名/身份、年龄、脸型、体型、服装轮廓必须明显不同；"
            "但时代、文明技术、建筑语汇、材料、纹样、色谱和光线规则完全统一。不要复用同一张脸。"
            "默认16:9画幅：每张画面以环境场景为主要背景，人物全身或中全景站在画面中央，"
            "正在做符合其职业身份的事情，场景与人物关系清楚。"
        ),
        "scene_concept": (
            "场景和建筑是唯一主体。优先远观大场景与清楚空间层级，严格表现用户填写的尺度；"
            "只有用户允许尺度人物时，才可在远处放一个极小、无脸、背对镜头的剪影，人物不得成为主体。"
        ),
    }
    directive = mode_directives.get(preset.get("id"))
    if directive:
        system += "\n\n当前工作流强制要求：\n" + directive
    user = "用户原始主题：\n%s\n\nAI扩写草案（可被用户修改，不能覆盖原始事实）：\n%s\n\n固定人物外观：\n%s\n\n固定世界观：\n%s\n\n风格方向：%s\n空间尺度：%s\n建筑语言：%s\n\n高级要求：\n%s\n\n必须出现：%s\n禁止出现：%s\n\n视觉风格：%s\n画幅：%s\n图片数量：%d张" % (
        ui.get("originalTheme") or "", ui.get("expandedBrief") or "（未单独扩写）",
        ui.get("fixedCharacter") or "", ui.get("fixedWorld") or "",
        ui.get("albumDirection") or "AI自动", ui.get("sceneScale") or "按主题判断",
        ui.get("architectureStyle") or "按主题判断",
        ui.get("advancedRequirements") or "",
        "；".join(ui.get("mustInclude") or []) or "（无）",
        "；".join(ui.get("mustExclude") or []) or "（无）",
        ui.get("visualStyle") or "按预设默认", ui.get("aspectRatio") or "16:9", count)
    num_predict = min(12000, 900 + count * 400)
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}], num_predict


def album_attach_prompts(project, plan):
    merged = dict(project)
    merged["seriesBible"] = plan.get("seriesBible") or {}
    for img in plan.get("images", []):
        img["autoPrompt"] = album_build_krea2_prompt(merged, img)
        img["activePrompt"] = img["autoPrompt"]
        img["promptSource"] = "auto"
        img["status"] = "planned"
        img["file"] = ""
        img["url"] = ""
        img["error"] = ""
        img["history"] = []
        img["retryCount"] = 0
    return plan


def album_plan_series(project, attempts=3):
    from app import chat_stream, _extract_json
    messages, num_predict = album_build_planner_context(project)
    plan = None
    feedback = ""
    for attempt in range(attempts):
        attempt_messages = list(messages)
        if feedback:
            attempt_messages.append({
                "role": "user",
                "content": "上一版规划被校验拒绝。请只修正以下问题并重新输出完整JSON：\n" + feedback,
            })
        raw = chat_stream(attempt_messages, 0.5 if not attempt else 0.3, None, num_predict).strip()
        try:
            candidate = _extract_json(raw)
            plan = album_normalize_plan(project, candidate)
        except Exception as exc:
            feedback = "规划不是合法JSON或字段不完整：%s" % exc
            continue
        errors = (album_validate_plan(project, plan)
                  + album_validate_diversity(project, plan)
                  + album_validate_director_kernel(project, plan))
        if not errors:
            plan = album_attach_prompts(project, plan)
            return plan
        feedback = "；".join(errors)
    raise RuntimeError("画册规划连续%d次校验未通过：%s" % (attempts, feedback))


def album_select_sample(project, plan):
    images = plan.get("images") or project.get("images") or []
    if not images:
        return 1
    best = max(images, key=lambda x: (int(x.get("sampleScore") or 0),
                                      -abs(int(x.get("index") or 1) - (len(images) // 2 + 1))))
    return int(best.get("index") or 1)


# ================== 5. Krea2 提示词构建器 ==================

ALBUM_ANCHOR_ORDER = ("age", "body", "face", "eyes", "hair", "mark", "gender", "height")


def album_character_anchors(project, image_plan):
    facts = {}
    for f in project.get("lockedFacts") or []:
        value = _clean(f.get("value"), 40)
        if value:
            facts.setdefault(str(f.get("type") or "fact"), value)
    va = image_plan.get("visibleAnchors") or []
    extra = []
    if isinstance(va, dict):
        extra = [str(v) for v in va.values() if str(v).strip()]
    elif isinstance(va, list):
        extra = [str(v) for v in va if str(v).strip()]
    anchors = []

    def add_anchor(value):
        value = _clean(value, 40)
        if not value:
            return
        for i, existing in enumerate(anchors):
            if value == existing:
                return
            if value in existing:
                return
            if existing in value:
                anchors[i] = value
                return
        anchors.append(value)

    for key in ALBUM_ANCHOR_ORDER:
        if facts.get(key):
            add_anchor(facts[key])
    for v in extra:
        if len(anchors) >= 6:
            break
        add_anchor(v)
    if not anchors and facts.get("age"):
        anchors.append(facts["age"])
    return anchors[:6]


ALBUM_PROTECTED_TAGS = {
    "user", "subject", "identity", "world", "action", "scene", "camera",
    "aspect", "style", "wardrobe", "constraint",
}


def _album_trim_prompt(parts, kernel, limit=None):
    max_len = limit or (520 if kernel == "character" else (
        380 if kernel in ("narrative", "setting") else 280))
    budget = max_len - max(0, len(parts) - 1)
    deduped = []
    for tag, text in parts:
        text = _clean(text)
        if text and not _album_dup([t for _, t in deduped], text):
            deduped.append((tag, text))
    parts = deduped

    def total():
        return sum(len(t) for _, t in parts)

    def finalize():
        result = "，".join(t for _, t in parts)
        segs = []
        seen = set()
        for s in result.split("，"):
            s = s.strip()
            if s and s not in seen:
                seen.add(s)
                segs.append(s)
        result = "，".join(segs)
        if len(result) > max_len - 1:
            result = result[:max_len - 1]
            cut = result.rfind("，")
            if cut > max_len * 0.55:
                result = result[:cut]
            result = result.rstrip("，；。")
        return result + "。"

    if total() <= budget:
        return finalize()
    for i in reversed([i for i, (tag, _) in enumerate(parts) if tag not in ALBUM_PROTECTED_TAGS]):
        if total() <= budget:
            break
        del parts[i]
    if total() > budget:
        for tag in ("finish", "rule", "light", "comp"):
            if total() <= budget:
                break
            idx = next((i for i, (t, _) in enumerate(parts) if t == tag), None)
            if idx is not None:
                del parts[idx]
    return finalize()


def album_style_for_preset(style, preset_id):
    """复用漫画画风时，场景图自动剔除会诱发大脸/人体的专用句段。"""
    style = _clean(style)
    if preset_id != "scene_concept" or not style:
        return style
    person_terms = (
        "面部", "五官", "皮肤", "毛孔", "绒毛", "发丝", "人体", "手部",
        "身材", "模特", "肖像", "人像",
    )
    segments = [x.strip() for x in re.split(r"[；;。]", style) if x.strip()]
    kept = [x for x in segments if not any(term in x for term in person_terms)]
    base = "；".join(kept)
    return _clean(base or "高预算影视场景概念美术，真实材质与自然大气透视", 240)


def _album_photo_branch(image_plan):
    """写真提示词四分支：person / far_person / no_person / material。"""
    lens = str(image_plan.get("focalLength") or "")
    lang = str(image_plan.get("shotLanguage") or "")
    stype = str(image_plan.get("storyType") or image_plan.get("title") or "")
    scene = str(image_plan.get("scene") or image_plan.get("environment") or "")
    shot = str(image_plan.get("shotSize") or "")
    if lens == "90mm":
        return "material"
    if "画面里没有人" in scene or "没有人" in scene:
        return "no_person"
    if "空镜" in stype and "人物" not in stype:
        return "no_person"
    if lens == "135mm" or (lang == "establishing" and shot):
        return "far_person"
    return "person"


def _photo_short_identity(project):
    ui = project.get("userInput", {})
    bible = project.get("seriesBible", {}) or {}
    text = _clean(ui.get("fixedCharacter") or bible.get("subjectLock"), 260)
    short = text[:56]
    wardrobe = _clean(ui.get("fixedWardrobe") or bible.get("wardrobeLock"), 40)
    if wardrobe:
        short += "，服装：" + wardrobe
    return short


def _album_build_photo_prompt(project, image_plan):
    """写真四分支构建器：
    A 人物主镜头：完整身份锁；B 远人物镜头：简化身份锁；
    C 无人环境镜头：完全不加载人物；D 材质Macro镜头：只加载材质。"""
    preset = album_get_preset(project.get("preset") or "")
    ui = project.get("userInput", {})
    bible = project.get("seriesBible", {}) or {}
    branch = _album_photo_branch(image_plan)
    parts = []

    style = _clean(ui.get("visualStyle") or bible.get("styleLock")
                   or "真实电影摄影质感，轻胶片，低饱和，自然肤色", 220)
    if style:
        parts.append(("style", style))
    finish = PHOTO_DIRECTOR_POST
    if finish:
        parts.append(("finish", finish))

    scene = _clean(image_plan.get("scene") or image_plan.get("environment")
                   or image_plan.get("subject"), 220)
    lens = str(image_plan.get("focalLength") or "50mm")
    lens_result = _clean(image_plan.get("lensResult"), 120)
    dof = PHOTO_DOF.get(lens, "中等景深")
    aspect = str(image_plan.get("aspectRatio") or ui.get("aspectRatio") or "16:9")
    comp = _clean(image_plan.get("composition"), 70)
    negative_space = _clean(image_plan.get("negativeSpace"), 60)

    if branch == "no_person":
        # C 分支：完全不注入人物身份锁与服装
        if scene:
            parts.append(("scene", scene))
        light_color = _clean("，".join(x for x in (
            image_plan.get("lighting"), image_plan.get("color")) if str(x).strip()), 110)
        if light_color:
            parts.append(("light", light_color))
        camera = _clean("，".join(x for x in (
            lens_result or lens, dof) if x), 160)
        if camera:
            parts.append(("camera", camera))
        comp_parts = _clean("，".join(x for x in (
            comp, negative_space, "%s画幅" % aspect) if x), 120)
        if comp_parts:
            parts.append(("aspect", comp_parts))
        parts.append(("constraint", "画面中没有任何人物，禁止人物、人脸、人体、剪影、背影"))
        parts.append(("constraint", "画面内无摄影机/拍摄设备，无屏幕、显示器，无文字、标签、徽章、标牌"))
    elif branch == "material":
        # D 分支：只加载材质与微距摄影
        if scene:
            parts.append(("scene", scene))
        material = _clean(image_plan.get("material") or bible.get("materialLock"), 90)
        if material:
            parts.append(("material", material))
        light_color = _clean("，".join(x for x in (
            image_plan.get("lighting"), image_plan.get("color")) if str(x).strip()), 100)
        if light_color:
            parts.append(("light", light_color))
        camera = _clean("，".join(x for x in (
            lens_result or ("90mm微距镜头"), dof) if x), 150)
        if camera:
            parts.append(("camera", camera))
        comp_parts = _clean("，".join(x for x in (
            comp, negative_space, "%s画幅" % aspect) if x), 100)
        if comp_parts:
            parts.append(("aspect", comp_parts))
        parts.append(("constraint", "画面里只有材质本身，没有人、没有脸、没有手，局部占画面70%以上"))
        parts.append(("constraint", "画面内无摄影机/拍摄设备，无文字、标签、徽章、标牌"))
    elif branch == "far_person":
        # B 分支：简化身份锁，人物在远处很小
        identity = _photo_short_identity(project)
        if identity:
            parts.append(("identity", "人物身份（简化）：%s" % identity))
        if scene:
            parts.append(("scene", scene))
        wardrobe = _clean(image_plan.get("wardrobe") or bible.get("wardrobeLock"), 50)
        if wardrobe:
            parts.append(("wardrobe", wardrobe))
        light_color = _clean("，".join(x for x in (
            image_plan.get("lighting"), image_plan.get("color")) if str(x).strip()), 100)
        if light_color:
            parts.append(("light", light_color))
        camera = _clean("，".join(x for x in (
            lens_result or lens, dof) if x), 180)
        if camera:
            parts.append(("camera", camera))
        comp_parts = _clean("，".join(x for x in (
            comp, negative_space, "%s画幅" % aspect) if x), 110)
        if comp_parts:
            parts.append(("aspect", comp_parts))
        parts.append(("constraint", "人物在画面深处且很小，高度约占画面10%，完整全身，禁止近景、半身、特写，禁止人物占画面超过20%"))
        parts.append(("constraint", "画面中只能有主角一个人，禁止第二人物、复制人物、旁观者"))
        parts.append(("constraint", "画面内无摄影机/拍摄设备，无文字、标签、徽章、标牌"))
    else:
        # A 分支：完整人物身份锁
        identity = _clean(ui.get("fixedCharacter") or bible.get("subjectLock")
                          or image_plan.get("subject"), 260)
        if identity:
            parts.append(("identity", "固定人物身份锁：%s" % identity))
        if scene:
            parts.append(("scene", scene))
        wardrobe = _clean(image_plan.get("wardrobe") or bible.get("wardrobeLock"), 90)
        if wardrobe:
            parts.append(("wardrobe", wardrobe))
        action_parts = [image_plan.get("action"), image_plan.get("bodyPose"),
                        image_plan.get("expression")]
        action = _clean("，".join(str(x) for x in action_parts
                                  if str(x).strip()
                                  and str(x).strip().lower() not in
                                  {"无", "无动作", "不适用", "none", "n/a"}), 130)
        if action:
            parts.append(("action", action))
        material = _clean(image_plan.get("material") or bible.get("materialLock"), 80)
        if material:
            parts.append(("material", material))
        light_color = _clean("，".join(x for x in (
            image_plan.get("lighting"), image_plan.get("color")) if str(x).strip()), 110)
        if light_color:
            parts.append(("light", light_color))
        camera = _clean("，".join(x for x in (
            lens_result or lens, dof) if x), 150)
        if camera:
            parts.append(("camera", camera))
        comp_parts = _clean("，".join(x for x in (
            comp, negative_space, "%s画幅" % aspect) if x), 120)
        if comp_parts:
            parts.append(("aspect", comp_parts))
        if image_plan.get("surrealFlag"):
            event = _clean(image_plan.get("surrealEvent") or "一个明确的超现实元素", 70)
            parts.append(("surreal", "超现实事件：%s。除该事件外，人物、建筑、光线、重力、材质全部保持真实物理" % event))
        parts.append(("constraint", "画面中只能出现主角一个人，禁止任何其他人：宇航员、旁观者、复制人物、镜像人物或背影人物"))
        parts.append(("constraint", "主角是成年女性，服装始终得体不暴露，裙摆不会掀起，动作端庄自然"))
        parts.append(("constraint", "画面内无摄影机/拍摄设备，无屏幕、显示屏、监视器，无文字、标签、徽章、标牌"))
        if lens == "50mm":
            parts.append(("constraint", "完整全身，从头到脚、鞋子、地面全部可见，绝不裁切"))

    user = _clean(ui.get("advancedRequirements"), 160)
    if user and not any(user in t for _, t in parts):
        parts.append(("user", user))
    return _album_trim_prompt(parts, "character")


def album_build_krea2_prompt(project, image_plan):
    preset = album_get_preset(project.get("preset") or "")
    kernel = preset.get("kernel")
    preset_id = preset.get("id")
    ui = project.get("userInput", {})
    bible = project.get("seriesBible", {}) or {}
    if preset_id == "character_design":
        identity = _clean(ui.get("fixedCharacter") or bible.get("subjectLock")
                          or image_plan.get("subject") or ui.get("expandedBrief")
                          or ui.get("originalTheme"), 300)
        style = _clean(ui.get("visualStyle") or bible.get("styleLock") or "高质量角色概念设计", 180)
        wardrobe = _clean(image_plan.get("wardrobe") or bible.get("wardrobeLock"), 130)
        detail = _clean(image_plan.get("designFocus") or image_plan.get("uniquePoint"), 130)
        material = _clean(image_plan.get("material") or bible.get("materialLock"), 90)
        parts = [
            ("style", style),
            ("identity", "唯一角色身份锁：%s" % identity),
            ("rule", "单张完整专业角色设定板，同一个角色仅以多个视角重复展示，不是多个不同人物"),
            ("rule", "画面必须且只能包含四个部分：三个全身视图（正面全身、严格左侧面全身、严格背面全身）和一个正面面部特写"),
            ("rule", "三个全身像从左到右等大并排，朝向分别是正前、左侧面、正后，各只有一个，绝不允许出现第二个背面或第四个全身像"),
            ("rule", "三幅全身像同一比例尺，头顶与脚底水平对齐，身高、头身比、骨相、发型、服装、配饰完全一致"),
            ("rule", "右侧单独一个面部特写：只截取额头到下巴，眼睛、鼻子、嘴唇占满画面，不含脖子以下"),
            ("rule", "不包含服装或道具的局部特写"),
            ("wardrobe", wardrobe),
            ("rule", "纯净浅灰中性背景，专业概念设计排版，无场景叙事，不裁切头顶手脚，轮廓锐利，细节清晰"),
            ("rule", "手脚结构准确，无多余肢体，无脸部漂移，无服装变化，无透视夸张，无文字乱码"),
            ("detail", detail),
            ("detail", material),
            ("aspect", "%s横版宽屏，高分辨率" % album_resolve_aspect(project, image_plan)),
            ("user", _clean(ui.get("advancedRequirements"), 180)),
        ]
        return _album_trim_prompt(parts, kernel, limit=760)
    if preset_id == "character_photography":
        return _album_build_photo_prompt(project, image_plan)
    parts = []
    style = album_style_for_preset(
        ui.get("visualStyle") or bible.get("styleLock") or "", preset_id)
    if style:
        parts.append(("style", style))
    finish = ALBUM_KERNEL_FINISH.get(kernel)
    if finish:
        parts.append(("finish", finish))
    if kernel == "character":
        if preset_id in ("world_character_album", "character_group"):
            person = _clean(image_plan.get("personIdentity") or image_plan.get("subject"), 180)
            if person:
                parts.append(("subject", "本张独立人物：" + person))
            world = _clean(ui.get("fixedWorld") or bible.get("worldLock")
                           or ui.get("expandedBrief") or ui.get("originalTheme"), 220)
            if world:
                parts.append(("world", "统一世界规则：" + world))
            parts.append(("rule", "本张人物拥有独立脸型、年龄、体型与职业轮廓，不复用其他画面的脸"))
        else:
            identity = _clean(ui.get("fixedCharacter") or bible.get("subjectLock")
                              or image_plan.get("subject"), 260)
            anchors = album_character_anchors(project, image_plan)
            anchor_text = "，".join(anchors)
            if anchor_text and not any(a in identity for a in anchors):
                identity_text = "；".join(x for x in (identity, anchor_text) if x)
            else:
                identity_text = identity
            if identity_text:
                parts.append(("identity", "固定人物身份锁：" + identity_text))
            parts.append(("rule", "整套始终是同一个人：骨相、五官距离、眼鼻唇形、发型发色、身高和身材比例不可改变"))
    else:
        subject = _clean(image_plan.get("subject"), 120)
        if subject:
            parts.append(("subject", subject))
    wardrobe = _clean(image_plan.get("wardrobe") or (
        bible.get("wardrobeLock") if kernel == "character" else ""), 90)
    material = _clean(image_plan.get("material") or bible.get("materialLock"), 90)
    negative_space = _clean(image_plan.get("negativeSpace"), 60)
    action_parts = [image_plan.get("action"), image_plan.get("bodyPose"), image_plan.get("expression")]
    empty_actions = {"无", "无动作", "不适用", "none", "n/a"}
    action = _clean("，".join(str(x) for x in action_parts
                              if str(x).strip()
                              and str(x).strip().lower() not in empty_actions), 130)
    env = _clean(image_plan.get("environment"), 130)
    light = _clean(image_plan.get("lighting"), 70)
    color = _clean(image_plan.get("color"), 60)
    light_color = _clean("，".join(x for x in (light, color) if x), 110)
    lens_result = _clean(image_plan.get("lensResult"), 110) if preset_id == "character_photography" else ""
    camera = _clean("，".join(str(x) for x in (
        lens_result or (image_plan.get("focalLength")
                        if preset_id == "character_photography" else ""),
        PHOTO_LANG_CN.get(str(image_plan.get("shotLanguage") or ""), "")
        if (preset_id == "character_photography" and not lens_result) else "",
        image_plan.get("shotSize"), image_plan.get("cameraAngle"),
        image_plan.get("lensFeeling")) if x and str(x).strip()), 120)
    # 摄影顺序：空间 → 光 → 镜头 → 服装 → 材质 → 动作 → 超现实 → 构图/留白/画幅
    if env:
        parts.append(("scene", env))
    if light_color:
        parts.append(("light", light_color))
    if camera:
        parts.append(("camera", camera))
    if wardrobe:
        parts.append(("wardrobe", wardrobe))
    if material:
        parts.append(("material", material))
    if action:
        parts.append(("action", action))
    if preset_id == "character_photography" and image_plan.get("surrealFlag"):
        event = _clean(image_plan.get("surrealEvent") or "一个明确的超现实元素", 70)
        parts.append(("surreal", "超现实事件：%s。除该事件外，人物、建筑、光线、重力、材质全部保持真实物理" % event))
    if preset_id == "character_photography":
        parts.append(("constraint", "画面中只能出现主角一个人，禁止任何其他人：宇航员、旁观者、复制人物、镜像人物或背影人物"))
        parts.append(("constraint", "画面内无摄影机/拍摄设备，无屏幕、显示屏、监视器，无文字、标签、徽章、标牌"))
        parts.append(("constraint", "主角是成年女性，服装始终得体不暴露，裙摆不会掀起，动作端庄自然"))
        if image_plan.get("focalLength") == "24mm":
            parts.append(("constraint", "人物占画面不超过15%，环境占绝对主导"))
        if image_plan.get("focalLength") == "50mm":
            parts.append(("constraint", "完整全身，从头到脚、鞋子、地面全部可见，绝不裁切"))
        if image_plan.get("focalLength") == "135mm":
            parts.append(("constraint", "人物必须在画面深处且很小，禁止近景、半身或人物占画面过大"))
        if image_plan.get("focalLength") == "90mm":
            parts.append(("constraint", "画面里只有材质本身，没有人、没有脸、没有手，局部占画面70%以上"))
    comp = _clean(image_plan.get("composition"), 70)
    aspect = str(image_plan.get("aspectRatio") or ui.get("aspectRatio") or "16:9")
    comp_parts = "，".join(x for x in (comp, negative_space, "%s画幅" % aspect) if x)
    if comp_parts:
        parts.append(("aspect", comp_parts))
    if preset_id == "scene_concept":
        world = _clean(ui.get("fixedWorld") or bible.get("worldLock")
                       or ui.get("expandedBrief") or ui.get("originalTheme"), 220)
        scale = _clean(ui.get("sceneScale") or image_plan.get("scaleReference"), 90)
        structure = _clean("，".join(x for x in (
            ui.get("architectureStyle"), image_plan.get("structuralFocus"))
            if x and str(x).strip()), 180)
        if world:
            parts.append(("world", world))
        if scale:
            parts.append(("subject", "空间尺度：" + scale))
        if structure:
            parts.append(("subject", "建筑与结构语言：" + structure))
        if ui.get("allowScaleFigure"):
            parts.append(("constraint", "仅在远处放一个占画面高度不超过2%的无脸背影剪影作为尺度参照"))
            parts.append(("constraint", "禁止任何前景人物、半身像、正面人物、人物特写、人脸和多人群像"))
            parts.append(("constraint", "no foreground person, no close-up portrait, only one tiny faceless back-view silhouette"))
        else:
            parts.append(("constraint", "画面完全无人，无人物剪影、无脸、无人体"))
        parts.append(("constraint", "建筑和场景占据绝对视觉主体，远观大构图，前中后景层次与尺度关系清楚"))
        parts.append(("constraint", "画面无标题、无标牌、无可读文字、无logo、无水印"))
        source_text = " ".join(str(x or "") for x in (
            ui.get("originalTheme"), ui.get("expandedBrief"), ui.get("sceneScale")))
        if "巨构" in source_text or "巨大" in source_text:
            parts.append(("constraint", "城市级超巨型体量与多层空间堆叠，不是村落、廊桥或普通低层建筑群"))
        if "未来" in source_text or "科幻" in source_text:
            parts.append(("constraint", "未来工程必须清楚可见：超尺度金属桁架、玻璃档案塔、垂直交通核心、悬浮轨道与能源结构，且与东方檐口秩序融合"))
        if "极繁" in source_text:
            parts.append(("constraint", "高密度极繁建筑细节，但承重、连接、基础与地形衔接必须真实可信"))
    def _rule_similar(a, b):
        sa = {x.strip() for x in a.split("，") if x.strip()}
        sb = {x.strip() for x in b.split("，") if x.strip()}
        if not sa or not sb:
            return False
        return len(sa & sb) / max(len(sa), len(sb)) >= 0.5

    for rule in preset.get("promptRules", []):
        rule_text = _clean(rule, 60)
        if rule_text and not any(_rule_similar(rule_text, t) for _, t in parts):
            parts.append(("rule", rule_text))
    adv = _clean(ui.get("advancedRequirements"), 140)
    if adv and not any(adv in text for _, text in parts):
        parts.append(("user", adv))
    return _album_trim_prompt(parts, kernel, limit=1000 if preset_id == "scene_concept" else None)


# ================== 6. 项目、生成、恢复、重出、封面与 PDF ==================

def album_create_project(user_input):
    ui = dict(user_input or {})
    preset_id = str(ui.get("preset") or "character_design").strip()
    preset = album_get_preset(preset_id)
    count = album_project_count(preset_id, ui.get("count"))
    default_aspect = {
        "character_design": "16:9",
        "character_photography": "auto",
        "world_character_album": "16:9",
        "scene_concept": "16:9",
    }.get(preset_id, "16:9")
    aspect = str(ui.get("aspectRatio") or default_aspect)
    if aspect != "auto" and aspect not in ALBUM_ASPECT_SIZES:
        aspect = default_aspect
    try:
        base_seed = int(ui.get("baseSeed") or 0)
    except (TypeError, ValueError):
        base_seed = 0
    name = _clean(ui.get("albumName") or ui.get("originalTheme"), 40) or "未命名画册"
    now = _now_iso()
    project = {
        "version": ALBUM_V3_VERSION,
        "id": hashlib.md5((preset_id + "\n" + name + "\n" + now).encode("utf-8")).hexdigest()[:12],
        "name": name,
        "preset": preset_id,
        "kernel": preset.get("kernel"),
        "status": "planning",
        "created": now,
        "updated": now,
        "userInput": {
            "albumName": name,
            "originalTheme": str(ui.get("originalTheme") or "").strip(),
            "expandedBrief": str(ui.get("expandedBrief") or "").strip(),
            "fixedCharacter": str(ui.get("fixedCharacter") or "").strip(),
            "fixedWardrobe": str(ui.get("fixedWardrobe") or "").strip(),
            "fixedWorld": str(ui.get("fixedWorld") or "").strip(),
            "albumDirection": str(ui.get("albumDirection") or "").strip(),
            "sceneScale": str(ui.get("sceneScale") or "").strip(),
            "architectureStyle": str(ui.get("architectureStyle") or "").strip(),
            "advancedRequirements": str(ui.get("advancedRequirements") or "").strip(),
            "mustInclude": [str(x).strip() for x in (ui.get("mustInclude") or []) if str(x).strip()],
            "mustExclude": [str(x).strip() for x in (ui.get("mustExclude") or []) if str(x).strip()],
            "count": count,
            "aspectRatio": aspect,
            "visualStyle": str(ui.get("visualStyle") or "").strip(),
            "stylePresetName": str(ui.get("stylePresetName") or "").strip(),
            "pdfTemplate": str(ui.get("pdfTemplate") or preset.get("pdfTemplate") or "cinematic"),
            "narrativeStrength": str(ui.get("narrativeStrength") or "medium"),
            "sceneVariation": str(ui.get("sceneVariation") or "medium"),
            "compositionVariation": str(ui.get("compositionVariation") or "medium"),
            "allowOutfitChange": bool(ui.get("allowOutfitChange", True)),
            "allowPeople": bool(ui.get("allowPeople", True)),
            "allowScaleFigure": bool(ui.get("allowScaleFigure", False)),
        },
        "lockedFacts": [],
        "seriesBible": {k: "" for k in SERIES_BIBLE_KEYS},
        "sequenceDesign": {
            "overallGoal": "", "opening": "", "development": "",
            "mainVisual": "", "variation": "", "ending": "",
        },
        "diversityMatrix": {
            "shotSizes": [], "cameraAngles": [], "compositions": [],
            "actions": [], "lightingChanges": [], "sceneChanges": [],
        },
        "images": [],
        "sample": {"imageIndex": 1, "confirmed": False, "promptEdited": False},
        "generation": {
            "completed": [], "pending": [], "failed": [], "currentIndex": None,
            "baseSeed": base_seed or random.randrange(1, 1900000000),
        },
        "pdf": {
            "template": preset.get("pdfTemplate") or "cinematic", "showTitle": True, "showCaption": True,
            "showPrompt": False, "pureImageVersion": False,
            "path": "", "coverPath": "", "coverUrl": "",
        },
        "dir": "",
    }
    _album_set_dir(project)
    return project


def album_save_project(project):
    from app import atomic_write_json, safe_name
    abs_dir = album_project_abs(project)
    os.makedirs(abs_dir, exist_ok=True)
    ui = project.get("userInput", {})
    original_parts = [
        ui.get("originalTheme"), ui.get("fixedCharacter"),
        ui.get("fixedWardrobe"), ui.get("fixedWorld"),
        ui.get("advancedRequirements"),
    ]
    original = "\n\n".join(str(x) for x in original_parts if str(x).strip())
    with open(os.path.join(abs_dir, "用户原始要求.txt"), "w", encoding="utf-8") as f:
        f.write(original or "（未填写）")
    with open(os.path.join(abs_dir, "AI扩写草案.txt"), "w", encoding="utf-8") as f:
        f.write(str(ui.get("expandedBrief") or "（未单独扩写）"))
    atomic_write_json(os.path.join(abs_dir, "album_project.json"), project)
    atomic_write_json(os.path.join(abs_dir, "系列视觉说明书.json"), project.get("seriesBible") or {})
    atomic_write_json(os.path.join(abs_dir, "整套规划.json"), {
        "albumName": project.get("name"),
        "seriesBible": project.get("seriesBible") or {},
        "sequenceDesign": project.get("sequenceDesign") or {},
        "images": project.get("images") or [],
    })
    prompt_dir = os.path.join(abs_dir, "提示词")
    os.makedirs(prompt_dir, exist_ok=True)
    for img in project.get("images", []):
        idx = int(img.get("index") or 0)
        if idx < 1:
            continue
        text = str(img.get("activePrompt") or img.get("autoPrompt") or "")
        with open(os.path.join(prompt_dir, "%02d.txt" % idx), "w", encoding="utf-8") as f:
            f.write(text)
    for sub in ("原图", "重出历史", "封面", "PDF"):
        os.makedirs(os.path.join(abs_dir, sub), exist_ok=True)
    project["updated"] = _now_iso()
    atomic_write_json(os.path.join(abs_dir, "album_project.json"), project)
    return project


def album_load_project(dir_rel):
    from app import COMIC_DIR, safe_name
    parts = [safe_name(x) for x in str(dir_rel or "").replace("\\", "/").split("/") if x]
    abs_dir = os.path.join(COMIC_DIR, *parts) if parts else ""
    if not abs_dir or not os.path.isdir(abs_dir):
        raise RuntimeError("画册项目目录不存在")
    project_path = os.path.join(abs_dir, "album_project.json")
    if os.path.exists(project_path):
        with open(project_path, encoding="utf-8") as f:
            project = json.load(f)
        project = album_migrate_project(project, dir_rel)
        album_ensure_local_urls(project)
        return project
    old_path = os.path.join(abs_dir, "画册方案.json")
    if os.path.exists(old_path):
        with open(old_path, encoding="utf-8") as f:
            old_data = json.load(f)
        project = album_migrate_project(old_data, dir_rel)
        album_ensure_local_urls(project)
        album_save_project(project)
        return project
    raise RuntimeError("该目录没有画册项目文件")


def album_ensure_local_urls(project):
    """为每张已完成图片补一个稳定的本地访问地址（/panel?p=...），
    ComfyUI 的临时输出链接失效后前端仍能显示原图。"""
    from urllib.parse import urlencode
    from app import COMIC_DIR
    abs_dir = album_project_abs(project)
    for img in project.get("images", []):
        f = str(img.get("file") or "")
        if not f or img.get("status") != "completed":
            continue
        p = os.path.join(abs_dir, "原图", f)
        if os.path.isfile(p):
            rel = os.path.relpath(p, COMIC_DIR).replace("\\", "/")
            img["archUrl"] = "/panel?" + urlencode({"p": rel})
    return project


def album_list_projects():
    from app import COMIC_DIR
    root = os.path.join(COMIC_DIR, "高质量画册")
    items = []
    if not os.path.isdir(root):
        return items
    for type_name in sorted(os.listdir(root)):
        type_dir = os.path.join(root, type_name)
        if not os.path.isdir(type_dir):
            continue
        for album_name in sorted(os.listdir(type_dir)):
            pj = os.path.join(type_dir, album_name, "album_project.json")
            old_pj = os.path.join(type_dir, album_name, "画册方案.json")
            try:
                if os.path.exists(pj):
                    with open(pj, encoding="utf-8") as f:
                        project = json.load(f)
                elif os.path.exists(old_pj):
                    with open(old_pj, encoding="utf-8") as f:
                        old_data = json.load(f)
                    rel = os.path.join("高质量画册", type_name, album_name).replace("\\", "/")
                    project = album_migrate_project(old_data, rel)
                else:
                    continue
                images = project.get("images") or []
                completed = sum(1 for x in images if x.get("status") == "completed")
                items.append({
                    "dir": project.get("dir", ""),
                    "name": project.get("name", album_name),
                    "preset": project.get("preset", ""),
                    "status": project.get("status", ""),
                    "updated": project.get("updated", ""),
                    "count": len(images),
                    "completed": completed,
                })
            except Exception:
                pass
    items.sort(key=lambda x: x.get("updated", ""), reverse=True)
    return items


def album_migrate_project(data, dir_rel=None):
    if isinstance(data, dict) and str(data.get("preset") or "") == "character_photography":
        from portrait_v1_core import migrate_photo_project
        project = migrate_photo_project(data)
        if dir_rel:
            project["dir"] = str(dir_rel).replace("\\", "/")
        return project
    if not isinstance(data, dict):
        raise RuntimeError("项目数据损坏，无法迁移")
    if data.get("version") in (3, ALBUM_V3_VERSION) and isinstance(data.get("images"), list):
        data["version"] = ALBUM_V3_VERSION
        if dir_rel:
            data["dir"] = str(dir_rel).replace("\\", "/")
        ui = data.setdefault("userInput", {})
        for key, default in (
                ("expandedBrief", ""), ("albumDirection", ""), ("sceneScale", ""),
                ("architectureStyle", ""), ("stylePresetName", ""),
                ("allowScaleFigure", False)):
            ui.setdefault(key, default)
        generation = data.setdefault("generation", {})
        generation.setdefault("baseSeed", random.randrange(1, 1900000000))
        generation.setdefault("completed", [])
        generation.setdefault("pending", [])
        generation.setdefault("failed", [])
        generation.setdefault("currentIndex", None)
        return data
    old = dict(data)
    mode = str(old.get("mode") or old.get("preset") or "cinematic_album")
    preset_id = ALBUM_LEGACY_MODE_MAP.get(mode, mode if mode in ALBUM_PRESETS else "cinematic_story")
    preset = album_get_preset(preset_id)
    old_items = old.get("items") or []
    old_completed = old.get("completed") or []
    completed_map = {}
    for c in old_completed:
        if isinstance(c, dict):
            try:
                completed_map[int(c.get("index"))] = c
            except Exception:
                pass
    images = []
    for i, it in enumerate(old_items, 1):
        if not isinstance(it, dict):
            it = {}
        entry = completed_map.get(i, {})
        plan = it.get("plan") if isinstance(it.get("plan"), dict) else {}
        auto = str(it.get("prompt") or "")
        status = "completed" if entry.get("file") else ("planned" if not entry else "pending")
        img = {k: str(plan.get(k) or it.get(k) or "") for k in ALBUM_PLAN_FIELDS}
        img.update({
            "index": i,
            "title": str(it.get("name") or ("第%d张" % i)),
            "role": str(it.get("role") or "variation"),
            "prompt": auto,
            "autoPrompt": auto,
            "userPrompt": "",
            "activePrompt": auto,
            "promptSource": "auto",
            "status": status,
            "file": str(entry.get("file") or ""),
            "url": str(entry.get("url") or ""),
            "seed": entry.get("seed"),
            "generatedAt": str(entry.get("generatedAt") or ""),
            "retryCount": 0,
            "history": [],
        })
        img["sampleScore"] = album_sample_score(preset, img)
        images.append(img)
    completed_idx = [x["index"] for x in images if x["status"] == "completed"]
    project = {
        "version": ALBUM_V3_VERSION,
        "id": str(old.get("id") or hashlib.md5(
            json.dumps(old, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]),
        "name": str(old.get("title") or "迁移画册"),
        "preset": preset_id,
        "kernel": preset.get("kernel"),
        "status": "completed" if len(completed_idx) == len(images) else ("images" if completed_idx else "planning"),
        "created": str(old.get("created") or old.get("updatedAt") or _now_iso()),
        "updated": _now_iso(),
        "userInput": {
            "originalTheme": str(old.get("premise") or old.get("userPremise") or ""),
            "expandedBrief": "",
            "fixedCharacter": str(old.get("fixedCharacter") or ""),
            "fixedWardrobe": "", "fixedWorld": "",
            "albumDirection": "", "sceneScale": "", "architectureStyle": "",
            "stylePresetName": "", "allowScaleFigure": False,
            "advancedRequirements": str(old.get("customArt") or ""),
            "mustInclude": [], "mustExclude": [],
            "count": len(images) or 6,
            "aspectRatio": str(old.get("aspect") or "16:9"),
            "visualStyle": str(old.get("aesthetic") or ""),
            "pdfTemplate": "cinematic",
            "narrativeStrength": "medium", "sceneVariation": "medium",
            "compositionVariation": "medium",
            "allowOutfitChange": True, "allowPeople": True,
        },
        "lockedFacts": [],
        "seriesBible": {
            "subjectLock": str(old.get("seriesLock") or ""),
            "worldLock": "", "wardrobeLock": "", "paletteLock": "",
            "materialLock": "", "cameraLock": "", "styleLock": "",
            "forbiddenChanges": [],
        },
        "sequenceDesign": {
            "overallGoal": "", "opening": "", "development": "",
            "mainVisual": "", "variation": "", "ending": "",
        },
        "diversityMatrix": {
            "shotSizes": [], "cameraAngles": [], "compositions": [],
            "actions": [], "lightingChanges": [], "sceneChanges": [],
        },
        "images": images,
        "sample": {
            "imageIndex": 1 if 1 in completed_map else album_select_sample(project, {"images": images}),
            "confirmed": False,
            "promptEdited": False,
        },
        "generation": {
            "completed": completed_idx,
            "pending": [x["index"] for x in images if x["status"] != "completed"],
            "failed": [],
            "currentIndex": None,
            "baseSeed": random.randrange(1, 1900000000),
        },
        "pdf": {
            "template": "cinematic", "showTitle": True, "showCaption": True,
            "showPrompt": False, "pureImageVersion": False,
            "path": str(old.get("pdfPath") or ""), "coverPath": "", "coverUrl": "",
        },
        "dir": str(dir_rel or "").replace("\\", "/"),
        "legacy": old,
    }
    return project


def album_item_description(img):
    img = img or {}
    return _clean("；".join(str(img.get(k) or "") for k in
                            ("subject", "action", "environment", "uniquePoint")
                            if str(img.get(k) or "").strip()), 260)


def album_start_image_job(project, index, seed=None, rerender=False):
    from app import COMIC_DIR, comfy_generate, panel_archive_file, safe_name
    idx = int(index)
    images = project.get("images") or []
    if idx < 1 or idx > len(images):
        raise RuntimeError("图片编号超出范围：%d" % idx)
    img = images[idx - 1]
    abs_dir = album_project_abs(project)
    old_file = str(img.get("file") or "")
    if not rerender and img.get("status") == "completed":
        if old_file and os.path.isfile(os.path.join(abs_dir, "原图", old_file)):
            return project, {"url": str(img.get("url") or ""), "archurl": "", "file": old_file, "skipped": True}
        img["status"] = "failed"
    if rerender and old_file and os.path.isfile(os.path.join(abs_dir, "原图", old_file)):
        hist_dir = os.path.join(abs_dir, "重出历史", "%02d" % idx)
        os.makedirs(hist_dir, exist_ok=True)
        dest_name = time.strftime("%Y%m%d_%H%M%S") + "_" + old_file
        dest = os.path.join(hist_dir, dest_name)
        try:
            os.replace(os.path.join(abs_dir, "原图", old_file), dest)
            history = img.setdefault("history", [])
            history.append({"file": dest_name, "at": _now_iso()})
        except Exception:
            pass
    prompt = str(img.get("activePrompt") or img.get("autoPrompt") or "")
    if not prompt:
        prompt = album_build_krea2_prompt(project, img)
        img["autoPrompt"] = prompt
    img["activePrompt"] = prompt
    img["promptSource"] = img.get("promptSource") or "auto"
    img["status"] = "generating"
    img["retryCount"] = int(img.get("retryCount") or 0) + (1 if rerender else 0)
    project["status"] = "generating"
    project["generation"]["currentIndex"] = idx
    album_save_project(project)
    fname = "%02d_%s" % (idx, safe_name(img.get("title") or ("第%02d张" % idx)))
    archive = panel_archive_file(str(project.get("dir") or "") + "/原图", fname)
    wh = ALBUM_ASPECT_SIZES.get(album_resolve_aspect(project, img))
    preset_id = str(project.get("preset") or "")
    generation = project.setdefault("generation", {})
    base_seed = int(generation.get("baseSeed") or random.randrange(1, 1900000000))
    generation["baseSeed"] = base_seed
    identity_series = preset_id in ("character_design", "character_photography")
    if seed is not None:
        sd = int(seed)
    elif rerender:
        sd = random.randrange(1, 1900000000)
        if identity_series and idx == int(project.get("sample", {}).get("imageIndex") or 1):
            generation["baseSeed"] = sd
    elif identity_series:
        # 当前Krea工作流没有参考图输入；同一文字身份锁 + 同一seed是可用的最强一致性约束。
        sd = base_seed
    else:
        sd = ((base_seed + idx * 7919) % 1899999999) or base_seed
    negative_prompt = ""
    if preset_id == "character_photography":
        branch = _album_photo_branch(img)
        if branch in ("no_person", "material"):
            negative_prompt = ("人物, 人脸, 人体, 手, 宇航员, 航天服人物, 摄影师, 摄影机, "
                               "监视器, 屏幕, 灯架, 文字, 标签, 徽章, 标牌, 乱码")
        else:
            negative_prompt = ("宇航员, 航天服人物, 摄影师, 摄影机, 监视器, 屏幕, 灯架, "
                               "反光板, 文字, 标签, 徽章, 标牌, 乱码, 人群, 第二个人, 复制人物")
    try:
        url = comfy_generate(prompt, sd, archive, wh,
                             negative_prompt=negative_prompt or None)
    except Exception as exc:
        img["status"] = "failed"
        img["error"] = str(exc)
        gen = project["generation"]
        gen["failed"] = sorted(set(gen.get("failed") or []) | {idx})
        gen["pending"] = sorted(set(gen.get("pending") or []) | {idx})
        gen["currentIndex"] = None
        project["status"] = "images"
        album_save_project(project)
        raise
    from urllib.parse import urlencode
    base = os.path.basename(archive)
    # /panel 固定以 COMIC_DIR 为根；旧代码相对项目父目录计算，导致刚生成的图全部404。
    rel_archive = os.path.relpath(archive, COMIC_DIR).replace("\\", "/")
    arch_url = "/panel?" + urlencode({"p": rel_archive})
    img.update({
        "status": "completed",
        "file": base,
        "seed": sd,
        "generatedAt": _now_iso(),
        "prompt": prompt,
        "activePrompt": prompt,
        "url": url,
        "archUrl": arch_url,
    })
    if preset_id == "character_design":
        # 人设图资产库：只镜像画册角色设定图，项目内原图继续保留。
        try:
            import character_asset_lib
            ui = project.get("userInput", {}) or {}
            human_text = (str(ui.get("fixedCharacter") or "").strip()
                          or str(ui.get("expandedBrief") or "").strip()
                          or str(ui.get("originalTheme") or "").strip())
            asset_name = (str(ui.get("fixedCharacter") or "").strip()
                          or str(ui.get("originalTheme") or "").strip()
                          or str(project.get("name") or "角色"))
            character_asset_lib.store_character_asset(
                kind="画册",
                project_name=project.get("name") or "",
                character_name=asset_name,
                image_path=archive,
                human_text=human_text,
                prompt_text=prompt,
                meta={"width": wh[0], "height": wh[1], "seed": sd, "preset": "角色设定图"},
            )
        except Exception as e:
            from app import log
            log("⚠ 人设图资产镜像失败：%s" % e)
    if idx == int(project.get("sample", {}).get("imageIndex") or 1):
        project["sample"]["promptEdited"] = False
        project["sample"]["confirmed"] = False
    gen = project["generation"]
    gen["completed"] = sorted(set(gen.get("completed") or []) | {idx})
    gen["pending"] = [i for i in gen.get("pending") or [] if i != idx]
    gen["failed"] = [i for i in gen.get("failed") or [] if i != idx]
    gen["currentIndex"] = None
    project["status"] = "completed" if len(gen["completed"]) == len(images) else "images"
    album_save_project(project)
    return project, {"url": url, "archurl": arch_url, "file": base, "skipped": False}


def album_resume_generation(project):
    abs_dir = album_project_abs(project)
    pending = []
    for img in project.get("images", []):
        idx = int(img.get("index") or 0)
        f = str(img.get("file") or "")
        if img.get("status") == "completed":
            if f and os.path.isfile(os.path.join(abs_dir, "原图", f)):
                continue
            img["status"] = "failed"
        pending.append(idx)
    gen = project["generation"]
    gen["pending"] = pending
    gen["failed"] = [i for i in gen.get("failed") or [] if i in pending]
    gen["completed"] = [i for i in gen.get("completed") or [] if i not in pending]
    album_save_project(project)
    return pending


def album_regenerate_image(project, index, prompt=None, restore_auto=False):
    idx = int(index)
    images = project.get("images") or []
    if idx < 1 or idx > len(images):
        raise RuntimeError("图片编号超出范围：%d" % idx)
    img = images[idx - 1]
    if restore_auto:
        img["userPrompt"] = ""
        img["activePrompt"] = str(img.get("autoPrompt") or "")
        img["promptSource"] = "auto"
        if idx == int(project.get("sample", {}).get("imageIndex") or 1):
            project["sample"]["promptEdited"] = False
            project["sample"]["confirmed"] = False
    elif prompt is not None:
        prompt = str(prompt).strip()
        if prompt:
            img["userPrompt"] = prompt
            img["activePrompt"] = prompt
            img["promptSource"] = "user"
            if idx == int(project.get("sample", {}).get("imageIndex") or 1):
                project["sample"]["promptEdited"] = True
                project["sample"]["confirmed"] = False
    album_save_project(project)
    return project


def album_can_start_batch(project):
    sample = project.get("sample") or {}
    if not sample.get("confirmed"):
        return False, "样片尚未确认，不能直接生成整套"
    if sample.get("promptEdited"):
        return False, "样片提示词已修改，请重新确认样片后再生成整套"
    return True, ""


def album_invalidate_after_setting_change(project):
    project["sample"]["confirmed"] = False
    project["sample"]["promptEdited"] = False
    for img in project.get("images", []):
        if img.get("status") != "completed":
            img["status"] = "planned"
    project["status"] = "planning"
    album_save_project(project)
    return project


def album_generate_cover(project, prompt=None):
    from app import COMIC_DIR, comfy_generate, panel_archive_file
    from urllib.parse import urlencode
    preset = album_get_preset(project.get("preset") or "")
    ui = project.get("userInput", {})
    bible = project.get("seriesBible", {}) or {}
    if not prompt:
        prompt = "画册封面主图，无任何文字，无标题，无logo；"
        prompt += _clean(bible.get("styleLock") or ui.get("visualStyle") or "高质量画册封面视觉", 130)
        prompt += "；" + _clean(bible.get("paletteLock") or "克制高级配色", 60)
        prompt += "；" + _clean(bible.get("subjectLock") or ui.get("originalTheme"), 130)
        prompt += "；%s画幅，电影感构图" % (ui.get("aspectRatio") or "16:9")
    archive = panel_archive_file(str(project.get("dir") or "") + "/封面", "cover")
    url = comfy_generate(prompt, random.randrange(1, 1900000000), archive,
                         ALBUM_ASPECT_SIZES.get(ui.get("aspectRatio") or "16:9"))
    project["pdf"]["coverPath"] = os.path.basename(archive)
    project["pdf"]["coverUrl"] = "/panel?" + urlencode(
        {"p": os.path.relpath(archive, COMIC_DIR).replace("\\", "/")})
    album_save_project(project)
    return project, url


# ================== PDF 排版模板 ==================

def _album_pdf_cover(title, mode_label, count, cover_path=None, accent=(202, 158, 91),
                     bg=(27, 24, 22), fg=(247, 241, 230)):
    from PIL import Image, ImageDraw
    from app import _font
    W, H = 2000, 1450
    page = Image.new("RGB", (W, H), bg)
    if cover_path and os.path.isfile(cover_path):
        with Image.open(cover_path) as src:
            src = src.convert("RGB")
            src.thumbnail((W - 160, H - 160), Image.Resampling.LANCZOS)
            page.paste(src, ((W - src.width) // 2, (H - src.height) // 2))
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        from PIL import ImageDraw as _id
        d = _id.Draw(overlay)
        d.rectangle((0, int(H * .62), W, H), fill=(10, 8, 6, 190))
        page.paste(overlay, (0, 0), overlay)
    draw = ImageDraw.Draw(page)
    draw.rectangle((70, 70, W - 70, H - 70), outline=accent, width=3)
    draw.line((140, int(H * .33), W - 140, int(H * .33)), fill=accent, width=2)
    draw.text((W // 2, int(H * .21)), str(title or "未命名画册")[:36],
              font=_font(64, True), fill=fg, anchor="mm")
    draw.text((W // 2, int(H * .40)), str(mode_label or "Krea2高质量画册"),
              font=_font(30), fill=accent, anchor="mm")
    draw.text((W // 2, int(H * .48)), "%d 幅精选作品" % count,
              font=_font(24), fill=(188, 180, 169), anchor="mm")
    draw.text((W // 2, H - 120), "KREA 2 · CURATED ALBUM",
              font=_font(21, True), fill=(128, 119, 108), anchor="mm")
    return page


def _album_pdf_save(title, pages, template):
    from app import _font, BOOK_DIR, safe_name
    os.makedirs(BOOK_DIR, exist_ok=True)
    suffix = {"cinematic": "", "fashion": "-时尚杂志版", "setting": "-设定集版",
              "catalog": "-图鉴目录版", "pure": "-纯图写真"}.get(template, "")
    out = os.path.join(BOOK_DIR, safe_name(title or "未命名画册") + "·Krea2高质量画册%s.pdf" % suffix)
    tmp = out + ".tmp.pdf"
    try:
        pages[0].save(tmp, "PDF", save_all=True, append_images=pages[1:],
                      resolution=200.0, quality=95)
        if not os.path.isfile(tmp) or os.path.getsize(tmp) < 1024:
            raise RuntimeError("画册PDF生成失败")
        os.replace(tmp, out)
    finally:
        for page in pages:
            try:
                page.close()
            except Exception:
                pass
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
    from urllib.parse import quote
    return out, "/book/" + quote(os.path.basename(out))


def _album_image_thumb(entry, box):
    from PIL import Image, ImageOps
    with Image.open(entry["path"]) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((box[2] - box[0], box[3] - box[1]), Image.Resampling.LANCZOS)
        return image


def _album_pdf_cinematic(title, entries, mode_label, project, opts, cover_path=None):
    from PIL import Image, ImageDraw
    from app import _font, _wrap
    W, H = 2000, 1450
    margin = 82
    pages = [_album_pdf_cover(title, mode_label, len(entries), cover_path)]
    for index, entry in enumerate(entries, 1):
        page = Image.new("RGB", (W, H), (246, 242, 235))
        draw = ImageDraw.Draw(page)
        if not opts.get("pureImageVersion"):
            if opts.get("showTitle", True):
                draw.text((margin, 48), str(entry["name"])[:40],
                          font=_font(36, True), fill=(38, 34, 30))
            draw.text((W - margin, 58), "%02d / %02d" % (index, len(entries)),
                      font=_font(22), fill=(126, 111, 91), anchor="ra")
        image_top = 122 if not opts.get("pureImageVersion") else 50
        image_bottom = H - 210 if not opts.get("pureImageVersion") else H - 50
        box = (margin, image_top, W - margin, image_bottom)
        image = _album_image_thumb(entry, box)
        x = box[0] + (box[2] - box[0] - image.width) // 2
        y = box[1] + (box[3] - box[1] - image.height) // 2
        page.paste(image, (x, y))
        draw.rectangle((x - 2, y - 2, x + image.width + 2, y + image.height + 2),
                       outline=(207, 194, 176), width=2)
        if not opts.get("pureImageVersion"):
            texts = []
            if opts.get("showCaption", True) and entry.get("description"):
                texts.append(str(entry["description"]))
            if opts.get("showPrompt") and entry.get("prompt"):
                texts.append("提示词：" + str(entry["prompt"]))
            y = H - 165
            for line in _wrap(draw, "  ".join(texts), _font(24), W - margin * 2)[:3]:
                draw.text((margin, y), line, font=_font(24), fill=(72, 65, 57))
                y += 34
        pages.append(page)
    return _album_pdf_save(title, pages, "cinematic")


def _album_pdf_fashion(title, entries, mode_label, project, opts, cover_path=None):
    from PIL import Image, ImageDraw
    from app import _font, _wrap
    W, H = 1700, 2400
    margin = 110
    pages = [_album_pdf_cover(title, mode_label, len(entries), cover_path,
                              accent=(150, 106, 56), bg=(250, 247, 242), fg=(40, 34, 28))]
    for index, entry in enumerate(entries, 1):
        page = Image.new("RGB", (W, H), (250, 247, 242))
        draw = ImageDraw.Draw(page)
        if opts.get("pureImageVersion"):
            box = (margin, margin, W - margin, H - margin)
            image = _album_image_thumb(entry, box)
            page.paste(image, ((W - image.width) // 2, (H - image.height) // 2))
            pages.append(page)
            continue
        with Image.open(entry["path"]) as src:
            landscape = src.width >= src.height
        draw.text((margin, 70), str(entry["name"])[:30],
                  font=_font(46, True), fill=(40, 34, 28))
        draw.text((W - margin, 82), "NO.%02d" % index,
                  font=_font(22), fill=(170, 150, 120), anchor="ra")
        if landscape:
            box = (margin, 180, W - margin, H - 420)
            image = _album_image_thumb(entry, box)
            x = (W - image.width) // 2
            y = 180
            page.paste(image, (x, y))
            caption_y = H - 360
        else:
            box = (int(W * .34), 190, W - margin, H - 360)
            image = _album_image_thumb(entry, box)
            page.paste(image, (W - margin - image.width, 190))
            caption_y = 190
        texts = []
        if opts.get("showCaption", True) and entry.get("description"):
            texts.append(str(entry["description"]))
        if opts.get("showPrompt") and entry.get("prompt"):
            texts.append("提示词：" + str(entry["prompt"]))
        y = caption_y
        for line in _wrap(draw, "  ".join(texts), _font(24), W - margin * 2)[:4]:
            draw.text((margin, y), line, font=_font(24), fill=(90, 82, 72))
            y += 34
        draw.text((W // 2, H - 90), "%02d / %02d" % (index, len(entries)),
                  font=_font(22), fill=(170, 150, 120), anchor="mm")
        pages.append(page)
    return _album_pdf_save(title, pages, "fashion")


def _album_pdf_setting(title, entries, mode_label, project, opts, cover_path=None):
    from PIL import Image, ImageDraw
    from app import _font, _wrap
    W, H = 2100, 1500
    margin = 90
    pages = [_album_pdf_cover(title, mode_label, len(entries), cover_path)]
    for index, entry in enumerate(entries, 1):
        page = Image.new("RGB", (W, H), (246, 242, 235))
        draw = ImageDraw.Draw(page)
        draw.text((margin, 55), "%02d　%s" % (index, str(entry["name"])[:30]),
                  font=_font(40, True), fill=(38, 34, 30))
        box = (margin, 150, int(W * .62), H - 140)
        image = _album_image_thumb(entry, box)
        page.paste(image, (box[0], box[1] + (box[3] - box[1] - image.height) // 2))
        draw.rectangle((box[0] - 2, box[1] - 2, box[0] + image.width + 2,
                        box[1] + image.height + 2), outline=(207, 194, 176), width=2)
        rx = int(W * .66)
        rw = W - rx - margin
        y = 160
        draw.text((rx, y), "设 计 重 点", font=_font(26, True), fill=(150, 106, 56))
        y += 52
        if opts.get("pureImageVersion"):
            pages.append(page)
            continue
        if opts.get("showCaption", True) and entry.get("description"):
            for line in _wrap(draw, str(entry["description"]), _font(24), rw)[:7]:
                draw.text((rx, y), line, font=_font(24), fill=(72, 65, 57))
                y += 34
        labels = []
        for key, label in (("viewType", "视角"), ("designFocus", "设计焦点"),
                           ("spaceType", "空间类型"), ("structuralFocus", "结构重点"),
                           ("surfaceDetail", "表面细节"), ("displayMode", "展示方式")):
            value = _clean(entry.get("_extra", {}).get(key))
            if value:
                labels.append("%s：%s" % (label, value))
        if opts.get("showPrompt") and entry.get("prompt"):
            labels.append("提示词：" + str(entry["prompt"]))
        for line in _wrap(draw, "\n".join(labels), _font(21), rw)[:10]:
            draw.text((rx, y), line, font=_font(21), fill=(110, 101, 90))
            y += 30
        pages.append(page)
    return _album_pdf_save(title, pages, "setting")


def _album_pdf_catalog(title, entries, mode_label, project, opts, cover_path=None):
    from PIL import Image, ImageDraw
    from app import _font, _wrap
    W, H = 1700, 2300
    margin = 100
    pages = [_album_pdf_cover(title, mode_label, len(entries), cover_path,
                              accent=(150, 106, 56), bg=(248, 246, 241), fg=(40, 34, 28))]
    for index, entry in enumerate(entries, 1):
        page = Image.new("RGB", (W, H), (248, 246, 241))
        draw = ImageDraw.Draw(page)
        draw.line((margin, 150, W - margin, 150), fill=(210, 200, 185), width=2)
        draw.text((margin, 70), "NO.%03d" % index, font=_font(30, True), fill=(150, 106, 56))
        draw.text((W - margin, 70), str(entry["name"])[:30],
                  font=_font(38, True), fill=(40, 34, 28), anchor="ra")
        box = (margin, 190, W - margin, H - 520)
        image = _album_image_thumb(entry, box)
        page.paste(image, ((W - image.width) // 2, box[1] + (box[3] - box[1] - image.height) // 2))
        draw.rectangle(((W - image.width) // 2 - 2, box[1] - 2,
                        (W - image.width) // 2 + image.width + 2,
                        box[1] + image.height + 2), outline=(210, 200, 185), width=2)
        if opts.get("pureImageVersion"):
            pages.append(page)
            continue
        attrs = [("名称", entry["name"]), ("编号", "%03d" % index)]
        for key, label in (("subject", "主体"), ("displayMode", "展示"),
                           ("structureFocus", "结构"), ("surfaceDetail", "表面"),
                           ("material", "材质")):
            value = _clean(entry.get("_extra", {}).get(key))
            if value:
                attrs.append((label, value))
        if opts.get("showCaption", True) and entry.get("description"):
            attrs.append(("说明", str(entry["description"])))
        if opts.get("showPrompt") and entry.get("prompt"):
            attrs.append(("提示词", str(entry["prompt"])))
        y = H - 480
        for label, value in attrs[:8]:
            draw.text((margin, y), label, font=_font(24, True), fill=(120, 100, 80))
            draw.text((margin + 120, y), str(value)[:72], font=_font(22), fill=(72, 65, 57))
            y += 44
        pages.append(page)
    return _album_pdf_save(title, pages, "catalog")


def _album_pdf_pure(title, entries, mode_label, project, opts, cover_path=None):
    """纯图写真：封面 + 一图一页，不显示提示词/seed/参数/母型标签。"""
    from PIL import Image, ImageDraw
    from app import _font
    W, H = 1800, 2400
    margin = 60
    pages = [_album_pdf_cover(title, "纯图写真", len(entries), cover_path,
                              accent=(202, 158, 91), bg=(24, 22, 20), fg=(244, 238, 228))]
    for index, entry in enumerate(entries, 1):
        page = Image.new("RGB", (W, H), (246, 242, 235))
        box = (margin, margin, W - margin, H - margin)
        image = _album_image_thumb(entry, box)
        x = box[0] + (box[2] - box[0] - image.width) // 2
        y = box[1] + (box[3] - box[1] - image.height) // 2
        page.paste(image, (x, y))
        draw = ImageDraw.Draw(page)
        draw.text((W - margin, H - 40), "%02d / %02d" % (index, len(entries)),
                  font=_font(20), fill=(140, 130, 118), anchor="ra")
        pages.append(page)
    return _album_pdf_save(title, pages, "pure")


def album_export_pdf(project, options=None):
    opts = dict(options or {})
    default_template = "pure" if project.get("preset") == "character_photography" else "cinematic"
    template = str(opts.get("template") or project.get("pdf", {}).get("template") or default_template)
    if template not in ALBUM_PDF_TEMPLATES:
        template = default_template
    opts.setdefault("showTitle", project.get("pdf", {}).get("showTitle", True))
    opts.setdefault("showCaption", project.get("pdf", {}).get("showCaption", True))
    opts.setdefault("showPrompt", project.get("pdf", {}).get("showPrompt", False))
    opts.setdefault("pureImageVersion", project.get("pdf", {}).get("pureImageVersion", False))
    abs_dir = album_project_abs(project)
    entries = []
    for img in project.get("images", []):
        f = str(img.get("file") or "")
        p = os.path.join(abs_dir, "原图", f)
        if img.get("status") == "completed" and f and os.path.isfile(p):
            entries.append({
                "path": p,
                "name": str(img.get("title") or ("第%02d张" % img.get("index"))),
                "description": album_item_description(img),
                "prompt": str(img.get("activePrompt") or img.get("autoPrompt") or ""),
                "_extra": img,
            })
    if not entries:
        raise RuntimeError("没有可导出的已完成图片")
    preset = album_get_preset(project.get("preset") or "")
    title = project.get("name") or "未命名画册"
    mode_label = preset.get("name") or "高质量画册"
    builder = {
        "cinematic": _album_pdf_cinematic,
        "fashion": _album_pdf_fashion,
        "setting": _album_pdf_setting,
        "catalog": _album_pdf_catalog,
        "pure": _album_pdf_pure,
    }[template]
    cover_path = None
    if opts.get("useCover"):
        cp = os.path.join(abs_dir, "封面", project.get("pdf", {}).get("coverPath") or "")
        if os.path.isfile(cp):
            cover_path = cp
    out, url = builder(title, entries, mode_label, project, opts, cover_path)
    project["pdf"]["template"] = template
    project["pdf"]["path"] = out
    album_save_project(project)
    return out, url


# ================== 兼容接口（供 app.py 与测试直接调用） ==================

def album_create_and_plan(user_input, with_image=False):
    """一步创建项目并规划（测试/脚本用）。"""
    project = album_create_project(user_input)
    facts = album_extract_locked_facts(project["userInput"])
    project["lockedFacts"] = facts["lockedFacts"]
    plan = album_plan_series(project)
    project["name"] = plan.get("albumName") or project["name"]
    project["seriesBible"] = plan.get("seriesBible") or {}
    project["sequenceDesign"] = plan.get("sequenceDesign") or {}
    project["diversityMatrix"] = plan.get("diversityMatrix") or {}
    project["images"] = plan.get("images") or []
    project["status"] = "planning"
    project["sample"]["imageIndex"] = album_select_sample(project, plan)
    project["sample"]["confirmed"] = False
    project["sample"]["promptEdited"] = False
    album_save_project(project)
    if with_image and project["images"]:
        idx = project["sample"]["imageIndex"]
        project, _ = album_start_image_job(project, idx)
    return project
