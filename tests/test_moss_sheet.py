"""Shared character design and deterministic MOSS sheet geometry; no models."""
import copy
import json
import unittest

from cores.moss_prompt_format import compile_draft
from cores.moss_sheet import compile_sheet


def plan():
    return {
        'identity': '成年原创探险家', 'style': '清楚线条的彩色动漫设定稿',
        'body': '七头身修长体型', 'hair': '黑色齐肩直发',
        'face': '棕色眼睛与圆润脸型', 'background': '简洁浅灰色背景',
        'outfit': {'upper': '墨绿短夹克', 'collar': '米白圆领内衬',
                   'waist': '腰部棕色皮带与左腰扣', 'lower': '灰色长裤',
                   'footwear': '深棕系带短靴'},
        'accessories': ['左腕银色手表'],
    }


class MossSheetTests(unittest.TestCase):
    def test_complete_design_exists_once_and_full_views_share_it(self):
        design = plan()
        draft = compile_sheet(design, 1360, 768)
        summary = draft['summary']['caption']
        for value in (*[design[name] for name in ('identity', 'style', 'body', 'hair', 'face', 'background')],
                      *design['outfit'].values(), *design['accessories']):
            self.assertEqual(summary.count(value), 1)
        self.assertIn('同一套服装', summary)
        for item in draft['items'][:3]:
            self.assertIn(design['identity'], item['caption'])
            self.assertIn('全图统一角色设定', item['caption'])
            for clothing in design['outfit'].values():
                self.assertNotIn(clothing, item['caption'])

    def test_fixed_view_orientation_and_head_foot_alignment(self):
        draft = compile_sheet(plan(), 1360, 768)
        captions = [item['caption'] for item in draft['items']]
        self.assertIn('正面站立全身', captions[0])
        self.assertIn('身体与头部整体朝右', captions[1])
        self.assertIn('鼻尖指向画面右侧', captions[1])
        self.assertIn('90度侧面', captions[1])
        self.assertIn('背面站立全身', captions[2])
        self.assertIn('正脸头肩特写', captions[3])
        boxes = [item['bbox'] for item in draft['items']]
        self.assertEqual({box[1] for box in boxes[:3]}, {64})
        self.assertEqual({box[3] for box in boxes[:3]}, {704})
        self.assertEqual({box[2] - box[0] for box in boxes[:3]}, {288})
        self.assertLess(boxes[3][3] - boxes[3][1], boxes[0][3] - boxes[0][1])

    def test_reference_canvas_boxes_have_real_whitespace(self):
        draft = compile_sheet(plan(), 1360, 768)
        boxes = [item['bbox'] for item in draft['items']]
        self.assertEqual(boxes, [[40, 64, 328, 704], [360, 64, 648, 704],
                                 [680, 64, 968, 704], [1016, 144, 1320, 464]])
        for left, right in zip(boxes, boxes[1:]):
            self.assertLess(left[2], right[0])
        for x0, y0, x1, y1 in boxes:
            self.assertGreater(x0, 0)
            self.assertGreater(y0, 0)
            self.assertLess(x1, 1360)
            self.assertLess(y1, 768)

    def test_scaled_boxes_remain_integer_bounded_aligned_and_separated(self):
        for width, height in ((1024, 768), (512, 512), (768, 1360), (256, 256), (16, 16)):
            with self.subTest(size=(width, height)):
                draft = compile_sheet(plan(), width, height)
                boxes = [item['bbox'] for item in draft['items']]
                for x0, y0, x1, y1 in boxes:
                    self.assertTrue(all(type(v) is int for v in (x0, y0, x1, y1)))
                    self.assertTrue(0 <= x0 < x1 <= width)
                    self.assertTrue(0 <= y0 < y1 <= height)
                self.assertEqual(len({(box[1], box[3]) for box in boxes[:3]}), 1)
                if width >= 256:
                    for left, right in zip(boxes, boxes[1:]):
                        self.assertLess(left[2], right[0])

    def test_portrait_reuses_face_hair_collar_with_close_framing(self):
        design = plan()
        portrait = compile_sheet(design, 1360, 768)['items'][3]['caption']
        for value in (design['identity'], design['hair'], design['face'], design['outfit']['collar']):
            self.assertIn(value, portrait)
        for value in (design['body'], design['outfit']['upper'], design['outfit']['waist'],
                      design['outfit']['lower'], design['outfit']['footwear']):
            self.assertNotIn(value, portrait)
        self.assertIn('裁切在上胸', portrait)
        self.assertNotIn('站立全身', portrait)

    def test_native_layout_compilation_preserves_geometry_and_design(self):
        draft = compile_sheet(plan(), 1360, 768)
        prompt, kind = compile_draft(draft, 1360, 768)
        native = json.loads(prompt)
        self.assertEqual(kind, 'layout')
        self.assertEqual(native['items'][0]['bbox'], [-320, -640, 320, -352])
        self.assertEqual(native['items'][3]['bbox'], [-240, 336, 80, 640])
        self.assertEqual(native['summary']['caption'], draft['summary']['caption'])
        self.assertTrue(all(item['label'] == 'character' for item in native['items']))
        self.assertNotIn('width', native)

    def test_input_is_unchanged_and_output_is_deterministic(self):
        design = plan()
        before = copy.deepcopy(design)
        first = compile_sheet(design, 1360, 768)
        second = compile_sheet(design, 1360, 768)
        self.assertEqual(first, second)
        self.assertEqual(design, before)
        first['items'][0]['bbox'][0] = 0
        self.assertEqual(second['items'][0]['bbox'][0], 40)

    def test_missing_extra_fields_and_old_per_view_plans_fail(self):
        invalid = [None, [], {'items': []}]
        for key in plan():
            design = plan()
            del design[key]
            invalid.append(design)
        for key in plan()['outfit']:
            design = plan()
            del design['outfit'][key]
            invalid.append(design)
        design = plan()
        design['views'] = []
        invalid.append(design)
        design = plan()
        design['outfit']['back_design'] = '额外设计'
        invalid.append(design)
        for design in invalid:
            with self.subTest(design=design), self.assertRaisesRegex(ValueError, 'Moss人设图'):
                compile_sheet(design, 1360, 768)

    def test_invalid_values_fail_and_empty_accessories_are_supported(self):
        design = plan()
        design['accessories'] = []
        self.assertEqual(len(compile_sheet(design, 1360, 768)['items']), 4)
        cases = []
        for key in ('identity', 'style', 'body', 'hair', 'face', 'background'):
            for value in ('', '  ', None, 123, False, []):
                design = plan()
                design[key] = value
                cases.append(design)
        for key in plan()['outfit']:
            for value in ('', None, {}):
                design = plan()
                design['outfit'][key] = value
                cases.append(design)
        for value in (None, '手表', {}, [None], [''], [123], ['  ']):
            design = plan()
            design['accessories'] = value
            cases.append(design)
        design = plan()
        design['outfit'] = []
        cases.append(design)
        for design in cases:
            with self.subTest(design=design), self.assertRaises(ValueError):
                compile_sheet(design, 1360, 768)

    def test_invalid_dimensions_fail_before_geometry(self):
        for width, height in ((True, 768), (1360, False), (None, 768), (0, 768),
                              (-16, 768), (1360.0, 768), ('1360', 768),
                              (1360, 767), (float('inf'), 768), (float('nan'), 768)):
            with self.subTest(size=(width, height)), self.assertRaisesRegex(ValueError, '画布宽高'):
                compile_sheet(plan(), width, height)


if __name__ == '__main__':
    unittest.main()
