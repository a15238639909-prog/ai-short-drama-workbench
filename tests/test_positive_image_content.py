"""No-model coverage of positive image text and unchanged sampler structure."""
import copy
import json
import unittest
from unittest.mock import patch

from cores.image_prompt_content import (assert_positive_description, image_prompt_content,
    normalize_positive_description, POSITIVE_OUTPUT_RULE)
from cores import authoring, prompt_writer
from models import anima_client as anima, moss_client, moss_prompt, qwen_client, gpu_manager, krea_client


class PositiveImageContentTests(unittest.TestCase):
    def test_common_visual_exclusions_become_positive_content(self):
        prompt = normalize_positive_description('安静的街道，无人物，无水印。')
        self.assertIn('空置的空间与静物', prompt)
        self.assertNotIn('无人物', prompt)
        self.assertNotIn('无水印', prompt)
        self.assertEqual(normalize_positive_description('blue coat, no text, no watermark'),
                         'blue coat, clean composition, clean composition')

    def test_clear_exclusion_phrases_are_checked_but_place_names_survive(self):
        for clause in ('无水印', '无字幕', '无边框', '无人物', '无主要人物', '没有可见人物',
                       '洁净无积灰', '没有污渍', '无明显磨损', '无活动人物',
                       '无强烈硬阴影', '不过度磨皮', '无破损', '没有明显破损',
                       '无杂乱杂物', '没有散落的杂物'):
            with self.subTest(clause=clause):
                with self.assertRaises(ValueError):
                    assert_positive_description(clause)
                result = normalize_positive_description(clause)
                self.assertNotIn(clause, result)
                assert_positive_description(result)
        text = '无锡郊外的河流，不知火舞站在石桥上。'
        self.assertEqual(assert_positive_description(text), text)
        self.assertEqual(normalize_positive_description(text), text)

    def test_literal_words_names_and_garment_terms_survive(self):
        text = '不知火舞穿无袖上衣，墙牌写着“不要进入”和‘禁止停车’，封面标题为"No Game No Life"。'
        self.assertEqual(assert_positive_description(text), text)
        self.assertEqual(normalize_positive_description(text), text)

    def test_redundant_negative_comparison_keeps_affirmative_visual_fact(self):
        text = '最暗处保留深灰细节而非纯黑，皮革呈哑光而不是镜面。'
        self.assertEqual(normalize_positive_description(text), '最暗处保留深灰细节，皮革呈哑光。')
        literal = '墙上写着“而非终点”，旁边是深灰色石板。'
        self.assertEqual(normalize_positive_description(literal), literal)

    def test_clean_scene_uses_positive_order_without_changing_cleanroom_names(self):
        text = '画面整洁无尘，无杂物。'
        self.assertEqual(normalize_positive_description(text), '画面干净整洁，陈设井然有序。')
        self.assertEqual(normalize_positive_description('无尘车间，设施排列整齐。'), '无尘车间，设施排列整齐。')

    def test_internal_rules_and_unresolved_negative_instructions_rejected(self):
        for prompt in ('人物遵守规则：不能出现第二个人。', '不要红色衣服',
                       'wardrobe_state记录已确认变化', '禁止画水印',
                       'Current wardrobe contract: {"characters": []}'):
            with self.subTest(prompt=prompt), self.assertRaises(ValueError):
                assert_positive_description(prompt)
        with self.assertRaises(ValueError):
            image_prompt_content('{"wardrobe_state":{"characters":[]}}')

    def test_labelled_negative_appendix_does_not_become_positive_tags(self):
        prompt = 'Positive prompt: blue coat, golden sunlight\nNegative prompt: bad hands, text, watermark'
        self.assertEqual(image_prompt_content(prompt, normalize=True), 'blue coat, golden sunlight')

    def test_moss_caption_sanitized_but_native_literal_text_and_coordinates_unchanged(self):
        layout = {'summary': {'caption': '彩色手绘漫画，无水印'},
                  'items': [{'label': 'text', 'caption': '红色手写标题', 'bbox': [-20, -30, 20, 30],
                             'text': '不要开门！\nNo Game No Life'},
                            {'label': 'character', 'caption': '不知火舞穿无袖上衣', 'bbox': [0, 0, 20, 30]}],
                  'bbox_coord_space': 'abs_centralized', 'bbox_format': 'yxyx'}
        original = copy.deepcopy(layout)
        clean = json.loads(image_prompt_content(json.dumps(layout, ensure_ascii=False), normalize=True))
        self.assertEqual(clean['items'], original['items'])
        self.assertEqual(clean['bbox_coord_space'], original['bbox_coord_space'])
        self.assertNotIn('无水印', clean['summary']['caption'])
        self.assertEqual(layout, original)

    def test_moss_writer_sends_rules_only_to_qwen_returns_visible_description(self):
        with patch.object(moss_client, 'local_guard'), patch.object(moss_client, '_resource_guard'), \
             patch.object(anima, 'idle_guard'), \
             patch.object(qwen_client, 'chat', return_value=(json.dumps({'caption': '窗边的一杯茶，无水印。'}, ensure_ascii=False),
                                                            {'finish_reason': 'stop'})) as query:
            result = moss_prompt.write_prompt('窗边一杯茶', system='你是摄影师', base_w=768, base_h=1024)
        self.assertIn(POSITIVE_OUTPUT_RULE, query.call_args.args[0])
        self.assertNotIn('无水印', result['prompt'])
        self.assertNotIn('最终画面正文', result['prompt'])
        self.assertEqual(moss_client.build_workflow(result['prompt'], 768, 1024, seed=3)['129']['inputs']['text'], result['prompt'])

    def test_anima_artist_quality_preserved_and_only_runtime_negative_text_changes(self):
        original = json.loads(anima.WORKFLOW.read_text(encoding='utf-8'))
        seed, artist = 5, anima.ARTISTS[1]['value']
        composed = anima.compose_prompt('Dragon Ball, Android 18, no text', artist)
        self.assertTrue(composed['prompt'].startswith(artist + ', '))
        self.assertTrue(all(tag in composed['prompt'] for tag in anima.QUALITY_PREFIX))
        self.assertNotIn('no text', composed['prompt'])
        graph = anima.build_workflow('Dragon Ball, Android 18, no text', 768, 1024, seed=seed, artist=artist)
        expected = copy.deepcopy(original)
        expected['11']['inputs']['text'] = composed['prompt']
        expected['12']['inputs']['text'] = ''
        expected['28']['inputs'].update(width=768, height=1024)
        expected['63']['inputs']['seed'] = seed
        expected['99']['inputs']['filename_prefix'] = graph['99']['inputs']['filename_prefix']
        self.assertEqual(graph, expected)
        self.assertEqual(json.loads(anima.WORKFLOW.read_text(encoding='utf-8')), original)

    def test_krea_internal_rule_rejected_before_gpu_or_model(self):
        with patch.object(gpu_manager, 'acquire', side_effect=AssertionError('GPU forbidden')) as acquire:
            with self.assertRaises(ValueError):
                krea_client.generate('禁止出现其他人物，wardrobe_state由程序确认')
        acquire.assert_not_called()

    def test_krea_writer_normalizes_before_returning_to_ui(self):
        with patch.object(prompt_writer, '_call', return_value='【出图提示词】蓝衣人物，无水印。\n【人物外观】黑色短发。') as call, \
             patch.object(prompt_writer, '_fill', return_value='后台指令'):
            prompt = prompt_writer.write('character', '蓝衣人物', {})
        self.assertIn(POSITIVE_OUTPUT_RULE, call.call_args.args[0])
        self.assertNotIn('无水印', prompt)
        self.assertIn('【人物外观】黑色短发', prompt)


if __name__ == '__main__':
    unittest.main()
