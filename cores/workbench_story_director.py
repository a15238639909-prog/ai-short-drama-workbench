"""Production transport for the accepted original Chinese story director.

Uses the unchanged original adapter. No experimental visual anchors, progress
ledger, story rewrite, video generation or media inspection runs here.
"""
import copy
import json
from pathlib import Path
import re
import time

from . import director_flow as legacy
from . import workbench_story_director_trial as original
from . import observed_identity_continuity as identity_continuity


PROFILE = 'original-story-zh-v1'


def enabled(settings=None):
    return (settings or {}).get('video_director_profile', PROFILE) == PROFILE


def profile_info(settings=None):
    if not enabled(settings):
        return None
    return dict(id=PROFILE, label='原版中文多镜头导演', status='formal', scope='new-prompts')


def _unknown():
    return dict(actual_visible='unknown', scene='unknown', characters=[], props=[],
                actions=[], completed=[], unknowns=['没有已确认的实际画面观察。'])


def _references(clip, chars, settings, include_scene):
    paths = settings.get('_director_reference_paths') or {}
    cards = {row['name']: row for row in chars if row.get('name')}
    refs = []
    for ref in clip['references']:
        if ref['role'] == 'scene' and not include_scene:
            continue
        card = cards.get(ref['name'], {})
        if ref['role'] == 'character' and card.get('_has_image') is False and ref['name'] not in paths:
            return None
        path = paths.get(ref['name']) or card.get('image_path') or card.get('path')
        if not isinstance(path, str) or not path or not Path(path).is_file():
            if ref['role'] == 'character':
                return None  # Do not invent a reference for an unpictured actor.
            continue
        number = len(refs) + 1
        item = dict(name=ref['name'], path=path, role=ref['role'], picture=number)
        if ref['role'] == 'character':
            item.update(subject=number, character_id=card.get('character_id') or card.get('id')
                        or ref['name'], voice=ref.get('voice') or card.get('voice') or '')
        refs.append(item)
    return refs or None


def _fallback(clip, refs):
    prompt = legacy.assemble(legacy.source_fallback(clip), clip)
    if not any(ref['role'] == 'scene' for ref in refs):
        from . import scene_reference_policy
        picture = next(ref['picture'] for ref in clip['references'] if ref['role'] == 'scene')
        prompt = scene_reference_policy.strip_scene_images(prompt, [picture])
    return prompt


def _model_payload(payload):
    result = copy.deepcopy(payload)
    result.pop('_observation_raw', None)
    plan = result['story_plan']
    outlines = []
    for beat in plan.get('beats') or []:
        outline = {key: beat[key] for key in ('index', 'scene', 'cast', 'ending') if key in beat}
        goal = beat.get('goal')
        if (isinstance(goal, str) and len(goal) <= 120
                and goal not in (beat.get('action'), beat.get('source_text'))):
            outline['goal'] = goal
        outlines.append(outline)
    result['story_plan'] = {key: plan[key] for key in (
        'title', 'logline', 'ending', 'characters', 'props', 'invariants', 'style') if key in plan}
    result['story_plan']['beats'] = outlines
    beat = result['beat']
    if beat.get('source_order'):
        beat['action'] = '按source_order的完整原文顺序落实动作与摄影意图；原话只按dialogue_slots发声。'
        if beat.get('goal') in (payload['beat'].get('action'), payload['beat'].get('source_text')):
            beat['goal'] = '落实本段source_order中的原文行动与对白。'
    beat.pop('source_contract', None)
    beat.pop('dialogue', None)
    trial = result.get('workbench_trial')
    if isinstance(trial, dict):
        trial.pop('references', None)
    return result


def _request(q, request, clip, refs, trace):
    system, payload, schema = request
    system += ('\nstory_plan只提供全故事的剧情方向、人物、场景与结尾概要；'
        '当前段完整原文及动作顺序见beat.source_text和beat.source_order，'
        '固定原对白逐字采用dialogue_slots，不推演或补写其他段原文。')
    system = system.replace('人物的脸、发型、发色、肤色、体型、服装和饰品全部按各自人设图，',
        '人物固定身份、脸、肤色与体型按各自人设图，原文明确的发型、发色与衣着变化按剧情执行，')
    system += '\n' + identity_continuity.RULES
    model_payload = _model_payload(payload)
    current = copy.deepcopy(model_payload)
    for attempt in range(3):
        evidence = dict(stage='original_chinese_director', attempt=attempt + 1,
                        system=system, payload=copy.deepcopy(current), schema=schema)
        if '_observation_raw' in payload:
            evidence['observation_raw'] = copy.deepcopy(payload['_observation_raw'])
        trace.append(evidence)
        try:
            raw = q(system, json.dumps(current, ensure_ascii=False), mt=5000,
                    temperature=.2, _tries=1, _check_context=True,
                    response_format={'type': 'json_object', 'schema': schema})
        except RuntimeError as error:
            if not hasattr(error, 'partial_text'):
                raise
            evidence.update(error=str(error), source_fallback=True)
            break
        evidence['raw'] = raw
        try:
            parsed = legacy._response_object(raw)
            compiled = original.compile_reply(parsed, payload)
            compiled = identity_continuity.apply_compiled_authority(compiled)
            evidence.update(compiled=compiled, status='accepted')
            return compiled['prompt'], compiled, False
        except (ValueError, KeyError, TypeError, IndexError, StopIteration) as error:
            evidence.update(error=str(error), status='format_repair')
            current = dict(model_payload, previous_reply=raw, format_error=str(error),
                correction='只修复当前段JSON、绑定及原对白格式，原动作、说话者、原字和顺序保持。')
    prompt = _fallback(clip, refs)
    trace.append(dict(stage='source_fallback', status='accepted', prompt=prompt,
        warning='当前段导演格式修复未完成，已按原文顺序保底转换并继续。'))
    return prompt, {}, True


def make_prompts(slices, chars, scenes, q, settings=None, start=0, count=None,
                 on_step=None, prev_tail='', direction=None, on_prompt=None,
                 trace_dir=None, on_error=None, actual_state_for=None):
    """Match director_flow.make_prompts; observation callback index is zero based.

    The caller owns frozen prompts/requests and sequential rendering. A written
    tail is a plan, so prev_tail is never promoted to observed actual_state.
    Missing images and subjective camera modes keep the existing compatible
    route. Only malformed director transport receives bounded local repairs.
    """
    settings = settings or {}
    from . import camera_view
    if (not enabled(settings) or not legacy.active(slices)
            or str(settings.get('pov') or '').strip() in camera_view.MODES):
        return legacy.make_prompts(slices, chars, scenes, q, settings=settings,
            start=start, count=count, on_step=on_step, prev_tail=prev_tail,
            direction=direction, on_prompt=on_prompt, trace_dir=trace_dir, on_error=on_error)
    from .age_policy import assert_allowed
    from .character_facts import check_design_scale
    check_design_scale(chars, settings)
    assert_allowed(cards=chars, settings=settings,
                   text='\n'.join(str(row.get('text') or '') for row in slices), media='video_prompt')
    start = max(0, int(start or 0))
    end = len(slices) if count is None else min(len(slices), start + max(0, int(count)))
    working = copy.deepcopy(slices)
    speakers = {}
    for row in working:
        for name, _ in (row.get('director') or {}).get('says') or []:
            legacy._assign_speaker(speakers, name, chars)
    output = []
    for index in range(start, end):
        sl, trace = working[index], []
        d = sl['director']
        d.setdefault('camera', str(direction or '按原文动作选择景别与机位。'))
        d.setdefault('purpose', '落实当前原文行动与对白。')
        prior = '\n'.join(str(row.get('text') or '') for row in working[:index])
        clip = legacy._clip(sl, index + 1, chars, speakers, prior, settings, str(direction or ''))
        include_scene = index == 0 or sl['scene_hint'] != working[index - 1].get('scene_hint')
        refs = _references(clip, chars, settings, include_scene)
        if not refs:
            output.extend(legacy.make_prompts(slices, chars, scenes, q, settings=settings,
                start=index, count=1, on_step=on_step, prev_tail=prev_tail if index == start else '',
                direction=direction, on_prompt=on_prompt, trace_dir=trace_dir, on_error=on_error))
            continue
        if on_step:
            on_step('原版中文导演：第%d/%d段' % (index + 1, len(slices)))
        try:
            state = _unknown()
            if actual_state_for is not None:
                try:
                    observed = actual_state_for(index)
                    if isinstance(observed, dict) and observed.get('status') != 'unavailable':
                        state = copy.deepcopy(observed)
                except (RuntimeError, ValueError, KeyError, TypeError) as error:
                    trace.append(dict(stage='observation_unavailable', warning=str(error)))
            observed_state = copy.deepcopy(state)
            state = identity_continuity.dynamic_state(state, refs)
            plan = original.build_plan(working, chars, title='当前已确认故事分镜',
                                       visual_style=clip['style'])
            beat = plan['beats'][index]
            request = original.write_request(plan, beat, state, refs, seconds=sl['seconds'],
                visual_style=clip['style'], scene_name=sl['scene_hint'],
                user_brief=str(settings.get('_director_user_brief') or ''))
            request[1]['_observation_raw'] = observed_state
            request[1]['appearance_source_evidence'] = identity_continuity.source_appearance_evidence(working[:index], refs, chars)
            # _clip owns stable voice IDs, including off-screen/group identities.
            source_lines = [row for row in clip['source_order'] if row.get('dialogue_id')]
            for slot, source in zip(request[1]['dialogue_slots'], source_lines):
                match = re.search(r'\((S\d+(?:,S\d+)*)\)', source.get('speech_prefix') or '')
                if match:
                    slot['speaker_id'] = match[1]
            prompt, compiled, fallback = _request(q, request, clip, refs, trace)
        finally:
            if trace_dir:
                folder = Path(trace_dir)
                folder.mkdir(parents=True, exist_ok=True)
                (folder / ('clip_%03d_%d.json' % (index + 1, time.time_ns()))).write_text(
                    json.dumps(dict(clip_id=index + 1, director_profile=PROFILE, attempts=trace),
                               ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        warnings = [row['warning'] for row in trace if row.get('warning')]
        if fallback and on_step:
            on_step('第%d段使用原文保底写词，原话与顺序保留，继续生成。' % (index + 1))
        assert_allowed(cards=chars, settings=settings, text=prompt, media='video_prompt')
        director_contract = copy.deepcopy(d)
        director_contract['intended_start_state'] = str(d.get('intended_start_state') or d.get('start_state') or '')
        visible = state.get('actual_visible')
        director_contract['start_state'] = (visible.strip() if isinstance(visible, str)
            and visible.strip().lower() not in ('', 'unknown', '未知') else '')
        director_contract['writer_profile'] = PROFILE
        # Authoring persists these two evidence fields back to the selected
        # frozen slice; its original blocks, D IDs and source stay untouched.
        slices[index]['director'].update(start_state=director_contract['start_state'],
            intended_start_state=director_contract['intended_start_state'])
        row = dict(n=index + 1, prompt=prompt, problems=warnings, critical_problems=[],
            source_text=slices[index]['text'], seconds=sl['seconds'], over=False,
            tail=legacy.contract.ending_text(d), source_contract=copy.deepcopy(d.get('source_contract') or {}),
            source_fallback=fallback, director_contract=director_contract, director_profile=PROFILE,
            scene=sl['scene_hint'], chars=[ref['name'] for ref in refs if ref['role'] == 'character'],
            cut=bool(sl.get('cut')), establish=bool(sl.get('establish')), shot=sl.get('shot', ''),
            direction=str(direction or ''), plan=dict(flow=legacy.VERSION, director_profile=PROFILE,
                camera=clip['camera'], purpose=clip['purpose']), relay={}, done_all=[], wardrobe={},
            beat=sl.get('beat', ''), beat_index=sl.get('beat_index', -1))
        if compiled:
            row['director_compilation'] = dict(dialogue=compiled.get('dialogue') or [],
                voice_binding_audit=compiled.get('voice_binding_audit') or [])
        if on_prompt:
            on_prompt(row)
        output.append(row)
    return output
