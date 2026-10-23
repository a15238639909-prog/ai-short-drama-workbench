"""No-model pilot checks; all generated test evidence is retained."""
from contextlib import contextmanager, nullcontext
import copy
import importlib.util
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fantasy_pilot', ROOT / 'tools/run_fantasy_combat_pilot.py')
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


def fixture():
    folder = ROOT / 'tests' / '.retained' / ('fantasy_pilot_%d' % time.time_ns())
    folder.mkdir(parents=True)
    for name in ('hero.png', 'mage.png', 'a.png', 'b.png'):
        (folder / name).write_bytes(b'retained mock image ' + name.encode())
    cards = [dict(id='hero', name='雷恩', image_path=str(folder / 'hero.png'),
                  personality='成年，勇敢', voice='明亮清晰的青年男声'),
             dict(id='mage', name='艾莉娅', image_path=str(folder / 'mage.png'),
                  personality='成年，果断', voice='明亮清晰的青年女声')]
    config = dict(cards=cards, scenes=[dict(id='A', name='天空竞技场', path=str(folder / 'a.png'),
        description='宽阔的露天石质竞技场'), dict(id='B', name='水晶圣殿', path=str(folder / 'b.png'),
        description='竞技场后方宽阔的水晶大殿')], scene_sequence=['A', 'A', 'B', 'B', 'B'],
        segment_count=5, segment_seconds=15, geometry=dict(width=1024, height=576, ratio='16:9'),
        seeds=[21, 22, 23, 24, 25], max_calls=dict(text_vision=40, h3=5),
        creative_request='两位成年主角在两处宽阔的场景协作完成剑术魔法战斗，精彩开头。', style='真实电影摄影，日式冒险人物审美')
    return folder, config


def story_plan():
    return dict(title='水晶试炼', logline='两位同伴协作完成试炼。', ending='水晶归位，光幕消退。',
        characters=[dict(id='hero', name='雷恩', appearance='沿用青年剑士图。', role='成年剑士'),
                    dict(id='mage', name='艾莉娅', appearance='沿用成年法师图。', role='成年法师')],
        props=[dict(id='guardian', name='无声魔像', appearance='金属魔法构装体', count=1)], invariants=[],
        beats=[dict(index=index, seconds=15, goal='配合完成试炼。', action='两人协作击退无声魔像。',
            turn='光幕逐渐消退。', emotion='通过抬眼展示坚定。', ending='两人站在石台旁。',
            cast=['hero', 'mage', 'guardian'], dialogue=[dict(speaker='雷恩', text='一起上。')]) for index in range(1, 6)])


def director_reply(payload):
    return dict(brief='两位主角协作完成试炼。', integrated_multimodal_description=(
        '[Shot 1] 远景交代宽阔空间，<Subject 1>向前冲刺挥剑，<Subject 2>在其侧方抬手施法，起势和回应持续前五秒。\n\n'
        '[Shot 2] At 00:05.000, 近景呈现剑锋击中光幕的结果，两人继续协作到第十秒。\n\n'
        '[Shot 3] At 00:10.000, 镜头转为双人中景，光幕消退，两位主角侧头看清彼此，最后五秒自然回应。'),
        overall_soundscape='脚步、剑锋击中光幕与魔法的轰鸣。', non_diegetic_music='N/A',
        expected_end_state='两人站在石台旁。',
        timeline=[dict(start=start, end=start + 5, action='协作行动持续。', visible_result='光幕逐渐消退。') for start in (0, 5, 10)],
        dialogue=[dict(speaker=slot['speaker'], text=slot['text'], start=6, end=8,
                       delivery='清晰坚定地发声。') for slot in payload['dialogue_slots']], continuity_notes=[])


class FantasyCombatPilotTests(unittest.TestCase):
    def test_camera_heading_colon_is_not_dialogue_and_speaker_alias_is_resolved(self):
        _, config = fixture()
        beat = story_plan()['beats'][0]
        beat.update(action='【0-5s】中景：雷恩举剑。\n【5-10s】近景：雷恩说：“一起上。”\n【10-15s】远景：两人冲出。',
                    dialogue=[dict(speaker='hero', text='一起上。')])
        original = copy.deepcopy(beat)
        text = pilot.transport_source(beat, lambda *_: None, config['cards'])
        self.assertIn('雷恩说：“一起上。”', text)
        self.assertEqual(text.count('一起上。'), 1)
        self.assertNotIn('近景：', text)
        self.assertEqual(beat, original)
        beat.update(action='【0-5s】中景：两人举剑。\n【5-10s】近景：两人冲出。', dialogue=[])
        self.assertIn('两人冲出。', pilot.transport_source(beat, lambda *_: None, config['cards']))

    def test_recover_repeated_entity_ids_keeps_action_dialogue_and_quantity(self):
        folder, config = fixture()
        plan = story_plan()
        plan['beats'][0]['cast'] += ['guardian', 'guardian']
        plan['props'][0]['count'] = 3
        original = copy.deepcopy(plan)
        trace = folder / 'model' / 'call_1_mock' / 'response_mock'
        trace.mkdir(parents=True)
        (trace / 'response.json').write_text(json.dumps(dict(text=json.dumps(plan))), encoding='utf-8')
        recovered, audit = pilot.recover_plan_transport(folder / 'model', config['cards'], {}, lambda *_: None)
        self.assertEqual(recovered['beats'][0]['cast'], ['hero', 'mage', 'guardian'])
        self.assertEqual(recovered['beats'][0]['action'], original['beats'][0]['action'])
        self.assertEqual(recovered['beats'][0]['dialogue'], original['beats'][0]['dialogue'])
        self.assertEqual(recovered['props'][0]['count'], 3)
        self.assertFalse(audit['creative_changes'])

    def test_transition_keeps_observed_departure_separate_from_destination(self):
        _, config = fixture()
        beat = story_plan()['beats'][2]
        beat['action'] = '两人从天空竞技场穿过蓝色传送门，进入水晶圣殿。'
        state = dict(actual_visible='两人还在天空竞技场入口的蓝色通道前。', scene='天空竞技场')
        system, payload, _ = pilot.scene_write_request(('', {}, {}), config, beat, state, has_previous=True)
        self.assertEqual(payload['current_scene_contract']['name'], '水晶圣殿')
        contract = payload['scene_continuity_contract']
        self.assertEqual(contract['start_state'], state['actual_visible'])
        self.assertEqual(contract['scene_transition'], {'from': '天空竞技场', 'to': '水晶圣殿',
                                                      'source_evidence': '天空竞技场穿过蓝色传送门，进入水晶圣殿'})
        self.assertIn('只定义到达地', system)
        first = pilot.scene_director_contract(config, story_plan()['beats'][0], state, has_previous=False)
        self.assertEqual(first['start_state'], '')
        self.assertNotIn('scene_transition', first)
        continuation = pilot.scene_director_contract(config, story_plan()['beats'][3], state, has_previous=True)
        self.assertEqual(continuation['start_state'], state['actual_visible'])
        self.assertNotIn('scene_transition', continuation)

    def test_scene_entrance_never_replays_departure_after_arrival_or_mixed_summary(self):
        _, config = fixture()
        beat = story_plan()['beats'][2]
        beat['action'] = '水晶圣殿中四具构装体围攻，雷恩举剑迎敌。'
        mixed = dict(actual_visible='图1：两人在天空竞技场战斗。图3、图4：水晶圣殿空场。',
            scene='天空竞技场与水晶圣殿',
            characters=[dict(id='hero', visibility='visible', certainty='observed')])
        original = copy.deepcopy(mixed)
        _, payload, _ = pilot.scene_write_request(('', {}, {}), config, beat, mixed, has_previous=True)
        contract = payload['scene_continuity_contract']
        self.assertNotIn('scene_transition', contract)
        self.assertEqual(contract['start_state'], '')
        self.assertEqual(payload['actual_state']['actual_visible'], 'unknown')
        self.assertEqual(payload['actual_state']['characters'], [])
        self.assertEqual(mixed, original)
        arrived = dict(actual_visible='水晶圣殿空场，未见人物。', scene='水晶圣殿')
        # Even an old passage in the plan cannot override an observed arrival.
        beat['action'] = '两人从天空竞技场穿过蓝色传送门，进入水晶圣殿。'
        after = pilot.scene_director_contract(config, beat, arrived, has_previous=True)
        self.assertNotIn('scene_transition', after)
        self.assertEqual(after['start_state'], arrived['actual_visible'])
        departure = dict(actual_visible='两人站在天空竞技场的门前。', scene='天空竞技场')
        before = pilot.scene_director_contract(config, beat, departure, has_previous=True)
        self.assertEqual(before['scene_transition']['from'], '天空竞技场')
        self.assertEqual(before['scene_transition']['to'], '水晶圣殿')

    def test_terminal_observation_uses_only_true_anchor_and_cannot_claim_process_actions(self):
        _, config = fixture()
        adopted = dict(anchor_path='actual_last_frame.png', frame_index=361,
                       frame_time_seconds=15.041667, kept_seconds=15.083333)
        original = copy.deepcopy(adopted)
        (system, payload, schema), images = pilot.terminal_observation_request(config, adopted)
        self.assertEqual(images, ['actual_last_frame.png'])
        self.assertIsNone(payload['previous_state'])
        self.assertIsNone(payload['beat'])
        self.assertEqual(payload['observation_role'], 'segment_end')
        self.assertEqual(payload['image_role_bindings'], [dict(image_index=1,
            role='actual_final_frame', frame_index=361, time_seconds=15.041667)])
        self.assertEqual(schema['properties']['actions']['maxItems'], 0)
        self.assertEqual(schema['properties']['completed']['maxItems'], 0)
        self.assertEqual(schema['properties']['keep_end_seconds'], {'type': 'null'})
        self.assertIn('空场就如实写当前未见人物', system)
        self.assertIn('真实最后一帧', system)
        self.assertEqual(adopted, original)

    def test_fixed_scene_pack_schedule_has_no_extra_end_frame(self):
        folder, config = fixture()
        normalized = pilot.validate_config(config)
        for index, count in enumerate((3, 2, 3, 2, 2), 1):
            refs = pilot.references_for_index(normalized, index)
            self.assertEqual(len(refs), count)
            self.assertEqual([r['picture'] for r in refs], list(range(1, count + 1)))
            self.assertFalse(any(ref['role'] == 'previous_frame' for ref in refs))
            if count == 3:
                self.assertEqual(refs[-1]['name'], '天空竞技场' if index == 1 else '水晶圣殿')

    def test_scene_allocation_maps_main_groups_without_changing_actions_or_speech(self):
        _, config = fixture()
        plan = story_plan()
        before = copy.deepcopy(plan)
        timeline = pilot.build_timeline(plan, config, lambda *_: None)
        self.assertEqual([[row['no'] for row in scene['segments']] for scene in timeline['scenes']], [[1, 2], [3, 4, 5]])
        self.assertEqual([pilot._row(timeline, index)[0] for index in range(1, 6)], [1, 1, 2, 2, 2])
        self.assertEqual(plan, before)
        self.assertTrue(all('雷恩说：“一起上。”' in row['source_text'] for scene in timeline['scenes'] for row in scene['segments']))

    def test_only_explicit_authorized_silent_nonhuman_can_move_to_props(self):
        _, config = fixture()
        plan = story_plan()
        enemy = plan['props'].pop()
        plan['characters'].append(dict(id=enemy['id'], name=enemy['name'], appearance=enemy['appearance'], role='静默构装体'))
        original_beats = copy.deepcopy(plan['beats'])
        retained, _ = pilot.normalize_plan_entities(plan, config['cards'], dict(characters=[]))
        self.assertEqual(len(retained['characters']), 3)
        authorized = [dict(id=enemy['id'], name=enemy['name'], kind='nonhuman', silent=True,
                           appearance=enemy['appearance'], count=1)]
        fixed, audit = pilot.normalize_plan_entities(plan, config['cards'], dict(characters=[]), authorized)
        self.assertEqual([row['id'] for row in fixed['characters']], ['hero', 'mage'])
        self.assertEqual(fixed['props'][0]['id'], 'guardian')
        self.assertEqual(fixed['beats'], original_beats)
        self.assertEqual(len(audit['migrated']), 1)
        speaking = copy.deepcopy(plan)
        speaking['beats'][0]['dialogue'].append(dict(speaker='guardian', text='停下。'))
        kept, _ = pilot.normalize_plan_entities(speaking, config['cards'], dict(characters=[]), authorized)
        self.assertEqual(len(kept['characters']), 3)

    def test_phase_container_expands_losslessly_without_inventing_quoted_dialogue(self):
        _, config = fixture()
        plan = story_plan()
        phases = [dict(phase='0～5秒', description='雷恩持剑后撤。'),
                  dict(phase='5～15秒', description='艾莉娅挥手，光幕消退。')]
        plan['beats'][0].update(action=phases, dialogue=[])
        original = copy.deepcopy(plan)
        fixed, audit = pilot.normalize_plan_entities(plan, config['cards'], {})
        self.assertEqual(fixed['beats'][0]['action'], '【0～5秒】雷恩持剑后撤。\n【5～15秒】艾莉娅挥手，光幕消退。')
        text = pilot.transport_source(fixed['beats'][0], lambda *_: None, config['cards'])
        self.assertNotIn('description', text)
        self.assertEqual(plan, original)
        self.assertEqual(len(audit['action_phase_transport']), 1)

    def test_direction_uses_config_subject_and_never_adds_western_enemy(self):
        _, config = fixture()
        config.update(creative_request='中式仙侠，二人协力关闭失控剑阵。', style='中式仙侠写实摄影', authorized_entities=[])
        direction = pilot.story_direction(config)
        self.assertIn(config['creative_request'], direction)
        self.assertNotIn('构装体', direction)
        self.assertNotIn('日式', direction)
        self.assertIn('不新增人物或未授权的非人角色', direction)

    def test_progress_and_stable_visual_context_keep_planned_and_observed_separate(self):
        from cores import workbench_pilot_continuity as continuity
        _, config = fixture()
        plan = story_plan()
        actual = dict(actual_visible='空场，未见人物。', scene='天空竞技场', characters=[])
        completed = dict(entity_id='guardian', event='魔像已经碎成残骸。', evidence_image=3,
                         evidence_description='地面可见同一金属魔像的碎片。')
        segments = [dict(index=1, observation=actual, progress_observation=dict(status='unavailable')),
                    dict(index=2, observation=actual, progress_observation=dict(completed_events=[completed], unknowns=[]))]
        before = copy.deepcopy((config, plan, actual, segments))
        system, payload, _ = continuity.apply_director_context(('', dict(actual_state=actual), {}),
            config, plan, config['scenes'][1], segments)
        ledger = payload['story_progress']
        self.assertEqual([row['index'] for row in ledger['consumed_beats']], [1, 2])
        self.assertEqual(ledger['consumed_beats'][0]['planned_ending'], plan['beats'][0]['ending'])
        self.assertEqual(ledger['consumed_beats'][0]['status'], 'filmed_allocation_not_observed_success')
        self.assertEqual(ledger['observed_completed_events'][0]['event'], completed['event'])
        self.assertEqual(ledger['observed_completed_events'][0]['segment_index'], 2)
        self.assertEqual(payload['actual_state'], actual)
        self.assertNotIn('魔像', payload['actual_state']['actual_visible'])
        self.assertEqual(payload['stable_visual_contract']['scene']['description'], config['scenes'][1]['description'])
        self.assertEqual(payload['stable_visual_contract']['props'], plan['props'])
        self.assertIn('不重启已完成过程', system)
        self.assertEqual((config, plan, actual, segments), before)

    def test_optional_process_request_has_no_planned_action_or_end_state(self):
        from cores import workbench_pilot_continuity as continuity
        _, config = fixture()
        plan = story_plan()
        plan['beats'][0]['ending'] = '尚未发生的秘密胜利'
        samples = [dict(path='one.png', time_seconds=0), dict(path='three.png', time_seconds=14.8)]
        _, payload, schema = continuity.progress_observation_request(config, plan, samples)
        self.assertNotIn('尚未发生的秘密胜利', json.dumps(payload, ensure_ascii=False))
        self.assertEqual(payload['image_role_bindings'][1]['time_seconds'], 14.8)
        self.assertEqual(schema['properties']['completed_events']['items']['properties']['evidence_image']['maximum'], 2)
        value = dict(completed_events=[dict(entity_id='guardian', event='魔像已破碎', evidence_image=2,
                                           evidence_description='可见金属残骸')], unknowns=[])
        continuity.validate_progress_observation(value, payload)
        value['completed_events'][0]['entity_id'] = 'invented_enemy'
        with self.assertRaises(ValueError): continuity.validate_progress_observation(value, payload)

    def test_core_request_order_accepts_transport_once_and_enforces_final_canonical_schema(self):
        from cores import story_video_core as core, story_video_prompts as prompts
        from models import story_video_client as client
        folder, config = fixture()
        system, payload, canonical = prompts.plan_request('既定小说正文', config['style'], 5, 15)
        raw = story_plan()
        raw['beats'][0]['action'] = [dict(phase='0～5秒', description='雷恩持剑后撤。'),
                                     dict(phase='5～15秒', description='艾莉娅抬手，光幕消退。')]
        raw['beats'][0]['cast'] += ['guardian']
        before = copy.deepcopy(raw)
        with self.assertRaises(ValueError): core.validate_schema(raw, canonical)
        transport = pilot.plan_transport_schema(canonical)
        observed = []
        def validate(candidate, request_payload):
            normalized, _ = pilot.normalize_plan_entities(candidate, config['cards'], {})
            core.validate_schema(normalized, canonical)
            observed.append(normalized)
        with patch.object(client, 'text', return_value=json.dumps(raw, ensure_ascii=False)) as model:
            accepted = core.request_json((system, payload, transport), folder / 'real_request_order', validator=validate)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(accepted, before)
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]['beats'][0]['cast'], ['hero', 'mage', 'guardian'])
        self.assertIsInstance(observed[0]['beats'][0]['action'], str)
        self.assertEqual(observed[0]['beats'][0]['dialogue'], before['beats'][0]['dialogue'])
        malformed = copy.deepcopy(raw)
        malformed['beats'][0]['action'][0]['invented_field'] = '不是受支持的无损容器'
        with self.assertRaises(ValueError): core.validate_schema(malformed, transport)
        self.assertEqual(canonical['properties']['beats']['items']['properties']['action']['type'], 'string')
        self.assertTrue(canonical['properties']['beats']['items']['properties']['cast']['uniqueItems'])

    def test_single_episode_repack_keeps_five_scenes_in_real_uw_first_episode(self):
        from cores import universal_writer as uw, workbench_pilot_continuity as continuity
        settings = dict(one_line='两位主角完成一部五段短片。', prose_words='450～600')
        rows = [dict(no=index, one_line='第%d场原始情节。' % index,
            events=['第%d场原始事件一。' % index, '第%d场原始事件二。' % index],
            start='第%d场开头。' % index, landing='第%d场落点。' % index,
            goal='第%d场目标。' % index, cast='沈砚、姜璃', pace='紧凑',
            purpose='第%d场作用。' % index, scenes=['第%d场场景。' % index]) for index in range(1, 6)]
        rows = uw.normalize_rows(rows)
        synopsis = '\n\n'.join(row['one_line'] for row in rows)
        original = dict(n=5, rows=rows, draft=synopsis, people=[dict(name='沈砚'), dict(name='姜璃')],
            problems=[], hard=[], design=dict(schema=1, synopsis=synopsis,
                people=[dict(name='沈砚'), dict(name='姜璃')],
                source_sig=uw.signature(uw.source(settings['one_line'], settings)), rows_sig=uw.signature(rows)))
        before = copy.deepcopy(original)
        adapted = continuity.single_episode_author_plan(original, 5)
        uw.save_design(settings, adapted['rows'], adapted['design'])
        self.assertTrue(uw.active_design(settings))
        self.assertEqual(uw.active_design(settings)['synopsis'], synopsis)
        data = uw.writing_input(settings, ep=1, previous=[])
        self.assertTrue(data['这是最后一话'])
        self.assertNotIn('后话内容（本话结束时这些还没发生，留给后话写）', data)
        self.assertEqual(data['本话安排']['events'], [event for row in rows for event in row['events']])
        for row in rows:
            self.assertIn(row['one_line'], data['本话安排']['one_line'])
        self.assertEqual(adapted['author_scene_rows'], rows)
        self.assertEqual(adapted['draft'], synopsis)
        self.assertEqual(adapted['people'], original['people'])
        self.assertEqual(original, before)
        self.assertEqual(continuity.single_episode_author_plan(original, 4), original)

    def test_real_saved_director_reply_recovers_six_shots_without_changing_actions_or_speech(self):
        from cores import workbench_pilot_continuity as continuity
        directory = ROOT / 'outputs/story_video/xianxia_continuity_5clips_20261021/segments/01/writing'
        response = directory / 'call_2_0c1ff7/text_1792594891854882000_0ab72173/response.json'
        if not response.exists():
            self.skipTest('本次实跑原始证据未安装；不生成替代模型回复')
        raw = json.loads(pilot._read(response)['text'])
        payload = pilot._read(directory / 'binding.json')['payload']
        original = copy.deepcopy(raw)
        response_sha = pilot._sha(response)
        normalized, audit = continuity.normalize_director_transport(raw, payload)
        self.assertEqual(normalized['timeline'][:4], raw['timeline'])
        self.assertEqual([(row['start'], row['end']) for row in normalized['timeline'][4:]], [(10, 12.5), (12.5, 15)])
        self.assertEqual(normalized['timeline'][-1]['visible_result'], raw['expected_end_state'])
        self.assertEqual(normalized['dialogue'], raw['dialogue'])
        self.assertEqual(normalized['overall_soundscape'], raw['overall_soundscape'])
        self.assertEqual(normalized['integrated_multimodal_description'].count('[Shot '), 6)
        self.assertEqual(normalized['integrated_multimodal_description'].count('全景。金色斜阳切开云海'), 1)
        self.assertIn('[Shot 6] At 00:12.500,', normalized['integrated_multimodal_description'])
        self.assertEqual([row['action'] for row in normalized['timeline'][:4]], [row['action'] for row in raw['timeline']])
        compiled = continuity.compile_director_reply(raw, payload)
        self.assertEqual(compiled['prompt'].count('[Shot '), 6)
        self.assertEqual(compiled['prompt'].count('<d>'), 2)
        saved_raw, saved_compiled, evidence = pilot.recover_director_transport(directory, payload)
        self.assertEqual(saved_raw, raw)
        self.assertEqual(saved_compiled['prompt'], compiled['prompt'])
        self.assertEqual(evidence['source_response'], str(response))
        changed_payload = copy.deepcopy(payload)
        changed_payload['actual_state'] = dict(actual_visible='不同的实际末帧')
        self.assertEqual(pilot.recover_director_transport(directory, changed_payload), (None, None, None))
        self.assertTrue(any(row['kind'] == 'exact_duplicate_preface_shot_removed' for row in audit))
        self.assertEqual(raw, original)
        self.assertEqual(pilot._sha(response), response_sha)

    def test_freeze_recovery_preserves_images_and_rejects_changed_source(self):
        folder, config = fixture()
        manifest = {}
        frozen = pilot._freeze_config(config, folder, manifest)
        self.assertEqual(len(manifest['reference_assets']), 4)
        self.assertTrue(all(str(folder / 'references') in card['image_path'] for card in frozen['cards']))
        self.assertEqual(pilot._freeze_config(config, folder, manifest), frozen)
        Path(config['cards'][0]['image_path']).write_bytes(b'changed reference')
        with self.assertRaises(ValueError):
            pilot._freeze_config(config, folder, manifest)

    def test_submission_budget_blocks_second_post_and_preserves_receipt(self):
        from models import h3_client
        folder, _ = fixture()
        class Session:
            manifest = dict(reference_policy=dict(max_h3_submissions=5))
            def guard(self): pass
            def checkpoint(self): pass
        session = Session()
        with patch.object(h3_client, '_http', return_value='queued') as http:
            with pilot.single_submission(session, folder, 1):
                self.assertEqual(h3_client._http('POST', '/prompt'), 'queued')
                self.assertEqual(h3_client._http('GET', '/history/id'), 'queued')
                with self.assertRaises(RuntimeError): h3_client._http('POST', '/prompt')
            self.assertEqual(http.call_count, 2)
            with pilot.single_submission(session, folder, 1):
                self.assertEqual(h3_client._http('GET', '/history/id'), 'queued')
                with self.assertRaises(RuntimeError): h3_client._http('POST', '/prompt')
        self.assertEqual(session.manifest['actual_h3_submissions'], 1)
        self.assertEqual(pilot._read(folder / 'video_submission.json')['submissions'], 1)

    def test_mock_five_clip_pipeline_uses_local_story_observation_and_main_renderer(self):
        from cores import store, story_video_reference as reference, story_video_media as media
        from cores import story_video_reference_prompts as prompts
        from models import gpu_manager, story_video_client as client, h3_client
        from api import segment_api as segment
        folder, config = fixture()
        (folder / 'config.json').write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
        observations, renders, writing_states, writing_contexts = [], [], [], []
        class Session(reference._Session):
            def guard(self): pass
            def models(self): return nullcontext()
            def ask(self, request, directory, images=None, validator=None):
                if 'count' in request[1]:
                    result = story_plan()
                else:
                    writing_states.append(copy.deepcopy(request[1]['actual_state']))
                    writing_contexts.append(copy.deepcopy(request[1]))
                    result = director_reply(request[1])
                if validator: validator(result, request[1])
                return result
            def review(self, request, directory, images=None, validator=None):
                observations.append(list(images))
                return dict(actual_visible='实际观察%d' % len(observations), characters=[], completed=[], actions=[])
        def render(sid, ep, sc, index, **kwargs):
            from cores import saga_core
            timeline = saga_core.ep_timeline(sid, ep)
            row = pilot._row(timeline, index)[1]
            actual = segment._render_context(sid, ep, sc, index, size_tier='0.7',
                override=kwargs['override'], fresh=True)
            expected_paths = [ref['path'] for ref in pilot.references_for_index(pilot._read(folder / 'effective_config.json'), index)]
            self.assertEqual(actual[6], expected_paths)
            renders.append(dict(index=index, scene=sc, refs=row['refs'], kwargs=kwargs))
            h3_client._http('POST', '/prompt')
            clip = folder / 'segments' / ('%02d' % index) / 'mock_main.mp4'
            clip.write_bytes(b'not a real video')
            row.update(video=str(clip), used_refs=row['refs'], render_request=dict(status='completed', key='fake_%d' % index))
            saga_core.save_ep_timeline(sid, ep, timeline)
            return dict(video=str(clip))
        def adopt(path, directory):
            return dict(clip_path=path, clip_sha256=pilot._sha(path), anchor_path='retained_anchor.png')
        original_data = store.DATA
        from cores import saga_core
        original_path = saga_core.seg_video_path
        try:
            with patch.object(reference, '_Session', Session), patch.object(reference, '_story', return_value='雷恩说：“一起上。”\n两位主角打败魔像。'), \
                 patch.object(prompts, 'sanitize_reference_observation', side_effect=lambda material, _: material), \
                 patch.object(segment, '_render_critical_problems', return_value=[]), patch.object(segment, 'is_segment_current', return_value=False), \
                 patch.object(segment, 'render_segment', side_effect=render), patch.object(gpu_manager, 'acquire'), \
                 patch.object(client, '_native_profile', side_effect=lambda *_: nullcontext()), patch.object(client, 'preserve_release'), \
                 patch.object(media, 'adopt', side_effect=adopt), \
                 patch.object(media, 'sample_at_times', return_value=[dict(path='sample.png')]), \
                 patch.object(media, 'join', return_value=dict(path='joined_mock.mp4')), \
                 patch.object(h3_client, '_http', return_value='queued'), \
                 patch('cores.story_video_offline.install'):
                pilot.run(folder)
        finally:
            store.DATA = original_data
            saga_core.seg_video_path = original_path
        self.assertEqual(len(observations[0]), 4)
        self.assertEqual(len(observations), 6)
        self.assertEqual([row['scene'] for row in renders], [1, 1, 2, 2, 2])
        self.assertEqual([len(row['refs']) for row in renders], [3, 2, 3, 2, 2])
        self.assertEqual([row['kwargs']['override']['seed'] for row in renders], [21, 22, 23, 24, 25])
        self.assertTrue(all(row['kwargs']['override']['steps'] == 10 for row in renders))
        self.assertEqual([state['actual_visible'] for state in writing_states], ['实际观察%d' % index for index in range(1, 6)])
        self.assertEqual([len(row['story_progress']['consumed_beats']) for row in writing_contexts], [0, 1, 2, 3, 4])
        self.assertEqual([row['stable_visual_contract']['scene']['id'] for row in writing_contexts], ['A', 'A', 'B', 'B', 'B'])
        self.assertTrue(all(not row['story_progress']['observed_completed_events'] for row in writing_contexts))
        self.assertEqual(len(pilot._read(folder / 'progress_ledger.json')['consumed_beats']), 5)
        self.assertEqual(pilot._read(folder / 'manifest.json')['status'], 'completed')
        self.assertEqual(pilot._read(folder / 'manifest.json')['actual_h3_submissions'], 5)


if __name__ == '__main__':
    os.environ['V41_NO_MODEL'] = '1'
    unittest.main()
