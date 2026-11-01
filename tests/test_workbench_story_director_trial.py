"""Zero-model coverage for the isolated Chinese workbench director adapter."""
import copy
import unittest

from cores import director_flow, reference_bindings, scene_reference_policy
from cores import video_continuity
from cores import workbench_story_director_trial as trial


def cards():
    return [dict(character_id='person_a', name='陆青尘', appearance='依原图', voice='清朗自然的青年男声'),
            dict(character_id='person_b', name='苏晚棠', appearance='依原图', voice='温柔明亮的青年女声')]


def slices():
    return [dict(seconds=15, scene_hint='云上庭院', chars=['陆青尘', '苏晚棠'],
        text='陆青尘扶稳即将滑落的花盆。\n苏晚棠：慢一点。\n花盆在桌面稳稳停住。',
        director=dict(flow='source-v1', cast=['陆青尘', '苏晚棠'], purpose='扶稳花盆并交换反应。',
            start_state='花盆靠近桌边。', target_state='花盆停稳，两人仍在桌边。',
            blocks=[[0, 5, '陆青尘扶稳即将滑落的花盆。', []],
                    [5, 10, '【D1】苏晚棠：慢一点。', [1]],
                    [10, 15, '花盆在桌面稳稳停住。', []]], says=[['苏晚棠', '慢一点。']])),
        dict(seconds=15, scene_hint='云上庭院', chars=['陆青尘', '苏晚棠'],
             text='苏晚棠把花盆移向桌心。\n陆青尘：这回稳了。', tail='花盆位于桌心。')]


def refs(scene=True):
    rows = [dict(name=row['name'], character_id=row['character_id'], voice=row['voice'],
                 subject=index, picture=index, role='character', path='fixture_%d.png' % index)
            for index, row in enumerate(cards(), 1)]
    if scene:
        rows.append(dict(name='云上庭院', subject=3, picture=3, role='scene', path='fixture_scene.png'))
    return rows


def request(index=0, scene=True):
    plan = trial.build_plan(slices(), cards(), visual_style='真人写实')
    return trial.write_request(plan, plan['beats'][index],
        dict(actual_visible='桌边两人及花盆可见。', completed=[], scene='云上庭院'), refs(scene))


def raw(payload):
    return dict(brief='花盆停稳，两人交换反应。',
        integrated_multimodal_description=(
            '[Shot 1] 中景呈现桌边人物与即将滑落的花盆，前五秒拍清手掌承托花盆的过程。\n\n'
            '[Shot 2] At 00:05.000, 镜头切至近景，苏晚棠侧头提醒同伴，中间五秒手掌继续稳定花盆。\n\n'
            '[Shot 3] At 00:10.000, 镜头切至双人中景，最后五秒花盆平稳落回桌面，两人抬眼交流。'),
        overall_soundscape='风吹树叶和花盆接触桌面的轻响。', non_diegetic_music='N/A',
        expected_end_state='花盆停稳，两人仍在桌边。',
        timeline=[dict(start=start, end=start + 5, action='继续本段动作。', visible_result='动作结果清楚可见。')
                  for start in (0, 5, 10)],
        dialogue=[dict(speaker=slot['speaker'], text=slot['text'], start=6, end=8,
                       delivery='轻声提醒，发音清楚。') for slot in payload['dialogue_slots']],
        continuity_notes=[])


class WorkbenchStoryDirectorTrialTests(unittest.TestCase):
    def test_existing_director_blocks_and_plain_source_are_mapped_without_new_story(self):
        source, people = slices(), cards()
        before = copy.deepcopy((source, people))
        plan = trial.build_plan(source, people)
        self.assertEqual(plan['beats'][0]['dialogue'], [dict(speaker='苏晚棠', text='慢一点。')])
        self.assertEqual(plan['beats'][1]['dialogue'], [dict(speaker='陆青尘', text='这回稳了。')])
        self.assertEqual(plan['beats'][0]['cast'], ['person_a', 'person_b'])
        self.assertEqual(plan['beats'][0]['ending'], '花盆停稳，两人仍在桌边。')
        self.assertNotIn('慢一点', plan['beats'][0]['action'])
        self.assertEqual((source, people), before)

    def test_source_order_preserves_repeated_and_short_dialogue(self):
        source = [dict(seconds=15, scene='云上庭院', source_order=[
            dict(text='花盆逐渐稳定。'), dict(text='好。', speaker='苏晚棠', dialogue_id=9),
            dict(text='好。', speaker='陆青尘', dialogue_id=10)])]
        beat = trial.build_plan(source, cards())['beats'][0]
        self.assertEqual(beat['dialogue'], [dict(speaker='苏晚棠', text='好。'),
                                          dict(speaker='陆青尘', text='好。')])

    def test_plain_written_text_stays_action_not_new_dialogue(self):
        source = [dict(seconds=15, scene='云上庭院', text='花盆上写着“慢一点”。\n苏晚棠：好。')]
        beat = trial.build_plan(source, cards())['beats'][0]
        self.assertIn('花盆上写着“慢一点”', beat['action'])
        self.assertEqual(beat['dialogue'], [dict(speaker='苏晚棠', text='好。')])

    def test_request_reuses_chinese_director_but_not_fake_tail_or_target_observation(self):
        system, payload, schema = request()
        self.assertIn('中文镜头正文', system)
        self.assertIn('按剧情切镜', system)
        self.assertIn('实际观察证据', system)
        self.assertEqual(payload['actual_state']['completed'], [])
        self.assertNotIn('continuation_contract', payload)
        self.assertEqual([row['role'] for row in payload['canonical_references']],
                         ['character', 'character', 'scene_anchor'])
        self.assertEqual(schema['properties']['dialogue']['minItems'], 1)

    def test_compiled_multishot_main_renderer_scope_and_reference_transport_are_compatible(self):
        _, payload, _ = request()
        reply = raw(payload)
        before = copy.deepcopy((reply, payload))
        result = trial.compile_reply(reply, payload)
        self.assertEqual((reply, payload), before)
        self.assertIn('[Shot 3] At 00:10.000', result['prompt'])
        self.assertIn('<Subject 3> is 云上庭院, the environment from <Picture 3>.', result['prompt'])
        self.assertIn('<Subject 2>，音色描述：温柔明亮的青年女声', result['prompt'])
        segment = dict(plan=dict(flow='source-v1'), source_text=slices()[0]['text'])
        final = video_continuity.scope_prompt(result['prompt'], segment)
        video_continuity.validate_prompt_references(final, ['a.png', 'b.png', 'scene.png'])
        self.assertEqual(director_flow.prompt_problems(final, slices()[0]['text'], 15, '云上庭院'), [])
        self.assertEqual(len(reference_bindings._definition_lines(final)), 3)
        catalog = [dict(name=row['name'], role=row['role'], path=row['path'], owner_id=str(index))
                   for index, row in enumerate(refs())]
        rebound, audit = reference_bindings.rebind_prompt(final,
            ['fixture_2.png', 'fixture_1.png', 'fixture_scene.png'],
            ['fixture_1.png', 'fixture_2.png', 'fixture_scene.png'], catalog)
        self.assertEqual(audit['status'], 'rebound')
        self.assertIn('<Subject 1> is 陆青尘, the character from <Picture 2>.', rebound)

    def test_continuation_uses_only_current_character_images_and_preserves_environment_scope(self):
        _, payload, _ = request(index=1, scene=False)
        result = trial.compile_reply(raw(payload), payload)
        self.assertNotIn('<Picture 3>', result['prompt'])
        self.assertIn('<Subject 3> is 云上庭院, the environment.', result['prompt'])
        final = video_continuity.scope_prompt(result['prompt'], dict(plan=dict(flow='source-v1')))
        video_continuity.validate_prompt_references(final, ['a.png', 'b.png'])
        self.assertNotIn('真实最后一帧', final)

    def test_empty_scene_request_compiles_without_inventing_reference_images_or_people(self):
        source = [dict(seconds=15, scene_hint='云上庭院', chars=[], no_chars=True,
                       text='窗帘轻轻摆动，窗外树影落到地面。')]
        plan = trial.build_plan(source, [])
        before = copy.deepcopy(plan)
        _, payload, _ = trial.write_request(plan, plan['beats'][0], {}, [])
        self.assertEqual(payload['mode'], 't2va')
        self.assertEqual(payload['canonical_references'], [])
        self.assertEqual(payload['workbench_trial']['references'], [])
        reply = raw(payload)
        reply.update(brief='窗帘随风摆动。', expected_end_state='树影落在地面。',
            integrated_multimodal_description=(
                '[Shot 1] 全景呈现安静庭院，前五秒窗帘随风轻轻摆动。\n\n'
                '[Shot 2] At 00:05.000, 中景呈现窗外枝叶，树影缓慢移向地面。\n\n'
                '[Shot 3] At 00:10.000, 近景呈现地面光斑，最后五秒光斑轻微晃动。'))
        compiled = trial.compile_reply(reply, payload)
        self.assertEqual(plan, before)
        self.assertIn('<Subject 1> is 云上庭院, the environment.', compiled['prompt'])
        self.assertIn('[Shot 3] At 00:10.000', compiled['prompt'])
        self.assertNotIn('<Picture', compiled['prompt'])
        self.assertNotIn('the character', compiled['prompt'])
        self.assertEqual(list(compiled['fields']), list(trial.director.FIELDS))
        video_continuity.validate_prompt_references(compiled['prompt'], [])

    def test_missing_reference_mapping_is_not_treated_as_an_empty_scene(self):
        source = [dict(seconds=15, scene_hint='云上庭院', chars=[],
                       text='窗帘轻轻摆动，树影落到地面。')]
        plan = trial.build_plan(source, [])
        with self.assertRaisesRegex(ValueError, '实际参考映射'):
            trial.write_request(plan, plan['beats'][0], {}, None)
        visible_plan = trial.build_plan(slices(), cards())
        with self.assertRaisesRegex(ValueError, '出镜人物缺少实际参考图'):
            trial.write_request(visible_plan, visible_plan['beats'][0], {}, [])

    def test_compiled_scene_definition_can_use_existing_scene_strip_policy(self):
        _, payload, _ = request()
        result = trial.compile_reply(raw(payload), payload)
        stripped = scene_reference_policy.strip_scene_images(result['prompt'], [3])
        self.assertIn('<Subject 3> is 云上庭院, the environment.', stripped)
        video_continuity.validate_prompt_references(stripped, ['a.png', 'b.png'])

    def test_wrong_dialogue_original_order_speaker_and_timing_still_rejected(self):
        _, payload, _ = request()
        for change in ('words', 'speaker', 'timing'):
            reply = raw(payload)
            if change == 'words':
                reply['dialogue'][0]['text'] = '小心一点。'
            elif change == 'speaker':
                reply['dialogue'][0]['speaker'] = '陆青尘'
            else:
                reply['dialogue'][0].update(start=14.8, end=16)
            with self.subTest(change=change), self.assertRaises(ValueError):
                trial.compile_reply(reply, payload)

    def test_invalid_subject_reference_and_wrong_speaker_subject_still_rejected(self):
        _, payload, _ = request()
        for field in ('integrated_multimodal_description', 'delivery'):
            reply = raw(payload)
            if field == 'delivery':
                reply['dialogue'][0]['delivery'] = '<Subject 1>，自然说话。'
            else:
                reply[field] += ' <Subject 9>抬起头。'
            with self.subTest(field=field), self.assertRaises(ValueError):
                trial.compile_reply(reply, payload)

    def test_missing_inputs_and_extra_tail_not_silently_filled(self):
        plan = trial.build_plan(slices(), cards())
        with self.assertRaises(ValueError):
            trial.build_plan([dict(seconds=15, scene='庭院')], cards())
        with self.assertRaises(ValueError):
            trial.write_request(plan, plan['beats'][0], {}, [])
        with self.assertRaisesRegex(ValueError, '不增加尾帧'):
            trial.write_request(plan, plan['beats'][0], {},
                refs(False) + [dict(role='previous_frame', picture=3, path='tail.png')])


class WorkbenchStoryDirectorSourceRecoveryTests(unittest.TestCase):
    def test_existing_source_text_is_retained_verbatim(self):
        source = '  现有分镜原文。\n苏晚棠：好。\n'
        beat = dict(source_text=source, action='其他计划文字。', dialogue=[])
        self.assertEqual(trial.main_source_text(beat), source)

    def test_action_already_owns_complete_speech_without_appending_duplicate(self):
        action = ('[0～5秒] 中景。苏晚棠看着石栏上的果核，轻声开口：“原来不是逃，是来讨债的。”'
                  '陆青尘垂眸注视掌心，平静接话：“它每日暮时必来寻回音，今日是实物相还。”'
                  '\n[5～10秒] 云灵飞向竹竿。\n[10～15秒] 两人相视一笑。')
        beat = dict(action=action, dialogue=[
            dict(speaker='苏晚棠', text='原来不是逃，是来讨债的。'),
            dict(speaker='陆青尘', text='它每日暮时必来寻回音，今日是实物相还。')])
        before = copy.deepcopy(beat)
        self.assertEqual(trial.main_source_text(beat), action)
        self.assertEqual(beat, before)

    def test_silent_action_gets_fixed_dialogue_once_and_keeps_written_words(self):
        beat = dict(action='苏晚棠抬起写着“慢一点。”的纸牌。',
                    dialogue=[dict(speaker='苏晚棠', text='慢一点。')])
        self.assertEqual(trial.main_source_text(beat),
            '苏晚棠抬起写着“慢一点。”的纸牌。\n苏晚棠说：“慢一点。”')

    def test_partial_changed_or_ambiguous_speech_returns_evidence_without_guessing(self):
        declared = [dict(speaker='苏晚棠', text='慢一点。'), dict(speaker='陆青尘', text='知道了。')]
        actions = ('苏晚棠说：“慢一点。”陆青尘抬头。',
                   '苏晚棠说：“小心一点。”陆青尘说：“知道了。”',
                   '两人看着花盆，轻声开口：“慢一点。”陆青尘说：“知道了。”')
        for action in actions:
            beat = dict(action=action, dialogue=declared)
            before = copy.deepcopy(beat)
            with self.subTest(action=action), self.assertRaises(ValueError) as caught:
                trial.main_source_text(beat)
            self.assertEqual(caught.exception.evidence['action'], action)
            self.assertEqual(beat, before)

    def _two_line_request(self):
        plan = trial.build_plan([dict(seconds=15, scene='云上庭院', source_order=[
            dict(text='花盆在桌边，两人扶稳花盆。'),
            dict(text='慢一点。', speaker='苏晚棠', dialogue_id=1),
            dict(text='知道了。', speaker='陆青尘', dialogue_id=2)])], cards())
        _, payload, _ = trial.write_request(plan, plan['beats'][0], {}, refs(False))
        reply = raw(payload)
        reply['integrated_multimodal_description'] = reply['integrated_multimodal_description'].replace(
            '前五秒拍清手掌承托花盆的过程。',
            '前五秒拍清手掌承托花盆的过程。苏晚棠轻声开口：“慢一点。”陆青尘接话道：“知道了。”')
        for index, line in enumerate(reply['dialogue']):
            line.update(start=1 + index * 4, end=3 + index * 4)
        return payload, reply

    def test_trial_compile_deduplicates_open_and_response_cues_retains_actions_and_dialogue(self):
        payload, reply = self._two_line_request()
        before = copy.deepcopy((reply, payload))
        result = trial.compile_reply(reply, payload)
        self.assertEqual((reply, payload), before)
        for line in reply['dialogue']:
            self.assertEqual(result['prompt'].count(line['text']), 1)
        self.assertIn('苏晚棠轻声开口。陆青尘接话道。', result['prompt'])
        self.assertIn('手掌承托花盆的过程。', result['prompt'])
        self.assertEqual([(line['speaker'], line['text'], line['start'], line['end'])
                          for line in result['dialogue']],
                         [(line['speaker'], line['text'], line['start'], line['end'])
                          for line in reply['dialogue']])
        adjustments = [row for row in result['automatic_adjustments']
                       if row['kind'] == 'trial_fixed_dialogue_copy_removed']
        self.assertEqual(sum(row['count'] for row in adjustments), 2)
        self.assertEqual([span['text'] for span in adjustments[0]['matches']], ['慢一点。', '知道了。'])

    def test_written_words_voice_metadata_and_ordinary_quotes_are_not_removed(self):
        payload, reply = self._two_line_request()
        ordinary = ('纸牌上写着接话道：“慢一点。”\n'
                    '音色描述：温柔，轻声开口：“慢一点。”\n'
                    '人物望向名为“慢一点。”的小船。')
        reply['integrated_multimodal_description'] += '\n' + ordinary
        normalized, adjustments = trial._normalize_trial_spoken_copies(reply, payload)
        self.assertIn(ordinary, normalized['integrated_multimodal_description'])
        self.assertEqual(sum(row['count'] for row in adjustments), 2)

    def test_novel_quote_or_partial_slot_is_not_mechanically_deleted(self):
        payload, reply = self._two_line_request()
        novel = '她轻声开口：“慢一点。”她接话道：“知道了，这里还有东西。”'
        reply['integrated_multimodal_description'] = novel
        normalized, adjustments = trial._normalize_trial_spoken_copies(reply, payload)
        self.assertIn('接话道：“知道了，这里还有东西。”', normalized['integrated_multimodal_description'])
        self.assertEqual(sum(row['count'] for row in adjustments), 1)


class ObservedSilentEntityNormalizationTests(unittest.TestCase):
    def fixture(self):
        plan = dict(characters=[dict(id='char_01', name='陆青尘', appearance='以人设图为准', role='主角'),
            dict(id='bird_01', name='云灵', appearance='白色小型鸟类', role='配角')], props=[],
            beats=[dict(index=1, cast=['char_01', 'bird_01'], action='云灵飞到竹竿上。',
                        dialogue=[dict(speaker='陆青尘', text='它回来了。')])])
        cards = [dict(id='char_01', name='陆青尘')]
        state = dict(characters=[dict(id='bird_01', appearance='白色小型鸟类（云灵）',
                                     visibility='visible', certainty='observed')])
        return plan, cards, state

    def test_observed_silent_non_reference_migrates_without_changing_story_or_inputs(self):
        plan, people, state = self.fixture()
        before = copy.deepcopy((plan, people, state))
        normalized, audit = trial.normalize_observed_silent_entities(plan, people, state)
        self.assertEqual((plan, people, state), before)
        self.assertEqual(normalized['beats'], plan['beats'])
        self.assertEqual(normalized['characters'], plan['characters'][:1])
        self.assertEqual(normalized['props'], [dict(id='bird_01', name='云灵', appearance='白色小型鸟类', count=1)])
        self.assertEqual([row['id'] for row in audit['migrated']], ['bird_01'])

    def test_unknown_voiced_and_conflicting_entities_are_retained(self):
        for kind in ('unknown', 'voiced', 'conflict', 'count'):
            plan, people, state = self.fixture()
            if kind == 'unknown':
                state['characters'][0]['certainty'] = 'unknown'
            elif kind == 'voiced':
                plan['beats'][0]['dialogue'].append(dict(speaker='bird_01', text='你好。'))
            elif kind == 'conflict':
                plan['props'].append(dict(id='bird_01', name='旧对象', appearance='已有对象', count=1))
            else:
                plan['characters'][1]['count'] = 2
            with self.subTest(kind=kind):
                normalized, audit = trial.normalize_observed_silent_entities(plan, people, state)
                self.assertEqual(normalized, plan)
                self.assertEqual(audit['migrated'], [])
                self.assertEqual(audit['retained'][0]['id'], 'bird_01')

    def test_fixed_animal_reference_is_never_migrated(self):
        plan, people, state = self.fixture()
        people.append(dict(id='bird_01', name='云灵', image_path='bird.png'))
        normalized, audit = trial.normalize_observed_silent_entities(plan, people, state)
        self.assertEqual(normalized, plan)
        self.assertEqual(audit['migrated'], [])


if __name__ == '__main__':
    unittest.main()
