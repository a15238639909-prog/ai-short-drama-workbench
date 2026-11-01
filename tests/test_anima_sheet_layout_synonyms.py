"""Layout checks accept local Qwen's equivalent back-full-body wording."""
import unittest
from models import anima_client


class AnimaSheetLayoutSynonymsTests(unittest.TestCase):
    def test_qwen_four_grid_back_full_body_is_complete(self):
        prompt = ('layout 2x2 grid, top-left front full body standing pose, '
                  'top-right 90-degree right profile full body standing pose, '
                  'bottom-left back full body standing pose, '
                  'bottom-right close-up face portrait, head fills frame')
        self.assertTrue(anima_client.sheet_layout_valid(prompt, portrait=True))

    def test_previous_back_view_forms_still_pass(self):
        for back in ('back view', 'back-full-body-view', 'rear full body'):
            with self.subTest(back=back):
                prompt = 'left panel front full body, side view full body, ' + back + ', headshot'
                self.assertTrue(anima_client.sheet_layout_valid(prompt, portrait=True))

    def test_missing_required_view_or_portrait_still_fails(self):
        for prompt in ('left front full body, profile full body, close-up portrait',
                       'left front full body, back full body, close-up portrait',
                       'left profile full body, back full body, close-up portrait',
                       'left front full body, profile full body, back full body'):
            with self.subTest(prompt=prompt):
                self.assertFalse(anima_client.sheet_layout_valid(prompt, portrait=True))


if __name__ == '__main__':
    unittest.main()
