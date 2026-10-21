"""Local reference-story runtime, integrated with the existing task worker.

One story, source-owned dialogue, one H3 submission per explicit segment take.
Quality reviews are evidence, never automatic rerolls or fabricated passes.
"""
from contextlib import contextmanager
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import time
import traceback

from . import story_video_core as core, story_video_reference_prompts as prompts
from . import story_video_characters as characters, story_video_media as media
from . import story_video_asr as asr, story_video_quality as quality, runtime_reset
from . import story_video_continuation as continuation
from . import story_video_flow as flow
from models import story_video_client as client, qwen_client, h3_client, gpu_manager

VERSION = 'reference-story-runtime-v1'
EMPTY_OR_LENGTH = '本地故事模型回复为空或被截断，已保留原始回复'


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _save(path, value):
    # Share the text/image checkpoint writer, including bounded retries for
    # Windows readers which briefly prevent atomic file replacement.
    core._save(path, value)


def _sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _signature(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def _warning(manifest, stage, report, reason=None):
    reason = reason or report.get('reason') or '；'.join(
        str(item.get('reason', item)) if isinstance(item, dict) else str(item)
        for item in report.get('issues', [])) or '本地质量意见未通过或证据不可用'
    warning = dict(stage=stage, reason=reason)
    if warning not in manifest.setdefault('continuation_warnings', []):
        manifest['continuation_warnings'].append(warning)


def _summary(manifest, complete=False):
    warnings = ['%s：%s' % (item['stage'], item['reason'])
                for item in manifest.get('continuation_warnings', [])]
    # Older accepted rows remain unchanged; their raw reviews still contribute
    # to the summary when a completed task is reopened under this policy.
    for row in manifest['segments']:
        for label, report in (('storyboard', row.get('storyboard', {})),
                              ('visual', row.get('visual_review', {})),
                              ('asr', row.get('asr', {}))):
            failed = (report.get('status') not in (None, 'passed', 'disabled') if label == 'asr'
                      else report.get('status') == 'unavailable' or report.get('passed') is False)
            if failed:
                reason = report.get('reason') or '；'.join(
                    str(item.get('reason', item)) if isinstance(item, dict) else str(item)
                    for item in report.get('issues', [])) or '未通过或证据不可用'
                warnings.append('第%d段 %s：%s' % (row['index'], label, reason))
    warnings = list(dict.fromkeys(warnings))
    return dict(status=('completed_with_warnings' if complete else 'working_with_warnings')
                    if warnings else 'recorded', quality_gate=False,
                generation_completed=complete, accepted_segments=len(manifest['segments']),
                reasons=warnings, warnings=warnings)


def validate_reference_plan(plan, payload, source_contract, cards):
    """Allow declared silent props in reference cast without rewriting the plan.

    The shared validator still checks all character/speech/timing constraints.
    Its temporary cast view excludes only declared props which are not also
    character IDs; unknown IDs remain present and are rejected normally.
    """
    validation_plan = copy.deepcopy(plan)
    character_ids = {row['id'] for row in plan['characters']}
    prop_ids = {row['id'] for row in plan.get('props', [])
                if isinstance(row, dict) and isinstance(row.get('id'), str)}
    silent_prop_ids = prop_ids - character_ids
    for beat in validation_plan['beats']:
        beat['cast'] = [identity for identity in beat['cast'] if identity not in silent_prop_ids]
    core.validate_plan(validation_plan, payload)
    characters.validate_plan_source(plan, source_contract, cards)


def _plan_usable_schema(schema):
    """Keep required shape; speech-capacity estimates belong to quality review."""
    result = copy.deepcopy(schema)
    dialogue = result['properties']['beats']['items']['properties']['dialogue']
    dialogue.pop('maxItems', None)
    dialogue['items']['properties']['text'].pop('maxLength', None)
    return result


def _validate_usable_reference_plan(plan, payload, cards):
    """Require executable structure and actual reference identities, not prose fidelity."""
    normalized, aliases = characters._identity_index(cards)
    by_id = {card['id']: card for card in normalized}
    ids = [row['id'] for row in plan['characters']]
    if len(ids) != len(set(ids)):
        raise ValueError('人物身份编号重复')
    if any(row['id'] not in by_id or row['name'] != by_id[row['id']]['name']
           for row in plan['characters']):
        raise ValueError('计划人物ID或名字与固定人物卡不一致')
    if [beat['index'] for beat in plan['beats']] != list(range(1, payload['count'] + 1)):
        raise ValueError('分段编号必须连续且与请求相符')
    props = {row['id'] for row in plan.get('props', [])}
    for beat in plan['beats']:
        seconds = beat['seconds']
        if (type(seconds) not in (int, float) or not math.isfinite(seconds)
                or seconds != payload['seconds'] or not set(beat['cast']) <= set(ids) | props):
            raise ValueError('分段人物或时长不符')
        last = 0
        for line in beat['dialogue']:
            identity = aliases.get(line['speaker'])
            if identity not in ids or identity not in beat['cast']:
                raise ValueError('对白说话者必须绑定本段固定人物参考')
            if 'start' in line or 'end' in line:
                start, end = line.get('start'), line.get('end')
                if (type(start) not in (int, float) or type(end) not in (int, float)
                        or not math.isfinite(start) or not math.isfinite(end)
                        or not last <= start < end <= seconds):
                    raise ValueError('对白时间重叠或越界')
                last = end


def _plan_source_audit(plan, payload, contract, cards, directory):
    """Record disagreement without rewriting source evidence or claiming it passed."""
    issues = []
    for check in (lambda: validate_reference_plan(plan, payload, contract, cards),
                  lambda: characters.validate_plan_source(plan, contract, cards)):
        try:
            check()
        except (ValueError, KeyError, TypeError) as error:
            issue = dict(code=getattr(error, 'code', 'plan_quality'), reason=str(error),
                         details=copy.deepcopy(getattr(error, 'details', {})))
            if issue not in issues:
                issues.append(issue)
    unresolved = contract.get('unresolved_sources') or contract.get('source_report', {}).get('issues')
    if unresolved:
        issues.append(dict(code='source_evidence_unavailable', reason='部分正文发声来源尚未确定，保留原文与未知记录',
                           details=copy.deepcopy(unresolved)))
    _, aliases = characters._identity_index(cards)
    return dict(status='warning' if issues else 'verified', quality_gate=False, issues=issues,
        expected=[[row['speaker_id'], row['text']] for row in contract.get('dialogue', [])],
        actual=[[aliases[line['speaker']], line['text']]
                for beat in plan['beats'] for line in beat['dialogue']],
        source_evidence_status=contract.get('evidence_status', 'recorded'),
        raw_response_paths=[str(path) for path in sorted(Path(directory).glob('call_*/*/response.json'))])


def _request_plan(session, request, directory, source_contract, cards):
    """Repair twice, then adopt a usable real draft with explicit quality warnings."""
    directory = Path(directory)
    candidates = []

    def usable(plan, payload):
        core.validate_schema(plan, _plan_usable_schema(request[2]))
        _validate_usable_reference_plan(plan, payload, cards)

    def validate(plan, payload):
        usable(plan, payload)
        candidates.append(copy.deepcopy(plan))
        validate_reference_plan(plan, payload, source_contract, cards)

    binding = dict(system=request[0], payload=request[1], schema=request[2], images=[])
    same = (directory / 'binding.json').exists() and _read(directory / 'binding.json') == binding
    def recover():
        requests = list(directory.glob('call_*/*/request.json'))
        latest = max(requests, key=lambda path: path.stat().st_mtime_ns) if requests else None
        # Recovery must never hide a newer missing response or service error.
        if (latest is None or not latest.with_name('response.json').exists()
                or latest.with_name('error.json').exists() or session.failure):
            return
        for path in sorted(directory.glob('call_*/*/response.json'),
                           key=lambda path: path.stat().st_mtime_ns, reverse=True):
            try:
                response = _read(path)
                if response.get('meta', {}).get('finish_reason') == 'length':
                    continue
                text = re.sub(r'^```(?:json)?\s*|\s*```$', '', str(response.get('text', '')).strip())
                plan = json.loads(text)
                usable(plan, request[1])
                candidates.append(plan)
                break
            except (ValueError, KeyError, TypeError):
                continue
    exhausted = bool(list(directory.glob('call_2_*/contract_error.json')))
    if same and exhausted and not (directory / 'accepted.json').exists():
        recover()
    if not candidates:
        try:
            plan = session.ask(request, directory, validator=validate)
        except RuntimeError as error:
            if not continuation.is_optional_error(error) or session.failure:
                raise
            # A capacity-schema failure happens before the strict validator;
            # inspect only retained real replies, never synthesize a fallback.
            if not candidates:
                recover()
            if not candidates:
                raise
            session.guard()
            plan = candidates[-1]
    else:
        session.guard()
        plan = candidates[-1]
    usable(plan, request[1])
    audit = _plan_source_audit(plan, request[1], source_contract, cards, directory)
    _save(directory / 'source_validation.json', audit)
    if audit['status'] == 'warning':
        _save(directory / 'adopted_with_warnings.json', dict(plan=plan,
            request_sha256=_signature(binding), source_validation=audit))
    return plan


def _freeze_assets(inputs, folder, manifest):
    records = manifest.setdefault('reference_assets', [])
    cards = characters.normalize_cards(inputs['character_cards'])
    supplied = [(card['image_path'], 'character_%02d' % index) for index, card in enumerate(cards, 1)]
    scene = str(inputs.get('initial_scene') or '').strip()
    if scene:
        supplied.append((scene, 'initial_scene'))
    if records:
        if len(records) != len(supplied):
            raise RuntimeError('参考素材数量变化，已有结果保留')
        for record, (source, _) in zip(records, supplied):
            if record['source'] != source or _sha(record['path']) != record['sha256']:
                raise RuntimeError('任务参考素材或其快照变化，已有结果保留')
    else:
        for source, label in supplied:
            source = Path(source)
            target = folder / 'references' / (label + source.suffix.lower())
            expected = _sha(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and _sha(target) != expected:
                raise RuntimeError('未记录的参考快照与原图不同，未覆盖：' + str(target))
            if not target.exists():
                shutil.copy2(source, target)
            records.append(dict(source=str(source), path=str(target), sha256=expected))
    for card, record in zip(cards, records):
        card['image_path'] = record['path']
    return cards, records[-1]['path'] if scene else None


def _latest_failed_quality_response(folder):
    """Inspect only the latest native request, not an older truncated attempt."""
    requests = list(Path(folder).glob('call_*/*/request.json'))
    if not requests:
        return None
    latest = max(requests, key=lambda path: path.stat().st_mtime_ns)
    response = latest.with_name('response.json')
    if not response.exists():
        return None  # A newer network/service failure must never be swallowed.
    data = _read(response)
    if data.get('meta', {}).get('finish_reason') == 'length' or not str(data.get('text') or '').strip():
        return dict(response_path=str(response), finish_reason=data.get('meta', {}).get('finish_reason'),
                    content_length=len(str(data.get('text') or '')))
    return None


class _Session:
    def __init__(self, tid, handle, folder, inputs, manifest):
        self.tid, self.handle, self.folder, self.inputs, self.manifest = tid, handle, folder, inputs, manifest
        self.stage_name, self.failure = 'initial', None

    def checkpoint(self):
        _save(self.folder / 'manifest.json', self.manifest)

    def guard(self):
        runtime_reset.assert_current()
        core._checkpoint(self.handle)
        gpu_manager.assert_local_run_allowed()
        client._loopback(qwen_client.QWEN_URL)
        client._loopback(h3_client.H3_URL)

    def stage(self, label, index=0):
        self.guard()
        self.stage_name = label
        self.handle.step(label, index, self.inputs['segment_count'], str(self.folder))
        self.manifest.setdefault('logs', []).append(dict(time=time.strftime('%H:%M:%S'), message=label))
        self.checkpoint()
        print(label, flush=True)

    @contextmanager
    def models(self):
        original_chat, original_acquire = qwen_client.chat, gpu_manager.acquire

        def acquire(who, *args, **kwargs):
            self.guard()
            return original_acquire(who, *args, **kwargs)

        def chat(system, user, **options):
            try:
                self.guard()
                count = self.manifest['actual_model_calls']
                if count >= self.manifest['reference_policy']['max_text_vision_calls']:
                    raise RuntimeError('已达到本次本地文本/视觉调用预算，全部结果保留')
                number = count + 1
                trace = self.folder / 'actual_calls' / ('%03d' % number)
                _save(trace / 'request.json', dict(stage=self.stage_name, endpoint=qwen_client.QWEN_URL,
                    model=qwen_client.QWEN_MODEL, system=system, user=user, options=options))
                self.manifest['actual_model_calls'] = number
                self.checkpoint()  # Before any actual call, including bounded repairs.
                result = original_chat(system, user, **options)
                text, meta = result if isinstance(result, tuple) else (result, {})
                _save(trace / 'response.json', dict(text=text, meta=meta))
                return result
            except Exception as error:
                self.failure = error
                if 'trace' in locals():
                    _save(trace / 'error.json', dict(type=type(error).__name__, message=str(error)))
                raise

        qwen_client.chat, gpu_manager.acquire = chat, acquire
        try:
            yield
        finally:
            qwen_client.chat, gpu_manager.acquire = original_chat, original_acquire

    def cached(self, name, inputs, action):
        target = self.folder / name / 'result.json'
        receipt = target.with_name('receipt.json')
        identity = _signature(inputs)
        if target.exists():
            bound = _read(receipt)
            if bound['input_sha256'] != identity or bound['result_sha256'] != _sha(target):
                raise RuntimeError('本地阶段缓存或输入发生变化：' + name)
            return _read(target)
        labels = {'reference_observation': '本地识别人物与场景参考', 'source_contract': '核对原文台词与说话人',
                  'planning': '本地安排完整故事分段', 'plan_review': '记录本地剧本预审',
                  'author_plan': '本地创作完整故事', 'author_writing': '本地编写故事正文'}
        self.stage(labels.get(name, name))
        _save(target.with_name('input.json'), inputs)
        result = action()
        if self.failure:
            _save(target.with_name('incomplete_%d.json' % time.time_ns()), result)
            raise self.failure
        _save(target, result)
        _save(receipt, dict(input_sha256=identity, result_sha256=_sha(target)))
        return result

    def ask(self, request, folder, images=None, validator=None):
        self.guard()
        folder = Path(folder)
        binding = dict(system=request[0], payload=request[1], schema=request[2],
                       images=[dict(path=str(path), sha256=_sha(path)) for path in (images or [])])
        target = folder / 'binding.json'
        if target.exists() and _read(target) != binding:
            raise RuntimeError('模型请求或证据变化，未复用旧回复：' + str(folder))
        _save(target, binding)
        return core.request_json(request, folder, images=images, validator=validator)

    def review(self, request, folder, images=None, validator=None):
        folder = Path(folder)
        identity = _signature(dict(request=request,
            images=[dict(path=str(path), sha256=_sha(path)) for path in (images or [])]))
        marker = folder / 'unavailable.json'
        if marker.exists():
            result = _read(marker)
            if result.get('request_sha256') != identity:
                raise RuntimeError('不可用质量记录的输入变化')
            return result

        def unavailable(reason, evidence=None):
            value = dict(status='unavailable', reason=reason, request_sha256=identity,
                raw_response_directory=str(folder), technical_evidence=evidence, quality_gate=False)
            _save(marker, value)
            return value

        binding = dict(system=request[0], payload=request[1], schema=request[2],
                       images=[dict(path=str(path), sha256=_sha(path)) for path in (images or [])])
        same = (folder / 'binding.json').exists() and _read(folder / 'binding.json') == binding
        if same and not (folder / 'accepted.json').exists():
            errors = sorted(folder.glob('call_2_*/contract_error.json'))
            if errors:
                error = RuntimeError('本地模型格式修复仍未通过：' + str(_read(errors[-1]).get('error', '')))
                if not continuation.is_optional_error(error):
                    raise error
                return unavailable('既有同请求的质量记录格式修复已耗尽', dict(error_path=str(errors[-1])))
            evidence = _latest_failed_quality_response(folder)
            if evidence:
                return unavailable('既有同请求的可选质量报告为空或截断，原答保留', evidence)
        try:
            return self.ask(request, folder, images, validator)
        except RuntimeError as error:
            if not continuation.is_optional_error(error):
                raise
            if str(error) == EMPTY_OR_LENGTH:
                evidence = _latest_failed_quality_response(folder)
                if evidence:
                    return unavailable(str(error), evidence)
                raise
            return unavailable(str(error))

    def writer(self, system, user, mt=9000, temperature=.75, _tries=1, reasoning=False, **options):
        """Preserve formal UW settings; one bounded technical length retry."""
        identity = _signature(dict(system=system, user=user, mt=mt, temperature=temperature,
                                   reasoning=reasoning, options=options))
        folder = self.folder / 'author_calls' / identity[:24]
        target = folder / 'accepted.json'
        if target.exists():
            saved = _read(target)
            if saved['request_sha256'] != identity:
                raise RuntimeError('写作调用缓存变化')
            return saved['text']
        try:
            for attempt in range(2):
                with gpu_manager.GPU_LOCK:
                    try:
                        with client._offline():
                            text, meta = qwen_client.chat(system, user, temperature=temperature,
                                max_tokens=mt, reasoning=reasoning, return_meta=True, timeout=900, **options)
                    finally:
                        client.preserve_release('qwen')
                _save(folder / ('response_%d.json' % attempt), dict(text=text, meta=meta))
                if meta.get('finish_reason') != 'length':
                    if not str(text or '').strip():
                        raise RuntimeError('本地正文模型返回空内容')
                    _save(target, dict(request_sha256=identity, text=text.strip()))
                    return text.strip()
                if attempt:
                    raise RuntimeError('本地正文一次技术长度修复仍未完成，原答保留')
                if reasoning and int(meta.get('reasoning_content_len') or 0) > int(meta.get('content_len') or len(text)):
                    reasoning = False
                else:
                    mt = min(16000, max(4000, int(mt) * 2))
                _save(folder / 'technical_retry.json', dict(reason='同一请求长度修复',
                    creative_reroll=False, next_reasoning=reasoning, next_max_tokens=mt))
        except Exception as error:
            self.failure = error
            raise


def _reference_sampling_options(inputs):
    """Keep old jobs' sampling signatures unchanged when the option is absent."""
    value = inputs.get('reference_image_size', 'match')
    if value not in ('match', 'max'):
        raise ValueError('参考图精度请选择标准或高清')
    return {'reference_image_size': value} if 'reference_image_size' in inputs else {}


def _generate_once(session, work, written, refs, geometry, index, take, signature):
    sampling_options = _reference_sampling_options(session.inputs)
    raw, receipt = work / 'raw.mp4', work / 'raw_receipt.json'
    if raw.exists():
        old = _read(receipt) if receipt.exists() else {}
        if old.get('input_signature') != signature or old.get('sha256') != _sha(raw):
            raise RuntimeError('原片缺少一致的输入凭据，未覆盖或重拍')
        return raw
    ledger_path = work / 'video_submission.json'
    ledger = _read(ledger_path) if ledger_path.exists() else dict(input_signature=signature, submissions=0)
    if ledger.get('input_signature') != signature:
        raise RuntimeError('视频提交账本输入变化')
    key = 'reference-story:%s:%d:%d:%s' % (session.tid, index, take, signature)
    original_http = h3_client._http

    def limited_http(method, path, *args, **kwargs):
        if method.upper() == 'POST' and path == '/prompt':
            session.guard()
            if ledger['submissions'] >= 1:
                raise RuntimeError('本段已提交一次，仅允许恢复原任务，禁止自动再次生成')
            ledger.update(submissions=1, submitted_at=time.time(), recovery_key=key)
            _save(ledger_path, ledger)
        return original_http(method, path, *args, **kwargs)

    trace = work / 'generation'
    trace.mkdir(parents=True, exist_ok=True)
    h3_client._http = limited_http
    with gpu_manager.GPU_LOCK:
        try:
            session.guard()
            with client._offline():
                gpu_manager.acquire('h3', note='reference story segment %d' % index)
                with client._native_profile(client._frames(session.inputs['segment_seconds']), trace, geometry):
                    result = h3_client.generate('ref2va', written['prompt'], images=[ref['path'] for ref in refs],
                        seconds=session.inputs['segment_seconds'], ratio=geometry['ratio'], steps=10,
                        seed=session.manifest['seeds'][index - 1], recovery_key=key, **sampling_options)
            _save(work / 'generation_result.json', result)
            shutil.copy2(result['output_path'], raw)
            _save(receipt, dict(input_signature=signature, sha256=_sha(raw), recovery_key=key))
            return raw
        finally:
            h3_client._http = original_http
            client.preserve_release('h3')


def _reference_visual_options(inputs):
    # New tasks opt in at validation. Existing tasks keep exact cached requests.
    if inputs.get('reference_visual_policy') != prompts.REFERENCE_VISUAL_POLICY:
        return {}
    return dict(reference_visual_policy=prompts.REFERENCE_VISUAL_POLICY,
                scene_reference_provided=bool(inputs.get('initial_scene')))


def _story(session, cards, material):
    inputs, folder = session.inputs, session.folder
    if inputs.get('story_input', 'story') == 'story':
        source = inputs['story_direction']
    else:
        from . import universal_writer as uw
        prior = {key: os.environ.get(key) for key in ('V41_PROSE_TEMPLATE', 'V41_MODEL_REVIEW')}
        os.environ.update(V41_PROSE_TEMPLATE='B', V41_MODEL_REVIEW='1')
        try:
            author_cards = characters.authoring_characters(cards)
            total_seconds = inputs['segment_count'] * inputs['segment_seconds']
            settings = dict(one_line=inputs['story_direction'],
                prose_words='%d～%d' % (max(120, total_seconds * 6), max(160, total_seconds * 8)),
                story_canon=dict(reference_facts=material, characters=author_cards),
                extra_requirements=characters.shortfilm_addendum(inputs['segment_count'], inputs['segment_seconds'])
                    + '\n作品风格：' + inputs['style'])
            if _reference_visual_options(inputs):
                settings['extra_requirements'] += '\n' + prompts.reference_visual_rule(bool(inputs.get('initial_scene')))
            planned = session.cached('author_plan', settings,
                lambda: uw.plan(inputs['story_direction'], copy.deepcopy(settings), n_eps=1, model=session.writer))
            if planned.get('n') != 1 or not planned.get('design'):
                raise RuntimeError('本地故事规划未形成可用结构，原答保留，未另抽候选')
            if planned.get('problems'):
                _warning(session.manifest, 'author_plan', dict(reason='故事规划意见：' +
                    json.dumps(planned['problems'], ensure_ascii=False)))
            uw.save_design(settings, planned['rows'], planned['design'])
            written = session.cached('author_writing', settings,
                lambda: uw.write(copy.deepcopy(settings), ep=1, previous=[], model=session.writer))
            source = written.get('prose')
            if not isinstance(source, str) or not source.strip():
                raise RuntimeError('本地正文尚不可用，未生成视频')
            session.manifest['author_review'] = written.get('review')
        finally:
            for key, value in prior.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    target = folder / 'story.txt'
    encoded = source.encode('utf-8')
    if target.exists() and target.read_bytes() != encoded:
        raise RuntimeError('固定源正文变化，未覆盖原文')
    if not target.exists():
        target.write_bytes(encoded)
    return source


def run_job(tid):
    task = core.runtime.load(tid)
    if task:
        runtime_reset.bind_thread(task.get('runtime_generation', 'initial'))
        runtime_reset.assert_current()
    if not task or task.get('kind') != core.KIND or core._folder(task) is None:
        raise RuntimeError('参考故事任务不存在或缺少保存目录')
    handle = core.runtime.TaskHandle(tid)
    inputs, folder = task['inputs'], core._folder(task)
    if inputs.get('input_mode') != 'reference':
        raise RuntimeError('参考运行器只接受明确的reference任务')
    signature = _signature(inputs)
    manifest_path = folder / 'manifest.json'
    manifest = _read(manifest_path) if manifest_path.exists() else dict(version=3, reference_version=VERSION,
        task_id=tid, inputs=inputs, inputs_sha256=signature, segments=[], logs=[], actual_model_calls=0,
        local_ai_only=True, created=time.time(), reference_policy=dict(quality_gate=False,
            max_takes_per_segment=1, max_storyboard_revisions=1,
            max_text_vision_calls=12 + 12 * inputs['segment_count']))
    if manifest.get('reference_version') != VERSION or manifest.get('inputs_sha256') != signature:
        raise RuntimeError('参考流程版本或任务输入变化，原片保留，请新建任务')
    session = _Session(tid, handle, folder, inputs, manifest)
    count, seconds = inputs['segment_count'], inputs['segment_seconds']
    try:
        optimized = flow.enabled(inputs)
        if flow.audio_enabled(inputs):
            audio_capability = asr.capability()
            if not audio_capability['available']:
                manifest['asr_capability'] = audio_capability
                _warning(manifest, 'asr_capability', audio_capability, audio_capability.get('reason') or '本地ASR暂不可用，音频识别保持未知')
        cards, scene = _freeze_assets(inputs, folder, manifest)
        if 'geometry' not in manifest:
            from .video_geometry import dimensions
            manifest['geometry'] = dimensions(*map(int, inputs.get('aspect_ratio', '16:9').split(':')))
            manifest['seeds'] = [int(_signature([tid, index])[:15], 16) for index in range(1, count + 1)]
        session.checkpoint()
        with session.models():
            initial_refs = characters.references_for_segment(cards, scene=scene)
            observed = prompts.observe_request('references')
            observed[1].update(image_role_bindings=[dict(image_index=index, role=ref['role'],
                **{key: ref[key] for key in ('character_id', 'subject') if key in ref})
                for index, ref in enumerate(initial_refs, 1)],
                canonical_characters=[dict(id=card['id'], name=card['name']) for card in cards])
            material = session.cached('reference_observation', dict(request=observed,
                images=[dict(path=ref['path'], sha256=_sha(ref['path'])) for ref in initial_refs]),
                lambda: session.review(observed, folder / 'reference_observation' / 'model',
                    [ref['path'] for ref in initial_refs]))
            if material.get('status') == 'unavailable':
                _warning(manifest, 'reference_observation', material)
                material = continuation.unknown_observation(material)
            if optimized:
                material = prompts.sanitize_reference_observation(material, observed[1]['image_role_bindings'])
                _save(folder / 'reference_observation' / 'effective.json', material)
            manifest['opening_observation'] = material
            source = _story(session, cards, material)
            manifest['source_story'] = source
            author_cards = characters.authoring_characters(cards)
            contract = session.cached('source_contract', dict(source=source, characters=author_cards),
                lambda: characters.prepare_source_contract(source, cards, strict=False))
            brief = ('以下是本次唯一完整源故事。忠实分为指定段数与时长，保留全部事件、因果、结尾、'
                '说话人和逐字对白；source_contract.dialogue是实际发声白名单，画内文字、心声和回忆'
                '不得新增为口播，不另编主线。\n\n' + source)
            plan_req = prompts.plan_request(brief, inputs['style'], count, seconds, material,
                **({'flow_optimization': True} if optimized else {}), **_reference_visual_options(inputs))
            plan_req[1].update(source_contract=contract, canonical_characters=author_cards)
            plan_req = (plan_req[0] + '\ncanonical_characters是唯一稳定人名/ID，'
                '逐句发声严格沿用source_contract.dialogue及其顺序。', plan_req[1], plan_req[2])
            plan = session.cached('planning', dict(request=plan_req),
                lambda: _request_plan(session, plan_req, folder / 'planning' / 'model', contract, cards))
            core.validate_schema(plan, _plan_usable_schema(plan_req[2]))
            _validate_usable_reference_plan(plan, plan_req[1], cards)
            source_audit = _plan_source_audit(plan, plan_req[1], contract, cards, folder / 'planning' / 'model')
            _save(folder / 'planning' / 'source_validation.json', source_audit)
            manifest['source_validation'] = source_audit
            if source_audit['status'] == 'warning':
                _warning(manifest, 'source_validation', source_audit)
            manifest.update(plan=plan, source_contract=contract, source_sha256=_sha(folder / 'story.txt'))
            review_req = prompts.review_request(plan, brief, inputs['style'], count, seconds, material,
                blocking_only=True, **_reference_visual_options(inputs))
            review = session.cached('plan_review', dict(request=review_req),
                lambda: session.review(review_req, folder / 'plan_review' / 'model', validator=core.validate_plan_review))
            manifest['plan_review'] = review
            if review.get('status') == 'unavailable' or review.get('passed') is False:
                _warning(manifest, 'plan_review', review)
            session.checkpoint()
            if inputs.get('quality_mode', 'automatic') != 'automatic' and not manifest.get('approved'):
                core._wait_for_user(handle, manifest, folder, 'story')
            if manifest.get('approved') and manifest.get('approved_plan_signature') != core.plan_signature(plan):
                raise RuntimeError('已确认故事版本变化，未继续生成')
            for beat in plan['beats']:
                index = beat['index']
                existing = next((row for row in manifest['segments'] if row['index'] == index), None)
                if existing:
                    for key in ('raw', 'clip', 'anchor'):
                        if _sha(existing[key + '_path']) != existing[key + '_sha256']:
                            raise RuntimeError('已完成参考视频或续接末帧变化，未重拍')
                    continue
                previous = manifest['segments'][-1] if manifest['segments'] else None
                state = previous['observation'] if previous else material
                refs = characters.references_for_segment(cards, scene=scene,
                    previous_frame=previous['anchor_path'] if previous else None)
                take = int(manifest.get('takes', {}).get(str(index), 1))
                work = folder / 'segments' / ('%02d_take_%d' % (index, take))
                write_req = prompts.write_request(plan, beat, state, seconds, mode='ref2va',
                    visual_style=inputs['style'], references=refs,
                    **({'flow_optimization': True} if optimized else {}), **_reference_visual_options(inputs))
                write_req[1].update(user_brief=brief, character_personalities=author_cards)
                write_req = (write_req[0] + '\n姓名性格按character_personalities；delivery只写当句表演，'
                    '不另编音色，固定音色由程序最终绑定。', write_req[1], write_req[2])
                def advisory_compiler(candidate, payload):
                    return prompts.compile_written(candidate, dict(payload, speech_budget_policy='advisory'))

                def validate_director(candidate, payload):
                    # A usable draft must still compile with exact dialogue,
                    # reference/voice binding and real numeric time bounds.
                    characters.bind_delivery(candidate, payload, cards, refs, compiler=advisory_compiler)
                    budget_candidates.append(copy.deepcopy(candidate))
                    prompts.validate_written(candidate, payload)
                    # Binding is part of a usable director response, rather
                    # than a later failure which could discard the first draft.
                    characters.bind_delivery(candidate, payload, cards, refs, compiler=prompts.compile_written)
                draft = written = storyboard = None
                for revision in range(2):
                    session.stage('第%d/%d段：本地%s中文导演分镜' % (index, count, '修订' if revision else '编写'), index - 1)
                    request = write_req if revision == 0 else prompts.revision_request(draft, write_req[1], storyboard)
                    budget_candidates, budget_advisory = [], False
                    try:
                        candidate = (session.ask if not revision else session.review)(request,
                            work / ('writing' if not revision else 'writing_revision_1'), validator=validate_director)
                    except RuntimeError as error:
                        if (not continuation.is_optional_error(error) or session.failure
                                or not budget_candidates):
                            raise
                        session.guard()
                        candidate, budget_advisory = budget_candidates[-1], True
                    if candidate.get('status') == 'unavailable':
                        _save(work / 'writing_revision_warning.json', candidate)
                        _warning(manifest, '第%d段 writing_revision' % index, candidate,
                            '导演修订格式不可用，保留已验证有效的原提示词')
                        break
                    draft = candidate
                    written = characters.bind_delivery(draft, request[1], cards, refs,
                        compiler=advisory_compiler if budget_advisory else prompts.compile_written)
                    budget_notes = [row for row in written.get('automatic_adjustments', [])
                                    if row.get('kind') == 'speech_budget_warning']
                    if budget_notes:
                        report = dict(status='warning', quality_gate=False, issues=budget_notes)
                        _save(work / 'speech_budget_warning.json', report)
                        _warning(manifest, '第%d段 speech_budget' % index, report)
                    story_req = prompts.storyboard_request(brief, inputs['style'], plan, beat, written, state, seconds)
                    session.stage('第%d/%d段：记录本地分镜预审' % (index, count), index - 1)
                    storyboard = session.review(story_req, work / ('storyboard_%d' % revision),
                                                 validator=prompts.validate_storyboard)
                    if storyboard.get('status') == 'unavailable' or storyboard.get('passed') is True or revision:
                        break
                if storyboard.get('status') == 'unavailable' or storyboard.get('passed') is False:
                    _warning(manifest, '第%d段 storyboard' % index, storyboard)
                _save(work / 'director_raw.json', draft)
                _save(work / 'director.json', written)
                _save(work / 'storyboard.json', storyboard)
                binding = dict(source_sha256=manifest['source_sha256'], plan_sha256=core.plan_signature(plan),
                    actual_state_sha256=_signature(state), director_sha256=_signature(written),
                    references=[dict(ref, sha256=_sha(ref['path'])) for ref in refs],
                    geometry=manifest['geometry'], seed=manifest['seeds'][index - 1], steps=10,
                    seconds=seconds, frames=client._frames(seconds))
                binding.update(_reference_sampling_options(inputs))
                if (work / 'inputs.json').exists() and _read(work / 'inputs.json') != binding:
                    raise RuntimeError('参考视频生成输入变化，原片保留')
                _save(work / 'inputs.json', binding)
                session.stage('第%d/%d段：H3本地全能参考生成' % (index, count), index - 1)
                raw = _generate_once(session, work, written, refs, manifest['geometry'], index, take, _signature(binding))
                adopted = media.adopt(raw, work / 'adopted', end_seconds=seconds)
                samples = media.sample_frames(adopted['clip_path'], work / 'samples', seconds=seconds)
                session.stage('第%d/%d段：本地观察实际画面与末帧' % (index, count), index - 1)
                observe_req = prompts.observe_request('segment', state, beat)
                observe_req[1].update(sample_timestamps=[sample['time_seconds'] for sample in samples],
                                      clip_duration_seconds=seconds, retention_policy='full_duration')
                shown = flow.compact_samples(samples, adopted) if optimized else samples
                if optimized:
                    observe_req = flow.compact_request(observe_req, shown)
                observation = session.review(observe_req, work / 'observation', [sample['path'] for sample in shown])
                if observation.get('status') == 'unavailable':
                    _warning(manifest, '第%d段 observation' % index, observation)
                    observation = continuation.unknown_observation(observation, last_known=state)
                if optimized:
                    observation, endpoint = flow.observation_pair(observation)
                    _save(work / 'endpoint' / 'derived.json', endpoint)
                else:
                    end_req = prompts.observe_request('opening', observation, beat)
                    end_req[1]['stage_detail'] = '实际完整采用视频的精确最后一帧，不筛选露脸帧，不冒充已完成剧情'
                    endpoint = session.review(end_req, work / 'endpoint', [adopted['anchor_path']])
                if endpoint.get('status') == 'unavailable':
                    _warning(manifest, '第%d段 endpoint' % index, endpoint)
                    endpoint = continuation.unknown_observation(endpoint, last_known=state)
                if flow.audio_enabled(inputs):
                    session.stage('第%d/%d段：本地ASR记录台词' % (index, count), index - 1)
                    audio = asr.review(adopted['clip_path'], written['dialogue'], work / 'asr')
                    continuation.assert_audio_source_usable(audio)
                    if audio.get('status') != 'passed':
                        _warning(manifest, '第%d段 asr' % index, audio)
                else:
                    audio = flow.disabled_audio()
                    _save(work / 'asr' / 'review.json', audio)
                visual = dict(status='unavailable', reason='没有有效事件契约，未认定通过', quality_gate=False)
                if storyboard.get('status') != 'unavailable' and storyboard.get('event_contract'):
                    records = [dict(sample, id='frame_%06d' % sample['frame_index']) for sample in samples]
                    visual_req = quality.segment_acceptance_request(storyboard['event_contract'], written,
                        records, endpoint, state, seconds, audio_review=audio)
                    visual = session.review(visual_req, work / 'visual_acceptance',
                        [sample['path'] for sample in samples], validator=quality.validate_acceptance)
                _save(work / 'visual_review.json', visual)
                if visual.get('status') == 'unavailable' or visual.get('passed') is False:
                    _warning(manifest, '第%d段 visual' % index, visual)
                endpoint['actions'] = observation['actions']
                uncertain = endpoint.get('status') == 'unavailable' or observation.get('status') == 'unavailable'
                endpoint['completed'] = [] if uncertain else [event['explanation']
                    for event in visual.get('events', []) if event['status'] == 'observed']
                if uncertain or visual.get('status') == 'unavailable':
                    endpoint['beat_status'] = 'unknown'
                else:
                    endpoint['beat_status'] = ('achieved' if visual.get('passed') else
                        'partial' if endpoint['completed'] else 'not_observed')
                endpoint['continuity_risks'] = list(dict.fromkeys(observation['continuity_risks'] + endpoint['continuity_risks']))
                row = dict(adopted, index=index, raw_sha256=_sha(raw), observation=endpoint,
                    sequence_observation=observation, prompt_path=str(work / 'director.json'),
                    model_mode='ref2va', take=take, retention_policy='full_duration',
                    input_signature=_signature(binding), storyboard=storyboard, visual_review=visual,
                    asr=audio, quality_gate=False,
                    quality_status='warning' if any(item['stage'].startswith('第%d段 ' % index)
                        for item in manifest.get('continuation_warnings', [])) else 'recorded')
                manifest['segments'].append(row)
                manifest['segments'].sort(key=lambda item: item['index'])
                manifest['quality_summary'] = _summary(manifest)
                session.checkpoint()
            session.stage('拼接参考故事与完整音画解码检查', count)
            movie = 'movie_revision_%d' % len(manifest['history']) if manifest.get('history') else 'movie'
            output = media.join(manifest['segments'], folder / movie)
            output.update(title=plan['title'], segments=len(manifest['segments']), local_ai_only=True,
                manifest_url=core._url(manifest_path), quality_gate=False,
                audio_review=('本地ASR记录；未人工审听或验证口型' if flow.audio_enabled(inputs)
                              else '未开启语音核对；保留原视频音轨'),
                visual_review='本地抽帧观察与实际末帧；质量意见不触发自动补拍',
                network_audit_url=core._url(folder / 'network_audit.jsonl'))
            manifest.update(output=output, ended=time.time())
            manifest['quality_summary'] = _summary(manifest, complete=True)
            output.update(generation_completed=True, quality_status=manifest['quality_summary']['status'])
            session.checkpoint()
            core._checkpoint(handle)
            return output
    except core.ControlStop:
        session.checkpoint()
        raise
    except Exception as error:
        manifest['last_error'] = str(error)
        _save(folder / 'reference_error.json', dict(type=type(error).__name__, message=str(error),
            details=getattr(error, 'details', None), traceback=traceback.format_exc()))
        session.checkpoint()
        raise
    finally:
        client.preserve_release()
