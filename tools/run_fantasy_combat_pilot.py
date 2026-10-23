"""Retained five-clip, two-set local-director pilot; production defaults stay intact.

Usage through the offline bootstrap::
    python cores/story_video_offline.py -- tools/run_fantasy_combat_pilot.py FOLDER

FOLDER/config.json supplies the two adult character cards, two set images,
creative request, style, fixed A,A,B,B,B shooting schedule and bounded budgets.
No inference occurs on import. Existing clips and frozen requests are reused.
"""
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
VERSION = 'fantasy-combat-local-pilot-v1'


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_config(value):
    """Validate this deliberately narrow paid-generation test before any model call."""
    from cores import story_video_characters as characters
    config = copy.deepcopy(value)
    config['cards'] = characters.normalize_cards(config.get('cards'))
    if len(config['cards']) != 2:
        raise ValueError('本次测试固定两张成年主角人设图')
    scenes = config.get('scenes')
    if not isinstance(scenes, list) or len(scenes) != 2:
        raise ValueError('本次测试固定两张场景图')
    for scene in scenes:
        if (not isinstance(scene, dict) or any(not isinstance(scene.get(key), str)
                or not scene[key].strip() for key in ('id', 'name', 'path', 'description'))
                or not Path(scene['path']).is_file()):
            raise ValueError('每张场景图须有id、name、现存path与description')
    if len({scene['id'] for scene in scenes}) != 2 or len({scene['name'] for scene in scenes}) != 2:
        raise ValueError('两场景的ID和名字须各自唯一')
    sequence = config.get('scene_sequence')
    expected = [scenes[0]['id']] * 2 + [scenes[1]['id']] * 3
    if sequence != expected or config.get('segment_count') != 5 or config.get('segment_seconds') != 15:
        raise ValueError('本次固定5段各15秒；前2段场景A，后3段场景B')
    if config.get('geometry') != dict(width=1024, height=576, ratio='16:9'):
        raise ValueError('本次固定1024×576、16:9，保持已验收的采样参数')
    if any(not isinstance(config.get(key), str) or not config[key].strip()
           for key in ('creative_request', 'style')):
        raise ValueError('须提供本次故事目标和视觉风格')
    seeds, budget = config.get('seeds'), config.get('max_calls') or {}
    if (not isinstance(seeds, list) or len(seeds) != 5
            or any(type(seed) is not int for seed in seeds)):
        raise ValueError('须提供5个固定整数seed')
    if (type(budget.get('text_vision')) is not int or not 1 <= budget['text_vision'] <= 60
            or budget.get('h3') != 5):
        raise ValueError('须给出1～60次本地文本/视觉预算，H3预算固定5次')
    entities = config.get('authorized_entities', [])
    if (not isinstance(entities, list) or any(not isinstance(row, dict)
            or any(not isinstance(row.get(key), str) or not row[key].strip()
                   for key in ('id', 'name', 'appearance'))
            or row.get('kind') not in ('nonhuman', 'object') or row.get('silent') is not True
            or type(row.get('count', 1)) is not int or row.get('count', 1) < 1 for row in entities)):
        raise ValueError('授权附加实体须明确id/name/appearance、非人或物件类型、静默状态和数量')
    if type(config.get('observe_process_progress', False)) is not bool:
        raise ValueError('过程视觉进度选项须为布尔值')
    frozen = config.get('frozen_input_dir')
    if frozen is not None and (not isinstance(frozen, str) or not frozen.strip()
            or not all((Path(frozen) / name).is_file() for name in ('story.txt', 'plan.json', 'config.json'))):
        raise ValueError('冻结对照须提供包含原story.txt、plan.json和config.json的既有目录')
    return config


def scene_for_index(config, index):
    identity = config['scene_sequence'][index - 1]
    return next(scene for scene in config['scenes'] if scene['id'] == identity)


def references_for_index(config, index):
    from cores import story_video_characters as characters
    entrance = index == 1 or config['scene_sequence'][index - 1] != config['scene_sequence'][index - 2]
    refs = characters.references_for_segment(config['cards'],
        scene=scene_for_index(config, index) if entrance else None)
    voices = {card['id']: card['voice'] for card in config['cards']}
    for ref in refs:
        if ref['role'] == 'character':
            ref['voice'] = voices[ref['character_id']]
    return refs


def story_direction(config):
    places = '\n'.join('%s：%s' % (scene['name'], scene['description']) for scene in config['scenes'])
    schedule = '、'.join('第%d段=%s' % (index, scene_for_index(config, index)['name'])
                        for index in range(1, 6))
    entities = config.get('authorized_entities') or []
    entity_rule = ('仅允许以下明确授权的无对白非人实体，归入props，不新增有名字或发声的人物：'
        + json.dumps(entities, ensure_ascii=False)) if entities else '不新增人物或未授权的非人角色。'
    return (config['creative_request'] + '\n全片75秒，恰好5段，每段15秒。只有两位已登记的成年主角。'
        + entity_rule +
        '先写完整的小说正文，所有具体情节、台词、动作、胜负与结果由本地AI构思。'
        '开头立即建立冲突与逆转机会，具体题材和行动服从creative_request；清楚交代动作因果。'
        '核心行动保留清晰空间关系、起势、接触或闪避和结果，给特写及反应留足时间。'
        '正常完整衣着，非血腥场面。人物与场景的审美、时代、世界观及实际质感严格按style与参考图。'
        '\n本次仅两场景：\n' + places + '\n固定拍摄分配：' + schedule + '。'
        '正文必须把由第一场景进入第二场景的过程写清；第二段末建立明确的去向或可见通道，'
        '第三段按实际到达进度接续，不重复已经完成的转场；不增加第三个地点，不用人物凭空瞬移代替转场。'
        '最终段完成本次主线胜负及关系回应，以可见结果收束。对白简短自然，给战斗留足观看时间。')


def normalize_plan_entities(candidate, cards, material, authorized_entities=()):
    """JSON category correction only; never rewrite action, speech or entity names."""
    from cores import workbench_story_director_trial as adapter
    candidate = copy.deepcopy(candidate)
    phase_audit = []
    for beat in candidate.get('beats', []):
        phases = beat.get('action')
        if (isinstance(phases, list) and phases and all(isinstance(row, dict)
                and set(row) == {'phase', 'description'}
                and all(isinstance(row.get(key), str) and row[key].strip()
                        for key in ('phase', 'description')) for row in phases)):
            # JSON field names and string quotes are transport, not dialogue.
            beat['action'] = '\n'.join('【%s】%s' % (row['phase'], row['description']) for row in phases)
            phase_audit.append(dict(index=beat.get('index'), original=phases,
                normalized=beat['action'], reason='只展开phase/description容器，逐字保留本地动作原稿。'))
    plan, audit = adapter.normalize_observed_silent_entities(candidate, cards, material)
    audit['action_phase_transport'] = phase_audit
    known = {str(card[key]) for card in cards for key in ('id', 'name')}
    speaking = {str(line['speaker']) for beat in plan.get('beats', []) for line in beat.get('dialogue', [])}
    keep = []
    for row in plan['characters']:
        identity, name = row.get('id'), row.get('name')
        if identity in known or name in known:
            keep.append(row)
            continue
        authorization = next((item for item in authorized_entities
            if item.get('id') == identity and item.get('name') == name
            and item.get('kind') in ('nonhuman', 'object') and item.get('silent') is True), None)
        conflict = any(prop.get('id') == identity or prop.get('name') == name for prop in plan['props'])
        if (authorization is None or identity in speaking or name in speaking or conflict
                or not all(isinstance(row.get(key), str) and row[key].strip()
                           for key in ('id', 'name', 'appearance'))):
            keep.append(row)
            continue
        prop = dict(id=identity, name=name, appearance=row['appearance'], count=authorization.get('count', 1))
        plan['props'].append(prop)
        audit['migrated'].append(dict(id=identity, original_character=copy.deepcopy(row),
            normalized_prop=copy.deepcopy(prop), reason='仅对配置明确授权的无对白非人/物件实体更正JSON分类，原剧情不改'))
    plan['characters'] = keep
    audit['cast_reference_dedup'] = []
    for beat in plan.get('beats', []):
        cast = beat.get('cast')
        if isinstance(cast, list) and all(isinstance(value, str) for value in cast):
            unique = list(dict.fromkeys(cast))
            if unique != cast:
                audit['cast_reference_dedup'].append(dict(index=beat['index'], original=cast, normalized=unique,
                    reason='cast是实体ID集合；数量仍由原稿动作和props.count定义'))
                beat['cast'] = unique
    return plan, audit


def plan_transport_schema(canonical_schema):
    """Accept only known lossless wrappers before request_json runs its validator.

    The final normalized plan still has to pass canonical_schema. The local
    schema checker supports type unions and items, rather than anyOf branches.
    """
    schema = copy.deepcopy(canonical_schema)
    fields = schema['properties']['beats']['items']['properties']
    fields['cast']['uniqueItems'] = False
    fields['action'].update(type=['string', 'array'], minItems=1,
        items=dict(type='object', properties=dict(
            phase=dict(type='string', minLength=1),
            description=dict(type='string', minLength=1)),
            required=['phase', 'description'], additionalProperties=False))
    return schema


def recover_plan_transport(directory, cards, material, validator, authorized_entities=()):
    """Reuse a complete saved local draft after lossless JSON-list normalization."""
    responses = sorted(Path(directory).glob('call_*/*/response.json'),
                       key=lambda path: path.stat().st_mtime, reverse=True)
    for path in responses:
        try:
            candidate, audit = normalize_plan_entities(json.loads(_read(path)['text']), cards, material, authorized_entities)
            validator(candidate, None)
            return candidate, dict(source_response=str(path), audit=audit, creative_changes=False)
        except (ValueError, KeyError, TypeError):
            continue
    return None, None


def recover_director_transport(directory, payload):
    """Try saved complete local replies with the same strict final compiler."""
    from cores import workbench_pilot_continuity as continuity
    binding = Path(directory) / 'binding.json'
    if binding.exists() and _read(binding).get('payload') != payload:
        return None, None, None
    responses = sorted(Path(directory).glob('call_*/*/response.json'),
                       key=lambda path: path.stat().st_mtime, reverse=True)
    for path in responses:
        try:
            raw = json.loads(_read(path)['text'])
            compiled = continuity.compile_director_reply(raw, payload)
            return raw, compiled, dict(source_response=str(path), creative_changes=False,
                                      policy='recover-complete-reply-after-lossless-director-transport')
        except (ValueError, KeyError, TypeError):
            continue
    return None, None, None


def transport_source(beat, save, cards=()):
    from cores import workbench_story_director_trial as adapter
    original = beat
    beat = copy.deepcopy(beat)
    names = {card['id']: card['name'] for card in cards}
    for line in beat.get('dialogue', []):
        line['speaker'] = names.get(line['speaker'], line['speaker'])
    # A line-leading camera label followed by a colon is layout, not a speaker.
    beat['action'] = re.sub(r'(?m)^(\s*【[^】\n]{1,24}】(?:大远景|中远景|中近景|远景|全景|中景|近景|特写))[:：]',
                            r'\1。', beat['action'])
    if beat != original:
        save('source_transport_warnings/beat_%02d_layout.json' % beat['index'],
             dict(original=original, transported=beat, creative_changes=False,
                  policy='canonical-speaker-alias-and-camera-heading-punctuation-only'))
    try:
        return adapter.main_source_text(beat)
    except ValueError as error:
        evidence = getattr(error, 'evidence', {})
        expected = [(line['speaker'], line['text']) for line in beat.get('dialogue', [])]
        parsed = evidence.get('parsed_dialogue') or []
        if ([words for _, words in parsed] != [words for _, words in expected]
                or any(speaker not in (owner, '待核对说话人')
                       for (speaker, _), (owner, _) in zip(parsed, expected))):
            raise
        save('source_transport_warnings/beat_%02d.json' % beat['index'],
            dict(status='warning', quality_gate=False, evidence=evidence,
                 policy='retain-ambiguous-source-use-validated-dialogue-slots'))
        return beat['action']


def build_timeline(plan, config, save):
    """Two real main-workbench scene groups; segment numbers stay globally unique."""
    scenes, slices = [], []
    cards = config['cards']
    for index, beat in enumerate(plan['beats'], 1):
        scene = scene_for_index(config, index)
        text = transport_source(beat, save, cards)
        names = [card['name'] for card in cards if card['id'] in beat['cast']]
        if not scenes or scenes[-1]['scene_id'] != scene['id']:
            scenes.append(dict(no=len(scenes) + 1, scene_id=scene['id'], name=scene['name'], segments=[]))
        scenes[-1]['segments'].append(dict(no=index, scene=scene['name'], chars=names, seconds=15,
            source_text=text, prompt='', plan=dict(flow='source-v1'), critical_problems=[]))
        slices.append(dict(text=text, seconds=15, scene_hint=scene['name'], director=dict(
            flow='source-v1', cast=names, start_state='', target_state=beat['ending'])))
    return dict(scenes=scenes, slicing=dict(slices=slices))


def _row(timeline, index):
    for scene in timeline['scenes']:
        for row in scene['segments']:
            if row['no'] == index:
                return scene['no'], row
    raise ValueError('缺少第%d段' % index)


def opening_facts(config, actual_state):
    """A montage summary cannot become the next video's literal opening state."""
    state = copy.deepcopy(actual_state or {})
    text = ' '.join(str(state.get(key) or '') for key in ('actual_visible', 'scene'))
    places = [scene['name'] for scene in config['scenes'] if scene['name'] in text]
    sample_numbers = set(re.findall(r'图\s*([1-9]\d*)', text))
    if len(places) > 1 or len(sample_numbers) > 1:
        return dict(actual_visible='unknown', scene='unknown', characters=[], props=[],
            actions=[], completed=[], unknowns=['旧观察混合多个时间或地点，未将其当作实际末帧事实。'])
    return state


def freeze_baseline_inputs(config, folder, manifest):
    """Read-only baseline import; exact bytes and creative plan are never rewritten."""
    baseline = config.get('frozen_input_dir')
    if not baseline:
        return None
    baseline, folder = Path(baseline).resolve(), Path(folder).resolve()
    if folder == baseline or folder.is_relative_to(baseline):
        raise ValueError('对照输出必须位于原测试目录之外，不能改动原样片目录')
    original_config = validate_config(_read(baseline / 'config.json'))
    for key in ('scene_sequence', 'segment_count', 'segment_seconds', 'geometry', 'seeds'):
        if config[key] != original_config[key]:
            raise ValueError('冻结对照须复用原始参数：' + key)
    for current, original in zip(config['cards'], original_config['cards']):
        if (any(current[key] != original[key] for key in ('id', 'name', 'personality', 'voice'))
                or _sha(current['image_path']) != _sha(original['image_path'])):
            raise ValueError('冻结对照须复用原人物卡和人设图')
    for current, original in zip(config['scenes'], original_config['scenes']):
        if (any(current[key] != original[key] for key in ('id', 'name', 'description'))
                or _sha(current['path']) != _sha(original['path'])):
            raise ValueError('冻结对照须复用原始场景图和地点定义')
    source = (baseline / 'story.txt').read_text(encoding='utf-8-sig')
    if not source.strip():
        raise ValueError('原始小说正文为空，未启动模型')
    plan = _read(baseline / 'plan.json')
    from cores import story_video_core as core, story_video_reference as reference
    from cores import story_video_reference_prompts as prompts
    request = prompts.plan_request(source, config['style'], 5, 15, {},
        flow_optimization=True, reference_visual_policy=prompts.REFERENCE_VISUAL_POLICY,
        scene_reference_provided=True)
    schema = reference._plan_usable_schema(request[2])
    schema['properties']['beats']['items']['properties']['scene'] = dict(type='string', minLength=1)
    core.validate_schema(plan, schema)
    reference._validate_usable_reference_plan(plan, dict(count=5, seconds=15), config['cards'])
    if any(beat.get('scene') != scene_for_index(config, index)['name']
           for index, beat in enumerate(plan['beats'], 1)):
        raise ValueError('原计划场景分配不符，未改写原计划')
    record = dict(baseline_dir=str(baseline), policy='exact-source-plan-comparison-v1',
        creative_changes=False, author_and_planning_calls=0,
        files={name: dict(source=str(baseline / name), sha256=_sha(baseline / name))
               for name in ('story.txt', 'plan.json', 'config.json')})
    previous = manifest.get('frozen_baseline')
    if previous is not None and previous != record:
        raise ValueError('冻结原稿或原配置已变化，保留本轮全部结果')
    for name in ('story.txt', 'plan.json'):
        target = folder / name
        if target.exists() and _sha(target) != record['files'][name]['sha256']:
            raise ValueError('本轮冻结原稿存在冲突，未覆盖：' + name)
    for name in ('story.txt', 'plan.json'):
        target = folder / name
        if not target.exists():
            shutil.copy2(baseline / name, target)
    manifest['frozen_baseline'] = record
    return source, plan


def scene_director_contract(config, beat, actual_state, *, has_previous):
    """A new reference slot is not evidence of an on-screen departure."""
    state = opening_facts(config, actual_state)
    start = str(state.get('actual_visible') or '').strip()
    contract = dict(start_state=start if has_previous and start != 'unknown' else '',
                    target_state=beat['ending'])
    index = beat['index']
    if not has_previous or index <= 1:
        return contract
    departure, arrival = scene_for_index(config, index - 1), scene_for_index(config, index)
    # Only an observed departure location plus the current action's explicit
    # passage can require departure staging. Unknown/mixed or already-arrived
    # endpoints must not resurrect a location merely because an image slot changed.
    observed = str(state.get('scene') or '')
    if (departure['id'] == arrival['id'] or departure['name'] not in observed
            or arrival['name'] in observed):
        return contract
    action = beat['action']
    left, right = action.find(departure['name']), action.find(arrival['name'])
    passage = action[left:right + len(arrival['name'])] if left >= 0 and right > left else ''
    movement = r'(?:穿过|穿入|跃入|冲入|走入|踏入|跨入|飞入|进入|通过|越过)'
    channel = r'(?:传送门|光门|通道|门洞|门环|光幕)'
    if not re.search(movement + r'.{0,24}' + channel + '|' + channel + r'.{0,24}' + movement, passage, re.S):
        # A new-scene beat without an explicit departure passage is a scene cut,
        # not authorization to paste the old location into its beginning shot.
        # The literal old frame remains in payload.actual_state as evidence.
        contract.update(start_state='', scene_cut=dict(**{'from': departure['name']},
            to=arrival['name'], source_evidence=action,
            policy='当前分段从新地点开场；剪辑建立新场景，不补写离场或穿门动作。'))
        return contract
    contract['scene_transition'] = dict(**{'from': departure['name']}, to=arrival['name'],
                                        source_evidence=passage)
    return contract


def scene_write_request(request, config, beat, actual_state, *, has_previous):
    system, payload, schema = request
    scene = scene_for_index(config, beat['index'])
    payload.update(actual_state=opening_facts(config, actual_state),
        current_scene_contract={key: scene[key] for key in ('id', 'name', 'description')},
        scene_schedule=[dict(index=index, id=scene_for_index(config, index)['id'],
                             name=scene_for_index(config, index)['name']) for index in range(1, 6)],
        scene_continuity_contract=scene_director_contract(config, beat, actual_state,
                                                        has_previous=has_previous))
    system += ('\ncurrent_scene_contract只固定本段地点的结构、时段、色彩与地标，不限制景别和机位。'
        'actual_state只含上一段实际画面的事实；参考图观察不是已经发生的开场。'
        '首次加入新场景图只表示参考图调度，不自动表示人物仍在旧地点。'
        '上一段末帧已到当前地点时直接承接，不回旧地点重演通道。'
        'scene_continuity_contract若含scene_transition，上一段实际末态属于出发地，'
        '本段场景图只定义到达地；按本段原稿的可见通道和动作完成换场，不混合两地建筑。'
        '若含scene_cut，本段原稿直接从新地点开场，按原稿剪辑建立新场景；'
        'actual_state保留的旧地点是此前末帧证据，不强塞进新场景开头，不擅自补写穿门或返程。'
        '同场景沿用已经建立的结构和光线，人物与道具动作继续向前，不复位。')
    return system, payload, schema


def render_anchor_preflight(compiled, config, scene):
    """Check the paid request text, rather than trusting payload-only metadata."""
    if config.get('final_anchor_policy') != 'render-anchors-v1':
        return None
    audit = compiled.get('final_anchor_audit') or {}
    prompt = compiled.get('prompt') or ''
    description = ' '.join(scene['description'].split())
    if (audit.get('policy') != 'render-anchors-v1' or audit.get('scene_id') != scene['id']
            or '【固定地点外观】' not in prompt or scene['name'] not in prompt
            or description not in prompt):
        raise RuntimeError('本轮固定场景尚未进入最终提示词，未提交视频请求')
    return dict(audit, prompt_sha256=hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
                scene_description_present=True)


def terminal_observation_request(config, adopted):
    """Use one true endpoint image for continuation; process samples stay in QA."""
    from cores import story_video_reference_prompts as prompts
    # No earlier montage or expected beat is supplied as visual evidence. Fixed
    # identities are labels only; a single frame cannot prove completed actions.
    system, payload, schema = prompts.observe_request('opening')
    payload.update(observation_role='segment_end',
        canonical_characters=[dict(id=card['id'], name=card['name']) for card in config['cards']],
        canonical_scene_names=[scene['name'] for scene in config['scenes']],
        image_role_bindings=[dict(image_index=1, role='actual_final_frame',
            frame_index=adopted.get('frame_index'), time_seconds=adopted.get('frame_time_seconds'))],
        clip_duration_seconds=adopted.get('kept_seconds'),
        evidence_scope='single actual final frame only; process samples retained separately for QA')
    system += ('\n本次唯一附图是刚生成视频的真实最后一帧，不是开场素材或首中尾拼图。'
        'actual_visible、scene和characters/props的visibility只写此末帧当前可见状态。'
        '空场就如实写当前未见人物；人物身份、位置或动作看不清写unknown，不能从故事补出人物。'
        'canonical_scene_names只是地点命名候选；画面确实可辨对应地点才使用其名称，否则描述实际地形或unknown。'
        '单张末帧不能证明此前动作，actions和completed保持空数组，keep_end_seconds填null。'
        '此前的计划、对白和过程样片均不构成本次末帧的可见证据；不要求补拍或裁切。')
    return (system, payload, schema), [adopted['anchor_path']]


@contextmanager
def single_submission(session, work, index):
    """Count real /prompt POSTs, including interrupted ones; recovery polls are free."""
    from cores import store
    from models import h3_client
    path = work / 'video_submission.json'
    ledger = _read(path) if path.exists() else dict(index=index, submissions=0)
    original_http = h3_client._http

    def limited(method, endpoint, *args, **kwargs):
        if method.upper() == 'POST' and endpoint == '/prompt':
            session.guard()
            if ledger['submissions'] >= 1:
                raise RuntimeError('本段已经提交一次，保留冻结请求，仅恢复原任务，不自动重拍')
            used = session.manifest.get('actual_h3_submissions', 0)
            if used >= session.manifest['reference_policy']['max_h3_submissions']:
                raise RuntimeError('已达到本次5段视频预算，全部结果保留')
            ledger.update(submissions=1, submitted_at=time.time())
            store.save_json(path, ledger)
            session.manifest['actual_h3_submissions'] = used + 1
            session.checkpoint()
        return original_http(method, endpoint, *args, **kwargs)
    h3_client._http = limited
    try:
        yield
    finally:
        h3_client._http = original_http


def _freeze_config(config, folder, manifest):
    """Keep independent snapshots without changing source images or prior outputs."""
    frozen = copy.deepcopy(config)
    records = manifest.setdefault('reference_assets', [])
    sources = [(card, 'image_path', 'character_%02d' % index)
               for index, card in enumerate(frozen['cards'], 1)]
    sources += [(scene, 'path', 'scene_%02d' % index)
                for index, scene in enumerate(frozen['scenes'], 1)]
    if records and len(records) != len(sources):
        raise ValueError('已冻结参考数量变化，保留全部结果')
    for number, (item, key, label) in enumerate(sources):
        source = Path(item[key]).resolve()
        target = folder / 'references' / (label + source.suffix.lower())
        digest = _sha(source)
        if records:
            saved = records[number]
            if (saved['source'] != str(source) or saved['sha256'] != digest
                    or _sha(saved['path']) != digest):
                raise ValueError('参考图或冻结快照变化，保留已有结果')
            target = Path(saved['path'])
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and _sha(target) != digest:
                raise ValueError('未登记参考快照存在冲突，未覆盖')
            if not target.exists():
                shutil.copy2(source, target)
            # Append after all copies below to avoid mistaking this first run
            # for recovery when copying its second reference.
        item[key] = str(target)
    if not records:
        for item, key, label in sources:
            source_item = config['cards'][int(label[-2:]) - 1] if label.startswith('character') else config['scenes'][int(label[-2:]) - 1]
            source_key = 'image_path' if label.startswith('character') else 'path'
            records.append(dict(source=str(Path(source_item[source_key]).resolve()),
                                path=item[key], sha256=_sha(item[key])))
    return frozen


def run(folder):
    # Imports happen only inside the explicit child run, after the caller has
    # entered the offline bootstrap. Each real local call also checks PUBG.
    os.environ.update(V41_QWEN_PORT='18080', V41_QWEN_URL='http://127.0.0.1:18080')
    from cores import story_video_offline as offline
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    offline.install(folder / 'offline_network_audit.jsonl')
    from cores import store, story_core, saga_core, asset_core
    from cores import story_video_reference as reference, story_video_reference_prompts as prompts
    from cores import story_video_characters as characters, story_video_core as core
    from cores import story_video_media as media, story_video_continuation as continuation
    from cores import workbench_story_director_trial as adapter
    from cores import workbench_pilot_continuity as continuity
    from models import story_video_client as client, gpu_manager
    from api import segment_api as segment

    config = validate_config(_read(folder / 'config.json'))
    signature = _signature(config)
    manifest_path = folder / 'manifest.json'
    manifest = _read(manifest_path) if manifest_path.exists() else dict(version=VERSION,
        task_id=folder.name, config_sha256=signature, created=time.time(), segments=[], logs=[],
        local_ai_only=True, production_defaults_changed=False, actual_model_calls=0,
        actual_h3_submissions=0, geometry=config['geometry'], seeds=config['seeds'],
        reference_policy=dict(max_text_vision_calls=config['max_calls']['text_vision'],
                              max_h3_submissions=config['max_calls']['h3'], max_takes_per_segment=1))
    if manifest['version'] != VERSION or manifest['config_sha256'] != signature:
        raise ValueError('测试输入变化，已有结果全部保留，请使用新目录')
    config = _freeze_config(config, folder, manifest)
    frozen_inputs = freeze_baseline_inputs(config, folder, manifest)
    cards, style = config['cards'], config['style']
    inputs = dict(story_input='idea', story_direction=story_direction(config), style=style,
        segment_count=5, segment_seconds=15, reference_visual_policy=prompts.REFERENCE_VISUAL_POLICY,
        initial_scene=config['scenes'][0]['path'])
    manifest['inputs'] = inputs
    def save(name, value):
        store.save_json(folder / name, value)
    save('effective_config.json', config)
    store.DATA = folder / 'isolated_data'
    for name in ('stories', 'assets', 'tasks', 'narrative', 'states', 'profiles', 'manifests'):
        (store.DATA / name).mkdir(parents=True, exist_ok=True)

    class Handle:
        tid = folder.name
        def step(self, label, done=0, total=5, detail=''):
            print(label, flush=True)
            save('status.json', dict(stage=label, done=done, total=total, updated=time.time()))
        def set(self, **values):
            print(json.dumps(values, ensure_ascii=False), flush=True)

    class PilotSession(reference._Session):
        def cached(self, name, inputs, action):
            original = super().cached(name, inputs, action)
            if name != 'author_plan':
                return original
            adapted = continuity.single_episode_author_plan(original, self.inputs['segment_count'])
            if adapted.get('author_transport'):
                save('author_plan_transport.json', dict(original=original, adapted=adapted,
                    original_cache_untouched=True, creative_changes=False))
            return adapted

    session = PilotSession(folder.name, Handle(), folder, inputs, manifest)
    if manifest.get('error'):
        manifest.setdefault('recovered_errors', []).append(manifest.pop('error'))
    manifest['status'] = 'running'
    session.checkpoint()
    sid = 'TRIAL_' + folder.name
    story = story_core.get_story(sid)
    if not story:
        story = dict(story_id=sid, title=config.get('title') or '五段双场景本地导演试验',
            settings=dict(story_mode='single', style=style), saga=dict(episodes=[dict(no=1)]))
        story_core.save_story(story)
        for index, card in enumerate(cards, 1):
            asset_core.save_asset(sid, 'characters', dict(character_id=card['id'], name=card['name'],
                behavior_anchor=card['personality'], voice=card['voice'], created=index))
            asset_core.save_asset(sid, 'visuals', dict(visual_id='V_' + card['id'], owner_id=card['id'],
                status='adopted', kind='character', path=card['image_path'], created=index))
        for scene in config['scenes']:
            asset_core.save_asset(sid, 'scenes', dict(scene_id=scene['id'], name=scene['name'], description=scene['description']))
            asset_core.save_asset(sid, 'visuals', dict(visual_id='V_' + scene['id'], owner_id=scene['id'],
                status='adopted', kind='scene', path=scene['path']))
    story.setdefault('settings', {}).update(video_steps=10, video_ratio='16:9', video_size_tier='0.7')
    story_core.save_story(story)
    saga_core.seg_video_path = lambda _sid, _ep, _sc, number, **kw: str(folder / 'segments' / ('%02d' % number) / 'main_render.mp4')
    try:
        with session.models():
            first_refs = characters.references_for_segment(cards)
            for scene in config['scenes']:
                first_refs.append(dict(role='scene_anchor', picture=len(first_refs) + 1,
                                       name=scene['name'], path=scene['path']))
            observation = prompts.observe_request('references')
            observation[1].update(image_role_bindings=[dict(image_index=index, role=ref['role'],
                name=ref.get('name'), **{key: ref[key] for key in ('character_id', 'subject') if key in ref})
                for index, ref in enumerate(first_refs, 1)],
                canonical_characters=[dict(id=card['id'], name=card['name']) for card in cards])
            material = session.cached('reference_observation', dict(request=observation),
                lambda: session.review(observation, folder / 'reference_observation/model', [ref['path'] for ref in first_refs]))
            if material.get('status') == 'unavailable':
                material = continuation.unknown_observation(material)
            material = prompts.sanitize_reference_observation(material, observation[1]['image_role_bindings'])
            material['reference_scenes'] = [{key: scene[key] for key in ('id', 'name', 'description')}
                                            for scene in config['scenes']]
            if frozen_inputs is not None:
                source, plan = frozen_inputs
                save('frozen_input_provenance.json', manifest['frozen_baseline'])
            else:
                source = reference._story(session, cards, material)
                contract = session.cached('source_contract', dict(source=source, cards=cards),
                    lambda: characters.prepare_source_contract(source, cards, strict=False))
                planning = prompts.plan_request(source, style, 5, 15, material,
                    flow_optimization=True, reference_visual_policy=prompts.REFERENCE_VISUAL_POLICY,
                    scene_reference_provided=True)
                planning[1].update(canonical_characters=characters.authoring_characters(cards), source_contract=contract,
                    authorized_entities=config.get('authorized_entities') or [],
                    scene_schedule=[dict(index=index, id=scene_for_index(config, index)['id'],
                        name=scene_for_index(config, index)['name'], description=scene_for_index(config, index)['description']) for index in range(1, 6)])
                planning = (planning[0] + '\ncanonical_characters是唯一两位稳定人物。严格保留正文台词及source_contract顺序；'
                    '额外无发声实体仅限authorized_entities明确授权的非人或物件，写入props，不另设人物参考。'
                    'authorized_entities为空时不新增敌方人物或灵兽。scene_schedule是固定拍摄地点：前两段A、后三段B。'
                    '剧情和转场由本地AI根据完整正文写清，各段行动不能脱离该地点。'
                    'props为持续道具自动提炼简短固定外形、材质、颜色及数量；invariants区分固定形状和可改变的状态。'
                    '一段围绕一组主要因果行动，多镜头服务起势、接触/闪避、结果和反应；同一回合结束后推进新回合，不复活重演。'
                    'cast是实体ID集合，同一ID只列一次；action写正文字符串，时间阶段可用【0～5秒】标题，JSON字段名不是台词。',
                    planning[1], reference._plan_usable_schema(planning[2]))
                canonical_plan_schema = copy.deepcopy(planning[2])
                planning = (planning[0], planning[1], plan_transport_schema(canonical_plan_schema))
                def validate_plan(candidate, payload):
                    normalized, _ = normalize_plan_entities(candidate, cards, material, config.get('authorized_entities', []))
                    core.validate_schema(normalized, canonical_plan_schema)
                    reference._validate_usable_reference_plan(normalized, planning[1], cards)
                def obtain_plan():
                    recovered, evidence = recover_plan_transport(folder / 'planning/model', cards, material, validate_plan,
                                                                 config.get('authorized_entities', []))
                    if recovered is not None:
                        save('planning/recovered_transport.json', evidence)
                        return recovered
                    return session.ask(planning, folder / 'planning/model', validator=validate_plan)
                raw_plan = session.cached('planning', dict(request=planning), obtain_plan)
                plan, adjustments = normalize_plan_entities(raw_plan, cards, material, config.get('authorized_entities', []))
                for index, beat in enumerate(plan['beats'], 1):
                    beat['scene'] = scene_for_index(config, index)['name']
                save('planning/entity_transport.json', dict(audit=adjustments, creative_changes=False))
                save('planning/source_validation.json', reference._plan_source_audit(plan, planning[1], contract, cards, folder / 'planning/model'))
                save('plan.json', plan)
            timeline = saga_core.ep_timeline(sid, 1)
            if not timeline or not timeline.get('scenes'):
                timeline = build_timeline(plan, config, save)
                saga_core.save_ep_timeline(sid, 1, timeline)
            for beat in plan['beats']:
                index = beat['index']
                if any(row['index'] == index for row in manifest['segments']):
                    continue
                previous = manifest['segments'][-1] if manifest['segments'] else None
                state = previous['observation'] if previous else material
                refs = references_for_index(config, index)
                scene = scene_for_index(config, index)
                work = folder / 'segments' / ('%02d' % index)
                request = adapter.write_request(plan, beat, state, refs, seconds=15,
                    visual_style=style, user_brief=inputs['story_direction'] + '\n完整正文：\n' + source, scene_name=scene['name'])
                request = scene_write_request(request, config, beat, state, has_previous=previous is not None)
                request = continuity.apply_director_context(request, config, plan, scene, manifest['segments'])
                save('progress_ledger.json', request[1]['story_progress'])
                save('segments/%02d/progress_ledger_before.json' % index, request[1]['story_progress'])
                session.stage('第%d/5段：本地中文多镜头导演写词' % index, index - 1)
                raw, compiled, recovered = recover_director_transport(work / 'writing', request[1])
                if raw is None:
                    try:
                        raw = session.ask(request, work / 'writing', validator=continuity.compile_director_reply)
                        compiled = continuity.compile_director_reply(raw, request[1])
                    except RuntimeError:
                        raw, compiled, recovered = recover_director_transport(work / 'writing', request[1])
                        if raw is None:
                            raise
                if recovered:
                    save('segments/%02d/director_transport_recovery.json' % index, recovered)
                save('segments/%02d/director.json' % index, compiled)
                (work / 'compiled_prompt.txt').write_text(compiled['prompt'], encoding='utf-8')
                save('segments/%02d/request_payload.json' % index, request[1])
                timeline = saga_core.ep_timeline(sid, 1)
                scene_no, current = _row(timeline, index)
                if not current.get('render_request'):
                    contract = scene_director_contract(config, beat, state, has_previous=previous is not None)
                    current.update(prompt=compiled['prompt'], refs=[dict(n=ref['name']) for ref in refs],
                        director_contract=contract)
                    timeline['slicing']['slices'][index - 1]['director'].update(contract)
                    saga_core.save_ep_timeline(sid, 1, timeline)
                issues = segment._render_critical_problems(story_core.get_story(sid), timeline, current, current['prompt'], 1, index)
                save('segments/%02d/main_preflight.json' % index, dict(issues=issues))
                if issues:
                    raise RuntimeError('主流程实际提交契约未通过，保留已完成视频：' + '；'.join(issues))
                if not segment.is_segment_current(sid, 1, scene_no, index, size_tier='0.7'):
                    session.stage('第%d/5段：主流程提交H3，%s' % (index,
                        '人物图＋本场景图' if len(refs) == 3 else '仅人物图，不加尾帧图'), index - 1)
                    if config.get('final_anchor_policy') == 'render-anchors-v1':
                        anchor_audit = render_anchor_preflight(compiled, config, scene)
                        save('segments/%02d/final_anchor_preflight.json' % index, anchor_audit)
                    started = time.time()
                    (work / 'generation').mkdir(parents=True, exist_ok=True)
                    with single_submission(session, work, index), gpu_manager.GPU_LOCK:
                        session.guard()
                        gpu_manager.acquire('h3', note='five-clip fantasy combat pilot')
                        try:
                            with client._native_profile(client._frames(15), work / 'generation', manifest['geometry']):
                                result = segment.render_segment(sid, 1, scene_no, index, size_tier='0.7',
                                    override=dict(seed=config['seeds'][index - 1], ratio='16:9', steps=10), log_fn=lambda value: print(value, flush=True))
                        finally:
                            client.preserve_release('h3')
                    save('segments/%02d/main_result.json' % index, result)
                    save('segments/%02d/render_timing.json' % index, dict(seconds=time.time() - started))
                current = _row(saga_core.ep_timeline(sid, 1), index)[1]
                adopted = media.adopt(current['video'], work / 'adopted')
                samples = media.sample_at_times(adopted['clip_path'], work / 'samples', [0, 7, 14.8])
                observed, observation_images = terminal_observation_request(config, adopted)
                session.stage('第%d/5段：本地观察真实末帧，过程样片保留供检查' % index, index - 1)
                visible = session.review(observed, work / 'observation', observation_images)
                if visible.get('status') == 'unavailable':
                    visible = continuation.unknown_observation(visible)
                progress = dict(status='unavailable', reason='过程视觉进度未开启；仅保存已拍摄分配，不猜补完成事件。', quality_gate=False)
                if config.get('observe_process_progress', False):
                    progress_request = continuity.progress_observation_request(config, plan, samples)
                    progress = session.review(progress_request, work / 'progress_observation',
                        [row['path'] for row in samples], validator=continuity.validate_progress_observation)
                manifest['segments'].append(dict(adopted, index=index, scene_id=scene['id'], scene_name=scene['name'],
                    observation=visible, progress_observation=progress,
                    prompt_path=str(work / 'director.json'), used_refs=current.get('used_refs'),
                    render_request=current.get('render_request'), shot_count=compiled['prompt'].count('[Shot ')))
                save('progress_ledger.json', continuity.progress_ledger(plan, manifest['segments']))
                session.checkpoint()
            session.stage('合并5段双场景战斗测试成片', 5)
            joined = media.join(manifest['segments'], folder / 'joined')
            manifest.update(status='completed', joined=joined, finished=time.time())
            session.checkpoint()
            save('status.json', dict(stage='completed', done=5, total=5, joined=joined))
            print(json.dumps(dict(completed=True, joined=joined), ensure_ascii=False), flush=True)
    except Exception as error:
        manifest.update(status='paused_with_evidence', error=str(error))
        session.checkpoint()
        save('status.json', dict(stage='paused_with_evidence', error=str(error), done=len(manifest['segments']), total=5))
        raise


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: run_fantasy_combat_pilot.py OUTPUT_FOLDER')
    run(sys.argv[1])
