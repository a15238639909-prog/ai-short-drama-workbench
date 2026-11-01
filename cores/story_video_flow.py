"""Opt-in flow improvements; no inference, media mutation or guessed observations."""
import copy

from . import wardrobe_continuity as wardrobe

PROFILE = 'story-video-flow-v2'


def enabled(inputs):
    return inputs.get('runtime_optimization') == PROFILE


def audio_enabled(inputs):
    # A missing option on old tasks keeps their original behavior.
    return inputs.get('audio_review_enabled', not enabled(inputs)) is True


def disabled_audio():
    return dict(status='disabled', enabled=False, quality_gate=False,
                reason='未开启语音核对；保留原视频音轨', issues=[])


def continuity_request(request, previous_frame, state):
    if not previous_frame:
        return request
    system, payload, schema = copy.deepcopy(request)
    payload['continuation_contract'] = dict(policy=PROFILE,
        previous_frame_path=str(previous_frame), previous_observation=copy.deepcopy(state))
    system += ('\n本段已有真实首帧。第一镜先承接该帧的机位、人物相对位置、朝向、姿势、持物和'
        '动作进度，再自然推进；不重新安排已完成的走近、迎接、交接或起跑。镜头变化和转场由'
        '当前动作建立可见过程，未知事实先揭示，不把原计划的开场替换实际末态。')
    return system, payload, schema


def compact_samples(samples, adopted):
    """Show the beginning, middle and exact retained final frame in one call."""
    ordered = sorted(samples, key=lambda row: row['time_seconds'])
    if not ordered:
        raise ValueError('实际视频缺少可用抽帧')
    seconds = float(adopted['kept_seconds'])
    middle = min(ordered, key=lambda row: abs(row['time_seconds'] - seconds / 2))
    last_index = adopted.get('frame_index', adopted.get('kept_frames', round(seconds * 24)) - 1)
    endpoint = dict(path=adopted['anchor_path'], frame_index=last_index,
                    time_seconds=adopted.get('frame_time_seconds', last_index / 24))
    return [dict(ordered[0], role='opening'), dict(middle, role='middle'), dict(endpoint, role='endpoint')]


def compact_request(request, samples):
    system, payload, schema = copy.deepcopy(request)
    fields = schema['properties']
    endpoint_fields = {key: copy.deepcopy(fields[key]) for key in (
        'actual_visible', 'characters', 'props', 'scene', 'water', 'unknowns', 'continuity_risks')}
    if 'scene_anchors' in fields:
        endpoint_fields['scene_anchors'] = copy.deepcopy(fields['scene_anchors'])
    fields['endpoint_state'] = dict(type='object', properties=endpoint_fields,
        required=list(endpoint_fields), additionalProperties=False)
    schema['required'].append('endpoint_state')
    payload.update(observation_policy=PROFILE,
        image_role_bindings=[dict(image_index=index, role=sample['role'],
            time_seconds=sample['time_seconds'], frame_index=sample['frame_index'])
            for index, sample in enumerate(samples, 1)],
        sample_timestamps=[sample['time_seconds'] for sample in samples],
        endpoint_image_index=3)
    system += ('\n本次一次完成简短过程观察和精确末帧观察。按image_role_bindings区分开头、中间、'
        '实际最后一帧；顶层actions只列确实可见的动作变化，不把计划目标当事实。endpoint_state只凭'
        '最后一张endpoint图记录末态：人物的相对位置、朝向、姿势、持物和环境；看不清写unknown，'
        '不把中间帧或开头状态复制为末态。人物外貌只记识别所需变化，不重复长篇衣饰。'
        '这三张图不足以证明完整剧情或口型；completed保持证据边界。')
    if wardrobe.enabled(payload):
        system += ('\nwardrobe_events只在顶层记录本次过程图片有明确前后变化证据的穿脱或换装，'
            '不能把单独末帧颜色或外观差异当成穿脱；endpoint_state只记录实际最后一帧，'
            '不在其中臆造before、动作或衣物去向，遮挡保持未知。')
    return system, payload, schema


def observation_pair(report):
    """Missing endpoint evidence stays unknown, never a borrowed earlier pose."""
    sequence = copy.deepcopy(report)
    endpoint = sequence.pop('endpoint_state', None)
    if report.get('status') == 'unavailable' or not isinstance(endpoint, dict):
        from .story_video_continuation import unknown_observation
        endpoint = unknown_observation(dict(reason='精确末帧观察缺失或不可用，当前姿势与持物未知'))
    else:
        endpoint = copy.deepcopy(endpoint)
        endpoint.update(actions=[], completed=[], missing=[], beat_status='unknown',
                        keep_end_seconds=None, keep_end_reason='沿用真实完整视频最后一帧')
    return sequence, endpoint
