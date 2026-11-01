"""Source scene defaults enter the existing design chain without model calls."""
import copy
import os
os.environ['V41_NO_MODEL'] = '1'
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from cores import authoring, asset_core, story_core, scene_condition


def design(name='宫殿大厅', merged='无', space='室内，完整石柱围绕开阔大厅。'):
    return (f'【景】{name}\n【归并自】{merged}\n【一句话空间】{space}\n'
            '【光】天窗光线\n【固有陈设】石柱\n【纵深与尺度】柱列通向尽头\n')


class SceneDefaultAuthoring(unittest.TestCase):
    def setUp(self):
        self.story = {'one_line': '朋友们在宫殿大厅商量下一次旅行。', 'settings': {}}
        self.cards = [{'scene_id': 'S', 'name': '宫殿大厅', 'auto_made': True,
                       'space': '室内，自动剧本写的破败大厅，杂乱且昏暗。'}]
        self.body = ('自动剧本：宫殿大厅里阴暗破败，柱子烧焦，物品杂乱，朋友们走进大厅讨论旅行。' * 3)

    def run_design(self, outputs=None, cards=None, story=None, *, force=False):
        cards = self.cards if cards is None else cards
        story = self.story if story is None else story
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(authoring, 'ensure_scenes', return_value=cards))
        stack.enter_context(patch.object(story_core, 'get_story', return_value=story))
        stack.enter_context(patch.object(authoring, '_ep', return_value=({}, {'body': self.body})))
        stack.enter_context(patch.object(asset_core, 'list_assets', return_value=[]))
        stack.enter_context(patch.object(authoring, '_ins', return_value='美术指导'))
        stack.enter_context(patch.object(authoring, '_dbg'))
        stack.enter_context(patch.object(authoring, 'design_problems', side_effect=lambda *_: []))
        update = stack.enter_context(patch.object(authoring, '_update_ep'))
        apply = stack.enter_context(patch.object(authoring, 'apply_design', return_value=cards))
        q = stack.enter_context(patch.object(authoring, '_q', side_effect=outputs or [design()]))
        self.last_mocks = q, apply, update
        return authoring.design_scenes('TEST', force=force), q, apply, update

    def test_original_source_defaults_precede_design_and_generated_aging_is_not_authority(self):
        generated = design(space='室内，昏暗破败大厅的石柱烧焦，物品杂乱。')
        result, q, apply, update = self.run_design([generated])
        self.assertEqual(result, self.cards)
        self.assertEqual(q.call_count, 1)
        condition = apply.call_args.kwargs['scene_conditions']['宫殿大厅']
        self.assertEqual(condition['lighting'], 'auto')
        self.assertEqual(condition['order'], 'tidy')
        self.assertEqual(condition['structure'], 'intact')
        self.assertEqual(condition['surface_effects'], [])
        expected = scene_condition.positive_condition(condition)
        self.assertIn('- 宫殿大厅：' + expected, q.call_args.args[1])
        self.assertLess(q.call_args.args[1].index(expected), q.call_args.args[1].index('【第 1 话剧本】'))
        self.assertEqual(apply.call_args.args[2], generated)
        update.assert_called_once()

    def test_five_user_states_enter_design_as_separate_dimension_overrides(self):
        cases = (
            ('宫殿大厅', 'auto', 'tidy', 'intact', False),
            ('宫殿大厅气氛黑暗', 'dark', 'tidy', 'intact', False),
            ('宫殿大厅柱子有烧焦痕迹', 'auto', 'tidy', 'intact', True),
            ('宫殿大厅东西杂乱', 'auto', 'messy', 'intact', False),
            ('宫殿大厅黑暗，建筑烧焦，东西杂乱破败', 'dark', 'messy', 'intact', True),
        )
        for source, lighting, order, structure, has_effect in cases:
            with self.subTest(source=source):
                _, q, apply, _ = self.run_design(story={'one_line': source})
                condition = apply.call_args.kwargs['scene_conditions']['宫殿大厅']
                self.assertEqual((condition['lighting'], condition['order'], condition['structure']),
                                 (lighting, order, structure))
                self.assertEqual(bool(condition['surface_effects']), has_effect)
                if '破败' in source:
                    self.assertIn('wear', [effect['kind'] for effect in condition['surface_effects']])
                self.assertIn(scene_condition.positive_condition(condition), q.call_args.args[1])

    def test_already_designed_cards_are_returned_without_writes_or_model(self):
        self.cards[0]['design'] = design()
        before = copy.deepcopy(self.cards)
        result, q, apply, update = self.run_design()
        self.assertEqual(result, before)
        q.assert_not_called()
        apply.assert_not_called()
        update.assert_not_called()

    def test_incompatible_scene_states_cannot_be_silently_merged(self):
        cards = [{'scene_id': 'A', 'name': '大厅', 'auto_made': True},
                 {'scene_id': 'B', 'name': '书房', 'auto_made': True}]
        merged = design('大厅', '书房')
        separate = design('大厅') + '\n' + design('书房')
        _, q, apply, _ = self.run_design([merged, separate], cards,
                                        {'one_line': '大厅黑暗，书房明亮。'})
        self.assertEqual(q.call_count, 2)
        self.assertIn('归并了状态不同的景', q.call_args.args[1])
        conditions = apply.call_args.kwargs['scene_conditions']
        self.assertEqual(conditions['大厅']['lighting'], 'dark')
        self.assertEqual(conditions['书房']['lighting'], 'bright')

    def test_repeated_incompatible_merge_does_not_save_generated_design(self):
        cards = [{'scene_id': 'A', 'name': '大厅', 'auto_made': True},
                 {'scene_id': 'B', 'name': '书房', 'auto_made': True}]
        with self.assertRaisesRegex(RuntimeError, '状态不同'):
            self.run_design([design('大厅', '书房')] * 3, cards,
                            {'one_line': '大厅黑暗，书房明亮。'})
        q, apply, update = self.last_mocks
        self.assertEqual(q.call_count, 3)
        update.assert_not_called()
        apply.assert_not_called()

    def test_apply_saves_frozen_metadata_and_preserves_user_fields_and_images(self):
        original = authoring._design_fields(authoring.parse_design(design())[0])
        card = dict(original, name='宫殿大厅', scene_id='S', edited_by_user=True,
                    visuals=['V'], prompt_override='用户保留的出图词')
        card['space'] = '室内，用户指定的完整大殿，陈设整齐。'
        card['light'] = '用户指定的暖灯'
        condition = scene_condition.condition_for_scene(self.story, card, scene_names=['宫殿大厅'])
        frozen = copy.deepcopy(condition)
        generated = design(space='室内，自动生成的烧焦大殿。')
        def assets(_sid, kind):
            return [card] if kind == 'scenes' else [{'owner_id': 'S', 'status': 'adopted'}]
        with patch.object(asset_core, 'list_assets', side_effect=assets), \
             patch.object(asset_core, 'save_asset') as save, \
             patch.object(asset_core, 'create_scene', side_effect=AssertionError('reuse existing card')), \
             patch.object(asset_core, 'delete_asset', side_effect=AssertionError('preserve existing card')), \
             patch.object(story_core, 'get_story', return_value={}), \
             patch.object(authoring, '_ep', return_value=({}, {'body': self.body})), \
             patch.object(scene_condition, 'condition_for_scene', side_effect=AssertionError('do not recompute')):
            result = authoring.apply_design('TEST', 1, generated,
                                           scene_conditions={'宫殿大厅': condition})
        self.assertEqual(result[0]['scene_condition'], frozen)
        self.assertEqual(card['space'], '室内，用户指定的完整大殿，陈设整齐。')
        self.assertEqual(card['light'], '用户指定的暖灯')
        self.assertEqual(card['visuals'], ['V'])
        self.assertEqual(card['prompt_override'], '用户保留的出图词')
        self.assertEqual(condition, frozen)
        self.assertIsNot(card['scene_condition'], condition)
        save.assert_called_once()


if __name__ == '__main__':
    unittest.main()
