# -*- coding: utf-8 -*-
"""SPACE_LIFE 主题域：写实生活化太空人物写真。
现实锚点：科研站、航空器、舰船舱室、工业设施、轨道实验室。
"""

from core.portrait_kernel.models import Character, DomainDef

CHARACTER_B = Character(
    age="20岁左右",
    ethnicity="成年东亚女性",
    body_type="身形纤细偏瘦",
    hair="黑色自然中短发",
    face_impression="清秀偏窄的年轻面部轮廓",
    distinctive_features=["深色眼睛", "左脸一颗很淡的小痣"],
    temperament="自然清爽安静",
)

SPACE_LIFE = DomainDef(
    id="SPACE_LIFE",
    name="生活化写实太空人物写真",
    reality_anchor="科研站、航空器、舰船舱室、工业设施、轨道实验室人物摄影",
    scene_ids=[
        "ORBITAL_BEDROOM", "LIVING_CABIN", "OBSERVATION_WINDOW", "LONG_MODULE",
        "MAINTENANCE_AREA", "EQUIPMENT_PREP", "OBSERVATION_DECK",
    ],
    wardrobe_ids=["ST", "GK", "WG", "JW", "TR", "IL", "PL"],
    character=CHARACTER_B,
    scene_suitability={
        "CLEAN_BEAUTY": ["OBSERVATION_DECK", "OBSERVATION_WINDOW", "LIVING_CABIN", "EQUIPMENT_PREP"],
        "QUIET_HUMAN": ["ORBITAL_BEDROOM", "OBSERVATION_WINDOW", "LIVING_CABIN"],
        "ENVIRONMENT_REACTION": ["OBSERVATION_WINDOW", "OBSERVATION_DECK", "LONG_MODULE", "ORBITAL_BEDROOM"],
        "BODY_IN_MOTION": ["LONG_MODULE", "LIVING_CABIN", "MAINTENANCE_AREA", "OBSERVATION_DECK"],
        "UNGUARDED_JOY": ["ORBITAL_BEDROOM", "LIVING_CABIN", "EQUIPMENT_PREP"],
        "FREE_CAMERA": ["LIVING_CABIN", "LONG_MODULE", "OBSERVATION_DECK", "MAINTENANCE_AREA"],
    },
    preferred_wardrobe={
        "ORBITAL_BEDROOM": ["ST", "GK"],
        "LIVING_CABIN": ["GK", "JW"],
        "OBSERVATION_WINDOW": ["GK", "ST"],
        "LONG_MODULE": ["JW", "WG"],
        "MAINTENANCE_AREA": ["JW", "WG"],
        "EQUIPMENT_PREP": ["PL", "IL"],
        "OBSERVATION_DECK": ["JW", "GK"],
    },
    rhythm_cycle=[
        "CLEAN_BEAUTY", "QUIET_HUMAN", "QUIET_HUMAN", "ENVIRONMENT_REACTION",
        "BODY_IN_MOTION", "QUIET_HUMAN", "CLEAN_BEAUTY", "QUIET_HUMAN",
        "UNGUARDED_JOY", "BODY_IN_MOTION", "FREE_CAMERA", "QUIET_HUMAN",
        "CLEAN_BEAUTY", "ENVIRONMENT_REACTION", "BODY_IN_MOTION", "QUIET_HUMAN",
        "FREE_CAMERA", "CLEAN_BEAUTY",
    ],
)
