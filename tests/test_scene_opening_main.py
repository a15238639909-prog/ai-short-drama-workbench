"""Opening scene facts at both formal writers; isolated CPU-only fixtures."""
import copy
import json
import unittest
from unittest.mock import patch

from tests import test_main_images as main_fixtures
from cores import asset_core, main_images, prompt_writer, story_core
from cores.scene_condition import condition_for_scene
from models import moss_prompt, krea_client, qwen_client, moss_client, anima_client

MOSS = main_fixtures.MOSS


CLEAN = '城堡卧室全景，中央为四柱床，白色床品洁净平整，后墙为石砌壁炉，顶部木梁清晰。'
DIRTY = '卧室的四柱床铺着布满褶皱与湿痕的白色床单。床尾地板散落着皮带和领带。'


class SceneOpeningMainTests(unittest.TestCase):
    setUp = main_fixtures.MainImageTests.setUp

    def scene(self, model='moss', **extra):
        story_core.save_story({'story_id': 'TEST', 'one_line': '城堡卧室',
                              'settings': dict(MOSS, image_model=model)})
        card = dict(scene_id='S1', name='城堡卧室', auto_made=True,
                    space='室内四柱床，床品布满褶皱与湿痕。后墙为石砌壁炉。',
                    fixtures='四柱床｜中央｜深棕木框，白色床单布满湿痕。',
                    depth='前景床尾的地板上散落着一条皮带和一条领带。顶部可见木梁。')
        card.update(extra)
        asset_core.save_asset('TEST', 'scenes', card)
        return card

    def test_moss_receives_clean_opening_facts_without_mutating_saved_design(self):
        card = self.scene()
        before = copy.deepcopy(asset_core.get_asset('TEST', 'scenes', 'S1'))
        with patch.object(moss_prompt, 'write_prompt', return_value={'prompt': CLEAN}) as writer:
            self.assertEqual(main_images.write_scene('TEST', card, MOSS), CLEAN)
        payload = json.loads(writer.call_args.args[0])['场景卡']
        rendered = json.dumps(payload, ensure_ascii=False)
        for residue in ('湿痕', '皮带', '领带'):
            self.assertNotIn(residue, rendered)
        for fixed in ('四柱床', '壁炉', '木梁'):
            self.assertIn(fixed, rendered)
        after = asset_core.get_asset('TEST', 'scenes', 'S1')
        for key in ('space', 'fixtures', 'depth'):
            self.assertEqual(before[key], after[key])

    def bound_cache(self, text):
        card = self.scene()
        condition = condition_for_scene(story_core.get_story('TEST'), card,
                                        scene_names=['城堡卧室'])
        card = main_images.bind_prompt(dict(card, scene_condition=condition), text, MOSS)
        card['image_prompt_facts_hash'] = main_images._facts_hash(card, MOSS)
        self.assertEqual(main_images.cached_prompt(card, MOSS), text)
        return card

    def test_invalid_current_automatic_cache_is_rewritten(self):
        card = self.bound_cache(DIRTY)
        with patch.object(moss_prompt, 'write_prompt', return_value={'prompt': CLEAN}) as writer:
            self.assertEqual(main_images.write_scene('TEST', card, MOSS), CLEAN)
        writer.assert_called_once()

    def test_valid_current_cache_is_reused(self):
        card = self.bound_cache(CLEAN)
        with patch.object(moss_prompt, 'write_prompt') as writer:
            self.assertEqual(main_images.write_scene('TEST', card, MOSS), CLEAN)
        writer.assert_not_called()

    def test_user_override_is_kept(self):
        card = main_images.bind_prompt(self.scene(), DIRTY, MOSS, 'prompt_override')
        with patch.object(moss_prompt, 'write_prompt') as writer:
            self.assertEqual(main_images.write_scene('TEST', card, MOSS), DIRTY)
        writer.assert_not_called()

    def test_real_moss_writer_repairs_unsupported_bedding_before_return(self):
        responses = [(json.dumps({'caption': DIRTY}, ensure_ascii=False), {'finish_reason': 'stop'}),
                     (json.dumps({'caption': CLEAN}, ensure_ascii=False), {'finish_reason': 'stop'})]
        with patch.object(qwen_client, 'chat', side_effect=responses) as chat, \
             patch.object(moss_client, 'local_guard'), \
             patch.object(moss_client, '_resource_guard'), \
             patch.object(anima_client, 'idle_guard'):
            result = main_images.write_scene('TEST', self.scene(), MOSS)
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(result, CLEAN)

    def test_both_krea_formal_paths_receive_curated_facts(self):
        settings = dict(MOSS, image_model='krea2')
        card = self.scene(model='krea2')
        with patch('cores.quality_core.compile_scene_prompt', return_value=CLEAN) as compiler:
            main_images.write_scene('TEST', card, settings)
        text = json.dumps({k: v for k, v in compiler.call_args.args[0].items()
                           if k in ('space', 'fixtures', 'depth')}, ensure_ascii=False)
        self.assertNotIn('湿痕', text)
        self.assertNotIn('皮带', text)
        unchanged = lambda value, *args, **kwargs: value
        story = story_core.get_story('TEST')
        story['saga'] = {'episodes': [{'no': 1, 'prose': '后来地板上散落着皮带，床单出现湿痕。'}]}
        story_core.save_story(story)
        with patch.object(prompt_writer, 'write', return_value=CLEAN) as writer, \
             patch.object(asset_core, '_scene_prompt_problems', return_value=[]), \
             patch.object(asset_core, 'scene_prompt_repair', side_effect=unchanged), \
             patch.object(asset_core, 'scene_prompt_fix_scale', side_effect=unchanged), \
             patch.object(asset_core, 'scene_prompt_fix_material', side_effect=unchanged), \
             patch.object(asset_core, 'scene_prompt_fix_world', side_effect=unchanged), \
             patch.object(krea_client, 'generate', return_value=self.result):
            asset_core.generate_scene_master('TEST', 'S1')
        request = writer.call_args.args[1]
        for residue in ('湿痕', '皮带', '【故事正文】'):
            self.assertNotIn(residue, request)
        for fixed in ('四柱床', '壁炉', '木梁'):
            self.assertIn(fixed, request)

    def test_krea_design_one_line_and_intent_cannot_restore_later_residue(self):
        design = ('【景】城堡卧室\n【镇场主体】中央四柱床\n'
                  '【固有陈设】四柱床，后墙石砌壁炉，顶部木梁\n'
                  '【纵深与尺度】前景床尾散落着皮带和领带\n'
                  '【一句话空间】室内四柱床，床单布满湿痕。')
        self.scene(model='krea2', design=design, intent='以后物件散乱的戏剧结果')
        unchanged = lambda value, *args, **kwargs: value
        with patch.object(prompt_writer, 'write', return_value=CLEAN) as writer, \
             patch.object(asset_core, '_scene_prompt_problems', return_value=[]), \
             patch.object(asset_core, 'scene_prompt_repair', side_effect=unchanged), \
             patch.object(asset_core, 'scene_prompt_fix_scale', side_effect=unchanged), \
             patch.object(asset_core, 'scene_prompt_fix_material', side_effect=unchanged), \
             patch.object(asset_core, 'scene_prompt_fix_world', side_effect=unchanged), \
             patch.object(krea_client, 'generate', return_value=self.result):
            asset_core.generate_scene_master('TEST', 'S1')
        request = writer.call_args.args[1]
        for residue in ('湿痕', '皮带', '领带', '戏剧结果'):
            self.assertNotIn(residue, request)
        self.assertIn('四柱床', request)
        self.assertIn('壁炉', request)


if __name__ == '__main__':
    unittest.main()
