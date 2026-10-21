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


def scope_prompt(prompt, segment, director=None):
    """Keep set references from restoring an object's obsolete staging."""
    if (segment.get('plan') or {}).get('flow') != 'source-v1':
        return prompt
    fields = ('subject_definitions', 'summary', 'retention_analysis', 'detailed_description',
              'overall_soundscape', 'non_diegetic_music')
    headers = list(re.finditer(r'(?m)^(' + '|'.join(fields) + r'):[ \t]*', prompt))
    if [match[1] for match in headers] != list(fields):
        raise ValueError('场景引用范围校验失败：视频提示词六字段不完整')
    sections = {match[1]: prompt[match.end():headers[index+1].start() if index+1 < len(headers) else len(prompt)].strip('\n')
                for index, match in enumerate(headers)}
    marker = 'Reference scope (' + REFERENCE_SCOPE_VERSION + '):'
    if marker in sections['detailed_description']:
        return prompt
    environments = re.findall(r'<Subject\s+(\d+)>[^\n]*the environment from <Picture\s+\d+>',
                              sections['subject_definitions'])
    if len(environments) != 1:
        raise ValueError('场景引用范围校验失败：缺少唯一场景主体定义')
    scene_number = environments[0]
    transition = (director or {}).get('scene_transition') or {}
    arrival_rule = (' This environment picture defines the arrival destination only. '
                    'Begin at the established departure location when the written action starts there, '
                    'retaining its existing door and architecture from preceding video evidence until the characters move away. '
                    'Do not redesign the departure shop or replace its door with architecture from the destination. '
                    'Descriptive wording does not authorize rebuilding an established location without a written alteration action.') if transition else ''
    people = dict(re.findall(r'<Subject\s+(\d+)> is ([^\n,]+), the character from <Picture\s+\d+>', sections['subject_definitions']))
    from .continuation_assets import source_narration
    declared_shirts = set()
    for sentence in re.split(r'[。！？!?\n]', source_narration(segment.get('source_text') or '')):
        owners = [number for number, name in people.items() if name in sentence]
        if len(owners) != 1:
            continue
        if (re.search(r'(?:身穿|穿着|衣着)[^，,]{0,16}衬衫', sentence)
                or re.search(r'衬衫(?:的)?(?:领口|衣领|袖口|前襟|衣襟|纽扣)', sentence)):
            # Holding a loose shirt is not a worn wardrobe state.
            if not re.search(r'(?:拿|握|拎|提|捏|抱|没有|没|未|不|脱下|脱掉)[^，,]{0,10}衬衫', sentence):
                declared_shirts.add(owners[0])
    shirt_rule = ' Current source wardrobe uses a collared button-up shirt with a front placket (衬衫); keep its declared color and reference face/hair identity.'
    object_rule = ('Movable story objects keep their declared quantity and latest location or holder from the confirmed source and preceding state. '
                   'The same continuing object keeps its physical shape, color, material and markings from preceding video evidence unless a written action changes or replaces it. '
                   'A stored object remains inside its closed container until a written action opens it or removes it. '
                   'Continue completed transfers using the same physical objects; do not duplicate objects or restore their earlier staging from a reference image.')
    wardrobe_rule = ('Keep the latest confirmed garment type, collar, color and coverage from the source and preceding video. '
                     'Sheet clothing supplies a baseline when no current wardrobe state is specified; it must not replace an already established garment.')
    definitions = []
    for line in sections['subject_definitions'].splitlines():
        if re.match(r'<Subject\s+' + scene_number + r'>', line):
            line += ' Retain fixed architecture, fixed furniture layout and lighting only; movable objects follow the current story state.' + arrival_rule
        elif re.match(r'<Subject\s+\d+>[^\n]*the character from <Picture\s+\d+>', line):
            line += ' Retain identity, face and hair. ' + wardrobe_rule + ' Pose and hand occupancy follow the current action.'
            number = re.match(r'<Subject\s+(\d+)>', line)[1]
            if number in declared_shirts:
                line += shirt_rule
        definitions.append(line)
    sections['subject_definitions'] = '\n'.join(definitions)
    retained = []
    for line in sections['retention_analysis'].splitlines():
        if re.match(r'<Subject\s+' + scene_number + r'>', line):
            line = ('<Subject %s> (appears in [Shot 1]): partially_preserved - fixed architecture, fixed furniture layout and lighting only. ' % scene_number) + arrival_rule + object_rule
        elif re.match(r'<Subject\s+\d+>', line):
            line += ' ' + wardrobe_rule + ' Reference identity does not restore an old pose or an earlier held object.'
            number = re.match(r'<Subject\s+(\d+)>', line)[1]
            if number in declared_shirts:
                line = line.replace('fully_preserved', 'partially_preserved') + shirt_rule
        retained.append(line)
    sections['retention_analysis'] = '\n'.join(retained)
    note = marker + ' Character references define identity; the set reference defines fixed room structure and lighting. ' + arrival_rule + wardrobe_rule + ' ' + object_rule
    # These notes are staging constraints, not extra spoken lines. Avoid adding
    # quoted dialogue from a director's shorthand to an already voiced prompt.
    start = str((director or {}).get('start_state') or '').strip()
    if start and 'Continuity handoff:' not in sections['detailed_description']:
        start = re.sub(r'[‘“\"「『][^’”\"」』\n]*[’”\"」』]', '', start)
        note += ' Beginning state constraints (not additional dialogue): ' + start
    sections['detailed_description'] = note + '\n' + sections['detailed_description']
    return '\n\n'.join(field + ':\n' + sections[field] for field in fields)


def handoff_prompt(prompt, frame_number, segment, previous, director=None, appearance_number=None):
    """Bind frame evidence to the current shot without reviving departed people."""
    director = director or {}
    prior = previous['segment']
    changed = bool(segment.get('scene') and prior.get('scene') and segment['scene'] != prior['scene'])
    changed = changed or bool((segment.get('seam') or {}).get('scene_changed'))
    context = {
        'previous_episode': previous['episode'], 'previous_segment': previous['segment_no'],
        'current_location': segment.get('scene'),
        'planned_start_state': director.get('start_state') or '',
        'planned_end_state': director.get('target_state') or segment.get('tail') or '',
        'declared_cast': director.get('cast') if 'cast' in director else segment.get('chars', []),
    }
    note = ('\nContinuity handoff: <Picture %d> is the actual final frame of the preceding video. '
            'Use it to carry over ongoing body support, held objects, wardrobe and spatial relationships '
            'where the current action continues them. Character sheets define identity. '
            'Apply explicit entrances, departures and time changes in this shot; reference availability '
            'does not require a person to appear. Do not duplicate a character or invent bystanders. ' % frame_number)
    transition = director.get('scene_transition') or {}
    if transition:
        note += ('This shot shows a written departure-to-arrival transition. The current environment picture is the destination, '
                 'not a replacement for the opening departure location. Begin with the preceding video state, '
                 'keep its established door, doorway, architecture and ongoing object appearance, '
                 'then show the written movement into the destination. Do not close an already-open door or redesign the departure location. '
                 'An unchanged physical entity keeps its observed appearance; a descriptive adjective is not an action replacing that entity. ')
    elif changed:
        note += ('This shot changes location: use the current environment reference for its background, '
                 'and establish the transition using the current action. Do not copy the previous room. ')
    else:
        note += ('Keep the established room and props; a camera cut may change framing, '
                 'but must not reset completed actions. ')
    note += 'Declared shot context: ' + json.dumps(context, ensure_ascii=False) + '\n'
    appearance_note = ''
    if appearance_number is not None:
        appearance_note = ('<Picture %d> is an earlier historical frame from the preceding video, used only as appearance evidence '
                           'for already-confirmed continuing characters, objects and departure architecture. '
                           'It is not a starting frame and does not establish current positions, poses, hand occupancy, '
                           'ownership, object count, container open/closed state or unfinished actions. '
                           'The immediately preceding final frame and current written actions control all those states. ' % appearance_number)
    fields = ('subject_definitions', 'summary', 'retention_analysis', 'detailed_description',
              'overall_soundscape', 'non_diegetic_music')
    headers = list(re.finditer(r'(?m)^(' + '|'.join(fields) + r'):[ \t]*', prompt))
    if not headers:
        # The approved legacy POV/time-block writer has no six-section wrapper.
        # Add only reference transport; keep its original story, camera and voice
        # instructions byte-for-byte rather than pretending it is malformed JSON.
        return prompt + '\n\n' + note.strip() + ('\n' + appearance_note if appearance_note else '')
    if [match[1] for match in headers] != list(fields):
        raise ValueError('续接提示词六字段顺序无效，已保留分镜，需修复后生成')
    sections = {match[1]: prompt[match.end():headers[index+1].start() if index+1 < len(headers) else len(prompt)].strip('\n')
                for index, match in enumerate(headers)}
    sections['subject_definitions'] += ('\n<Picture %d> is the actual final frame of the immediately preceding video; '
        'it is the continuity anchor for [Shot 1], while the current character and environment subjects retain their identities.' % frame_number)
    sections['retention_analysis'] += ('\n<Picture %d> ([Shot 1] continuity): weak_reference - '
        'carry over ongoing action and object ownership; apply declared entrances, departures and location changes.' % frame_number)
    if appearance_number is not None:
        sections['subject_definitions'] += '\n' + appearance_note
        sections['retention_analysis'] += ('\n<Picture %d> ([Shot 1] historical appearance): partially_preserved - '
            'physical shape, color, material and markings of continuing entities only; never restore earlier staging or replay actions.' % appearance_number)
        note += '\n' + appearance_note
    sections['detailed_description'] = note.strip() + '\n' + sections['detailed_description']
    return '\n\n'.join(field + ':\n' + sections[field] for field in fields)


def validate_prompt_references(prompt, images):
    referenced = set(int(number) for number in re.findall(r'<Picture\s+(\d+)>', str(prompt)))
    if referenced - set(range(1, len(images) + 1)):
        raise ValueError('最终视频提示词含未绑定的图片编号，已暂停生成')
    if len(images) > 9:
        raise ValueError('最终视频参考图超过9张，已暂停生成')
