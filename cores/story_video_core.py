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

from . import runtime_core as runtime, store

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / 'outputs' / 'story_video'
KIND = 'story_video'
ASPECT_RATIOS = ('16:9', '9:16', '1:1', '4:5', '3:4')
_ADMISSION = threading.RLock()
_CHILDREN = {}


def capabilities():
    from . import paths
    keys = ('llama_server_exe', 'model_gguf', 'mmproj_gguf', 'h3_root')
    missing = [key for key in keys if not paths.get(key) or not Path(paths.get(key)).exists()]
    return {'available': not missing, 'missing': missing, 'local_only': True,
            'inputs': ['image_and_text', 'text'], 'segment_count': {'min': 2, 'max': 10, 'default': 10},
            'segment_seconds': 15, 'width': 1024, 'height': 576,
            'flow_version': 2, 'aspect': 'opening_image; text selectable, defaults to 16:9',
            'aspect_ratios': list(ASPECT_RATIOS),
            'steps': 10, 'quality': 'preview',
            'vision': 'local_qwen', 'audio_review': 'not implemented',
            'offline_scope': 'loopback-only Python worker and descendants; local model files'}


def validate_inputs(data):
    result = {'story_direction': str(data.get('story_direction') or '').strip(),
              'style': str(data.get('style') or '电影写实，自然表演，镜头服务剧情').strip()}
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
            'plan': manifest.get('plan'), 'review': manifest.get('plan_review'),
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
        if not task or task.get('kind') != KIND:
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
    with (folder/'worker.log').open('ab') as log:
        process = subprocess.Popen(command, cwd=str(ROOT), env=build_env(log_path),
                                   stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        _CHILDREN[task['id']] = process
        try:
            while process.poll() is None:
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
    counted = sum(len(re.sub(r'[^\w\u4e00-\u9fff]', '', line['text'])) for line in lines)
    if counted > budget['max_dialogue_characters_total']:
        raise ValueError('对白超过自然表演预算')
    duration = counted / budget['speech_characters_per_second'] + len(lines)*budget['speech_pause_seconds_per_line']
    if duration > budget['max_dialogue_seconds'] + .01:
        raise ValueError('对白无足够停顿时间')
    last = 0
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
            last = line['end']


def validate_plan(plan, payload):
    count, seconds = payload['count'], payload['seconds']
    if [b['index'] for b in plan['beats']] != list(range(1, count+1)):
        raise ValueError('分段编号必须连续且与请求相符')
    ids = [c['id'] for c in plan['characters']]
    if len(ids) != len(set(ids)):
        raise ValueError('人物身份编号重复')
    all_lines = {re.sub(r'\W', '', line['text']).casefold()
                 for item in plan['beats'] for line in item['dialogue']}
    for beat in plan['beats']:
        if not set(beat['cast']) <= set(ids) or beat['seconds'] != seconds:
            raise ValueError('分段人物或时长不符')
        own_lines = {re.sub(r'\W', '', line['text']) for line in beat['dialogue']}
        quoted_lines = re.findall(r'[“「"]([^”」"]+)[”」"]', beat['action'])
        if any(re.sub(r'\W', '', line) not in own_lines for line in quoted_lines):
            wrong = [line for line in quoted_lines if re.sub(r'\W', '', line) not in own_lines]
            raise ValueError('动作描述不能提前使用其他段对白或新增台词：第%d段action请删除原话%s及其提前回应，原话只能放所属段dialogue；action只写本段可见动作' %
                             (beat['index'], json.dumps(wrong, ensure_ascii=False)))
        own_normalized = {line.casefold() for line in own_lines}
        action_normalized = re.sub(r'\W', '', beat['action']).casefold()
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


def validate_plan_review(review, payload):
    if not isinstance(review['passed'], bool) or review['passed'] != (not review['issues']):
        raise ValueError('审阅通过时issues必须为空，未通过时必须给出具体问题')


def plan_signature(plan):
    encoded = json.dumps(plan, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _quality_gate(manifest, folder, inputs, opening_state, stage):
    """One independent local review and at most one whole-plan revision."""
    from . import story_video_prompts as prompts
    if inputs.get('flow_version', 1) >= 2:
        if not manifest.get('plan_review'):
            stage('本地AI审阅故事（意见供你参考）', 0)
            request = prompts.review_request(manifest['plan'], inputs['story_direction'], inputs['style'],
                inputs['segment_count'], inputs['segment_seconds'], opening_state)
            review = request_json(request, folder/'planning/review_1', validator=validate_plan_review)
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
        validate_plan_review(stored, {})
        return current
    round_no = 2 if stored.get('round') == 2 else 1
    plan_source = manifest.get('plan_source') or str((folder/'planning/accepted.json').resolve())
    manifest['plan_source'] = plan_source
    while True:
        stage('本地AI独立审阅剧本' if round_no == 1 else '本地AI复审修改稿', 0)
        review_dir = folder/'planning'/('review_%d' % round_no)
        request = prompts.review_request(current, inputs['story_direction'], inputs['style'],
            inputs['segment_count'], inputs['segment_seconds'], opening_state)
        review = request_json(request, review_dir, validator=validate_plan_review)
        manifest['plan_review'] = dict(review, round=round_no, pending=False,
            plan_signature=signature, plan_source=plan_source,
            review_source=str((review_dir/'accepted.json').resolve()))
        _save(folder/'manifest.json', manifest)
        if review['passed']:
            return current
        if round_no == 2:
            raise RuntimeError('本地AI剧本复审仍未通过，原稿、修改稿和审阅均已保留，未开始生成视频')
        stage('本地AI按审阅修改整稿（仅一次）', 0)
        revision_request = prompts.plan_request(inputs['story_direction'], inputs['style'],
            inputs['segment_count'], inputs['segment_seconds'], opening_state)
        revision_request[1].update(previous_draft=copy.deepcopy(current),
            review_feedback=copy.deepcopy(review),
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


def validate_written(obj, payload):
    from .story_video_prompts import compile_written, I2VA_FIRST_FRAME
    obj = compile_written(obj, payload)
    prompt, seconds = obj['prompt'], payload['seconds']
    style = str(payload.get('visual_style') or '')
    if ('真人' in style or re.search(r'live.action|photoreal', style, re.I)) and not re.search(
            r'live[- ]action|photoreal|real (?:adult )?actors', obj['integrated_multimodal_description'], re.I):
        raise ValueError('用户要求真人写实，镜头正文必须明确真人电影/真实演员风格')
    fields = ['integrated_multimodal_description:', 'overall_soundscape:', 'non_diegetic_music:']
    positions = [prompt.find(field) for field in fields]
    headers = re.findall(r'(?m)^(integrated_multimodal_description|overall_soundscape|non_diegetic_music):', prompt)
    if (any(pos < 0 for pos in positions) or positions != sorted(positions)
            or headers != [field[:-1] for field in fields] or '[Shot 1]' not in prompt):
        raise ValueError('H3三字段或第一镜格式不符')
    if payload['mode'] == 'i2va' and not prompt.startswith(I2VA_FIRST_FRAME):
        raise ValueError('续接首帧声明缺失')
    references = re.findall(r'<(Picture|Subject|Video|Audio)\s+(\d+)>', prompt)
    if payload['mode'] == 't2va' and (references or not prompt.startswith(fields[0])):
        raise ValueError('纯文字首段不能引用不存在的参考图')
    if payload['mode'] == 'i2va' and any(kind != 'Picture' or number != '1' for kind, number in references):
        raise ValueError('续接只有一张当前首帧')
    description = prompt[positions[0]+len(fields[0]):positions[1]]
    shots = [int(number) for number in re.findall(r'\[Shot\s+(\d+)\]', description)]
    if shots not in ([1], [1, 2]):
        raise ValueError('本段只允许一至两个连续编号镜头')
    if len(shots) == 2:
        cut = re.search(r'\[Shot 2\]\s+At (\d{2}):(\d{2}\.\d{3}),', description)
        if not cut or not 0 < int(cut[1])*60+float(cut[2]) < seconds:
            raise ValueError('切镜时间格式不符或超出本段时长')
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
    from . import story_video_prompts as prompts
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


def run_job(tid):
    from . import story_video_prompts as prompts, story_video_media as media
    from models import story_video_client as client
    task = runtime.load(tid)
    if not task or task.get('kind') != KIND or _folder(task) is None:
        raise RuntimeError('故事视频任务检查点不存在')
    handle = runtime.TaskHandle(tid)
    folder = _folder(task)
    inputs = task['inputs']
    manifest_path = folder/'manifest.json'
    manifest = _load(manifest_path) or {'version': 1, 'task_id': tid, 'inputs': inputs,
        'local_ai_only': True, 'segments': [], 'logs': [], 'created': time.time(),
        'offline_scope': 'Python network guard; local GGUF/native model files; no cloud fallback'}

    def note(message):
        manifest['logs'].append({'time': time.strftime('%H:%M:%S'), 'message': message})
        _save(manifest_path, manifest)
        print(message, flush=True)

    def stage(label, done):
        _checkpoint(handle)
        handle.step(label, done, inputs['segment_count'], str(folder))
        note(label)

    try:
        state = manifest.get('opening_observation')
        opening = inputs['opening_image']
        if opening:
            saved_opening = folder/('opening' + Path(opening).suffix.lower())
            if not saved_opening.exists():
                shutil.copy2(opening, saved_opening)
            opening = str(saved_opening)
            if state is None:
                stage('本地视觉识别起始图', 0)
                state = request_json(observation_request('opening', None, None, inputs), folder/'opening_observation', [opening])
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
                previous, state = accepted['anchor_path'], accepted['observation']
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
                samples = media.sample_frames(raw_path, work/'samples', seconds=inputs['segment_seconds'])
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
                observation = request_json(observe, work/'observation', [s['path'] for s in samples])
                if observation.get('style_alignment') == 'mismatch' and index == 1 and not previous:
                    observation['retry_required'] = True
                    observation['retry_reason'] = '本地视觉发现首段风格不符：' + observation['actual_visual_style']
                if observation['retry_required'] and not staged and take == 0:
                    note('第%d段本地视觉要求局部重试：%s' % (index, observation['retry_reason']))
                    continue
                if observation['retry_required'] and not staged:
                    raise RuntimeError('第%d段两次尝试仍不可用，已保留检查点：%s' % (index, observation['retry_reason']))
                if staged and observation['retry_required']:
                    note('第%d段有重做建议，保留当前版本供你选择：%s' % (index, observation['retry_reason']))
                end = (inputs['segment_seconds'] if staged else
                       observation.get('keep_end_seconds') or inputs['segment_seconds'])
                end = min(float(end), inputs['segment_seconds'])
                if end < min(2, inputs['segment_seconds']):
                    end = inputs['segment_seconds']
                    observation['unknowns'].append('建议裁切过早，保留整段；实际采用末帧另行确认')
                adopted = media.adopt(raw_path, work/'adopted', end_seconds=end)
                # A short visual check of the exact adopted frame prevents an
                # earlier sample summary from being mistaken for the endpoint.
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
                      audio_review='音轨完整解码；未审听或验证中文口型',
                      network_audit_url=_url(folder/'network_audit.jsonl'))
        manifest['output'] = output
        manifest['ended'] = time.time()
        note('本地故事视频已完成，原片与采用版完整音画解码通过')
        _checkpoint(handle)
        return output
    except ControlStop:
        note('任务已在检查点停止，结果完整保留')
        raise
    except Exception as error:
        manifest['last_error'] = str(error)
        note('任务停止：' + str(error))
        raise
    finally:
        client.preserve_release()


runtime.RUNNERS[KIND] = _runner
