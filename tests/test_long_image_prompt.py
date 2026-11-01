"""Opt-in long-form Qwen protocols and API boundaries; no model inference."""
import json
import threading
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from api import image_api
from models import anima_client, long_image_prompt, moss_client, moss_prompt, qwen_client


def prose(topic, length):
    sentence = '柔和侧光照亮人物和衣料，肩部与手掌形成自然接触，庭院石墙呈现细腻纹理。'
    return (topic + '。' + sentence * (length // len(sentence) + 1))[:length]


def natural_plan():
    return {
        'shot': {key: prose('内部拍摄方案专用标识', 40) for key in
                 ('scene_and_time', 'framing', 'action_and_gaze', 'support_and_contact', 'lighting')},
        'paragraphs': [prose('庭院写真第' + str(n + 1) + '段', 200) for n in range(10)],
    }


def character_plan():
    return {
        'identity': '原创成年女调查员',
        'style': prose('真人影视定妆摄影', 60),
        'body': prose('原设匀称体型与自然站姿', 140),
        'hair': prose('深棕及肩短发与清楚的后脑轮廓', 140),
        'face': prose('棕色眼睛与清晰下颌，神态自然平稳', 240),
        'outfit': {
            'upper': prose('靛蓝提花短外套与银色袖扣', 240),
            'collar': prose('米白圆领内搭', 120),
            'waist': prose('深棕皮腰带与哑光金属扣', 120),
            'lower': prose('深灰直筒长裤与清楚裤缝', 200),
            'footwear': prose('黑色系带短靴与皮革纹理', 140),
        },
        'accessories': ['左腕佩戴银色手表'],
        'background': prose('浅灰背景，左上方大面积柔光形成清楚明暗转折，暗侧保留织纹和体积', 200),
    }


def marker(model='moss', sheet=False):
    return '【GPT长文：' + ('Moss' if model == 'moss' else 'Krea2') + '：' + (
        '精细人设' if sheet else '自然单图') + '】\n依据用户需求展开一套完整设计。'


class LongImagePromptTests(unittest.TestCase):
    def guards(self, plan=None, finish='stop'):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.local = stack.enter_context(patch.object(moss_client, 'local_guard'))
        self.resources = stack.enter_context(patch.object(moss_client, '_resource_guard'))
        self.idle = stack.enter_context(patch.object(anima_client, 'idle_guard'))
        self.chat = stack.enter_context(patch.object(qwen_client, 'chat', return_value=(
            json.dumps(plan if plan is not None else natural_plan(), ensure_ascii=False),
            {'finish_reason': finish})))

    def test_only_new_first_line_markers_select_long_writer(self):
        for old in ('【GPT优化指令词：Moss独立】', '【GPT优化指令词：Krea2】',
                    '【Moss独立：人物人设图】', '普通规则\n' + marker(), None, ''):
            with self.subTest(old=old):
                self.assertIsNone(long_image_prompt.profile(old))
        self.assertEqual(long_image_prompt.profile(marker()), {'model': 'moss', 'sheet': False})
        self.assertEqual(long_image_prompt.profile(marker('krea2', True)),
                         {'model': 'krea2', 'sheet': True})

    def test_model_mismatch_and_old_marker_fail_before_model_call(self):
        self.guards()
        for system, model in ((marker(), 'krea2'), (marker('krea2'), 'moss'),
                              ('【GPT优化指令词：Krea2】', 'krea2')):
            with self.subTest(model=model), self.assertRaisesRegex(ValueError, '当前模型'):
                long_image_prompt.write_prompt('庭院写真', system, model)
        self.chat.assert_not_called()
        self.local.assert_not_called()

    def test_existing_moss_preset_stays_on_original_writer(self):
        self.guards({'caption': '清晨海边的红色灯塔。'})
        with patch.object(long_image_prompt, 'write_prompt', side_effect=AssertionError('old route')):
            result = moss_prompt.write_prompt('海边灯塔', '【GPT优化指令词：Moss独立】规则', 1024, 768)
        self.assertEqual(result['prompt'], '清晨海边的红色灯塔。')
        self.assertEqual(self.chat.call_args.kwargs['max_tokens'], 4096)

    def test_moss_new_marker_forwards_canvas_and_validation_to_long_writer(self):
        validate = lambda prompt: None
        result = {'model': 'moss', 'prompt': '海边灯塔', 'format': 'caption'}
        with patch.object(long_image_prompt, 'write_prompt', return_value=result) as writer:
            actual = moss_prompt.write_prompt('海边灯塔', marker(), 1440, 1920, upscale=2,
                                             output_format='caption', validate_prompt=validate)
        self.assertEqual(actual, result)
        writer.assert_called_once_with('海边灯塔', marker(), 'moss', base_w=1440,
            base_h=1920, upscale=2, output_format='caption', validate_prompt=validate)

    def test_both_models_join_only_final_paragraphs_with_full_output_budget(self):
        plan = natural_plan()
        self.guards(plan)
        for model in ('moss', 'krea2'):
            with self.subTest(model=model):
                self.chat.reset_mock()
                result = long_image_prompt.write_prompt('中式庭院成年女性写真', marker(model), model,
                                                       base_w=1024, base_h=768)
                self.assertEqual(result['model'], model)
                self.assertEqual(result['format'], 'caption')
                self.assertEqual(result['prompt'], '\n\n'.join(plan['paragraphs']))
                self.assertNotIn('内部拍摄方案专用标识', result['prompt'])
                self.assertNotIn('scene_and_time', result['prompt'])
                self.chat.assert_called_once()
                options = self.chat.call_args.kwargs
                self.assertEqual(options['max_tokens'], 8192)
                self.assertTrue(options['check_context'])
                self.assertTrue(options['return_meta'])
                self.assertFalse(options['reasoning'])
                self.assertEqual(options['response_format']['type'], 'json_schema')
                self.assertTrue(options['response_format']['json_schema']['strict'])

    def test_moss_sheet_uses_four_shared_views_and_directional_light(self):
        plan = character_plan()
        self.guards(plan)
        result = long_image_prompt.write_prompt('原创成年女调查员人设图', marker(sheet=True),
                                               'moss', base_w=1360, base_h=768)
        self.assertEqual(result['format'], 'layout')
        board = json.loads(result['prompt'])
        self.assertEqual(len(board['items']), 4)
        summary = board['summary']['caption']
        self.assertIn('靛蓝提花短外套', summary)
        self.assertIn('暗侧保留织纹和体积', summary)
        self.assertNotIn('柔和均匀照明', summary)
        self.assertIn('正面站立全身', board['items'][0]['caption'])
        self.assertIn('90度侧面站立全身', board['items'][1]['caption'])
        self.assertIn('背面站立全身', board['items'][2]['caption'])
        portrait = board['items'][3]['caption']
        self.assertIn('正脸头肩特写', portrait)
        self.assertIn('米白圆领内搭', portrait)
        self.assertNotIn('靛蓝提花短外套', portrait)
        self.assertNotIn('黑色系带短靴', portrait)

    def test_krea_sheet_is_plain_chinese_prose_with_shared_views(self):
        self.guards(character_plan())
        result = long_image_prompt.write_prompt('原创成年女调查员人设图', marker('krea2', True), 'krea2')
        self.assertEqual(result['format'], 'caption')
        prompt = result['prompt']
        self.assertFalse(prompt.lstrip().startswith('{'))
        self.assertNotIn('bbox', prompt)
        self.assertNotIn('柔和均匀照明', prompt)
        self.assertIn('朝右90度纯侧面全身', prompt)
        self.assertIn('暗侧保留织纹和体积', prompt)
        portrait = prompt.rsplit('\n\n', 1)[-1]
        self.assertIn('正脸头肩特写', portrait)
        self.assertNotIn('靛蓝提花短外套', portrait)
        self.assertNotIn('黑色系带短靴', portrait)

    def test_cover_keeps_literal_words_and_compiles_real_pixel_box(self):
        plan = natural_plan()
        plan['items'] = [
            {'label': 'text', 'caption': '左上角亮黄色标题',
             'text': 'Street\nscenes', 'bbox': [40, 80, 440, 160]},
            {'label': 'text', 'caption': '标题下方白色小字',
             'text': 'HONG KONG', 'bbox': [40, 176, 440, 240]},
        ]
        self.guards(plan)
        result = long_image_prompt.write_prompt('香港杂志封面，文字Street和scenes及HONG KONG',
                                               marker(), 'moss', base_w=1024, base_h=768)
        self.assertEqual(result['format'], 'layout')
        board = json.loads(result['prompt'])
        self.assertEqual(board['summary']['caption'], '\n\n'.join(plan['paragraphs']))
        self.assertEqual([item['text'] for item in board['items']], ['Street\nscenes', 'HONG KONG'])
        self.assertEqual(board['items'][0]['bbox'], [-304, -472, -224, -72])
        self.assertEqual(board['bbox_coord_space'], 'abs_centralized')
        self.assertEqual(board['bbox_format'], 'yxyx')

    def test_out_of_canvas_layout_is_rejected_after_one_repair(self):
        plan = natural_plan()
        plan['items'] = [{'label': 'text', 'caption': '标题', 'text': 'Street',
                          'bbox': [0, 0, 1025, 200]}]
        self.guards(plan)
        with self.assertRaisesRegex(ValueError, '超出画布'):
            long_image_prompt.write_prompt('香港封面', marker(), 'moss', base_w=1024, base_h=768)
        self.assertEqual(self.chat.call_count, 2)

    def test_truncated_response_fails_without_short_prompt_fallback(self):
        self.guards(natural_plan(), finish='length')
        with self.assertRaisesRegex(ValueError, '未完整结束'):
            long_image_prompt.write_prompt('庭院写真', marker(), 'moss')
        self.assertEqual(self.chat.call_count, 2)
        self.assertEqual(self.local.call_count, 2)
        self.assertEqual(self.resources.call_count, 2)
        self.assertEqual(self.idle.call_count, 2)

    def test_short_paragraphs_fail_instead_of_returning_old_short_words(self):
        plan = natural_plan()
        plan['paragraphs'][3] = '庭院侧脸写真。'
        self.guards(plan)
        with self.assertRaisesRegex(ValueError, '正文长度'):
            long_image_prompt.write_prompt('庭院写真', marker('krea2'), 'krea2')
        self.assertEqual(self.chat.call_count, 2)
        repair = json.loads(self.chat.call_args.args[1])['修复']
        self.assertIn('正文长度', repair['错误'])
        self.assertEqual(json.loads(repair['上一版']), plan)

    def test_one_repair_can_return_a_complete_long_prompt(self):
        plan = natural_plan()
        self.guards()
        self.chat.side_effect = [
            (json.dumps({'caption': '短句'}, ensure_ascii=False), {'finish_reason': 'stop'}),
            (json.dumps(plan, ensure_ascii=False), {'finish_reason': 'eos'}),
        ]
        result = long_image_prompt.write_prompt('庭院写真', marker(), 'moss')
        self.assertEqual(result['prompt'], '\n\n'.join(plan['paragraphs']))
        self.assertEqual(self.chat.call_count, 2)

    def test_content_validation_failure_is_not_silently_accepted(self):
        self.guards()
        with self.assertRaisesRegex(ValueError, '场景校验失败'):
            long_image_prompt.write_prompt('庭院写真', marker(), 'moss',
                validate_prompt=lambda prompt: (_ for _ in ()).throw(ValueError('场景校验失败')))
        self.assertEqual(self.chat.call_count, 2)

    def test_busy_guard_stops_before_qwen_and_without_format_repair(self):
        self.guards()
        self.resources.side_effect = RuntimeError('资源忙碌')
        with self.assertRaisesRegex(RuntimeError, '资源忙碌'):
            long_image_prompt.write_prompt('庭院写真', marker('krea2'), 'krea2')
        self.chat.assert_not_called()
        self.resources.assert_called_once()

    def test_invalid_format_and_noncallable_validator_fail_before_qwen(self):
        self.guards()
        for options in ({'output_format': 'unknown'}, {'validate_prompt': 'not-callable'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                long_image_prompt.write_prompt('庭院写真', marker(), 'moss', **options)
        self.chat.assert_not_called()
        self.local.assert_not_called()

    def test_moss_standard_sheet_can_explicitly_request_plain_caption(self):
        self.guards(character_plan())
        result = long_image_prompt.write_prompt('原创成年女调查员人设图', marker(sheet=True),
                                               'moss', output_format='caption')
        self.assertEqual(result['format'], 'caption')
        self.assertFalse(result['prompt'].startswith('{'))
        self.assertIn('正脸头肩特写', result['prompt'])
        self.assertNotIn('bbox', result['prompt'])

    def test_custom_moss_preserves_explicit_caption_and_requested_view(self):
        expected = {'model': 'moss', 'format': 'caption', 'prompt': '成年女调查员正面定妆照。'}
        with patch.object(moss_prompt, 'write_prompt', return_value=expected) as writer:
            result = long_image_prompt.write_prompt('只要正面一张人设图', marker(sheet=True),
                                                   'moss', output_format='caption')
        self.assertEqual(result, expected)
        self.assertEqual(writer.call_args.args[0], '只要正面一张人设图')
        self.assertEqual(writer.call_args.kwargs['output_format'], 'caption')

    def test_custom_krea_passes_content_through_external_validation(self):
        self.guards({'caption': '成年女调查员坐在木椅上的正面定妆照。'})
        seen = []

        def reject(prompt):
            seen.append(prompt)
            raise ValueError('自定义内容未满足要求')

        with self.assertRaisesRegex(ValueError, '自定义内容未满足要求'):
            long_image_prompt.write_prompt('坐姿人设图', marker('krea2', True), 'krea2',
                                           validate_prompt=reject)
        self.assertEqual(seen, ['成年女调查员坐在木椅上的正面定妆照。'])
        self.chat.assert_called_once()

    def test_custom_krea_rejects_layout_masquerading_as_plain_caption(self):
        plan = {'width': 1360, 'height': 768, 'bbox_coord_space': 'abs', 'bbox_format': 'xyxy',
                'summary': {'caption': '成年女调查员定妆照。'},
                'items': [{'label': 'character', 'caption': '人物坐在木椅上。',
                           'bbox': [40, 40, 900, 720]}]}
        self.guards(plan)
        with self.assertRaisesRegex(ValueError, 'Krea2自定义人设须返回完整中文正文'):
            long_image_prompt.write_prompt('坐姿人设图', marker('krea2', True), 'krea2')
        self.chat.assert_called_once()

    def test_pseudo_json_in_collar_is_rejected_before_portrait_compilation(self):
        plan = character_plan()
        plan['outfit']['collar'] += '\", \"waist\": \"深棕皮腰带\", \"lower\": \"深灰长裤\"'
        self.guards(plan)
        self.chat.side_effect = [
            (json.dumps(plan, ensure_ascii=False), {'finish_reason': 'stop'}),
            (json.dumps({'field_0': plan['outfit']['collar']}, ensure_ascii=False),
             {'finish_reason': 'stop'}),
        ]
        with patch('cores.moss_sheet.compile_sheet') as compiler:
            with self.assertRaisesRegex(ValueError, 'JSON字段语法'):
                long_image_prompt.write_prompt('原创成年女调查员人设图', marker('krea2', True), 'krea2')
        compiler.assert_not_called()
        self.assertEqual(self.chat.call_count, 2)

    def test_alternative_plan_repairs_once_and_preserves_quoted_visible_copy(self):
        quoted = '“今晚可能有雨或风。”'
        good = natural_plan()
        good['paragraphs'][0] = prose('木桌上的纸条写着' + quoted, 200)
        bad = natural_plan()
        bad['paragraphs'][0] = good['paragraphs'][0]
        bad['paragraphs'][1] = prose('主光来自左侧窗户或右侧台灯', 200)
        self.guards()
        self.chat.side_effect = [
            (json.dumps(bad, ensure_ascii=False), {'finish_reason': 'stop'}),
            (json.dumps({'field_0': good['paragraphs'][1]}, ensure_ascii=False),
             {'finish_reason': 'stop'}),
        ]
        result = long_image_prompt.write_prompt('成年女性在窗边阅读，纸条文字为' + quoted,
                                               marker(), 'moss')
        self.assertEqual(result['prompt'], '\n\n'.join(good['paragraphs']))
        self.assertIn(quoted, result['prompt'])
        self.assertNotIn('主光来自左侧窗户或右侧台灯', result['prompt'])
        self.assertEqual(self.chat.call_count, 2)
        repair = json.loads(self.chat.call_args.args[1])['待修字段']['field_0']
        self.assertIn('备选方案', repair['修复要求'])
        self.assertEqual(repair['path'], ['paragraphs', 1])

    def test_local_repair_preserves_good_paragraphs_and_requests_only_bad_fields(self):
        plan = natural_plan()
        original = list(plan['paragraphs'])
        plan['paragraphs'][2] = prose('窗边主光来自左侧或右侧', 200)
        plan['paragraphs'][7] = prose('人物身旁可能摆放木椅', 200)
        fixed = {
            'field_0': prose('窗边主光从左侧照亮人物右颊', 200),
            'field_1': prose('人物身旁摆放一张深棕色木椅', 200),
        }
        self.guards()
        self.chat.side_effect = [
            (json.dumps(plan, ensure_ascii=False), {'finish_reason': 'stop'}),
            (json.dumps(fixed, ensure_ascii=False), {'finish_reason': 'stop'}),
        ]
        result = long_image_prompt.write_prompt('成年女性在书店窗边阅读', marker('krea2'), 'krea2')
        paragraphs = result['prompt'].split('\n\n')
        self.assertEqual(len(paragraphs), len(original))
        for index, paragraph in enumerate(paragraphs):
            with self.subTest(index=index):
                self.assertEqual(paragraph, fixed[{2: 'field_0', 7: 'field_1'}[index]]
                                 if index in (2, 7) else original[index])
        self.assertEqual(self.chat.call_count, 2)
        repair_call = self.chat.call_args
        schema = repair_call.kwargs['response_format']['json_schema']['schema']
        self.assertEqual(set(schema['properties']), {'field_0', 'field_1'})
        self.assertEqual(set(schema['required']), {'field_0', 'field_1'})
        self.assertFalse(schema['additionalProperties'])
        for value in schema['properties'].values():
            self.assertEqual(value['type'], 'string')
        targets = json.loads(repair_call.args[1])['待修字段']
        self.assertEqual(set(targets), {'field_0', 'field_1'})
        self.assertEqual(targets['field_0']['path'], ['paragraphs', 2])
        self.assertEqual(targets['field_1']['path'], ['paragraphs', 7])
        self.assertEqual(targets['field_0']['原文'], plan['paragraphs'][2])
        self.assertEqual(targets['field_1']['原文'], plan['paragraphs'][7])

    def test_new_height_is_rejected_but_explicit_user_height_is_allowed(self):
        plan = character_plan()
        plan['body'] = '身高165厘米，头身比1:7.5，匀称体态，肩颈自然放松。'
        with self.assertRaisesRegex(ValueError, '未给定具体身高'):
            long_image_prompt._concrete_descriptions(plan, True, '原创成年女调查员，匀称体态')
        long_image_prompt._concrete_descriptions(
            plan, True, '原创成年女调查员，匀称体态，身高165厘米，头身比1:7.5')

    def test_presentation_cleanup_preserves_design_light_and_literal_copy(self):
        quoted = '“没有明显噪点或模糊”'
        text = ('无袖上衣有两排盘扣，增加两排盘扣与腰带。'
                '青石板受左窗光照亮，增强了空间纵深感。纸条写着' + quoted + '。')
        cleaned = long_image_prompt._clean_presentation(text)
        self.assertIn('无袖上衣有两排盘扣，增加两排盘扣与腰带。', cleaned)
        self.assertIn('青石板受左窗光照亮', cleaned)
        self.assertIn('纸条写着' + quoted, cleaned)
        self.assertNotIn('增强了空间纵深感', cleaned)
        unquoted = long_image_prompt._clean_presentation('青石板受左窗光照亮。没有明显噪点或模糊。')
        self.assertIn('青石板受左窗光照亮', unquoted)
        self.assertNotIn('没有明显噪点或模糊', unquoted)


class KreaLongPromptApiTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(image_api, '_KREA_PROMPT_LOCK', threading.Lock())
        guard.start()
        self.addCleanup(guard.stop)

    def test_success_passes_only_opt_in_model_and_returns_prompt(self):
        data = {'model': 'krea2', 'format': 'caption', 'prompt': '完整中文长文'}
        with patch.object(long_image_prompt, 'write_prompt', return_value=data) as writer:
            result = image_api.krea2_long_prompt(None, '', {'text': '庭院写真', 'system': marker('krea2')})
        self.assertEqual(result, {'ok': True, 'data': data})
        writer.assert_called_once_with('庭院写真', marker('krea2'), 'krea2')
        self.assertTrue(image_api._KREA_PROMPT_LOCK.acquire(blocking=False))
        image_api._KREA_PROMPT_LOCK.release()

    def test_busy_returns_conflict_without_call_and_keeps_owner_lock(self):
        image_api._KREA_PROMPT_LOCK.acquire()
        with patch.object(long_image_prompt, 'write_prompt') as writer:
            result = image_api.krea2_long_prompt(None, '', {'text': '庭院写真', 'system': marker('krea2')})
        self.assertFalse(result['ok'])
        self.assertEqual(result['_status'], 409)
        writer.assert_not_called()
        self.assertFalse(image_api._KREA_PROMPT_LOCK.acquire(blocking=False))
        image_api._KREA_PROMPT_LOCK.release()

    def test_failure_and_bad_payload_return_error_and_release_own_lock(self):
        with patch.object(long_image_prompt, 'write_prompt', side_effect=ValueError('长度不足')):
            result = image_api.krea2_long_prompt(None, '', {'text': '庭院写真', 'system': marker('krea2')})
        self.assertFalse(result['ok'])
        self.assertEqual(result['_status'], 400)
        self.assertIn('长度不足', result['error'])
        self.assertTrue(image_api._KREA_PROMPT_LOCK.acquire(blocking=False))
        image_api._KREA_PROMPT_LOCK.release()
        result = image_api.krea2_long_prompt(None, '', [])
        self.assertFalse(result['ok'])
        self.assertEqual(result['_status'], 400)


if __name__ == '__main__':
    unittest.main()
