"""年龄与内容边界的唯一规则源。

只判断、只报错；不修改年龄、人物卡、服装、比例或提示词。
数字年龄是事实，年龄段是年龄段，未知仍是未知。
文本规则不能证明图片中的人物年龄；参考图的视觉验收另行进行。
"""
import json
import re
import unicodedata


class AgePolicyError(ValueError):
    """内容边界不通过。调用者应展示消息并停止，不改年龄后重试。"""


ADULT_AGE = 18
_DIGITS = dict(zip("零一二三四五六七八九两", (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 2)))
_MINOR_WORDS = re.compile(r"未成年|未满\s*18|婴儿|幼儿|幼童|儿童|小学生|初中生|小男孩|小女孩|男童|女童|十几岁|\b(?:child|children|toddler|preteen|underage|minor)\b", re.I)
_CHILD_LOOK = re.compile(r"(?:幼童|儿童|幼儿|婴儿)(?:般的?|的)?(?:外貌|外观|面容|身体|脸|体态)|(?:外貌|外观|面容)(?:像|如同)(?:幼童|儿童|幼儿)|\bchildlike\s+(?:face|body|appearance)\b", re.I)
_ADULT_WORDS = re.compile(r"成年|成人|中年|老年|老人|老者|\badult\b", re.I)
_AGE_TOKEN = r"(?:\d{1,4}|[零一二三四五六七八九十百两]{1,6})"
_AGES = re.compile(r"(" + _AGE_TOKEN + r")\s*(?:岁|周岁|years?\s*old|[- ]year[- ]old)", re.I)
_AGE_FIELD = re.compile(r"(?<![A-Za-z_])[\"']?(?:age|年龄)[\"']?\s*[:：=]\s*[\"']?(" + _AGE_TOKEN + r")", re.I)
_SENSITIVE = re.compile(r"成人向|极致诱惑|情爱|性化|色情|情色|情趣|性感|性挑逗|性爱|性交|性行为|性器官|生殖器|阴茎|阴道|阴唇|阴蒂|睾丸|肛门|\b(?:sexual|erotic|pornographic|porn|fetish|genitals)\b", re.I)
_NUDITY = re.compile(r"裸体|全裸|赤身|赤裸|一丝不挂|不穿(?:任何)?衣|裸着身体|\b(?:nude|naked|nudity)\b", re.I)


def _text(value):
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False)
    return unicodedata.normalize("NFKC", str(value if value is not None else ""))


def _number(raw):
    raw = _text(raw).strip()
    if raw.isdigit():
        return int(raw)
    if not raw or any(c not in _DIGITS and c not in "十百" for c in raw):
        return None
    total, digit = 0, 0
    for c in raw:
        if c in _DIGITS:
            digit = _DIGITS[c]
        else:
            total += (digit or 1) * (10 if c == "十" else 100)
            digit = 0
    return total + digit


def age_number(value):
    """仅解析明确的单个年龄，不把‘少年’、空值、范围猜成数字。"""
    if isinstance(value, dict):
        value = value.get("age")
    raw = _text(value).strip()
    match = re.fullmatch(r"(" + _AGE_TOKEN + r")\s*(?:周岁|岁|years?\s*old)?", raw, re.I)
    return _number(match.group(1)) if match else None


def age_status(value):
    raw = _text(value).strip()
    n = age_number(value)
    if n is not None:
        return "minor" if n < ADULT_AGE else "adult"
    if _MINOR_WORDS.search(raw) or raw in ("少年", "少女", "青少年"):
        return "minor"
    # 跨成年边界的范围仍需按未成年处理，而不是只取末尾数字。
    nums = re.findall(_AGE_TOKEN, raw)
    if re.search(r"[-~～至到]", raw) and len(nums) == 2:
        values = [_number(x) for x in nums]
        if all(x is not None for x in values):
            return "minor" if min(values) < ADULT_AGE else "adult"
    return "adult" if _ADULT_WORDS.search(raw) else "unknown"


def character_status(card):
    card = card or {}
    visible = " ".join(_text(card.get(k)) for k in (
        "appearance", "appearance_details", "look_full", "identity_anchor"))
    if _CHILD_LOOK.search(visible):
        return "minor"
    status = age_status(card.get("age"))
    if status != "unknown":
        return status
    identity = " ".join(_text(card.get(k)) for k in ("char_type", "identity", "role"))
    if _MINOR_WORDS.search(identity):
        return "minor"
    return "unknown"


def is_minor(card):
    return character_status(card) == "minor"


def sensitive_text(text):
    content = _text(text)
    return bool(_SENSITIVE.search(content) or re.search(
        r"强烈曲线|夸张曲线|巨乳|爆乳|(?:乳房|胸部).{0,12}(?:幻想级|巨大|特别大)", content))
def sensitive_settings(settings):
    settings = settings or {}
    return sensitive_text([settings.get("content_tendencies"), settings.get("custom_content_scale")])


def assert_allowed(*, cards=(), settings=None, text="", media="text"):
    """共用检查：正常内容原样通过；敏感内容年龄不足/不明则明确停止。

    cards 应传实际参与本次生成的人物，不传预设库或其他项目的人物。
    头身比例选项名称不作为人物年龄依据。
    """
    if isinstance(cards, dict):
        cards = (cards,)
    cards = tuple(cards or ())
    content = _text(text)
    card_text = " ".join(_text({k: v for k, v in c.items() if k not in (
        "sheet_body_ratio", "height", "image_prompt", "created", "updated")}) for c in cards)
    sensitive = sensitive_settings(settings) or sensitive_text(content) or sensitive_text(card_text)
    if media in ("image", "video", "image_prompt"):
        sensitive = sensitive or bool(_NUDITY.search(content + card_text))
    if not sensitive:
        return
    for card in cards:
        status = character_status(card)
        name = str(card.get("name") or "该人物")
        if status == "minor":
            raise AgePolicyError("%s：保留原年龄和外观，本次内容涉及成人性化或裸露，已停止生成。正常内容可以继续。" % name)
        if status == "unknown":
            raise AgePolicyError("%s：年龄尚未明确，本次敏感内容暂停；请核实真实年龄，系统不会自动改成成年。" % name)
    # 最终文本再次检查，防止模型输出/手动提示词新增了低龄人物。
    found = [_number(m.group(1)) for pattern in (_AGES, _AGE_FIELD) for m in pattern.finditer(content)]
    if any(n is not None and n < ADULT_AGE for n in found) or _MINOR_WORDS.search(content) or _CHILD_LOOK.search(content):
        raise AgePolicyError("本次敏感内容出现未成年年龄或儿童外观，已停止；人物原设定未修改。")
    if not cards and not any(n is not None and n >= ADULT_AGE for n in found) and not _ADULT_WORDS.search(content):
        raise AgePolicyError("本次敏感内容缺少明确的人物年龄依据，已暂停；普通内容不受影响。")


def assert_model_request(user, *, media="text"):
    """模型入口检查，仅判断本次请求，不修改人物年龄或正文。"""
    assert_allowed(text=user, media=media)

def assert_model_response(request, response):
    # 保留输入中的年龄事实，防止模型只在输出中省略年龄后变更尺度。
    if sensitive_text(response):
        assert_allowed(text=_text(request) + "\n" + _text(response), media="text")
