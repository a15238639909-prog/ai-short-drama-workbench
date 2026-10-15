"""CPU-only regression for ordinary outfits competing with hidden anatomy."""
import copy
import os
os.environ['V41_NO_MODEL'] = '1'
import unittest
from unittest.mock import patch

from cores import authoring, asset_core, prompt_writer as pw, style_presets as sp
from cores import character_clothing as cc


class ClothingVisibilityTests(unittest.TestCase):
    def test_real_outfits_override_hidden_muscles_at_both_prompt_stages(self):
        cases = [
            ('阿宁', '女', 28, '蓝色外套；黑色长裤',
             '女性低体脂精瘦肌肉身材，窄胸廓、细腰，肩头、上臂和前臂肌肉分界清楚；'
             '腹肌分块、马甲线和腹斜肌明显，背肌与大腿小腿肌群可见，四肢偏细而有肌肉。'),
            ('林海', '男', 30, '灰色衬衫；黑色长裤',
             '男性低体脂精瘦身材，肌肉体积较小，肩臂、胸肌、腹肌与腿部肌肉分界清楚，腰腹收紧、四肢偏细。'),
        ]
        for name, sex, age, outfit, body in cases:
            with self.subTest(name=name):
                card = dict(name=name, character_id='MOCK', sex=sex, age=age, char_type='人类',
                            clothing=outfit, clothing_from_brief=True, clothing_requirement='自定义',
                            build='偏瘦有线条', sheet_body_ratio='标准成人',
                            sheet_body_override='偏瘦有线条', hair='黑色短发')
                original = copy.deepcopy(card)
                facts = {'appearance': {'脸': '琥珀色眼睛', '身体': body}, 'expression': '平静',
                         'clothing': [{'类别': '上装', '名称': '黑色高领上衣'},
                                      {'类别': '下装', '名称': '灰色裤子'}]}
                facts_original = copy.deepcopy(facts)
                sheet = '16:9横向四格人物设定板。' + body + '琥珀色眼睛。穿着' + outfit + '。真实摄影成像。'
                with patch.object(sp, 'sheet_body_text', return_value=body), \
                     patch.object(pw, 'prepare_character_facts', return_value=facts) as prepare, \
                     patch.object(pw, 'write_character', return_value=(sheet, '')) as write, \
                     patch.object(asset_core, 'get_asset', return_value=None), \
                     patch.object(asset_core, 'save_asset') as save:
                    result = authoring.write_char_sheet('MOCK', card, {})
                for text in (prepare.call_args.args[0], write.call_args.args[0], result):
                    for phrase in ('腹肌', '马甲线', '腹斜肌', '胸肌', '背肌', '肌肉分界', '肌群可见'):
                        self.assertNotIn(phrase, text)
                    for piece in outfit.split('；'):
                        self.assertIn(piece, text)
                    self.assertIn('精瘦', text)
                self.assertTrue(result.startswith('各视图中的同一人物均穿着'))
                self.assertIn('16:9横向四格人物设定板。', result)
                self.assertIn('琥珀色眼睛。', result)
                self.assertIn('真实摄影成像。', result)
                self.assertNotIn('黑色高领上衣', write.call_args.args[0])
                self.assertNotIn('灰色裤子', write.call_args.args[0])
                self.assertEqual(card, original)
                self.assertEqual(facts, facts_original)
                save.assert_not_called()

    def test_regular_coverage_keeps_silhouette_face_hair_and_material(self):
        guard = cc.coverage({'clothing': '灰色衬衫；黑色长裤'})
        text = '窄胸廓、细腰。黑发，琥珀色瞳孔，面部肌肉自然放松。衬衫布料褶皱清楚。腹肌分块可见。'
        result = cc.visible_text(text, guard)
        self.assertEqual(result, '窄胸廓、细腰。黑发，琥珀色瞳孔，面部肌肉自然放松。衬衫布料褶皱清楚。')

    def test_explicit_exposed_clothing_keeps_existing_handling(self):
        for outfit in ('短款露脐上衣，黑裤', '透明衬衫', '蓝色外套敞开', '全身赤裸，不穿任何衣物'):
            guard = cc.coverage({'clothing': outfit})
            self.assertEqual(guard, {})
            self.assertEqual(cc.final_sheet('原有体型说明', guard), '原有体型说明')

    def test_pants_alone_do_not_claim_torso_is_covered(self):
        guard = cc.coverage({'clothing': '黑色长裤'})
        text = cc.visible_text('腹肌清晰，腿部肌肉分界清楚。黑发。', guard)
        self.assertIn('腹肌清晰', text)
        self.assertNotIn('腿部肌肉', text)
        self.assertNotIn('上衣', cc.clothing_fact(guard))

    def test_custom_scale_clothing_is_not_rewritten(self):
        with patch.object(sp, 'character_figure_variant', return_value={'pose': '指定姿势', 'clothing': '指定尺度服装'}):
            text = authoring._char_one_line({'name': '测试', 'sex': '女', 'age': 28, 'clothing': '蓝色外套',
                                             'sheet_body_override': '腹肌分块可见'}, {})
        self.assertIn('腹肌分块可见', text)
        self.assertIn('指定尺度服装', text)
        self.assertNotIn('固定穿搭与遮挡事实', text)


if __name__ == '__main__':
    unittest.main()
