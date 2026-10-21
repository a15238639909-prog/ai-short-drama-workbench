"""No-model contracts for preflight, visual evidence and adoption gates."""
import copy
import os
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'

from cores import story_video_quality as quality


BRIEF = '三人进入神殿，路飞一拳击碎石像，最后拿起宝石。'
BEAT = {'goal': '排除守门石像', 'action': '路飞迎上石像，把它击碎；三人进入神殿。',
        'turn': '通路打开', 'emotion': '紧张转为果断', 'ending': '三人在殿内', 'dialogue': []}
WRITTEN = {'expected_end_state': '石像头部被击碎', 'dialogue': [],
           'timeline': [{'start': 4, 'end': 10, 'action': 'punch the statue'}]}
EVENT = {'id': 'statue', 'source_quote': '一拳击碎石像', 'before': '石像完整挡路',
         'action': '拳头击中石像', 'after': '石像碎裂，通路打开',
         'start': 4, 'end': 10, 'kind': 'transition'}
SAMPLES = [dict(id='before', path='before.png', time_seconds=3),
           dict(id='contact', path='contact.png', time_seconds=7.5),
           dict(id='after', path='after.png', time_seconds=10.5),
           dict(id='last', path='last.png', time_seconds=14.958333)]


def evidence(sample_id, visible='该帧直接可见的事实'):
    return {'sample_id': sample_id, 'visible': visible}


def storyboard_fixture(payload):
    return {'request_signature': payload['request_signature'], 'passed': True,
            'issues': [], 'event_contract': [copy.deepcopy(EVENT)], 'dialogue_timing': []}


def acceptance_fixture(payload):
    return {'request_signature': payload['request_signature'], 'passed': True,
            'events': [{'id': 'statue', 'status': 'observed', 'confirmation_mode': 'sequence',
                        'before': [evidence('before', '完整石像挡路')],
                        'action': [evidence('contact', '拳头接触石像躯干')],
                        'after': [evidence('after', '石像碎块散落，通路露出')],
                        'explanation': '前态、接触和碎裂结果在不同时间样本可见'}],
            'critical_issues': [], 'follow_up_times': []}


class StoryQualityTests(unittest.TestCase):
    def storyboard(self):
        return quality.storyboard_request(BRIEF, '真人电影', {'beats': [BEAT]},
                                           BEAT, WRITTEN, {'actual_visible': '三人在门口'}, 15)

    def acceptance(self, contract=None, samples=None, audio=None):
        return quality.segment_acceptance_request(
            [copy.deepcopy(EVENT)] if contract is None else contract, WRITTEN,
            copy.deepcopy(SAMPLES) if samples is None else samples,
            {'actual_visible': '殿内可见碎石', 'time_seconds': 14.958333},
            {'actual_visible': '三人在门口'}, 15, audio_review=audio)

    def test_builders_are_pure_and_do_not_mutate_sources(self):
        original = copy.deepcopy((BEAT, WRITTEN, SAMPLES, EVENT))
        with patch('builtins.open', side_effect=AssertionError('filesystem access')), \
             patch('socket.socket', side_effect=AssertionError('network/model access')):
            _, story, _ = self.storyboard()
            _, visual, _ = self.acceptance()
            quality.validate_storyboard(storyboard_fixture(story), story)
            quality.validate_acceptance(acceptance_fixture(visual), visual)
        story['beat']['action'] = 'changed'
        visual['sample_records'][0]['path'] = 'changed'
        self.assertEqual((BEAT, WRITTEN, SAMPLES, EVENT), original)

    def test_advisory_does_not_block_but_blocking_issue_does(self):
        _, payload, _ = self.storyboard()
        reply = storyboard_fixture(payload)
        reply['issues'] = [dict(severity='advisory', reason='服装纹理略有变化', correction='保持既有外观')]
        quality.validate_storyboard(reply, payload)
        reply['issues'][0]['severity'] = 'blocking'
        with self.assertRaisesRegex(ValueError, 'blocking'):
            quality.validate_storyboard(reply, payload)
        reply['passed'] = False
        quality.validate_storyboard(reply, payload)

    def test_source_quote_cannot_promote_director_only_details(self):
        _, payload, _ = self.storyboard()
        reply = storyboard_fixture(payload)
        reply['event_contract'][0]['source_quote'] = WRITTEN['expected_end_state']
        with self.assertRaisesRegex(ValueError, 'source_quote'):
            quality.validate_storyboard(reply, payload)
        reply['event_contract'][0]['source_quote'] = '三人进入神殿'
        quality.validate_storyboard(reply, payload)

    def test_model_schema_limits_quotes_to_supplied_source_fragments(self):
        from cores import story_video_core as core
        _, payload, schema = self.storyboard()
        reply = storyboard_fixture(payload)
        reply['event_contract'][0]['source_quote'] = payload['brief']
        core.validate_schema(reply, schema)
        reply['event_contract'][0]['source_quote'] = 'a plausible paraphrase invented by the reviewer'
        with self.assertRaises(ValueError):
            core.validate_schema(reply, schema)

    def test_plan_only_action_cannot_become_a_user_requirement(self):
        _, payload, _ = self.storyboard()
        reply = storyboard_fixture(payload)
        reply['event_contract'][0]['source_quote'] = BEAT['action']
        # Even a stale or mistakenly expanded choice list cannot override brief.
        payload['source_quote_choices'].append(BEAT['action'])
        with self.assertRaisesRegex(ValueError, '只能来自用户brief'):
            quality.validate_storyboard(reply, payload)

    def test_source_choices_only_include_user_events_not_planner_transitions(self):
        from cores import story_video_core as core

        brief = '雾中出现岛屿和金色神殿，三人进入神殿。'
        beat = dict(BEAT, goal='先站起', action='路飞松绳，娜美把木杆抵在甲板上，然后转头',
                    ending='木杆已放妥', dialogue=[dict(speaker='c2', text='先放下木杆。')])
        _, payload, schema = quality.storyboard_request(
            brief, '电影质感', {'beats': [beat]}, beat, WRITTEN, {}, 15)
        choices = payload['source_quote_choices']
        self.assertIn(brief, choices)
        self.assertIn('雾中出现岛屿和金色神殿', choices)
        self.assertIn('三人进入神殿', choices)
        self.assertTrue(all(choice in brief for choice in choices))
        self.assertFalse(any('松绳' in choice or '木杆' in choice for choice in choices))
        self.assertEqual(schema['properties']['event_contract']['items']['properties']['source_quote']['enum'], choices)
        reply = storyboard_fixture(payload)
        reply['event_contract'][0]['source_quote'] = choices[1]
        core.validate_schema(reply, schema)
        quality.validate_storyboard(reply, payload)
        reply['event_contract'][0]['source_quote'] = beat['action']
        with self.assertRaises(ValueError):
            core.validate_schema(reply, schema)

    def timed_storyboard(self, lines):
        written = copy.deepcopy(WRITTEN)
        written['dialogue'] = copy.deepcopy(lines)
        written['timeline'] = [dict(start=0, end=3, action='grab the gem', visible_result='gem held in hand')]
        request = quality.storyboard_request(BRIEF, '真人电影', {'beats': [BEAT]},
                                              BEAT, written, {'actual_visible': '宝石仍在台座上'}, 15)
        return request[1], written

    def test_late_warning_cannot_be_approved_but_can_return_blocking_feedback(self):
        payload, _ = self.timed_storyboard([
            dict(speaker='c1', text='路飞，先别碰！', start=9.5, end=12)])
        reply = storyboard_fixture(payload)
        reply['dialogue_timing'] = [dict(line_index=0, relation='before', timeline_index=0,
            boundary='end', reason='提醒须在“grab the gem”完成之前说完')]
        with self.assertRaisesRegex(ValueError, '12.000.*3.000'):
            quality.validate_storyboard(reply, payload)
        reply['passed'] = False
        reply['issues'] = [dict(severity='blocking', reason='警告晚于拿取宝石',
                                correction='把警告移到取宝石之前，保留原话')]
        quality.validate_storyboard(reply, payload)
        reply.update(passed=True, issues=[])
        reply['dialogue_timing'][0].update(relation='independent', timeline_index=-1,
                                           reason='这句是独立闲聊')
        with self.assertRaisesRegex(ValueError, '提醒/制止'):
            quality.validate_storyboard(reply, payload)

    def test_verbatim_action_excerpt_need_not_repeat_the_entire_timeline_sentence(self):
        _, written = self.timed_storyboard([dict(speaker='c1', text='这道光真亮。', start=1, end=2)])
        written['timeline'][0]['action'] = 'Camera moves closer. Luffy stretches arm, grabs floating gem, retracts it. Others follow.'
        _, payload, _ = quality.storyboard_request(BRIEF, '真人电影', {'beats': [BEAT]},
            BEAT, written, {'actual_visible': '宝石仍在台座上'}, 15)
        reply = storyboard_fixture(payload)
        reply['dialogue_timing'] = [dict(line_index=0, relation='overlap', timeline_index=0,
            boundary='start', reason='Luffy stretches arm, grabs floating gem, retracts it.')]
        quality.validate_storyboard(reply, payload)
        reply['dialogue_timing'][0]['reason'] = 'invented action outside the actual timeline'
        with self.assertRaisesRegex(ValueError, '真实timeline'):
            quality.validate_storyboard(reply, payload)

    def test_real_before_after_and_overlap_anchors_pass_without_changing_written(self):
        for text, start, end, relation in (
                ('先别碰！', .5, 2, 'before'), ('拿到啦！', 3.1, 4, 'after'),
                ('这道光真亮。', 1, 2, 'overlap')):
            with self.subTest(relation=relation):
                payload, written = self.timed_storyboard([dict(speaker='c1', text=text, start=start, end=end)])
                original = copy.deepcopy(written)
                reply = storyboard_fixture(payload)
                reply['dialogue_timing'] = [dict(line_index=0, relation=relation,
                    timeline_index=0, boundary='end', reason='参照真实动作“grab the gem”安排本句')]
                quality.validate_storyboard(reply, payload)
                self.assertEqual(payload['written'], original)
                self.assertEqual(written, original)

    def test_neutral_speech_still_requires_complete_unique_per_line_coverage(self):
        payload, _ = self.timed_storyboard([
            dict(speaker='c1', text='今天风真大。', start=0, end=1),
            dict(speaker='c2', text='景色真美。', start=2, end=3)])
        reply = storyboard_fixture(payload)
        with self.assertRaisesRegex(ValueError, 'dialogue_timing'):
            quality.validate_storyboard(reply, payload)
        timing = dict(line_index=0, relation='independent', timeline_index=-1,
                      boundary='start', reason='环境感叹没有动作因果依赖')
        reply['dialogue_timing'] = [copy.deepcopy(timing), copy.deepcopy(timing)]
        with self.assertRaisesRegex(ValueError, '不重复'):
            quality.validate_storyboard(reply, payload)
        reply['dialogue_timing'][1]['line_index'] = 1
        quality.validate_storyboard(reply, payload)

    def test_contract_times_ids_and_signature_are_checked(self):
        _, payload, _ = self.storyboard()
        for defect in ('old_signature', 'repeated_id', 'outside_clip', 'zero_transition'):
            reply = storyboard_fixture(payload)
            if defect == 'old_signature':
                reply['request_signature'] = 'old'
            elif defect == 'repeated_id':
                reply['event_contract'].append(copy.deepcopy(reply['event_contract'][0]))
            elif defect == 'outside_clip':
                reply['event_contract'][0]['end'] = 16
            else:
                reply['event_contract'][0]['end'] = reply['event_contract'][0]['start']
            with self.subTest(defect=defect), self.assertRaises(ValueError):
                quality.validate_storyboard(reply, payload)

    def test_transition_requires_different_ordered_before_action_after_frames(self):
        _, payload, _ = self.acceptance()
        reply = acceptance_fixture(payload)
        quality.validate_acceptance(reply, payload)
        for defect in ('same_frame', 'missing_action', 'reversed_times'):
            changed = copy.deepcopy(reply)
            if defect == 'same_frame':
                for field in ('before', 'action', 'after'):
                    changed['events'][0][field] = [evidence('contact')]
            elif defect == 'missing_action':
                changed['events'][0]['action'] = []
            else:
                changed['events'][0]['before'] = [evidence('after')]
            with self.subTest(defect=defect), self.assertRaises(ValueError):
                quality.validate_acceptance(changed, payload)

    def test_decisive_result_still_needs_distinct_before_and_after_evidence(self):
        _, payload, _ = self.acceptance()
        reply = acceptance_fixture(payload)
        reply['events'][0].update(confirmation_mode='decisive_result', action=[],
            explanation='先见完整阻挡石像，后见同处石像碎块和畅通入口，可明确确认结果')
        quality.validate_acceptance(reply, payload)
        reply['events'][0]['before'] = [evidence('after')]
        with self.assertRaises(ValueError):
            quality.validate_acceptance(reply, payload)

    def test_static_goal_can_use_a_clear_single_result_frame(self):
        event = dict(EVENT, kind='state', source_quote='三人在殿内',
                     before='无需证明过去动作', action='检查画面', after='三人在殿内')
        _, payload, _ = self.acceptance(contract=[event])
        reply = acceptance_fixture(payload)
        reply['events'][0].update(confirmation_mode='state', before=[], action=[],
                                  after=[evidence('last', '三人均站在门内')])
        quality.validate_acceptance(reply, payload)

    def test_unknown_cannot_pass_and_false_contradiction_needs_evidence(self):
        _, payload, _ = self.acceptance()
        reply = acceptance_fixture(payload)
        reply['events'][0].update(status='insufficient_evidence', before=[], action=[], after=[],
                                  explanation='关键接触和结果被遮挡')
        with self.assertRaisesRegex(ValueError, 'unknown'):
            quality.validate_acceptance(reply, payload)
        reply.update(passed=False, follow_up_times=[8.5, 9.5, 10])
        quality.validate_acceptance(reply, payload)
        reply['events'][0]['status'] = 'contradicted'
        with self.assertRaisesRegex(ValueError, '相反画面证据'):
            quality.validate_acceptance(reply, payload)

    def test_event_coverage_and_real_sample_ids_are_required(self):
        _, payload, schema = self.acceptance()
        self.assertEqual(schema['properties']['events']['items']['properties']['before']['items']
                         ['properties']['sample_id']['enum'], [sample['id'] for sample in SAMPLES])
        for defect in ('missing_event', 'wrong_event', 'invented_frame'):
            reply = acceptance_fixture(payload)
            if defect == 'missing_event':
                reply['events'] = []
            elif defect == 'wrong_event':
                reply['events'][0]['id'] = 'invented_event'
            else:
                reply['events'][0]['after'][0]['sample_id'] = 'never_attached'
            with self.subTest(defect=defect), self.assertRaises(ValueError):
                quality.validate_acceptance(reply, payload)

    def test_critical_issue_requires_evidence_and_prevents_adoption(self):
        _, payload, _ = self.acceptance()
        reply = acceptance_fixture(payload)
        reply['critical_issues'] = [dict(category='duplicated_main_subjects',
            reason='最后一帧出现两名同样的草帽男子', evidence=[evidence('last')])]
        with self.assertRaisesRegex(ValueError, 'passed'):
            quality.validate_acceptance(reply, payload)
        reply['passed'] = False
        quality.validate_acceptance(reply, payload)
        reply['critical_issues'][0]['evidence'] = []
        with self.assertRaisesRegex(ValueError, '图像证据'):
            quality.validate_acceptance(reply, payload)

    def test_follow_up_is_bounded_to_retained_last_frame_and_three_targets(self):
        _, payload, _ = self.acceptance()
        reply = acceptance_fixture(payload)
        reply['passed'] = False
        reply['events'][0]['status'] = 'insufficient_evidence'
        for values in ([15], [-.1], [8, 8], [1, 2, 3, 4], [float('nan')]):
            reply['follow_up_times'] = values
            with self.subTest(times=values), self.assertRaises(ValueError):
                quality.validate_acceptance(reply, payload)
        reply['follow_up_times'] = [0, 8.5, 14.958333]
        quality.validate_acceptance(reply, payload)
        passed = acceptance_fixture(payload)
        passed['follow_up_times'] = [8.5]
        with self.assertRaises(ValueError):
            quality.validate_acceptance(passed, payload)

    def test_raw_tail_frames_and_duplicate_ids_are_rejected(self):
        for defect in ('raw_tail', 'duplicate_id'):
            samples = copy.deepcopy(SAMPLES)
            if defect == 'raw_tail':
                samples[-1]['time_seconds'] = 15.042
            else:
                samples[-1]['id'] = samples[0]['id']
            with self.subTest(defect=defect), self.assertRaises(ValueError):
                self.acceptance(samples=samples)

    def test_signatures_change_with_samples_or_audio_and_reject_cached_verdict(self):
        _, original, _ = self.acceptance()
        reply = acceptance_fixture(original)
        samples = copy.deepcopy(SAMPLES)
        samples[1]['time_seconds'] = 7.6
        _, changed, _ = self.acceptance(samples=samples)
        self.assertNotEqual(changed['request_signature'], original['request_signature'])
        with self.assertRaisesRegex(ValueError, '签名'):
            quality.validate_acceptance(reply, changed)
        audio = {'status': 'passed', 'transcript': '先别碰', 'segments': [{'start': 12, 'end': 13}]}
        _, with_audio, _ = self.acceptance(audio=audio)
        self.assertNotEqual(with_audio['request_signature'], original['request_signature'])
        self.assertEqual(with_audio['audio_review'], audio)
        self.assertEqual(quality.signature({'a': 1, 'b': 2}), quality.signature({'b': 2, 'a': 1}))

    def test_audio_unavailable_or_failed_cannot_be_silently_skipped(self):
        _, payload, _ = self.acceptance()
        visual = acceptance_fixture(payload)
        self.assertTrue(quality.adoption_decision(visual, {'status': 'passed'})['passed'])
        for audio in (None, {'status': 'unavailable'}, {'status': 'failed', 'reason': '原句未识别'}):
            with self.subTest(audio=audio):
                result = quality.adoption_decision(visual, audio)
                self.assertFalse(result['passed'])
                self.assertTrue(result['reasons'])
        visual['events'][0]['status'] = 'insufficient_evidence'
        self.assertFalse(quality.adoption_decision(visual, {'status': 'passed'})['passed'])

    def test_audio_failures_expose_up_to_three_missing_original_lines(self):
        _, payload, _ = self.acceptance()
        audio = {'status': 'failed', 'issues': [dict(reason='未识别到原句', expected='原话%d' % index)
                                               for index in range(4)]}
        result = quality.adoption_decision(acceptance_fixture(payload), audio)
        self.assertFalse(result['passed'])
        for index in range(3):
            self.assertTrue(any('原话%d' % index in reason for reason in result['reasons']))
        self.assertFalse(any('原话3' in reason for reason in result['reasons']))

    def test_sampling_targets_are_bounded_unique_and_are_not_observation_claims(self):
        events = [dict(EVENT, id='one', start=1, end=4),
                  dict(EVENT, id='two', start=5, end=9),
                  dict(EVENT, id='three', start=10, end=15)]
        original = copy.deepcopy(events)
        times = quality.sampling_times(events, 15)
        self.assertLessEqual(len(times), 7)
        self.assertEqual(times, sorted(set(times)))
        self.assertTrue(all(0 <= time < 15 for time in times))
        self.assertEqual(events, original)


if __name__ == '__main__':
    unittest.main()
