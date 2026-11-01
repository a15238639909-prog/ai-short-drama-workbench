"""Keep optional quality evidence separate from usable video generation."""
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import urllib.error

from . import story_video_quality as quality


POLICY = 'repair-then-continue-v1'
PARTIAL_POLICY = 'saved-slots-partial-v1'
_EMPTY_OUTPUT = '本地故事模型回复为空或被截断，已保留原始回复'


def enabled(inputs):
    return (inputs.get('director_profile') == 'story-mode-director-zh-v1'
            and inputs.get('quality_mode') == 'automatic'
            and inputs.get('input_mode') in ('image', 'text'))


def source_director_fallback(payload):
    """Transport the saved beat and exact dialogue; infer no new story or actor."""
    from .story_video_core import _plan_action_text

    beat, seconds = payload['beat'], payload['seconds']

    def prose(value):
        return re.sub(r'<\s*(?:Subject|Picture|Audio|Video)\b[^>\n]*(?:>|$)', '',
                      str(value), flags=re.I).strip()

    def source_field(label, value):
        text = prose(value)
        if not text:
            raise ValueError('保存的本段原文缺少可用内容：' + label)
        return text if re.search(r'[\u4e00-\u9fff]', text) else label + '：' + text

    action = source_field('原文动作', _plan_action_text(beat['action']))
    ending, goal = source_field('原文末态', beat['ending']), source_field('原文目标', beat['goal'])
    phases = 3 if seconds >= 12 else 1
    slots = payload.get('dialogue_slots') or []
    width = seconds / max(1, len(slots))
    budget = payload.get('timing_budget') or {}
    rate = budget.get('speech_characters_per_second', 4.5)
    pause = budget.get('speech_pause_seconds_per_line', .5)
    durations = [max(.1, math.ceil(len(re.sub(r'[^\w\u4e00-\u9fff]', '', slot['text']))
                                  / rate * 1000) / 1000 + .001) for slot in slots]
    feasible = sum(durations) + len(slots) * pause <= seconds
    dialogue, cursor = [], pause / 2
    for index, slot in enumerate(slots):
        start = cursor if feasible else width * (index + .05)
        end = start + durations[index] if feasible else width * (index + .95)
        dialogue.append(dict(speaker=slot['speaker'], text=slot['text'],
                             start=start, end=end, delivery='自然说出本句原话。'))
        cursor = end + pause
    return dict(integrated_multimodal_description='[Shot 1] ' + action,
        overall_soundscape='与本段动作相符的现场环境声。', non_diegetic_music='N/A',
        expected_end_state=ending, brief=goal,
        timeline=[dict(start=seconds * index / phases, end=seconds * (index + 1) / phases,
                       action=action, visible_result=ending) for index in range(phases)],
        dialogue=dialogue,
        continuity_notes=['导演格式修复耗尽，沿用已保存的本段动作与固定原话，不采用错误素材编号。'])


def is_optional_error(error):
    """Only exhausted report formatting may become unavailable evidence."""
    if type(error) is not RuntimeError:
        return False
    message = str(error)
    if not (message.startswith('本地模型格式修复仍未通过：') or message == _EMPTY_OUTPUT):
        return False
    current = error
    while current is not None:
        if isinstance(current, OSError) or type(current).__name__ in (
                'LocalASRBlocked', 'Cancelled', 'ControlStop', 'TaskPaused'):
            return False
        details = str(current)
        if any(token in details for token in (
                'PUBG', 'pubg', '签名不一致', '输入已变化', '产物内容改变',
                '缓存或输入发生变化', '任务已被清理', '旧的暂停状态', '显卡正被')):
            return False
        current = current.__cause__ or current.__context__
    return True


def is_optional_evidence_error(error):
    """A failed reviewer is unknown evidence; it is not a failed video.

    Kept separate from the director fallback gate: optional reports may be
    omitted when their service is unavailable, but this must not authorize
    replacing a required authoring/model operation with invented content.
    """
    current, visited = error, set()
    chain = []
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        chain.append(current)
        if type(current).__name__ in ('LocalASRBlocked', 'Cancelled', 'ControlStop',
                                     'TaskPaused', 'AgePolicyError'):
            return False
        details = str(current)
        if any(token in details for token in (
                'PUBG', 'pubg', 'V41_NO_MODEL', '签名不一致', '输入已变化', '产物内容改变',
                '缓存或输入发生变化', '输入变化', '证据变化', '任务已被清理', '任务取消',
                '任务已取消', '旧的暂停状态', '显卡正被', '运行保护', '停止请求')):
            return False
        # Filesystem failures cannot silently weaken reference/cached evidence
        # bindings. Network errors are the only optional OSError category.
        if isinstance(current, OSError) and not isinstance(current,
                (TimeoutError, ConnectionError, urllib.error.URLError)):
            return False
        current = current.__cause__ or current.__context__
    if is_optional_error(error):
        return True
    for item in chain:
        if isinstance(item, (TimeoutError, ConnectionError, urllib.error.URLError)):
            return True
        if type(item) in (ValueError, KeyError, TypeError, IndexError, json.JSONDecodeError):
            return True
        if type(item).__name__ in ('ModelHTTPError', 'ModelContextError', 'ContextCheckError'):
            return True
        if isinstance(item, RuntimeError) and any(token in str(item) for token in (
                '模型服务不可用', '文字模型服务不可用', '秒内未就绪', '模型请求失败',
                '本地文本/视觉调用预算', '本轮模型调用上限', '模型文件缺失')):
            return True
    return False


def optional_request(request, directory, ask, save, images=None, validator=None):
    """Reuse only bound unavailable reports; never invent a successful verdict."""
    image_records = []
    for value in images or []:
        path = Path(value)
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        image_records.append(dict(path=str(path.resolve()), sha256=digest))
    token = quality.signature(dict(request=request, images=image_records))
    marker = Path(directory) / ('unavailable_' + token[:20] + '.json')
    from .story_video_core import _model_cache_current
    if marker.exists() and _model_cache_current(directory, marker):
        retained = json.loads(marker.read_text(encoding='utf-8'))
        if retained.get('request_sha256') != token or retained.get('status') != 'unavailable':
            raise RuntimeError('质量报告不可用标记与请求签名不一致，原记录保留')
        return retained
    try:
        return ask(request, directory, images=images, validator=validator)
    except Exception as error:
        if not is_optional_evidence_error(error):
            raise
        report = dict(status='unavailable', reason=str(error), request_sha256=token,
            error_type=type(error).__name__,
            raw_response_directory=str(Path(directory).resolve()), quality_gate=False)
        save(marker, report)
        return report


def unknown_observation(report, last_known=None):
    """A missing observation is unknown, never an asserted visual fact."""
    reason = str(report.get('reason') or '本段视觉报告不可用')
    result = dict(status='unavailable', actual_visible='未知：本次视觉报告不可用。',
        unknowns=[reason], characters=[], props=[], scene='unknown', water='unknown',
        actions=[], completed=[], missing=[], continuity_risks=[reason],
        beat_status='unknown', keep_end_seconds=None,
        keep_end_reason='保留实际完整视频与真实末帧；观察不可用不等于动作已完成。')
    if last_known is not None:
        result['last_known_state'] = copy.deepcopy(last_known)
    return result


def assert_audio_source_usable(audio):
    """An unusable source file is different from uncertain speech recognition."""
    if not isinstance(audio, dict):
        return
    for issue in audio.get('issues', []):
        if not isinstance(issue, dict):
            continue
        code = str(issue.get('code') or '')
        reason = str(issue.get('reason') or '')
        if (code == 'missing_video' or
                any(text in reason for text in ('音轨探测失败', '音轨提取失败'))):
            raise RuntimeError('视频或音轨文件处理失败，原片保留：' + (reason or code))


def usable_clip_audio_evidence(audio, clip_path):
    """An ASR worker failure does not invalidate a separately adopted clip."""
    try:
        assert_audio_source_usable(audio)
    except RuntimeError as error:
        clip = Path(clip_path)
        if not clip.is_file() or not clip.stat().st_size:
            raise
        audio = copy.deepcopy(audio)
        audio['source_processing_issues'] = copy.deepcopy(audio.get('issues') or [])
        audio.update(status='unavailable', quality_gate=False,
            issues=[dict(code='asr_source_unavailable', reason='已采用视频存在，独立语音处理不可用')],
            source_processing_warning=str(error),
            reason='已采用视频文件存在；ASR独立处理失败，语音结果保持未知，保留原视频')
    return audio


def adoption_decision(visual, audio):
    assert_audio_source_usable(audio)
    disabled = (isinstance(audio, dict) and audio.get('status') == 'disabled'
                and audio.get('enabled') is False)
    decision = (quality.adoption_decision(visual, audio, audio_required=False) if disabled
                else quality.adoption_decision(visual, audio))
    if disabled:
        decision.update(audio_review='disabled', quality_scope='visual_only')
    return dict(decision, adopted=True, adoption_policy=POLICY,
                warnings=copy.deepcopy(decision['reasons']),
                quality_status='passed' if decision['passed'] else 'warning')


def completion_summary(segments, warnings=None):
    notes = list(warnings or [])
    for row in segments:
        evidence = row.get('quality') or {}
        notes.extend(evidence.get('warnings') or evidence.get('reasons') or [])
    notes = list(dict.fromkeys(str(note) for note in notes if str(note).strip()))
    passed = bool(segments) and all((row.get('quality') or {}).get('passed') is True
                                    for row in segments) and not notes
    return dict(passed=passed, status='passed' if passed else 'completed_with_warnings',
        generation_completed=True, accepted_segments=len(segments), reasons=notes,
        warnings=notes, adoption_policy=POLICY)


def segment_state(manifest, index, state):
    """Keep actual last-known facts and explicitly flag ungenerated story slots."""
    saved = {row['index'] for row in manifest.get('segments', [])}
    gaps = [number for number in range(1, index) if number not in saved]
    value = copy.deepcopy(state)
    if gaps:
        if not isinstance(value, dict):
            value = unknown_observation(dict(reason='尚无可用的前段观察'))
        reason = ('第%s段未生成或尚不可用，不能把其计划当作已发生；从最后确认画面继续，'
                  '本段仍依原剧情任务，缺失前提保持未知，不预演缺段结果。' % '、'.join(map(str, gaps)))
        value['missing_segments'] = gaps
        value['continuity_risks'] = list(value.get('continuity_risks') or []) + [reason]
    return value


def freeze_segment_context(folder, manifest, plan, index, previous_frame, state, save):
    """Recover the original slot's bound task, never resubmit under a new frame."""
    path = Path(folder) / 'continuation_input.json'
    frame = str(previous_frame) if previous_frame else None
    def digest(value):
        if not value:
            return None
        with Path(value).open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
    binding = dict(plan_signature=quality.signature(plan), previous_frame=frame,
                   previous_frame_sha256=digest(frame), state=segment_state(manifest, index, state))
    if path.exists():
        saved = json.loads(path.read_text(encoding='utf-8'))
        if (saved.get('plan_signature') != binding['plan_signature'] or
                digest(saved.get('previous_frame')) != saved.get('previous_frame_sha256')):
            raise RuntimeError('分段续接输入已变化，原任务及已有视频保留')
        warning = saved != binding
        return saved.get('previous_frame'), saved.get('state'), warning
    save(path, binding)
    return frame, binding['state'], False


def record_segment_failure(manifest, index, error, stage, save, directory):
    from .runtime_core import item_failure_requires_stop
    if item_failure_requires_stop(error):
        raise error
    if any(token in str(error) for token in ('已完成参考视频或续接末帧变化',
            '视频提交账本输入变化', '参考视频生成输入变化')):
        raise error
    entry = dict(index=index, status='pending', stage=stage, error=str(error),
                 error_type=type(error).__name__, automatic_resubmission=False)
    manifest.setdefault('segment_failures', {})[str(index)] = entry
    save(Path(directory) / 'segment_failure.json', entry)
    return entry


def partial_output(manifest, plan, folder, expected, join, save, url, *, merge_name='movie_partial'):
    """Publish an honest partial assembly and all saved slot URLs."""
    from .runtime_core import item_failure_requires_stop
    rows = sorted(manifest.get('segments') or [], key=lambda row: row['index'])
    present = {row['index'] for row in rows}
    missing = [number for number in range(1, expected + 1) if number not in present]
    output, aggregate_error = {}, ''
    if rows:
        try:
            output = join(rows, Path(folder) / merge_name)
        except Exception as error:
            if item_failure_requires_stop(error):
                raise
            aggregate_error = str(error)
    output.update(title=plan['title'], segments=len(rows), expected_segments=expected,
        missing_segments=missing, generation_completed=False, partial_policy=PARTIAL_POLICY,
        quality_status='partial', local_ai_only=True, aggregate_error=aggregate_error,
        manifest_url=url(Path(folder) / 'manifest.json'),
        detail=('本轮已处理，已保存%d/%d段；缺段：%s；已完成片段保留' %
                (len(rows), expected, '、'.join(map(str, missing)))) if missing else
               '片段均已保存，合并待恢复；未重复生成视频',
        saved_segments=[dict(index=row['index'], path=row.get('clip_path') or row.get('video_path'),
                             url=url(Path(row.get('clip_path') or row.get('video_path'))))
                        for row in rows])
    manifest['output'] = output
    manifest['quality_summary'] = dict(passed=False, status='partial',
        generation_completed=False, accepted_segments=len(rows), missing_segments=missing,
        warnings=[entry['error'] for entry in manifest.get('segment_failures', {}).values()])
    save(Path(folder) / 'manifest.json', manifest)
    return output
