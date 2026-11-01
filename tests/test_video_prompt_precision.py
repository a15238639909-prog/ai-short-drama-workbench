"""Final video text regressions: source actions own every story transition.

All planners are local Python fakes. This suite never calls an AI/media model.
"""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from cores import source_shot_compiler as compiler
from cores import wardrobe_continuity as wardrobe
from cores import workbench_story_director as writer
from tests.test_source_shot_compiler import model_reply
from tests.test_workbench_story_director_release import fixture


class VideoPromptPrecisionTests(unittest.TestCase):
    SOURCE = ('阿岚站在红色出租车的右后门旁，车门关着。'
              '阿岚抬起右手，握住车门把手。'
              '阿岚将右后车门拉开，扶住门边看向车内。')

    def write(self, source=None, speech=None, corrupt=None, always_bad=False, cast=None, repair_noise=False, delta_reply=None, traces=None, clothing=None, current_wardrobe=None):
        slices, cards, settings = fixture()
        settings.pop('video_action_compiler')
        settings['style'] = '写实电影摄影，冷暖光色，细微胶片颗粒'
        cast = ['阿岚'] if cast is None else cast
        cards = [card for card in cards if card['name'] in cast]
        if clothing is not None or current_wardrobe is not None:
            settings['wardrobe_policy'] = wardrobe.POLICY
            for card in cards:
                card['clothing'] = (clothing or {}).get(card['name'], '')
        source = self.SOURCE if source is None else source
        speech = [] if speech is None else speech
        text = source
        for index, (speaker, words) in enumerate(speech, 1):
            text += '\n【D%d】%s：%s' % (index, speaker, words)
        row = copy.deepcopy(slices[0])
        row.update(seconds=12, scene_hint='旧街', text=text, chars=list(cast))
        row['director'].update(cast=list(cast), blocks=[[0, 12, text, list(range(1, len(speech) + 1))]], says=speech)
        settings['_director_reference_paths'] = {name: __file__ for name in cast + ['旧街']}
        settings['_director_scene_facts'] = {'旧街': {'space': '湿石板路面与路边红色出租车', 'light': '冷色路灯与暖色店铺窗光'}}
        calls = []

        def q(system, user, **options):
            request = json.loads(user)
            calls.append(request)
            plan = model_reply(request)
            if corrupt and (always_bad or len(calls) == 1):
                corrupt(plan, request)
            if repair_noise and len(calls) > 1:
                plan['style_description'] = '未经请求的全新影像风格。'
                plan['shots'][0].update(framing='远景', weight=10)
                plan['shots'][0]['visual']['lighting'] = '未经请求的整片红色灯光。'
            if delta_reply is not None and len(calls) > 1:
                self.assertIn('field_values', request)
                schema = options['response_format']['schema']
                self.assertFalse(schema['additionalProperties'])
                self.assertEqual(set(schema['required']), set(request['field_values']))
                self.assertEqual(set(schema['properties']), set(request['field_values']))
                self.assertIn('不重写整份镜头计划', system)
                return json.dumps(delta_reply(request), ensure_ascii=False)
            return json.dumps(plan, ensure_ascii=False)

        writer_options = {}
        if current_wardrobe is not None:
            writer_options['actual_state_for'] = lambda index: {
                'actual_visible': 'unknown', 'wardrobe_state': copy.deepcopy(current_wardrobe)}
        if traces is None:
            result = writer.make_prompts([row], cards, ['旧街'], q, settings=settings, **writer_options)[0]
        else:
            with patch.object(Path, 'mkdir'), patch.object(Path, 'write_text',
                    side_effect=lambda text, **kwargs: traces.append(json.loads(text))):
                result = writer.make_prompts([row], cards, ['旧街'], q, settings=settings,
                    trace_dir='not_created_precision_trace', **writer_options)[0]
        return result, calls

    def assert_repaired(self, clause, *, field='composition', source=None, cast=None):
        def corrupt(plan, request):
            plan['shots'][0]['visual'][field] = clause
        row, calls = self.write(source=source, corrupt=corrupt, cast=cast)
        self.assertEqual(len(calls), 2, clause)
        self.assertFalse(row['source_fallback'], clause)
        self.assertNotIn(clause, row['prompt'])
        return row, calls

    def test_final_text_has_concrete_summary_and_voice_without_meta_sentences(self):
        row, calls = self.write(speech=[['阿岚', '上车吧。']])
        self.assertEqual(len(calls), 1)
        self.assertFalse(row['source_fallback'])
        for unwanted in ('原文行动', '当前分段', '当前语境', '说出原话', '表演沿'):
            self.assertNotIn(unwanted, row['prompt'])
        self.assertIn('旧街', row['prompt'].split('summary:\n')[1].split('\n\n')[0])
        self.assertEqual(row['prompt'].count('上车吧。'), 1)
        self.assertIn('<d>[Chinese] 上车吧。</d>', row['prompt'])

    def test_source_actions_only_appear_once_in_actual_final_prompt(self):
        row, _ = self.write()
        body = row['prompt'].split('detailed_description:\n')[1].split('\n\noverall_soundscape:')[0]
        for action in ('抬起右手', '握住车门把手', '将右后车门拉开', '扶住门边看向车内'):
            self.assertEqual(body.count(action), 1, body)
        audit = row['director_compilation']['source_shot_audit']
        self.assertEqual([unit for shot in audit['shots'] for unit in shot['unit_ids']], [unit['id'] for unit in audit['units']])

    def test_visual_cannot_repeat_a_source_action_even_if_source_has_same_verb(self):
        self.assert_repaired('阿岚抬起右手，握住车门把手。')

    def test_synonym_action_restatement_is_also_rejected(self):
        self.assert_repaired('阿岚伸手抓住车门把手。', field='camera_detail')

    def test_existing_open_verb_does_not_authorize_another_object(self):
        self.assert_repaired('阿岚打开路边店铺的抽屉。')

    def test_existing_action_does_not_authorize_another_actor(self):
        self.assert_repaired('顾亭拉开右后车门。', cast=['阿岚', '顾亭'])

    def test_later_action_cannot_be_inserted_into_first_shot(self):
        self.assert_repaired('阿岚将右后车门拉开，扶住门边。')

    def test_extra_body_and_clothing_transitions_are_rejected_in_any_visual_field(self):
        samples = (('composition', '阿岚侧身扶着门边，身体部分探入车内。'),
                   ('lighting', '暖光照亮阿岚，她的上半身探进车内。'),
                   ('texture', '阿岚坐进后排座椅，风衣形成褶皱。'),
                   ('camera_detail', '阿岚换上白色外套，面部清晰。'))
        for field, text in samples:
            with self.subTest(field=field, text=text):
                self.assert_repaired(text, field=field)

    def test_uncast_person_is_not_added_as_static_background(self):
        self.assert_repaired('门口站着一名陌生男子。')

    def test_open_performance_prose_is_rejected_and_enum_keeps_a_real_state(self):
        self.assert_repaired('阿岚神情平静，右手握住车门把手。', field='performance')
        self.assertIn('神情专注。', compiler.PERFORMANCE)
        def state(plan, request):
            plan['shots'][0]['visual']['performance'] = '神情专注。'
        row, calls = self.write(corrupt=state)
        self.assertEqual(len(calls), 1)
        self.assertFalse(row['source_fallback'])
        self.assertIn('神情专注。', row['prompt'])

    def test_intentional_source_repetition_and_sequence_are_preserved(self):
        source = '阿岚握住把手。阿岚松开把手。阿岚握住把手。'
        row, _ = self.write(source=source)
        body = row['prompt'].split('detailed_description:\n')[1].split('\n\noverall_soundscape:')[0]
        self.assertEqual(body.count('握住把手。'), 2)
        self.assertLess(body.index('握住把手。'), body.index('松开把手。'))
        self.assertGreater(body.rindex('握住把手。'), body.index('松开把手。'))

    def test_exact_negative_dialogue_is_not_filtered_as_a_rule(self):
        words = '不要动，等我把门打开。'
        row, _ = self.write(source='', speech=[['阿岚', words]])
        self.assertEqual(row['prompt'].count(words), 1)
        self.assertIn('<d>[Chinese] ' + words + '</d>', row['prompt'])

    def test_source_event_sound_is_allowed_without_replaying_action(self):
        def sound(plan, request):
            plan['soundscape'] = '细雨落在车顶的沙沙声，车门拉开时的轻响。'
        row, calls = self.write(corrupt=sound)
        self.assertEqual(len(calls), 1)
        self.assertFalse(row['source_fallback'])
        self.assertIn('车门拉开时的轻响。', row['prompt'])
        self.assertIn('细微胶片颗粒', row['prompt'])
        self.assertEqual(row['director_compilation']['source_shot_audit']['shots'][-1]['end'], 12)

    def test_static_door_location_near_sidewalk_is_not_a_character_movement(self):
        location = '右后车门靠近人行道，红色车身位于画面右侧。'
        def locate(plan, request):
            plan['shots'][0]['visual']['composition'] = location
        row, calls = self.write(corrupt=locate)
        self.assertEqual(len(calls), 1)
        self.assertFalse(row['source_fallback'])
        self.assertIn(location, row['prompt'])

    def test_character_approaching_door_remains_a_source_owned_action(self):
        self.assert_repaired('阿岚靠近车门。')

    def test_source_pull_shut_authorizes_closing_sound_synonym(self):
        sound = '细雨落在车顶，车门关闭时的声响低沉。'
        def close_sound(plan, request):
            plan['soundscape'] = sound
        row, calls = self.write(source='阿岚拉上右后车门。', corrupt=close_sound)
        self.assertEqual(len(calls), 1)
        self.assertFalse(row['source_fallback'])
        self.assertIn(sound, row['prompt'])

    def test_opening_source_does_not_authorize_new_closing_sound(self):
        sound = '细雨声中传来一声关门闷响。'
        def close_sound(plan, request):
            plan['soundscape'] = sound
        row, calls = self.write(source='阿岚拉开右后车门。', corrupt=close_sound)
        self.assertEqual(len(calls), 2)
        self.assertFalse(row['source_fallback'])
        self.assertNotIn('关门闷响', row['prompt'])
        self.assertIn('关门', calls[1].get('field_errors', calls[1].get('format_error', '')))

    def test_closing_another_object_does_not_authorize_a_car_door_sound(self):
        sound = '细雨中传来车门关闭时的闷响。'
        def door_sound(plan, request):
            plan['soundscape'] = sound
        for source in ('阿岚拉上窗帘。', '阿岚合上书本。'):
            with self.subTest(source=source):
                row, calls = self.write(source=source, corrupt=door_sound)
                self.assertEqual(len(calls), 2)
                self.assertFalse(row['source_fallback'])
                self.assertNotIn('车门关闭', row['prompt'])

    def test_seated_body_and_lap_hand_state_cannot_be_repeated_in_photography(self):
        row, calls = self.assert_repaired('阿岚坐稳在后排，双手自然放在膝上。')
        self.assertIn('坐稳', calls[1].get('field_errors', calls[1].get('format_error', '')))

    def test_photography_cannot_switch_file_bag_from_left_arm_to_right_arm(self):
        source = '阿岚左臂夹着文件袋，右手握住手电筒。'
        texture = '左袖布料带有柔软褶皱，文件袋的纸面纹理清楚。'
        def wrong_arm(plan, request):
            plan['shots'][0]['visual']['composition'] = '阿岚右臂夹着文件袋。'
            plan['shots'][0]['visual']['texture'] = texture
        for persistent in (False, True):
            with self.subTest(persistent=persistent):
                row, calls = self.write(source=source, corrupt=wrong_arm, always_bad=persistent)
                self.assertEqual(len(calls), 2)
                self.assertFalse(row['source_fallback'])
                self.assertNotIn('右臂夹着文件袋', row['prompt'])
                self.assertEqual(row['prompt'].count('左臂夹着文件袋'), 1)
                self.assertIn(texture, row['prompt'])
                self.assertIn('夹着', calls[1].get('field_errors', calls[1].get('format_error', '')))
                audit = row['director_compilation']['source_shot_audit']
                self.assertEqual(bool(audit.get('photography_review_required')), persistent)
                if persistent:
                    self.assertIn(['shots', 0, 'visual', 'composition'], audit['omitted_visual_fields'])

    def test_static_arm_skin_and_cloth_texture_is_preserved(self):
        source = '阿岚左臂夹着文件袋，右手握住手电筒。'
        texture = '左臂袖口的布料织纹清楚，手背皮肤有自然细纹，纸袋边缘呈现细小纤维。'
        def detail(plan, request):
            plan['shots'][0]['visual']['texture'] = texture
        row, calls = self.write(source=source, corrupt=detail)
        self.assertEqual(len(calls), 1)
        self.assertFalse(row['source_fallback'])
        self.assertIn(texture, row['prompt'])
        self.assertEqual(row['prompt'].count('左臂夹着文件袋'), 1)

    def test_exclusive_clothing_detail_binds_its_owner_and_wrong_name_is_not_accepted(self):
        clothing = {'阿岚': '深灰色风衣、米白色高领衫、深色长裤、黑色短靴',
                    '顾亭': '黑色西装、白色衬衫、黑色长裤'}
        source = '阿岚与顾亭停在门边。'
        for wrong_name, persistent in ((False, False), (True, False), (True, True)):
            detail = '侧光擦亮' + ('顾亭的' if wrong_name else '') + '深灰色风衣肩部，布料纹理清楚。'
            def clothing_detail(plan, request):
                plan['shots'][0]['visual']['lighting'] = detail
            with self.subTest(wrong_name=wrong_name, persistent=persistent):
                row, calls = self.write(source=source, cast=['阿岚', '顾亭'], clothing=clothing,
                    corrupt=clothing_detail, always_bad=persistent)
                self.assertEqual(len(calls), 2 if wrong_name else 1)
                self.assertFalse(row['source_fallback'])
                audit = row['director_compilation']['source_shot_audit']
                accepted = audit['shots'][0]['visual']['lighting']
                if not wrong_name:
                    self.assertIn('阿岚', accepted)
                    self.assertIn('深灰色风衣', accepted)
                    self.assertNotIn('顾亭', accepted)
                else:
                    self.assertNotIn('顾亭的深灰色风衣', accepted)
                    self.assertEqual(bool(audit.get('photography_review_required')), persistent)
        def offscreen_detail(plan, request):
            plan['shots'][0]['visual']['lighting'] = '侧光擦亮深灰色风衣肩部，布料纹理清楚。'
        row, calls = self.write(source='顾亭停在门边。阿岚随后走进门厅。',
            cast=['阿岚', '顾亭'], clothing=clothing, corrupt=offscreen_detail, always_bad=True)
        self.assertEqual(len(calls), 2)
        self.assertFalse(row['source_fallback'])
        audit = row['director_compilation']['source_shot_audit']
        self.assertTrue(audit['photography_review_required'])
        self.assertEqual(audit['shots'][0]['visual']['lighting'], '')

    def test_confirmed_replacement_outfit_or_changed_layer_does_not_restore_baseline_coat(self):
        old_design = '深灰色风衣、米白色高领衫、深色长裤、黑色短靴、银色腕表'
        for whole_outfit, new_item in ((True, '蓝色工作服'), (False, '蓝色夹克')):
            # Synthetic evidence-backed wardrobe fixtures, independent of an
            # observed endpoint pose. They never invoke video observation.
            confirmed = {
                'policy': wardrobe.POLICY, 'last_segment': 1,
                'characters': {'a': {'name': '阿岚', 'baseline_design': old_design,
                    'outfit_replaced': whole_outfit,
                    'slots': {('outfit' if whole_outfit else 'outerwear'):
                        {'status': 'worn', 'item': new_item, 'holder': '阿岚',
                         'location': '身上', 'segment': 1,
                         'before': old_design if whole_outfit else '深灰色风衣',
                         'after': new_item},
                        'shoes': {'status': 'worn', 'item': '黑色短靴', 'segment': 1},
                        'accessory': {'status': 'worn', 'item': '银色腕表', 'segment': 1}}}},
            }
            stored = copy.deepcopy(confirmed)
            bindings = [{'character_id': 'a', 'name': '阿岚', 'role': 'character', 'subject': 1}]
            sent = wardrobe.model_state(confirmed, bindings)
            self.assertEqual(confirmed, stored)
            self.assertNotIn('深灰色风衣', json.dumps(sent, ensure_ascii=False))
            direct_lines = '\n'.join(wardrobe.positive_clothing_lines(confirmed, bindings))
            self.assertNotIn('深灰色风衣', direct_lines)
            self.assertEqual(confirmed, stored)
            self.assertIn('深灰色风衣', confirmed['characters']['a']['baseline_design'])
            for old_coat in (True, False):
                garment = '深灰色风衣' if old_coat else new_item
                detail = '侧光擦亮' + garment + '肩部，布料纹理清楚。'
                def clothing_detail(plan, request):
                    plan['shots'][0]['visual']['lighting'] = detail
                with self.subTest(whole_outfit=whole_outfit, old_coat=old_coat):
                    row, calls = self.write(source='阿岚停在门边。', clothing={'阿岚': old_design},
                        current_wardrobe=confirmed, corrupt=clothing_detail, always_bad=old_coat)
                    self.assertFalse(row['source_fallback'])
                    self.assertFalse(row['director_contract']['start_state'])
                    retention = row['prompt'].split('retention_analysis:\n')[1].split('\n\ndetailed_description:')[0]
                    self.assertNotIn('深灰色风衣', retention)
                    self.assertIn('黑色短靴', row['prompt'])
                    self.assertIn('银色腕表', row['prompt'])
                    if not whole_outfit:
                        self.assertIn('米白色高领衫', row['prompt'])
                        self.assertIn('深色长裤', row['prompt'])
                    body = row['prompt'].split('detailed_description:\n')[1].split('\n\noverall_soundscape:')[0]
                    self.assertNotIn('深灰色风衣', body)
                    audit = row['director_compilation']['source_shot_audit']
                    if old_coat:
                        self.assertEqual(len(calls), 2)
                        self.assertTrue(audit['photography_review_required'])
                        self.assertIn(['shots', 0, 'visual', 'lighting'], audit['omitted_visual_fields'])
                    else:
                        self.assertEqual(len(calls), 1)
                        self.assertIn(new_item, body)
                        self.assertIn('阿岚', audit['shots'][0]['visual']['lighting'])
        for transition in ({}, {'before': '深灰色风衣', 'after': '在深灰色风衣外叠穿蓝色夹克'}):
            state = {
                'policy': wardrobe.POLICY, 'last_segment': 1,
                'characters': {'a': {'name': '阿岚', 'baseline_design': old_design,
                    'outfit_replaced': False, 'slots': {'outerwear': {
                        'status': 'worn', 'item': '蓝色夹克', 'segment': 1, **transition}}}},
            }
            stored = copy.deepcopy(state)
            sent = wardrobe.model_state(state, bindings)
            self.assertIn('深灰色风衣', sent['characters']['a']['baseline_design'])
            self.assertEqual(state, stored)
            def old_coat_detail(plan, request):
                plan['shots'][0]['visual']['lighting'] = '侧光擦亮深灰色风衣肩部，布料纹理清楚。'
            with self.subTest(transition=transition):
                row, calls = self.write(source='阿岚停在门边。', clothing={'阿岚': old_design},
                    current_wardrobe=state, corrupt=old_coat_detail)
                self.assertEqual(len(calls), 1)
                self.assertFalse(row['source_fallback'])
                self.assertIn('深灰色风衣', row['prompt'])
                self.assertIn('蓝色夹克', row['prompt'])
                self.assertIn('米白色高领衫', row['prompt'])

    def test_sound_has_a_physical_source_or_an_explicit_fictional_source(self):
        examples = (
            ('阿岚用手电筒照亮湿地。', '手电筒光束扫过湿地的细微摩擦声。', False),
            ('阿岚拉开后门。阿岚把钥匙放在金属盘里。',
             '细雨落地的沙沙声、门轴的轻响、钥匙轻碰金属盘的叮当声。', True),
            ('魔法手电的光束扫过湿地，发出细微的摩擦声。',
             '魔法手电的光束扫过湿地时发出的细微摩擦声。', True),
            ('手电筒光束扫过湿地，阿岚鞋底与湿地摩擦发出细微声响。',
             '手电筒光束扫过湿地的细微摩擦声。', False),
        )
        for source, sound, allowed in examples:
            def sound_detail(plan, request):
                plan['soundscape'] = sound
            with self.subTest(source=source, sound=sound):
                row, calls = self.write(source=source, corrupt=sound_detail)
                self.assertFalse(row['source_fallback'])
                self.assertEqual(len(calls), 1 if allowed else 2)
                actual_sound = row['prompt'].split('overall_soundscape:\n')[1].split('\n\nnon_diegetic_music:')[0]
                if allowed:
                    self.assertIn(sound, actual_sound)
                else:
                    self.assertNotIn('光束扫过湿地的细微摩擦声', actual_sound)

    def test_writer_three_slices_keep_declared_light_off_without_claiming_video_evidence(self):
        original_slices, cards, settings = fixture()
        settings.pop('video_action_compiler')
        cards = cards[:1]
        settings['style'] = '写实电影摄影，细微胶片颗粒'
        settings['_director_reference_paths'] = {name: __file__ for name in ('阿岚', '修复室', '后巷')}
        settings['_director_scene_facts'] = {
            '修复室': {'light': '高窗冷色微光，室内大面积深暗'},
            '后巷': {'light': '冷色环境光与室外路灯暖光'},
        }
        slices = []
        for scene, text in (('修复室', '顶灯突然熄灭。阿岚左臂夹着文件袋。'),
                            ('修复室', '阿岚拉开后门，走到后门门槛旁。'),
                            ('后巷', '阿岚从后门迈入后巷，停在墙边。')):
            segment = copy.deepcopy(original_slices[0])
            segment.update(seconds=12, scene_hint=scene, text=text, chars=['阿岚'])
            segment['director'].update(cast=['阿岚'], blocks=[[0, 12, text, []]], says=[])
            slices.append(segment)
        calls = []
        def q(system, user, **options):
            request = json.loads(user)
            calls.append(request)
            context = request['declared_lighting_states']
            self.assertEqual(context['kind'], 'declared_story_plan')
            self.assertEqual([(state['scene'], state['source'], state['status']) for state in context['states']],
                             [('修复室', '顶灯', 'off')])
            self.assertFalse(request['endpoint_start_state'])
            self.assertNotIn('verified', context)
            self.assertNotIn('actual_state', request)
            if 'field_values' in request:
                self.assertEqual(set(request['field_values']), {'shots.0.visual.lighting'})
                # The third segment deliberately fails its one bounded rewrite.
                # Only the unreliable light sentence should then be omitted.
                replacements = {key: value if request['scene'] == '后巷' else '高窗冷色微光擦亮衣料边缘。'
                                for key, value in request['field_values'].items()}
                return json.dumps(replacements, ensure_ascii=False)
            plan = model_reply(request)
            plan['shots'][0]['visual']['lighting'] = ('门缝透出的室内暖光照亮衣袖。' if request['scene'] == '后巷'
                else '室内暖光照亮衣袖。')
            return json.dumps(plan, ensure_ascii=False)
        rows = writer.make_prompts(slices, cards, ['修复室', '后巷'], q, settings=settings,
            actual_state_for=lambda index: {'actual_visible': 'unknown', 'scene': 'unknown'})
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(calls), 6)
        self.assertIn('修复室', calls[-1]['declared_lighting_states']['linked_scenes'])
        self.assertIn('后巷', calls[-1]['declared_lighting_states']['linked_scenes'])
        for index, row in enumerate(rows):
            self.assertFalse(row['source_fallback'])
            self.assertNotIn('室内暖光', row['prompt'])
            self.assertFalse(row['director_contract']['start_state'])
            audit = row['director_compilation']['source_shot_audit']
            self.assertEqual(bool(audit.get('photography_review_required')), index == 2)
        self.assertIn('高窗冷色微光擦亮衣料边缘。', rows[0]['prompt'])
        self.assertIn('高窗冷色微光擦亮衣料边缘。', rows[1]['prompt'])
        self.assertIn(['shots', 0, 'visual', 'lighting'], rows[2]['director_compilation']['source_shot_audit']['omitted_visual_fields'])

    def test_global_door_pose_is_repaired_into_source_owned_action_only(self):
        for state in ('紧闭', '敞开'):
            scene = '雨夜旧街，红色出租车的右后车门' + state + '。'
            def global_state(plan, request):
                plan['scene_description'] = scene
            with self.subTest(state=state):
                row, calls = self.write(corrupt=global_state)
                self.assertEqual(len(calls), 2)
                self.assertFalse(row['source_fallback'])
                self.assertNotIn(state, row['prompt'])
                self.assertIn(state, calls[1].get('field_errors', calls[1].get('format_error', '')))
                self.assertEqual(row['prompt'].count('将右后车门拉开'), 1)

    def test_rich_static_photography_and_focus_change_still_reach_model_text(self):
        details = dict(composition='红色车身位于前景右侧，店铺招牌在后方形成纵深。',
                       lighting='冷色路灯擦亮发丝边缘，车窗透出柔和暖光。',
                       texture='风衣肩部有细密水珠，车漆带着湿润的细小反光。',
                       performance='神情专注。',
                       camera_detail='焦点由车门把手缓慢转至面部，背景灯光柔和虚化。')
        def enrich(plan, request):
            plan['shots'][0]['visual'].update(details)
        row, calls = self.write(corrupt=enrich)
        self.assertEqual(len(calls), 1)
        self.assertFalse(row['source_fallback'])
        for description in details.values():
            self.assertIn(description, row['prompt'])

    def test_uncertain_open_performance_can_be_omitted(self):
        def omit(plan, request):
            for shot in plan['shots']:
                shot['visual'].pop('performance')
        row, calls = self.write(corrupt=omit, always_bad=True)
        self.assertEqual(len(calls), 1)
        self.assertFalse(row['source_fallback'])

    def test_environment_and_ambiguous_multi_actor_shots_do_not_get_a_face_state(self):
        def state(plan, request):
            plan['shots'][0]['visual']['performance'] = '神情专注。'
        for source, cast in (('薄雾沿路边的石板缓缓移动。', []),
                             ('阿岚与顾亭站在出租车旁。', ['阿岚', '顾亭'])):
            with self.subTest(source=source):
                row, calls = self.write(source=source, cast=cast, corrupt=state)
                self.assertEqual(len(calls), 1)
                self.assertFalse(row['source_fallback'])
                self.assertNotIn('神情专注', row['prompt'])

    def test_persistently_bad_optional_visual_is_omitted_with_honest_review_warning(self):
        def corrupt(plan, request):
            plan['shots'][0]['visual']['composition'] = '阿岚坐进后排座椅。'
            plan['shots'][0]['weight'] = 7
        traces = []
        row, calls = self.write(corrupt=corrupt, always_bad=True, traces=traces)
        self.assertEqual(len(calls), 2)
        self.assertFalse(row['source_fallback'])
        self.assertTrue(row['problems'])
        self.assertTrue(any('需复核' in warning for warning in row['problems']))
        self.assertNotIn('坐进后排座椅', row['prompt'])
        self.assertEqual(row['prompt'].count('握住车门把手'), 1)
        self.assertIn('侧光柔和，暗部细节自然。', row['prompt'])
        self.assertIn('细腻的材质纹理', row['prompt'])
        self.assertIn('焦点清晰，背景柔和。', row['prompt'])
        audit = row['director_compilation']['source_shot_audit']
        self.assertTrue(audit['photography_review_required'])
        self.assertEqual(audit['omitted_visual_fields'], [['shots', 0, 'visual', 'composition']])
        self.assertEqual(audit['shots'][0]['visual']['composition'], '')
        self.assertGreater(audit['shots'][0]['end'], 5)
        self.assertEqual(traces[-1]['attempts'][-1]['status'], 'accepted_with_omissions')

    def test_bad_source_group_or_unomittable_style_still_uses_full_source_fallback(self):
        for issue in ('group', 'style'):
            def corrupt(plan, request):
                if issue == 'group':
                    plan['shots'][0]['unit_ids'] = ['U999']
                else:
                    plan['style_description'] = '阿岚坐进后排座椅。'
            with self.subTest(issue=issue):
                row, calls = self.write(corrupt=corrupt, always_bad=True)
                self.assertEqual(len(calls), 2)
                self.assertTrue(row['source_fallback'])
                self.assertTrue(row['problems'])
                audit = row['director_compilation']['source_shot_audit']
                self.assertFalse(audit.get('photography_review_required', False))
                self.assertFalse(audit.get('omitted_visual_fields', []))
                self.assertEqual(row['prompt'].count('握住车门把手'), 1)
                self.assertEqual([u for shot in audit['shots'] for u in shot['unit_ids']], ['U001', 'U002', 'U003'])

    def test_unreliable_scene_and_sound_use_specific_safe_defaults_only(self):
        def corrupt(plan, request):
            plan['scene_description'] = '旧街，右后车门紧闭。'
            plan['soundscape'] = '一声关门闷响。'
        row, calls = self.write(corrupt=corrupt, always_bad=True)
        self.assertEqual(len(calls), 2)
        self.assertFalse(row['source_fallback'])
        self.assertNotIn('紧闭', row['prompt'])
        self.assertNotIn('关门闷响', row['prompt'])
        self.assertIn('轻微的现场环境底噪。', row['prompt'])
        self.assertIn('侧光柔和，暗部细节自然。', row['prompt'])
        audit = row['director_compilation']['source_shot_audit']
        self.assertTrue(audit['photography_review_required'])
        self.assertEqual({tuple(path) for path in audit['omitted_visual_fields']},
                         {('scene_description',), ('soundscape',)})

    def test_field_repair_keeps_previously_accepted_camera_and_other_details(self):
        def corrupt(plan, request):
            plan['shots'][0]['visual']['composition'] = '阿岚坐进后排座椅。'
        row, calls = self.write(corrupt=corrupt, repair_noise=True)
        self.assertEqual(len(calls), 2)
        self.assertFalse(row['source_fallback'])
        self.assertNotIn('坐进后排座椅', row['prompt'])
        self.assertNotIn('未经请求', row['prompt'])
        self.assertIn('中景，平视，固定机位', row['prompt'])
        self.assertIn('侧光柔和，暗部细节自然。', row['prompt'])

    def test_repair_merge_rejects_non_visual_or_invalid_paths(self):
        base = model_reply({'scene': '旧街', 'source_units': [{'id': 'U001'}]})
        proposal = copy.deepcopy(base)
        for path in ([], ['shots', 0, 'unit_ids'], ['shots', -1, 'visual', 'lighting'],
                     ['shots', True, 'visual', 'lighting'], ['shots', 0, 'visual', 'unknown'],
                     ['shots', 999, 'visual', 'lighting'], ['arbitrary']):
            with self.subTest(path=path), self.assertRaises((ValueError, KeyError, IndexError, TypeError)):
                writer._repair_source_visual_fields(base, proposal, [path])

    def test_flat_delta_repairs_top_and_nested_fields_without_replacing_camera_plan(self):
        replacement = {
            'scene_description': '雨夜旧街，路边红色出租车与湿石板路面。',
            'shots.0.visual.composition': '红色车身位于画面右侧，门把手位于前景。',
        }
        original_light = '冷色路灯在车漆上形成窄亮边。'
        def corrupt(plan, request):
            plan['scene_description'] = '雨夜旧街，右后车门紧闭。'
            plan['shots'][0]['visual']['composition'] = '阿岚坐稳在后排，双手放在膝上。'
            plan['shots'][0]['visual']['lighting'] = original_light
            plan['shots'][0]['weight'] = 7
        def delta(request):
            self.assertEqual(set(request['field_values']), set(replacement))
            return replacement
        row, calls = self.write(corrupt=corrupt, delta_reply=delta)
        self.assertEqual(len(calls), 2)
        self.assertFalse(row['source_fallback'])
        for text in replacement.values():
            self.assertIn(text, row['prompt'])
        self.assertIn(original_light, row['prompt'])
        self.assertIn('中景，平视，固定机位', row['prompt'])
        self.assertNotIn('坐稳在后排', row['prompt'])
        self.assertNotIn('紧闭', row['prompt'])
        audit = row['director_compilation']['source_shot_audit']
        self.assertGreater(audit['shots'][0]['end'], 5)
        self.assertEqual([u for shot in audit['shots'] for u in shot['unit_ids']], ['U001', 'U002', 'U003'])
        self.assertEqual(row['prompt'].count('握住车门把手'), 1)

    def test_flat_delta_with_an_unrequested_camera_key_is_rejected(self):
        def corrupt(plan, request):
            plan['shots'][0]['visual']['composition'] = '阿岚坐进后排座椅。'
        def malicious_delta(request):
            self.assertEqual(set(request['field_values']), {'shots.0.visual.composition'})
            return {'shots.0.visual.composition': '红色车身位于画面右侧。',
                    'shots.0.framing': '远景'}
        row, calls = self.write(corrupt=corrupt, delta_reply=malicious_delta)
        self.assertEqual(len(calls), 2)
        self.assertTrue(row['source_fallback'])
        self.assertTrue(row['problems'])
        self.assertNotIn('坐进后排座椅', row['prompt'])
        self.assertEqual(row['prompt'].count('握住车门把手'), 1)


if __name__ == '__main__':
    unittest.main()
