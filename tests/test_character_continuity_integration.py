"""Identity and shot safeguards across the real prompt compiler, without models."""
import copy
import json
import os
os.environ['V41_NO_MODEL'] = '1'
import unittest

from cores import observed_identity_continuity as identity
from cores import video_continuity
from cores import workbench_story_director as release
from tests.test_workbench_story_director_release import fixture, response


class CharacterContinuityIntegrationTests(unittest.TestCase):
    def test_impossible_view_enters_existing_format_repair_with_source_intact(self):
        slices, cards, settings = fixture()
        before = copy.deepcopy(slices[0]['text'])
        requests = []

        def query(system, user, **options):
            payload = json.loads(user)
            requests.append(payload)
            value = response(payload)
            if len(requests) == 1:
                value['integrated_multimodal_description'] = (
                    '[Shot 1] 中景，<Subject 1>背对镜头，<Subject 1>的睫毛在脸颊上投下阴影。\n\n'
                    '[Shot 2] At 00:02.000, 双人中景，茶杯平稳停住。\n\n'
                    '[Shot 3] At 00:04.000, 双人中景，两人交换提醒。')
            return json.dumps(value, ensure_ascii=False)

        result = release.make_prompts(slices, cards, ['竹庭'], query, settings=settings, count=1)
        self.assertEqual(len(requests), 2)
        self.assertIn('背对镜头', requests[1]['format_error'])
        self.assertFalse(result[0]['source_fallback'])
        self.assertEqual(result[0]['source_text'], before)
        self.assertEqual(result[0]['prompt'].count('慢一点。'), 1)

    def test_visible_gaze_target_gets_reference_without_changing_original_actions(self):
        slices, cards, settings = fixture()
        source = '阿岚的目光从顾亭身上移开，随即看回顾亭的脸。'
        slices[0].update(text=source)
        slices[0]['director'].update(cast=['阿岚'], camera='中景，镜头聚焦阿岚。',
            blocks=[[0, 6, source, []]], says=[])
        requests = []

        def query(system, user, **options):
            payload = json.loads(user)
            requests.append(payload)
            return json.dumps(response(payload), ensure_ascii=False)

        result = release.make_prompts(slices, cards, ['竹庭'], query, settings=settings, count=1)
        names = [reference['name'] for reference in requests[0]['canonical_references']
                 if reference['role'] == 'character']
        self.assertEqual(names, ['阿岚', '顾亭'])
        self.assertEqual(result[0]['chars'], ['阿岚', '顾亭'])
        self.assertEqual(result[0]['source_text'], source)
        self.assertEqual(requests[0]['beat']['source_text'], source)

    def test_renderer_does_not_restore_observed_identity_over_new_authority(self):
        fields = dict(subject_definitions=(
            '<Subject 1> is 顾亭, the character from <Picture 1>.\n'
            '<Subject 2> is 竹庭, the environment from <Picture 2>.'),
            summary='继续交谈。', retention_analysis='<Subject 1> fully_preserved\n<Subject 2> fully_preserved',
            detailed_description='[Shot 1] 顾亭站在门边，手中持有红盒子。',
            overall_soundscape='风声。', non_diegetic_music='N/A')
        compiled = identity.apply_compiled_authority(dict(fields=fields))
        scoped = video_continuity.scope_prompt(compiled['prompt'],
            dict(plan=dict(flow='source-v1'), source_text='顾亭换上蓝色外套，手中仍持有红盒子。'))
        self.assertIn('Apply explicit written wardrobe or hairstyle changes in story order', scoped)
        self.assertIn('an unapproved face, hair or clothing drift does not become a new design', scoped)
        self.assertNotIn('coverage from the source and preceding video', scoped)
        self.assertIn('红盒子', scoped)


if __name__ == '__main__':
    unittest.main()
