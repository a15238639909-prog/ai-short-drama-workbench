import json
import unittest
from cores import moss_comic_layout as layout


class MossComicLayoutTests(unittest.TestCase):
    def fixture(self):
        project = dict(base_w=768, base_h=1360, style='彩色水彩漫画', characters=[
            dict(id='c01', name='林岸', appearance='年龄：28；性别：男；发型：黑色短发', personality='这段性格不得画出'),
            dict(id='c02', name='陈青', appearance='戴眼镜的棕色短发女性', personality='这段性格不得画出')])
        state = dict(c01=dict(clothing='黄色雨衣', held_items='双手空着', injury='未知', location='门后'),
                     c02=dict(clothing='蓝色夹克', held_items='右手握钥匙', injury='未知', location='门锁旁'))
        page = dict(summary='不得作为额外字幕的剧情摘要', scene='灯塔门口', character_ids=['c01', 'c02'], panels=[
            dict(number=1, character_ids=['c01', 'c02'], description='陈青转动钥匙，林岸在后方看着。',
                 ending_state=state, dialogue=[dict(speaker='c01', text='亮了！')], narration='')])
        return project, page

    def test_only_supplied_dialogue_becomes_text_items(self):
        project, page = self.fixture()
        result = json.loads(layout.compile_prompt(project, page))
        self.assertEqual([item['text'] for item in result['items'] if item['label'] == 'text'], ['亮了！'])
        self.assertIn('共1处', result['summary']['caption'])
        self.assertNotIn(page['summary'], str(result))
        self.assertNotIn('这段性格', str(result))
        self.assertEqual(result['bbox_format'], 'yxyx')

    def test_silent_panel_is_only_an_object_and_primary_actor_is_first(self):
        project, page = self.fixture()
        page['panels'][0]['dialogue'] = []
        result = json.loads(layout.compile_prompt(project, page))
        self.assertEqual(len(result['items']), 1)
        caption = result['items'][0]['caption']
        self.assertLess(caption.index('陈青，'), caption.index('林岸，'))
        self.assertIn('双手空着', caption)
        self.assertNotIn('未知', caption)
        self.assertIn('以动作画面叙事', caption)

    def test_one_to_four_panel_boxes_stay_inside_canvas_and_do_not_overlap(self):
        for count in range(1, 5):
            boxes = layout.panel_boxes(count, 768, 1360)
            self.assertEqual(len(boxes), count)
            for index, (x0, y0, x1, y1) in enumerate(boxes):
                self.assertTrue(0 <= x0 < x1 <= 768 and 0 <= y0 < y1 <= 1360)
                for a0, b0, a1, b1 in boxes[index+1:]:
                    self.assertFalse(x0 < a1 and x1 > a0 and y0 < b1 and y1 > b0)


if __name__ == '__main__':
    unittest.main()
