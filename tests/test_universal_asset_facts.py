"""Pure / mocked regressions for source facts; no models or production writes."""
import copy
import os
os.environ['V41_NO_MODEL'] = '1'
import unittest
from unittest.mock import patch
from cores import oral_story, authoring, asset_core, main_images


def design(name='走廊', space='室内医院走廊', fixtures='左侧木门、右侧玻璃窗', light='夜间顶灯'):
    return f'【景】{name}\n【一句话空间】{space}\n【固有陈设】{fixtures}\n【光】{light}\n【纵深与尺度】十米长\n'


class OutfitFacts(unittest.TestCase):
    def test_partial_requirement_does_not_drop_other_parts_in_same_sentence(self):
        text, facts = oral_story.fixed_outfit_description(['穿了灰色长裤'],
            '上装：宽松病号服上衣，下装：蓝色长裤，鞋履：白色运动鞋。')
        self.assertIn('宽松病号服上衣', text)
        self.assertIn('灰色长裤', text)
        self.assertIn('白色运动鞋', text)
        self.assertNotIn('蓝色长裤', text)
        self.assertEqual(facts, ['灰色长裤'])

    def test_source_preserves_hospital_top_fit_and_bare_feet(self):
        source = '阿宁穿着宽松的病号服上衣，下身穿了灰色短裤，赤足。'
        hints = oral_story.outfit_hints(source, ['阿宁'])['阿宁']
        text, _ = oral_story.fixed_outfit_description(hints, '红色外套；黑色长裤；白色运动鞋')
        self.assertIn('宽松的病号服上衣', text)
        self.assertIn('灰色短裤', text)
        self.assertIn('赤足', text)
        self.assertNotIn('了灰色', text)
        self.assertNotIn('红色外套', text)
        self.assertNotIn('运动鞋', text)

    def test_cross_genre_partial_clothes_and_unknown_source_terms(self):
        for requirement, keep, replace in (
            ('蓝色外套', '黑色长裤', '红色外套'),
            ('灰色长裤', '上装：云锦短褐', '蓝色长裤'),
            ('银白太空服上衣', '下装：黑色压力裤', '上装：红色制服'),
            ('黑色作战靴', '上装：迷彩背心', '鞋履：白色运动鞋')):
            with self.subTest(requirement=requirement):
                text, _ = oral_story.fixed_outfit_description([requirement], keep + '，' + replace)
                self.assertIn(requirement, text)
                self.assertIn(keep, text)
                self.assertNotIn(replace, text)

    def test_unknown_garment_with_body_slot_is_retained_and_not_complete(self):
        text, _ = oral_story.fixed_outfit_description(['下装：灰色长裤'], '上装：云锦短褐，蓝色长裤')
        self.assertIn('云锦短褐', text)
        text, facts = oral_story.fixed_outfit_description(['上装：月织灵纱'], '上装：红色外套，下装：灰色长裤')
        self.assertEqual(facts, ['上装：月织灵纱'])
        self.assertIn('灰色长裤', text)
        self.assertNotIn('红色外套', text)

    def test_two_people_in_one_sentence_do_not_share_clothes(self):
        hints = oral_story.outfit_hints('阿宁穿蓝色衬衫，林海穿红色外套。', ['阿宁', '林海'])
        self.assertNotIn('红色', str(hints['阿宁']))
        self.assertNotIn('蓝色', str(hints['林海']))


class SceneFacts(unittest.TestCase):
    def test_partial_design_keeps_unspecified_space_and_fixture(self):
        card = {'space': '室内小店', 'fixtures': '木桌', 'design': '【景】小店\n【光】窗外晨光'}
        effective = asset_core.effective_scene_card(card)
        self.assertEqual(effective['space'], '室内小店')
        self.assertEqual(effective['fixtures'], '木桌')
        self.assertEqual(effective['light'], '窗外晨光')

    def test_storage_read_projects_facts_without_writing(self):
        card = {'scene_id': 'S', 'space': '室外街道', 'design': design()}
        from cores import store
        from pathlib import Path
        with patch.object(asset_core, 'assets_path', return_value=Path('isolated-assets')), \
             patch.object(store, 'load_json', return_value=card), \
             patch.object(store, 'save_json', side_effect=AssertionError('read must not write')):
            self.assertEqual(asset_core.get_asset('TEST', 'scenes', 'S')['space'], '室内医院走廊')
        self.assertEqual(card['space'], '室外街道')

    def test_confirmed_design_wins_over_automatic_extraction_without_mutation(self):
        card = {'scene_id': 'S', 'name': '走廊', 'space': '室外柏油路',
                'contract_text': '室外柏油路', 'ground': '柏油', 'light': '白昼阳光',
                'interior': '室外', 'design': design()}
        before = copy.deepcopy(card)
        effective = asset_core.effective_scene_card(card)
        self.assertEqual(card, before)
        self.assertEqual(effective['space'], '室内医院走廊')
        self.assertEqual(effective['contract_text'], effective['space'])
        self.assertEqual(effective['light'], '夜间顶灯')
        self.assertFalse(effective['ground'])
        self.assertTrue(asset_core.scene_indoor(card))
        with patch.object(asset_core, 'list_assets', return_value=[]):
            facts = main_images._scene_facts('TEST', card)
        self.assertNotIn('柏油', str(facts))
        self.assertIn('右侧玻璃窗', str(facts))

    def test_hand_edit_survives_read_and_automatic_redesign(self):
        original = authoring._design_fields(authoring.parse_design(design())[0])
        card = dict(original, scene_id='S', name='走廊', edited_by_user=True)
        card.update(space='用户指定的室内长廊', light='暖色台灯', fixtures='窗边木桌')
        read = asset_core.effective_scene_card(card)
        self.assertEqual(read['contract_text'], card['space'])
        self.assertEqual(read['light'], '暖色台灯')
        new = authoring._design_fields(authoring.parse_design(design(light='蓝色顶灯'))[0])
        authoring._apply_scene_design_fields(card, new)
        self.assertEqual(card['light'], '暖色台灯')
        self.assertEqual(card['fixtures'], '窗边木桌')
        self.assertEqual(card['space'], '用户指定的室内长廊')

    def test_manual_contract_edit_updates_its_space_alias(self):
        card = authoring._design_fields(authoring.parse_design(design())[0])
        card['contract_text'] = '用户手写的书房'
        self.assertEqual(asset_core.effective_scene_card(card)['space'], '用户手写的书房')
        card['design'] = card['design'].replace('室内医院走廊', '室内狭窄走廊')
        self.assertEqual(asset_core.effective_scene_card(card)['space'], '用户手写的书房')

    def test_partial_outer_layer_keeps_existing_inner_layer(self):
        clothing, _ = oral_story.fixed_outfit_description(['穿蓝色外套'], '白色衬衫；黑色外套；灰色长裤')
        self.assertIn('白色衬衫', clothing)
        self.assertIn('灰色长裤', clothing)
        self.assertNotIn('黑色外套', clothing)

    def test_legacy_user_card_keeps_existing_edits(self):
        card = {'space': '用户指定的船舱', 'light': '紧急红灯',
                'edited_by_user': True, 'design': design()}
        self.assertEqual(asset_core.effective_scene_card(card)['space'], card['space'])
        self.assertEqual(asset_core.effective_scene_card(card)['light'], card['light'])

    def test_same_source_facts_reach_krea_and_moss_and_manual_prompt_survives(self):
        card = {'scene_id': 'S', 'name': '走廊', 'space': '室外柏油路', 'design': design()}
        from cores import quality_core
        with patch.object(quality_core, 'compile_scene_prompt', return_value='OK') as compile:
            main_images.write_scene('TEST', card, {'image_model': 'krea2'})
        self.assertEqual(compile.call_args.args[0]['space'], '室内医院走廊')
        card = main_images.bind_prompt(card, '手动场景词', {'image_model': 'moss'}, 'prompt_override')
        self.assertEqual(main_images.write_scene('TEST', card, {'image_model': 'moss'}), '手动场景词')


class AliasFacts(unittest.TestCase):
    def test_explicit_identity_supports_different_genres(self):
        for alias, name in (('黑影', '丧尸医生'), ('蒙面人', '叶青'), ('维修员', '周师傅'), ('零号', '舰长')):
            with self.subTest(alias=alias):
                cards = [{'name': alias}, {'name': name}]
                before = copy.deepcopy(cards)
                merged = oral_story.merge_explicit_character_aliases(cards, f'{alias}原来就是{name}。')
                self.assertEqual(cards, before)
                self.assertEqual(len(merged), 1)
                self.assertEqual(oral_story.card_aliases(merged)[alias], name)
                self.assertIn(alias, merged[0]['alias_evidence'])

    def test_ambiguity_negation_question_and_disguise_do_not_merge(self):
        for text in ('黑影和医生一起进门。', '黑影不是医生。', '黑影就是医生？',
                     '她以为黑影就是医生。', '黑影假扮医生。', '她说：“黑影就是医生。”'):
            with self.subTest(text=text):
                self.assertEqual(len(oral_story.merge_explicit_character_aliases(
                    [{'name': '黑影'}, {'name': '医生'}], text)), 2)

    def test_conflicting_alias_and_real_name_do_not_override_identity(self):
        aliases = oral_story.card_aliases([{'name': '甲', 'aliases': ['代号']},
                                          {'name': '乙', 'aliases': ['代号', '甲']}])
        self.assertNotIn('代号', aliases)
        self.assertEqual(aliases['甲'], '甲')


if __name__ == '__main__':
    unittest.main()
