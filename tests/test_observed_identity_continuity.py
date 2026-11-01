"""Observed design drift cannot replace source identities; no model or disk writes."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from cores import observed_identity_continuity as continuity
from cores import workbench_story_director as release
from tests.test_workbench_story_director_release import fixture, response


class ObservedIdentityContinuityTests(unittest.TestCase):
    def references(self):
        return [dict(role='character', name='顾亭', character_id='b', picture=1, subject=1)]

    def observation(self):
        return dict(actual_visible='黑发女子站在门边，白色衣着明显。', scene='庭院中，木门关闭。',
            characters=[dict(id='S2', appearance='黑色盘发，身穿白色长裙。',
                position='画面右侧，站在木门边。', pose='背对镜头，黑色盘发清晰可见。',
                hands='双手交握，右手握着红色盒子。', visibility='visible', certainty='observed')],
            props=[dict(id='box', appearance='红色木盒', holder='S2', count=1)],
            actions=['右手接过红色盒子。'], completed=['黑发女子接过红色盒子。'],
            unknowns=['面部不可见，身份未知。'])

    def test_wrong_design_is_isolated_while_real_dynamic_evidence_survives(self):
        original = self.observation()
        before = copy.deepcopy(original)
        value = continuity.dynamic_state(original, self.references())
        dynamic_text = json.dumps(value, ensure_ascii=False)
        self.assertNotIn('黑色盘发', dynamic_text)
        self.assertNotIn('黑发', dynamic_text)
        self.assertNotIn('白色', dynamic_text)
        self.assertIn('站在门边', dynamic_text)
        self.assertEqual(value['characters'][0]['position'], before['characters'][0]['position'])
        self.assertEqual(value['characters'][0]['pose'], '背对镜头')
        self.assertEqual(value['characters'][0]['hands'], before['characters'][0]['hands'])
        self.assertEqual(value['props'], before['props'])
        self.assertEqual(value['actions'], before['actions'])
        self.assertEqual(value['completed'], ['女子接过红色盒子'])
        self.assertEqual(value['scene'], before['scene'])
        self.assertEqual(original, before)

    def test_observer_numbers_are_not_rebound_to_current_subject_numbers(self):
        observation = self.observation()
        unknown = continuity.dynamic_state(observation, self.references())['characters'][0]
        self.assertEqual(unknown['observation_id'], 'S2')
        self.assertNotIn('character_id', unknown)
        observation['characters'][0]['character_id'] = 'b'
        known = continuity.dynamic_state(observation, self.references())['characters'][0]
        self.assertEqual(known['character_id'], 'b')

    def test_removing_design_keeps_dynamic_text_before_and_after_it(self):
        for text, expected in (
                ('黑发女子在木门右侧端着红色木盒。', '女子在木门右侧端着红色木盒'),
                ('站在门边的黑发女子握着红色木盒。', '站在门边的女子握着红色木盒'),
                ('身穿蓝裙的女子站在门边并将外套递给同伴。', '女子站在门边并将外套递给同伴')):
            with self.subTest(text=text):
                observation = dict(actual_visible=text, characters=[])
                self.assertEqual(continuity.dynamic_state(observation, self.references())['actual_visible'], expected)

    def test_clothing_as_held_or_transferred_object_is_not_filtered(self):
        observation = dict(actual_visible='女子将蓝色外套递给同伴。', characters=[dict(
            id='S2', hands='右手握着白色长裙，左手将红色围巾递给同伴。')],
            completed=['蓝色外套已经交给同伴。'])
        state = continuity.dynamic_state(observation, self.references())
        self.assertEqual(state['actual_visible'], observation['actual_visible'])
        self.assertEqual(state['characters'][0]['hands'], observation['characters'][0]['hands'])
        self.assertEqual(state['completed'], observation['completed'])

    def test_explicit_source_changes_remain_complete_and_in_order(self):
        source = [dict(text='顾亭换上蓝色外套，将头发扎成马尾。她拿起红盒子。'),
                  dict(text='顾亭没有换回旧衣。阿岚说：“下一场换上绿外套。”')]
        before = copy.deepcopy(source)
        evidence = continuity.source_appearance_evidence(source, self.references(),
            [dict(name='顾亭'), dict(name='阿岚')])
        self.assertEqual([row['source_clause'] for row in evidence],
                         ['顾亭换上蓝色外套', '将头发扎成马尾'])
        self.assertEqual([row['active_attributes'] for row in evidence], [['outerwear'], ['hair_style']])
        self.assertEqual(source, before)

    def test_long_descriptive_history_is_bounded_and_only_current_cast_changes_remain(self):
        source = [dict(text='顾亭穿着白色衣服，袖口随着抬手而收拢。' * 20) for _ in range(100)]
        source[0]['text'] += '顾亭换上蓝色外套。顾亭把头发染成银色。'
        source[1]['text'] += '顾亭戴上红色围巾。顾亭将头发扎成马尾。'
        source[50]['text'] += '顾亭换上绿色外套。'
        source[-1]['text'] += '阿岚换上黑色礼服。'
        evidence = continuity.source_appearance_evidence(source, self.references(),
            [dict(name='顾亭'), dict(name='阿岚')])
        self.assertLess(len(json.dumps(evidence, ensure_ascii=False)), 1000)
        self.assertEqual(len(evidence), 4)
        self.assertTrue(all(row['character'] == '顾亭' for row in evidence))
        clauses = [row['source_clause'] for row in evidence]
        self.assertIn('顾亭换上绿色外套', clauses)
        self.assertNotIn('顾亭换上蓝色外套', clauses)
        self.assertIn('顾亭戴上红色围巾', clauses)
        self.assertIn('顾亭把头发染成银色', clauses)
        self.assertIn('顾亭将头发扎成马尾', clauses)
        self.assertNotIn('袖口', json.dumps(evidence, ensure_ascii=False))

    def test_repeated_events_deduplicate_and_whole_outfit_replaces_old_top_and_bottom(self):
        source = [dict(text='顾亭穿上白色衬衫。顾亭穿上黑色长裤。顾亭披上蓝色外套。'),
                  dict(text='顾亭换上绿色连衣裙。顾亭戴上红色围巾。' * 5)]
        evidence = continuity.source_appearance_evidence(source, self.references())
        clauses = [row['source_clause'] for row in evidence]
        self.assertEqual(len(evidence), 3)
        self.assertIn('顾亭披上蓝色外套', clauses)
        self.assertIn('顾亭换上绿色连衣裙', clauses)
        self.assertIn('顾亭戴上红色围巾', clauses)
        self.assertNotIn('顾亭穿上白色衬衫', clauses)
        self.assertNotIn('顾亭穿上黑色长裤', clauses)

    def test_irreducible_source_event_is_not_silently_truncated(self):
        source = [dict(text='顾亭换上' + '蓝色织纹' * 1500 + '外套。')]
        before = copy.deepcopy(source)
        with self.assertRaises(continuity.AppearanceEvidenceLimitError) as caught:
            continuity.source_appearance_evidence(source, self.references())
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(source, before)

    def test_ordinary_unknown_state_keeps_its_existing_semantics(self):
        unknown = dict(actual_visible='unknown', scene='unknown', characters=[], props=[],
                       actions=[], completed=[], unknowns=['尚无实际末帧。'])
        self.assertEqual(continuity.dynamic_state(unknown, []), unknown)

    def test_released_writer_keeps_audit_source_changes_and_context_budget(self):
        slices, cards, settings = fixture()
        changed = '顾亭换上蓝色外套，将头发扎成马尾。'
        slices[0]['text'] = changed + slices[0]['text']
        slices[0]['director']['blocks'][0][2] = changed + slices[0]['director']['blocks'][0][2]
        original_observation = self.observation()
        observation_before = copy.deepcopy(original_observation)
        submitted, traces = [], []

        def query(system, user, **kwargs):
            payload = json.loads(user)
            submitted.append((system, payload, kwargs))
            return json.dumps(response(payload), ensure_ascii=False)

        with patch.object(Path, 'mkdir'), patch.object(Path, 'write_text',
                side_effect=lambda text, **kwargs: traces.append(json.loads(text))):
            rows = release.make_prompts(slices, cards, ['竹庭'], query, settings=settings,
                start=1, count=1, actual_state_for=lambda index: original_observation,
                trace_dir='not_created')
        system, payload, options = submitted[0]
        self.assertEqual(len(submitted), 1)
        self.assertTrue(options['_check_context'])
        self.assertNotIn('_observation_raw', payload)
        self.assertNotIn('黑色盘发', json.dumps(payload, ensure_ascii=False))
        self.assertNotIn('白色长裙', json.dumps(payload, ensure_ascii=False))
        self.assertEqual(payload['beat']['source_text'], slices[1]['text'])
        self.assertNotIn('source_contract', payload['beat'])
        self.assertNotIn('source_text', payload['story_plan']['beats'][0])
        self.assertEqual([row['source_clause'] for row in payload['appearance_source_evidence']],
                         ['顾亭换上蓝色外套', '将头发扎成马尾'])
        self.assertIn('原文明确且已经发生的换装', system)
        self.assertIn('不能一律恢复参考图旧衣或旧发型', system)
        self.assertEqual(traces[0]['attempts'][0]['observation_raw'], observation_before)
        self.assertEqual(original_observation, observation_before)
        self.assertFalse(rows[0]['source_fallback'])
        self.assertIn('沿用对应人设图', rows[0]['prompt'])
        self.assertNotIn('Identity authority (source-and-reference-identity-v1):', rows[0]['prompt'])
        self.assertNotIn('人物外观完整沿用对应人设图', rows[0]['prompt'])
        self.assertIn('这回稳了。', rows[0]['prompt'])


if __name__ == '__main__':
    unittest.main()
