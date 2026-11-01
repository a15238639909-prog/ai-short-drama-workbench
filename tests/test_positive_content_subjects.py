import unittest
from cores.image_prompt_content import normalize_positive_description


class PositiveSubjectTests(unittest.TestCase):
    def test_no_other_people_keeps_the_explicit_subject(self):
        text = normalize_positive_description('一位成年女性坐在窗边，无其他人物。')
        self.assertIn('成年女性', text)
        self.assertIn('已指定主体', text)
        self.assertNotIn('空置', text)

    def test_no_people_remains_an_empty_environment(self):
        text = normalize_positive_description('雨夜小巷，无人物。')
        self.assertIn('空置的空间', text)

    def test_photographic_exclusions_become_visible_attributes(self):
        text = normalize_positive_description('光线无明显阴影，不刺眼，机位无移动，前景不遮挡中景主体。')
        self.assertIn('均匀光照', text)
        self.assertIn('画面稳定', text)
        self.assertIn('中景主体清晰可见', text)
        for word in ('无明显阴影', '不刺眼', '无移动', '不遮挡'):
            self.assertNotIn(word, text)


if __name__ == '__main__':
    unittest.main()
