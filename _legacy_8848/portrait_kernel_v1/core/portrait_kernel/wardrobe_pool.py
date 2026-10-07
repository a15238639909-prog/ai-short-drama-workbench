# -*- coding: utf-8 -*-
"""V1.0 服装池：REAL_WORLD 与 SPACE_LIFE 独立，描述必须明确可生成。"""

from .models import WardrobeItem

REAL_WARDROBE = {
    "WT": WardrobeItem("WT", "白T", "白色圆领T恤", "REAL_WORLD"),
    "GK": WardrobeItem("GK", "灰针织衫", "浅灰针织衫", "REAL_WORLD"),
    "MK": WardrobeItem("MK", "米白开衫", "米白宽松针织开衫", "REAL_WORLD"),
    "BC": WardrobeItem("BC", "白衬衫", "白色衬衫", "REAL_WORLD"),
    "NJ": WardrobeItem("NJ", "牛仔外套", "浅蓝牛仔外套", "REAL_WORLD"),
    "WD": WardrobeItem("WD", "羊毛大衣", "米白羊毛大衣和厚针织围巾", "REAL_WORLD"),
    "DR": WardrobeItem("DR", "简单连衣裙", "浅灰色圆领连衣裙", "REAL_WORLD"),
    "SP": WardrobeItem("SP", "运动T恤", "白色运动T恤和浅灰运动长裤", "REAL_WORLD"),
    "LJ": WardrobeItem("LJ", "深色轻外套", "深蓝薄外套", "REAL_WORLD"),
}

SPACE_WARDROBE = {
    "ST": WardrobeItem("ST", "白色棉T", "白色棉T恤", "SPACE_LIFE"),
    "GK": WardrobeItem("GK", "灰色针织衫", "灰色针织衫", "SPACE_LIFE"),
    "WG": WardrobeItem("WG", "深灰工装裤", "深灰工装裤和白色T恤", "SPACE_LIFE"),
    "JW": WardrobeItem("JW", "实用工作外套", "深灰实用型工作外套", "SPACE_LIFE"),
    "TR": WardrobeItem("TR", "舱内训练服", "浅灰舱内训练服", "SPACE_LIFE"),
    "IL": WardrobeItem("IL", "普通内层服", "白色贴身内层服", "SPACE_LIFE"),
    "PL": WardrobeItem("PL", "宇航准备内层服", "白色内层服，旁边放一件厚重宇航服", "SPACE_LIFE"),
}


def get_wardrobe_pool(domain: str):
    return REAL_WARDROBE if domain == "REAL_WORLD" else SPACE_WARDROBE


def select_wardrobes(shots, domain, preferred_map, k=6):
    """给整本选服装：先定K套（默认6），再按场景偏好+最少使用分配，单套2-3次。"""
    pool = get_wardrobe_pool(domain)
    used_scenes = {shot.scene_id for shot in shots}
    freq = {}
    for scene_id in used_scenes:
        for w in preferred_map.get(scene_id, []):
            if w in pool:
                freq[w] = freq.get(w, 0) + 1
    chosen = sorted(pool, key=lambda w: (-freq.get(w, 0), w))[:k]
    usage = {wid: 0 for wid in pool}
    target_per = (len(shots) + k - 1) // k
    assigned = []
    for shot in shots:
        scene_id = shot.scene_id
        pref = [w for w in preferred_map.get(scene_id, []) if w in chosen]
        candidates = pref + [w for w in chosen if w not in pref]
        best = None
        for w in candidates:
            if usage[w] >= target_per:
                continue
            if best is None or usage[w] < usage[best]:
                best = w
        if best is None:
            best = min(chosen, key=lambda w: usage[w])
        usage[best] += 1
        assigned.append(best)
    distinct = len([w for w in chosen if usage[w] > 0])
    return assigned, distinct
