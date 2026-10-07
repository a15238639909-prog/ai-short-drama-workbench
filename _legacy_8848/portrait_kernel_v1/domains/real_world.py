# -*- coding: utf-8 -*-
"""REAL_WORLD 主题域：日常、城市、旅行、自然、室内等真实世界写真。"""

from core.portrait_kernel.models import Character, DomainDef

CHARACTER_A = Character(
    age="20岁左右",
    ethnicity="成年东亚女性",
    body_type="身形纤细偏瘦",
    hair="黑色自然中短发",
    face_impression="清秀偏窄的年轻面部轮廓",
    distinctive_features=["深色眼睛", "左脸一颗很淡的小痣"],
    temperament="自然清爽安静",
)

REAL_WORLD = DomainDef(
    id="REAL_WORLD",
    name="真实世界人物写真",
    reality_anchor="真实城市生活摄影：街道、咖啡馆、居室、公园与户外自然光",
    scene_ids=[
        "BEDROOM", "HOME_WINDOW", "CAFE", "CITY_STREET", "BUS_STOP",
        "PARK", "RIVERSIDE", "SEASIDE", "FOREST", "GRASSLAND", "OLD_TOWN",
        "TRAVEL_ROOM",
    ],
    wardrobe_ids=["WT", "GK", "MK", "BC", "NJ", "WD", "DR", "SP", "LJ"],
    character=CHARACTER_A,
    scene_suitability={
        "CLEAN_BEAUTY": ["CITY_STREET", "BUS_STOP", "SEASIDE", "GRASSLAND", "OLD_TOWN"],
        "QUIET_HUMAN": ["BEDROOM", "HOME_WINDOW", "CAFE", "TRAVEL_ROOM", "PARK"],
        "ENVIRONMENT_REACTION": ["BUS_STOP", "RIVERSIDE", "SEASIDE", "FOREST", "GRASSLAND", "OLD_TOWN"],
        "BODY_IN_MOTION": ["CITY_STREET", "PARK", "RIVERSIDE", "OLD_TOWN", "GRASSLAND"],
        "UNGUARDED_JOY": ["CAFE", "PARK", "HOME_WINDOW", "BEDROOM"],
        "FREE_CAMERA": ["CITY_STREET", "RIVERSIDE", "FOREST", "GRASSLAND", "OLD_TOWN"],
    },
    preferred_wardrobe={
        "BEDROOM": ["WT", "DR"],
        "HOME_WINDOW": ["GK", "MK"],
        "CAFE": ["GK", "MK", "BC"],
        "CITY_STREET": ["NJ", "WD"],
        "BUS_STOP": ["WD", "NJ"],
        "PARK": ["SP", "NJ"],
        "RIVERSIDE": ["LJ", "BC"],
        "SEASIDE": ["DR", "SP"],
        "FOREST": ["NJ", "LJ"],
        "GRASSLAND": ["SP", "DR"],
        "OLD_TOWN": ["BC", "LJ"],
        "TRAVEL_ROOM": ["GK", "WT"],
    },
    rhythm_cycle=[
        "CLEAN_BEAUTY", "QUIET_HUMAN", "ENVIRONMENT_REACTION", "QUIET_HUMAN",
        "BODY_IN_MOTION", "QUIET_HUMAN", "CLEAN_BEAUTY", "UNGUARDED_JOY",
        "BODY_IN_MOTION", "ENVIRONMENT_REACTION", "FREE_CAMERA", "QUIET_HUMAN",
        "CLEAN_BEAUTY", "QUIET_HUMAN", "ENVIRONMENT_REACTION", "BODY_IN_MOTION",
        "FREE_CAMERA", "CLEAN_BEAUTY",
    ],
)
