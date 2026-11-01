"""Scene state reaches both main prompt paths without production writes/models."""
import json
import unittest
from unittest.mock import patch

from tests import test_main_images as main_fixtures
from cores import asset_core, main_images, prompt_writer, story_core
from cores.scene_condition import positive_condition
from models import anima_client, krea_client, moss_client, moss_prompt, qwen_client
MOSS = main_fixtures.MOSS


class SceneDefaultMainTests(unittest.TestCase):
    setUp = main_fixtures.MainImageTests.setUp

    def save(self, text, model='moss'):
        story_core.save_story({'story_id': 'TEST', 'one_line': text,
                              'settings': dict(MOSS, image_model=model)})
        card = dict(scene_id='S1', name='宫殿大厅', space='宽阔的宫殿大厅', auto_made=True)
        asset_core.save_asset('TEST', 'scenes', card)
        return card

    def test_five_explicit_requests_reach_one_shared_positive_state(self):
        cases = [
            ('宫殿大厅', 'auto', 'tidy', set()),
            ('宫殿大厅，气氛黑暗', 'dark', 'tidy', set()),
            ('宫殿大厅，柱子有烧焦痕迹', 'auto', 'tidy', {'scorch'}),
            ('宫殿大厅，东西杂乱', 'auto', 'messy', set()),
            ('宫殿大厅，气氛黑暗，建筑有烧焦痕迹，东西杂乱破败',
             'dark', 'messy', {'scorch', 'wear', 'peeling', 'crack'}),
        ]
        for text, lighting, order, effects in cases:
            with self.subTest(text=text):
                card = self.save(text)
                with patch.object(moss_prompt, 'write_prompt', return_value={'prompt': '宫殿大厅的空间全景。'}) as writer:
                    main_images.write_scene('TEST', card, MOSS)
                condition = writer.call_args.kwargs['validate_prompt'].keywords['condition']
                self.assertEqual(condition['lighting'], lighting)
                self.assertEqual(condition['order'], order)
                self.assertEqual(condition['structure'], 'intact')
                self.assertEqual({x['kind'] for x in condition['surface_effects']}, effects)
                facts = json.loads(writer.call_args.args[0])['场景卡']
                self.assertEqual(facts['场景当前状态'], positive_condition(condition))
                self.assertNotIn('scene_condition', facts)
                self.assertEqual(asset_core.get_asset('TEST', 'scenes', 'S1')['scene_condition'], condition)

    def test_darkness_does_not_authorize_burned_building_in_actual_writer(self):
        card = self.save('宫殿大厅，气氛黑暗')
        responses = [('{"caption":"昏暗的大厅，柱子烧焦，屋顶坍塌。"}', {'finish_reason': 'stop'}),
                     ('{"caption":"昏暗的宫殿大厅，柱列完整，地面整洁，陈设有序，中央通道宽阔。"}',
                      {'finish_reason': 'stop'})]
        with patch.object(qwen_client, 'chat', side_effect=responses) as chat, \
             patch.object(moss_client, 'local_guard'), patch.object(moss_client, '_resource_guard'), \
             patch.object(anima_client, 'idle_guard'):
            prompt = main_images.write_scene('TEST', card, MOSS)
        self.assertEqual(chat.call_count, 2)
        self.assertIn('柱列完整', prompt)
        self.assertNotIn('坍塌', prompt)

    def test_generated_design_cannot_grant_its_own_damage_exception(self):
        card = self.save('宫殿大厅')
        card['space'] = '自动设计为烧焦、杂乱破败的宫殿大厅'
        with patch.object(moss_prompt, 'write_prompt', return_value={'prompt': '完整整洁的宫殿大厅。'}) as writer:
            main_images.write_scene('TEST', card, MOSS)
        condition = writer.call_args.kwargs['validate_prompt'].keywords['condition']
        self.assertEqual(condition['surface_effects'], [])
        self.assertEqual(condition['order'], 'tidy')

    def test_changed_user_request_invalidates_an_automatic_cached_prompt(self):
        card = self.save('宫殿大厅')
        with patch.object(moss_prompt, 'write_prompt', return_value={'prompt': '完整整洁的宫殿大厅。'}):
            main_images.write_scene('TEST', card, MOSS)
        saved = asset_core.get_asset('TEST', 'scenes', 'S1')
        story_core.save_story({'story_id': 'TEST', 'one_line': '宫殿大厅，气氛黑暗', 'settings': dict(MOSS)})
        with patch.object(moss_prompt, 'write_prompt', return_value={'prompt': '昏暗而整洁的宫殿大厅。'}) as writer:
            main_images.write_scene('TEST', saved, MOSS)
        writer.assert_called_once()

    def test_krea_compiler_receives_the_same_positive_condition(self):
        card = self.save('宫殿大厅，气氛黑暗', model='krea2')
        with patch('cores.quality_core.compile_scene_prompt', return_value='昏暗整洁的宫殿大厅，柱列完整。') as compiler:
            main_images.write_scene('TEST', card, {'image_model': 'krea2', 'style': '电影级写实'})
        self.assertIn('环境照明昏暗', compiler.call_args.kwargs['purpose_details'])
        self.assertIn('场景干净整洁', compiler.call_args.kwargs['purpose_details'])
        self.assertTrue(compiler.call_args.kwargs['appearance_only'])
        self.assertTrue(compiler.call_args.kwargs['clear_reference'])

    def test_large_krea_references_do_not_invent_fog_or_ruined_framing(self):
        for name, interior, space in [('宫殿大厅', '内', '宫殿大厅，柱列与尽头主位'),
                                      ('森林空地', '外', '森林中的开阔空地，周围树木完整')]:
            with self.subTest(name=name):
                story_core.save_story({'story_id': 'TEST', 'one_line': name,
                                      'settings': {'image_model': 'krea2'}})
                card = dict(scene_id='S1', name=name, interior=interior,
                            space=space, scale_class='大', auto_made=True)
                asset_core.save_asset('TEST', 'scenes', card)
                prompt = main_images.write_scene('TEST', card,
                    {'image_model': 'krea2', 'style': '电影级写实'})
                self.assertIn('前中远景清晰', prompt)
                self.assertFalse(any(word in prompt for word in
                    ('雾气', '雾霭', '烟气', '残垣', '五到八层', '浅景深')))

    def test_main_scene_writer_uses_clear_reference_brief_and_keeps_special_facts(self):
        card = self.save('宫殿大厅，气氛黑暗，柱子有烧焦痕迹')
        with patch.object(moss_prompt, 'write_prompt', return_value={
                'prompt': '昏暗的宫殿大厅，柱子留有焦痕。'}) as writer:
            main_images.write_scene('TEST', card, MOSS)
        options = writer.call_args.kwargs
        self.assertEqual(options['output_format'], 'caption')
        self.assertEqual(options['system'], main_images.SCENE_REFERENCE_INSTRUCTION)
        payload = json.loads(writer.call_args.args[0])
        style = payload['项目视觉设定']['画风说明']
        self.assertIn('前中远景清晰', style)
        self.assertFalse(any(word in style + options['system']
                             for word in ('浅景深', '雾气', '胶片颗粒', '青橙分离')))
        state = payload['场景卡']['场景当前状态']
        self.assertIn('环境照明昏暗', state)
        self.assertIn('柱子', state)
        self.assertIn('烧焦', state)

    def test_scene_basic_information_cannot_reintroduce_old_light_or_arrangement(self):
        card = self.save('宫殿大厅，气氛黑暗，东西杂乱')
        card.update(
            interior='内', time_of_day='白天',
            space='室内宫殿大厅，朱漆立柱和木构梁架完整，中央通道贯通前后，明亮日光洒满大厅，木椅井然有序',
            light='明亮日光从高窗照入中央通道',
            window='两侧高窗',
            fixtures='两侧木椅井然有序，北端石阶完整')
        with patch.object(moss_prompt, 'write_prompt', return_value={
                'prompt': '昏暗的宫殿大厅，木椅错位摆放，朱漆立柱和木构梁架完整。'}) as writer:
            main_images.write_scene('TEST', card, MOSS)
        payload = json.loads(writer.call_args.args[0])
        facts = payload['场景卡']
        basic = facts['场景基本信息']
        self.assertIn('环境照明昏暗', facts['light'])
        self.assertIn(facts['light'], basic)
        self.assertIn(facts['space'], basic)
        self.assertIn('错位摆放', facts['场景当前状态'])
        payload_text = json.dumps(payload, ensure_ascii=False)
        for obsolete in ('明亮日光', '洒满大厅', '井然有序'):
            self.assertNotIn(obsolete, payload_text)
        for structure in ('朱漆立柱', '木构梁架', '高窗', '中央通道', '石阶'):
            self.assertIn(structure, payload_text)
        self.assertIn('朱漆立柱', basic)
        self.assertIn('木构梁架', basic)

    def test_scene_payload_omits_dramatic_intent_but_keeps_spatial_facts(self):
        card = self.save('宫殿大厅')
        card.update(
            intent='以压迫和荒凉映衬主角绝望的命运',
            space='室内宫殿大厅，朱漆立柱围绕中央通道',
            layout='南侧入口与中央通道相连，北端石阶通向主位',
            fixtures='北端主位台，两侧固定木栏')
        with patch.object(moss_prompt, 'write_prompt', return_value={
                'prompt': '写实宫殿大厅，朱漆立柱围绕中央通道，北端为主位台。'}) as writer:
            main_images.write_scene('TEST', card, MOSS)
        payload = json.loads(writer.call_args.args[0])
        facts = payload['场景卡']
        self.assertNotIn('intent', facts)
        self.assertNotIn(card['intent'], json.dumps(payload, ensure_ascii=False))
        for field in ('space', 'layout', 'fixtures'):
            self.assertEqual(facts[field], card[field])

    def test_set_dressing_keeps_explicit_counts_for_different_location_functions(self):
        cases = [
            ('走廊', '四盏壁灯，左右各两盏，分别位于入口与尽头'),
            ('起居室', '两把单椅分置茶几两侧，一张茶几位于休息区中央'),
            ('教室', '三十套课桌椅分六排五列，一张讲台位于前墙中央'),
        ]
        for name, fixtures in cases:
            with self.subTest(name=name):
                story_core.save_story({'story_id': 'TEST', 'one_line': name + '，' + fixtures,
                                      'settings': dict(MOSS)})
                card = dict(scene_id='S1', name=name, space='室内' + name,
                            fixtures=fixtures, auto_made=True)
                asset_core.save_asset('TEST', 'scenes', card)
                with patch.object(moss_prompt, 'write_prompt', return_value={
                        'prompt': name + '。' + fixtures + '。空间清晰。'}) as writer:
                    prompt = main_images.write_scene('TEST', card, MOSS)
                facts = json.loads(writer.call_args.args[0])['场景卡']
                self.assertEqual(facts['fixtures'], fixtures)
                self.assertIn(fixtures, prompt)
                self.assertEqual(asset_core.get_asset('TEST', 'scenes', 'S1')['fixtures'], fixtures)

    def test_old_automatic_scene_words_expire_but_manual_prompt_is_preserved(self):
        card = self.save('宫殿大厅')
        card = main_images.bind_prompt(card, '旧版浅景深大厅', MOSS)
        with patch.object(main_images, 'SCENE_PROMPT_POLICY', 'scene-default-state-v1'):
            card['image_prompt_facts_hash'] = main_images._facts_hash(card, MOSS)
        with patch.object(moss_prompt, 'write_prompt', return_value={
                'prompt': '写实宫殿大厅，空间和陈设清晰。'}) as writer:
            result = main_images.write_scene('TEST', card, MOSS)
        writer.assert_called_once()
        self.assertIn('清晰', result)
        manual = main_images.bind_prompt(card, '按我的要求保留薄雾和暗光。', MOSS, 'prompt_override')
        with patch.object(moss_prompt, 'write_prompt') as writer:
            self.assertEqual(main_images.write_scene('TEST', manual, MOSS),
                             manual['prompt_override'])
        writer.assert_not_called()

    def test_krea_generation_repairs_state_conflict_before_sampling(self):
        self.save('宫殿大厅，气氛黑暗', model='krea2')
        unchanged = lambda prompt, *args, **kwargs: prompt
        with patch.object(prompt_writer, 'write', side_effect=[
                '昏暗的宫殿大厅，柱子烧焦。', '昏暗的宫殿大厅，柱列完整，地面洁净。']) as writer, \
             patch.object(asset_core, '_scene_prompt_problems', side_effect=lambda *a, **k: []), \
             patch.object(asset_core, 'scene_prompt_repair', side_effect=unchanged), \
             patch.object(asset_core, 'scene_prompt_fix_scale', side_effect=unchanged), \
             patch.object(asset_core, 'scene_prompt_fix_material', side_effect=unchanged), \
             patch.object(asset_core, 'scene_prompt_fix_world', side_effect=unchanged), \
             patch.object(krea_client, 'generate', return_value=self.result) as generate:
            _, prompt = asset_core.generate_scene_master('TEST', 'S1')
        self.assertEqual(writer.call_count, 2)
        self.assertIn('场景干净整洁', writer.call_args.args[1])
        generate.assert_called_once()
        self.assertNotIn('烧焦', prompt)


if __name__ == '__main__':
    unittest.main()
