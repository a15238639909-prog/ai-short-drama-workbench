"""Positive visible descriptions at the image prompt boundary.

Backend instructions stay with Qwen. These pure functions neither call a model
nor discard arbitrary negated facts. Quoted wording and MOSS text fields are
literal on-image copy and remain byte-for-byte intact.
"""
import copy
import json
import re


POSITIVE_OUTPUT_RULE = (
    '\n【最终画面正文】交付给生图模型的内容全部使用正面、具体、可见的描述：'
    '主体外观与服装、动作表情、空间构图、材质、光源、色彩和影像质感。'
    '逐字上图的文字放在引号或实际text字段中。'
    '规则、检查过程、状态字段和负面词清单由后台处理。')

_QUOTED = re.compile(r'''“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"(?:\\.|[^"\\])*"|(?<!\w)'(?:\\.|[^'\\])*'(?!\w)''', re.S)
_META = re.compile(
    r'Wardrobe continuity|Current wardrobe contract|Identity authority|'
    r'\b(?:wardrobe_state|source_order|source_units|baseline_design|character_id)(?![A-Za-z0-9_])|'
    r'(?:负面|负向|反向)提示词\s*[:：]|negative\s+prompt\s*[:：]|'
    r'(?:输出格式|格式要求|遵守规则|必须遵守|程序规则|后台规则)\s*[:：]?', re.I)
_INSTRUCTION = re.compile(
    r'禁止|不得|不允许|不许|不要|不能|必须|务必|应当|只允许|而非|而不是|'
    r'避免(?:出现|产生|使用|添加|绘制|画出|多余|文字|水印|畸形|变形)|'
    r'(?:不出现|不包含|不添加|不绘制|不生成|不能出现)|'
    r'\b(?:must|should|do\s+not|never|cannot|avoid\s+(?:text|watermarks?|extra|distort\w*|draw\w*|add\w*)|'
    r'(?:no|without)\s+(?:text|watermarks?|extra\s+\w+|borders?|captions?|logos?|people|characters?|background))\b', re.I)
_EXCLUSION = re.compile(
    r'(?:无|没有)多余陈设|'
    r'(?:整洁|干净)无尘|(?:无|没有|不含)(?:任何|多余)?(?:(?:杂乱|散落)?的?杂物(?:堆积|成堆)?|杂乱的?陈设)|'
    r'(?:无|没有|不含)(?:任何|可读|具体)?(?:主要|次要|其他|无关|多余|可见|活动|出镜)?(?:人物|主角|人群|水印|字幕|边框|文字|标注|签名|logo|强烈(?:的)?硬阴影)|'
    r'(?:无|没有|不带|未见)(?:明显)?(?:积灰|积尘|污渍|锈迹|磨损|裂纹|破损)|'
    r'不过度磨皮|(?:无|没有)(?:明显|强烈(?:的)?)?(?:硬)?阴影|'
    r'无(?:镜头)?移动|不刺眼|不遮挡.{0,8}主体|未(?:入画|出镜)', re.I)


def _protect(text):
    quoted = []
    def keep(match):
        quoted.append(match.group())
        return '\x00Q%d\x00' % (len(quoted) - 1)
    return _QUOTED.sub(keep, text), quoted


def _restore(text, quoted):
    for index, value in enumerate(quoted):
        text = text.replace('\x00Q%d\x00' % index, value)
    return text


def assert_positive_description(text, *, label='画面描述'):
    """Validate generated visual prose, without changing dialogue or names."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError(label + '须为具体的正面画面描述')
    visible, _ = _protect(text)
    if _META.search(visible) or _INSTRUCTION.search(visible) or _EXCLUSION.search(visible):
        raise ValueError(label + '含程序规则或反向指令，请改为主体、动作、空间与光影的正面描述')
    return text


def normalize_positive_description(text, *, label='画面描述'):
    """Normalize only unambiguous visual exclusions in newly written prompts."""
    if not isinstance(text, str):
        raise ValueError(label + '须为文字')
    value, quoted = _protect(text.strip())
    # A labelled negative appendix is separate from the positive picture, never
    # a bag of tags to be folded into the main conditioning text.
    value = re.split(r'(?im)^\s*(?:negative\s+prompt|负面提示词|负向提示词|反向提示词)\s*[:：]', value, maxsplit=1)[0]
    value = re.sub(r'(?im)^\s*(?:positive\s+prompt|正面提示词|正向提示词)\s*[:：]\s*', '', value)
    replacements = (
        (r'(?:无|没有)多余陈设', '陈设精简有序'),
        (r'(?:整洁|干净)无尘', '干净整洁'),
        (r'(?:无|没有|不含)(?:任何|多余)?(?:(?:杂乱|散落)?的?杂物(?:堆积|成堆)?|杂乱的?陈设)', '陈设井然有序'),
        (r'(?:而非|而不是)[^，。；;！!？?\n]*', ''),
        (r'(?:无|没有|不含|不出现)(?:任何)?(?:其他|无关|多余)人物', '画面集中呈现已指定主体'),
        (r'(?:无|没有|不含|不出现)(?:任何|具体)?(?:主要|次要|可见|活动|出镜)?(?:人物|主角|人群)', '空置的空间与静物'),
        (r'(?:洁净|干净)?(?:无|没有|不带|未见)(?:明显)?(?:积灰|积尘|污渍)', '表面洁净'),
        (r'(?:无|没有|不带|未见)(?:明显)?(?:锈迹|磨损|裂纹|破损)', '构件表面完整'),
        (r'(?:无|没有|不含)(?:可读)?(?:水印|字幕|文字|标注|签名|标志|logo)', '整洁的画面'),
        (r'(?:无|没有|不含)边框', '完整画幅'),
        (r'(?:无|没有|不含)强烈(?:的)?硬阴影', '柔和漫射光与平缓的阴影边缘'),
        (r'(?:无|没有)(?:明显|强烈(?:的)?)?(?:硬)?阴影', '均匀光照与柔和的阴影边缘'),
        (r'无(?:镜头)?移动', '画面稳定'),
        (r'不刺眼', '亮度柔和'),
        (r'不遮挡((?:前景|中景|后景|画面|主要|人物)?主体)', r'\1清晰可见'),
        (r'不做(?:过度)?磨皮|不过度磨皮', '保留自然肤质与毛孔'),
        (r'(?:不扣|未扣)(?:纽扣|扣子)', '纽扣敞开'),
        (r'\b(?:no|without)\s+(?:watermarks?|captions?|logos?|text)\b', 'clean composition'),
        (r'\b(?:no|without)\s+borders?\b', 'full-frame composition'),
        (r'\b(?:no|without)\s+(?:people|characters?)\b', 'unoccupied environment'),
    )
    for pattern, replacement in replacements:
        value = re.sub(pattern, replacement, value, flags=re.I)
    value = re.sub(r'([，,；;])\s*\1+', r'\1', value).strip(' \t\r\n，,；;')
    result = _restore(value, quoted)
    return assert_positive_description(result, label=label)


def image_prompt_content(prompt, *, normalize=False):
    """Check plain image text or the descriptive fields of native MOSS layouts.

    Native bounding-box keys are model conditioning, not backend policy. Actual
    `text` strings are protected, including negative wording on signs or bubbles.
    """
    value = str(prompt or '').strip()
    check = normalize_positive_description if normalize else assert_positive_description
    if value.startswith('{'):
        try:
            draft = json.loads(value)
        except ValueError:
            return check(value)
        if isinstance(draft, dict) and _META.search(' '.join(str(key) for key in draft)):
            raise ValueError('画面描述含后台状态字段，请使用具体正面画面描述')
        if isinstance(draft, dict) and set(draft) == {'caption'}:
            result = {'caption': check(draft['caption'])}
            return json.dumps(result, ensure_ascii=False, separators=(',', ':')) if normalize else value
        if isinstance(draft, dict) and {'summary', 'items'} <= draft.keys():
            result = copy.deepcopy(draft)
            summary = result.get('summary')
            if isinstance(summary, dict):
                for key in ('caption',):
                    if key in summary:
                        summary[key] = check(summary[key], label='布局总体描述')
                for key in ('meta', 'palette'):
                    if isinstance(summary.get(key), list):
                        summary[key] = [check(item, label='布局总体描述') for item in summary[key]]
            for item in result.get('items') or []:
                if not isinstance(item, dict):
                    continue
                if 'caption' in item:
                    item['caption'] = check(item['caption'], label='布局画面描述')
                for key in ('tags', 'palette'):
                    if isinstance(item.get(key), list):
                        item[key] = [check(tag, label='布局画面描述') for tag in item[key]]
            return json.dumps(result, ensure_ascii=False, separators=(',', ':')) if normalize else value
    return check(value)
