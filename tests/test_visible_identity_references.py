"""Source-owned visible identity references, without model or filesystem work."""
import copy
import json
import os
import unittest

os.environ['V41_NO_MODEL'] = '1'

from cores import director_flow as director, episode_contract as contract


CARDS = [dict(name='林雨', character_id='A', age=28, sex='女'),
         dict(name='陈安', character_id='B', age=30, sex='男', aliases=['阿安']),
         dict(name='周宁', character_id='C', age=27, sex='男')]


def frozen_slice(source, camera='平视中景，动作顺序沿用原文。', cast=None):
    script, dialogue, report = contract.prepare_source(source, CARDS)
    return dict(text=source, seconds=8, scene_hint='桥边', director=dict(
        flow='source-v1', cast=cast or ['林雨'], camera=camera, purpose='按原文自然互动。',
        blocks=[[0, 8, script, [row['id'] for row in report['dialogue']]]],
        says=dialogue, source_contract_version=contract.VERSION,
        presence=dict(actors=[], absent=[]), start_state='', target_state='保持当前姿态。'))


class VisibleIdentityReferencesTests(unittest.TestCase):
    def test_environment_only_scene_retains_no_character_references(self):
        source = '薄雾沿溪水流动。'
        presence = contract.presence_evidence(source, [])
        self.assertEqual(presence['visible'], [])
        segment = frozen_slice(source)
        segment['director']['cast'] = []
        clip = director._clip(segment, 1, [], {}, '', {}, '')
        self.assertEqual([row['name'] for row in clip['references']], ['桥边'])

    def test_gaze_targets_are_bound_without_reclassifying_them_as_action_actors(self):
        for source in ('林雨看向陈安。', '林雨看着陈安。', '林雨凝视陈安。',
                       '林雨目光落在陈安身上。', '林雨的视线缓缓转向身旁的陈安。',
                       '林雨的目光从陈安身上移开，随即看回陈安的脸。',
                       '林雨看回陈安。', '林雨望回陈安。',
                       '林雨注视阿安。'):
            with self.subTest(source=source):
                result = contract.presence_evidence(source, CARDS)
                self.assertIn('陈安', result['visible'])
                self.assertNotIn('陈安', result['actors'])
                self.assertLessEqual(result['minimum_action_participants'], 1)
                self.assertTrue(any(row['name'] == '陈安' for row in result['visual_identity_candidates']))

    def test_camera_gaze_countershot_and_coframed_dialogue_bind_both_identities(self):
        for camera in ('双人中景，林雨看向陈安。', '林雨与陈安同框交谈。',
                       '林雨和陈安面对面说话。', '林雨与陈安交换目光。',
                       '镜头对准林雨，随后反打陈安。'):
            with self.subTest(camera=camera):
                self.assertCountEqual(contract.camera_identity_evidence(camera, CARDS)['names'], ['林雨', '陈安'])

    def test_mentions_recollections_representations_and_offscreen_people_are_excluded(self):
        sources = ('林雨寻找陈安。', '林雨回忆自己看向陈安。', '林雨想象陈安的背影。',
                   '林雨看向陈安的照片。', '林雨看向照片中的陈安。',
                   '镜头聚焦陈安的画像。', '林雨听到陈安的声音。',
                   '镜头聚焦陈安的录音。', '林雨看向画外的陈安。',
                   '陈安的背影不出镜。', '陈安的背影在画外。',
                   '林雨看向陈安离去的方向。', '林雨看向已离场的陈安。',
                   '林雨看向陈安昨日离去的方向。', '林雨看向不在场的陈安。',
                   '林雨的目光从陈安的照片上移开。', '林雨回忆自己看回陈安。',
                   '林雨说：“我看向陈安。”', '林雨：陈安看着周宁。')
        for source in sources:
            with self.subTest(source=source):
                self.assertNotIn('陈安', contract.presence_evidence(source, CARDS)['visible'])
                self.assertNotIn('陈安', contract.camera_identity_evidence(source, CARDS)['names'])

    def test_negated_gaze_or_coframing_does_not_add_identity(self):
        for source in ('林雨没有看向陈安。', '林雨并未注视陈安。', '林雨不看向陈安。',
                       '不要拍摄陈安的背影。', '林雨没有与陈安同框。', '林雨与陈安并未交谈。'):
            with self.subTest(source=source):
                self.assertNotIn('陈安', contract.presence_evidence(source, CARDS)['visible'])

    def test_known_absence_excludes_gaze_target_until_source_has_real_return(self):
        result = contract.presence_evidence('陈安已经失踪。林雨看向陈安。', CARDS)
        self.assertNotIn('陈安', result['visible'])
        self.assertEqual(result['absent'], ['陈安'])
        result = contract.presence_evidence('陈安已经失踪。陈安走进门。林雨看向陈安。', CARDS)
        self.assertIn('陈安', result['visible'])
        self.assertEqual(result['absent'], [])
        self.assertNotIn('陈安', contract.camera_identity_evidence('林雨看向陈安。', CARDS, ['陈安'])['names'])

    def test_new_plan_adds_source_gaze_target_when_planner_omits_it(self):
        calls = []

        def query(system, payload, **kwargs):
            data = json.loads(payload)
            calls.append(data)
            return json.dumps(dict(initial_state='桥边', shots=[dict(scene_name='桥边', cast=['林雨'],
                camera='平视中景', purpose='完成原文回应。', end_unit=len(data['units']),
                seconds=8, end_state='两人保持原处。')]), ensure_ascii=False)

        source = '林雨看向陈安，林雨说：“先等等。”'
        slices = director.plan(source, [dict(name='桥边')], CARDS, query)
        self.assertEqual(len(calls), 1)
        self.assertCountEqual(slices[0]['director']['cast'], ['林雨', '陈安'])
        self.assertEqual(slices[0]['director']['says'], [['林雨', '先等等。']])
        self.assertIn('林雨看向陈安', slices[0]['text'])

    def test_source_plan_fallback_also_adds_gaze_target_without_model(self):
        result = director.source_plan_fallback(dict(units=director.units_of('林雨看向陈安。'),
            scenes=['桥边'], characters=[card['name'] for card in CARDS], dialogue=[]))
        self.assertCountEqual(result['shots'][0]['cast'], ['林雨', '陈安'])

    def test_frozen_slice_recomputes_source_and_camera_references_without_rewriting(self):
        segment = frozen_slice('林雨看向陈安。\n林雨：先等等。')
        before = copy.deepcopy(segment)
        clip = director._clip(segment, 4, CARDS, {}, '', {}, '')
        self.assertEqual([row['name'] for row in clip['references']], ['林雨', '陈安', '桥边'])
        self.assertEqual(segment['text'], before['text'])
        self.assertEqual(segment['director']['blocks'], before['director']['blocks'])
        self.assertEqual(segment['director']['says'], before['director']['says'])
        self.assertIn('身份参考槽位不要求所有人物同时出镜', clip['continuity_rules'])
        prompt = director.assemble(dict(clip_id=4, summary='A quiet response', style='Soft light',
            text='<Subject 1> remains by the bridge. {{D1}}', overall_soundscape='Bridge ambience'), clip)
        self.assertIn('<Subject 2> is 陈安, the character from <Picture 2>.', prompt)
        self.assertIn('<Subject 2> (identity reference only)', prompt)
        self.assertEqual(prompt.count('<d>[Chinese] 先等等。</d>'), 1)

    def test_frozen_slice_does_not_reintroduce_prior_absence_through_gaze_target(self):
        for prior in ('陈安已经失踪。', '陈安已经离场。', '陈安离开桥边。', '陈安走出门。'):
            with self.subTest(prior=prior):
                segment = frozen_slice('林雨看向陈安。', cast=['林雨', '陈安'])
                clip = director._clip(segment, 5, CARDS, {}, prior, {}, '')
                self.assertEqual([row['name'] for row in clip['references']], ['林雨', '桥边'])

    def test_current_departure_keeps_identity_but_next_segment_can_exclude_it(self):
        source = '陈安走出门。'
        presence = contract.presence_evidence(source, CARDS)
        self.assertEqual(presence['actors'], ['陈安'])
        self.assertEqual(presence['absent'], ['陈安'])
        segment = frozen_slice(source, cast=['陈安'])
        clip = director._clip(segment, 4, CARDS, {}, '', {}, '')
        self.assertEqual([row['name'] for row in clip['references']], ['陈安', '桥边'])
        self.assertNotIn('陈安', contract.presence_evidence('陈安已经离场。林雨看向陈安。', CARDS)['visible'])

    def test_source_repair_adds_target_to_legacy_slice_without_model(self):
        segment = frozen_slice('林雨看向陈安。\n林雨：先等等。')
        segment['director'].pop('source_contract_version')

        def forbidden_query(*args, **kwargs):
            self.fail('明确原文不应调用模型')

        director.repair_slice_source(segment, CARDS, forbidden_query)
        self.assertIn('陈安', segment['director']['cast'])

    def test_explicit_pov_camera_owner_keeps_reference_exclusion(self):
        segment = frozen_slice('林雨看向陈安。')
        clip = director._clip(segment, 4, CARDS, {}, '',
            dict(pov='第一视角POV', camera_owner='林雨'), '')
        self.assertEqual([row['name'] for row in clip['references']], ['陈安', '桥边'])
        first_person = CARDS + [dict(name='我', character_id='VIEWER', age=32)]
        self.assertNotIn('我', contract.camera_identity_evidence('陈安看向我。', first_person)['names'])

    def test_collective_reference_expansion_and_actor_count_are_preserved(self):
        group = dict(name='两位同伴', character_id='GROUP', entity_type='group',
            group_count=2, group_members=['陈安', '周宁'])
        cards = CARDS + [group]
        presence = contract.presence_evidence('林雨看向两位同伴。', cards)
        self.assertEqual(presence['actors'], ['林雨'])
        self.assertEqual(presence['minimum_action_participants'], 1)
        segment = frozen_slice('林雨看向两位同伴。')
        clip = director._clip(segment, 4, cards, {}, '', {}, '')
        self.assertEqual([row['name'] for row in clip['references']], ['林雨', '陈安', '周宁', '桥边'])


if __name__ == '__main__':
    unittest.main()
