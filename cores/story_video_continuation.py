"""Keep optional quality evidence separate from usable video generation."""
import copy
import hashlib
import json
from pathlib import Path

from . import story_video_quality as quality


POLICY = 'repair-then-continue-v1'
_EMPTY_OUTPUT = '本地故事模型回复为空或被截断，已保留原始回复'


def enabled(inputs):
    return (inputs.get('director_profile') == 'story-mode-director-zh-v1'
            and inputs.get('quality_mode') == 'automatic'
            and inputs.get('input_mode') in ('image', 'text'))


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
    if marker.exists():
        retained = json.loads(marker.read_text(encoding='utf-8'))
        if retained.get('request_sha256') != token or retained.get('status') != 'unavailable':
            raise RuntimeError('质量报告不可用标记与请求签名不一致，原记录保留')
        return retained
    try:
        return ask(request, directory, images=images, validator=validator)
    except RuntimeError as error:
        if not is_optional_error(error):
            raise
        report = dict(status='unavailable', reason=str(error), request_sha256=token,
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
