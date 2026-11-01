"""Main asset model routing and frozen inputs, with retained isolated fixtures.

No local models, running services, production stories or permanent deletion.
"""
from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'

from cores import (asset_core, authoring, gen_history, main_images, project_settings,
                   quality_core, store, story_core)
from cores.moss_prompt_format import compile_draft, rescale_compiled
from models import anima_client, krea_client, moss_client, moss_prompt, qwen_client


MOSS = {'image_model': 'moss', 'image_quality': '2x', 'style': '电影级写实'}


def layout(width=1360, height=768):
    return compile_draft({'width': width, 'height': height,
        'bbox_coord_space': 'abs', 'bbox_format': 'xyxy',
        'summary': {'caption': '同一位成年修表师'},
        'items': [{'label': 'character', 'caption': '正面全身',
                   'bbox': [32, 32, width - 32, height - 32]}]}, width, height)[0]


class MainImageTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='main_images_'))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(store, 'DATA', self.folder / 'data'))
        self.stack.enter_context(patch.object(authoring, '__file__',
            str(self.folder / 'application' / 'cores' / 'authoring.py')))
        self.stack.enter_context(patch.object(Path, 'unlink',
            side_effect=AssertionError('permanent deletion forbidden')))
        self.stack.enter_context(patch('shutil.rmtree',
            side_effect=AssertionError('permanent deletion forbidden')))
        self.stack.enter_context(patch.object(gen_history, 'record'))
        self.stack.enter_context(patch.object(authoring, 'hair_is_designed', return_value=True))
        self.output = self.folder / 'generated.png'
        self.output.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 8 + struct.pack('>II', 2720, 1536))
        self.result = {'output_path': str(self.output), 'seed': 17}
        self.stack.enter_context(patch.object(krea_client, 'generate',
            side_effect=AssertionError('unexpected real Krea call')))
        self.stack.enter_context(patch.object(moss_client, 'generate',
            side_effect=AssertionError('unexpected real Moss call')))
        self.stack.enter_context(patch.object(qwen_client, 'chat',
            side_effect=AssertionError('unexpected real Qwen call')))
        story_core.save_story({'story_id': 'TEST', 'settings': dict(MOSS)})

    def character(self, name='阿宁', cid='C1'):
        card = {'character_id': cid, 'name': name, 'age': 30, 'sex': '女',
                'hair': '黑色侧分短发', 'clothing': '蓝色工作服、灰色长裤和黑色工作鞋',
                'height': '168cm', 'face_spec': {'face_shape': '椭圆脸'},
                'appearance_details': '左眉有一颗浅痣', 'version': 1}
        asset_core.save_asset('TEST', 'characters', card)
        return card

    def test_old_project_fallback_and_explicit_options(self):
        self.assertEqual(main_images.snapshot(project_settings.get({})),
                         {'model': 'krea2', 'quality': 'workflow'})
        self.assertEqual(main_images.snapshot(MOSS),
            {'model': 'moss', 'base_w': 1360, 'base_h': 768, 'upscale': 2, 'quality': '2x'})
        self.assertEqual(main_images.snapshot(dict(MOSS, image_quality='4k')),
            {'model': 'moss', 'base_w': 1920, 'base_h': 1088, 'upscale': 2, 'quality': '4k'})
        self.assertEqual(main_images.snapshot({'image_quality': '4k'})['quality'], 'workflow')
        for settings in ({'image_model': 'unknown'}, dict(MOSS, image_quality='unknown')):
            with self.assertRaises(ValueError):
                main_images.snapshot(settings)

    def test_frozen_inputs_restore_and_do_not_leak_to_another_worker(self):
        seen = []
        with main_images.frozen(main_images.snapshot(MOSS)):
            self.assertEqual(main_images.current({})['model'], 'moss')
            with main_images.frozen(None):
                self.assertEqual(main_images.current(MOSS)['model'], 'krea2')
            self.assertEqual(main_images.current({})['model'], 'moss')
            thread = threading.Thread(target=lambda: seen.append(main_images.current({})['model']))
            thread.start()
            thread.join()
        self.assertEqual(seen, ['krea2'])
        self.assertEqual(main_images.current({})['model'], 'krea2')
        with self.assertRaises(ValueError), main_images.frozen(
                dict(main_images.snapshot(MOSS), base_w=1920)):
            pass

    def test_cache_model_binding_and_layout_resize_preserve_literal_text(self):
        prompt = layout()
        card = main_images.bind_prompt({}, prompt, MOSS)
        self.assertEqual(main_images.cached_prompt(card, {}), '')
        resized = main_images.cached_prompt(card, dict(MOSS, image_quality='4k'))
        self.assertEqual(resized, rescale_compiled(prompt, 1360, 768, 1920, 1088))
        self.assertEqual(json.loads(resized)['items'][0]['caption'], '正面全身')
        literal = '  手动指定蓝衣服。\n'
        manual = main_images.bind_prompt({}, literal, MOSS, 'prompt_override')
        self.assertEqual(main_images.cached_prompt(manual, MOSS, 'prompt_override'), literal)
        self.assertEqual(main_images.cached_prompt({'prompt_override': literal}, {}, 'prompt_override'), literal)
        self.assertEqual(main_images.cached_prompt({'prompt_override': literal}, MOSS, 'prompt_override'), '')
        self.assertEqual(main_images.cached_prompt({'image_prompt': prompt, 'image_prompt_model': 'moss'}, MOSS), '')

    def test_character_writer_uses_authoritative_card_and_binds_canvas(self):
        card = self.character('孙悟空')
        before = copy.deepcopy(card)
        with patch.object(moss_prompt, 'write_prompt', return_value={'prompt': layout()}) as write:
            prompt = main_images.write_character('TEST', card, MOSS)
        options = write.call_args.kwargs
        self.assertFalse(options['named_identity'])
        self.assertEqual((options['base_w'], options['base_h'], options['upscale']), (1360, 768, 2))
        facts = json.loads(write.call_args.args[0])['人物卡']
        self.assertEqual(facts['name'], '孙悟空')
        self.assertEqual(facts['clothing'], card['clothing'])
        self.assertEqual(facts['height'], '168cm')
        self.assertTrue(options['system'].startswith('【Moss独立：人物人设图】'))
        self.assertEqual(card, before)
        saved = asset_core.get_asset('TEST', 'characters', 'C1')
        self.assertEqual(saved['image_prompt_model'], 'moss')
        self.assertEqual(main_images.cached_prompt(saved, MOSS), prompt)
        with patch.object(moss_prompt, 'write_prompt') as write:
            self.assertEqual(main_images.write_character('TEST', saved, MOSS), prompt)
        write.assert_not_called()
        saved['clothing'] = '白色工作服、灰色长裤'
        self.assertEqual(main_images.cached_prompt(saved, MOSS), '')
        self.assertEqual(main_images.cached_prompt(asset_core.get_asset('TEST', 'characters', 'C1'),
                         dict(MOSS, style='平面动漫')), '')

    def test_main_card_opt_out_skips_named_identity_database(self):
        plan = {'identity': '原创孙悟空，30岁修表师', 'style': '真实摄影', 'body': '普通成年体型',
            'hair': '黑色短发', 'face': '左眉浅痣', 'background': '浅灰背景', 'accessories': [],
            'outfit': {'upper': '蓝色工作服', 'collar': '蓝色翻领', 'waist': '灰色腰带',
                       'lower': '灰色长裤', 'footwear': '黑色工作鞋'}}
        with patch.object(moss_client, 'local_guard'), patch.object(moss_client, '_resource_guard'), \
             patch.object(anima_client, 'idle_guard'), \
             patch.object(qwen_client, 'chat', return_value=(json.dumps(plan), {'finish_reason': 'stop'})), \
             patch.object(moss_prompt, 'sheet_identities', side_effect=AssertionError('name database must not override card')):
            result = moss_prompt.write_prompt('原创孙悟空，30岁修表师', '【Moss独立：人物人设图】规则',
                                               1360, 768, upscale=2, named_identity=False)
        self.assertIn('蓝色工作服', result['prompt'])
        self.assertEqual(len(json.loads(result['prompt'])['items']), 4)

    def test_moss_character_passes_krea_figure_scale_to_prompt_writer(self):
        from cores import prompt_writer, style_presets
        card = self.character()
        before = copy.deepcopy(card)
        settings = dict(MOSS, content_tendencies=['测试氛围'],
                        custom_content_scale='服装整洁，姿态自然')
        with patch.object(style_presets, 'tendency_effect', return_value='神态沉静') as effect:
            expected = prompt_writer._project_block(settings)['scale']
            effect.reset_mock()
            with patch.object(moss_prompt, 'write_prompt', return_value={'prompt': layout()}) as write:
                result = main_images.write_character('TEST', card, settings)
        project = json.loads(write.call_args.args[0])['项目视觉设定']
        self.assertEqual(project['content_tendencies'], ['测试氛围'])
        self.assertEqual(project['custom_content_scale'], settings['custom_content_scale'])
        self.assertEqual(project['人设图尺度说明'], expected)
        self.assertTrue(effect.call_args_list)
        self.assertTrue(all(call.args == (['测试氛围'], 'figure') for call in effect.call_args_list))
        self.assertEqual(result, layout())
        self.assertEqual(card, before)

    def test_moss_character_scale_changes_expire_only_automatic_character_words(self):
        from cores import style_presets
        card = self.character()
        scene = {'scene_id': 'S1', 'name': '修表店', 'space': '整洁店面'}
        settings = dict(MOSS, content_tendencies=['测试氛围'],
                        custom_content_scale='服装整洁，姿态自然')
        with patch.object(style_presets, 'tendency_effect', return_value='神态沉静'):
            unstamped = main_images.bind_prompt(card, layout(), MOSS)
            self.assertEqual(main_images.cached_prompt(unstamped, settings), '')
            # Previously saved words used the same card/style hash, without scale.
            old = main_images.bind_prompt(card, layout(), MOSS)
            old['image_prompt_facts_hash'] = main_images._facts_hash(card, MOSS)
            self.assertEqual(main_images.cached_prompt(old, settings), '')
            with patch.object(moss_prompt, 'write_prompt', return_value={'prompt': layout()}):
                main_images.write_character('TEST', card, settings)
            saved = asset_core.get_asset('TEST', 'characters', 'C1')
            self.assertEqual(main_images.cached_prompt(saved, settings), layout())
            self.assertEqual(main_images.cached_prompt(saved,
                dict(settings, content_tendencies=['另一氛围'])), '')
            self.assertEqual(main_images.cached_prompt(saved,
                dict(settings, custom_content_scale='服装整洁，神态轻松')), '')
            self.assertEqual(main_images._facts_hash(scene, settings), main_images._facts_hash(scene, MOSS))
            manual = main_images.bind_prompt(saved, '手动指定蓝色工作服', settings, 'prompt_override')
            self.assertEqual(main_images.cached_prompt(manual, MOSS, 'prompt_override'), '手动指定蓝色工作服')
        with patch.object(style_presets, 'tendency_effect', return_value='神态轻松'):
            self.assertEqual(main_images.cached_prompt(saved, settings), '')
        empty = main_images._project_facts(MOSS)
        for key in ('content_tendencies', 'custom_content_scale', '人设图尺度说明'):
            self.assertNotIn(key, empty)

    def test_group_keeps_shared_reference_and_checks_original_age(self):
        card = self.character('维修员们')
        card.update(entity_type='group', group_count=3, group_appearance='蓝色工作服')
        with patch.object(moss_prompt, 'write_prompt') as write:
            prompt = main_images.write_character('TEST', card, MOSS)
        write.assert_not_called()
        self.assertIn('维修员们', prompt)
        self.assertNotIn('正脸头肩特写', prompt)
        young = dict(card, entity_type='individual', name='小宁', age=12, clothing='蓝色工作服')
        with patch('cores.age_policy.assert_allowed', side_effect=ValueError('original card guard')) as guard, \
             self.assertRaisesRegex(ValueError, 'original card guard'):
            main_images.write_character('TEST', young, MOSS)
        self.assertEqual(guard.call_args.kwargs['cards'][0]['age'], 12)

    def test_scene_moss_bypasses_legacy_text_repairs_and_records_actual_model(self):
        card = {'scene_id': 'S1', 'name': '修表店', 'space': '清晨的木制柜台与高窗',
                'design': '窗在左侧墙面，柜台沿后墙，进门保留通道', 'version': 1}
        asset_core.save_asset('TEST', 'scenes', card)
        prompt = layout()
        with patch.object(moss_prompt, 'write_prompt', return_value={'prompt': prompt}), \
             patch.object(moss_client, 'generate', return_value=self.result) as generate, \
             patch.object(asset_core, 'scene_prompt_repair', side_effect=AssertionError('JSON must not be repaired as Krea text')):
            visual, actual = asset_core.generate_scene_master('TEST', 'S1')
        self.assertEqual(actual, prompt)
        generate.assert_called_once_with(prompt, base_w=1360, base_h=768, upscale=2)
        self.assertEqual((visual['model'], visual['width'], visual['height'], visual['upscale']),
                         ('moss', 2720, 1536, 2))
        manifests = list((store.DATA / 'manifests').glob('*.json'))
        manifest = store.load_json(manifests[0], {})
        self.assertEqual(manifest['model'], 'moss')
        self.assertEqual(manifest['parameters']['base_width'], 1360)
        self.assertEqual(manifest['parameters']['seed'], 17)

    def test_scene_payload_keeps_source_light_fixtures_weather_and_invalidates_changed_light(self):
        card = {'scene_id': 'S1', 'name': '修表店', 'space': '室内木制柜台',
                'light': '左侧高窗透入阴天冷光，右侧台灯形成小块暖光',
                'fixtures': '后墙一排木制工具柜，右侧固定工作台', 'weather': '阴雨',
                'ground': '深灰石板地面', 'walls': '浅色石灰墙', 'door': '正面木门',
                'window': '左侧高窗', 'regions': '前方通道与后方工作区',
                'base_light_color': '窗光冷灰与台灯暖黄', 'time_of_day': '清晨', 'interior': '内'}
        asset_core.save_asset('TEST', 'scenes', card)
        with patch.object(moss_prompt, 'write_prompt', return_value={'prompt': '修表店空间参考板'}) as writer:
            main_images.write_scene('TEST', card, MOSS)
        facts = json.loads(writer.call_args.args[0])['场景卡']
        self.assertEqual(writer.call_args.kwargs['output_format'], 'caption')
        scene_style = json.loads(writer.call_args.args[0])['项目视觉设定']['画风说明']
        self.assertNotIn('人物卡', scene_style)
        self.assertNotIn('五官', scene_style)
        self.assertNotIn('毛孔', scene_style)
        for key in ('light', 'fixtures', 'weather', 'ground', 'walls', 'door', 'window',
                    'regions', 'base_light_color'):
            self.assertEqual(facts[key], card[key])
        saved = asset_core.get_asset('TEST', 'scenes', 'S1')
        self.assertEqual(main_images.cached_prompt(saved, MOSS), '修表店空间参考板')
        saved['light'] = '右侧窗透入夕阳暖光'
        self.assertEqual(main_images.cached_prompt(saved, MOSS), '')

    def test_scene_policy_expires_auto_words_but_keeps_manual_override(self):
        from cores.generation_recovery import fingerprint
        card = {'scene_id': 'S1', 'name': '大厅', 'space': '维护完好的石构大厅'}
        old_hash = fingerprint({
            'card': {key: card.get(key) for key in main_images._SCENE_FIELDS},
            'project': main_images._project_facts(MOSS)})
        cached = main_images.bind_prompt(card, '旧版自动稿', MOSS)
        cached['image_prompt_facts_hash'] = old_hash
        self.assertEqual(main_images.cached_prompt(cached, MOSS), '')
        cached = main_images.bind_prompt(cached, '用户指定的旧城堡', MOSS, 'prompt_override')
        self.assertEqual(main_images.cached_prompt(cached, MOSS, 'prompt_override'), '用户指定的旧城堡')

    def test_scene_writer_repairs_invalid_layout_once_into_single_caption(self):
        card = {'scene_id': 'S1', 'name': '会客室', 'space': '维护完好的木构会客室',
                'layout': '门边区｜两名成年访客交谈｜左侧木门旁｜石板地面'}
        asset_core.save_asset('TEST', 'scenes', card)
        broken = {'width': 1360, 'height': 768, 'bbox_coord_space': 'abs', 'bbox_format': 'xyxy',
                  'summary': {'caption': '会客室空间'}, 'items': [
                      {'label': 'object', 'caption': '木门', 'bbox': [0, 0, 400, 768], 'text': ''}]}
        responses = [(json.dumps(broken), {'finish_reason': 'stop'}),
                     ('{"caption":"木构会客室，左侧木门，后墙嵌入书柜；窗光照亮完整的木纹与石板接缝。"}',
                      {'finish_reason': 'stop'})]
        with patch.object(qwen_client, 'chat', side_effect=responses) as chat, \
             patch.object(moss_client, 'local_guard') as game_guard, \
             patch.object(moss_client, '_resource_guard'), patch.object(anima_client, 'idle_guard'):
            prompt = main_images.write_scene('TEST', card, MOSS)
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(game_guard.call_count, 2)
        self.assertEqual(json.loads(chat.call_args.args[1])['格式修复']['错误'],
                         'Moss提示词格式：item.text须为非空文字')
        self.assertIn('唯一输出格式', chat.call_args.args[0])
        self.assertFalse(prompt.startswith('{'))
        self.assertEqual(asset_core.get_asset('TEST', 'scenes', 'S1')['image_prompt'], prompt)

    def test_caption_json_string_keeps_content_checks_without_extra_model_repair(self):
        card = {'scene_id': 'S1', 'name': '装卸台', 'space': '有顶的装卸台'}
        asset_core.save_asset('TEST', 'scenes', card)
        reply = json.dumps('装卸台，完整雨棚覆盖通道，无杂乱陈设。', ensure_ascii=False)
        with patch.object(qwen_client, 'chat', return_value=(reply, {'finish_reason': 'stop'})) as chat, \
             patch.object(moss_client, 'local_guard') as game_guard, \
             patch.object(moss_client, '_resource_guard'), patch.object(anima_client, 'idle_guard'):
            prompt = main_images.write_scene('TEST', card, MOSS)
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(game_guard.call_count, 1)
        self.assertIn('陈设井然有序', prompt)
        self.assertNotIn('无杂乱', prompt)

    def test_scene_source_identity_weather_and_construction_reach_real_writer(self):
        story_core.save_story({'story_id': 'TEST', 'settings': dict(MOSS,
            initial_story_input='暴雨持续。胶片库房有一扇玻璃门。人物经过室内走廊。')})
        card = {'scene_id': 'S1', 'name': '库房', 'space': '现代工业库房，雨后初晴'}
        asset_core.save_asset('TEST', 'scenes', card)
        reply = '{"caption":"胶片库房，玻璃门完整，窗外暴雨，通道宽敞整洁。"}'
        with patch.object(qwen_client, 'chat', return_value=(reply, {'finish_reason': 'stop'})) as chat, \
             patch.object(moss_client, 'local_guard'), patch.object(moss_client, '_resource_guard'), \
             patch.object(anima_client, 'idle_guard'):
            prompt = main_images.write_scene('TEST', card, MOSS)
        request = json.loads(chat.call_args.args[1])
        facts = json.loads(request['需求'])['场景卡']
        self.assertEqual(facts['name'], '胶片库房')
        self.assertEqual(facts['weather'], '暴雨天气')
        self.assertIn('玻璃门', facts['原文固定构造'])
        self.assertIn('暴雨', prompt)
        self.assertEqual(card['name'], '库房')

    def test_rain_to_clear_weather_drift_uses_bounded_content_repair(self):
        story_core.save_story({'story_id': 'TEST', 'settings': dict(MOSS,
            initial_story_input='暴雨持续。人物进入接待大厅。')})
        card = {'scene_id': 'S1', 'name': '接待大厅'}
        asset_core.save_asset('TEST', 'scenes', card)
        responses = [('{"caption":"接待大厅，门外雨后初晴。"}', {'finish_reason': 'stop'}),
                     ('{"caption":"接待大厅，门外暴雨，室内地面整洁。"}', {'finish_reason': 'stop'})]
        with patch.object(qwen_client, 'chat', side_effect=responses) as chat, \
             patch.object(moss_client, 'local_guard') as guard, patch.object(moss_client, '_resource_guard'), \
             patch.object(anima_client, 'idle_guard'):
            prompt = main_images.write_scene('TEST', card, MOSS)
        self.assertEqual((chat.call_count, guard.call_count), (2, 2))
        self.assertIn('天气', json.loads(chat.call_args.args[1])['格式修复']['错误'])
        self.assertIn('暴雨', prompt)

    def test_unrequested_scene_decay_is_not_saved_after_failed_repair(self):
        from cores.scene_prompt_content import ScenePromptContentError
        card = {'scene_id': 'S1', 'name': '城堡走廊', 'space': '白天宽阔的城堡走廊'}
        asset_core.save_asset('TEST', 'scenes', card)
        with patch.object(qwen_client, 'chat', return_value=(
                '{"caption":"城堡走廊的石板布满岁月磨损，石缝长着青苔。"}', {'finish_reason': 'stop'})) as chat, \
             patch.object(moss_client, 'local_guard'), patch.object(moss_client, '_resource_guard'), \
             patch.object(anima_client, 'idle_guard'), self.assertRaises(ScenePromptContentError):
            main_images.write_scene('TEST', card, MOSS)
        self.assertEqual(chat.call_count, 2)
        self.assertNotIn('image_prompt', asset_core.get_asset('TEST', 'scenes', 'S1'))

    def test_scene_payload_reuses_empty_board_curation_and_omits_story_cast(self):
        self.character('阿宁')
        card = {'scene_id': 'S1', 'name': '修表店', 'space': '宽六米的室内修表店',
                'layout': '门口区｜阿宁身穿红色雨衣站在门口｜画面左侧｜石板地面',
                'design': '【景】修表店\n【这里发生的戏】阿宁身穿红色雨衣走进店里\n'
                          '【一句话空间】宽六米的室内修表店\n'
                          '【光】左侧高窗透入冷色天光\n'
                          '【固有陈设】后墙固定工具柜\n'
                          '【纵深与尺度】柜台到后墙四米\n'
                          '【戏区】门口区｜阿宁身穿红色雨衣站在门口｜画面左侧｜石板地面'}
        asset_core.save_asset('TEST', 'scenes', card)
        with patch.object(moss_prompt, 'write_prompt', return_value={'prompt': '修表店空间参考板'}) as writer:
            main_images.write_scene('TEST', card, MOSS)
        payload = writer.call_args.args[0]
        facts = json.loads(payload)['场景卡']
        for forbidden in ('阿宁', '红色雨衣', '站在门口', '走进店里', '这里发生的戏'):
            self.assertNotIn(forbidden, payload)
        self.assertNotIn('design', facts)
        self.assertEqual(facts['layout'], '门口区｜画面左侧｜石板地面')
        self.assertEqual(facts['light'], '左侧高窗透入冷色天光')
        self.assertEqual(facts['fixtures'], '后墙固定工具柜')
        self.assertEqual(facts['depth'], '柜台到后墙四米')

    def test_batch_writes_all_drafts_before_images_and_keeps_prior_version(self):
        cards = [self.character(), self.character('阿海', 'C2')]
        previous = self.folder / 'old_portrait.png'
        previous.write_bytes(b'preserved prior version')
        asset_core.add_approved_visual('TEST', 'character_master', 'C1', 1, str(previous))
        events = []
        def write(sid, card, settings):
            events.append('write:' + card['name'])
            return layout()
        def image(prompt, **kwargs):
            events.append('image')
            return self.result
        with patch.object(main_images, 'write_character', side_effect=write), \
             patch.object(moss_client, 'generate', side_effect=image):
            result = authoring._gen_char_images('TEST', cards, MOSS)
        self.assertEqual(result, {'ok': 2, 'failed': []})
        self.assertEqual(events, ['write:阿宁', 'write:阿海', 'image', 'image'])
        self.assertEqual(previous.read_bytes(), b'preserved prior version')
        visuals = asset_core.list_assets('TEST', 'visuals')
        adopted = [v for v in visuals if v['status'] == 'adopted']
        self.assertEqual(len(adopted), 2)
        portrait = next(v for v in adopted if v['owner_id'] == 'C1')
        self.assertEqual(portrait['visual_version'], 2)
        self.assertEqual(portrait['model'], 'moss')
        self.assertIn('__v2.png', portrait['path'])
        self.assertTrue(Path(portrait['path']).is_relative_to(self.folder))

    def test_old_project_krea_generation_call_is_unchanged(self):
        card = self.character()
        story_core.save_story({'story_id': 'TEST', 'settings': {}})
        with patch.object(authoring, 'write_char_sheet', return_value='Krea旧稿'), \
             patch.object(krea_client, 'generate', return_value=self.result) as generate:
            visual, prompt = asset_core.generate_character_master('TEST', card['character_id'])
        self.assertEqual(prompt, 'Krea旧稿')
        generate.assert_called_once_with('Krea旧稿', width=1920, height=1080, steps=7)
        self.assertEqual(visual['model'], 'krea2')


if __name__ == '__main__':
    unittest.main()
