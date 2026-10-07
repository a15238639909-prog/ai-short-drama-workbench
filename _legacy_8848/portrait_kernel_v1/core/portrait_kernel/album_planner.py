# -*- coding: utf-8 -*-
"""V1.0 整本写真规划器：动态生成节奏、场景、状态、服装与镜头。
不使用固定12/24张硬模板；图片数量变化时仍按同一管线规划。
"""

from typing import List

from .models import AlbumPlan, DomainDef, ShotPlan
from .photography import PhotographyChooser
from .scene_pool import get_scene
from .wardrobe_pool import select_wardrobes


STATE_BY_MOTHER = {
    "CLEAN_BEAUTY": ["STILL"],
    "QUIET_HUMAN": ["RELAXED", "OBSERVING"],
    "ENVIRONMENT_REACTION": ["REACTING"],
    "BODY_IN_MOTION": ["MOVING", "INTERACTING"],
    "UNGUARDED_JOY": ["JOY"],
    "FREE_CAMERA": ["OBSERVING", "STILL", "MOVING"],
}

RHYTHM_CYCLE = [
    "CLEAN_BEAUTY", "QUIET_HUMAN", "ENVIRONMENT_REACTION", "QUIET_HUMAN",
    "BODY_IN_MOTION", "QUIET_HUMAN", "CLEAN_BEAUTY", "UNGUARDED_JOY",
    "BODY_IN_MOTION", "ENVIRONMENT_REACTION", "FREE_CAMERA", "QUIET_HUMAN",
    "CLEAN_BEAUTY", "QUIET_HUMAN", "ENVIRONMENT_REACTION", "BODY_IN_MOTION",
    "FREE_CAMERA", "CLEAN_BEAUTY", "QUIET_HUMAN", "CLEAN_BEAUTY",
    "BODY_IN_MOTION", "QUIET_HUMAN", "ENVIRONMENT_REACTION", "FREE_CAMERA",
]


def _no_triple_repeat(mothers: List[str]) -> List[str]:
    out = list(mothers)
    for i in range(2, len(out)):
        if out[i] == out[i - 1] == out[i - 2]:
            for j, alt in enumerate(RHYTHM_CYCLE):
                if alt != out[i - 1] and alt != out[i]:
                    out[i] = alt
                    break
    return out


def _pick_scene(mother: str, domain: DomainDef, usage: dict, last_two: List[str], offset: int) -> str:
    suitable = domain.scene_suitability[mother]
    candidates = [s for s in suitable if s not in last_two]
    if not candidates:
        candidates = suitable
    candidates.sort(key=lambda s: (usage[s], s))
    return candidates[offset % len(candidates)]


class AlbumPlanner:
    def plan(self, domain: DomainDef, count: int, project_name: str, seed_base: int = 20260819) -> AlbumPlan:
        cycle = domain.rhythm_cycle or RHYTHM_CYCLE
        mothers = _no_triple_repeat((cycle * ((count // len(cycle)) + 1))[:count])
        usage = {s: 0 for s in domain.scene_ids}
        last_scenes: List[str] = []
        beats = []
        for i, mother in enumerate(mothers):
            scene_id = _pick_scene(mother, domain, usage, last_scenes, i)
            usage[scene_id] += 1
            last_scenes.append(scene_id)
            if len(last_scenes) > 2:
                last_scenes.pop(0)
            state_opts = STATE_BY_MOTHER[mother]
            state = state_opts[i % len(state_opts)]
            beats.append((mother, state, scene_id))

        # 先做不含服装/镜头的骨架，选服装，再补镜头与光线
        skeleton = []
        for i, (mother, state, scene_id) in enumerate(beats):
            scene = get_scene(scene_id)
            action_opts = scene.actions[state]
            action = action_opts[i % len(action_opts)]
            skeleton.append(ShotPlan(
                index=i, key="", mother=mother, state=state, scene_id=scene_id,
                wardrobe_id="", action=action, lens=50, frame="half_body",
                angle="eye_level", lighting="", effect=None, aspect="4:5", seed=0))

        k = min(6, max(4, (count + 2) // 3))
        assigned, distinct = select_wardrobes(skeleton, domain.id, domain.preferred_wardrobe, k=k)
        chooser = PhotographyChooser()
        last_lens, last_frame, last_angle = [], [], []
        shots = []
        for i, sp in enumerate(skeleton):
            scene = get_scene(sp.scene_id)
            lens, frame, angle = chooser.choose(
                i, sp.mother, scene, last_lens, last_frame, last_angle)
            last_lens, last_frame, last_angle = chooser._last_lens, chooser._last_frame, chooser._last_angle
            lighting = scene.lighting[i % len(scene.lighting)]
            effect = None
            if sp.mother != "ENVIRONMENT_REACTION" and sp.state == "REACTING":
                effect = scene.effects[i % len(scene.effects)]
            aspect = "3:2" if frame in ("full_body", "environmental_portrait") else "4:5"
            sp.wardrobe_id = assigned[i]
            sp.lens = lens
            sp.frame = frame
            sp.angle = angle
            sp.lighting = lighting
            sp.effect = effect
            sp.aspect = aspect
            sp.key = "%02d" % (i + 1)
            sp.seed = seed_base + i
            shots.append(sp)
        return AlbumPlan(
            project_name=project_name, domain=domain.id, character=domain.character,
            count=count, shots=shots)
