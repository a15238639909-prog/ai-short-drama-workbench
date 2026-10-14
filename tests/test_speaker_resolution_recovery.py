import json
import os
import unittest

os.environ['V41_NO_MODEL'] = '1'
from cores import director_flow as director, episode_contract as contract


CARDS = [{'name': '林雨', 'age': 28, 'sex': '女', 'identity': '导游'},
         {'name': '陈安', 'age': 30, 'sex': '男', 'identity': '管理员'},
         {'name': '游客们', 'age': 30, 'entity_type': 'group', 'aliases': ['众游客'],
          'identity': '三名成年游客', 'group_count': 3}]


def length_error():
    error = RuntimeError('文字输出达到长度上限，未完整结束')
    error.partial_text = '{"shots": [{"end_unit": 1'
    return error


def slice_fixture(name='林雨', words='门已经打开了。', body=None, cast=None):
    text = body or ('【D1】%s：%s' % (name, words))
    return {'text': text, 'seconds': 10, 'scene_hint': '大厅', 'director': {
        'flow': director.VERSION, 'cast': list(cast if cast is not None else [name]),
        'camera': '平视中景', 'purpose': '回应', 'blocks': [[0, 10, text, [1]]],
        'says': [(name, words)], 'source_contract_version': contract.VERSION,
        'start_state': '', 'end_state_text': '仍停在原处。'}}


class SourceSpeakerTests(unittest.TestCase):
    def no_review(self, *args, **kwargs):
        self.fail('generated source must not be re-reviewed by a model')

    def test_person_and_group_alias_bind_without_model(self):
        source = '【D1】林雨：我们出发吧。\n【D2】众游客：好的。'
        dialogue = [('林雨', '我们出发吧。'), ('众游客', '好的。')]
        script, fixed = director.resolve_speakers(source, dialogue, CARDS, self.no_review)
        self.assertEqual(fixed, [('林雨', '我们出发吧。'), ('游客们', '好的。')])
        self.assertIn('【D2】游客们：好的。', script)

    def test_explicit_nearby_source_speaker_keeps_original_words(self):
        source = '林雨回头说：\n【D1】待核对说话人：门锁住了。'
        trace = []
        script, fixed = director.resolve_speakers(source, [('待核对说话人', '门锁住了。')],
                                                  CARDS, self.no_review, trace=trace)
        self.assertEqual(fixed, [('林雨', '门锁住了。')])
        self.assertIn('林雨回头说：', script)
        self.assertIn('【D1】林雨：门锁住了。', script)
        self.assertFalse(trace[0]['model_review'])

    def test_single_explicit_gender_antecedent_can_bind_source_pronoun(self):
        source = '林雨走近铁门。\n她说：\n【D1】待核对说话人：门锁住了。'
        _, fixed = director.resolve_speakers(source, [('待核对说话人', '门锁住了。')], CARDS, self.no_review)
        self.assertEqual(fixed, [('林雨', '门锁住了。')])

    def test_ambiguous_pronoun_keeps_source_voice_instead_of_guessing(self):
        cards = CARDS + [{'name': '周宁', 'age': 27, 'sex': '女'}]
        source = '林雨和周宁站在门边。\n她说：\n【D1】待核对说话人：我们走吧。'
        trace = []
        script, fixed = director.resolve_speakers(source, [('待核对说话人', '我们走吧。')],
                                                  cards, self.no_review, trace=trace)
        self.assertEqual(fixed, [('她', '我们走吧。')])
        self.assertIn('【D1】她：我们走吧。', script)
        self.assertEqual(trace[0]['bindings'][0]['binding'], 'source_voice')

    def test_explicit_unbound_label_is_preserved_without_manual_gate(self):
        source = '【D1】门外的人：请开门。'
        fixed_script, fixed = director.resolve_speakers(source, [('门外的人', '请开门。')], CARDS, self.no_review)
        self.assertEqual((fixed_script, fixed), (source, [('门外的人', '请开门。')]))

    def test_addressee_is_never_substituted_for_source_speaker(self):
        source = '林雨站在门边。\n她向陈安说：\n【D1】待核对说话人：请开门。'
        _, fixed = director.resolve_speakers(source, [('待核对说话人', '请开门。')], CARDS, self.no_review)
        self.assertEqual(fixed, [('林雨', '请开门。')])
        source = '她向陈安说：\n【D1】待核对说话人：请开门。'
        _, fixed = director.resolve_speakers(source, [('待核对说话人', '请开门。')], CARDS, self.no_review)
        self.assertEqual(fixed, [('她', '请开门。')])
        source = '林雨向陈安说：\n【D1】待核对说话人：请开门。'
        _, fixed = director.resolve_speakers(source, [('待核对说话人', '请开门。')], CARDS, self.no_review)
        self.assertEqual(fixed, [('林雨', '请开门。')])

    def test_many_unbound_lines_cannot_hit_model_output_limit(self):
        words = '请大家先在原地等候。'
        dialogue = [('未具名游客', words)] * 50
        source = '\n'.join('【D%d】未具名游客：%s' % (did, words) for did in range(1, 51))
        script, fixed = director.resolve_speakers(source, dialogue, CARDS, self.no_review)
        self.assertEqual(script, source)
        self.assertEqual(fixed, dialogue)


class DirectorLocalRecoveryTests(unittest.TestCase):
    def test_plan_length_failure_uses_current_source_units_at_4000_budget(self):
        source = '林雨站在大厅。\n林雨：我们走吧。\n陈安：好的。'
        calls = []
        def q(system, user, **kwargs):
            calls.append((json.loads(user), kwargs))
            raise length_error()
        slices = director.plan(source, [{'name': '大厅'}], CARDS, q)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]['mt'], 4000)
        self.assertEqual([tuple(line) for sl in slices for line in sl['director']['says']],
                         [('林雨', '我们走吧。'), ('陈安', '好的。')])
        self.assertTrue(all(4 <= sl['seconds'] <= 15 for sl in slices))
        self.assertIn('林雨站在大厅。', '\n'.join(sl['text'] for sl in slices))

    def test_repeated_broken_plan_json_falls_back_without_losing_dialogue(self):
        calls = []
        def q(system, user, **kwargs):
            calls.append(user)
            return '{"shots": ['
        slices = director.plan('林雨：继续走。\n陈安：继续走。', [{'name': '大厅'}], CARDS, q)
        self.assertEqual(len(calls), 3)
        self.assertEqual([tuple(line) for sl in slices for line in sl['director']['says']],
                         [('林雨', '继续走。'), ('陈安', '继续走。')])

    def test_unbound_voice_can_plan_without_inventing_character_card(self):
        def q(system, user, **kwargs):
            raise length_error()
        slices = director.plan('门外的人：请开门。', [{'name': '大厅'}], CARDS, q)
        self.assertEqual([tuple(row) for row in slices[0]['director']['says']], [('门外的人', '请开门。')])
        self.assertNotIn('门外的人', slices[0]['director']['cast'])

    def test_write_length_failure_uses_lossless_source_fallback(self):
        calls = []
        def q(system, user, **kwargs):
            calls.append(user)
            raise length_error()
        rows = director.make_prompts([slice_fixture()], CARDS, ['大厅'], q)
        self.assertEqual(len(calls), 1)
        self.assertTrue(rows[0]['source_fallback'])
        self.assertEqual(rows[0]['prompt'].count('<d>[Chinese] 门已经打开了。</d>'), 1)

    def test_repeated_write_json_error_preserves_source_action_and_speech(self):
        calls = []
        def q(system, user, **kwargs):
            calls.append(user)
            return 'not JSON'
        body = '林雨双手握住水瓶。\n【D1】林雨：我们走吧。'
        rows = director.make_prompts([slice_fixture(words='我们走吧。', body=body)], CARDS, ['大厅'], q)
        self.assertEqual(len(calls), 3)
        self.assertTrue(rows[0]['source_fallback'])
        self.assertIn('双手握住水瓶', rows[0]['prompt'])
        self.assertEqual(rows[0]['prompt'].count('<d>[Chinese] 我们走吧。</d>'), 1)

    def test_service_disconnect_is_not_reported_as_success(self):
        def q(system, user, **kwargs):
            raise RuntimeError('连不上文字模型')
        with self.assertRaisesRegex(RuntimeError, '连不上文字模型'):
            director.plan('林雨站在大厅。', [{'name': '大厅'}], CARDS, q)


class SourceVoiceAndCollectiveTests(unittest.TestCase):
    def test_anonymous_voice_needs_no_fake_image_or_person(self):
        sl = slice_fixture(name='门外的人', words='请开门。', cast=[])
        clip = director._clip(sl, 1, CARDS, {}, '', {}, '')
        self.assertEqual([row['name'] for row in clip['references']], ['大厅'])
        self.assertEqual(clip['source_order'][0]['binding'], 'source_voice')
        data = director.source_fallback(clip)
        prompt = director.assemble(data, clip)
        self.assertIn('The source voice "门外的人" (S1) says', prompt)
        self.assertEqual(prompt.count('<d>[Chinese] 请开门。</d>'), 1)
        self.assertNotIn('<Picture 2>', prompt)

    def test_named_collective_expands_member_refs_and_uses_compound_voice_ids(self):
        group = {'name': '两人', 'age': 29, 'entity_type': 'group',
                 'group_members': ['林雨', '陈安'], 'group_count': 2}
        cards = CARDS[:2] + [group]
        sl = slice_fixture(name='两人', words='好的。', body='林雨和陈安站在大厅。\n【D1】两人：好的。')
        clip = director._clip(sl, 1, cards, {}, '', {}, '')
        self.assertEqual([row['name'] for row in clip['references']], ['林雨', '陈安', '大厅'])
        self.assertEqual(clip['source_order'][1]['binding'], 'collective')
        self.assertIn('(S1,S2)', clip['source_order'][1]['speech_prefix'])
        prompt = director.assemble(director.source_fallback(clip), clip)
        self.assertIn('say together in unison', prompt)
        self.assertIn('The collective "两人" consists of <Subject 1> and <Subject 2>', prompt)
        self.assertEqual(prompt.count('<d>[Chinese] 好的。</d>'), 1)
        self.assertNotIn('两人, the character from <Picture', prompt)

    def test_unnamed_group_reference_is_explicitly_collective(self):
        sl = slice_fixture(name='游客们', words='好的。')
        clip = director._clip(sl, 1, CARDS, {}, '', {}, '')
        self.assertEqual([row['name'] for row in clip['references']], ['游客们', '大厅'])
        prompt = director.assemble(director.source_fallback(clip), clip)
        self.assertIn('This subject is a collective group, not one individual.', prompt)
        self.assertIn('(3 people)', prompt)
        self.assertIn('<Subject 1> (S1) say together in unison', prompt)

    def test_member_individual_voice_is_reused_for_later_collective(self):
        cards = CARDS[:2] + [{'name': '两人', 'entity_type': 'group', 'group_members': ['林雨', '陈安']}]
        speakers = {}
        director._assign_speaker(speakers, '陈安', cards)
        director._assign_speaker(speakers, '两人', cards)
        director._assign_speaker(speakers, '陈安', cards)
        self.assertEqual(speakers['陈安'], 1)
        self.assertEqual(speakers['两人'], (2, 1))
        self.assertEqual(speakers['林雨'], 2)

    def test_current_source_group_members_override_saved_members_without_card_mutation(self):
        import copy
        cards = CARDS[:2] + [{'name': '周宁', 'age': 35, 'sex': '男'},
            {'name': '众人', 'entity_type': 'group', 'group_count': 2, 'group_members': ['林雨', '周宁']}]
        original = copy.deepcopy(cards)
        # The card currently holds later-episode members, but D1 belongs to
        # the earlier explicit Lin/Chen collective. The references stay in
        # asset order even when occurrence membership is listed backwards.
        sl = slice_fixture(name='众人', words='好的。', body='众人齐声说：\n【D1】众人：好的。')
        sl['director']['source_dialogue'] = [{'id': 1, 'speaker': '众人', 'text': '好的。',
            'entity_type': 'group', 'group_count': 2, 'group_members': ['陈安', '林雨']}]
        clip = director._clip(sl, 1, cards, {}, '林雨和陈安站在大厅。', {}, '')
        self.assertEqual([row['name'] for row in clip['references']], ['林雨', '陈安', '大厅'])
        self.assertIn('<Subject 2> and <Subject 1>', clip['source_order'][1]['speech_prefix'])
        self.assertNotIn('周宁', clip['source_order'][1]['speech_prefix'])
        self.assertEqual(cards, original)

    def test_partial_named_members_do_not_impersonate_full_collective(self):
        cards = CARDS[:2] + [{'name': '游客们', 'entity_type': 'group', 'group_count': 5,
            'group_members': ['林雨', '陈安']}]
        sl = slice_fixture(name='游客们', words='好的。')
        clip = director._clip(sl, 1, cards, {}, '', {}, '')
        self.assertEqual([row['name'] for row in clip['references']], ['游客们', '大厅'])
        self.assertEqual(clip['source_order'][0]['speech_prefix'], '<Subject 1> (S1)')
        prompt = director.assemble(director.source_fallback(clip), clip)
        self.assertIn('(5 people)', prompt)
        self.assertNotIn('(S1,S2)', prompt)


if __name__ == '__main__':
    unittest.main()
