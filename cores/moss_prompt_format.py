"""Validate local planner drafts and serialize MOSS's native layout coordinates.

Protocol reference: OpenMOSS/MOSS-Image skills/moss-image-prompt-enhancer.
This adapter is independently implemented for the workbench's local Qwen writer.
"""
import json
import math


def _need(condition, message):
    if not condition:
        raise ValueError('Moss提示词格式：' + message)


def _object(value, allowed, required, name):
    _need(isinstance(value, dict), name + '须为对象')
    _need(not (value.keys() - allowed), name + '含有未支持字段')
    _need(not (required - value.keys()), name + '缺少必填字段')


def _words(value, name):
    _need(isinstance(value, str) and bool(value.strip()), name + '须为非空文字')


def _string_list(value, name):
    _need(isinstance(value, list), name + '须为文字数组')
    for item in value:
        _words(item, name)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _need(key not in result, 'JSON字段重复：' + key)
        result[key] = value
    return result


def _bad_number(value):
    raise ValueError('Moss提示词格式：JSON数值无效：' + value)


def parse_draft(value):
    """Accept strict JSON, optionally surrounded by a single Markdown fence."""
    _words(value, 'Qwen输出')
    value = value.strip()
    if value.startswith('```'):
        lines = value.splitlines()
        _need(lines[0].lower() in ('```', '```json') and lines[-1] == '```',
              '代码块未完整结束')
        value = '\n'.join(lines[1:-1])
    try:
        return json.loads(value, object_pairs_hook=_unique, parse_constant=_bad_number)
    except json.JSONDecodeError as error:
        raise ValueError('Moss提示词格式：Qwen未返回完整JSON，请重新生成') from error


def _canvas(width, height):
    _need(all(type(v) is int and v > 0 and v % 16 == 0 for v in (width, height)),
          '画布宽高须为16的正整数倍')


def _box(box, width, height):
    _need(isinstance(box, list) and len(box) == 4, 'bbox须为四个坐标')
    _need(all(type(v) in (int, float) and math.isfinite(v) for v in box),
          'bbox须为有限数值')
    left, top, right, bottom = box
    _need(0 <= left < right <= width and 0 <= top < bottom <= height,
          '布局坐标超出画布或区域为空')
    return [math.floor(round(top, 9)) - height // 2,
            math.floor(round(left, 9)) - width // 2,
            math.ceil(round(bottom, 9)) - height // 2,
            math.ceil(round(right, 9)) - width // 2]


def compile_draft(draft, width, height):
    """Return (positive_prompt, format); never clip invalid planner output."""
    _canvas(width, height)
    _need(isinstance(draft, dict), '草稿须为JSON对象')
    if 'caption' in draft:
        _object(draft, {'caption'}, {'caption'}, '单图描述')
        _words(draft['caption'], 'caption')
        return draft['caption'].strip(), 'caption'
    keys = {'width', 'height', 'summary', 'items', 'bbox_coord_space', 'bbox_format'}
    _object(draft, keys, keys, '布局草稿')
    _canvas(draft['width'], draft['height'])
    _need((draft['width'], draft['height']) == (width, height), '布局画布与本次出底尺寸不一致')
    _need(draft['bbox_coord_space'] == 'abs' and draft['bbox_format'] == 'xyxy',
          '草稿坐标须为abs/xyxy')
    summary = draft['summary']
    _object(summary, {'caption', 'canvas', 'meta', 'palette'}, {'caption'}, 'summary')
    _words(summary['caption'], 'summary.caption')
    if 'canvas' in summary:
        _need(summary['canvas'] in ('single', 'multi_panel', 'collage'), 'canvas类型无效')
    for key in ('meta', 'palette'):
        if key in summary:
            _string_list(summary[key], 'summary.' + key)
    _need(isinstance(draft['items'], list) and bool(draft['items']), 'items须为非空平面数组')
    items = []
    for item in draft['items']:
        _object(item, {'label', 'caption', 'bbox', 'text', 'tags', 'palette'},
                {'label', 'caption', 'bbox'}, 'item')
        _need(item['label'] in ('object', 'character', 'text'), 'item.label类型无效')
        _words(item['caption'], 'item.caption')
        if item['label'] == 'text':
            _need('text' in item, '文字区域缺少实际文字')
        if 'text' in item:
            _words(item['text'], 'item.text')
        for key in ('tags', 'palette'):
            if key in item:
                _string_list(item[key], 'item.' + key)
        items.append(dict(item, bbox=_box(item['bbox'], width, height)))
    result = {'summary': summary, 'items': items,
              'bbox_coord_space': 'abs_centralized', 'bbox_format': 'yxyx'}
    return json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False), 'layout'


def rescale_compiled(prompt, width, height, target_width, target_height):
    """Scale recognized native layout conditioning for a second sampling stage.

    Captions and unrelated JSON pass through byte-for-byte. Native layouts must
    fit the first-stage canvas; their text and other visual fields are preserved.
    """
    value = str(prompt)
    if not value.lstrip().startswith('{'):
        return value
    try:
        draft = json.loads(value)
    except ValueError:
        return value
    if not isinstance(draft, dict) or not {'summary', 'items'} <= draft.keys():
        return value
    draft = parse_draft(value)
    _canvas(width, height)
    _canvas(target_width, target_height)
    _need(draft.get('bbox_coord_space') == 'abs_centralized' and draft.get('bbox_format') == 'yxyx',
          '请先生成本工作台支持的布局格式')
    _need(isinstance(draft['items'], list) and bool(draft['items']), '布局区域须为非空数组')
    converted = []
    for item in draft['items']:
        _need(isinstance(item, dict), '布局区域须为对象')
        box = item.get('bbox')
        _need(isinstance(box, list) and len(box) == 4 and
              all(type(v) in (int, float) and math.isfinite(v) for v in box), 'bbox无效')
        top, left, bottom, right = box
        _box([left + width / 2, top + height / 2,
              right + width / 2, bottom + height / 2], width, height)
        converted.append(dict(item, bbox=[
            math.floor(round(top * target_height / height, 9)),
            math.floor(round(left * target_width / width, 9)),
            math.ceil(round(bottom * target_height / height, 9)),
            math.ceil(round(right * target_width / width, 9))]))
    result = dict(draft, items=converted)
    return json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
