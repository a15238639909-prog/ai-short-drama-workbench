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
from . import wardrobe_continuity as wardrobe
from . import source_shot_compiler as source_shots


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
    # An empty establishing continuation has no identity picture of its own.
    # Its actual previous frame is supplied later by the renderer, not invented
    # here or replaced with the scene master again.
    return refs


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
    if request[1].get('source_shot_policy') == source_shots.VERSION:
        return _source_request(q, request[1], clip, refs, trace)
    system, payload, schema = request
    system += ('\nstory_plan只提供全故事的剧情方向、人物、场景与结尾概要；'
        '当前段完整原文及动作顺序见beat.source_text和beat.source_order，'
        '固定原对白逐字采用dialogue_slots，不推演或补写其他段原文。')
    if wardrobe.enabled(payload):
        system = wardrobe.rewrite_rules(system) + '\n' + wardrobe.authority_rules()
    else:
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
        except (RuntimeError, OSError) as error:
            from .runtime_core import item_failure_requires_stop
            from .workbench_director_runtime import control_error
            if control_error(error) or item_failure_requires_stop(error):
                raise
            evidence.update(error=str(error), source_fallback=True)
            break
        evidence['raw'] = raw
        try:
            parsed = legacy._response_object(raw)
            compiled = original.compile_reply(parsed, payload)
            if wardrobe.enabled(payload):
                compiled = wardrobe.apply_compiled(compiled, payload.get('wardrobe_state'), refs)
            else:
                compiled = identity_continuity.apply_compiled_authority(compiled)
            # Use the same final text check as the submitter while repairs are
            # still available. A structurally valid JSON response can still
            # stage a later action or assign a line to the wrong Subject.
            problems = legacy.prompt_problems(compiled['prompt'],
                payload['beat'].get('source_text') or clip.get('source_text') or '',
                payload['seconds'], clip.get('scene') or (payload.get('workbench_trial') or {}).get('scene'))
            if problems:
                raise ValueError('；'.join(problems))
            evidence.update(compiled=compiled, status='accepted')
            return compiled['prompt'], compiled, False
        except (ValueError, KeyError, TypeError, IndexError, StopIteration) as error:
            evidence.update(error=str(error), status='format_repair')
            current = dict(model_payload, previous_reply=raw, format_error=str(error),
                correction='只修复当前段JSON、绑定及原对白格式，原动作、说话者、原字和顺序保持。')
    prompt = _fallback(clip, refs)
    if wardrobe.enabled(payload):
        prompt = wardrobe.apply_prompt(prompt, payload.get('wardrobe_state'), refs)
    trace.append(dict(stage='source_fallback', status='accepted', prompt=prompt,
        warning='当前段导演格式修复未完成，已按原文顺序保底转换并继续。'))
    return prompt, {}, True


def _repair_source_visual_fields(previous, proposed, paths):
    """A photography repair cannot silently rewrite accepted camera/source choices."""
    result = copy.deepcopy(previous)
    for path in paths:
        if not isinstance(path, (list, tuple)) or not path:
            raise ValueError('摄影修补字段路径无效')
        top_level = len(path) == 1 and path[0] in ('style_description', 'scene_description', 'soundscape')
        shot_field = (len(path) == 4 and path[0] == 'shots' and
                      isinstance(path[1], int) and not isinstance(path[1], bool) and path[1] >= 0 and
                      path[2] == 'visual' and path[3] in source_shots.VISUAL_FIELDS)
        acting_field = (len(path) == 3 and path[0] == 'shots' and
                        isinstance(path[1], int) and not isinstance(path[1], bool) and path[1] >= 0
                        and path[2] == 'acting')
        if not (top_level or shot_field or acting_field):
            raise ValueError('摄影修补范围超出已指出的画面字段')
        old, new = result, proposed
        for key in path[:-1]:
            old, new = old[key], new[key]
        old[path[-1]] = copy.deepcopy(new[path[-1]])
    return result


_SOURCE_VISUAL_REPAIR_SYSTEM = '''你只改写影视摄影补充中被指出的字段，不重写整份镜头计划。
只返回给定键组成的JSON对象，各值保持字段规定的类型：摄影字段是文字，acting是表演条目数组。摄影字段只保留确定可见的静态空间、光源、色彩、材质、焦点和允许的表情；人物动作与台词由程序另外写入。
按field_errors删除对应字段里的问题内容，保留有用摄影细节。例如“金属门紧闭”可改为“金属门与灰色墙面”；“她抬手握住把手”可改为“金属把手位于画面中央，手部轮廓在前景”。门窗开合状态只由原文时间轴表达。
摄影字段没有可靠细节时可填空串；scene_description可只填具体场景名称。soundscape只写当前环境声，以及source_units中对应物件实际动作的声音。不要把画外声音变成画内人物行动。
acting是按unit_id和actor绑定的表演补充数组，只修给出的条目；preparation、execution、reaction写原事件内部的注意力、发力节奏、呼吸与眉眼口角变化。剧情主干与对白由程序各写一次。衣装、持物、灯光状态继续由现有事实决定。缺少可靠表演依据时填空数组，保留其他摄影细节。
camera_detail以镜头或机位为主语，写起始构图、运动方向和结束构图；可以跟随当前原文中同一演员的起身、俯身或转身。
不要返回镜头编号、分组、台词、时长或其他未请求字段。最终字段只写正面可见或可听内容。'''


def _source_visual_repair_request(payload, previous, paths, error):
    fields, properties = {}, {}
    for path in paths:
        key = '.'.join(map(str, path))
        node = previous
        for part in path:
            node = node[part]
        fields[key] = node
        if len(path) == 1:
            properties[key] = copy.deepcopy(source_shots.SCHEMA['properties'][path[0]])
        elif len(path) == 3 and path[2] == 'acting':
            properties[key] = copy.deepcopy(source_shots.SCHEMA['properties']['shots']['items']['properties']['acting'])
        else:
            properties[key] = copy.deepcopy(source_shots.SCHEMA['properties']['shots']['items']['properties']['visual']['properties'][path[-1]])
    schema = dict(type='object', additionalProperties=False, required=list(fields), properties=properties)
    request = dict(payload, field_values=fields, field_errors=str(error),
                   repair_fields=[list(path) for path in paths])
    return request, schema


def _source_visual_repair_reply(previous, proposed, paths):
    keys = {'.'.join(map(str, path)) for path in paths}
    if isinstance(proposed, dict) and set(proposed) == keys:
        nested = copy.deepcopy(previous)
        for path in paths:
            node = nested
            for part in path[:-1]:
                node = node[part]
            node[path[-1]] = proposed['.'.join(map(str, path))]
        return _repair_source_visual_fields(previous, nested, paths)
    # Older structured adapters can return a full plan. Only the explicitly
    # requested fields are accepted from it; the rest remains frozen.
    if isinstance(proposed, dict) and set(proposed) == set(source_shots.SCHEMA['required']):
        return _repair_source_visual_fields(previous, proposed, paths)
    raise ValueError('摄影修补回复须只包括已指出的字段及其新文字')


def _omit_unreliable_source_visual(plan, paths, payload):
    """Keep accepted photography when one bounded rewrite remains unreliable."""
    proposed = copy.deepcopy(plan)
    for path in paths:
        if len(path) == 4 and path[0] == 'shots' and path[2] == 'visual':
            proposed['shots'][path[1]]['visual'][path[3]] = ''
        elif len(path) == 3 and path[0] == 'shots' and path[2] == 'acting':
            shot = proposed['shots'][path[1]]
            lookup = {unit['id']: unit for unit in source_shots.units(payload)}
            local = [lookup[unit] for unit in shot['unit_ids']]
            names = [person['name'] for person in payload['story_plan'].get('characters') or []]
            accepted, used = [], set()
            entries = shot.get('acting') or []
            for entry in entries if isinstance(entries, list) else []:
                try:
                    item = source_shots.staging.validate_staging([entry], local, names,
                        event_patterns=source_shots._EVENTS)[0]
                except (ValueError, KeyError, TypeError, IndexError):
                    if not isinstance(entry, dict):
                        continue
                    kept = dict(entry)
                    for phase in source_shots.staging.PHASES:
                        trial = dict(entry, **dict.fromkeys(source_shots.staging.PHASES, ''))
                        trial[phase] = entry.get(phase, '')
                        try:
                            phase_item = source_shots.staging.validate_staging([trial], local, names,
                                event_patterns=source_shots._EVENTS)[0]
                            kept[phase] = phase_item[phase]
                        except (ValueError, KeyError, TypeError, IndexError):
                            kept[phase] = ''
                    try:
                        item = source_shots.staging.validate_staging([kept], local, names,
                            event_patterns=source_shots._EVENTS)[0]
                    except (ValueError, KeyError, TypeError, IndexError):
                        continue
                identity = (item['unit_id'], item['actor'])
                if identity not in used:
                    accepted.append(item)
                    used.add(identity)
            shot['acting'] = accepted[:3]
        elif tuple(path) == ('scene_description',):
            facts = payload.get('scene_facts') or {}
            condition = facts.get('scene_condition')
            if condition:
                from .scene_condition import positive_condition
                proposed['scene_description'] = str(payload['beat']['scene']) + '，' + positive_condition(condition)
            else:
                proposed['scene_description'] = str(payload['beat']['scene'])
        elif tuple(path) == ('soundscape',):
            proposed['soundscape'] = '轻微的现场环境底噪。'
        elif tuple(path) == ('style_description',) and payload.get('declared_lighting_states', {}).get('states'):
            from .source_lighting_state import safe_style_description
            style = str(payload.get('visual_style') or '')
            safe_style = safe_style_description(style,
                source_context=payload['declared_lighting_states'],
                scene_facts=payload.get('scene_facts') or {}, current_scene=payload['beat']['scene'])
            if not safe_style or safe_style == style:
                raise ValueError('风格字段仍需可靠修补')
            proposed['style_description'] = safe_style
        else:
            raise ValueError('当前摄影字段无法可靠省略，保留原文基础稿待复核')
    return _repair_source_visual_fields(plan, proposed, paths)


def _source_request(q, payload, clip, refs, trace):
    """Camera choices refer to current source IDs; program owns all action text."""
    model_payload = source_shots.model_payload(payload)
    current = copy.deepcopy(model_payload)
    repair_base, repair_paths = None, []

    def compile_plan(plan):
        raw = source_shots.compile_actions(plan, payload)
        compiled = original.compile_reply(raw, payload)
        compiled['source_shot_audit'] = raw['source_shot_audit']
        if wardrobe.enabled(payload):
            compiled = wardrobe.apply_compiled(compiled, payload.get('wardrobe_state'), refs)
        else:
            compiled = identity_continuity.apply_compiled_authority(compiled)
        if payload.get('source_wardrobe_plan'):
            from . import source_wardrobe_plan
            compiled = source_wardrobe_plan.apply_compiled(compiled, payload['source_wardrobe_plan'], refs)
        problems = legacy.prompt_problems(compiled['prompt'], payload['beat']['source_text'],
            payload['seconds'], clip.get('scene') or payload['beat']['scene'])
        if problems:
            raise ValueError('；'.join(problems))
        return compiled

    # One original response and one bounded repair. Detailed text is never
    # declared accepted merely because its JSON and original IDs are valid.
    for attempt in range(2):
        request_system, request_schema = source_shots.SYSTEM, source_shots.SCHEMA
        if repair_base is not None and repair_paths:
            request_system = _SOURCE_VISUAL_REPAIR_SYSTEM
            if any(len(path) == 3 and path[2] == 'acting' for path in repair_paths):
                request_system += '\n' + source_shots.staging.ACTING_INSTRUCTION
            current, request_schema = _source_visual_repair_request(model_payload, repair_base, repair_paths, current['format_error'])
        evidence = dict(stage=source_shots.VERSION, attempt=attempt + 1,
            system=request_system, payload=copy.deepcopy(current), schema=request_schema)
        trace.append(evidence)
        try:
            raw = q(request_system, json.dumps(current, ensure_ascii=False), mt=5000,
                temperature=.2, _tries=1, _check_context=True,
                response_format={'type': 'json_object', 'schema': request_schema})
        except (RuntimeError, OSError) as error:
            from .runtime_core import item_failure_requires_stop
            from .workbench_director_runtime import control_error
            if control_error(error) or item_failure_requires_stop(error):
                raise
            evidence.update(error=str(error), source_fallback=True)
            break
        evidence['raw'] = raw
        plan = None
        try:
            plan = legacy._response_object(raw)
            if repair_base is not None and repair_paths:
                plan = _source_visual_repair_reply(repair_base, plan, repair_paths)
                evidence.update(repair_fields=copy.deepcopy(repair_paths), effective_plan=copy.deepcopy(plan))
            compiled = compile_plan(plan)
            evidence.update(status='accepted', compiled=compiled)
            return compiled['prompt'], compiled, False
        except (ValueError, KeyError, TypeError, IndexError) as error:
            evidence.update(status='format_repair', error=str(error))
            paths = getattr(error, 'field_paths', None)
            if attempt == 1 and paths and isinstance(plan, dict):
                try:
                    safe_plan = _omit_unreliable_source_visual(plan, paths, payload)
                    compiled = compile_plan(safe_plan)
                    compiled['source_shot_audit']['omitted_visual_fields'] = [list(path) for path in paths]
                    compiled['source_shot_audit']['photography_review_required'] = True
                except (ValueError, KeyError, TypeError, IndexError):
                    pass
                else:
                    evidence.update(status='accepted_with_omissions', effective_plan=safe_plan,
                        compiled=compiled, warning='已省略%d项未通过的摄影补充；原文动作、台词、机位及其他有效细节保留，需复核文字。' % len(paths))
                    return compiled['prompt'], compiled, False
            if paths and isinstance(plan, dict):
                repair_base, repair_paths = copy.deepcopy(plan), list(paths)
            else:
                repair_base, repair_paths = None, []
            current = dict(model_payload, previous_reply=raw, format_error=str(error),
                repair_fields=copy.deepcopy(repair_paths),
                correction='只修复错误指出的摄影或acting字段，保留其余已通过内容。剧情动作由程序按原文写入一次；摄影补充写当前阶段的光色、材质、构图和焦点，acting按明确演员和原文单元补充事件内部的表演，不新增剧情事件。镜头分组本身有误时才调整分组。')
    # A malformed camera choice has no reason to restore unconstrained plot prose.
    compiled = compile_plan(source_shots.default_plan(payload))
    trace.append(dict(stage='source_shot_fallback', status='accepted', compiled=compiled,
        warning='镜头编排未完成，已按当前原文顺序使用基础机位；原动作、台词及完整时长保留。'))
    return compiled['prompt'], compiled, True


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
    source_repairs = {}
    for index in range(start, end):
        repaired = []
        # Upgrade the explicitly versioned legacy transport. Unversioned or
        # manually authored blocks retain their IDs and user timing contract.
        if (working[index].get('director') or {}).get('source_contract_version') == 'episode-source-v1':
            legacy.repair_slice_source(working[index], chars, q, on_step, repaired)
        source_repairs[index] = repaired
    from . import scene_reference_policy
    # authoring canonical_scene_slices resolves shooting-position aliases before
    # this writer. Include all earlier slices even for a start/count rewrite.
    scene_keys = [('name', str(row.get('scene_hint') or '').strip())
                  if str(row.get('scene_hint') or '').strip() else None for row in working]
    speakers = {}
    for row in working:
        for name, _ in (row.get('director') or {}).get('says') or []:
            legacy._assign_speaker(speakers, name, chars)
    output = []
    for index in range(start, end):
        sl, trace = working[index], list(source_repairs.get(index) or [])
        boundary_audit = []
        source_owned = settings.get('video_action_compiler', source_shots.VERSION) == source_shots.VERSION
        if source_owned:
            from . import source_action_continuation
            untrimmed_sl = copy.deepcopy(sl)
            sl, boundary_audit = source_action_continuation.trim_slice(
                sl, working[index - 1] if index else None, chars)
            working[index] = sl
        d = sl['director']
        d.setdefault('camera', str(direction or '按原文动作选择景别与机位。'))
        d.setdefault('purpose', '落实当前原文行动与对白。')
        prior = '\n'.join(str(row.get('text') or '') for row in working[:index])
        clip = legacy._clip(sl, index + 1, chars, speakers, prior, settings, str(direction or ''))
        include_scene = scene_reference_policy.first_use_in_plan(scene_keys[index], scene_keys[:index])
        refs = _references(clip, chars, settings, include_scene)
        if refs is None:
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
                except (RuntimeError, ValueError, KeyError, TypeError, OSError) as error:
                    from .workbench_director_runtime import control_error
                    if control_error(error):
                        raise
                    trace.append(dict(stage='observation_unavailable', warning=str(error)))
            observed_state = copy.deepcopy(state)
            wardrobe_state = state.pop('wardrobe_state', None) if wardrobe.enabled(settings) else None
            if wardrobe.enabled(settings) and wardrobe_state is None:
                wardrobe_state = wardrobe.initial_state(chars)
            relay_state = observed_state
            time_jump = re.match(r'^\s*(?:次日|翌日|第二天|数日后|几天后|多年后|'
                r'[一二三四五六七八九十百两零\d]+(?:年|个月|月|天|日|小时|分钟)(?:之)?后)', sl.get('text') or '')
            if time_jump or (index > 0 and sl['scene_hint'] != working[index - 1].get('scene_hint')):
                relay_state = _unknown()
                trace.append(dict(stage='scene_transition',
                    warning='本段换景或明确跳时，上一时空的位置、姿势与持物不作为强起镜状态；已确认衣着独立延续。'))
            if source_owned:
                from .workbench_director_runtime import endpoint_start_state
                verified_start = endpoint_start_state(relay_state, refs)
                if verified_start and not time_jump:
                    # Re-evaluate the original working source: a verified
                    # seated pose can invalidate a planned standing state.
                    sl, boundary_audit = source_action_continuation.trim_slice(
                        untrimmed_sl, working[index - 1] if index else None, chars,
                        verified_start_text=verified_start)
                    working[index], d = sl, sl['director']
                    d.setdefault('camera', str(direction or '按原文动作选择景别与机位。'))
                    d.setdefault('purpose', '落实当前原文行动与对白。')
                    clip = legacy._clip(sl, index + 1, chars, speakers, prior, settings, str(direction or ''))
                if boundary_audit:
                    trace.append(dict(stage='source_action_continuation',
                        policy='boundary-forward-only-v1', omissions=copy.deepcopy(boundary_audit)))
            state = identity_continuity.dynamic_state(relay_state, refs)
            plan = original.build_plan(working, chars, title='当前已确认故事分镜',
                                       visual_style=clip['style'])
            beat = plan['beats'][index]
            request = original.write_request(plan, beat, state, refs, seconds=sl['seconds'],
                visual_style=clip['style'], scene_name=sl['scene_hint'],
                user_brief=str(settings.get('_director_user_brief') or ''))
            request[1]['_observation_raw'] = observed_state
            if settings.get('video_action_compiler', source_shots.VERSION) == source_shots.VERSION:
                from .workbench_director_runtime import endpoint_start_state
                from .source_lighting_state import declared_lighting_states
                request[1]['endpoint_start_state'] = endpoint_start_state(relay_state, refs)
                request[1]['declared_lighting_states'] = declared_lighting_states(working, index)
                request[1]['camera_intent'] = clip['camera']
                request[1]['scene_facts'] = copy.deepcopy((settings.get('_director_scene_facts') or {}).get(sl['scene_hint']) or {})
                bound_ids = {ref['character_id']: ref for ref in refs if ref['role'] == 'character'}
                request[1]['appearance_facts'] = [dict(name=card.get('name'),
                    subject=bound_ids[str(card.get('character_id') or card.get('id') or card.get('name'))]['subject'],
                    appearance=str(card.get('appearance') or ''))
                    for card in chars if str(card.get('character_id') or card.get('id') or card.get('name')) in bound_ids]
                request[1]['source_shot_policy'] = source_shots.VERSION
            request[1]['appearance_source_evidence'] = identity_continuity.source_appearance_evidence(working[:index], refs, chars)
            if wardrobe.enabled(settings):
                request[1]['wardrobe_policy'] = wardrobe.POLICY
                request[1]['wardrobe_state'] = wardrobe.model_state(wardrobe_state, refs)
            if source_owned:
                from . import source_wardrobe_plan
                request[1]['source_wardrobe_plan'] = source_wardrobe_plan.build_start_plan(
                    working, index, chars, observed_state=wardrobe_state)
            # _clip owns stable voice IDs, including off-screen/group identities.
            source_lines = [row for row in clip['source_order'] if row.get('dialogue_id')]
            for slot, source in zip(request[1]['dialogue_slots'], source_lines):
                match = re.search(r'\((S\d+(?:,S\d+)*)\)', source.get('speech_prefix') or '')
                if match:
                    slot['speaker_id'] = match[1]
            prompt, compiled, fallback = _request(q, request, clip, refs, trace)
            if boundary_audit:
                compiled.setdefault('source_shot_audit', {})['boundary_action_omissions'] = copy.deepcopy(boundary_audit)
        finally:
            if trace_dir:
                try:
                    folder = Path(trace_dir)
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / ('clip_%03d_%d.json' % (index + 1, time.time_ns()))).write_text(
                        json.dumps(dict(clip_id=index + 1, director_profile=PROFILE, attempts=trace),
                                   ensure_ascii=False, indent=2, default=str), encoding='utf-8')
                except OSError as error:
                    trace.append(dict(stage='trace_unavailable',
                        warning='附加导演日志未保存，已保留可用提示词：' + str(error)))
        warnings = [row['warning'] for row in trace if row.get('warning')]
        if fallback and on_step and source_owned:
            on_step('第%d段摄影扩写未通过，已生成原文基础稿，需复核。' % (index + 1))
        elif fallback and on_step:
            on_step('第%d段使用原文保底写词，原话与顺序保留，继续生成。' % (index + 1))
        assert_allowed(cards=chars, settings=settings, text=prompt, media='video_prompt')
        director_contract = copy.deepcopy(d)
        # Effective boundary wording belongs to this prompt, not the saved
        # user's source blocks or dialogue. Its omissions have their own audit.
        if boundary_audit:
            for key in ('blocks', 'says', 'source_contract', 'source_dialogue'):
                if key in slices[index]['director']:
                    director_contract[key] = copy.deepcopy(slices[index]['director'][key])
            director_contract['boundary_action_omissions'] = copy.deepcopy(boundary_audit)
        director_contract['intended_start_state'] = str(d.get('intended_start_state') or d.get('start_state') or '')
        from .workbench_director_runtime import endpoint_start_state
        director_contract['start_state'] = endpoint_start_state(relay_state, refs)
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
        if wardrobe.enabled(settings):
            row['plan']['wardrobe_policy'] = wardrobe.POLICY
        if compiled:
            row['director_compilation'] = dict(dialogue=compiled.get('dialogue') or [],
                voice_binding_audit=compiled.get('voice_binding_audit') or [])
            if compiled.get('source_shot_audit'):
                row['director_compilation']['source_shot_audit'] = compiled['source_shot_audit']
                row['plan']['source_shot_policy'] = source_shots.VERSION
                row['plan']['prompt_content_policy'] = 'positive-visual-v1'
                row['plan']['scene_reference_scope'] = scene_reference_policy.SCOPE
        if on_prompt:
            on_prompt(row)
        output.append(row)
    return output
