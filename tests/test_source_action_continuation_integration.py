"""Boundary prefix suppression through the formal source-writing hook.

Replies are local dictionaries. No real model, service, media generation,
production project writes or filesystem deletion is performed.
"""
import copy
import json
import os
import unittest

os.environ['V41_NO_MODEL'] = '1'

from cores import workbench_story_director as writer
from tests import test_source_shot_compiler as source_fixture
from tests import test_workbench_story_director_release as release_fixture


RISE_AND_ARRIVE = '阿岚从椅子上起身，走到木门旁。'
REPLAY_AND_PROGRESS = '阿岚从椅子上起身，走到木门旁，左手压住门把。'


class SourceActionContinuationIntegrationTests(unittest.TestCase):
    def inputs(self, first=RISE_AND_ARRIVE, second=REPLAY_AND_PROGRESS,
               second_scene='竹庭', user_timing=False):
        _, cards, settings = release_fixture.fixture()
        settings.pop('video_action_compiler')
        settings['_director_reference_paths'] = {
            name: __file__ for name in ('阿岚', '顾亭', '竹庭', second_scene)}
        scenes = list(dict.fromkeys(['竹庭', second_scene]))
        slices = []
        for index, (action, scene, speaker, words, did) in enumerate((
                (first, '竹庭', '阿岚', '先等等。', 9),
                (second, second_scene, '顾亭', '我看清楚了。', 12)), 1):
            text = action + '\n【D%d】%s：%s' % (did, speaker, words)
            if user_timing and index == 2:
                blocks = [[0, 3, action, []],
                          [3, 12, '【D%d】%s：%s' % (did, speaker, words), [did]]]
            else:
                blocks = [[0, 12, text, [did]]]
            director = dict(flow='source-v1', cast=['阿岚', '顾亭'],
                camera='按当前行动交代人物与门的位置。',
                purpose='依次推进当前行动和交流。',
                start_state='', target_state='unknown',
                blocks=blocks, says=[[speaker, words]])
            if user_timing and index == 2:
                director['timing_source'] = 'user'
            slices.append(dict(no=index, seconds=12, scene_hint=scene,
                               chars=['阿岚', '顾亭'], text=text, director=director))
        return slices, cards, settings, scenes

    def write(self, *args, start=0, count=None, state=None, **kwargs):
        slices, cards, settings, scenes = self.inputs(*args, **kwargs)
        before = copy.deepcopy(slices)
        calls = []

        def q(system, user, **options):
            payload = json.loads(user)
            calls.append(copy.deepcopy(payload))
            plan = source_fixture.model_reply(payload)
            for shot in plan['shots']:
                shot['acting'] = []
            return json.dumps(plan, ensure_ascii=False)

        actual = (lambda index: copy.deepcopy(state)) if state is not None else None
        rows = writer.make_prompts(slices, cards, scenes, q, settings=settings,
                                  start=start, count=count, actual_state_for=actual)
        # The writer has always updated start-state metadata; the original
        # story, block time ranges, dialogue IDs and words remain untouched.
        for current, original in zip(slices, before):
            self.assertEqual(current['text'], original['text'])
            self.assertEqual(current['director']['blocks'], original['director']['blocks'])
            self.assertEqual(current['director']['says'], original['director']['says'])
        for row in rows:
            original = before[row['n'] - 1]
            self.assertEqual(row['source_text'], original['text'])
            self.assertEqual(row['director_contract']['blocks'], original['director']['blocks'])
            self.assertEqual(row['director_contract']['says'], original['director']['says'])
            self.assertFalse(row['source_fallback'])
        return rows, calls, before

    def body(self, row):
        return row['prompt'].split('detailed_description:\n', 1)[1].split(
            '\n\noverall_soundscape:', 1)[0]

    def omissions(self, row):
        return row['director_compilation']['source_shot_audit'].get(
            'boundary_action_omissions', [])

    def observed_standing(self, verified=True):
        return dict(actual_visible='阿岚站在木门旁。', scene='竹庭',
            endpoint_evidence=dict(version='explicit-final-frame-v1',
                                   frame_index=7, verified=verified),
            characters=[dict(id='a', frame_index=7, certainty='observed',
                             visibility='visible', position='木门旁',
                             pose='站立', hands='unknown')], props=[])

    def test_adjacent_planned_prefix_is_not_replayed_in_full_or_partial_rewrite(self):
        for start in (0, 1):
            with self.subTest(start=start):
                rows, calls, _ = self.write(start=start, count=1 if start else None)
                row = rows[-1]
                body = self.body(row)
                for repeated in ('起身', '站起', '走到'):
                    self.assertNotIn(repeated, body)
                    self.assertNotIn(repeated, json.dumps(calls[-1]['source_units'], ensure_ascii=False))
                self.assertEqual(body.count('压住门把。'), 1)
                self.assertEqual(body.count('我看清楚了。'), 1)
                self.assertIn('(S2)', body)
                suppressed = [item for item in self.omissions(row)
                              if item['method'] == 'suppress_repeated_prefix']
                self.assertEqual(len(suppressed), 2)
                self.assertEqual({item['source_kind'] for item in suppressed}, {'planned_source'})
                self.assertNotIn('observed_start_state', json.dumps(self.omissions(row), ensure_ascii=False))
                self.assertNotIn('environment from <Picture', row['prompt'])
                if start == 0:
                    self.assertIn('environment from <Picture 3>', rows[0]['prompt'])

    def test_verified_endpoint_standing_suppresses_a_conflicting_new_rise(self):
        rows, calls, _ = self.write(first='顾亭翻看工作记录。',
            second='阿岚起身，左手压住门把。', start=1, count=1,
            state=self.observed_standing())
        row = rows[0]
        self.assertNotIn('起身', self.body(row))
        self.assertEqual(self.body(row).count('压住门把。'), 1)
        self.assertTrue(calls[0]['endpoint_start_state'])
        self.assertTrue(any(item.get('source_kind') == 'observed_start_state'
                            and item.get('method') == 'suppress_repeated_prefix'
                            for item in self.omissions(row)))

    def test_unverified_standing_cannot_be_used_as_observed_completion(self):
        state = self.observed_standing(verified=False)
        rows, calls, _ = self.write(first='顾亭翻看工作记录。',
            second='阿岚起身，左手压住门把。', start=1, count=1, state=state)
        self.assertIn('起身', self.body(rows[0]))
        self.assertEqual(calls[0]['endpoint_start_state'], '')
        self.assertFalse(any(item.get('source_kind') == 'observed_start_state'
                             and item.get('method') == 'suppress_repeated_prefix'
                             for item in self.omissions(rows[0])))

    def test_verified_sitting_overrides_the_previous_written_standing_arrival(self):
        for verified in (True, False):
            with self.subTest(verified=verified):
                state = self.observed_standing(verified=verified)
                state['actual_visible'] = '阿岚坐在椅子旁。'
                state['characters'][0].update(position='椅子旁', pose='坐姿')
                rows, calls, _ = self.write(start=1, count=1, state=state)
                row = rows[0]
                if verified:
                    # Recompute from the original slice: the earlier written
                    # standing/arrival cannot erase a transition still needed
                    # from this contradictory but verified real endpoint.
                    self.assertIn('起身', self.body(row))
                    self.assertIn('走到木门旁', self.body(row))
                    self.assertIn('椅子旁', calls[0]['endpoint_start_state'])
                    self.assertIn('坐姿', calls[0]['endpoint_start_state'])
                    self.assertFalse(any(item.get('method') == 'suppress_repeated_prefix'
                                         for item in self.omissions(row)))
                else:
                    # Unverified visual wording cannot replace the adjacent
                    # source's declared standing state or claim observed proof.
                    self.assertNotIn('起身', self.body(row))
                    self.assertNotIn('走到', self.body(row))
                    self.assertEqual(calls[0]['endpoint_start_state'], '')
                    suppressed = [item for item in self.omissions(row)
                                  if item.get('method') == 'suppress_repeated_prefix']
                    self.assertEqual(len(suppressed), 2)
                    self.assertEqual({item['source_kind'] for item in suppressed}, {'planned_source'})
                self.assertEqual(self.body(row).count('压住门把。'), 1)
                self.assertEqual(self.body(row).count('我看清楚了。'), 1)

    def test_changed_scene_and_explicit_time_jump_preserve_the_new_rise(self):
        cases = [dict(second_scene='门厅'),
                 dict(second='第二天清晨，' + REPLAY_AND_PROGRESS)]
        for changes in cases:
            with self.subTest(changes=changes):
                rows, calls, _ = self.write(start=1, count=1,
                    state=self.observed_standing(), **changes)
                self.assertIn('起身', self.body(rows[0]))
                self.assertIn('走到木门旁', self.body(rows[0]))
                self.assertEqual(calls[0]['endpoint_start_state'], '')
                self.assertFalse(any(item.get('method') == 'suppress_repeated_prefix'
                                     for item in self.omissions(rows[0])))

    def test_explicit_new_round_or_intervening_sit_preserves_the_new_transition(self):
        cases = [
            '阿岚再次起身，走到木门旁，左手压住门把。',
            '阿岚先坐下检查表盘，再起身，走到木门旁，左手压住门把。',
        ]
        for source in cases:
            with self.subTest(source=source):
                rows, _, _ = self.write(second=source, start=1, count=1,
                    state=self.observed_standing())
                self.assertIn('起身', self.body(rows[0]))
                self.assertIn('走到木门旁', self.body(rows[0]))
                self.assertEqual(self.body(rows[0]).count('压住门把。'), 1)
                self.assertFalse(any(item.get('method') == 'suppress_repeated_prefix'
                                     for item in self.omissions(rows[0])))

    def test_another_actors_arrival_is_not_the_previous_actors_event(self):
        rows, _, _ = self.write(second='顾亭走到木门旁，抬眼。', start=1, count=1)
        self.assertIn('<Subject 2>走到木门旁', self.body(rows[0]))
        self.assertEqual(self.body(rows[0]).count('我看清楚了。'), 1)
        self.assertFalse(any(item.get('method') == 'suppress_repeated_prefix'
                             for item in self.omissions(rows[0])))

    def test_user_time_blocks_keep_original_boundaries_and_dialogue_after_trimming(self):
        rows, calls, before = self.write(start=1, count=1, user_timing=True)
        row = rows[0]
        self.assertNotIn('起身', self.body(row))
        self.assertNotIn('走到', self.body(row))
        self.assertEqual(self.body(row).count('我看清楚了。'), 1)
        audit = row['director_compilation']['source_shot_audit']
        self.assertEqual([(shot['start'], shot['end']) for shot in audit['shots']],
                         [(0, 3), (3, 12)])
        self.assertEqual(row['director_contract']['blocks'], before[1]['director']['blocks'])
        self.assertEqual(calls[0]['source_units'][0]['block_end'], 3)
        self.assertEqual(calls[0]['source_units'][-1]['block_start'], 3)
        self.assertEqual(row['director_compilation']['dialogue'][0]['start'], 3)

    def test_static_standing_description_does_not_create_another_transition(self):
        source = '阿岚站在木门旁，左手压住门把。'
        rows, _, _ = self.write(second=source, start=1, count=1)
        self.assertIn('<Subject 1>站在木门旁', self.body(rows[0]))
        self.assertEqual(self.body(rows[0]).count('压住门把。'), 1)
        self.assertNotIn('起身', self.body(rows[0]))
        self.assertFalse(any(item.get('method') == 'suppress_repeated_prefix'
                             for item in self.omissions(rows[0])))

    def test_exact_event_tail_head_overlap_is_suppressed_but_other_objects_remain(self):
        cases = [
            ('阿岚推开木门。', '阿岚推开窗户。'),
            ('阿岚递出怀表。', '阿岚递出信封。'),
        ]
        for previous, other_object in cases:
            with self.subTest(previous=previous):
                repeated_core = previous.rstrip('。')
                repeated_source = repeated_core + '，阿岚抬眼。'
                rows, calls, _ = self.write(first=previous, second=repeated_source,
                                           start=1, count=1)
                row = rows[0]
                # Exact lexical overlap includes actor and object. Only its
                # leading event is shortened; the newly authored action stays.
                self.assertNotIn(repeated_core.replace('阿岚', '<Subject 1>'), self.body(row))
                self.assertNotIn(repeated_core,
                                 json.dumps(calls[0]['source_units'], ensure_ascii=False))
                self.assertEqual(self.body(row).count('抬眼。'), 1)
                self.assertEqual(self.body(row).count('我看清楚了。'), 1)
                exact = [item for item in self.omissions(row)
                         if item.get('method') == 'suppress_exact_tail_head']
                self.assertEqual(len(exact), 1)
                self.assertEqual(exact[0]['source_kind'], 'planned_source')
                self.assertEqual(exact[0]['actor'], '阿岚')

                different_core = other_object.rstrip('。')
                rows, _, _ = self.write(first=previous,
                    second=different_core + '，阿岚抬眼。', start=1, count=1)
                row = rows[0]
                self.assertIn(different_core.replace('阿岚', '<Subject 1>'), self.body(row))
                self.assertEqual(self.body(row).count('抬眼。'), 1)
                self.assertFalse(any(item.get('method') == 'suppress_exact_tail_head'
                                     for item in self.omissions(row)))


if __name__ == '__main__':
    unittest.main()
