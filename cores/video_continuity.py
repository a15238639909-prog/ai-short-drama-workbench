"""Video handoff and media checks shared by single-clip and episode generation.

These checks confirm file integrity and declared dependencies, not semantic visual
quality. A rendered frame remains evidence, not an automatically verified story fact.
"""
import hashlib
import json
import math
import os
import re
import subprocess
from pathlib import Path

REFERENCE_SCOPE_VERSION = 'story-state-v2'


def prepare_handoff(previous):
    """Extract historical appearance evidence; this is never a new start-state anchor."""
    video = Path(str((previous.get('segment') or {}).get('video') or ''))
    if not video.is_absolute():
        video = Path(__file__).resolve().parent.parent / video
    stamp = media_stamp(video)
    if not stamp:
        raise ValueError('上一段视频不存在，不能准备衔接外观参考')
    key = hashlib.sha256(json.dumps({'video': stamp, 'time': 0.5, 'version': 'early-appearance-v1'}, sort_keys=True).encode('utf-8')).hexdigest()[:12]
    frame = video.with_name(video.stem + '__appearance_' + key + '.png')
    if not frame.is_file():
        pending = frame.with_name(frame.stem + '.pending.png')
        result = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-v', 'error', '-ss', '0.5', '-i', str(video),
                                 '-frames:v', '1', '-update', '1', '-y', str(pending)],
                                capture_output=True, timeout=60)
        if result.returncode or not pending.is_file() or not pending.stat().st_size:
            raise ValueError('衔接外观帧提取失败，视频和中间文件均已保留')
        from PIL import Image
        with Image.open(pending) as rendered:
            rendered.verify()
        os.replace(pending, frame)
    return str(frame.resolve())


def media_stamp(path):
    p = Path(str(path or ''))
    if not path or not p.is_file():
        return None
    s = p.stat()
    return {'path': str(p.resolve()), 'bytes': s.st_size, 'mtime_ns': s.st_mtime_ns}


def validate_video(path, expected_seconds=None):
    """Check the actual stream and decode it before adopting a generated file."""
    if not media_stamp(path):
        raise ValueError('视频文件不存在，需补生成')
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
    if result.returncode:
        raise ValueError('视频文件无法读取：' + result.stderr[-300:])
    try:
        data = json.loads(result.stdout)
        stream = next(s for s in data.get('streams', []) if s.get('codec_type') == 'video')
        seconds = float(stream.get('duration') or data.get('format', {}).get('duration') or 0)
        width, height = int(stream.get('width') or 0), int(stream.get('height') or 0)
        if not math.isfinite(seconds) or seconds <= 0 or width <= 0 or height <= 0:
            raise ValueError()
    except (ValueError, TypeError, StopIteration, KeyError):
        raise ValueError('视频没有有效画面或时长，需补生成') from None
    if expected_seconds is not None:
        expected = float(expected_seconds)
        # H3 frame quantization and rounded generation duration differ slightly
        # from planned seconds. This still catches missing/truncated clips.
        if abs(seconds - expected) > max(1.25, expected * .025):
            raise ValueError('视频时长异常：应约 %.2f 秒，实际 %.2f 秒' % (expected, seconds))
    decoded = subprocess.run(
        ['ffmpeg', '-hide_banner', '-v', 'error', '-xerror', '-i', str(path),
         '-map', '0:v:0', '-f', 'null', '-'], capture_output=True, timeout=180)
    if decoded.returncode:
        raise ValueError('视频画面解码失败，原文件保留，需补生成')
    return dict(media_stamp(path), duration=seconds, width=width, height=height,
                audio=any(s.get('codec_type') == 'audio' for s in data.get('streams', [])),
                check='decoded', visual_quality='not_reviewed')


def previous_segment(story, timeline, episode, scene_no, segment_no):
    """Find the immediate predecessor, including scene/episode boundaries.

    Do not jump over an ungenerated segment to a convenient older frame.
    """
    scenes = list((timeline or {}).get('scenes') or [])
    current = next((s for s in scenes if int(s.get('no') or 1) == int(scene_no)), None)
    if current is None:
        return None
    current_index = scenes.index(current)
    rows = sorted(current.get('segments') or [], key=lambda s: int(s.get('no') or 0))
    predecessor = next((s for s in rows if int(s.get('no') or 0) == int(segment_no) - 1), None)
    previous_scene, previous_episode = current, int(episode)
    if predecessor is None:
        if int(segment_no) > 1 and (not rows or int(rows[0].get('no') or 0) != int(segment_no)):
            return None
        if current_index:
            previous_scene = scenes[current_index - 1]
            previous_rows = sorted(previous_scene.get('segments') or [], key=lambda s: int(s.get('no') or 0))
            predecessor = previous_rows[-1] if previous_rows else None
            if int(segment_no) > 1 and (not predecessor or int(predecessor.get('no') or 0) != int(segment_no) - 1):
                return None
        elif int(segment_no) == 1 and int(episode) > 1:
            prior = next((e for e in ((story.get('saga') or {}).get('episodes') or [])
                          if int(e.get('no') or 0) == int(episode) - 1), {})
            prior_timeline = prior.get('timeline') or {}
            previous_scenes = prior_timeline.get('scenes') or []
            if not previous_scenes:
                return None
            previous_scene = previous_scenes[-1]
            previous_rows = sorted(previous_scene.get('segments') or [], key=lambda s: int(s.get('no') or 0))
            predecessor = previous_rows[-1] if previous_rows else None
            expected = len(((prior_timeline.get('slicing') or {}).get('slices') or []))
            all_previous = [g for s in previous_scenes for g in (s.get('segments') or [])]
            if expected and sorted(int(g.get('no') or 0) for g in all_previous) != list(range(1, expected + 1)):
                return None
            if any(g.get('stale_video') or not media_stamp(g.get('video')) for g in all_previous):
                return None
            previous_episode -= 1
    if not predecessor or predecessor.get('stale_video') or not media_stamp(predecessor.get('video')):
        return None
    return {'episode': previous_episode, 'scene_no': int(previous_scene.get('no') or 1),
            'segment_no': int(predecessor.get('no') or 0), 'segment': predecessor}


def segment_signature(segment, parameters, previous=None, references=None):
    payload = {k: segment.get(k) for k in ('prompt', 'seconds', 'chars', 'scene', 'refs', 'source_text', 'seam')}
    payload['parameters'] = parameters
    payload['references'] = [media_stamp(p) for p in (references or [])]
    if previous:
        payload['handoff_version'] = 'h3-handoff-v2'
        payload['previous'] = {k: previous[k] for k in ('episode', 'scene_no', 'segment_no')}
        payload['previous']['video'] = media_stamp(previous['segment'].get('video'))
        payload['previous']['frame'] = media_stamp(previous['segment'].get('end_frame'))
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode('utf-8')).hexdigest()


def _prompt_sections(prompt, *, label):
    fields = ('subject_definitions', 'summary', 'retention_analysis', 'detailed_description',
              'overall_soundscape', 'non_diegetic_music')
    headers = list(re.finditer(r'(?m)^(' + '|'.join(fields) + r'):[ \t]*', prompt))
    if not headers:
        return fields, None
    if [match[1] for match in headers] != list(fields):
        raise ValueError(label + '：视频提示词六字段不完整或顺序无效')
    return fields, {match[1]: prompt[match.end():headers[index + 1].start() if index + 1 < len(headers)
                                   else len(prompt)].strip('\n') for index, match in enumerate(headers)}


def scope_prompt(prompt, segment, director=None):
    """Validate reference scope; present concrete states without policy prose."""
    if (segment.get('plan') or {}).get('flow') != 'source-v1':
        return prompt
    fields, sections = _prompt_sections(prompt, label='场景引用范围校验失败')
    if sections is None:
        raise ValueError('场景引用范围校验失败：视频提示词六字段不完整')
    environments = re.findall(r'<Subject\s+(\d+)>[^\n]*the environment(?: from <Picture\s+\d+>)?(?=[.,\s]|$)',
                              sections['subject_definitions'])
    if len(environments) != 1:
        raise ValueError('场景引用范围校验失败：缺少唯一场景主体定义')
    # Compatibility reading: discard only automatically generated policy lines.
    # Original shot prose, source dialogue and frame labels remain untouched.
    prefixes = ('Reference scope (', 'Continuity handoff:', 'Declared shot context:',
                'Identity authority (', 'Wardrobe continuity (', 'Current wardrobe contract:')
    for field in ('retention_analysis', 'detailed_description'):
        sections[field] = '\n'.join(line for line in sections[field].splitlines()
            if not line.lstrip().startswith(prefixes))
    people = dict(re.findall(r'<Subject\s+(\d+)> is ([^\n,]+), the character from <Picture\s+\d+>',
                             sections['subject_definitions']))
    from .continuation_assets import source_narration
    shirt_people = set()
    for sentence in re.split(r'[。！？!?\n]', source_narration(segment.get('source_text') or '')):
        owners = [number for number, name in people.items() if name in sentence]
        if len(owners) == 1 and (re.search(r'(?:身穿|穿着|衣着)[^，,]{0,16}衬衫', sentence)
                or re.search(r'衬衫(?:的)?(?:领口|衣领|袖口|前襟|衣襟|纽扣)', sentence)):
            if not re.search(r'(?:拿|握|拎|提|捏|抱|没有|没|未|不|脱下|脱掉)[^，,]{0,10}衬衫', sentence):
                shirt_people.add(owners[0])
    definitions = []
    for line in sections['subject_definitions'].splitlines():
        # Remove exact legacy suffix boundaries that this function produced.
        for suffix in (' Retain fixed architecture,', ' Retain identity, face and hair.'):
            line = line.split(suffix, 1)[0]
        number = re.match(r'<Subject\s+(\d+)>', line)
        if number and number[1] in shirt_people and '有翻领、前襟与纽扣的衬衫' not in line:
            line += ' 当前穿着有翻领、前襟与纽扣的衬衫。'
        definitions.append(line)
    sections['subject_definitions'] = '\n'.join(definitions)
    start = str((director or {}).get('start_state') or '').strip()
    if start and start not in sections['detailed_description']:
        start = re.sub(r'[‘“\"「『][^’”\"」』\n]*[’”\"」』]', '', start).strip()
        if start:
            sections['detailed_description'] = '起镜状态：' + start + '\n' + sections['detailed_description']
    return '\n\n'.join(field + ':\n' + sections[field] for field in fields)


def handoff_context(segment, previous, director=None):
    """Internal request evidence, never part of the generator's scene prose."""
    director = director or {}
    return dict(previous_episode=previous['episode'], previous_segment=previous['segment_no'],
        current_location=segment.get('scene'), planned_start_state=director.get('start_state') or '',
        planned_end_state=director.get('target_state') or segment.get('tail') or '',
        declared_cast=director.get('cast') if 'cast' in director else segment.get('chars', []))


def handoff_prompt(prompt, frame_number, segment, previous, director=None, appearance_number=None):
    """Add concise factual frame roles, preserving source camera and dialogue."""
    fields, sections = _prompt_sections(prompt, label='续接提示词六字段顺序无效')
    definition = '<Picture %d>是紧接本段的前一段视频末帧，呈现该时刻的位置、姿态、穿戴和持物。' % frame_number
    prior = previous['segment']
    changed = bool(segment.get('scene') and prior.get('scene') and segment['scene'] != prior['scene'])
    changed = changed or bool((segment.get('seam') or {}).get('scene_changed'))
    retention = '<Picture %d>：%s。' % (frame_number,
        '此前地点中已出现实体的状态参考' if changed else '本段起镜的动态状态参考')
    historical = ('<Picture %d>是此前视频的历史画面，提供已出现实体的形状、色彩、材质和标记参考。' %
                  appearance_number) if appearance_number is not None else ''
    if sections is None:
        return prompt + '\n\n' + '\n'.join(x for x in (definition, historical) if x)
    sections['subject_definitions'] += '\n' + definition
    sections['retention_analysis'] += '\n' + retention
    if historical:
        sections['subject_definitions'] += '\n' + historical
        sections['retention_analysis'] += '\n<Picture %d>：已出现实体的外观参考。' % appearance_number
    return '\n\n'.join(field + ':\n' + sections[field] for field in fields)


def validate_prompt_references(prompt, images):
    referenced = set(int(number) for number in re.findall(r'<Picture\s+(\d+)>', str(prompt)))
    if referenced - set(range(1, len(images) + 1)):
        raise ValueError('最终视频提示词含未绑定的图片编号，已暂停生成')
    if len(images) > 9:
        raise ValueError('最终视频参考图超过9张，已暂停生成')
