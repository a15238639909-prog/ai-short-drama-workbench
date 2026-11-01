"""Local MOSS planning protocol, queue guards and API isolation (no models)."""
import copy
import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from api import image_api
from cores.moss_prompt_format import compile_draft, parse_draft, rescale_compiled
from models import anima_client, moss_client, moss_prompt, qwen_client


def layout():
    return {'width': 1024, 'height': 768, 'bbox_coord_space': 'abs', 'bbox_format': 'xyxy',
            'summary': {'caption': '手绘电影海报', 'canvas': 'single'},
            'items': [{'label': 'text', 'caption': '顶部红色标题',
                       'bbox': [100.2, 20.5, 920.1, 150.1], 'text': '回家\n今晚见！'},
                      {'label': 'character', 'caption': '红衣女孩侧身走向灯塔',
                       'bbox': [320, 180, 700, 768]}]}


class MossPromptFormatTests(unittest.TestCase):
    def test_caption_is_plain_text(self):
        self.assertEqual(compile_draft({'caption': ' 清晨窗边的茶杯。 '}, 1024, 768),
                         ('清晨窗边的茶杯。', 'caption'))

    def test_layout_compiles_pixel_edges_and_literal_words(self):
        draft = layout()
        before = copy.deepcopy(draft)
        prompt, kind = compile_draft(draft, 1024, 768)
        self.assertEqual(kind, 'layout')
        self.assertNotIn('\n', prompt)
        compiled = json.loads(prompt)
        self.assertEqual(compiled['items'][0]['bbox'], [-364, -412, -233, 409])
        self.assertEqual(compiled['items'][0]['text'], '回家\n今晚见！')
        self.assertEqual(compiled['bbox_format'], 'yxyx')
        self.assertNotIn('width', compiled)
        self.assertEqual(draft, before)

    def test_reject_bad_bounds_wrong_canvas_and_nested_items(self):
        cases = []
        for box in ([-1, 0, 200, 200], [0, 0, 1025, 300], [1, 1, 1, 20],
                    [True, 0, 200, 200], [0, 0, float('inf'), 200]):
            draft = layout()
            draft['items'][0]['bbox'] = box
            cases.append(draft)
        draft = layout()
        draft['width'] = 1008
        cases.append(draft)
        draft = layout()
        draft['items'] = [[draft['items'][0]]]
        cases.append(draft)
        draft = layout()
        del draft['items'][0]['text']
        cases.append(draft)
        for draft in cases:
            with self.subTest(draft=draft), self.assertRaises(ValueError):
                compile_draft(draft, 1024, 768)

    def test_json_duplicate_keys_nonfinite_and_extra_fields_rejected(self):
        for text in ('{"caption":"a","caption":"b"}', '{"caption":NaN}',
                     '{"caption":"a"} trailing'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_draft(text)
        with self.assertRaises(ValueError):
            compile_draft({'caption': '茶杯', 'negative': '模糊'}, 1024, 768)
        self.assertEqual(parse_draft('```json\n{"caption":"茶杯"}\n```'), {'caption': '茶杯'})

    def test_second_stage_layout_keeps_words_and_relative_position(self):
        original, _ = compile_draft(layout(), 1024, 768)
        final = json.loads(rescale_compiled(original, 1024, 768, 1232, 928))
        first = json.loads(original)
        self.assertEqual(final['items'][0]['text'], first['items'][0]['text'])
        self.assertEqual(final['items'][1]['bbox'], [-247, -231, 464, 227])
        self.assertEqual(rescale_compiled('一张普通照片', 1024, 768, 1232, 928), '一张普通照片')
        self.assertEqual(rescale_compiled('{"other":"原样"}', 1024, 768, 1232, 928), '{"other":"原样"}')

    def test_workflow_scales_only_refine_conditioning_and_keeps_sampling(self):
        prompt, _ = compile_draft(layout(), 1024, 768)
        graph = moss_client.build_workflow(prompt, 1024, 768, seed=17)
        self.assertEqual(graph['129']['inputs']['text'], prompt)
        self.assertEqual(graph['120']['inputs']['positive'], ['129', 0])
        self.assertEqual(graph['122']['inputs']['positive'], ['moss_layout_refine', 0])
        self.assertEqual(graph['moss_layout_refine']['inputs']['text'],
                         rescale_compiled(prompt, 1024, 768, 1232, 928))
        ordinary = moss_client.build_workflow('海边灯塔', 1024, 768, seed=17)
        for node in ('120', '121', '122', '130', '158'):
            expected = copy.deepcopy(ordinary[node])
            if node == '122':
                expected['inputs']['positive'] = ['moss_layout_refine', 0]
            self.assertEqual(graph[node], expected)
        for value in (v for node in graph.values() for v in node['inputs'].values() if isinstance(v, list)):
            self.assertIn(value[0], graph)

    def test_two_times_layout_refinement_uses_final_canvas(self):
        prompt, _ = compile_draft(layout(), 1024, 768)
        graph = moss_client.build_workflow(prompt, 1024, 768, seed=17, upscale=2)
        self.assertEqual(graph['129']['inputs']['text'], prompt)
        self.assertEqual(graph['121']['inputs']['scale_by'], 2)
        self.assertEqual(graph['moss_layout_refine']['inputs']['text'],
                         rescale_compiled(prompt, 1024, 768, 2048, 1536))


class MossPromptWriterTests(unittest.TestCase):
    def guards(self, response=None):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.local = stack.enter_context(patch.object(moss_client, 'local_guard'))
        self.resources = stack.enter_context(patch.object(moss_client, '_resource_guard'))
        self.idle = stack.enter_context(patch.object(anima_client, 'idle_guard'))
        self.chat = stack.enter_context(patch.object(qwen_client, 'chat', return_value=response or
            ('{"caption":"清晨海边，红色灯塔，水彩笔触。"}', {'finish_reason': 'stop'})))

    def test_chinese_local_qwen_contract_and_real_dimensions(self):
        self.guards()
        result = moss_prompt.write_prompt('  海边灯塔  ', 'Moss专用规则', base_w=1024, base_h=768)
        self.assertEqual(result, {'model': 'moss', 'prompt': '清晨海边，红色灯塔，水彩笔触。',
                                 'format': 'caption', 'base_w': 1024, 'base_h': 768})
        self.local.assert_called_once()
        self.resources.assert_called_once()
        self.idle.assert_called_once()
        args, kwargs = self.chat.call_args
        self.assertTrue(args[0].startswith('Moss专用规则'))
        self.assertEqual(json.loads(args[1])['实际出底宽'], 1024)
        self.assertEqual(kwargs['response_format'], {'type': 'json_object'})
        self.assertTrue(kwargs['check_context'])
        self.assertFalse(kwargs['reasoning'])
        self.assertNotIn('"items"', args[0])

    def test_format_selection_follows_user_task_or_sheet_preset(self):
        self.assertFalse(moss_prompt.layout_needed('雨后灯塔前的红衣探险家，手持钥匙', 'GPT规则'))
        for text in ('带标题的电影海报', '超级赛亚人孙悟空人设图', '四格漫画', 'character sheet'):
            self.assertTrue(moss_prompt.layout_needed(text, 'GPT规则'))
        self.assertTrue(moss_prompt.layout_needed('七龙珠 人造人18号', '【Moss独立：人物人设图】规则'))

    def test_format_selection_reads_json_values_instead_of_internal_keys(self):
        request = json.dumps({'场景卡': {'layout': '入口在左侧，高窗沿右墙排列',
                                      'poster': '', '人设图': {}, 'space': '城堡长廊'}},
                             ensure_ascii=False)
        self.assertFalse(moss_prompt.layout_needed(request, '【Moss独立：场景图】规则'))
        for value in ('四格漫画', '带标题的电影海报', 'character sheet'):
            request = json.dumps({'需求': {'内容': [value]}}, ensure_ascii=False)
            self.assertTrue(moss_prompt.layout_needed(request, 'Moss规则'))

    def test_explicit_caption_overrides_incidental_layout_words_and_sheet_preset(self):
        self.guards()
        with patch.object(moss_prompt, 'sheet_identities', side_effect=AssertionError('caption is not a sheet')):
            result = moss_prompt.write_prompt(
                '{"layout":"墙上悬挂漫画海报"}', '【Moss独立：人物人设图】规则',
                1024, 768, output_format='caption')
        self.assertEqual(result['format'], 'caption')
        self.assertNotIn('"items"', self.chat.call_args.args[0])
        self.chat.assert_called_once()

    def test_explicit_layout_can_plan_a_scene_without_keyword_guessing(self):
        self.guards((json.dumps(layout(), ensure_ascii=False), {'finish_reason': 'stop'}))
        result = moss_prompt.write_prompt('灯塔与海岸', 'Moss规则', 1024, 768, output_format='layout')
        self.assertEqual(result['format'], 'layout')
        self.assertIn('"items"', self.chat.call_args.args[0])

    def test_explicit_4k_dimensions_are_planned_at_the_base_canvas(self):
        self.guards(('{"caption":"窗边写真"}', {'finish_reason': 'stop'}))
        result = moss_prompt.write_prompt('成年女子窗边写真', 'Moss规则', 1440, 1920, upscale=2)
        self.assertEqual((result['base_w'], result['base_h']), (1440, 1920))
        self.assertEqual(json.loads(self.chat.call_args.args[1])['实际出底高'], 1920)
        self.chat.reset_mock()
        with self.assertRaises(ValueError):
            moss_prompt.write_prompt('成年女子窗边写真', 'Moss规则', 1440, 1920)
        self.chat.assert_not_called()

    def test_api_forwards_explicit_upscale_to_planner(self):
        with patch.object(moss_prompt, 'write_prompt', return_value={'model': 'moss'}) as writer:
            response = image_api.moss_prompt(None, '', {'text': '写真', 'system': '规则',
                'base_w': 1440, 'base_h': 1920, 'upscale': 2})
        self.assertTrue(response['ok'])
        writer.assert_called_once_with('写真', '规则', base_w=1440, base_h=1920, upscale=2)

    def test_standard_sheet_compiles_one_shared_design(self):
        plan = {'identity': '原创成年女剑客', 'style': '彩色手绘', 'body': '成年修长体型',
                'hair': '深棕色长发', 'face': '棕色眼睛', 'background': '浅灰背景',
                'outfit': {'upper': '蓝色上装', 'collar': '白色圆领', 'waist': '棕色腰带',
                           'lower': '灰色长裤', 'footwear': '黑色短靴'}, 'accessories': []}
        self.guards((json.dumps(plan, ensure_ascii=False), {'finish_reason': 'stop'}))
        result = moss_prompt.write_prompt('原创成年女剑客', '【Moss独立：人物人设图】规则', 1360, 768)
        self.assertEqual(result['format'], 'layout')
        compiled = json.loads(result['prompt'])
        self.assertEqual(len(compiled['items']), 4)
        self.assertIn('蓝色上装', compiled['summary']['caption'])
        self.assertNotIn('黑色短靴', compiled['items'][3]['caption'])
        self.assertIn('一份全图共用', self.chat.call_args.args[0])
        self.assertNotIn('"items"', self.chat.call_args.args[0])

    def test_custom_sheet_views_keep_free_layout_contract(self):
        instruction = '【Moss独立：人物人设图】规则'
        self.assertTrue(moss_prompt.standard_sheet_requested('三视图加正脸特写', instruction))
        for text in ('只要三视图', '五视图', '坐姿人设图', '四面全身',
                     '只要正面一张', '三视图，不含面部特写', '两名成年人物的人设图'):
            self.assertFalse(moss_prompt.standard_sheet_requested(text, instruction))
        self.assertFalse(moss_prompt.standard_sheet_requested('人设图', '用户自定义规则'))

    def test_named_sheet_preserves_request_instead_of_invented_costume(self):
        from tests.test_moss_sheet import plan
        design = plan()
        design['outfit']['upper'] = '错误的紫色礼服'
        self.guards((json.dumps(design, ensure_ascii=False), {'finish_reason': 'stop'}))
        request = '孙悟空，超级赛亚人，穿宇航服，真人画风'
        with patch.object(moss_prompt, 'sheet_identities', return_value=[{'trigger': 'son goku, dragon ball'}]):
            result = moss_prompt.write_prompt(request, '【Moss独立：人物人设图】规则', 1360, 768)
        self.assertIn(request, result['prompt'])
        self.assertIn('son goku, dragon ball', result['prompt'])
        self.assertNotIn('错误的紫色礼服', result['prompt'])

    def test_layout_has_no_fallback_to_krea_writer(self):
        self.guards((json.dumps(layout(), ensure_ascii=False), {'finish_reason': 'stop'}))
        result = moss_prompt.write_prompt('标题“回家”的海报', 'Moss布局', 1024, 768)
        self.assertEqual(result['format'], 'layout')
        self.assertEqual(json.loads(result['prompt'])['items'][0]['text'], '回家\n今晚见！')
        self.chat.assert_called_once()

    def test_bad_input_and_guard_failure_prevent_model_call(self):
        self.guards()
        for text in ('', None, 123):
            with self.subTest(text=text), self.assertRaises(ValueError):
                moss_prompt.write_prompt(text, '规则')
        self.chat.assert_not_called()
        self.local.side_effect = RuntimeError('PUBG正在运行')
        with self.assertRaisesRegex(RuntimeError, 'PUBG'):
            moss_prompt.write_prompt('灯塔', '规则')
        self.chat.assert_not_called()

    def test_bad_explicit_format_prevents_guards_and_model_call(self):
        self.guards()
        for mode in ('json', '', None, []):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, '格式选项'):
                moss_prompt.write_prompt('灯塔', '规则', output_format=mode)
        self.local.assert_not_called()
        self.chat.assert_not_called()

    def test_truncated_or_invalid_response_gets_one_repair_then_stops(self):
        self.guards()
        for response in (('{"caption":"截断', {'finish_reason': 'length'}),
                         ('{"caption":"截断', {'finish_reason': 'stop'})):
            self.chat.reset_mock()
            self.chat.return_value = response
            with self.subTest(response=response), self.assertRaises(ValueError):
                moss_prompt.write_prompt('灯塔', '规则')
            self.assertEqual(self.chat.call_count, 2)

    def test_layout_empty_object_text_repairs_without_deleting_real_title(self):
        broken = layout()
        broken['items'][1]['text'] = ''
        self.guards()
        self.chat.side_effect = [
            (json.dumps(broken, ensure_ascii=False), {'finish_reason': 'stop'}),
            (json.dumps(layout(), ensure_ascii=False), {'finish_reason': 'stop'})]
        request = '灯塔电影海报，标题逐字为“回家\n今晚见！”'
        result = moss_prompt.write_prompt(request, 'Moss规则', 1024, 768)
        self.assertEqual(json.loads(result['prompt'])['items'][0]['text'], '回家\n今晚见！')
        self.assertEqual((self.local.call_count, self.resources.call_count, self.idle.call_count), (2, 2, 2))
        repair_user = json.loads(self.chat.call_args.args[1])
        self.assertEqual(repair_user['需求'], request)
        self.assertEqual(json.loads(repair_user['格式修复']['上一版']), broken)
        self.assertIn('item.text须为非空文字', repair_user['格式修复']['错误'])
        self.assertIn('一次格式修复', self.chat.call_args.args[0])

    def test_empty_actual_title_is_rejected_after_the_bounded_repair(self):
        broken = layout()
        broken['items'][0]['text'] = ''
        self.guards((json.dumps(broken, ensure_ascii=False), {'finish_reason': 'stop'}))
        with self.assertRaisesRegex(ValueError, 'item.text须为非空文字'):
            moss_prompt.write_prompt('标题“回家”的海报', 'Moss规则', 1024, 768)
        self.assertEqual(self.chat.call_count, 2)

    def test_repair_can_correct_an_unrequested_layout_to_caption(self):
        self.guards()
        self.chat.side_effect = [
            (json.dumps(layout(), ensure_ascii=False), {'finish_reason': 'stop'}),
            ('{"caption":"明亮的城堡长廊，完整石材与雕花木门。"}', {'finish_reason': 'stop'})]
        result = moss_prompt.write_prompt('城堡长廊', 'Moss规则', 1024, 768, output_format='caption')
        self.assertEqual(result['format'], 'caption')
        self.assertIn('未按本次画面格式输出', json.loads(self.chat.call_args.args[1])['格式修复']['错误'])

    def test_repair_can_complete_a_truncated_response(self):
        self.guards()
        self.chat.side_effect = [
            ('{"caption":"清晨海边', {'finish_reason': 'length'}),
            ('{"caption":"清晨海边的红色灯塔。"}', {'finish_reason': 'stop'})]
        result = moss_prompt.write_prompt('灯塔', 'Moss规则', 1024, 768)
        self.assertEqual(result['prompt'], '清晨海边的红色灯塔。')

    def test_scene_content_review_shares_one_repair_budget(self):
        self.guards()
        self.chat.side_effect = [
            ('{"caption":"城堡走廊地面布满青苔。"}', {'finish_reason': 'stop'}),
            ('{"caption":"城堡走廊，完整石板与雕花木门。"}', {'finish_reason': 'stop'})]
        def validate(prompt):
            if '青苔' in prompt:
                raise ValueError('场景事实未要求室内青苔')
        result = moss_prompt.write_prompt('城堡走廊', 'Moss规则', 1024, 768,
            output_format='caption', validate_prompt=validate)
        self.assertEqual(self.chat.call_count, 2)
        self.assertNotIn('青苔', result['prompt'])
        self.assertIn('场景事实未要求室内青苔', json.loads(self.chat.call_args.args[1])['格式修复']['错误'])

    def test_scene_content_and_format_failures_never_create_a_third_call(self):
        self.guards()
        self.chat.side_effect = [
            ('{"caption":"截断', {'finish_reason': 'stop'}),
            ('{"caption":"城堡走廊地面布满青苔。"}', {'finish_reason': 'stop'})]
        with self.assertRaisesRegex(ValueError, 'unsupported scene'):
            moss_prompt.write_prompt('城堡走廊', 'Moss规则', 1024, 768,
                output_format='caption', validate_prompt=lambda prompt: (_ for _ in ()).throw(ValueError('unsupported scene')))
        self.assertEqual(self.chat.call_count, 2)
        self.assertEqual(self.chat.call_count, 2)

    def test_repair_rechecks_all_guards_before_a_second_model_call(self):
        for guard_name in ('local', 'resources', 'idle'):
            self.guards(('{"caption":"未结束', {'finish_reason': 'stop'}))
            guard = getattr(self, guard_name)
            guard.side_effect = [None, RuntimeError('resource changed before repair')]
            with self.subTest(guard=guard_name), self.assertRaisesRegex(RuntimeError, 'resource changed'):
                moss_prompt.write_prompt('灯塔', 'Moss规则', 1024, 768)
            self.assertEqual(guard.call_count, 2)
            self.chat.assert_called_once()

    def test_network_and_age_errors_are_not_format_retries(self):
        self.guards()
        self.chat.side_effect = ValueError('network response failed')
        with self.assertRaisesRegex(ValueError, 'network'):
            moss_prompt.write_prompt('灯塔', 'Moss规则', 1024, 768)
        self.chat.assert_called_once()
        self.chat.reset_mock(side_effect=True)
        with patch('cores.age_policy.assert_model_request', side_effect=ValueError('age request blocked')), \
             self.assertRaisesRegex(ValueError, 'age request'):
            moss_prompt.write_prompt('灯塔', 'Moss规则', 1024, 768)
        self.chat.assert_not_called()
        with patch('cores.age_policy.assert_model_response', side_effect=ValueError('age response blocked')), \
             self.assertRaisesRegex(ValueError, 'age response'):
            moss_prompt.write_prompt('灯塔', 'Moss规则', 1024, 768)
        self.chat.assert_called_once()

    def test_api_routes_dimensions_and_busy_lock(self):
        data = {'model': 'moss', 'prompt': '灯塔', 'format': 'caption', 'base_w': 1024, 'base_h': 768}
        with patch.object(moss_prompt, 'write_prompt', return_value=data) as writer:
            result = image_api.moss_prompt(None, '', {'text': '灯塔', 'system': '规则', 'base_w': 1024, 'base_h': 768})
            self.assertEqual(result, {'ok': True, 'data': data})
            writer.assert_called_once_with('灯塔', '规则', base_w=1024, base_h=768)
            image_api._MOSS_LOCK.acquire()
            try:
                self.assertEqual(image_api.moss_prompt(None, '', {'text': '灯塔'})['_status'], 409)
            finally:
                image_api._MOSS_LOCK.release()
        with patch.object(moss_prompt, 'write_prompt', side_effect=ValueError('无效布局')):
            self.assertEqual(image_api.moss_prompt(None, '', {'text': '灯塔'})['_status'], 400)
        self.assertEqual(image_api.moss_prompt(None, '', [])['_status'], 400)
        self.assertTrue(image_api._MOSS_LOCK.acquire(blocking=False))
        image_api._MOSS_LOCK.release()


if __name__ == '__main__':
    unittest.main()
