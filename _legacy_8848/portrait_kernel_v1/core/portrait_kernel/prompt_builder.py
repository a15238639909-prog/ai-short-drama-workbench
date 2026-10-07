# -*- coding: utf-8 -*-
"""V1.0 Prompt 构建器：把整本规划翻译成单张精准Prompt，并做结构检查。"""

import re
from typing import List

from .models import AlbumPlan
from .photography import PhotographyChooser
from .scene_pool import get_scene
from .wardrobe_pool import get_wardrobe_pool

FORBIDDEN = ("活人感", "高级感", "AI感", "自然抓拍感", "国际写真", "摄影大师",
             "电影级", "生命力", "灵魂感", "禁止", "真人感", "自然真实",
             "高质量摄影", "极致真实", "人物第一", "情绪张力", "故事感", "去AI")
TRUNC_TAILS = ("，", "、", "：", "与", "和", "的", "中", "主", "画面", "只有")


class PromptBuilder:
    def __init__(self):
        self._camera = PhotographyChooser()

    def build(self, album: AlbumPlan) -> List[str]:
        prompts = []
        pool = get_wardrobe_pool(album.domain)
        for shot in album.shots:
            scene = get_scene(shot.scene_id)
            wardrobe = pool[shot.wardrobe_id]
            near = shot.frame in ("close_up", "head_shoulders")
            identity = album.character.weak_lock(near=near)
            camera = self._camera.camera_text(shot.lens, shot.frame, shot.angle)
            parts = [identity, shot.action, "穿" + wardrobe.description, scene.space]
            if shot.effect:
                parts.append(shot.effect)
            parts.append(camera)
            parts.append(shot.lighting)
            prompt = "。".join(parts) + "。"
            prompts.append(prompt)
        return prompts


def validate_prompt(prompt: str, key: str = "") -> List[str]:
    errors = []
    if not prompt:
        return ["%s 提示词为空" % key]
    if len(prompt) < 80 or len(prompt) > 180:
        errors.append("%s 长度异常（%d）" % (key, len(prompt)))
    if prompt.endswith(TRUNC_TAILS):
        errors.append("%s 提示词以残句结尾" % key)
    for w in FORBIDDEN:
        if w in prompt:
            errors.append("%s 混入禁用词：%s" % (key, w))
    if "或" in prompt:
        errors.append("%s 出现“或”" % key)
    if re.search(r"\d+\s*[-–~]\s*\d+\s*(mm|厘米|米)", prompt):
        errors.append("%s 出现焦段/距离范围" % key)
    return errors
