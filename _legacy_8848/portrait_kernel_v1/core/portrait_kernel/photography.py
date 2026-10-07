# -*- coding: utf-8 -*-
"""V1.0 摄影选择器：焦段/画幅/机位，按画面需要选择，不机械平均。"""

from typing import Dict, List

MOTHER_LENS: Dict[str, List[int]] = {
    "CLEAN_BEAUTY": [50, 85],
    "QUIET_HUMAN": [50, 70, 85],
    "ENVIRONMENT_REACTION": [35, 50],
    "BODY_IN_MOTION": [35, 50],
    "UNGUARDED_JOY": [50, 85],
    "FREE_CAMERA": [28, 35, 85],
}

MOTHER_FRAME: Dict[str, List[str]] = {
    "CLEAN_BEAUTY": ["half_body", "three_quarter"],
    "QUIET_HUMAN": ["close_up", "half_body"],
    "ENVIRONMENT_REACTION": ["half_body", "close_up"],
    "BODY_IN_MOTION": ["full_body", "three_quarter", "half_body"],
    "UNGUARDED_JOY": ["close_up", "half_body"],
    "FREE_CAMERA": ["half_body", "environmental_portrait", "full_body", "close_up"],
}

MOTHER_ANGLE: Dict[str, List[str]] = {
    "CLEAN_BEAUTY": ["eye_level", "three_quarter"],
    "QUIET_HUMAN": ["eye_level", "slight_high"],
    "ENVIRONMENT_REACTION": ["eye_level", "side"],
    "BODY_IN_MOTION": ["eye_level", "slight_low"],
    "UNGUARDED_JOY": ["eye_level", "three_quarter"],
    "FREE_CAMERA": ["close_wide", "slight_high", "side"],
}

FRAME_TEXT = {
    "close_up": "近景",
    "head_shoulders": "头肩",
    "half_body": "半身",
    "three_quarter": "七分身",
    "full_body": "全身",
    "environmental_portrait": "环境人像",
}

ANGLE_TEXT = {
    "eye_level": "平视",
    "slight_high": "轻微俯拍",
    "slight_low": "轻微仰拍",
    "side": "侧面机位",
    "three_quarter": "3/4侧面",
    "close_wide": "近距离广角",
}


def _distance(lens: int) -> str:
    if lens in (28, 35):
        return "相机离她约0.8米"
    if lens == 50:
        return "相机离她约1.5米"
    if lens == 70:
        return "相机离她约2米"
    if lens == 85:
        return "相机离她约3米"
    return "远距离观察"


class PhotographyChooser:
    def __init__(self):
        self._last_lens: List[int] = []
        self._last_frame: List[str] = []
        self._last_angle: List[str] = []

    def choose(self, index: int, mother: str, scene, last_lens=None, last_frame=None, last_angle=None):
        """返回 (lens, frame, angle)。带重复惩罚，避免连续三张相同。"""
        if last_lens is not None:
            self._last_lens = last_lens
        if last_frame is not None:
            self._last_frame = last_frame
        if last_angle is not None:
            self._last_angle = last_angle

        lens_candidates = list(MOTHER_LENS[mother])
        lens_order = {v: i for i, v in enumerate(scene.preferred_lenses)}
        # 封面首张优先85mm
        if index == 0 and 85 in lens_candidates:
            lens = 85
        else:
            fresh = [v for v in lens_candidates if v not in self._last_lens[-2:]]
            if fresh:
                lens = min(fresh, key=lambda v: lens_order.get(v, 99))
            else:
                lens = max(lens_candidates, key=lambda v: (
                    self._last_lens[::-1].index(v) if v in self._last_lens else -1))

        frame_candidates = [v for v in MOTHER_FRAME[mother] if v in scene.preferred_frames]
        if not frame_candidates:
            frame_candidates = list(MOTHER_FRAME[mother])
        frame_order = {v: i for i, v in enumerate(scene.preferred_frames)}
        fresh_frames = [v for v in frame_candidates if v not in self._last_frame[-2:]]
        if fresh_frames:
            frame = min(fresh_frames, key=lambda v: frame_order.get(v, 99))
        else:
            frame = max(frame_candidates, key=lambda v: (
                self._last_frame[::-1].index(v) if v in self._last_frame else -1))

        angle_candidates = list(MOTHER_ANGLE[mother])
        fresh_angles = [v for v in angle_candidates if v not in self._last_angle[-2:]]
        if fresh_angles:
            angle = fresh_angles[index % len(fresh_angles)]
        else:
            angle = angle_candidates[index % len(angle_candidates)]

        self._last_lens.append(lens)
        self._last_frame.append(frame)
        self._last_angle.append(angle)
        return lens, frame, angle

    def camera_text(self, lens: int, frame: str, angle: str) -> str:
        parts = ["%dmm" % lens, _distance(lens), FRAME_TEXT[frame], ANGLE_TEXT[angle]]
        if frame == "environmental_portrait":
            parts.append("人物约占画面50%")
        elif frame == "full_body":
            parts.append("人物完整入画")
        elif frame in ("close_up", "head_shoulders"):
            parts.append("焦点在眼睛")
        return "，".join(parts)
