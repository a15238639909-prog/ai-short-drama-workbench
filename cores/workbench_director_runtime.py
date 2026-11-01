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


def endpoint_start_state(state, bindings=()):
    from . import endpoint_evidence
    return endpoint_evidence.start_state(state, bindings)


def _warning(log_fn, reason):
    if log_fn:
        log_fn('实际末帧观察仅作辅助，记录未知并继续：' + str(reason)[:180])


def after_render(sid, ep, segment_no, log_fn=None):
    """Observe one literal last frame; optional report failures never block film."""
    from . import story_core, wardrobe_continuity as wardrobe
    if wardrobe.enabled(story_core.get_story(sid)):
        return _after_render_wardrobe(sid, ep, segment_no, log_fn)
    from . import store, video_continuity as vc, story_video_media as media, asset_core, endpoint_evidence
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
            characters = asset_core.list_assets(sid, 'characters') or []
            bindings = [dict(image_index=1, role='actual_final_frame',
                frame_index=len(times) - 1, time_seconds=times[-1])]
            system, payload, schema = endpoint_evidence.request_contract((system, payload, schema), characters, bindings)
            payload.update(observation_role='segment_end', evidence_scope='single actual final frame only',
                image_role_bindings=bindings)
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
            observation = endpoint_evidence.normalize(observation, characters, bindings, single_frame=True)
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


def wardrobe_state_for(sid, ep, index):
    from . import story_core, asset_core, video_continuity as vc, wardrobe_continuity as wardrobe
    story = story_core.get_story(sid) or {}
    if not wardrobe.enabled(story):
        return None
    characters = asset_core.list_assets(sid, 'characters') or []
    state = wardrobe.initial_state(characters)
    episodes = {int(row['no']): row for row in (story.get('saga') or {}).get('episodes') or []
                if isinstance(row, dict) and str(row.get('no') or '').isdigit()}
    if 1 not in episodes and story.get('timeline'):
        episodes[1] = dict(timeline=story['timeline'])
    for number, episode in sorted(episodes.items()):
        if number > int(ep):
            continue
        rows = sorted([row for scene in (episode.get('timeline') or {}).get('scenes') or []
                       for row in scene.get('segments') or []], key=lambda row: int(row.get('no') or 0))
        for row in rows:
            if number == int(ep) and int(row.get('no') or 0) > int(index):
                continue
            evidence = row.get('director_actual_state') or {}
            stamp = vc.media_stamp(row.get('video'))
            if (not stamp or row.get('stale_video') or evidence.get('policy') != wardrobe.POLICY
                    or evidence.get('video_stamp') != stamp):
                continue
            state = wardrobe.advance(state, characters=characters,
                sequence=evidence.get('observation'), segment=int(row.get('no') or 0),
                evidence_frames=evidence.get('evidence_frames') or [],
                source_events=str(row.get('source_text') or ''))
    return state


def _after_render_wardrobe(sid, ep, segment_no, log_fn=None):
    from . import store, saga_core, asset_core, wardrobe_continuity as wardrobe, endpoint_evidence
    from . import video_continuity as vc, story_video_media as media
    from . import story_video_reference_prompts as prompts, story_video_core as core
    _, _, segment = _segment(sid, ep, segment_no)
    stamp = vc.media_stamp((segment or {}).get('video'))
    prior_state = wardrobe_state_for(sid, ep, int(segment_no) - 1)
    if not stamp or (segment or {}).get('stale_video'):
        return dict(unknown('上一段尚无当前有效视频。'), wardrobe_state=prior_state)
    characters = asset_core.list_assets(sid, 'characters') or []
    identity = hashlib.sha256(json.dumps(stamp, sort_keys=True).encode()).hexdigest()[:16]
    safe_sid = re.sub(r'[^\w.-]', '_', str(sid))
    folder = (Path(store.DATA) / 'director_actual_frames' / ('%s_e%d' % (safe_sid, ep))
              / ('segment_%03d_%s_wardrobe_v1' % (segment_no, identity)))
    record = folder / 'result.json'
    evidence_frames = []
    try:
        prior = (segment or {}).get('director_actual_state') or {}
        if prior.get('video_stamp') == stamp and prior.get('policy') == wardrobe.POLICY:
            observation = copy.deepcopy(prior['observation'])
            evidence_frames = prior.get('evidence_frames') or []
        elif record.exists():
            saved = json.loads(record.read_text(encoding='utf-8'))
            if saved.get('video_stamp') != stamp or saved.get('policy') != wardrobe.POLICY:
                raise ValueError('衣着观察缓存与当前视频不符')
            observation = saved['observation']
            evidence_frames = saved.get('evidence_frames') or []
        else:
            folder.mkdir(parents=True, exist_ok=True)
            _, _, times = media._timeline(stamp['path'])
            if not times:
                raise ValueError('视频没有可观察的实际帧')
            evidence_frames = sorted({0, len(times) - 1})
            images, bindings = [], []
            for index in evidence_frames:
                target = folder / ('actual_frame_%06d.png' % index)
                media._extract(stamp['path'], index, target)
                images.append(str(target))
                bindings.append(dict(image_index=len(images), frame_index=index, time_seconds=times[index],
                    role='actual_final_frame' if index == len(times) - 1 else 'wardrobe_before_frame'))
            system, payload, schema = wardrobe.observation_request(prompts.observe_request('opening'),
                characters, transition_evidence=True, frame_bindings=bindings,
                source_text=str((segment or {}).get('source_text') or ''))
            system, payload, schema = endpoint_evidence.request_contract((system, payload, schema), characters, bindings)
            payload.update(observation_role='segment_end', evidence_scope='actual first and final frames',
                           image_role_bindings=bindings)
            system += ('\n非衣着状态只描述最后一张实际末帧，actual_visible不能混写早先画面或图片编号。'
                'actions与completed填空数组，keep_end_seconds填null。首帧只供核实衣着变化，未知保持unknown。')
            store.save_json(folder / 'request.json', dict(system=system, payload=payload, schema=schema,
                images=images, video_stamp=stamp, policy=wardrobe.POLICY))
            observation = core.request_json((system, payload, schema), folder / 'model', images=images)
            observation = endpoint_evidence.normalize(observation, characters, evidence_frames)
            store.save_json(record, dict(video_stamp=stamp, observation=observation,
                evidence_frames=evidence_frames, policy=wardrobe.POLICY))
    except Exception as error:
        if any(word in str(error) for word in ('已取消', 'PUBG', '游戏模式')):
            raise
        observation = dict(unknown(str(error)), status='unavailable')
        evidence_frames = []
        _warning(log_fn, error)
    # Cached mixed first/last reports are not retroactively accepted as precise
    # endpoint evidence. Keep them available for inspection, but do not re-call
    # a model merely to fill missing or conflicting fields.
    observation = endpoint_evidence.normalize(observation, characters, evidence_frames)
    state = wardrobe.advance(prior_state, characters=characters, sequence=observation,
        segment=segment_no, evidence_frames=evidence_frames,
        source_events=str((segment or {}).get('source_text') or ''))
    fresh, _, row = _segment(sid, ep, segment_no)
    if row is not None and not row.get('stale_video') and vc.media_stamp(row.get('video')) == stamp:
        row['director_actual_state'] = dict(video_stamp=stamp, observation=copy.deepcopy(observation),
            evidence_frames=evidence_frames, evidence_directory=str(folder),
            policy=wardrobe.POLICY, quality_gate=False)
        row['wardrobe_state'] = copy.deepcopy(state)
        row['wardrobe_video_stamp'] = stamp
        saga_core.save_ep_timeline(sid, ep, fresh)
    else:
        return dict(unknown('观察期间视频已更换或失效。'), wardrobe_state=prior_state)
    return dict(copy.deepcopy(observation), wardrobe_state=state)


def actual_state_for(sid, ep, index):
    """index is the absolute zero-based slice index, like make_prompts.start."""
    if int(index) > 0:
        return after_render(sid, ep, int(index))
    state = wardrobe_state_for(sid, ep, 0)
    return dict(unknown(), wardrobe_state=state) if state is not None else unknown()


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


def control_error(error):
    """A user stop/reset or the game guard must propagate through batch recovery."""
    return (type(error).__name__ in ('Cancelled', 'JobCancelled', '_Cancelled')
            or any(word in str(error) for word in ('已取消', 'PUBG', '游戏模式')))


def run_sequence(sid, ep, targets, *, render, prepare=None, on_step=None, on_complete=None,
                 on_error=None, size_tier=None):
    """Write one missing prompt, render/recover it, observe, then write the next."""
    from . import authoring, saga_core
    from api import segment_api
    outputs = []
    opening_done = False
    def run_one(number):
        nonlocal opening_done
        number = int(number)
        timeline, scene_no, row = _segment(sid, ep, number)
        if row and row.get('video') and segment_api.is_segment_current(sid, ep, scene_no, number, size_tier=size_tier):
            outputs.append(row['video'])
            # Old complete clips need no model work. A following new writer
            # obtains its preceding endpoint through actual_state_for on demand.
            if on_complete:
                on_complete(number, True)
            return
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
        # A stale row may still carry an older clip. Only a confirmed renderer
        # result counts as this attempt's saved video.
        video = result.get('video')
        if not video:
            raise RuntimeError('第%d段未返回已保存视频，保留该段待重试。' % number)
        outputs.append(video)
        try:
            fresh, _, rendered = _segment(sid, ep, number)
            if rendered is not None:
                rendered.pop('stale_video', None)
                saga_core.save_ep_timeline(sid, ep, fresh)
        except Exception as error:
            if control_error(error):
                raise
            _warning(on_step, '已生成视频，附加状态保存失败：' + str(error))
        try:
            after_render(sid, ep, number, on_step)
        except Exception as error:
            if control_error(error):
                raise
            _warning(on_step, error)
        if on_complete:
            on_complete(number, False)
    for number in targets:
        try:
            run_one(number)
        except Exception as error:
            if control_error(error):
                raise
            # A missing prompt/reference or an individual render failure cannot
            # revoke later independent work. No blind resubmission is made here;
            # the renderer retains its pending request and recovery checkpoint.
            if on_error:
                on_error(int(number), str(error))
            if on_step:
                on_step('第%d段保留待重试，继续后续段：%s' % (int(number), str(error)[:160]))
    return outputs
