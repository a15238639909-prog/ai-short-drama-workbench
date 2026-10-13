"""Transient clothing/visibility facts for character sheets; never edits cards."""
import copy
import re


_TOP = re.compile(r"外套|衬衫|夹克|毛衣|卫衣|针织衫|T恤|T-shirt|上衣|上装|连衣裙|长袍|风衣|大衣", re.I)
_BOTTOM = re.compile(r"长裤|裤子|牛仔裤|长裙|连衣裙|长袍")
_EXPOSED = re.compile(r"露腹|露脐|露腰|露胸|裸胸|赤裸|全裸|半裸|无上装|不穿|透视|透明|网纱|镂空|比基尼|泳衣|文胸|胸罩|敞开|敞着|敞胸|开襟|短款")


def coverage(card, disabled=False):
    """Only ordinary, explicitly covering garments enable the visibility guard."""
    outfit = str((card or {}).get("clothing") or "").strip()
    if disabled or not outfit or _EXPOSED.search(outfit):
        return {}
    torso, legs = bool(_TOP.search(outfit)), bool(_BOTTOM.search(outfit))
    if not torso and not legs:
        return {}
    pieces = [p.strip() for p in re.split(r"[；;，,、。\n]+", outfit)
              if _TOP.search(p) or _BOTTOM.search(p)]
    return {"torso": torso, "legs": legs, "pieces": pieces,
            "outfit": "、".join(pieces), "fixed": bool(card.get("clothing_from_brief"))}


def visible_text(text, guard):
    """Remove only clauses that require anatomy hidden by the selected garments."""
    text = str(text or "")
    if not guard:
        return text
    parts = re.split(r"([，,；;。\n])", text)
    kept = []
    changed = False
    for i in range(0, len(parts), 2):
        clause = parts[i]
        visibility = re.search(r"分界|分块|可见|明显|清晰|清楚|显露|露出|裸露", clause)
        hidden = guard["torso"] and (
            re.search(r"腹肌|腹斜肌|马甲线|胸肌|背肌", clause) or
            (visibility and re.search(r"胸廓|胸膛|肋骨|腹部皮肤|肌肉|肌群", clause)))
        hidden = hidden or (guard["legs"] and re.search(r"腿|臀", clause) and
                            re.search(r"肌肉|肌群|皮肤", clause) and visibility)
        if hidden:
            changed = True
            continue
        kept.append(clause)
        if i + 1 < len(parts):
            kept.append(parts[i + 1])
    if not changed:
        return text
    return "".join(kept).strip("，,；;\n ")


def clothing_fact(guard):
    if not guard:
        return ""
    covered = []
    if guard["torso"]:
        covered.append("上衣衣身完整覆盖胸腹与后背")
    if guard["legs"]:
        covered.append("下装保持完整的布料覆盖")
    return ("各视图中的同一人物均穿着" + guard["outfit"] + "，" + "，".join(covered) +
            "，体型由穿衣后的整体轮廓与衣料自然起伏表现。")


def prepared_facts(facts, guard):
    """Keep Qwen's face/hair facts but reconcile temporary wardrobe/occlusion."""
    if not guard:
        return facts
    out = copy.deepcopy(facts)
    out["appearance"] = {key: cleaned for key, value in facts["appearance"].items()
                         if (cleaned := visible_text(value, guard))}
    if guard["fixed"]:
        fixed = [{"类别": "上装" if _TOP.search(piece) else "下装", "名称": piece}
                 for piece in guard["pieces"]]
        slots = {item["类别"] for item in fixed}
        out["clothing"] = fixed + [item for item in facts["clothing"] if item["类别"] not in slots]
    return out


def final_sheet(sheet, guard):
    if not guard:
        return sheet
    cleaned = visible_text(sheet, guard)
    # Put positive wardrobe facts before anatomy, also when Qwen placed clothes
    # late in an otherwise correct result. Some image workflows ignore negatives.
    fact = clothing_fact(guard)
    return cleaned if cleaned.startswith(fact) else fact + "\n" + cleaned
