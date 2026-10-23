"""Checkpointed, entirely local story planning, visual feedback and video continuation."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

from . import runtime_core as runtime, store, runtime_reset
from . import story_video_flow as flow

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / 'outputs' / 'story_video'
KIND = 'story_video'
QUALITY_POLICY_VERSION = 3
ASPECT_RATIOS = ('16:9', '9:16', '1:1', '4:5', '3:4')
_ADMISSION = threading.RLock()
_CHILDREN = {}


def _prompt_builder(inputs):
    """Keep existing manifests on their original director contract."""
    from . import story_video_prompts as legacy
    if 'director_profile' not in inputs:
        return legacy
    from . import story_video_mode_prompts as modes
    if inputs['director_profile'] != modes.PROFILE:
        raise RuntimeError('故事视频导演版本未知，已保留原任务，未静默切换规则')
    return modes.for_mode(inputs.get('story_input_mode', inputs.get('input_mode')))


def cancel_all_children():
    errors, terminated = [], 0
    for tid, process in list(_CHILDREN.items()):
        try:
            if process.poll() is None:
                if os.name == 'nt' and isinstance(process.pid, int):
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=10)
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                terminated += 1
        except (OSError, subprocess.SubprocessError) as error:
            errors.append(str(error))
    return {'terminated': terminated, 'errors': errors}


def capabilities():
    from . import paths
    from . import story_video_asr
    keys = ('llama_server_exe', 'model_gguf', 'mmproj_gguf', 'h3_root')
    missing = [key for key in keys if not paths.get(key) or not Path(paths.get(key)).exists()]
    return {'available': not missing, 'missing': missing, 'local_only': True,
            'inputs': ['image_and_text', 'text', 'reference'], 'segment_count': {'min': 2, 'max': 10, 'default': 10},
            'reference_mode': True, 'reference_flow_version': 3,
            'reference_image_sizes': ['match', 'max'],
            'reference_limits': {'min_characters': 1, 'max_characters': 7, 'initial_scenes': 1},
            'segment_seconds': 15, 'width': 1024, 'height': 576,
            'flow_version': 2, 'aspect': 'opening_image; text selectable, defaults to 16:9',
            'aspect_ratios': list(ASPECT_RATIOS),
            'steps': 10, 'quality': 'preview',
            'vision': 'local_qwen', 'audio_review': story_video_asr.capability(),
            'audio_review_optional': True, 'audio_review_enabled_default': False,
            'runtime_optimization': 'story-video-flow-v2',
            'quality_modes': ['guided', 'automatic'],
            'offline_scope': 'loopback-only Python worker and descendants; local model files'}


def validate_inputs(data):
    if data.get('input_mode') == 'reference':
        return _validate_reference_inputs(data)
    result = {'story_direction': str(data.get('story_direction') or '').strip(),
              'style': str(data.get('style') or '电影写实，自然表演，镜头服务剧情').strip()}
    audio_review_enabled = data.get('audio_review_enabled', False)
    if type(audio_review_enabled) is not bool:
        raise ValueError('语音核对选项须为布尔值')
    if data.get('runtime_optimization', 'story-video-flow-v2') != 'story-video-flow-v2':
        raise ValueError('故事视频运行优化版本未知，请重新加载工作台')
    result.update(audio_review_enabled=audio_review_enabled,
                  runtime_optimization='story-video-flow-v2')
    if not 2 <= len(result['story_direction']) <= 24000:
        raise ValueError('请填写2～24000字的故事方向')
    if len(result['style']) > 2000:
        raise ValueError('风格说明请控制在2000字以内')
    for key, default, lo, hi in (('segment_count', 10, 2, 10), ('segment_seconds', 15, 4, 15)):
        value = data.get(key, default)
        if isinstance(value, bool) or str(value) != str(int(value)) or not lo <= int(value) <= hi:
            raise ValueError('%s范围为%d～%d整数' % (key, lo, hi))
        result[key] = int(value)
    mode = data.get('input_mode')
    if mode not in (None, 'image', 'text'):
        raise ValueError('请选择首图或纯文字模式')
    opening = '' if mode == 'text' else str(data.get('opening_image') or '').strip()
    if mode == 'image' and not opening:
        raise ValueError('首图模式请先上传图片')
    if opening:
        image = Path(opening)
        if not image.is_absolute() or opening.startswith(('\\\\', '//')) or not image.is_file():
            raise ValueError('起始图须为存在的本地图片')
        if image.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp') or image.stat().st_size > 20*1024*1024:
            raise ValueError('起始图须为20MB内PNG、JPG或WebP')
        from PIL import Image
        with Image.open(image) as opened:
            if opened.width * opened.height > 40_000_000:
                raise ValueError('起始图像素过大，请先缩小')
            opened.verify()
        opening = str(image.resolve())
    result['opening_image'] = opening
    result['input_mode'] = 'image' if opening else 'text'
    aspect = str(data.get('aspect_ratio') or '16:9').strip()
    if not opening and aspect not in ASPECT_RATIOS:
        raise ValueError('视频比例请选择' + '、'.join(ASPECT_RATIOS))
    result['aspect_ratio'] = aspect if aspect in ASPECT_RATIOS else '16:9'
    result['flow_version'] = 2
    quality_mode = data.get('quality_mode', 'guided')
    if quality_mode not in ('guided', 'automatic'):
        raise ValueError('请选择手动确认或本地自动审稿验收')
    result['quality_mode'] = quality_mode
    return result


def _reference_image(value, label):
    """Validate a local uploaded reference without moving or changing it."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError('请上传' + label)
    value = value.strip()
    image = Path(value)
    if not image.is_absolute() or value.startswith(('\\\\', '//')) or not image.is_file():
        raise ValueError(label + '须为存在的本地图片')
    if image.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp') or not 0 < image.stat().st_size <= 20*1024*1024:
        raise ValueError(label + '须为20MB内PNG、JPG或WebP')
    from PIL import Image
    with Image.open(image) as opened:
        if opened.width * opened.height > 40_000_000:
            raise ValueError(label + '像素过大，请先缩小')
        opened.verify()
    return str(image.resolve())


def _validate_reference_inputs(data):
    from . import story_video_characters as characters
    from . import story_video_reference_prompts as reference_prompts
    # Reuse the established text/count/style/ratio validation. Legacy input
    # modes keep their own behavior and task versions.
    result = validate_inputs(dict(data, input_mode='text', opening_image=''))
    if result['segment_seconds'] != 15:
        raise ValueError('全能参考每段使用完整15秒')
    source = data.get('story_input', 'story')
    if source not in ('story', 'idea'):
        raise ValueError('请选择完整故事或故事想法')
    reference_image_size = data.get('reference_image_size', 'match')
    if reference_image_size not in ('match', 'max'):
        raise ValueError('参考图精度请选择标准或高清')
    raw_cards = data.get('character_cards')
    if not isinstance(raw_cards, list) or not 1 <= len(raw_cards) <= 7:
        raise ValueError('全能参考需要1～7位人物，请为真实续接末帧预留参考位置')
    cards = characters.normalize_cards(raw_cards)
    for card in cards:
        if len(card['id']) > 64 or len(card['name']) > 80 or len(card['personality']) > 1200 or len(card['voice']) > 600:
            raise ValueError('人物名字、性格或声音描述过长，请精简后再生成')
        card['image_path'] = _reference_image(card['image_path'], card['name'] + '的人设图')
        card['sha256'] = _frame_signature(card['image_path'])
    scene = data.get('initial_scene') or ''
    if not isinstance(scene, str):
        raise ValueError('初始场景只能上传一张图片')
    scene = _reference_image(scene, '初始场景图') if scene.strip() else ''
    result.update(input_mode='reference', flow_version=3, story_input=source,
                  character_cards=cards, initial_scene=scene,
                  initial_scene_sha256=_frame_signature(scene) if scene else '',
                  reference_image_size=reference_image_size,
                  reference_visual_policy=reference_prompts.REFERENCE_VISUAL_POLICY)
    return result


def _busy():
    active = [t for t in runtime.list_tasks(200) if t.get('status') in ('running', 'queued')]
    if active:
        raise RuntimeError('已有生成任务，请先等待当前任务结束')
    from api import saga_api
    if any(j.get('running') for j in saga_api._JOBS.values()) or (ROOT/'cache/current_task.json').exists():
        raise RuntimeError('工作台正在生成，请稍后开始故事视频')
    from models import gpu_manager
    gpu_manager.assert_local_run_allowed()
    for port in (8188, 8190):
        try:
            import urllib.request
            with urllib.request.urlopen('http://127.0.0.1:%d/queue' % port, timeout=3) as response:
                queue = json.load(response)
        except (OSError, ValueError):
            continue
        if queue.get('queue_running') or queue.get('queue_pending'):
            raise RuntimeError('本地生成器仍有任务，请等它结束')


def start(inputs):
    data = validate_inputs(inputs)
    with _ADMISSION:
        _busy()
        cap = capabilities()
        if cap['missing']:
            raise RuntimeError('本地模型路径缺失：' + '、'.join(cap['missing']))
        # Optional ASR availability is evidence, not a generation prerequisite.
        runtime.RUNNERS[KIND] = _runner
        return runtime.create_task(KIND, '本地故事视频', data)


def _folder(task):
    value = (task.get('checkpoint') or {}).get('story_video_folder')
    return Path(value) if value else None


def _load(path, default=None):
    return store.load_json(path, {} if default is None else default)


def _save(path, obj):
    store.save_json(path, obj)


def status(tid=''):
    tasks = [t for t in runtime.list_tasks(200) if t.get('kind') == KIND]
    task = runtime.load(tid) if tid else (tasks[0] if tasks else None)
    if not task:
        return {'task': None, 'observations': [], 'logs': []}
    if task.get('kind') != KIND:
        raise ValueError('这不是故事视频任务')
    folder = _folder(task)
    manifest = _load(folder/'manifest.json') if folder else {}
    observations = [{'segment': row['index'], 'observed': row['observation'].get('actual_visible', ''),
                     'beat_status': row['observation'].get('beat_status'),
                     'continuity_risks': row['observation'].get('continuity_risks', [])}
                    for row in manifest.get('segments', [])]
    return {'task': task, 'output': task.get('output') or manifest.get('output'),
            'quality_mode': task['inputs'].get('quality_mode', 'guided'),
            'quality_summary': manifest.get('quality_summary'),
            'plan': manifest.get('plan'), 'review': manifest.get('plan_review'),
            'source_story': manifest.get('source_story', ''),
            'decision': manifest.get('decision'), 'geometry': manifest.get('geometry'),
            'preview_url': _url(Path(manifest['segments'][0]['clip_path'])) if manifest.get('segments') and manifest['segments'][0].get('clip_path') else '',
            'segments': [{'index': s['index'], 'url': _url(Path(s['clip_path'])),
                          'warning': s.get('sequence_observation', {}).get('retry_reason', '')}
                         for s in manifest.get('segments', []) if s.get('clip_path')],
            'observations': observations, 'logs': manifest.get('logs', [])[-40:],
            'title': (manifest.get('plan') or {}).get('title'),
            'manifest_url': _url(folder/'manifest.json') if folder else ''}


def action(tid, act, segment=None):
    with _ADMISSION:
        task = runtime.load(tid)
        if not task or task.get('cleared_at') or task.get('kind') != KIND:
            raise ValueError('故事视频任务不存在')
        if act in ('approve', 'redo', 'rewrite'):
            process = _CHILDREN.get(tid)
            if task.get('status') in ('running', 'queued') or (process and process.poll() is None):
                raise RuntimeError('当前阶段仍在保存，请稍后操作')
            folder = _folder(task)
            manifest = _load(folder/'manifest.json') if folder else {}
            if act == 'rewrite':
                return start(task['inputs'])
            _busy()
            if not manifest.get('plan'):
                raise ValueError('请先生成故事')
            if act == 'approve':
                if task.get('status') != 'waiting_user' or manifest.get('decision') not in ('story', 'preview'):
                    raise ValueError('当前没有待确认的故事或首段视频')
                manifest['approved'] = True
                manifest['approved_plan_signature'] = plan_signature(manifest['plan'])
            else:
                if task['inputs'].get('flow_version', 1) < 2:
                    raise ValueError('这是旧流程任务；请点击重新写故事进入新的两阶段流程，原片保留')
                if isinstance(segment, bool) or not str(segment or '').isdigit():
                    raise ValueError('请选择需要重做的段落')
                index = int(segment)
                if not any(s['index'] == index for s in manifest.get('segments', [])):
                    raise ValueError('该段还没有视频可重做')
                manifest.setdefault('history', []).append({'segments': copy.deepcopy(manifest['segments']),
                    'output': manifest.get('output'), 'redo_from': index, 'time': time.time()})
                manifest['segments'] = [s for s in manifest['segments'] if s['index'] < index]
                for old in manifest['history'][-1]['segments']:
                    if old['index'] >= index:
                        manifest.setdefault('takes', {})[str(old['index'])] = old.get('take', 1) + 1
                manifest.pop('output', None)
            manifest.pop('decision', None)
            _save(folder/'manifest.json', manifest)
            task.update(status='queued', error='', output=None, pause_requested=False, cancel_requested=False)
            runtime.save(task)
            runtime.RUNNERS[KIND] = _runner
            runtime._queue(tid)
            return task
        if act in ('pause', 'cancel') and task.get('status') in ('running', 'queued'):
            # Keep running until the in-flight inference reaches a checkpoint.
            # This prevents a premature resume from creating a second worker.
            task[act + '_requested'] = True
            task['detail'] = '当前阶段完成后' + ('暂停' if act == 'pause' else '取消') + '，结果将保留'
            runtime.save(task)
            return task
        if act in ('resume', 'retry'):
            process = _CHILDREN.get(tid)
            if process and process.poll() is None:
                raise RuntimeError('当前阶段尚在保存，请稍后继续')
            if any(t['id'] != tid and t.get('status') in ('running', 'queued') for t in runtime.list_tasks(200)):
                raise RuntimeError('已有其他生成任务，请稍后继续')
            from models import gpu_manager
            gpu_manager.assert_local_run_allowed()
            task.update(pause_requested=False, cancel_requested=False)
            runtime.save(task)
            runtime.RUNNERS[KIND] = _runner
            return runtime.action(tid, act)
        if act == 'cancel' and task.get('status') in ('paused', 'interrupted', 'failed', 'waiting_user'):
            return runtime.action(tid, act)
        return task


def _url(path):
    from urllib.parse import quote
    return '/files/' + quote(Path(path).resolve().relative_to(ROOT).as_posix(), safe='/')


def _runner(handle, task):
    from .story_video_offline import build_env
    folder = _folder(task)
    if folder is None:
        folder = OUTPUT / (time.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8])
        folder.mkdir(parents=True)
        checkpoint = dict(task.get('checkpoint') or {}, story_video_folder=str(folder))
        handle.set(checkpoint=checkpoint)
    log_path = folder/'network_audit.jsonl'
    command = [sys.executable, '-B', str(ROOT/'cores/story_video_offline.py'),
               '--', '-m', 'cores.story_video_worker', task['id']]
    env = build_env(log_path)
    env['V41_RUNTIME_GENERATION'] = task.get('runtime_generation', 'initial')
    runtime_reset.assert_current(env['V41_RUNTIME_GENERATION'])
    with (folder/'worker.log').open('ab') as log:
        process = subprocess.Popen(command, cwd=str(ROOT), env=env,
                                   stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        _CHILDREN[task['id']] = process
        try:
            runtime_reset.assert_current(task.get('runtime_generation', 'initial'))
            while process.poll() is None:
                runtime_reset.assert_current(task.get('runtime_generation', 'initial'))
                time.sleep(1)
            current = runtime.load(task['id']) or {}
            if current.get('status') == 'waiting_user':
                return None
            if current.get('status') in ('paused', 'cancelled'):
                raise runtime.TaskPaused()
            if process.returncode:
                raise RuntimeError(current.get('error') or '本地故事视频中断，检查点已保存，可重试')
            output = _load(folder/'manifest.json').get('output')
            if not output:
                raise RuntimeError('任务未返回经过验收的成片')
            return output
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            _CHILDREN.pop(task['id'], None)


def validate_schema(value, schema, path='reply'):
    """Small JSON contract validator; no optional package or remote schema lookup."""
    types = schema.get('type')
    allowed = [types] if isinstance(types, str) else (types or [])
    actual = ('null' if value is None else 'boolean' if isinstance(value, bool) else
              'object' if isinstance(value, dict) else 'array' if isinstance(value, list) else
              'string' if isinstance(value, str) else 'integer' if isinstance(value, int) else
              'number' if isinstance(value, float) and math.isfinite(value) else 'invalid')
    if allowed and actual not in allowed and not (actual == 'integer' and 'number' in allowed):
        raise ValueError(path + '类型不符')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(path + '取值不符')
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        if set(schema.get('required', [])) - set(value):
            raise ValueError(path + '缺少字段：' + ','.join(set(schema['required'])-set(value)))
        if schema.get('additionalProperties') is False and set(value) - set(properties):
            raise ValueError(path + '有未定义字段')
        for key in value.keys() & properties.keys():
            validate_schema(value[key], properties[key], path+'.'+key)
    elif isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', 10000):
            raise ValueError(path + '项目数不符')
        if schema.get('uniqueItems') and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            raise ValueError(path + '重复项目')
        for index, item in enumerate(value):
            validate_schema(item, schema.get('items', {}), path+'[%d]' % index)
    elif isinstance(value, str):
        if len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', 100000):
            raise ValueError(path + '文字长度不符')
        if schema.get('pattern') and not re.search(schema['pattern'], value):
            raise ValueError(path + '不符合字段格式：动作只写可见表演，逐字对白仅放dialogue')
    elif actual in ('integer', 'number'):
        if value < schema.get('minimum', -math.inf) or value > schema.get('maximum', math.inf):
            raise ValueError(path + '数字超出范围')
        if 'exclusiveMinimum' in schema and value <= schema['exclusiveMinimum']:
            raise ValueError(path + '数字须为正数')
    return value


def _dialogue(lines, budget, seconds):
    for line in lines:
        if re.fullmatch(r'\s*(?:（[^（）]*）|\([^()]*\))\s*', line['text']):
            raise ValueError('整句括号说明不能作为逐字对白，请移入action或delivery')
    counted = sum(len(re.sub(r'[^\w\u4e00-\u9fff]', '', line['text'])) for line in lines)
    if counted > budget['max_dialogue_characters_total']:
        raise ValueError('对白超过自然表演预算')
    duration = counted / budget['speech_characters_per_second'] + len(lines)*budget['speech_pause_seconds_per_line']
    if duration > budget['max_dialogue_seconds'] + .01:
        raise ValueError('对白无足够停顿时间')
    last, actual_speech = 0, 0.0
    for line in lines:
        if 'start' in line:
            if not last <= line['start'] < line['end'] <= seconds:
                raise ValueError('对白时间重叠或越界')
            # The total budget already reserves each line's trailing pause;
            # the utterance interval contains speech only.
            estimate = len(re.sub(r'[^\w\u4e00-\u9fff]', '', line['text']))/budget['speech_characters_per_second']
            actual = line['end']-line['start']
            if actual+.1 < estimate:
                raise ValueError('单句对白发声时间不足：需要约%.2f秒，实际%.2f秒' % (estimate, actual))
            actual_speech += actual
            last = line['end']
    if actual_speech + len(lines)*budget['speech_pause_seconds_per_line'] > min(seconds, budget['max_dialogue_seconds']) + .01:
        raise ValueError('对白实际发声与逐句停顿超过本段时长预算')


def _plan_action_text(action):
    """Read known action metadata without rewriting the model's original plan."""
    if not isinstance(action, str):
        raise ValueError('action须为文字字符串')
    text = action.lstrip()
    if not text.startswith(('[', '{')):
        return action
    try:
        structured = json.loads(action)
    except ValueError:
        # Ordinary prose may start with a shot label or a literal bracket.
        return action
    rows = [structured] if isinstance(structured, dict) else structured
    if not isinstance(rows, list) or not rows:
        raise ValueError('结构化action须为非空phase/description对象数组或单对象')
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'phase', 'description'}:
            raise ValueError('结构化action仅支持phase与description字段，不能隐藏dialogue或丢弃未知字段')
        if any(not isinstance(row[key], str) or not row[key].strip()
               for key in ('phase', 'description')):
            raise ValueError('结构化action的phase与description须为非空字符串')
    return '\n'.join(row['phase'] + '：' + row['description'] for row in rows)


def _action_spoken_quotes(action, speaker_aliases=()):
    """Quotes are dialogue only when an explicit speech attribution is present."""
    quotes = r'''“([^”\n]+)”|「([^」\n]+)」|『([^』\n]+)』|"([^"\n]+)"|‘([^’\n]+)’|'([^'\n]+)' '''.strip()
    chinese_verb = (r'(?:说道|低声道|轻声道|问道|喊道|答道|回答|低语|朗读|念出|读出|喊出|叫道|呼喊|大喊|'
                    r'(?<!小)(?<!传)说|问|喊)')
    english_verb = r'(?:says?|said|asks?|asked|shouts?|shouted|whispers?|whispered|replies|replied|yells?|yelled)'
    named_speaker = '|'.join(re.escape(value) for value in speaker_aliases if isinstance(value, str) and value)
    for match in re.finditer(quotes, action):
        before, after = action[:match.start()], action[match.end():]
        attributed = (
            re.search(chinese_verb + r'(?:了|着)?(?:一声|一句话|一句)?[\s:：,，]*$', before)
            or re.search(r'\b' + english_verb + r'(?:\s+(?:softly|quietly|loudly))?[\s:,]*$', before, re.I)
            or re.match(r'^[，,\s]*(?:他|她|它|[\u4e00-\u9fff]{1,4})(?:低声|轻声|大声|高声|小声)?'
                        + chinese_verb + r'(?=[，,。.!！?？\s]|$)', after)
            or re.match(r'^[,\s]*(?:he|she|they|[A-Z][A-Za-z]{0,20})\s+' + english_verb
                        + r'\b', after, re.I)
            or (named_speaker and re.search(r'(?:^|[\s，,。；;！？!?：:])(?:' + named_speaker
                + r')\s*[:：]\s*$', before)))
        if attributed:
            yield next(value for value in match.groups() if value is not None)


def validate_plan(plan, payload):
    count, seconds = payload['count'], payload['seconds']
    if [b['index'] for b in plan['beats']] != list(range(1, count+1)):
        raise ValueError('分段编号必须连续且与请求相符')
    ids = [c['id'] for c in plan['characters']]
    if len(ids) != len(set(ids)):
        raise ValueError('人物身份编号重复')
    all_lines = {re.sub(r'\W', '', line['text']).casefold()
                 for item in plan['beats'] for line in item['dialogue']}
    speaker_aliases = {value for character in plan['characters']
                       for value in (character['id'], character['name'])}
    for beat in plan['beats']:
        if not set(beat['cast']) <= set(ids) or beat['seconds'] != seconds:
            raise ValueError('分段人物或时长不符')
        action_text = _plan_action_text(beat['action'])
        own_lines = {re.sub(r'\W', '', line['text']) for line in beat['dialogue']}
        quoted_lines = list(_action_spoken_quotes(action_text, speaker_aliases))
        if any(re.sub(r'\W', '', line) not in own_lines for line in quoted_lines):
            wrong = [line for line in quoted_lines if re.sub(r'\W', '', line) not in own_lines]
            raise ValueError('动作描述不能提前使用其他段原话或新增台词：第%d段action请删除原话%s及其提前回应，原话只能放所属段dialogue；action只写本段可见动作' %
                             (beat['index'], json.dumps(wrong, ensure_ascii=False)))
        own_normalized = {line.casefold() for line in own_lines}
        action_normalized = re.sub(r'\W', '', action_text).casefold()
        speech_cue = r'(?:说道|低声道|轻声道|说|问|回答|喊|低语|安慰).{0,4}'
        if any(line and line not in own_normalized and line in action_normalized
               and (len(line) >= 6 or re.search(speech_cue + re.escape(line), action_normalized))
               for line in all_lines):
            raise ValueError('动作描述不能提前使用其他段原话，原话只放在所属段dialogue')
        speakers = {value for character in plan['characters'] if character['id'] in beat['cast']
                    for value in (character['id'], character['name'])}
        if any(line['speaker'] not in speakers for line in beat['dialogue']):
            raise ValueError('对白说话者必须属于本段人物')
        _dialogue(beat['dialogue'], payload['timing_budget'], seconds)


def _plan_review_sources(payload):
    sources = [payload.get('brief', '')]

    def collect(value):
        if isinstance(value, str):
            sources.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(payload.get('plan', {}))
    if payload.get('review_evidence_policy') == 'story-review-evidence-v2':
        count, seconds = payload.get('count'), payload.get('seconds')
        if (type(count) is int and count > 0 and type(seconds) in (int, float)
                and math.isfinite(seconds) and seconds > 0):
            contract = dict(segment_count=count, segment_seconds=seconds,
                            total_seconds=count * seconds, seconds_scope='per_segment')
            text = '共%d段，每段%g秒，全片%g秒。seconds与timing_budget仅约束单段。' % (
                count, seconds, count * seconds)
            # Only our computed contract is a parameter source. Arbitrary
            # serialized JSON keys/numbers are not narrative evidence.
            if (payload.get('timing_contract') == contract
                    and payload.get('duration_contract_text') == text
                    and payload.get('parameter_sources') == [text]):
                sources.append(text)
    return sources


def _review_quote_matches(item, sources):
    quote = item.get('source_quote')
    return (isinstance(quote, str) and bool(quote.strip())
            and any(quote in text for text in sources))


def validate_plan_review(review, payload):
    if payload.get('review_policy') == 'core_errors_only_v1':
        issues = review['issues']
        blocking = any(item.get('severity') == 'blocking' for item in issues)
        if type(review['passed']) is not bool or review['passed'] != (not blocking):
            raise ValueError('新版审稿passed仅取决于是否存在blocking，一般建议不能阻断')
        sources = _plan_review_sources(payload)
        for item in issues:
            if item.get('severity') not in ('blocking', 'advisory'):
                raise ValueError('新版审稿每项须明确blocking或advisory')
            if not _review_quote_matches(item, sources):
                if (payload.get('review_evidence_policy') == 'story-review-evidence-v2'
                        and item['severity'] == 'advisory'):
                    # Quarantined by _request_plan_review, never used to
                    # rewrite the story or passed downstream as advice.
                    continue
                raise ValueError('审稿source_quote必须逐字来自用户原文或当前计划，不能虚构问题依据')
        return
    if not isinstance(review['passed'], bool) or review['passed'] != (not review['issues']):
        raise ValueError('审阅通过时issues必须为空，未通过时必须给出具体问题')


def _request_plan_review(request, directory):
    review = request_json(request, directory, validator=validate_plan_review)
    payload = request[1]
    if (payload.get('review_evidence_policy') != 'story-review-evidence-v2'
            or payload.get('review_policy') != 'core_errors_only_v1'):
        return review
    sources = _plan_review_sources(payload)
    unverified = [dict(issue=copy.deepcopy(item),
        evidence_error='引文未匹配原文、计划或已核对的时长参数，建议不可用')
        for item in review['issues']
        if item['severity'] == 'advisory' and not _review_quote_matches(item, sources)]
    adopted = copy.deepcopy(review)
    adopted['issues'] = [item for item in adopted['issues']
        if item['severity'] != 'advisory' or _review_quote_matches(item, sources)]
    adopted['unverified_advisories'] = unverified
    adopted['evidence_status'] = 'partially_unavailable' if unverified else 'complete'
    audit_path = Path(directory) / 'evidence_audit.json'
    adopted['evidence_audit_path'] = str(audit_path.resolve())
    _save(audit_path, dict(policy=payload['review_evidence_policy'],
        raw_review_path=str((Path(directory) / 'accepted.json').resolve()),
        model_passed=review['passed'], adopted_passed=adopted['passed'],
        evidence_status=adopted['evidence_status'], unverified_advisories=unverified))
    return adopted


def plan_signature(plan):
    encoded = json.dumps(plan, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _quality_gate(manifest, folder, inputs, opening_state, stage):
    """One independent local review and at most one whole-plan revision."""
    from . import story_video_continuation as continuation
    if continuation.enabled(inputs):
        return _continuing_quality_gate(manifest, folder, inputs, opening_state, stage)
    prompts = _prompt_builder(inputs)
    automatic = inputs.get('quality_mode') == 'automatic'
    if inputs.get('flow_version', 1) >= 2 and inputs.get('quality_mode') != 'automatic':
        if not manifest.get('plan_review'):
            stage('本地AI审阅故事（意见供你参考）', 0)
            request = prompts.review_request(manifest['plan'], inputs['story_direction'], inputs['style'],
                inputs['segment_count'], inputs['segment_seconds'], opening_state)
            review = _request_plan_review(request, folder/'planning/review_1')
            manifest['plan_review'] = dict(review, advisory=True, round=1, pending=False,
                plan_signature=plan_signature(manifest['plan']), plan_source=manifest.get('plan_source'),
                review_source=str((folder/'planning/review_1/accepted.json').resolve()))
            _save(folder/'manifest.json', manifest)
        return manifest['plan']
    # Existing rendered stories keep their established plan; this gate applies
    # before a new story commits any video, not retrospectively to old outputs.
    if manifest['segments']:
        return manifest['plan']
    current = manifest['plan']
    signature = plan_signature(current)
    stored = manifest.get('plan_review') or {}
    if stored and stored.get('plan_signature') != signature:
        raise RuntimeError('当前计划与已保存审阅版本不一致，结果已保留，请新建任务')
    if stored.get('passed') and not stored.get('pending'):
        stored_payload = prompts.review_request(current, inputs['story_direction'], inputs['style'],
            inputs['segment_count'], inputs['segment_seconds'], opening_state,
            blocking_only=automatic)[1]
        validate_plan_review(stored, stored_payload)
        if automatic:
            manifest['plan_advisories'] = [copy.deepcopy(item) for item in stored['issues']
                                         if item.get('severity') == 'advisory']
            _save(folder/'manifest.json', manifest)
        return current
    round_no = 2 if stored.get('round') == 2 else 1
    plan_source = manifest.get('plan_source') or str((folder/'planning/accepted.json').resolve())
    manifest['plan_source'] = plan_source
    while True:
        stage('本地AI独立审阅剧本' if round_no == 1 else '本地AI复审修改稿', 0)
        review_dir = folder/'planning'/('review_%d' % round_no)
        request = prompts.review_request(current, inputs['story_direction'], inputs['style'],
            inputs['segment_count'], inputs['segment_seconds'], opening_state,
            blocking_only=automatic)
        review = _request_plan_review(request, review_dir)
        manifest['plan_review'] = dict(review, round=round_no, pending=False,
            plan_signature=signature, plan_source=plan_source,
            review_source=str((review_dir/'accepted.json').resolve()))
        _save(folder/'manifest.json', manifest)
        if review['passed']:
            if automatic:
                manifest['plan_advisories'] = [copy.deepcopy(item) for item in review['issues']
                    if item.get('severity') == 'advisory']
                _save(folder/'manifest.json', manifest)
            return current
        if round_no == 2:
            raise RuntimeError('本地AI剧本复审仍未通过，原稿、修改稿和审阅均已保留，未开始生成视频')
        stage('本地AI按审阅修改整稿（仅一次）', 0)
        revision_request = prompts.plan_request(inputs['story_direction'], inputs['style'],
            inputs['segment_count'], inputs['segment_seconds'], opening_state)
        feedback = copy.deepcopy(review)
        if request[1].get('review_evidence_policy') == 'story-review-evidence-v2':
            feedback = {key: feedback[key] for key in ('passed', 'issues', 'strengths')}
        revision_request[1].update(previous_draft=copy.deepcopy(current),
            review_feedback=feedback,
            revision_scope='仅依据本地AI审阅修复整稿，保持用户目标与输入段数时长；这是唯一一次整稿重写')
        revision_dir = folder/'planning/revision_1'
        revised = request_json(revision_request, revision_dir, validator=validate_plan)
        manifest.setdefault('rejected_plan_versions', []).append({
            'plan': copy.deepcopy(current), 'plan_signature': signature, 'plan_source': plan_source,
            'error': '本地AI剧本审阅未通过', 'review': copy.deepcopy(review), 'time': time.time()})
        current = manifest['plan'] = revised
        signature = plan_signature(current)
        plan_source = manifest['plan_source'] = str((revision_dir/'accepted.json').resolve())
        # Checkpoint the adopted revision before the next review. A paused or
        # interrupted worker resumes at review_2 without rewriting the plan.
        manifest['plan_review'] = dict(review, passed=False, round=2, pending=True,
            plan_signature=signature, plan_source=plan_source,
            review_source=str((folder/'planning/review_2/accepted.json').resolve()))
        _save(folder/'manifest.json', manifest)
        round_no = 2


def _continuation_warning(manifest, stage, report, reason=None):
    """Keep quality failures visible without changing any model verdict."""
    reason = reason or report.get('reason') or '；'.join(
        str(item.get('reason', item)) if isinstance(item, dict) else str(item)
        for item in report.get('issues', [])) or '本地质量复核未通过或证据不可用'
    warning = dict(stage=stage, reason=reason)
    if warning not in manifest.setdefault('continuation_warnings', []):
        manifest['continuation_warnings'].append(warning)


def _continuation_summary(manifest, complete=False):
    from . import story_video_continuation as continuation
    summary = continuation.completion_summary(manifest['segments'], warnings=[
        '%s：%s' % (item['stage'], item['reason'])
        for item in manifest.get('continuation_warnings', [])])
    warnings = summary.get('warnings', [])
    summary.update(
        accepted_segments=len(manifest['segments']), generation_completed=complete)
    if warnings:
        summary['passed'] = False
    summary['status'] = ('completed_with_warnings' if not summary['passed'] else 'passed') if complete else (
        'working_with_warnings' if warnings else 'working')
    return summary


def _continuing_quality_gate(manifest, folder, inputs, opening_state, stage):
    """New automatic policy: one repair, then preserve evidence and continue."""
    from . import story_video_continuation as continuation
    prompts = _prompt_builder(inputs)
    current = manifest['plan']
    if manifest['segments']:
        return current
    signature = plan_signature(current)
    stored = manifest.get('plan_review') or {}
    if stored and stored.get('plan_signature') != signature:
        raise RuntimeError('当前计划与已保存审阅版本不一致，结果已保留，请新建任务')
    if stored.get('continuation_policy') == continuation.POLICY and not stored.get('pending'):
        return current
    if stored.get('passed') and not stored.get('pending'):
        payload = prompts.review_request(current, inputs['story_direction'], inputs['style'],
            inputs['segment_count'], inputs['segment_seconds'], opening_state, blocking_only=True)[1]
        validate_plan_review(stored, payload)
        manifest['plan_advisories'] = [copy.deepcopy(item) for item in stored['issues']
                                      if item.get('severity') == 'advisory']
        _save(folder/'manifest.json', manifest)
        return current
    round_no = 2 if stored.get('round') == 2 else 1
    plan_source = manifest.get('plan_source') or str((folder/'planning/accepted.json').resolve())

    def ask_review(request, directory, images=None, validator=None):
        return _request_plan_review(request, directory)

    while True:
        stage('本地AI独立审阅剧本' if round_no == 1 else '本地AI复审修改稿', 0)
        request = prompts.review_request(current, inputs['story_direction'], inputs['style'],
            inputs['segment_count'], inputs['segment_seconds'], opening_state, blocking_only=True)
        review_dir = folder/'planning'/('review_%d' % round_no)
        review = continuation.optional_request(request, review_dir, ask_review, _save)
        manifest['plan_review'] = dict(review, round=round_no, pending=False,
            plan_signature=signature, plan_source=plan_source, advisory=True, quality_gate=False,
            continuation_policy=continuation.POLICY,
            review_source=str((review_dir/'accepted.json').resolve()))
        # Only verified, non-blocking advice may reach later segment writers.
        manifest['plan_advisories'] = [copy.deepcopy(item) for item in review.get('issues', [])
                                      if item.get('severity') == 'advisory']
        _save(folder/'manifest.json', manifest)
        if review.get('status') == 'unavailable' or round_no == 2 or review.get('passed'):
            if review.get('passed') is not True:
                _continuation_warning(manifest, 'plan_review', review)
            if review.get('unverified_advisories'):
                _continuation_warning(manifest, 'plan_review', review, '部分普通建议缺少可靠依据，已隔离')
            _save(folder/'manifest.json', manifest)
            return current
        stage('本地AI按审阅修改整稿（仅一次）', 0)
        revision = prompts.plan_request(inputs['story_direction'], inputs['style'],
            inputs['segment_count'], inputs['segment_seconds'], opening_state)
        revision[1].update(previous_draft=copy.deepcopy(current),
            review_feedback={key: copy.deepcopy(review[key]) for key in ('passed', 'issues', 'strengths')},
            revision_scope='仅依据本地AI审阅修复整稿，保持用户目标与输入段数时长；这是唯一一次整稿重写')
        revised = continuation.optional_request(revision, folder/'planning/revision_1',
            request_json, _save, validator=validate_plan)
        if revised.get('status') == 'unavailable':
            manifest['plan_revision_warning'] = revised
            _continuation_warning(manifest, 'plan_revision', revised,
                '整稿修订格式修复仍失败，保留已有有效故事和分段计划继续')
            _continuation_warning(manifest, 'plan_review', review)
            _save(folder/'manifest.json', manifest)
            return current
        manifest.setdefault('rejected_plan_versions', []).append(dict(plan=copy.deepcopy(current),
            plan_signature=signature, plan_source=plan_source,
            error='本地AI审阅建议修订，原计划与审阅保留', review=copy.deepcopy(review), time=time.time()))
        current = manifest['plan'] = revised
        signature = plan_signature(current)
        plan_source = manifest['plan_source'] = str((folder/'planning/revision_1/accepted.json').resolve())
        manifest['plan_review'] = dict(review, round=2, pending=True, plan_signature=signature,
            plan_source=plan_source, advisory=True, quality_gate=False,
            continuation_policy=continuation.POLICY,
            review_source=str((folder/'planning/review_2/accepted.json').resolve()))
        _save(folder/'manifest.json', manifest)
        round_no = 2


def validate_written(obj, payload):
    prompts = _prompt_builder(payload)
    obj = prompts.compile_written(obj, payload)
    prompt, seconds = obj['prompt'], payload['seconds']
    style = str(payload.get('visual_style') or '')
    live_action_pattern = r'live[- ]action|photoreal|real (?:adult )?actors'
    if 'director_profile' in payload:
        live_action_pattern += r'|真人|实拍|真实演员|写实摄影'
    if ('真人' in style or re.search(r'live.action|photoreal', style, re.I)) and not re.search(
            live_action_pattern, obj['integrated_multimodal_description'], re.I):
        raise ValueError('用户要求真人写实，镜头正文必须明确真人电影/真实演员风格')
    fields = ['integrated_multimodal_description:', 'overall_soundscape:', 'non_diegetic_music:']
    positions = [prompt.find(field) for field in fields]
    headers = re.findall(r'(?m)^(integrated_multimodal_description|overall_soundscape|non_diegetic_music):', prompt)
    if (any(pos < 0 for pos in positions) or positions != sorted(positions)
            or headers != [field[:-1] for field in fields] or '[Shot 1]' not in prompt):
        raise ValueError('H3三字段或第一镜格式不符')
    if payload['mode'] == 'i2va' and not prompt.startswith(prompts.I2VA_FIRST_FRAME):
        raise ValueError('续接首帧声明缺失')
    references = re.findall(r'<(Picture|Subject|Video|Audio)\s+(\d+)>', prompt)
    if payload['mode'] == 't2va' and (references or not prompt.startswith(fields[0])):
        raise ValueError('纯文字首段不能引用不存在的参考图')
    if payload['mode'] == 'i2va' and any(kind != 'Picture' or number != '1' for kind, number in references):
        raise ValueError('续接只有一张当前首帧')
    description = prompt[positions[0]+len(fields[0]):positions[1]]
    shots = list(re.finditer(r'\[Shot\s+(\d+)\]', description))
    if [int(shot[1]) for shot in shots] != list(range(1, len(shots)+1)):
        raise ValueError('Shot编号须从1连续递增')
    last_cut = 0
    for shot in shots[1:]:
        cut = re.match(r'\s+At (\d{2}):(\d{2}\.\d{3}),', description[shot.end():])
        if not cut:
            raise ValueError('切镜时间格式不符或超出本段时长')
        cut_time = int(cut[1])*60+float(cut[2])
        if not last_cut < cut_time < seconds:
            raise ValueError('Shot切点须严格递增且位于本段时长内')
        last_cut = cut_time
    last = 0
    for item in obj['timeline']:
        if not last <= item['start'] < item['end'] <= seconds:
            raise ValueError('动作时间轴重叠或越界')
        if seconds >= 12 and abs(item['start'] - last) > .001:
            raise ValueError('动作时间轴须从0秒连续覆盖整段，不留空等区间')
        last = item['end']
    if seconds >= 12 and abs(last - seconds) > .001:
        raise ValueError('动作时间轴须持续到本段最后一秒，不能提前结束')
    _dialogue(obj['dialogue'], payload['timing_budget'], seconds)
    source = [(line['speaker'], line['text']) for line in payload['beat']['dialogue']]
    if [(line['speaker'], line['text']) for line in obj['dialogue']] != source:
        raise ValueError('对白须保持本段已规划原话')
    spoken = re.findall(r'<d>\s*\[[^\]\n]+\]\s*(.*?)\s*</d>', prompt, re.S)
    if (spoken != [line['text'] for line in obj['dialogue']]
            or prompt.count('<d>') != len(spoken) or prompt.count('</d>') != len(spoken)):
        raise ValueError('H3提示词中的对白须逐句对应原话与顺序')


def request_json(request, directory, images=None, validator=None):
    from models import story_video_client as client
    system, payload, schema = request
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory/'accepted.json'
    def correction_payload(previous, error, scope):
        try:
            previous = json.loads(previous) if isinstance(previous, str) else previous
        except ValueError:
            pass
        return dict(payload, previous_reply=previous, correction=str(error), correction_scope=scope)
    if target.exists():
        accepted = _load(target)
        try:
            validate_schema(accepted, schema)
            if validator:
                validator(accepted, payload)
            return accepted
        except (ValueError, KeyError, TypeError) as error:
            retained = target.with_name('accepted_rejected_%d.json' % time.time_ns())
            target.rename(retained)
            payload = correction_payload(accepted, error, '依据更新后的同一契约修复当前回复，保持用户目标')
    # Recover a complete local-model response after interruption or a contract
    # upgrade. Revalidate it; never manufacture replacement creative content.
    responses = sorted(directory.glob('call_*/*/response.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    for response in responses:
        try:
            candidate = json.loads(_load(response).get('text', ''))
            validate_schema(candidate, schema)
            if validator:
                validator(candidate, payload)
            _save(target, candidate)
            return candidate
        except (ValueError, KeyError, TypeError):
            continue
    # One schema/timing repair; never substitute an engineer-authored story.
    for attempt in range(2):
        call_dir = directory/('call_%d_%s' % (attempt+1, uuid.uuid4().hex[:6]))
        call_system = system
        if payload.get('correction'):
            call_system += ('\n本次是修复请求。上一份回复未通过，不能照抄。必须修复的具体错误：'
                            + payload['correction'] + '。previous_reply只是待修正的旧稿，不是正确答案；'
                            '保持用户目标，重新输出已修正且符合schema的完整JSON。')
        if images:
            raw = client.vision(call_system, payload, images, call_dir, max_tokens=3500, schema=schema)
        else:
            raw = client.text(call_system, payload, call_dir, max_tokens=6000 if 'count' in payload else 4500, schema=schema)
        try:
            text = str(raw).strip()
            if text.startswith('```'):
                text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
            obj = json.loads(text)
            validate_schema(obj, schema)
            if validator:
                validator(obj, payload)
            _save(target, obj)
            return obj
        except (ValueError, KeyError, TypeError) as error:
            _save(call_dir/'contract_error.json', {'error': str(error)})
            if attempt:
                raise RuntimeError('本地模型格式修复仍未通过：' + str(error)) from error
            payload = correction_payload(raw, error, '只修复当前JSON/时长/引用错误，保持当前故事与已知事实')


class ControlStop(Exception):
    pass


def observation_request(stage, state, beat, inputs):
    prompts = _prompt_builder(inputs)
    system, payload, schema = prompts.observe_request(stage, state, beat)
    system += ('\n另请明确实际视觉类型：真人摄影、3D、二维动画或未知。'
               'style_alignment只凭图判断，不因为用户要求某风格就说匹配；'
               '用户明确要求真人而画面是二维动画时必须mismatch。'
               '概括性电影光影要求且无法确认时写unknown。')
    payload['desired_visual_style'] = inputs['style']
    schema['properties'].update(actual_visual_style={'type': 'string', 'minLength': 1},
        style_alignment={'type': 'string', 'enum': ['matching', 'mismatch', 'unknown']})
    schema['required'].extend(['actual_visual_style', 'style_alignment'])
    return system, payload, schema


def _checkpoint(handle):
    task = runtime.load(handle.tid) or {}
    if task.get('cancel_requested') or task.get('status') == 'cancelled':
        handle.set(status='cancelled', detail='已取消，所有已生成结果保留')
        raise ControlStop()
    if task.get('pause_requested') or task.get('status') == 'paused':
        handle.set(status='paused', detail='已保存当前阶段，可继续')
        raise ControlStop()


def _wait_for_user(handle, manifest, folder, decision):
    manifest['decision'] = decision
    _save(folder/'manifest.json', manifest)
    handle.set(status='waiting_user', error='', detail=(
        '故事已就绪，请查看后点击生成成片' if decision == 'story' else '故事和第一段视频已就绪，请决定是否延伸成片'))
    raise ControlStop()


def _frame_signature(path):
    if not path:
        return 'text-opening'
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _automatic_resume_check(row, plan, previous, inputs=None):
    from . import story_video_continuation as continuation
    evidence = row.get('quality') or {}
    adopted_warning = (continuation.enabled(inputs or {}) and evidence.get('adopted') is True
                       and evidence.get('adoption_policy') == continuation.POLICY)
    if ((not evidence.get('passed') and not adopted_warning) or evidence.get('policy_version') != QUALITY_POLICY_VERSION
            or evidence.get('plan_signature') != plan_signature(plan)
            or evidence.get('first_frame_signature') != _frame_signature(previous)
            or evidence.get('clip_signature') != _frame_signature(row['clip_path'])
            or evidence.get('anchor_signature') != _frame_signature(row['anchor_path'])):
        raise RuntimeError('第%d段的自动验收记录或输入已变化，已保留结果，请新建任务或明确重做该段' % row['index'])


def _guided_resume_media_check(row):
    """Check saved evidence before a guided continuation consumes its tail.

    Older rows without hashes retain their compatibility: require their files,
    but do not invent a prior content signature or rewrite the adoption record.
    """
    for key, label in (('raw', '原视频'), ('clip', '采用视频'), ('anchor', '续接尾帧')):
        path = row.get(key + '_path') or (row.get('video_path') if key == 'clip' else None)
        if not path or not Path(path).is_file():
            raise RuntimeError('第%d段%s文件缺失，已有素材保留，未重新生成或替换' % (row['index'], label))
        expected = row.get(key + '_sha256')
        if expected and _frame_signature(path) != expected:
            raise RuntimeError('第%d段%s内容变化，已有素材保留，未继续使用或重新生成' % (row['index'], label))


def _automatic_segment(tid, manifest, folder, inputs, plan, beat, previous, state, stage, note):
    """Adopt only a verified take; never advance from a rejected endpoint."""
    from . import story_video_media as media
    from . import story_video_quality as quality, story_video_asr as asr
    from . import story_video_continuation as continuation
    from models import story_video_client as client
    prompts = _prompt_builder(inputs)
    continuing = continuation.enabled(inputs)
    optimized = flow.enabled(inputs)
    index, seconds = beat['index'], inputs['segment_seconds']
    mode = 'i2va' if previous else 't2va'
    base = int(manifest.get('takes', {}).get(str(index), 1))
    ledger_key = '%d:%d' % (index, base)
    ledger = manifest.setdefault('quality_attempts', {}).setdefault(ledger_key,
        {'base_take': base, 'max_takes': 2, 'attempts': {}})
    if continuing:
        ledger.update(max_takes=1, continuation_policy=continuation.POLICY)
    feedback = None
    manifest_path = folder/'manifest.json'

    def checkpoint():
        _save(manifest_path, manifest)

    def optional(request, directory, images=None, validator=None):
        if continuing:
            return continuation.optional_request(request, directory, request_json, _save,
                images=images, validator=validator)
        return request_json(request, directory, images=images, validator=validator)

    for take in range(base, base + ledger['max_takes']):
        entry = ledger['attempts'].setdefault(str(take), {'take': take, 'status': 'preparing'})
        if entry['status'] == 'rejected' and not continuing:
            feedback = entry['decision']
            continue
        work = folder/'segments'/('%02d_take_%d' % (index, take))
        work.mkdir(parents=True, exist_ok=True)
        checkpoint()
        written = storyboard = None
        warnings = []
        for revision in range(2):
            stage('第%d/%d段：本地AI%s电影分镜' % (index, len(plan['beats']), '修订' if revision else '编写'), index-1)
            request = prompts.write_request(plan, beat, state, seconds, mode, visual_style=inputs['style'])
            if optimized:
                request = flow.continuity_request(request, previous, state)
            request[1]['automatic_normalization'] = True
            advisories = [item for item in manifest.get('plan_advisories', [])
                          if item.get('beat') in (0, index)]
            if advisories:
                request[1].update(plan_advisories=copy.deepcopy(advisories),
                    advisory_scope='仅作本段自然节奏与衔接参考，不改用户核心事件或原台词，不将建议升格硬要求')
            if feedback:
                request[1].update(previous_take_feedback=feedback,
                    retry_scope='只修复本段验收的具体失败，从同一采用首帧开始；不重写全故事，不承接被拒绝片的末态')
            if revision:
                request[1].update(previous_reply=written, storyboard_feedback=storyboard,
                    correction='按分镜预审的阻塞问题修正当前词稿，保留全部原对白、用户事件及合格内容',
                    correction_scope='仅当前段因果、对白时序、可拍性与容量')
            writer = optional if revision else request_json
            draft = writer(request, work/('writing' if not revision else 'writing_revision_1'), validator=validate_written)
            if continuing and draft.get('status') == 'unavailable':
                entry['writing_revision_warning'] = draft
                warnings.append('分镜修订格式不可用，保留已验证有效的原提示词')
                break
            written = prompts.compile_written(draft, request[1])
            dialogue_adjustments = [item for item in written.get('automatic_adjustments', [])
                if str(item.get('kind', '')).startswith('dialogue_text_')]
            if dialogue_adjustments:
                ambiguous = any(item.get('kind') == 'dialogue_text_overlap_warning' for item in dialogue_adjustments)
                report = dict(status='warning' if ambiguous else 'normalized',
                              quality_gate=False, adjustments=dialogue_adjustments)
                _save(work/'dialogue_text_adjustments.json', report)
                entry['dialogue_text_adjustments'] = copy.deepcopy(report)
                if ambiguous:
                    warnings.append('对白与普通描述存在文字重合，已保留原对白和动作并记录提醒继续')
            stage('第%d/%d段：本地分镜预审%s' % (index, len(plan['beats']), '复核' if revision else ''), index-1)
            review_request = quality.storyboard_request(inputs['story_direction'], inputs['style'],
                plan, beat, written, state, seconds)
            signature = review_request[1]['request_signature']
            storyboard = optional(review_request, work/'storyboard'/signature[:20], validator=quality.validate_storyboard)
            if storyboard.get('passed') or storyboard.get('status') == 'unavailable':
                break
        if continuing and storyboard.get('passed') is not True:
            warnings.append('分镜预审未通过或证据不可用，保留原报告继续生成')
        if not continuing and not storyboard['passed']:
            entry.update(status='blocked_before_generation', storyboard=storyboard)
            checkpoint()
            raise RuntimeError('第%d段分镜修订后仍未通过，未派发该段视频：%s' %
                (index, '；'.join(i['reason'] for i in storyboard['issues'] if i['severity'] == 'blocking')))
        director = work/'director.json'
        raw_path = work/'raw.mp4'
        bindings = dict(storyboard_signature=signature,
                        first_frame_signature=_frame_signature(previous), plan_signature=plan_signature(plan))
        if raw_path.exists() and (_load(director) != written or
                any(entry.get(key) != value for key, value in bindings.items())):
            raise RuntimeError('已有第%d段视频的分镜、首帧或计划输入发生变化，原片保留，未复用错误缓存' % index)
        _save(director, written)
        entry.update(bindings)
        if not raw_path.exists():
            stage('第%d/%d段：H3本地生成（第%d/%d次）' % (index, len(plan['beats']), take-base+1, ledger['max_takes']), index-1)
            entry.update(status='dispatched', dispatched_at=entry.get('dispatched_at', time.time()))
            checkpoint()  # Persist the attempt before submitting GPU work.
            result = client.video(written['prompt'], first_frame=previous, seconds=seconds,
                trace_dir=work/'generation', recovery_key='local-story:%s:%d:%d' % (tid, index, take),
                geometry=manifest['geometry'])
            from .video_geometry import preserve_display_ratio
            preserve_display_ratio(result['output_path'], raw_path, manifest['geometry'])
            _save(work/'generation_result.json', result)
        # All acceptance evidence comes from exactly the clip being delivered.
        adopted = media.adopt(raw_path, work/'adopted', end_seconds=seconds)
        clip_path = adopted['clip_path']
        stage('第%d/%d段：检查实际采用画面' % (index, len(plan['beats'])), index-1)
        samples = media.sample_frames(clip_path, work/'samples', seconds=seconds)
        observe = observation_request('segment', state, beat, inputs)
        observe[1].update(sample_timestamps=[s['time_seconds'] for s in samples],
            clip_duration_seconds=seconds, retention_policy='full_duration',
            repair_policy='先只记录实际可见事实；当前片是否采用由独立事件验收和本地ASR决定，不把预期动作当完成')
        observe[2]['properties'].update(retry_required={'type': 'boolean'}, retry_reason={'type': 'string'})
        observe[2]['required'].extend(['retry_required', 'retry_reason'])
        shown = flow.compact_samples(samples, adopted) if optimized else samples
        if optimized:
            observe = flow.compact_request(observe, shown)
        observation = optional(observe, work/'observation', [s['path'] for s in shown])
        if continuing and observation.get('status') == 'unavailable':
            warnings.append('序列画面观察不可用，实际动作和结果保持未知')
            observation = continuation.unknown_observation(observation, last_known=state)
        if optimized:
            observation, endpoint = flow.observation_pair(observation)
            _save(work/'endpoint'/'derived.json', endpoint)
        else:
            endpoint_req = observation_request('opening', observation, beat, inputs)
            endpoint_req[1]['stage_detail'] = '实际采用片的精确末帧，只记录当前姿势、持物和环境；动作完成由独立事件证据验收'
            endpoint = optional(endpoint_req, work/'endpoint', [adopted['anchor_path']])
        if continuing and endpoint.get('status') == 'unavailable':
            warnings.append('末帧观察不可用，人物与场景状态保持未知，仍使用实际精确末帧续接')
            endpoint = continuation.unknown_observation(endpoint, last_known=state)
        if flow.audio_enabled(inputs):
            stage('第%d/%d段：本地语音识别核对台词' % (index, len(plan['beats'])), index-1)
            audio = asr.review(clip_path, written['dialogue'], work/'audio_review')
        else:
            audio = flow.disabled_audio()
            _save(work/'audio_review'/'review.json', audio)
        if continuing:
            continuation.assert_audio_source_usable(audio)
        if audio['status'] == 'unavailable' and not continuing:
            entry.update(status='audio_unavailable', audio=audio)
            checkpoint()
            raise RuntimeError('本地台词核对暂不可用，当前视频保留，未浪费补拍：' +
                '；'.join(str(item.get('reason', item)) if isinstance(item, dict) else str(item)
                         for item in audio.get('issues', [])))

        def sample_records(rows):
            records = {}
            for n, row in enumerate(rows):
                key = 'frame_%06d' % row.get('frame_index', round(row['time_seconds']*24))
                records[key] = dict(row, id=key)
            return sorted(records.values(), key=lambda item: item['time_seconds'])

        records = sample_records(samples)
        visual = None
        for evidence_round in range(2 if storyboard.get('event_contract') else 0):
            stage('第%d/%d段：关键事件%s验收' % (index, len(plan['beats']), '补证' if evidence_round else '画面'), index-1)
            acceptance_request = quality.segment_acceptance_request(storyboard['event_contract'], written,
                records, endpoint, state, seconds, audio_review=audio)
            signature = acceptance_request[1]['request_signature']
            visual = optional(acceptance_request, work/'visual_acceptance'/signature[:20],
                [s['path'] for s in records], validator=quality.validate_acceptance)
            if visual.get('passed') or evidence_round or not visual.get('follow_up_times'):
                break
            extra = media.sample_at_times(clip_path, work/'extra_evidence', visual['follow_up_times'])
            records = sample_records(records + extra)
            if len(records) > 9:
                if continuing:
                    visual = dict(status='unavailable', quality_gate=False,
                        reason='补证超出本轮检查上限，证据保留，未完成核验',
                        sample_records=records, previous_review=visual)
                    _save(work/'visual_acceptance/over_limit_evidence.json', visual)
                    break
                raise RuntimeError('视觉证据超出9张上限，未静默丢弃证据')
        if continuing and visual is None:
            visual = dict(status='unavailable', quality_gate=False,
                reason='分镜预审未提供有效事件契约，未伪造契约或宣称画面验收通过')
            _save(work/'visual_acceptance/unavailable_contract.json', visual)
        decision = (continuation.adoption_decision(visual, audio) if continuing
                    else quality.adoption_decision(visual, audio))
        if continuing and warnings:
            decision['passed'] = False
            decision['reasons'] = list(dict.fromkeys(decision.get('reasons', []) + warnings))
            decision['warnings'] = copy.deepcopy(decision['reasons'])
            decision['quality_status'] = 'warning'
        evidence = dict(decision, policy_version=QUALITY_POLICY_VERSION, plan_signature=plan_signature(plan),
            first_frame_signature=_frame_signature(previous), clip_signature=_frame_signature(clip_path),
            anchor_signature=_frame_signature(adopted['anchor_path']),
            storyboard=storyboard, visual=visual, audio=audio)
        _save(work/'quality_decision.json', evidence)
        entry.update(status=('accepted' if decision['passed'] else
            'accepted_with_warnings' if continuing else 'rejected'), decision=evidence)
        checkpoint()
        if not decision['passed'] and not continuing:
            feedback = evidence
            manifest['quality_summary'] = {'passed': False, 'status': 'repairing',
                'accepted_segments': len(manifest['segments']), 'reasons': decision['reasons']}
            checkpoint()
            note('第%d段未采用，第%d/%d次：%s' % (index, take-base+1, ledger['max_takes'], '；'.join(decision['reasons'])))
            continue
        if continuing and not decision['passed']:
            note('第%d段已保存并带提醒继续：%s' % (index, '；'.join(decision['reasons'])))
        endpoint['actions'] = observation['actions']
        if continuing:
            uncertain_observation = (endpoint.get('status') == 'unavailable'
                                     or observation.get('status') == 'unavailable')
            endpoint['completed'] = [] if uncertain_observation else [event['explanation']
                for event in visual.get('events', []) if event.get('status') == 'observed']
            endpoint['beat_status'] = ('unknown' if uncertain_observation or visual.get('status') == 'unavailable'
                else 'achieved' if visual.get('passed') else 'partial' if endpoint['completed'] else 'not_observed')
        else:
            endpoint['completed'] = [event['explanation'] for event in visual['events'] if event['status'] == 'observed']
            endpoint['beat_status'] = 'achieved'
        endpoint['continuity_risks'] = list(dict.fromkeys(observation['continuity_risks']+endpoint['continuity_risks']))
        return dict(adopted, index=index, raw_path=str(raw_path), observation=endpoint,
            sequence_observation=observation, prompt_path=str(director), model_mode=mode, take=take,
            retention_policy='full_duration', quality=evidence)
    raise RuntimeError('第%d段已用完两次本地生成，仍未通过验收；已保留全部版本，未向后生成：%s' %
        (index, '；'.join((feedback or {}).get('reasons', ['未知验收错误']))))


def run_job(tid):
    from . import story_video_media as media
    from . import story_video_continuation as continuation
    from models import story_video_client as client
    task = runtime.load(tid)
    if task:
        runtime_reset.bind_thread(task.get('runtime_generation', 'initial'))
        runtime_reset.assert_current()
    if not task or task.get('kind') != KIND or _folder(task) is None:
        raise RuntimeError('故事视频任务检查点不存在')
    if task['inputs'].get('input_mode') == 'reference':
        from .story_video_reference import run_job as run_reference_job
        return run_reference_job(tid)
    handle = runtime.TaskHandle(tid)
    folder = _folder(task)
    inputs = task['inputs']
    automatic = inputs.get('quality_mode') == 'automatic'
    manifest_path = folder/'manifest.json'
    manifest = _load(manifest_path) if manifest_path.exists() else None
    inputs_signature = plan_signature(inputs)
    if manifest is None:
        manifest = {'version': 1, 'task_id': tid, 'inputs': inputs,
            'inputs_sha256': inputs_signature,
            'local_ai_only': True, 'segments': [], 'logs': [], 'created': time.time(),
            'offline_scope': 'Python network guard; local GGUF/native model files; no cloud fallback'}
        if inputs.get('flow_version', 1) >= 2 and inputs.get('input_mode') in ('image', 'text'):
            from .story_video_mode_prompts import PROFILE
            manifest['director_profile'] = PROFILE
    elif ((manifest.get('inputs_sha256') and manifest['inputs_sha256'] != inputs_signature)
            or isinstance(manifest.get('inputs'), dict)
            and plan_signature(manifest['inputs']) != inputs_signature):
        raise RuntimeError('故事任务输入变化，已有素材和视频保留，请新建任务或恢复原输入后继续')
    # The marker belongs to this worker/manifest, never to shared admission data.
    inputs = dict(inputs)
    inputs.pop('director_profile', None)
    if 'director_profile' in manifest:
        inputs['director_profile'] = manifest['director_profile']
    prompts = _prompt_builder(inputs)
    continuing = continuation.enabled(inputs)
    optimized = flow.enabled(inputs)
    if continuing:
        inputs['continuation_policy'] = manifest['continuation_policy'] = continuation.POLICY
    if automatic:
        manifest.setdefault('quality_policy', {'version': QUALITY_POLICY_VERSION, 'max_takes_per_segment': 2,
            'max_storyboard_revisions': 1, 'max_evidence_supplements': 1, 'audio': 'required_local_asr'})
        if continuing:
            manifest['quality_policy'].update(continuation_policy=continuation.POLICY,
                max_takes_per_segment=1, audio='local_asr_warning_only')

    def note(message):
        manifest['logs'].append({'time': time.strftime('%H:%M:%S'), 'message': message})
        _save(manifest_path, manifest)
        print(message, flush=True)

    def stage(label, done):
        _checkpoint(handle)
        handle.step(label, done, inputs['segment_count'], str(folder))
        note(label)

    try:
        if automatic and manifest['quality_policy']['version'] != QUALITY_POLICY_VERSION:
            raise RuntimeError('通用验收模板已升级；旧任务及全部视频保留，请新建任务使用新版，未重置旧任务补拍额度')
        state = manifest.get('opening_observation')
        opening = inputs['opening_image']
        if opening:
            saved_opening = folder/('opening' + Path(opening).suffix.lower())
            if not saved_opening.exists():
                shutil.copy2(opening, saved_opening)
            opening = str(saved_opening)
            if state is None:
                stage('本地视觉识别起始图', 0)
                opening_request = observation_request('opening', None, None, inputs)
                state = (continuation.optional_request(opening_request, folder/'opening_observation',
                    request_json, _save, images=[opening]) if continuing else
                    request_json(opening_request, folder/'opening_observation', [opening]))
                if continuing and state.get('status') == 'unavailable':
                    _continuation_warning(manifest, 'opening_observation', state)
                    state = continuation.unknown_observation(state)
                manifest['opening_observation'] = state
                _save(manifest_path, manifest)
        plan_request = prompts.plan_request(inputs['story_direction'], inputs['style'],
                inputs['segment_count'], inputs['segment_seconds'], state)
        if manifest.get('plan'):
            try:
                validate_schema(manifest['plan'], plan_request[2])
                validate_plan(manifest['plan'], plan_request[1])
            except (ValueError, KeyError, TypeError) as error:
                if manifest['segments']:
                    raise RuntimeError('已有视频的故事契约失效，保留全部结果，请新建任务：' + str(error)) from error
                manifest.setdefault('rejected_plan_versions', []).append(
                    {'plan': manifest.pop('plan'), 'error': str(error), 'time': time.time()})
                _save(manifest_path, manifest)
        if not manifest.get('plan'):
            stage('本地AI编写完整故事与电影分镜', 0)
            plan = request_json(plan_request, folder/'planning', validator=validate_plan)
            manifest['plan'] = plan
            manifest['plan_source'] = str((folder/'planning/accepted.json').resolve())
            _save(manifest_path, manifest)
        plan = _quality_gate(manifest, folder, inputs, state, stage)
        staged = inputs.get('flow_version', 1) >= 2
        if staged and 'geometry' not in manifest:
            from .video_geometry import from_image, dimensions
            aspect = inputs.get('aspect_ratio', '16:9')
            manifest['geometry'] = from_image(opening) if opening else dimensions(*map(int, aspect.split(':')))
            _save(manifest_path, manifest)
        if automatic and not manifest.get('approved'):
            manifest.update(approved=True, approved_plan_signature=plan_signature(plan),
                approval_source='local_plan_review')
            _save(manifest_path, manifest)
        if staged and opening and not manifest.get('approved'):
            _wait_for_user(handle, manifest, folder, 'story')
        if staged and not opening and manifest['segments'] and not manifest.get('approved'):
            _wait_for_user(handle, manifest, folder, 'preview')
        if staged and manifest.get('approved') and manifest.get('approved_plan_signature') != plan_signature(plan):
            raise RuntimeError('故事内容已变化，请重新确认')
        previous = opening or None
        for beat in plan['beats']:
            index = beat['index']
            accepted = next((s for s in manifest['segments'] if s['index'] == index), None)
            if accepted:
                if automatic:
                    _automatic_resume_check(accepted, plan, previous, inputs)
                else:
                    _guided_resume_media_check(accepted)
                previous, state = accepted['anchor_path'], accepted['observation']
                continue
            if automatic:
                row = _automatic_segment(tid, manifest, folder, inputs, plan, beat, previous, state, stage, note)
                manifest['segments'].append(row)
                manifest['segments'].sort(key=lambda item: item['index'])
                manifest['quality_summary'] = (_continuation_summary(manifest) if continuing else
                    {'passed': False, 'status': 'working',
                     'accepted_segments': len(manifest['segments']), 'reasons': []})
                _save(manifest_path, manifest)
                previous, state = row['anchor_path'], row['observation']
                note(('第%d段已保存并按实际末帧续接：%s' if continuing else
                      '第%d段已通过本地画面与台词验收并保存：%s') % (index, state['actual_visible']))
                continue
            # Bounded, local take repair.  A minor drift continues from observed facts.
            initial_take = int(manifest.get('takes', {}).get(str(index), 1)) - 1
            for take in (range(initial_take, initial_take+1) if staged else range(2)):
                work = folder/'segments'/('%02d_take_%d' % (index, take+1))
                work.mkdir(parents=True, exist_ok=True)
                stage('第%d/%d段：本地AI写电影提示词' % (index, len(plan['beats'])), index-1)
                mode = 'i2va' if previous else 't2va'
                req = prompts.write_request(plan, beat, state, inputs['segment_seconds'], mode,
                                            visual_style=inputs['style'])
                if optimized:
                    req = flow.continuity_request(req, previous, state)
                if take and not staged:
                    req[1]['previous_take_feedback'] = observation
                    req[1]['retry_scope'] = '只重做本段可见关键错误，从同一采用首帧开始，不重写全故事'
                written = request_json(req, work/'writing', validator=validate_written)
                written = prompts.compile_written(written, req[1])
                _save(work/'director.json', written)
                raw_path = work/'raw.mp4'
                if not raw_path.exists():
                    stage('第%d/%d段：H3本地生成' % (index, len(plan['beats'])), index-1)
                    geometry_options = {'geometry': manifest['geometry']} if staged else {}
                    result = client.video(written['prompt'], first_frame=previous,
                        seconds=inputs['segment_seconds'], trace_dir=work/'generation',
                        recovery_key='local-story:%s:%d:%d' % (tid, index, take+1), **geometry_options)
                    if staged:
                        from .video_geometry import preserve_display_ratio
                        preserve_display_ratio(result['output_path'], raw_path, manifest['geometry'])
                    else:
                        shutil.copy2(result['output_path'], raw_path)
                    _save(work/'generation_result.json', result)
                stage('第%d/%d段：本地视觉核对实际画面' % (index, len(plan['beats'])), index-1)
                prepared = (media.adopt(raw_path, work/'adopted', end_seconds=inputs['segment_seconds'])
                            if optimized else None)
                samples = media.sample_frames(prepared['clip_path'] if prepared else raw_path,
                    work/'samples', seconds=inputs['segment_seconds'])
                observe = observation_request('segment', state, beat, inputs)
                observe[1]['sample_timestamps'] = [s['time_seconds'] for s in samples]
                observe[1]['clip_duration_seconds'] = inputs['segment_seconds']
                observe[1]['repair_policy'] = (
                    'only set retry_required=true for clearly unusable/blank frames, duplicated main subjects, '
                    'or the core goal entirely missing. Minor shape/color drift should continue from actual facts. '
                    'This is the final available take.' if take else
                    'One local retry is allowed only for clearly unusable/blank frames, duplicate main subjects, '
                    'or the core goal entirely missing. Minor shape/color drift should continue from actual facts.')
                if staged:
                    observe[1]['repair_policy'] = '只记录实际画面偏差和尾部停顿。完整保留本段，不裁短、不自动重做或重试视频。'
                    observe[1]['retention_policy'] = 'full_duration'
                observe[2]['properties'].update(retry_required={'type': 'boolean'}, retry_reason={'type': 'string'})
                observe[2]['required'].extend(['retry_required', 'retry_reason'])
                shown = flow.compact_samples(samples, prepared) if optimized else samples
                if optimized:
                    observe = flow.compact_request(observe, shown)
                    observation = continuation.optional_request(observe, work/'observation', request_json,
                        _save, images=[s['path'] for s in shown])
                    if observation.get('status') == 'unavailable':
                        _continuation_warning(manifest, '第%d段 observation' % index, observation)
                        observation = continuation.unknown_observation(observation, last_known=state)
                else:
                    observation = request_json(observe, work/'observation', [s['path'] for s in samples])
                if observation.get('style_alignment') == 'mismatch' and index == 1 and not previous:
                    observation['retry_required'] = True
                    observation['retry_reason'] = '本地视觉发现首段风格不符：' + observation['actual_visual_style']
                if observation.get('retry_required') and not staged and take == 0:
                    note('第%d段本地视觉要求局部重试：%s' % (index, observation['retry_reason']))
                    continue
                if observation.get('retry_required') and not staged:
                    raise RuntimeError('第%d段两次尝试仍不可用，已保留检查点：%s' % (index, observation['retry_reason']))
                if staged and observation.get('retry_required'):
                    note('第%d段有重做建议，保留当前版本供你选择：%s' % (index, observation['retry_reason']))
                end = (inputs['segment_seconds'] if staged else
                       observation.get('keep_end_seconds') or inputs['segment_seconds'])
                end = min(float(end), inputs['segment_seconds'])
                if end < min(2, inputs['segment_seconds']):
                    end = inputs['segment_seconds']
                    observation['unknowns'].append('建议裁切过早，保留整段；实际采用末帧另行确认')
                adopted = prepared or media.adopt(raw_path, work/'adopted', end_seconds=end)
                # A short visual check of the exact adopted frame prevents an
                # earlier sample summary from being mistaken for the endpoint.
                if optimized:
                    observation, endpoint = flow.observation_pair(observation)
                    _save(work/'endpoint'/'derived.json', endpoint)
                else:
                    stage('第%d/%d段：确认真实续接末帧' % (index, len(plan['beats'])), index-1)
                    endpoint_request = observation_request('opening', observation, beat, inputs)
                    endpoint_request[1]['stage_detail'] = '精确采用末帧：只记录这一帧当前姿势/持物/水态，动作完成仍以上一步序列证据为准'
                    if staged:
                        endpoint_request[1]['stage_detail'] = '完整时长末帧：本段未按视觉建议截短；只记录这一帧的真实姿势、持物及环境状态，下一段从此帧接续'
                    endpoint = request_json(endpoint_request, work/'endpoint', [adopted['anchor_path']])
                endpoint['actions'] = observation['actions']
                endpoint['completed'] = observation['completed']
                endpoint['beat_status'] = observation['beat_status']
                endpoint['continuity_risks'] = list(dict.fromkeys(observation['continuity_risks']+endpoint['continuity_risks']))
                row = dict(adopted, index=index, raw_path=str(raw_path), observation=endpoint,
                           sequence_observation=observation, prompt_path=str(work/'director.json'),
                           model_mode=mode, take=take+1,
                           retention_policy='full_duration' if staged else 'suggested_endpoint')
                for key, path in (('raw', raw_path), ('clip', row.get('clip_path') or row.get('video_path')),
                                  ('anchor', row['anchor_path'])):
                    if not row.get(key + '_sha256'):
                        row[key + '_sha256'] = _frame_signature(path)
                if optimized:
                    if flow.audio_enabled(inputs):
                        from . import story_video_asr as asr
                        row['asr'] = asr.review(adopted['clip_path'], written['dialogue'], work/'audio_review')
                        continuation.assert_audio_source_usable(row['asr'])
                        if row['asr'].get('status') != 'passed':
                            _continuation_warning(manifest, '第%d段 asr' % index, row['asr'])
                    else:
                        row['asr'] = flow.disabled_audio()
                        _save(work/'audio_review'/'review.json', row['asr'])
                manifest['segments'].append(row)
                manifest['segments'].sort(key=lambda item: item['index'])
                _save(manifest_path, manifest)
                previous, state = row['anchor_path'], endpoint
                note('第%d段已保存：%s' % (index, endpoint['actual_visible']))
                handle.step('已完成%d/%d段' % (index, len(plan['beats'])), index, len(plan['beats']))
                if staged and not opening and index == 1 and not manifest.get('approved'):
                    _wait_for_user(handle, manifest, folder, 'preview')
                break
        stage('拼接与完整音画解码验收', len(plan['beats']))
        movie_name = 'movie_revision_%d' % len(manifest['history']) if manifest.get('history') else 'movie'
        output = media.join(manifest['segments'], folder/movie_name)
        output.update(title=plan['title'], segments=len(manifest['segments']),
                      manifest_url=_url(manifest_path), local_ai_only=True,
                      visual_review='local visual model, sampled frames and exact adopted endpoint',
                      audio_review=('未开启语音核对；保留原视频音轨' if not flow.audio_enabled(inputs) else
                          '本地ASR逐句内容核对；未人工审听或验证中文口型' if automatic or optimized else
                                    '音轨完整解码；未审听或验证中文口型'),
                      network_audit_url=_url(folder/'network_audit.jsonl'))
        manifest['output'] = output
        if automatic:
            manifest['quality_summary'] = (_continuation_summary(manifest, complete=True) if continuing else
                {'passed': True, 'status': 'passed',
                'accepted_segments': len(manifest['segments']), 'reasons': [],
                'rejected_takes': sum(e['status'] == 'rejected' for batch in manifest.get('quality_attempts', {}).values()
                                      for e in batch['attempts'].values())})
            output['quality_passed'] = manifest['quality_summary']['passed']
            if continuing:
                output['generation_completed'] = True
                output['quality_status'] = manifest['quality_summary']['status']
        manifest['ended'] = time.time()
        note('本地故事视频已完成，原片与采用版完整音画解码通过')
        _checkpoint(handle)
        return output
    except ControlStop:
        note('任务已在检查点停止，结果完整保留')
        raise
    except Exception as error:
        manifest['last_error'] = str(error)
        if automatic:
            manifest['quality_summary'] = {'passed': False, 'status': 'failed',
                'accepted_segments': len(manifest['segments']), 'reasons': [str(error)]}
        note('任务停止：' + str(error))
        raise
    finally:
        client.preserve_release()


runtime.RUNNERS[KIND] = _runner
