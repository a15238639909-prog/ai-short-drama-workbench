# -*- coding: utf-8 -*-
"""V1.0 核心数据模型：人物、场景包、服装、镜头、单张与整本规划。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

MOTHERS = [
    "CLEAN_BEAUTY",
    "QUIET_HUMAN",
    "ENVIRONMENT_REACTION",
    "BODY_IN_MOTION",
    "UNGUARDED_JOY",
    "FREE_CAMERA",
]

STATES = ["STILL", "RELAXED", "OBSERVING", "INTERACTING", "MOVING", "REACTING", "JOY"]

FRAMES = [
    "close_up", "head_shoulders", "half_body",
    "three_quarter", "full_body", "environmental_portrait",
]

ANGLES = ["eye_level", "slight_high", "slight_low", "side", "three_quarter", "close_wide"]


@dataclass
class Character:
    """人物身份层：只回答“她是谁”，不锁表情。"""

    age: str
    ethnicity: str
    body_type: str
    hair: str
    face_impression: str
    distinctive_features: List[str] = field(default_factory=list)
    temperament: str = ""
    custom_weak_lock: str = ""

    def weak_lock(self, near: bool = False) -> str:
        if self.custom_weak_lock:
            text = self.custom_weak_lock
            if near:
                text += "，皮肤自然，眼睛有真实水光，头发有零散碎发"
            return text
        text = "%s%s，%s，%s，%s，%s" % (
            self.age,
            self.ethnicity,
            self.body_type,
            self.hair,
            self.face_impression,
            "，".join(self.distinctive_features) if self.distinctive_features else "深色眼睛",
        )
        if near:
            text += "，皮肤自然，眼睛有真实水光，头发有零散碎发"
        return text


@dataclass
class ScenePackage:
    """场景包：只存空间、动作、环境作用、光线四类信息。"""

    id: str
    name: str
    domain: str
    space: str
    actions: Dict[str, List[str]]
    effects: List[str]
    lighting: List[str]
    preferred_lenses: List[int]
    preferred_frames: List[str]


@dataclass
class WardrobeItem:
    id: str
    name: str
    description: str
    domain: str


@dataclass
class ShotPlan:
    index: int
    key: str
    mother: str
    state: str
    scene_id: str
    wardrobe_id: str
    action: str
    lens: int
    frame: str
    angle: str
    lighting: str
    effect: Optional[str]
    aspect: str
    seed: int


@dataclass
class AlbumPlan:
    project_name: str
    domain: str
    character: Character
    count: int
    shots: List[ShotPlan] = field(default_factory=list)


@dataclass
class DomainDef:
    id: str
    name: str
    reality_anchor: str
    scene_ids: List[str]
    wardrobe_ids: List[str]
    character: Character
    scene_suitability: Dict[str, List[str]]
    preferred_wardrobe: Dict[str, List[str]]
    rhythm_cycle: List[str] = field(default_factory=list)
