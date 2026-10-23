"""Pure continuity evidence for the retained local-director pilot.

Film allocation, sampled process evidence and a literal final frame are separate
records. Importing this module never invokes a model or changes production data.
"""
import copy
import math
import re


_PHASE = re.compile(r'【\s*(\d+(?:\.\d+)?)\s*[～~—-]\s*(\d+(?:\.\d+)?)\s*秒\s*】')
_SHOT = re.compile(r'\[Shot\s+(\d+)\]', re.I)
_CUT = re.compile(r'\s*At\s+(\d{1,2}):(\d{1,2}(?:\.\d{1,3})?)\s*,\s*', re.I)


def normalize_director_transport(raw, payload):
    """Use explicit phase ranges and existing cuts, without editing shot prose.

    Missing intra-phase cuts are evenly allocated only between explicit known
    anchors. They are audited as transport allocation, not authored timings.
    Conflicting existing timings are never overwritten.
    """
    value, audit = copy.deepcopy(raw), []
    description = value.get('integrated_multimodal_description')
    if not isinstance(description, str):
        return value, audit
    phases = list(_PHASE.finditer(description))
    if not phases:
        return value, audit
    seconds = float(payload['seconds'])
    if (float(phases[0][1]) != 0 or float(phases[-1][2]) != seconds
            or any(float(phase[1]) >= float(phase[2]) for phase in phases)
            or any(float(left[2]) != float(right[1]) for left, right in zip(phases, phases[1:]))):
        return value, audit
    parsed = []
    for index, phase in enumerate(phases):
        stop = phases[index + 1].start() if index + 1 < len(phases) else len(description)
        body = description[phase.end():stop].strip()
        labels = list(_SHOT.finditer(body))
        if not labels or body[:labels[0].start()].strip():
            return value, audit
        shots = []
        for number, label in enumerate(labels):
            end = labels[number + 1].start() if number + 1 < len(labels) else len(body)
            text = body[label.end():end].strip()
            cut = _CUT.match(text)
            existing_start = int(cut[1]) * 60 + float(cut[2]) if cut else None
            if cut:
                text = text[cut.end():].strip()
            if not text:
                return value, audit
            shots.append(dict(original_label=int(label[1]), text=text, at=existing_start))
        parsed.append(dict(start=float(phase[1]), end=float(phase[2]), shots=shots))
    prefix = description[:phases[0].start()].strip()
    if prefix:
        labels = list(_SHOT.finditer(prefix))
        if (len(labels) != 1 or prefix[:labels[0].start()].strip()
                or int(labels[0][1]) != parsed[0]['shots'][0]['original_label']
                or prefix[labels[0].end():].strip() != parsed[0]['shots'][0]['text']):
            return value, audit
        audit.append(dict(kind='exact_duplicate_preface_shot_removed', original=prefix,
                          reason='同一首镜已逐字出现在第一个明确时段内，只去掉冗余副本。'))
    old_timeline = value.get('timeline') or []
    all_shots, new_timeline = [], []
    for phase in parsed:
        shots = phase['shots']
        anchors = {0: phase['start'], len(shots): phase['end']}
        matches = {}
        for position, shot in enumerate(shots):
            matching = [row for row in old_timeline if isinstance(row, dict)
                        and str(row.get('action') or '').strip() == shot['text']]
            row = matching[0] if len(matching) == 1 else None
            existing_start = shot['at'] if shot['at'] is not None else row.get('start') if row else None
            if existing_start is not None:
                if (type(existing_start) not in (int, float)
                        or not phase['start'] <= existing_start < phase['end']
                        or position in anchors and abs(anchors[position] - existing_start) > .000001):
                    raise ValueError('已有镜头切点与明确时段冲突，未覆盖原时间')
                anchors[position] = float(existing_start)
            if row:
                matches[position] = row
        positions = sorted(anchors)
        starts = dict(anchors)
        for left, right in zip(positions, positions[1:]):
            if anchors[right] <= anchors[left]:
                raise ValueError('已有切点不是递增时间，未覆盖原时间')
            for position in range(left + 1, right):
                starts[position] = round(anchors[left] + (anchors[right] - anchors[left])
                                         * (position - left) / (right - left), 6)
        for position, shot in enumerate(shots):
            start, end = starts[position], starts[position + 1]
            row = matches.get(position)
            if row and (abs(float(row['start']) - start) > .000001
                        or abs(float(row['end']) - end) > .000001):
                raise ValueError('已有时间轴与镜头/明确时段冲突，未覆盖原时间')
            if row:
                new_timeline.append(copy.deepcopy(row))
            else:
                new_timeline.append(dict(start=start, end=end, action=shot['text'],
                    visible_result=value['expected_end_state'] if abs(end - seconds) < .000001 else shot['text']))
                audit.append(dict(kind='missing_timeline_from_explicit_phase', start=start, end=end,
                    action=shot['text'], timing_basis='已有时段与相邻切点之间按镜头顺序均分；不修改动作或对白。'))
            all_shots.append(dict(text=shot['text'], start=start))
    paragraphs = []
    for index, shot in enumerate(all_shots, 1):
        label = '[Shot %d]' % index
        if index > 1:
            label += ' At %02d:%06.3f,' % (int(shot['start'] // 60), shot['start'] % 60)
        paragraphs.append(label + ' ' + shot['text'])
    value['integrated_multimodal_description'] = '\n\n'.join(paragraphs)
    value['timeline'] = new_timeline
    audit.append(dict(kind='explicit_phase_shot_transport', phase_count=len(parsed), shot_count=len(all_shots),
        original_description=description, normalized_description=value['integrated_multimodal_description'],
        creative_changes=False, policy='保留全部唯一镜头正文，已有切点优先，只归一阶段与镜头传输标记。'))
    return value, audit


def compile_director_reply(raw, payload):
    from . import workbench_story_director_trial as adapter
    normalized, audit = normalize_director_transport(raw, payload)
    normalized, timing_payload, timing_audit = normalize_director_speech_timing(normalized, payload)
    compiled = adapter.compile_reply(normalized, timing_payload)
    audit.extend(timing_audit)
    if audit:
        compiled.setdefault('automatic_adjustments', []).extend(audit)
    return finalize_prompt_anchors(compiled, payload)


def normalize_director_speech_timing(raw, payload):
    """Extend short speech and shift only the affected following time slots.

    Source words, identities, order, delivery and shot prose are immutable.
    An unavailable natural-speech budget is advisory; unsafe timestamps remain
    errors in the existing compiler, rather than being coerced or truncated.
    """
    from . import story_video_prompts as base
    value, timing_payload, audit = copy.deepcopy(raw), copy.deepcopy(payload), []
    lines = value.get('dialogue')
    if not isinstance(lines, list) or not lines:
        return value, timing_payload, audit
    seconds = base._seconds(payload.get('seconds'))
    budget = payload.get('timing_budget') or base._timing_budget(seconds)
    rate, pause = budget['speech_characters_per_second'], budget['speech_pause_seconds_per_line']
    previous_end = 0.0
    for line in lines:
        if not isinstance(line, dict) or not isinstance(line.get('text'), str):
            raise ValueError('dialogue须包含逐字原话')
        start, end = line.get('start'), line.get('end')
        if any(type(time) not in (int, float) or not math.isfinite(time) for time in (start, end)):
            raise ValueError('对白起止时间须为有限数字')
        if not previous_end <= start < end <= seconds:
            raise ValueError('对白须按时间顺序且不重叠、不超出本段')
        previous_end = end
    counted = [len(re.sub(r'[^\w\u4e00-\u9fff]', '', line['text'])) for line in lines]
    proposed = copy.deepcopy(lines)
    previous_changed = False
    for index, (original, line, count) in enumerate(zip(lines, proposed, counted)):
        minimum = count / rate
        if index and previous_changed:
            gap = max(pause, original['start'] - lines[index - 1]['end'])
            line['start'] = max(original['start'], proposed[index - 1]['end'] + gap)
        # Keep an already adequate duration, including after a necessary shift.
        duration = max(original['end'] - original['start'], minimum)
        if line['start'] != original['start'] or duration > original['end'] - original['start']:
            line['start'] = math.ceil((line['start'] - 1e-10) * 1000) / 1000
            line['end'] = math.ceil((line['start'] + duration - 1e-10) * 1000) / 1000
        previous_changed = line['start'] != original['start'] or line['end'] != original['end']
    fits = proposed[-1]['end'] <= seconds
    if fits:
        for index, (old, new) in enumerate(zip(lines, proposed)):
            if (old['start'], old['end']) != (new['start'], new['end']):
                audit.append(dict(kind='dialogue_timing_transport', line_index=index,
                    from_start=old['start'], from_end=old['end'],
                    to_start=new['start'], to_end=new['end'],
                    reason='按自然发声预算扩展不足句，只顺延受影响的后句并保留句间停顿。',
                    creative_changes=False))
        value['dialogue'] = proposed
    actual = sum(line['end'] - line['start'] for line in value['dialogue'])
    insufficient = (not fits or sum(counted) > budget['max_dialogue_characters_total']
        or sum(counted) / rate + len(lines) * pause > budget['max_dialogue_seconds'] + .01
        or actual + len(lines) * pause > min(seconds, budget['max_dialogue_seconds']) + .01)
    if insufficient:
        timing_payload['speech_budget_policy'] = 'advisory'
        audit.append(dict(kind='speech_budget_advisory_transport',
            reason='保持原首句时点后自然发声预算不足；保留原字、有限且段内不重叠时间，只提示预算风险。',
            estimated_last_end=proposed[-1]['end'], seconds=seconds,
            creative_changes=False))
    return value, timing_payload, audit


_FINAL_ANCHOR_POLICY = 'render-anchors-v1'
_ANCHOR_SECTIONS = (('subject_definitions', '固定地点外观'),
                    ('retention_analysis', '本段实体外观'))


def _anchor_text(value):
    """Reference facts are plain text, never an additional media binding."""
    text = ' '.join(str(value or '').split())
    text = re.sub(r'<Subject\s+\d+>', '该已绑定主体', text, flags=re.I)
    return re.sub(r'<Picture\s+\d+>', '该已有参考', text, flags=re.I)


def _active_visual_props(compiled, payload):
    """Select current-beat facts; an episode inventory is not the visible cast."""
    beat = payload.get('beat') or {}
    cast = set(beat.get('cast') or [])
    # Spoken mentions alone do not introduce an object into the picture.
    action = '\n'.join(str(beat.get(key) or '') for key in ('goal', 'action', 'turn', 'ending'))
    action += '\n' + str((compiled.get('fields') or {}).get('detailed_description') or '')
    props = (payload.get('stable_visual_contract') or {}).get('props') or []
    return [row for row in props if isinstance(row, dict) and row.get('name')
            and row.get('appearance') and (row.get('id') in cast or row['name'] in action)]


def finalize_prompt_anchors(compiled, payload):
    """Carry opt-in fixed visual facts into the actual six-field H3 request.

    Only descriptive retention fields change. The local author's shot prose,
    dialogue, performance, timing and Subject/Picture bindings stay untouched.
    Global prop counts are deliberately absent: they may span several scenes.
    """
    result = copy.deepcopy(compiled)
    if payload.get('final_anchor_policy') != _FINAL_ANCHOR_POLICY:
        return result
    from . import story_video_reference_prompts as director
    fields = result['fields']
    # Replace our own blocks on re-finalization rather than accumulating them.
    for field, title in _ANCHOR_SECTIONS:
        fields[field] = re.sub(r'\n?【' + title + r'】.*?【' + title + r'结束】',
                              '', fields[field], flags=re.S).rstrip('\n')
    stable = payload.get('stable_visual_contract') or {}
    scene = stable.get('scene') or payload.get('current_scene_contract') or {}
    if scene.get('name') and scene.get('description'):
        scene_text = ('地点为%s时，固定外观沿用：%s '
            '这些事实约束该地点的建筑、材质、地标、时段与光色；景别和机位随本段动作变化。'
            '转场前后各保留对应地点，实际损坏及可移动物件服从本段动作和已观测状态。') % (
                _anchor_text(scene['name']), _anchor_text(scene['description']))
        fields['subject_definitions'] += '\n【固定地点外观】' + scene_text + '【固定地点外观结束】'
    props = _active_visual_props(result, payload)
    if props:
        prop_text = '\n'.join('%s：%s' % (_anchor_text(row['name']), _anchor_text(row['appearance']))
                              for row in props)
        prop_text += ('\n以上仅约束本段动作实际呈现该物件时的外形，不要求它一直出镜或提前出现。'
            '同一可携实体在取下、移动或交接后延续为原物，原位置不再出现它的副本；'
            '原有剑鞘、挂环等容器与被取出的物件分别延续。'
            '能量附着、光晕和轨迹是原物产生的光效，不另生一件同形实体。'
            '群体当段数量及后续增减依本段原稿与已观测状态，跨场景总量不展开为当前出镜数量；'
            '破碎、消失、收起及持有人变化按动作结果延续。')
        fields['retention_analysis'] += '\n【本段实体外观】' + prop_text + '【本段实体外观结束】'
    result['prompt'] = '\n\n'.join(field + ':\n' + fields[field] for field in director.FIELDS)
    result['final_anchor_audit'] = dict(policy=_FINAL_ANCHOR_POLICY,
        scene_id=scene.get('id'), prop_ids=[row.get('id') for row in props],
        count_policy='current_beat_and_observed_state_only', creative_changes=False)
    return result


def single_episode_author_plan(planned, expected_scenes):
    """Repack a short film's mislabeled video scenes into one authoring episode.

    Only the explicitly requested scene count is eligible. All original rows,
    draft and synopsis survive; this changes grouping, never story sentences.
    """
    if planned.get('n') == 1:
        return copy.deepcopy(planned)
    rows, design = planned.get('rows'), planned.get('design')
    if (not isinstance(rows, list) or len(rows) != expected_scenes or planned.get('n') != len(rows)
            or expected_scenes <= 1 or not isinstance(design, dict)
            or not isinstance(design.get('synopsis'), str) or not design['synopsis'].strip()
            or planned.get('hard') or planned.get('problems')
            or any(not isinstance(row, dict) or row.get('no') != index
                   or not isinstance(row.get('one_line'), str) or not row['one_line'].strip()
                   or not isinstance(row.get('events'), list) or not row['events']
                   for index, row in enumerate(rows, 1))):
        # Unknown structures remain visible to the existing usable-plan check.
        return copy.deepcopy(planned)
    from . import universal_writer as uw
    merged = dict(no=1)
    for key in uw.ROW_TEXT:
        if key in ('start', 'landing', 'pace', 'cast'):
            continue
        merged[key] = '\n\n'.join(str(row.get(key) or '') for row in rows if row.get(key))
    merged.update(start=rows[0].get('start', ''), landing=rows[-1].get('landing', ''),
        pace=rows[0].get('pace', '适中'),
        cast='、'.join(dict.fromkeys(name.strip() for row in rows
            for name in str(row.get('cast') or '').split('、') if name.strip())),
        events=[copy.deepcopy(event) for row in rows for event in row['events']],
        link_in=rows[0].get('link_in', ''), link_out=rows[-1].get('link_out', ''))
    for key in ('title', 'purpose', 'pace_why'):
        merged[key] = '\n'.join(str(row[key]) for row in rows if row.get(key))
    for key in ('scenes', 'focus', 'beats'):
        merged[key] = [copy.deepcopy(value) for row in rows for value in (row.get(key) or [])]
    normalized_rows = uw.normalize_rows([merged])
    result = copy.deepcopy(planned)
    result.update(rows=normalized_rows, n=1, author_scene_rows=copy.deepcopy(rows),
        author_transport=dict(policy='single-episode-scene-grouping-v1', original_n=planned['n'],
            source_rows_sha256=uw.signature(rows), creative_changes=False,
            reason='用户要求一部短片；本地作者把视频段误标为多话，只统一场次归属，原稿全部保留。'))
    result['design']['rows_sig'] = uw.signature(normalized_rows)
    return result


def stable_visual_contract(config, plan, scene):
    """Reuse reference facts and the local author's own prop definitions verbatim."""
    return dict(scene={key: scene[key] for key in ('id', 'name', 'description')},
        props=[{key: copy.deepcopy(row[key]) for key in ('id', 'name', 'appearance', 'count')
                if key in row} for row in plan.get('props', []) if isinstance(row, dict)],
        invariants=copy.deepcopy(plan.get('invariants') or []),
        authority='人物外形以人设图为准；这里仅复用固定场景和道具定义，不固定当前机位、持有人或位置。')


def progress_ledger(plan, segments):
    """A filmed beat is consumed allocation, never proof its intended ending happened."""
    beats = {row['index']: row for row in plan.get('beats', [])}
    consumed, completed, unknown = [], [], []
    for segment in sorted(segments, key=lambda row: row['index']):
        index = segment['index']
        beat = beats.get(index, {})
        consumed.append(dict(index=index, goal=beat.get('goal', ''),
            planned_ending=beat.get('ending', ''),
            status='filmed_allocation_not_observed_success',
            policy='此段剧本已拍摄，不重演其开头；目标与ending不因拍摄而成为事实。'))
        observation = segment.get('progress_observation') or {}
        if observation.get('status') == 'unavailable' or not observation:
            unknown.append(dict(index=index, reason='没有独立过程事件证据；不从计划补出胜负或完成状态。'))
            continue
        for event in observation.get('completed_events', []):
            completed.append(dict(copy.deepcopy(event), segment_index=index,
                certainty='observed_in_sampled_process_frames',
                source='segments/%02d/progress_observation' % index))
        unknown.extend(dict(index=index, reason=text) for text in observation.get('unknowns', []))
    return dict(consumed_beats=consumed, observed_completed_events=completed, unknowns=unknown,
        evidence_boundary='已拍摄段落不等于计划已实现；过程样片证明的事件不等于当前末帧仍可见。')


def progress_observation_request(config, plan, samples):
    """Ask local AI about chronological process samples, without planned actions."""
    identities = [dict(id=row['id'], name=row['name']) for row in config['cards']]
    identities += [dict(id=row['id'], name=row['name']) for row in plan.get('props', [])]
    sample_bindings = [dict(image_index=index,
        time_seconds=row.get('time_seconds', row.get('seconds', row.get('time', 'unknown'))),
        role='chronological_process_sample') for index, row in enumerate(samples, 1)]
    event_schema = dict(type='object', properties=dict(entity_id=dict(type='string'),
        event=dict(type='string'), evidence_image=dict(type='integer', minimum=1, maximum=len(samples)),
        evidence_description=dict(type='string')),
        required=['entity_id', 'event', 'evidence_image', 'evidence_description'], additionalProperties=False)
    schema = dict(type='object', properties=dict(completed_events=dict(type='array', items=event_schema),
        unknowns=dict(type='array', items=dict(type='string'))),
        required=['completed_events', 'unknowns'], additionalProperties=False)
    system = ('你只记录已生成视频过程样片中有直接证据的已结束事件，输出指定JSON。'
        '附图依时间先后排列；它们是过程证据，不是下一段开场。没有提供剧本目标，不猜补。'
        '只有清楚可见的破碎残骸、已经完成的交接、已经打开的门等才记completed_events。'
        '举剑、站立、发光或敌人暂时出画不能证明击败、施法完成或死亡。'
        '只填canonical_entities中的明确身份；认不清写unknowns，不新增身份。'
        'event简短写已经结束的事件及结果；evidence_image填直接证明该结果的附图序号，'
        'evidence_description只描述那张图可见的依据。没有充分证据就输出空completed_events数组。'
        '记录不是审稿，不评判作品质量，不要求重拍。')
    return (system, dict(canonical_entities=identities, image_role_bindings=sample_bindings,
                        evidence_scope='sampled process images only; no planned beat or ending supplied'), schema)


def validate_progress_observation(value, payload):
    allowed = {row['id'] for row in payload['canonical_entities']}
    count = len(payload['image_role_bindings'])
    for row in value.get('completed_events', []):
        if (row.get('entity_id') not in allowed or type(row.get('evidence_image')) is not int
                or not 1 <= row['evidence_image'] <= count
                or not all(isinstance(row.get(key), str) and row[key].strip()
                           for key in ('event', 'evidence_description'))):
            raise ValueError('过程记录只接受已绑定实体及实际样片序号，不影响已有视频')


def apply_director_context(request, config, plan, scene, segments):
    system, payload, schema = request
    payload.update(stable_visual_contract=stable_visual_contract(config, plan, scene),
                   story_progress=progress_ledger(plan, segments))
    if config.get('final_anchor_policy') == _FINAL_ANCHOR_POLICY:
        payload['final_anchor_policy'] = _FINAL_ANCHOR_POLICY
    system += ('\nstory_progress与actual_state分工不同：actual_state只决定真实末帧的当前位置、姿态和持物；'
        'consumed_beats只表示之前的拍摄分配已经消耗，不能将其goal或planned_ending当成已发生事实，'
        '不要为了重新介绍人物或地点而重演旧段开头。observed_completed_events只含过程样片证明的结束事件，'
        '已明确结束的同一次攻击、交接、开启或败敌不能复活重演；镜外与未知不等于消失或失败。'
        '若当前beat提到已结束的同一事件，保留原话，只把镜头落实到其后果与本段新行动，不重启已完成过程。'
        'stable_visual_contract固定场景结构、时段、主色和地标，持续复用同一份道具外形；'
        '人设图决定人物外观。武器保持相同实体形状、颜色、长度和数量，魔法沿器物附着或向外释放，'
        '不得把剑变成法杖、让法杖变成另一种武器或凭空复制。持有人、位置及损坏随明确可见动作更新。'
        'props.count是全片登记量，可能跨多个场景，不是当前可见数；群体数量按本段原稿及实际状态。'
        '只为本段实际使用的道具沿用外形，后文道具不能提前出场。'
        '同一实体从身上取下或交接后，原位置不保留副本；能量附着与轨迹不是另一个实体。'
        '每段围绕一组主要因果行动推进，给予冲刺、格挡接触、闪避、反击和落地清楚的观看时间；'
        '动作有起势、接触或闪避、结果及关键反应，不以切镜跳过决定性接触或结果。'
        '按剧情切换空间建立景、动作跟拍和人物特写，切镜数量服从剧情，不用静态发光或无目的运镜代替行动。')
    return system, payload, schema
