"""Main-workbench original Chinese director scheduling and optional frame evidence.

No models run on import. Existing words, hand edits, videos and pending request
snapshots are kept; only a missing current segment is written before rendering.
"""
import copy
import hashlib
import json
from pathlib import Path
import re


def _segment(sid, ep, number):
    from . import saga_core
    timeline = saga_core.ep_timeline(sid, ep) or {}
    for scene in timeline.get('scenes') or []:
        for row in scene.get('segments') or []:
            if int(row.get('no') or 0) == int(number):
                return timeline, int(scene.get('no') or 1), row
    return timeline, 1, None


def enabled(sid, ep=1):
    from . import authoring, story_core, director_flow, camera_view, reference_ready
    from . import workbench_story_director as writer
    settings = (story_core.get_story(sid) or {}).get('settings') or {}
    if (not writer.enabled(settings) or not authoring.use_new_pipeline()
            or authoring._pov_spec(settings)[0] in camera_view.MODES):
        return False
    # Do not change the no-image route or legacy frozen slice formats.
    if not reference_ready.adopted_paths(sid):
        return False
    return director_flow.active(authoring.episode_slices(sid, ep))


def unknown(reason='尚无上一段实际末帧证据。'):
    return dict(actual_visible='unknown', scene='unknown', characters=[], props=[],
                actions=[], completed=[], unknowns=[str(reason)])


def _warning(log_fn, reason):
    if log_fn:
        log_fn('实际末帧观察仅作辅助，记录未知并继续：' + str(reason)[:180])


def after_render(sid, ep, segment_no, log_fn=None):
    """Observe one literal last frame; optional report failures never block film."""
    from . import store, video_continuity as vc, story_video_media as media
    from . import story_video_reference_prompts as prompts, story_video_core as core
    timeline, _, segment = _segment(sid, ep, segment_no)
    stamp = vc.media_stamp((segment or {}).get('video'))
    if not stamp:
        return unknown('上一段尚无已保存的视频。')
    prior = (segment or {}).get('director_actual_state') or {}
    if prior.get('video_stamp') == stamp:
        return copy.deepcopy(prior['observation'])
    identity = hashlib.sha256(json.dumps(stamp, sort_keys=True).encode()).hexdigest()[:16]
    safe_sid = re.sub(r'[^\w.-]', '_', str(sid))
    folder = Path(store.DATA) / 'director_actual_frames' / ('%s_e%d' % (safe_sid, ep)) / ('segment_%03d_%s' % (segment_no, identity))
    record = folder / 'result.json'
    try:
        if record.exists():
            saved = json.loads(record.read_text(encoding='utf-8'))
            observation = saved['observation'] if saved.get('video_stamp') == stamp else unknown('末帧缓存与视频不符。')
        else:
            folder.mkdir(parents=True, exist_ok=True)
            _, _, times = media._timeline(stamp['path'])
            anchor = folder / 'actual_last_frame.png'
            media._extract(stamp['path'], len(times) - 1, anchor)
            system, payload, schema = prompts.observe_request('opening')
            payload.update(observation_role='segment_end', evidence_scope='single actual final frame only',
                image_role_bindings=[dict(image_index=1, role='actual_final_frame',
                    frame_index=len(times) - 1, time_seconds=times[-1])])
            system += ('\n附图仅一张刚生成视频的精确最后一帧。只写这一帧当前可见事实；'
                '未知保持unknown，空场如实写未见人物。actions和completed填空数组，keep_end_seconds填null。'
                '不能从剧情或此前镜头补出身份、胜负或已完成事件，不要求补拍和裁切。')
            store.save_json(folder / 'request.json', dict(system=system, payload=payload, schema=schema,
                images=[str(anchor)], video_stamp=stamp))
            observation = core.request_json((system, payload, schema), folder / 'model', images=[str(anchor)])
            observation['actions'], observation['completed'] = [], []
            visible = observation.get('actual_visible')
            if (not isinstance(visible, str) or not visible.strip()
                    or re.search(r'图\s*[2-9]|图\s*1\s*[-～~、与和]\s*\d', visible)):
                observation = unknown('单帧观察不完整或混入多帧摘要。')
            store.save_json(record, dict(video_stamp=stamp, observation=observation))
    except Exception as error:
        # Cancellation and GPU/game permissions still apply to all local AI.
        if any(word in str(error) for word in ('已取消', 'PUBG', '游戏模式')):
            raise
        observation = unknown(str(error))
        _warning(log_fn, error)
        try:
            store.save_json(record, dict(video_stamp=stamp, observation=observation,
                status='unavailable', quality_gate=False, error=str(error)))
        except (OSError, ValueError):
            pass
    try:
        from . import saga_core
        fresh, _, row = _segment(sid, ep, segment_no)
        if row is not None and vc.media_stamp(row.get('video')) == stamp:
            row['director_actual_state'] = dict(video_stamp=stamp, observation=observation,
                evidence_directory=str(folder), policy='single-true-final-frame-v1', quality_gate=False)
            saga_core.save_ep_timeline(sid, ep, fresh)
    except (OSError, ValueError, KeyError) as error:
        _warning(log_fn, error)
    return copy.deepcopy(observation)


def actual_state_for(sid, ep, index):
    """index is the absolute zero-based slice index, like make_prompts.start."""
    return unknown() if int(index) <= 0 else after_render(sid, ep, int(index))


def explicit_opening(sid, ep, *, size_tier=None, log_fn=None):
    """Keep the existing opt-in opening feature, including its media reuse."""
    from . import story_core, opening
    if not opening.enabled((story_core.get_story(sid) or {}).get('settings') or {}):
        return
    from api import segment_api
    try:
        _, error = segment_api.render_opening_all(sid, ep, size_tier=size_tier,
            log_fn=log_fn, chk=log_fn)
        if error:
            _warning(log_fn, error)
    except Exception as error:
        if any(word in str(error) for word in ('已取消', 'PUBG', '游戏模式')):
            raise
        _warning(log_fn, error)


def run_sequence(sid, ep, targets, *, render, prepare=None, on_step=None, on_complete=None, size_tier=None):
    """Write one missing prompt, render/recover it, observe, then write the next."""
    from . import authoring, saga_core
    from api import segment_api
    outputs = []
    opening_done = False
    for number in targets:
        number = int(number)
        timeline, scene_no, row = _segment(sid, ep, number)
        if row and row.get('video') and segment_api.is_segment_current(sid, ep, scene_no, number, size_tier=size_tier):
            outputs.append(row['video'])
            # Old complete clips need no model work. A following new writer
            # obtains its preceding endpoint through actual_state_for on demand.
            if on_complete:
                on_complete(number, True)
            continue
        request = (row or {}).get('render_request') or {}
        if not str((row or {}).get('prompt') or '').strip():
            if request.get('key') and str(request.get('final_prompt') or '').strip():
                row['prompt'] = request['final_prompt']
                saga_core.save_ep_timeline(sid, ep, timeline)
            else:
                if on_step:
                    on_step('原版中文导演：只补第%d段提示词' % number)
                saved_prompts = set()
                def save_prompt(prompt_row):
                    authoring.build_timeline(sid, ep, [prompt_row])
                    saved_prompts.add(prompt_row['n'])
                created = authoring.make_h3_prompts(sid, ep, start=number - 1, count=1,
                    on_step=on_step, on_prompt=save_prompt) or []
                for prompt_row in created:
                    if prompt_row['n'] not in saved_prompts:
                        save_prompt(prompt_row)
        timeline, scene_no, row = _segment(sid, ep, number)
        if not row or not str(row.get('prompt') or '').strip():
            raise RuntimeError('第%d段尚无可执行提示词，已生成内容保留。' % number)
        if prepare:
            prepare(number)
        if not opening_done:
            explicit_opening(sid, ep, size_tier=size_tier, log_fn=on_step)
            opening_done = True
        if on_step:
            on_step('生成第%d段视频；完成后观察真实末帧再续写' % number)
        result = render(scene_no, number)
        outputs.append(result.get('video') or row.get('video'))
        fresh, _, rendered = _segment(sid, ep, number)
        if rendered is not None and result.get('video'):
            rendered.pop('stale_video', None)
            saga_core.save_ep_timeline(sid, ep, fresh)
        after_render(sid, ep, number, on_step)
        if on_complete:
            on_complete(number, False)
    return outputs
