import json
import os
import unittest
from contextlib import ExitStack
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from cores import episode_contract as contract, director_flow as director, episode_ledger


CARDS = [{'name': '林雨', 'age': 28, 'sex': '女'}, {'name': '陈安', 'age': 30, 'sex': '男'},
         {'name': '周宁', 'age': 27, 'sex': '男'}]


class SourceContractTests(unittest.TestCase):
    def test_literary_dialogue_recovered_without_rewriting(self):
        source = '林雨握住水瓶，说：“我们先到桥边等。”\n陈安问：“周宁回来了吗？”'
        script, says, report = contract.prepare_source(source, CARDS)
        self.assertEqual(says, [('林雨', '我们先到桥边等。'), ('陈安', '周宁回来了吗？')])
        self.assertIn('林雨握住水瓶，说：', script)
        self.assertEqual(report['source_original'], source)
        self.assertFalse(report['issues'])

    def test_silent_thought_signage_and_recalled_quotes_not_dialogue(self):
        for source in ('林雨站在门边。陈安捡起钥匙。', '林雨心想：“不能让他知道。”',
                       '门上刻着：“禁止入内”。', '林雨想起当年那句“不要回来”。'):
            with self.subTest(source=source):
                _, says, report = contract.prepare_source(source, CARDS)
                self.assertEqual(says, [])
                self.assertEqual(report['source_original'], source)

    def test_repeated_and_short_speech_preserved(self):
        _, says, _ = contract.prepare_source('林雨：好。\n林雨：好。\n陈安：嗯。', CARDS)
        self.assertEqual(says, [('林雨', '好。'), ('林雨', '好。'), ('陈安', '嗯。')])

    def test_inline_known_speaker_is_lifted_without_deduplication(self):
        source = '风吹过桥面。林雨：继续走。陈安：继续走。'
        script, says, report = contract.prepare_source(source, CARDS)
        self.assertEqual(says, [('林雨', '继续走。'), ('陈安', '继续走。')])
        self.assertIn('风吹过桥面。', script)
        self.assertEqual(report['source_original'], source)

    def test_narrator_is_not_forced_to_on_screen_dialogue(self):
        source = '旁白：雨下了一夜。\n音效：钟响。\n林雨：我们走。'
        script, says, _ = contract.prepare_source(source, CARDS)
        self.assertEqual(says, [('林雨', '我们走。')])
        self.assertIn('旁白：雨下了一夜。', script)

    def test_unknown_speaker_remains_explicitly_unresolved(self):
        _, says, report = contract.prepare_source('她说：“门锁住了。”', CARDS)
        self.assertEqual(says, [('待核对说话人', '门锁住了。')])
        self.assertEqual(report['issues'][0]['code'], 'speaker_unresolved')

    def test_alias_and_performance_cue(self):
        cards = [{'name': '艾拉', 'aliases': ['阿拉'], 'age': 25, 'sex': '女'}]
        script, says, _ = contract.prepare_source('阿拉（轻声）：先把火把给我。', cards)
        self.assertEqual(says, [('艾拉', '先把火把给我。')])
        self.assertIn('轻声', script)

    def test_long_dialogue_splits_only_at_punctuation_without_losing_words(self):
        words = '我们先沿着桥边找一找，如果找不到就到门外集合。' * 6
        script, says, report = contract.prepare_source('林雨：' + words, CARDS, split_long_dialogue=True)
        self.assertGreater(len(says), 1)
        self.assertEqual(''.join(text for _, text in says), words)
        self.assertTrue(all(contract.speech_seconds(text) <= 15 for _, text in says))
        self.assertEqual({row['source_dialogue_id'] for row in report['dialogue']}, {1})
        self.assertEqual([row['fragment_index'] for row in report['dialogue']], list(range(1, len(says) + 1)))

    def test_more_than_200_character_literary_quote_is_not_lost(self):
        words = '我们先沿着桥边找一找，如果找不到就到门外集合。' * 12
        _, says, _ = contract.prepare_source('林雨说：“' + words + '”', CARDS, split_long_dialogue=True)
        self.assertEqual(''.join(text for _, text in says), words)
        self.assertTrue(all(name == '林雨' for name, _ in says))
        _, thoughts, _ = contract.prepare_source('林雨心想：“' + words + '”', CARDS, split_long_dialogue=True)
        self.assertEqual(thoughts, [])

    def test_long_clause_without_punctuation_continues_losslessly_within_budget(self):
        words = '字' * 80
        _, says, report = contract.prepare_source('林雨：' + words, CARDS, split_long_dialogue=True)
        self.assertGreater(len(says), 1)
        self.assertEqual(''.join(fragment for _, fragment in says), words)
        self.assertTrue(all(who == '林雨' and contract.speech_seconds(fragment) <= 15 for who, fragment in says))
        self.assertFalse(report['issues'])

    def test_english_long_speech_prefers_spaces_without_losing_them(self):
        words = 'Please carry the blue toolbox through the door and wait beside the bench. ' * 6
        fragments = contract.split_speech(words)
        self.assertEqual(''.join(fragments), words)
        self.assertTrue(all(contract.speech_seconds(fragment) <= 15 for fragment in fragments))
        # Existing sentence punctuation remains preferred; other splits are
        # word boundaries and keep every original whitespace character.
        self.assertTrue(all(fragment.endswith((' ', '.', '\n')) for fragment in fragments[:-1]))

    def test_long_unspaced_word_and_unicode_marks_keep_exact_text(self):
        for words in ('A' * 200, '字' * 65 + 'a\u0301' + '字' * 80, '字' * 65 + '\r\n' + '字' * 80):
            with self.subTest(words=words[:20]):
                fragments = contract.split_speech(words)
                self.assertGreater(len(fragments), 1)
                self.assertEqual(''.join(fragments), words)
                self.assertTrue(all(contract.speech_seconds(fragment) <= 15 for fragment in fragments))
                self.assertFalse(any(fragment.startswith(('\u0301', '\n')) for fragment in fragments[1:]))

    def test_presence_does_not_promote_missing_or_mentioned_person(self):
        presence = contract.presence_evidence('林雨递给陈安水瓶。周宁已经失踪。林雨寻找周宁。', CARDS)
        self.assertEqual(presence['actors'], ['林雨', '陈安'])
        self.assertEqual(presence['absent'], ['周宁'])

    def test_explicit_return_updates_presence(self):
        presence = contract.presence_evidence('周宁已经失踪。周宁走进门。', CARDS)
        self.assertIn('周宁', presence['actors'])
        self.assertEqual(presence['absent'], [])

    def test_named_body_actions_preserve_presence_but_not_dialogue_mentions(self):
        presence = contract.presence_evidence(
            '林雨双手稳稳端着盒子。陈安垂下右手。陈安侧身让出通道。'
            '林雨目光落在盒上。周宁已经失踪。\n林雨：周宁的背影看不清。', CARDS)
        self.assertEqual(presence['actors'], ['林雨', '陈安'])
        self.assertEqual(presence['absent'], ['周宁'])
        self.assertEqual(len([row for row in presence['evidence'] if row['presence'] == 'action']), 4)
        self.assertEqual(contract.presence_evidence('周宁的照片放在桌上。周宁背影的画像挂在墙上。', CARDS)['actors'], [])

    def test_camera_candidates_require_visual_evidence_and_respect_absence(self):
        camera = '镜头推近至林雨面部，随后焦点虚化至背景中陈安的背影或门缝透出的光。'
        self.assertEqual(contract.camera_identity_evidence(camera, CARDS)['names'], ['林雨', '陈安'])
        self.assertEqual(contract.camera_identity_evidence(camera, CARDS, ['陈安'])['names'], ['林雨'])
        for mention in ('林雨说：“去找周宁，看看周宁的双手。”', '陈安不出镜。', '陈安的背影在画外。',
                        '镜头聚焦周宁的照片。', '林雨寻找周宁。', '周宁已经失踪。'):
            with self.subTest(mention=mention):
                self.assertFalse({'周宁', '陈安'} & set(contract.camera_identity_evidence(mention, CARDS)['names']))

    def test_ending_alias_is_shared(self):
        self.assertEqual(contract.ending_text({'target_state': '林雨抱着陈安，站在桥下。', 'end_state': {}}),
                         '林雨抱着陈安，站在桥下。')
        self.assertIn('手持钥匙', contract.ending_text({'end_state': {'林雨': {'hands': '手持钥匙'}}}))


class DirectorContractTests(unittest.TestCase):
    def test_frozen_clip_binds_optional_background_identity_without_forcing_appearance(self):
        source = '林雨双手端着盒子。\n林雨：嗯。\n两人仍停在原处。'
        sl = {'text': source, 'seconds': 8, 'scene_hint': '桥边', 'director': {
            'cast': ['林雨'], 'camera': '镜头推近至林雨面部，随后焦点虚化至背景中陈安的背影或门缝透出的光。',
            'purpose': '回应后停留', 'blocks': [[0, 8, '林雨双手端着盒子。\n【D7】林雨：嗯。\n两人仍停在原处。', [7]]],
            'says': [['林雨', '嗯。']], 'source_contract_version': contract.VERSION,
            'presence': {'actors': [], 'absent': []}, 'start_state': '陈安站在门边。'}}
        clip = director._clip(sl, 4, CARDS, {'林雨': 1}, '', {}, '')
        self.assertEqual([row['name'] for row in clip['references']], ['林雨', '陈安', '桥边'])
        self.assertEqual(sl['director']['cast'], ['林雨', '陈安'])
        self.assertEqual(clip['presence_contract']['actors'], ['林雨'])
        self.assertIn('不要求每帧都显示', clip['continuity_rules'])
        prompt = director.assemble({'clip_id': 4, 'summary': 'A quiet reply', 'style': 'Soft light',
            'text': '<Subject 1> holds the box. {{D7}}', 'overall_soundscape': 'Room ambience'}, clip)
        self.assertIn('<Subject 2> is 陈安, the character from <Picture 2>.', prompt)
        self.assertIn('<Subject 2> (identity reference only)', prompt)
        self.assertEqual(prompt.count('<d>[Chinese] 嗯。</d>'), 1)

    def test_new_plan_binds_camera_identity_but_excludes_explicitly_missing_person(self):
        def q(system, payload, **kwargs):
            data = json.loads(payload)
            return json.dumps({'initial_state': '桥边', 'shots': [{'scene_name': '桥边', 'cast': ['林雨'],
                'camera': '林雨面部特写，背景中陈安的背影，周宁的身影。', 'purpose': '停留',
                'end_unit': len(data['units']), 'seconds': 8, 'end_state': '两人仍停在原处。'}]}, ensure_ascii=False)
        slices = director.plan('周宁已经失踪。林雨双手端着盒子。', [{'name': '桥边'}], CARDS, q)
        self.assertEqual(slices[0]['director']['cast'], ['林雨', '陈安'])
        self.assertEqual(slices[0]['director']['presence']['actors'], ['林雨'])

    def test_plan_recovers_quote_adds_actor_and_removes_missing_character(self):
        payloads = []

        def q(system, payload, **kwargs):
            data = json.loads(payload)
            payloads.append(data)
            return json.dumps({'initial_state': '桥边', 'shots': [{'scene_name': '桥边',
                'cast': ['周宁'], 'camera': '中景固定', 'purpose': '接过水瓶继续寻找',
                'end_unit': len(data['units']), 'seconds': 12, 'end_state': '林雨与陈安留在桥边，周宁下落不明。'}]}, ensure_ascii=False)

        slices = director.plan('林雨递给陈安水瓶。周宁已经失踪。\n陈安说：“我们到桥下找。”',
                               [{'name': '桥边'}], CARDS, q, prev_ledger='林雨持水瓶；陈安站在桥边。')
        row = slices[0]['director']
        self.assertCountEqual(row['cast'], ['林雨', '陈安'])
        self.assertEqual(row['says'], [['陈安', '我们到桥下找。']])
        self.assertEqual(row['target_state'], row['end_state_text'])
        self.assertEqual(row['start_state'], '林雨持水瓶；陈安站在桥边。')
        self.assertEqual(payloads[0]['continuity_contract']['source_presence']['absent'], ['周宁'])

    def test_old_slice_recovers_actual_dialogue_only(self):
        sl = {'text': '林雨说：“灯灭了。”', 'seconds': 10, 'scene_hint': '桥边', 'director': {
            'cast': ['林雨'], 'camera': '中景', 'purpose': '观察', 'blocks': [[0, 10, '林雨说：“灯灭了。”', []]],
            'says': [], 'target_state': '林雨握住手电', 'end_state': {}}}
        trace = []
        def no_model(*args, **kwargs):
            self.fail('explicit speaker must not need a model')
        self.assertTrue(director.repair_slice_source(sl, CARDS, no_model, trace=trace))
        self.assertEqual(sl['director']['says'], [('林雨', '灯灭了。')])
        self.assertEqual(sl['director']['blocks'][0][3], [1])
        self.assertIn('林雨说：“灯灭了。”', sl['director']['source_contract']['reports'][0]['source_original'])
        self.assertFalse(director.repair_slice_source(sl, CARDS, no_model))

    def test_unknown_speaker_resolved_as_metadata_only(self):
        source = '林雨走近铁门。\n她说：“门锁住了。”'
        script, says, _ = contract.prepare_source(source, CARDS)
        def q(system, payload, **kwargs):
            return json.dumps({'resolutions': [{'dialogue_id': 1, 'speaker': '林雨',
                'evidence': '林雨走近铁门。', 'certain': True}]}, ensure_ascii=False)
        fixed, says = director.resolve_speakers(script, says, CARDS, q)
        self.assertEqual(says, [('林雨', '门锁住了。')])
        self.assertIn('【D1】林雨：门锁住了。', fixed)

    def test_source_voice_is_preserved_without_a_model_review_gate(self):
        calls = []
        def q(system, payload, **kwargs):
            calls.append(payload)
            self.fail('the source must not trigger another speaker review')
        trace = []
        script, dialogue = director.resolve_speakers('有人说话。', [('待核对说话人', '走吧。')], CARDS, q, trace=trace)
        self.assertEqual(calls, [])
        self.assertEqual(script, '有人说话。')
        self.assertEqual(dialogue, [('未具名说话者', '走吧。')])
        self.assertEqual(trace[-1]['bindings'][0]['binding'], 'source_voice')

    def test_newly_recovered_long_speech_does_not_render_in_short_clip(self):
        original = '林雨说：“' + '路很远' * 25 + '。”'
        sl = {'text': original, 'seconds': 6, 'scene_hint': '桥边', 'director': {'cast': ['林雨'],
            'blocks': [[0, 6, original, []]], 'says': []}}
        with self.assertRaises(contract.ContractError) as caught:
            director.repair_slice_source(sl, CARDS, lambda *a, **kw: None)
        self.assertEqual(caught.exception.code, 'dialogue_timing')
        self.assertEqual(sl['director']['says'], [])

    def test_old_automatic_slice_extends_only_affected_clip_up_to_15_seconds(self):
        words = '先沿着左边的台阶往上走，到了顶层以后再把门打开。'
        source = '林雨说：“' + words + '”'
        sl = {'text': source, 'seconds': 4, 'scene_hint': '桥边', 'director': {
            'flow': director.VERSION, 'cast': ['林雨'], 'blocks': [[0, 4, source, []]], 'says': []}}
        trace = []
        director.repair_slice_source(sl, CARDS, lambda *a, **kw: None, trace=trace)
        self.assertGreater(sl['seconds'], 4)
        self.assertLessEqual(sl['seconds'], 15)
        self.assertEqual(sl['director']['seconds'], sl['seconds'])
        self.assertEqual(sl['director']['blocks'][-1][1], sl['seconds'])
        self.assertEqual(sl['director']['says'], [('林雨', words)])
        self.assertEqual(sl['director']['timing_repair']['original_seconds'], 4)
        self.assertTrue(any(row['stage'] == 'timing_repair' for row in trace))

    def test_user_timing_is_not_silently_extended(self):
        source = '林雨说：“先沿着左边的台阶往上走，到了顶层以后再把门打开。”'
        sl = {'text': source, 'seconds': 4, 'scene_hint': '桥边', 'director': {
            'flow': director.VERSION, 'timing_source': 'user', 'cast': ['林雨'],
            'blocks': [[0, 4, source, []]], 'says': []}}
        with self.assertRaises(contract.ContractError):
            director.repair_slice_source(sl, CARDS, lambda *a, **kw: None)
        self.assertEqual(sl['seconds'], 4)
        self.assertEqual(sl['director']['says'], [])

    def test_fractional_valid_duration_is_not_rounded_for_no_reason(self):
        sl = {'text': '林雨走近桥边。', 'seconds': 5.5, 'scene_hint': '桥边', 'director': {
            'flow': director.VERSION, 'cast': ['林雨'], 'blocks': [[0, 5.5, '林雨走近桥边。', []]], 'says': []}}
        director.repair_slice_source(sl, CARDS, lambda *a, **kw: None)
        self.assertEqual(sl['seconds'], 5.5)

    def test_initial_plan_can_split_one_long_source_utterance_across_clips(self):
        words = '先到桥边等着我，把灯熄灭以后再往楼上走。' * 6
        def q(system, payload, **kwargs):
            data = json.loads(payload)
            return json.dumps({'initial_state': '桥边', 'shots': [{'scene_name': '桥边', 'cast': ['林雨'],
                'camera': '中景固定', 'purpose': '交代行动', 'end_unit': unit['id'], 'seconds': 15,
                'end_state': '林雨仍站在桥边。'} for unit in data['units']]}, ensure_ascii=False)
        slices = director.plan('林雨：' + words, [{'name': '桥边'}], CARDS, q)
        self.assertGreater(len(slices), 1)
        self.assertEqual(''.join(line[1] for sl in slices for line in sl['director']['says']), words)
        self.assertTrue(all(4 <= sl['seconds'] <= 15 for sl in slices))
        self.assertEqual({row['source_dialogue_id'] for sl in slices for row in sl['director']['source_dialogue']}, {1})

    def test_unpunctuated_long_clause_splits_losslessly_and_continues(self):
        def q(*args, **kwargs):
            error = RuntimeError('文字输出达到长度上限')
            error.partial_text = '{"shots": ['
            raise error
        words = '字' * 80
        slices = director.plan('林雨：' + words, [{'name': '桥边'}], CARDS, q)
        self.assertGreater(len(slices), 1)
        self.assertEqual(''.join(line[1] for sl in slices for line in sl['director']['says']), words)
        self.assertTrue(all(4 <= sl['seconds'] <= 15 for sl in slices))


class PromptContractTests(unittest.TestCase):
    def clip(self, dialogue=True):
        return {'clip_id': 1, 'seconds': 10, 'source_order': ([{'dialogue_id': 7, 'speaker': '林雨',
                'text': '门已经打开了。', 'speech_prefix': '<Subject 1> (S1)'}] if dialogue else []),
                'references': [{'name': '林雨', 'role': 'character', 'subject': 1, 'picture': 1,
                                'subject_label': '<Subject 1>'}]}

    def raw(self, text):
        return {'clip_id': 1, 'summary': 'Open door', 'style': 'Natural light', 'text': text,
                'overall_soundscape': 'Footsteps'}

    def test_nonstandard_anchor_normalized_without_changing_words(self):
        for marker in ('{{ D 007 }}', '{D7}', '【D7】', '[D7]', '<D7>'):
            with self.subTest(marker=marker):
                result = director.assemble(self.raw('<Subject 1> opens the door. ' + marker), self.clip())
                self.assertIn('<d>[Chinese] 门已经打开了。</d>', result)

    def test_reference_scope_keeps_fixed_scene_but_not_old_movable_prop_state(self):
        clip = self.clip()
        clip['references'][0]['picture_label'] = '<Picture 1>'
        clip['references'].append({'name': '桥边', 'role': 'scene', 'subject': 2, 'picture': 2,
                                   'subject_label': '<Subject 2>', 'picture_label': '<Picture 2>'})
        prompt = director.assemble(self.raw('<Subject 1> closes the box. {{D7}}'), clip)
        sections = {key: prompt.split(key + ':\n', 1)[1].split('\n\n', 1)[0] for key in director.FIELDS}
        self.assertIn('fixed architecture, fixed furniture layout and lighting only', sections['subject_definitions'])
        self.assertIn('<Subject 2> (appears in [Shot 1]): partially_preserved', sections['retention_analysis'])
        self.assertNotIn('<Subject 2> (appears in [Shot 1]): fully_preserved', prompt)
        self.assertIn('Movable object count, location, holder and completed actions follow the confirmed source and preceding state', prompt)
        self.assertIn('do not duplicate an object or restore its earlier placement', prompt)
        self.assertIn('<Subject 1> (appears in [Shot 1]): fully_preserved - character identity, face, hair and clothing only', prompt)
        self.assertEqual(prompt.count('<d>[Chinese] 门已经打开了。</d>'), 1)
        self.assertEqual(sections['non_diegetic_music'], 'N/A')

    def test_unknown_duplicate_and_missing_anchors_cannot_pass(self):
        for markers in ('{{D1}}', '{{D7}} {{D7}}', ''):
            with self.subTest(markers=markers), self.assertRaises(ValueError):
                director.assemble(self.raw('<Subject 1> opens the door. ' + markers), self.clip())

    def test_exact_literal_restores_anchor_at_its_original_position(self):
        raw = self.raw('<Subject 1> points. 门已经打开了。 The door swings.')
        fixed = director.repair_missing_dialogue(raw, self.clip())
        self.assertEqual(fixed['text'], '<Subject 1> points. {{D7}} The door swings.')
        self.assertIn('门已经打开了。', director.assemble(fixed, self.clip()))
        with self.assertRaises(ValueError):
            director.repair_missing_dialogue(self.raw('<Subject 1> opens the door.'), self.clip())

    def test_unknown_anchor_repaired_from_source_in_bounded_retry(self):
        calls = []
        clip = self.clip()
        def q(system, payload, **kwargs):
            calls.append(json.loads(payload))
            return json.dumps(self.raw('<Subject 1> opens the door. ' + ('{{D1}}' if len(calls) == 1 else '{{D7}}')))
        result = director._request(q, 'write', clip, {}, lambda data: director.assemble(data, clip))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]['output_contract']['dialogue_placeholders'], ['{{D7}}'])
        self.assertIn('门已经打开了。', result)

    def test_silent_clip_hallucination_retries_without_deleting_tokens(self):
        clip, calls = self.clip(False), []
        def q(system, payload, **kwargs):
            calls.append(json.loads(payload))
            return json.dumps(self.raw('<Subject 1> closes the door. {{D1}}'))
        with self.assertRaises(contract.ContractError) as caught:
            director._request(q, 'write', clip, {}, lambda data: director.assemble(data, clip),
                              repair=lambda data: director.repair_missing_dialogue(data, clip))
        self.assertEqual(len(calls), 3)
        self.assertEqual(caught.exception.code, 'prompt_contract')
        self.assertEqual(calls[1]['output_contract']['dialogue_placeholders'], [])


class SourceFallbackTests(unittest.TestCase):
    def slice(self, silent=False):
        body = '林雨双手端着红盒立在原地。' + ('' if silent else '\n【D7】林雨：嗯。\n【D8】林雨：嗯。') + '\n两人仍停在原处。'
        return {'text': contract.plain_script(body), 'seconds': 8, 'scene_hint': '桥边', 'director': {
            'flow': director.VERSION, 'cast': ['林雨', '陈安'], 'camera': '近景至特写。林雨回应“嗯。”，镜头缓慢推近至林雨面部。',
            'purpose': '回应后停留', 'blocks': [[0, 8, body, [] if silent else [7, 8]]],
            'says': [] if silent else [['林雨', '嗯。'], ['林雨', '嗯。']],
            'source_contract_version': contract.VERSION, 'end_state_text': '两人仍停在原处。'}}

    def clip(self, silent=False):
        return director._clip(self.slice(silent), 1, CARDS, {'林雨': 1}, '', {}, '')

    @staticmethod
    def bad_writer(*args, **kwargs):
        return json.dumps({'clip_id': 1, 'summary': 'A reply', 'style': 'Natural light',
            'text': '<Subject 2> has just exited. {{D999}}', 'overall_soundscape': 'Invented door slam'})

    def test_repeated_missing_short_lines_use_source_after_three_format_failures(self):
        clip, trace = self.clip(), []
        with patch.object(self, 'bad_writer', wraps=self.bad_writer) as q:
            prompt = director._request(q, 'write', clip, {}, lambda data: director.assemble(data, clip), trace,
                repair=lambda data: director.repair_missing_dialogue(data, clip), fallback=director.source_fallback)
        self.assertEqual(q.call_count, 3)
        self.assertEqual(trace[-1]['stage'], 'source_fallback')
        self.assertEqual(trace[-1]['status'], 'accepted')
        self.assertEqual(prompt.count('<d>[Chinese] 嗯。</d>'), 2)
        self.assertIn('<Subject 1>双手端着红盒立在原地。', prompt)
        self.assertLess(prompt.index('双手端着红盒'), prompt.index('<d>[Chinese] 嗯。</d>'))
        self.assertLess(prompt.rindex('<d>[Chinese] 嗯。</d>'), prompt.index('两人仍停在原处。'))
        self.assertIn('近景至特写；缓慢推近', prompt)
        self.assertNotIn('has just exited', prompt)
        self.assertNotIn('Invented door slam', prompt)
        self.assertEqual([field for field in director.FIELDS if field + ':' in prompt], list(director.FIELDS))

    def test_source_fallback_rejects_invalid_timing_speaker_or_references(self):
        mutations = [lambda clip: clip.update(seconds=3), lambda clip: clip.update(seconds=16),
            lambda clip: clip['source_order'][1].update(text='字' * 80),
            lambda clip: clip['source_order'][1].update(speaker='周宁'),
            lambda clip: clip['source_order'][2].update(dialogue_id=7),
            lambda clip: clip['references'][0].update(picture=9),
            lambda clip: clip['references'].pop(),
            lambda clip: clip['source_order'][0].update(text='林雨：遗漏的原对白。')]
        for mutate in mutations:
            clip = self.clip()
            mutate(clip)
            with self.subTest(mutation=mutate), self.assertRaises((ValueError, KeyError)):
                director.source_fallback(clip)

    def test_real_make_prompts_reports_fallback_and_preserves_silent_source(self):
        for silent in (False, True):
            steps, sl = [], self.slice(silent)
            rows = director.make_prompts([sl], CARDS, {'桥边': {}}, self.bad_writer, on_step=steps.append)
            self.assertTrue(rows[0]['source_fallback'])
            self.assertEqual(rows[0]['director_contract']['prompt_recovery']['mode'], 'source_fallback')
            self.assertTrue(any('source_fallback' in step for step in steps))
            self.assertEqual(rows[0]['prompt'].count('<d>[Chinese]'), 0 if silent else 2)

    def test_source_fallback_cannot_catch_transport_or_override_validation(self):
        clip = self.clip()
        with patch.object(director, 'source_fallback', wraps=director.source_fallback) as fallback:
            def unavailable(*args, **kwargs):
                raise ConnectionError('offline service')
            with self.assertRaises(ConnectionError):
                director._request(unavailable, 'write', clip, {}, lambda data: director.assemble(data, clip), fallback=fallback)
            fallback.assert_not_called()
        trace = []
        def refuse(data):
            raise ValueError('final validation refuses')
        with self.assertRaises(contract.ContractError):
            director._request(self.bad_writer, 'write', clip, {}, refuse, trace, fallback=director.source_fallback)
        self.assertEqual(trace[-1]['status'], 'blocked')
        from cores.age_policy import AgePolicyError
        for checks, expected_calls in (([AgePolicyError('source rejected')], 0),
                                       ([None, AgePolicyError('output rejected')], 3)):
            with patch('cores.age_policy.assert_allowed', side_effect=checks), \
                 patch.object(self, 'bad_writer', wraps=self.bad_writer) as q, \
                 self.assertRaises(AgePolicyError):
                director.make_prompts([self.slice()], CARDS, {'桥边': {}}, q)
            self.assertEqual(q.call_count, expected_calls)


class DepartureGuardTests(unittest.TestCase):
    actual_departure = ('The shot holds on her for a beat, emphasizing the stillness of the moment '
                        'before she fully steps out, leaving the frame.')

    def test_actual_preview_exit_is_rejected_by_new_and_saved_prompt_checks(self):
        fixture = SourceFallbackTests()
        clip = fixture.clip()
        data = director.source_fallback(clip)
        good = director.assemble(data, clip)
        data['text'] += '\n' + self.actual_departure
        with self.assertRaisesRegex(ValueError, '不可新增或预演离场'):
            director.assemble(data, clip)
        old_prompt = good.replace('overall_soundscape:', self.actual_departure + '\n\noverall_soundscape:')
        self.assertTrue(any('不可新增或预演离场' in item for item in
                           director.prompt_problems(old_prompt, fixture.slice()['text'], 8, '桥边')))

    def test_physical_exit_forms_and_explicit_departure_source(self):
        for model in ('She walks outside.', 'He is moving out.', 'He runs out of the shop.', 'She goes out.',
                      'She exits the room.', 'She leaves the shop.', '她走出店门。', '他出门。', '她离开室内。'):
            with self.subTest(model=model):
                self.assertTrue(contract.unsupported_departure(model, '两人仍在原处。'))
                self.assertEqual(contract.unsupported_departure(model, '她握住把手，走出店门。'), '')

    def test_frame_focus_view_and_negated_model_exit_are_not_physical_departures(self):
        for model in ('She steps out of frame.', 'He moves out of focus.', 'She walks out of the camera\'s field of view.',
                      'She leaves the frame.', 'She does not step outside.', '她没有走出店门。', '她不出门。'):
            with self.subTest(model=model):
                self.assertEqual(contract.unsupported_departure(model, '她站在门边。'), '')

    def test_source_dialogue_negation_and_intent_do_not_authorize_exit(self):
        for source in ('林雨：我先走出店门。', '林雨说：“我们出门看看。”\n她仍站在门边。',
                       '林雨还未走出店门。', '林雨没有离开房间。', '林雨不出门。',
                       '林雨准备走出店门。', 'She has not left the shop.'):
            with self.subTest(source=source):
                self.assertTrue(contract.unsupported_departure(self.actual_departure, source))
        self.assertEqual(contract.unsupported_departure('<Subject 1> says, <d>[Chinese] 我出门看看。</d>', '她仍站在门边。'), '')

    def test_actual_outward_threshold_transition_permits_exit_but_not_plans_or_inward_crossing(self):
        model = 'They walk out of the shop into the courtyard.'
        for source in ('两人跨过门槛，踏上门外青灰色的石板小院。', '林雨向外跨过门槛。', '她走到门外。'):
            with self.subTest(source=source):
                self.assertEqual(contract.unsupported_departure(model, source), '')
        for source in ('林雨：跨过门槛，踏上门外的石板小院。', '她准备踏上门外的石板小院。',
                       '她尚未跨过门槛，也没有踏上门外的石板小院。', '她不向外跨过门槛。',
                       '她跨过门槛，走进屋内。'):
            with self.subTest(source=source):
                self.assertTrue(contract.unsupported_departure(model, source))

    def test_repeated_unsupported_exit_uses_existing_source_fallback(self):
        clip, trace = SourceFallbackTests().clip(), []
        def q(*args, **kwargs):
            data = director.source_fallback(clip)
            data['text'] += '\n' + self.actual_departure
            return json.dumps(data)
        prompt = director._request(q, 'write', clip, {}, lambda data: director.assemble(data, clip), trace,
            repair=lambda data: director.repair_missing_dialogue(data, clip), fallback=director.source_fallback)
        self.assertEqual(len([row for row in trace if row['stage'] == 'write']), 3)
        self.assertEqual(trace[-1]['status'], 'accepted')
        self.assertNotIn('steps out', prompt)
        self.assertEqual(prompt.count('<d>[Chinese] 嗯。</d>'), 2)


class LedgerContractTests(unittest.TestCase):
    def test_previous_state_available_even_before_model_ledger_exists(self):
        timeline = {'slicing': {'slices': [{'scene_hint': '桥边', 'director': {
            'target_state': '林雨扶着陈安，周宁仍然失踪。', 'end_state': {},
            'presence': {'absent': ['周宁']}}}]}}
        with patch('cores.saga_core.get_saga', return_value={'episodes': [{'no': 2}]}), \
             patch('cores.saga_core.ep_timeline', return_value=timeline):
            text = episode_ledger.text('offline', 2)
        self.assertIn('林雨扶着陈安', text)
        self.assertIn('明确不在场：周宁', text)
        self.assertIn('上一话最后地点：桥边', text)


class MakePicturesSourceTests(unittest.TestCase):
    def run_single(self, source, episode_no=1, **episode_fields):
        from cores import authoring
        episode = dict(prose=source, body='旧稿漏掉了短句', pictures='旧稿漏掉了短句', **episode_fields)
        saved = {}
        def save(sid, no, **values):
            self.assertEqual(no, episode_no)
            saved.update(values)
        with ExitStack() as stack:
            stack.enter_context(patch.object(authoring, '_ep', return_value=({}, episode)))
            stack.enter_context(patch('cores.story_core.get_story', return_value={'settings': {'story_mode': 'single'}}))
            stack.enter_context(patch.object(authoring, 'cast_cards', return_value=CARDS))
            stack.enter_context(patch.object(authoring, '_update_ep', side_effect=save))
            stack.enter_context(patch.object(authoring, '_dbg'))
            for name in ('_q', '_story_v2', 'literal_pictures_from_prose', 'prose_to_pictures',
                         'fix_missing_dialogue', 'fix_speakers', 'reorder_dialogue',
                         'pictures_director_pass', 'build_shotlist_for', 'desex_text'):
                stack.enter_context(patch.object(authoring, name, side_effect=AssertionError('legacy/model path reached: ' + name)))
            stack.enter_context(patch('cores.oral_story.lift_inline_dialogue', side_effect=AssertionError('lossy lift reached')))
            result = authoring.make_pictures('OFFLINE', episode_no)
        self.assertNotIn('prose', saved)
        self.assertEqual(episode['prose'], source)
        return result, saved

    def test_actual_make_pictures_preserves_short_and_repeated_dialogue(self):
        from cores import authoring
        source = '林雨推开门，陈安跟上。\n林雨：好。\n陈安：嗯。\n林雨：好。\n林雨：继续走。\n林雨：继续走。'
        result, saved = self.run_single(source)
        _, says, _ = contract.prepare_source(result['pictures'], CARDS)
        self.assertEqual(says, [('林雨', '好。'), ('陈安', '嗯。'), ('林雨', '好。'),
                                ('林雨', '继续走。'), ('林雨', '继续走。')])
        self.assertEqual(saved['pictures'], saved['body'])
        self.assertEqual(saved['pictures_conversion_version'], authoring.LOSSLESS_PICTURES_VERSION)
        self.assertEqual(saved['pictures_source_contract']['source_original'], source)
        self.assertEqual(saved['pictures_source_contract']['conversion_version'], authoring.LOSSLESS_PICTURES_VERSION)

    def test_next_episode_uses_current_prose_even_with_stale_v2_script(self):
        source = '林雨说：“好。”\n陈安点头，握住门把。\n林雨：好。'
        result, saved = self.run_single(source, episode_no=3, script_v2='陈旧v2稿没有这些对白。')
        self.assertEqual(contract.prepare_source(result['pictures'], CARDS)[1], [('林雨', '好。'), ('林雨', '好。')])
        self.assertEqual(saved['pictures_source_contract']['source_original'], source)

    def test_short_replies_survive_make_pictures_through_real_director_plan(self):
        source = '林雨：好。\n陈安：嗯。\n林雨：好。'
        result, _ = self.run_single(source)
        def q(system, payload, **kwargs):
            data = json.loads(payload)
            return json.dumps({'initial_state': '桥边', 'shots': [{'scene_name': '桥边',
                'cast': ['林雨', '陈安'], 'camera': '中景固定', 'purpose': '回应后继续',
                'end_unit': len(data['units']), 'seconds': 8, 'end_state': '两人仍站在桥边。'}]}, ensure_ascii=False)
        slices = director.plan(result['pictures'], [{'name': '桥边'}], CARDS, q)
        self.assertEqual([line for sl in slices for line in sl['director']['says']],
                         [['林雨', '好。'], ['陈安', '嗯。'], ['林雨', '好。']])

    def test_legacy_mode_keeps_existing_conversion_route(self):
        from cores import authoring
        class LegacyReached(Exception):
            pass
        with patch.object(authoring, '_ep', return_value=({}, {'prose': '保留旧模式的原有流程。'})), \
             patch('cores.story_core.get_story', return_value={'settings': {'story_mode': 'legacy'}}), \
             patch.object(authoring, 'cast_cards', return_value=CARDS), \
             patch.object(authoring, 'confirmed_arrangement', return_value={}), \
             patch.object(authoring, 'prose_to_pictures', side_effect=LegacyReached), \
             patch.object(authoring, '_update_ep') as save:
            with self.assertRaises(LegacyReached):
                authoring.make_pictures('OFFLINE', 1)
        save.assert_not_called()


if __name__ == '__main__':
    unittest.main()
