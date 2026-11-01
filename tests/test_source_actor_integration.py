"""Actor staging through the real source compiler and six-field adapter.

All replies are local Python dictionaries. No model, service, media generation,
production project writes or fixture deletion is involved.
"""
import copy
import json
import os
import unittest

os.environ['V41_NO_MODEL'] = '1'

from cores import episode_contract, workbench_story_director as writer
from tests import test_source_shot_compiler as source_fixture
from tests import test_workbench_story_director_release as release_fixture


def acting(unit_id, actor='阿岚', **overrides):
    result = dict(unit_id=unit_id, actor=actor,
                  preparation='目光短暂停顿，眉间轻收。',
                  execution='指尖轻轻收拢，呼吸稍缓。',
                  reaction='眉间逐渐舒展，嘴角稍稍放松。')
    result.update(overrides)
    return result


class SourceActorIntegrationTests(unittest.TestCase):
    def write(self, source='阿岚扶稳茶杯。', speech=(), cast=None,
              planner=None, repair=None):
        slices, cards, settings = release_fixture.fixture()
        settings.pop('video_action_compiler')
        cast = ['阿岚', '顾亭'] if cast is None else cast
        cards = [card for card in cards if card['name'] in cast]
        text = source
        for index, (speaker, words) in enumerate(speech, 1):
            text += '\n【D%d】%s：%s' % (index, speaker, words)
        original = copy.deepcopy(slices[0])
        original.update(seconds=12, text=text, chars=list(cast))
        original['director'].update(cast=list(cast),
            blocks=[[0, 12, text, list(range(1, len(speech) + 1))]],
            says=[list(line) for line in speech])
        original['director']['start_state'] = ''
        settings['_director_reference_paths'] = {name: __file__ for name in cast + ['竹庭']}
        calls = []
        initial = []

        def q(system, user, **options):
            payload = json.loads(user)
            calls.append((copy.deepcopy(payload), copy.deepcopy(options)))
            if 'field_values' in payload:
                if repair:
                    value = repair(payload, options, initial[0])
                else:
                    value = {key: ([] if key.endswith('.acting') else '焦点清晰，背景柔和。')
                             for key in payload['field_values']}
            else:
                value = source_fixture.model_reply(payload)
                for shot in value['shots']:
                    shot['acting'] = []
                if planner:
                    planner(value, payload)
                initial.append(copy.deepcopy(value))
            return json.dumps(value, ensure_ascii=False)

        row = writer.make_prompts([original], cards, ['竹庭'], q,
                                  settings=settings)[0]
        self.assertEqual(row['source_text'], text)
        self.assertEqual(original['director']['blocks'],
                         [[0, 12, text, list(range(1, len(speech) + 1))]])
        self.assertEqual(original['director']['says'], [list(line) for line in speech])
        return row, calls, initial[0]

    def body(self, row):
        return row['prompt'].split('detailed_description:\n', 1)[1].split(
            '\n\noverall_soundscape:', 1)[0]

    def test_two_named_actors_keep_individual_staging_and_exact_story_words(self):
        source = '阿岚扶稳茶杯。顾亭看向木桌。'
        speech = [('阿岚', '慢一点。'), ('顾亭', '我看清了。')]

        def plan(value, payload):
            for shot, unit in zip(value['shots'], payload['source_units']):
                if unit['kind'] != 'action':
                    continue
                actor = '阿岚' if unit['text'].startswith('阿岚') else '顾亭'
                shot['acting'] = [acting(unit['id'], actor)]

        row, calls, _ = self.write(source, speech, planner=plan)
        self.assertEqual(len(calls), 1)
        self.assertFalse(row['source_fallback'])
        body = self.body(row)
        for subject in (1, 2):
            self.assertIn('<Subject %d>目光短暂停顿，眉间轻收。' % subject, body)
            self.assertIn('<Subject %d>指尖轻轻收拢，呼吸稍缓。' % subject, body)
        self.assertEqual(body.count('扶稳茶杯。'), 1)
        self.assertEqual(body.count('看向木桌。'), 1)
        for _, words in speech:
            self.assertEqual(body.count(words), 1)
        self.assertIn('(S1)', body)
        self.assertIn('(S2)', body)
        audit = row['director_compilation']['source_shot_audit']
        self.assertEqual([item['actor'] for shot in audit['shots'] for item in shot['acting']],
                         ['阿岚', '顾亭'])

    def test_legacy_state_survives_single_actor_shot_in_a_two_reference_segment(self):
        def plan(value, payload):
            value['shots'][0]['visual']['performance'] = '神情专注。'

        row, calls, _ = self.write(planner=plan)
        self.assertEqual(len(calls), 1)
        self.assertIn('<Subject 1>神情专注。', self.body(row))
        self.assertIn('<Subject 2>', row['prompt'])
        self.assertNotIn('<Subject 2>神情专注。', self.body(row))

    def test_empty_scene_rejects_invented_actor_and_keeps_its_source_once(self):
        def plan(value, payload):
            value['shots'][0]['visual']['performance'] = '神情专注。'
            value['shots'][0]['acting'] = [acting(payload['source_units'][0]['id'])]

        row, calls, _ = self.write('薄雾沿溪水移动。', cast=[], planner=plan)
        self.assertEqual(len(calls), 2)
        self.assertFalse(row['source_fallback'])
        self.assertNotIn('character from', row['prompt'])
        self.assertNotIn('神情专注', self.body(row))
        self.assertNotIn('眉间轻收', self.body(row))
        self.assertEqual(self.body(row).count('薄雾沿溪水移动。'), 1)

    def test_camera_follows_same_actor_existing_rise_without_repeating_story_sentence(self):
        camera = '镜头随阿岚起身缓慢上摇，保持上半身构图，最后稳定在侧脸近景。'

        def plan(value, payload):
            value['shots'][0]['movement'] = '向上摇镜'
            value['shots'][0]['visual']['camera_detail'] = camera

        row, calls, _ = self.write('阿岚起身，顾亭站在木桌旁。', planner=plan)
        self.assertEqual(len(calls), 1)
        self.assertFalse(row['source_fallback'])
        self.assertIn(camera.replace('阿岚', '<Subject 1>'), self.body(row))
        self.assertIn('向上摇镜', self.body(row))
        self.assertEqual(self.body(row).count('<Subject 1>起身，<Subject 2>站在木桌旁。'), 1)

    def test_camera_cannot_create_an_action_change_its_actor_or_change_its_object(self):
        cases = [
            ('阿岚起身，顾亭站在木桌旁。', '镜头随阿岚蹲下缓慢下摇，保持上半身构图。'),
            ('阿岚起身，顾亭站在木桌旁。', '镜头随顾亭起身缓慢上摇，保持上半身构图。'),
            ('阿岚拉开木门。', '镜头随着阿岚拉开窗帘缓慢横移，保持中景构图。'),
        ]
        for source, camera in cases:
            with self.subTest(camera=camera):
                def plan(value, payload):
                    value['shots'][0]['visual']['camera_detail'] = camera

                row, calls, _ = self.write(source, planner=plan)
                self.assertEqual(len(calls), 2)
                self.assertFalse(row['source_fallback'])
                self.assertNotIn(camera.replace('阿岚', '<Subject 1>').replace('顾亭', '<Subject 2>'),
                                 self.body(row))
                self.assertEqual(set(calls[1][0]['field_values']), {'shots.0.visual.camera_detail'})

    def test_camera_cannot_repeat_the_source_handover_with_a_transfer_synonym(self):
        source = '阿岚递出怀表，顾亭双手接过。'
        camera = '镜头固定，聚焦于双手交接处，阿岚递出怀表，顾亭手指合拢托稳，焦点保持清晰。'

        def plan(value, payload):
            value['shots'][0]['visual']['camera_detail'] = camera

        row, calls, _ = self.write(source, planner=plan)
        self.assertEqual(len(calls), 2)
        self.assertFalse(row['source_fallback'])
        self.assertEqual(set(calls[1][0]['field_values']), {'shots.0.visual.camera_detail'})
        self.assertIn('递出', calls[1][0]['field_errors'])
        self.assertEqual(self.body(row).count('递出怀表'), 1)
        self.assertNotIn('手指合拢托稳', self.body(row))

    def test_noncontiguous_units_across_dialogue_report_order_without_reordering_source(self):
        slices, cards, settings = release_fixture.fixture()
        settings.pop('video_action_compiler')
        text = ('阿岚停在工作台边。顾亭坐在木椅上。阿岚抬眼。阿岚递出怀表。\n'
                '【D1】顾亭：交给我吧。\n顾亭接过怀表。')
        original = copy.deepcopy(slices[0])
        original.update(seconds=12, text=text)
        blocks = [[0, 12, text, [1]]]
        original['director'].update(blocks=copy.deepcopy(blocks),
                                    says=[['顾亭', '交给我吧。']], start_state='')
        calls = []

        def q(system, user, **options):
            payload = json.loads(user)
            calls.append(copy.deepcopy(payload))
            self.assertEqual([unit['id'] for unit in payload['source_units']],
                             ['U001', 'U002', 'U003', 'U004', 'U005', 'U006'])
            self.assertEqual(payload['source_units'][4]['kind'], 'dialogue')
            plan = source_fixture.model_reply(payload)
            # A similar handover after the spoken line cannot be pulled before it.
            merged = dict(plan['shots'][3], unit_ids=['U004', 'U006'])
            plan['shots'] = plan['shots'][:3] + [merged, plan['shots'][4]]
            return json.dumps(plan, ensure_ascii=False)

        row = writer.make_prompts([original], cards, ['竹庭'], q,
                                  settings=settings)[0]
        self.assertEqual(len(calls), 2)
        self.assertIn('正确顺序：U001→U002→U003→U004→U005→U006', calls[1]['format_error'])
        self.assertIn('当前顺序：U001→U002→U003→U004→U006→U005', calls[1]['format_error'])
        self.assertTrue(row['source_fallback'])
        audit = row['director_compilation']['source_shot_audit']
        self.assertEqual([unit for shot in audit['shots'] for unit in shot['unit_ids']],
                         ['U001', 'U002', 'U003', 'U004', 'U005', 'U006'])
        self.assertEqual(original['text'], text)
        self.assertEqual(original['director']['blocks'], blocks)
        self.assertEqual(row['source_text'], text)
        self.assertEqual(self.body(row).count('递出怀表。'), 1)
        self.assertEqual(self.body(row).count('接过怀表。'), 1)
        self.assertEqual(self.body(row).count('交给我吧。'), 1)

    def test_doorway_light_word_boundaries_do_not_become_actor_departures(self):
        descriptions = [
            '门框边缘勾勒出门外透入的冷光。',
            '窗边轮廓突出门外的清冷光线。',
            '窄窗透出门外的冷光。',
        ]
        for lighting in descriptions:
            with self.subTest(lighting=lighting):
                def plan(value, payload):
                    value['shots'][0]['visual']['lighting'] = lighting

                row, calls, _ = self.write(planner=plan)
                self.assertEqual(len(calls), 1)
                self.assertFalse(row['source_fallback'])
                self.assertIn(lighting, self.body(row))

    def test_real_exit_after_a_light_description_is_still_unsupported(self):
        for light in ('门框边缘勾勒出门外的冷光', '轮廓突出门外的冷光', '窄窗透出门外的冷光'):
            with self.subTest(light=light):
                # Keep both matches in one clause: skipping the optical word
                # boundary must not skip a later real departure in that clause.
                model = light + '随后他出门。'
                self.assertTrue(episode_contract.unsupported_departure(model, '他仍在木桌旁。'))
                # Source-only optical wording cannot authorize any actor exit.
                self.assertTrue(episode_contract.unsupported_departure('他出门。', light + '。'))

    def test_hand_force_and_elbow_micro_adjustment_reach_final_actor_text(self):
        micro = '手部力度平稳，肘弯轻轻调整。'

        def plan(value, payload):
            value['shots'][0]['acting'] = [acting(payload['source_units'][0]['id'],
                preparation='', execution=micro, reaction='')]

        row, calls, _ = self.write(planner=plan)
        self.assertEqual(len(calls), 1)
        self.assertFalse(row['source_fallback'])
        self.assertIn('<Subject 1>' + micro, self.body(row))

    def grouped_plan(self, value, payload):
        first = value['shots'][0]
        first.update(unit_ids=[unit['id'] for unit in payload['source_units'][:2]],
                     framing='近景', movement='向上摇镜', weight=7)
        first['visual']['camera_detail'] = '镜头随阿岚起身缓慢上摇，保持上半身构图。'
        first['acting'] = [acting(first['unit_ids'][0],
                                  execution='指尖轻轻收拢，打开木门。')]
        value['shots'] = [first] + value['shots'][2:]

    def assert_group_and_dialogue(self, row, original):
        audit = row['director_compilation']['source_shot_audit']
        self.assertEqual([shot['unit_ids'] for shot in audit['shots']],
                         [shot['unit_ids'] for shot in original['shots']])
        self.assertIn('近景，平视，向上摇镜', self.body(row))
        self.assertIn('镜头随<Subject 1>起身缓慢上摇，保持上半身构图。', self.body(row))
        for words in ('先等等。', '我在这里。'):
            self.assertEqual(self.body(row).count(words), 1)
        self.assertNotIn('打开木门', self.body(row))
        self.assertFalse(row['source_fallback'])

    def test_invalid_acting_uses_array_only_delta_and_preserves_group_and_dialogue(self):
        def repair(payload, options, original):
            self.assertEqual(payload['repair_fields'], [['shots', 0, 'acting']])
            schema = options['response_format']['schema']
            self.assertEqual(schema['required'], ['shots.0.acting'])
            self.assertEqual(schema['properties']['shots.0.acting']['type'], 'array')
            return {'shots.0.acting': [acting(original['shots'][0]['unit_ids'][0])]}

        row, calls, original = self.write('阿岚起身。顾亭停在木桌旁。',
            [('阿岚', '先等等。'), ('顾亭', '我在这里。')],
            planner=self.grouped_plan, repair=repair)
        self.assertEqual(len(calls), 2)
        self.assert_group_and_dialogue(row, original)
        self.assertIn('<Subject 1>指尖轻轻收拢，呼吸稍缓。', self.body(row))

    def test_full_plan_compatibility_repair_cannot_replace_other_camera_fields(self):
        def repair(payload, options, original):
            value = copy.deepcopy(original)
            value['style_description'] = '未经请求的全新影像风格。'
            value['shots'][0].update(unit_ids=['U999'], framing='远景', weight=1,
                                     movement='固定机位')
            value['shots'][0]['visual']['camera_detail'] = '未经请求的摄影变化。'
            value['shots'][0]['acting'] = [acting(original['shots'][0]['unit_ids'][0])]
            return value

        row, calls, original = self.write('阿岚起身。顾亭停在木桌旁。',
            [('阿岚', '先等等。'), ('顾亭', '我在这里。')],
            planner=self.grouped_plan, repair=repair)
        self.assertEqual(len(calls), 2)
        self.assert_group_and_dialogue(row, original)
        self.assertNotIn('未经请求', row['prompt'])

    def test_persistently_invalid_acting_phase_is_omitted_without_losing_camera(self):
        def repair(payload, options, original):
            return {'shots.0.acting': copy.deepcopy(original['shots'][0]['acting'])}

        row, calls, original = self.write('阿岚起身。顾亭停在木桌旁。',
            [('阿岚', '先等等。'), ('顾亭', '我在这里。')],
            planner=self.grouped_plan, repair=repair)
        self.assertEqual(len(calls), 2)
        self.assert_group_and_dialogue(row, original)
        audit = row['director_compilation']['source_shot_audit']
        kept = audit['shots'][0]['acting']
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]['actor'], '阿岚')
        self.assertEqual(kept[0]['execution'], '')
        self.assertEqual(kept[0]['preparation'], '目光短暂停顿，眉间轻收。')
        self.assertEqual(kept[0]['reaction'], '眉间逐渐舒展，嘴角稍稍放松。')
        self.assertTrue(audit['photography_review_required'])
        self.assertEqual(audit['omitted_visual_fields'], [['shots', 0, 'acting']])
        self.assertTrue(any('需复核' in text for text in row['problems']))

    def test_failed_repair_keeps_good_phases_and_other_actors_valid_staging(self):
        good_micro = '手部力度平稳，肘弯略微调整。'

        def plan(value, payload):
            self.grouped_plan(value, payload)
            first = value['shots'][0]
            first['acting'].append(acting(first['unit_ids'][1], '顾亭',
                preparation='眉头轻蹙。', execution=good_micro, reaction='呼吸逐渐放缓。'))

        def repair(payload, options, original):
            return {'shots.0.acting': copy.deepcopy(original['shots'][0]['acting'])}

        row, calls, original = self.write('阿岚起身。顾亭停在木桌旁。',
            [('阿岚', '先等等。'), ('顾亭', '我在这里。')], planner=plan, repair=repair)
        self.assertEqual(len(calls), 2)
        self.assert_group_and_dialogue(row, original)
        audit = row['director_compilation']['source_shot_audit']
        kept = audit['shots'][0]['acting']
        self.assertEqual([entry['actor'] for entry in kept], ['阿岚', '顾亭'])
        self.assertEqual(kept[0]['execution'], '')
        self.assertTrue(kept[0]['preparation'])
        self.assertTrue(kept[0]['reaction'])
        self.assertEqual(kept[1]['unit_id'], 'U002')
        self.assertIn('<Subject 2>' + good_micro, self.body(row))
        self.assertIn('<Subject 1>目光短暂停顿，眉间轻收。', self.body(row))
        self.assertTrue(audit['photography_review_required'])
        self.assertEqual(audit['omitted_visual_fields'], [['shots', 0, 'acting']])

    def test_failed_entry_repair_keeps_good_phases_without_repeating_door_operation(self):
        before = '眉头轻蹙，呼吸放缓。'
        after = '眼睑轻轻收紧，呼吸逐渐平稳。'

        def plan(value, payload):
            value['shots'][0]['acting'] = [acting(payload['source_units'][0]['id'],
                preparation=before, execution='左手压住门把。', reaction=after)]

        def persistent(payload, options, original):
            return {'shots.0.acting': copy.deepcopy(original['shots'][0]['acting'])}

        row, calls, _ = self.write('阿岚左手压住门把，拉开木门。', planner=plan, repair=persistent)
        self.assertEqual(len(calls), 2)
        self.assertFalse(row['source_fallback'])
        audit = row['director_compilation']['source_shot_audit']
        kept = audit['shots'][0]['acting']
        self.assertEqual(len(kept), 1)
        self.assertEqual((kept[0]['preparation'], kept[0]['execution'], kept[0]['reaction']),
                         (before, '', after))
        self.assertIn('<Subject 1>' + before, self.body(row))
        self.assertIn('<Subject 1>' + after, self.body(row))
        self.assertEqual(self.body(row).count('压住门把'), 1)
        self.assertEqual(self.body(row).count('拉开木门'), 1)
        self.assertTrue(audit['photography_review_required'])

        def split_operation(value, payload):
            value['shots'][0]['acting'] = [acting(payload['source_units'][0]['id'],
                preparation='眉头轻蹙，拿', execution='起，指尖轻动。', reaction='')]

        # A forbidden operation split across individually valid-looking phases
        # must still fail the final combined validation after partial salvage.
        split_row, split_calls, _ = self.write(planner=split_operation, repair=persistent)
        self.assertEqual(len(split_calls), 2)
        self.assertEqual(split_row['director_compilation']['source_shot_audit']['shots'][0]['acting'], [])
        self.assertNotIn('拿起', self.body(split_row))

    def test_staging_rejects_object_and_gaze_target_changes_but_keeps_local_eye_motion(self):
        forbidden = [
            '手指轻收，压住门把。',
            '指尖收拢，托稳怀表。',
            '肩部轻松，递到同事面前。',
            '目光从门口移到桌面。',
            '目光转向门口。',
            '收回目光，眼睑轻轻收紧。',
        ]
        for micro in forbidden:
            with self.subTest(micro=micro):
                def plan(value, payload):
                    value['shots'][0]['acting'] = [acting(payload['source_units'][0]['id'],
                        preparation='', execution=micro, reaction='')]

                row, calls, _ = self.write(planner=plan)
                self.assertEqual(len(calls), 2)
                self.assertFalse(row['source_fallback'])
                self.assertEqual(set(calls[1][0]['field_values']), {'shots.0.acting'})
                self.assertNotIn(micro, self.body(row))
                self.assertEqual(self.body(row).count('扶稳茶杯。'), 1)

        local = '眼神短暂凝住，眼睑略微收紧，眉头轻蹙。'

        def local_eye(value, payload):
            value['shots'][0]['acting'] = [acting(payload['source_units'][0]['id'],
                preparation='', execution=local, reaction='')]

        row, calls, _ = self.write(planner=local_eye)
        self.assertEqual(len(calls), 1)
        self.assertIn('<Subject 1>' + local, self.body(row))

    def test_fixed_camera_repairs_camera_travel_but_preserves_rack_focus(self):
        travel = '镜头推近至人物侧脸，画面保持清晰。'

        def moving(value, payload):
            value['shots'][0]['visual']['camera_detail'] = travel

        row, calls, _ = self.write('阿岚检查怀表。', planner=moving)
        self.assertEqual(len(calls), 2)
        self.assertFalse(row['source_fallback'])
        self.assertEqual(set(calls[1][0]['field_values']), {'shots.0.visual.camera_detail'})
        self.assertIn('固定机位', calls[1][0]['field_errors'])
        self.assertNotIn(travel, self.body(row))

        focus = '镜头固定，焦点从脸部转向怀表，背景保持柔和。'

        def rack_focus(value, payload):
            value['shots'][0]['visual']['camera_detail'] = focus

        row, calls, _ = self.write('阿岚检查怀表。', planner=rack_focus)
        self.assertEqual(len(calls), 1)
        self.assertFalse(row['source_fallback'])
        self.assertIn(focus, self.body(row))
        self.assertIn('固定机位', self.body(row))


    def test_external_cue_precedes_reactive_preparation(self):
        def plan(value, payload):
            value['shots'][0]['acting'] = [acting(payload['source_units'][0]['id'],
                preparation='眉头轻蹙，呼吸短暂停顿。', execution='', reaction='')]
        row, _, _ = self.write('店门外响起敲门声，阿岚抬眼看向门口。', planner=plan)
        body = self.body(row)
        self.assertLess(body.index('响起敲门声'), body.index('眉头轻蹙'))
        self.assertEqual(body.count('响起敲门声'), 1)

    def test_ordinary_action_keeps_its_preparation_before_the_action(self):
        def plan(value, payload):
            value['shots'][0]['acting'] = [acting(payload['source_units'][0]['id'],
                preparation='指腹轻微发力。', execution='', reaction='')]
        row, _, _ = self.write(planner=plan)
        body = self.body(row)
        self.assertLess(body.index('指腹轻微发力'), body.index('扶稳茶杯'))

    def test_moving_camera_removes_only_conflicting_fixed_clause(self):
        def plan(value, payload):
            value['shots'][0]['movement'] = '平稳跟拍'
            value['shots'][0]['visual']['camera_detail'] = '镜头固定，焦点在阿岚面部，背景保留木桌。'
        row, calls, _ = self.write(planner=plan)
        body = self.body(row)
        self.assertEqual(len(calls), 1)
        self.assertIn('平稳跟拍', body)
        self.assertIn('焦点在<Subject 1>面部', body)
        self.assertNotIn('镜头固定', body)

    def test_photography_cannot_repeat_source_hand_contact_or_switch_its_side(self):
        source = '阿岚左手压住门把，侧头看向顾亭。'
        for side in ('左手', '右手'):
            with self.subTest(side=side):
                composition = '阿岚位于画面左侧，' + side + '压住门把，门框在后景。'

                def plan(value, payload):
                    value['shots'][0]['visual']['composition'] = composition

                row, calls, _ = self.write(source, planner=plan)
                self.assertEqual(len(calls), 2)
                self.assertFalse(row['source_fallback'])
                self.assertEqual(set(calls[1][0]['field_values']), {'shots.0.visual.composition'})
                self.assertIn('压住', calls[1][0]['field_errors'])
                body = self.body(row)
                self.assertEqual(body.count('左手压住门把'), 1)
                self.assertNotIn('右手压住门把', body)
                self.assertNotIn(composition.replace('阿岚', '<Subject 1>'), body)

    def test_camera_focus_transfer_and_finger_force_do_not_replay_object_operation(self):
        source = '阿岚左手压住门把，侧头看向顾亭。'
        camera = '镜头将焦点从门框移到阿岚面部，保持中景构图。'
        micro = '指腹轻微发力，手腕短暂停顿。'

        def plan(value, payload):
            value['shots'][0]['visual']['camera_detail'] = camera
            value['shots'][0]['acting'] = [acting(payload['source_units'][0]['id'],
                preparation='', execution=micro, reaction='')]

        row, calls, _ = self.write(source, planner=plan)
        self.assertEqual(len(calls), 1)
        self.assertFalse(row['source_fallback'])
        body = self.body(row)
        self.assertIn(camera.replace('阿岚', '<Subject 1>'), body)
        self.assertIn('<Subject 1>' + micro, body)
        self.assertEqual(body.count('左手压住门把'), 1)


if __name__ == '__main__':
    unittest.main()
