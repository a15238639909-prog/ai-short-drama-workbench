"""零模型回归：年龄真实、外观保留、所有生成入口共用边界。"""
import ast
import copy
import importlib
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from cores import age_policy as policy


class AgePolicyTests(unittest.TestCase):
    def test_numeric_ages(self):
        for raw, expected in [('12', 12), ('40岁', 40), ('十九岁', 19),
                              ('十七岁', 17), ('四十', 40), ('０', 0),
                              ('300', 300), ('少年', None), ('17-19', None),
                              ('', None), ('19岁改为40岁', None)]:
            with self.subTest(raw=raw):
                self.assertEqual(policy.age_number(raw), expected)

    def test_age_categories(self):
        for raw, expected in [('未成年', 'minor'), ('成年', 'adult'),
                              ('青年', 'unknown'), ('学生', 'unknown'),
                              ('少年', 'minor'), ('17-19岁', 'minor'),
                              ('18-21岁', 'adult'), ('', 'unknown')]:
            with self.subTest(raw=raw):
                self.assertEqual(policy.age_status(raw), expected)

    def test_normal_content_does_not_mutate(self):
        for age in ('0', '12', '17', '19', '40', '少年', ''):
            card = dict(name='测试人物', age=age, build='极瘦', clothing='普通短裤和运动上衣',
                        face_type='高颧骨', hair='黑色长发', sheet_body_ratio='迷你幼态')
            original = copy.deepcopy(card)
            policy.assert_allowed(cards=[card], text='在学校阅读、跑步、和家人交谈。', media='image')
            self.assertEqual(card, original)

    def test_sensitive_minor_and_unknown_stop_without_mutation(self):
        for age in ('0', '12', '17', '少年', '未成年', '', '17-19岁'):
            card = dict(name='测试人物', age=age, clothing='普通运动服')
            settings = {'content_tendencies': ['成人向']}
            before = copy.deepcopy((card, settings))
            with self.assertRaises(policy.AgePolicyError):
                policy.assert_allowed(cards=[card], settings=settings)
            self.assertEqual((card, settings), before)

    def test_adult_ratio_and_family_role_do_not_change_age(self):
        for role in ('成年女儿', '大学生', '学徒'):
            card = dict(name='测试人物', age='40', identity=role, sheet_body_ratio='幼态少年')
            self.assertEqual(policy.character_status(card), 'adult')
            policy.assert_allowed(cards=[card], settings={'content_tendencies': ['成人向']})

    def test_actual_child_appearance_is_not_relabelled_adult(self):
        card = dict(age='40', appearance_details='儿童外貌')
        with self.assertRaises(policy.AgePolicyError):
            policy.assert_allowed(cards=[card], settings={'content_tendencies': ['成人向']})

    def test_card_cleaner_preserves_fields(self):
        from cores.authoring import sanitize_card_fields
        card = dict(age='40', build='极瘦', appearance_details='皮包骨，苍白，高颧骨',
                    clothing='旧外套带补丁', hair='干枯长发', voice='沙哑')
        before = copy.deepcopy(card)
        self.assertEqual(sanitize_card_fields(card), before)
        self.assertEqual(card, before)

    def test_extraction_preserves_known_and_unknown_ages(self):
        from cores.authoring import guess_age
        from cores.whole_plan import norm_age
        for age in ('12', '40', '少年', ''):
            self.assertEqual(norm_age(age, '女儿'), age)
            self.assertEqual(guess_age(dict(age=age, identity='成年学生')), age)
        self.assertEqual(guess_age({'name': '小明'}, '小明，40岁，在车站等车'), '40')

    def test_extract_prompt_has_original_story(self):
        from cores.stage1_core import build_extract_user
        text = '12岁的小明与40岁的父亲去公园。'
        result = build_extract_user(text, {})
        self.assertIn(text, result)
        with self.assertRaises(policy.AgePolicyError):
            build_extract_user(text, {'content_tendencies': ['成人向']})

    def test_body_text_keeps_chosen_build(self):
        from cores import authoring, style_presets
        with patch.object(style_presets, 'normalize_body_option', side_effect=lambda sex, x: x), \
             patch.object(style_presets, 'body_def', return_value='成年男性，肩窄，四肢纤细'):
            for age in ('12', '40'):
                result = authoring.body_text(dict(age=age, sex='男', build='极瘦'))
                self.assertIn('极瘦', result)
                self.assertIn('肩窄', result)
                self.assertNotIn('成年男性', result)

    def test_sheet_does_not_change_normal_character(self):
        from cores import authoring, asset_core, prompt_writer
        for age in ('12', '40'):
            card = dict(name='小明', character_id='TEST', age=age, sex='男',
                        build='极瘦', sheet_body_ratio='标准成人', hair='黑色长发',
                        clothing='普通短裤和运动上衣', appearance_details='苍白，高颧骨')
            original = copy.deepcopy(card)
            generated = age + '岁男性，苍白，高颧骨，黑色长发，普通短裤和运动上衣。'
            with patch.object(prompt_writer, 'write_character', return_value=(generated, '')) as write, \
                 patch.object(asset_core, 'get_asset', return_value=None):
                result = authoring.write_char_sheet('TEST', card, {})
            self.assertEqual(result, generated)
            self.assertEqual(card, original)
            sent = write.call_args.args[0]
            self.assertIn(age + '岁', sent)
            self.assertIn('普通短裤和运动上衣', sent)
            self.assertIn('高颧骨', sent)

    def test_minor_sensitive_stops_before_prompt_writer(self):
        from cores import authoring, prompt_writer
        with patch.object(prompt_writer, 'write_character') as write:
            with self.assertRaises(policy.AgePolicyError):
                authoring.write_char_sheet('TEST', dict(age='12'), {'content_tendencies': ['成人向']})
            write.assert_not_called()

    def test_outfit_keeps_explicit_normal_selection(self):
        from cores import project_prompt
        entries = [{'name': '短袖短裤', 'prompt': '普通短袖和运动短裤', 'keywords': []},
                   {'name': '敏感测试款', 'prompt': '成人向服装', 'keywords': []}]
        with patch.object(project_prompt, 'load_outfit_library', return_value={'现代都市': entries}), \
             patch.object(project_prompt, 'resolved_world', return_value='现代都市'):
            card = {'age': '12', 'outfit_preset': '短袖短裤'}
            chosen, _ = project_prompt._choose_outfit_entry(card)
            self.assertEqual(chosen['name'], '短袖短裤')
            card['outfit_preset'] = '敏感测试款'
            with self.assertRaises(policy.AgePolicyError):
                project_prompt._choose_outfit_entry(card)
            self.assertEqual(card['outfit_preset'], '敏感测试款')

    def test_all_model_entry_points_stop_before_model(self):
        from models import qwen_client, krea_client, krea_edit_client, h3_client
        text = '年龄：12；内容尺度：成人向'
        calls = [lambda: qwen_client.chat('', text), lambda: krea_client.generate(text),
                 lambda: krea_edit_client.edit('not-an-image.png', text),
                 lambda: h3_client.generate('t2va', text)]
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaises(policy.AgePolicyError):
                    call()

    def test_no_model_guard_on_normal_requests(self):
        from models import qwen_client, krea_client, krea_edit_client, h3_client
        text = '12岁学生穿校服在教室读书'
        for call in (lambda: qwen_client.chat('', text), lambda: krea_client.generate(text),
                     lambda: krea_edit_client.edit('not-an-image.png', text),
                     lambda: h3_client.generate('t2va', text)):
            with self.assertRaisesRegex(RuntimeError, 'V41_NO_MODEL'):
                call()

    def test_output_cannot_drop_input_age(self):
        with self.assertRaises(policy.AgePolicyError):
            policy.assert_model_response('人物年龄12岁', '内容尺度：成人向')
        policy.assert_model_response('人物年龄12岁', '这个学生在认真阅读。')

    def test_existing_age_selfcheck(self):
        from cores import decisions
        self.assertTrue(decisions._check_d17()[0])

    def test_manual_image_prompt_uses_real_card(self):
        from cores import asset_core, story_core, project_settings
        from models import krea_client
        card = dict(name='测试人物', character_id='TEST', age='12', sex='男')
        with patch.object(asset_core, 'get_asset', return_value=card), \
             patch.object(story_core, 'get_story', return_value={}), \
             patch.object(project_settings, 'get', return_value={}), \
             patch.object(krea_client, 'generate') as generate:
            with self.assertRaises(policy.AgePolicyError):
                asset_core.generate_character_master('TEST', 'TEST', custom_prompt='成年人物，成人向')
            generate.assert_not_called()
        self.assertEqual(card['age'], '12')

    def test_fallback_compiler_keeps_age_with_small_ratio(self):
        from cores.quality_core import compile_character_prompt
        card = dict(name='测试人物', age='40', sex='男', build='标准',
                    sheet_body_ratio='幼态少年', hair='黑色短发', clothing='普通旅行服')
        original = copy.deepcopy(card)
        text = compile_character_prompt(card)
        self.assertIn('40', text)
        self.assertNotIn('幼童般的幻想少年外观', text)
        self.assertNotIn('幼态幻想少年外观', text)
        self.assertEqual(card, original)

    def test_voice_keeps_age_and_invalidates_stale_cache(self):
        from cores import authoring
        for age in ('12', '40'):
            card = dict(age=age, sex='男', voice='低沉沙哑', voice_h3_en='stale', voice_h3_age='19')
            self.assertIn(age + '岁', authoring.h3_voice(card))
            with patch.object(authoring, '_q', side_effect=RuntimeError('offline')):
                voice = authoring.h3_voice_tech(card)
            self.assertIn(age + ' years old', voice)
            self.assertEqual(card['age'], age)

    def test_embedded_entry_points_have_shared_guard_first(self):
        # 只抽取入口函数执行；不导入旧模块、启动后台线程或加载历史任务。
        root = Path(__file__).resolve().parents[1]
        specs = [('_legacy_8848/app.py', 'llama_chat_stream',
                  ([{'role': 'user', 'content': '年龄12岁，成人向'}],)),
                 ('_legacy_8848/app.py', 'comfy_generate', ('年龄12岁，成人向',)),
                 ('_legacy_8848/video_gen_core.py', '_run_task',
                  (None, {'request': {'prompt': '年龄12岁，成人向'}}))]
        for path, name, args in specs:
            tree = ast.parse((root / path).read_text(encoding='utf-8-sig'))
            fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
            module = ast.Module(body=[fn], type_ignores=[])
            env = {'os': os}
            exec(compile(ast.fix_missing_locations(module), path, 'exec'), env)
            with self.assertRaises(policy.AgePolicyError):
                env[name](*args)

    def test_image_number_is_not_age(self):
        policy.assert_model_request('成年人物，成人向，image: 12')
        self.assertEqual(policy.age_number(0), 0)

    def test_backend_registration(self):
        import api
        get_count, post_count = api.load_all()
        self.assertGreater(get_count, 0)
        self.assertGreater(post_count, 100)

    def test_source_parses(self):
        root = Path(__file__).resolve().parents[1]
        for folder in ('cores', 'api', 'models', 'production'):
            for file in (root / folder).glob('*.py'):
                with self.subTest(file=file.name):
                    ast.parse(file.read_text(encoding='utf-8-sig'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
