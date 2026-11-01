"""Synthetic story -> real prompt compiler -> actual API preflight, no models.

All stories below are newly authored test data.  The suite does not load a
saved project, create media, start a service, or replace any validator with a
fixed answer.  Both production compiler routes feed the same renderer gate.
"""
import copy
import os
from pathlib import Path
import re
import sys
import unittest

os.environ['V41_NO_MODEL'] = '1'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api import segment_api
from cores import authoring, director_flow, episode_contract, video_continuity
from cores import workbench_story_director_trial as trial


def story(key, text, names=('赵禾',), scene='测试地点', dialogue=(), **extra):
    return dict(key=key, text=text, names=names, scene=scene,
                dialogue=list(dialogue), **extra)


CASES = [
    story('daily_tea', '赵禾在厨房把茶杯放到桌上，钱川点头。',
          ('赵禾', '钱川'), '厨房'),
    story('daily_umbrella', '赵禾收起滴水的雨伞，随后把伞靠在玄关角落。', scene='玄关'),
    story('campus_children_science', '十岁的赵禾和十岁的钱川在小学科学课上把纸桥搭好，两人一起观察桥面的积木。',
          ('赵禾', '钱川'), '小学科学教室', age=10),
    story('campus_children_lost_property', '十岁的赵禾在小学图书角捡起一本练习册，随后把它交给十岁的钱川。',
          ('赵禾', '钱川'), '小学图书角', age=10),
    story('work_report', '赵禾放下核对完的报告说：“数据对上了。”',
          scene='办公室', dialogue=(('赵禾', '数据对上了。'),)),
    story('work_handover', '赵禾把工具箱递给钱川，钱川说：“我收到了。”',
          ('赵禾', '钱川'), '维修间', (('钱川', '我收到了。'),)),
    story('comedy_hat', '赵禾伸手找头上的帽子，钱川指向帽檐后两人相视而笑。',
          ('赵禾', '钱川'), '候车亭'),
    story('comedy_wrong_box', '赵禾郑重打开礼盒，里面弹出一只纸青蛙，赵禾愣了一下。', scene='客厅'),
    story('suspense_envelope', '赵禾在空书桌上发现一封没有署名的信，手停在信封上方。', scene='书房'),
    story('suspense_footprints', '赵禾沿着走廊的湿脚印照亮转角，脚印在关着的储物门前停止。', scene='走廊'),
    story('fantasy_seed', '赵禾把发光的种子放入花盆，一片透明叶子从土中缓缓展开。', scene='温室'),
    story('fantasy_clock', '赵禾拧动停摆的怀表，桌上的落叶逆着风轻轻旋起。', scene='钟表店'),
    story('xianxia_seal', '赵禾在山门石阶前展开符纸，一道光纹沿着石阶向上延伸。', scene='山门石阶'),
    story('xianxia_sword', '赵禾抬手收回悬停的木剑，木剑稳稳落入掌心。', scene='练剑台'),
    story('combat_shield', '赵禾举起木盾挡住钱川挥来的训练棍，两人随即退回各自的起点。',
          ('赵禾', '钱川'), '训练场'),
    story('combat_dodge', '赵禾侧身避开落下的训练沙袋，随后站稳并扶住摆动的沙袋。', scene='训练室'),
    story('silent_repair', '赵禾全程不说话，只把桌边松动的螺丝拧紧。', scene='工作台'),
    story('single_monologue', '赵禾面对录音机说：“今天终于修好了。”',
          scene='维修间', dialogue=(('赵禾', '今天终于修好了。'),)),
    story('three_people_turns', '赵禾说：“到齐了。”钱川说：“可以开始。”孙宁说：“我来记录。”',
          ('赵禾', '钱川', '孙宁'), '会议室',
          (('赵禾', '到齐了。'), ('钱川', '可以开始。'), ('孙宁', '我来记录。'))),
    story('three_people_script_lines', '赵禾：到齐了。\n钱川：可以开始。\n孙宁：我来记录。',
          ('赵禾', '钱川', '孙宁'), '会议室',
          (('赵禾', '到齐了。'), ('钱川', '可以开始。'), ('孙宁', '我来记录。'))),
    story('same_words_two_speakers', '赵禾举起一张纸说：“好了。”钱川放下笔说：“好了。”',
          ('赵禾', '钱川'), '画室', (('赵禾', '好了。'), ('钱川', '好了。'))),
    story('first_person_hands', '我的第一人称视角里，钱川把一只纸船推到我的手边。',
          ('钱川',), '河边桌旁', camera='我的第一人称视角，眼睛高度，只见我的手和钱川。'),
    story('first_person_dialogue', '我的第一人称视角里，钱川把钥匙交到我的掌心说：“收好了。”',
          ('钱川',), '门廊', (('钱川', '收好了。'),),
          camera='我的第一人称视角，眼睛高度，只见我的手和钱川。'),
    story('handheld_selfie', '赵禾单手持手机自拍，空着的手举起展览门票说：“我们到了。”',
          scene='展厅入口', dialogue=(('赵禾', '我们到了。'),),
          camera='赵禾单手持手机自拍，前置镜头保持一臂距离。'),
    story('surveillance_fixed', '监控固定高位俯拍，赵禾把拾到的钱包交给钱川，两人仍留在大厅。',
          ('赵禾', '钱川'), '大厅', camera='监控固定高位俯拍，全程固定机位。'),
    story('ordinary_observer', '普通第三人称镜头从侧面拍赵禾把装好的模型放回展柜。',
          scene='模型室', camera='普通第三人称侧面中景。'),
    story('written_sign', '赵禾在纸牌上写着“禁止入内”，随后把纸牌立在桌上。', scene='工作室'),
    story('quoted_inner_thought', '赵禾心想“这封信来得太晚了”，手指停在未拆的信封上。', scene='书房'),
    story('quoted_recalled_speech', '赵禾回想钱川昨天说过的“钥匙留在门口”，继续检查抽屉。', scene='储物间'),
    story('negated_departure', '赵禾没有走出店门，只把钥匙放到柜台上。', scene='商店'),
    story('dialogue_exit_intent_only', '赵禾说：“我准备出门。”随后仍站在柜台边。',
          scene='商店', dialogue=(('赵禾', '我准备出门。'),)),
    story('explicit_scene_transition', '赵禾推开店门走到门外的小院，把钥匙放进院内的信箱。', scene='小院'),
    story('continuous_handoff_start', '赵禾从桌上拿起红盒，递到钱川面前但尚未松手。',
          ('赵禾', '钱川'), '工作室'),
    story('continuous_handoff_finish', '钱川接稳赵禾递来的红盒，赵禾松开手。',
          ('赵禾', '钱川'), '工作室'),
]


def cards_for(case):
    return [dict(character_id='matrix_person_%d' % index, name=name,
                 age=case.get('age', 26), appearance='人物外观以对应原图为准。',
                 voice='自然清晰的中文声音。')
            for index, name in enumerate(case['names'], 1)]


def source_slice(case):
    cards = cards_for(case)
    numbered, parsed, report = episode_contract.prepare_source(case['text'], cards)
    if report['issues']:
        raise AssertionError('原文说话者未明确: %s' % report['issues'])
    if parsed != case['dialogue']:
        raise AssertionError('原文对白提取与语义不同: expected=%r actual=%r' % (case['dialogue'], parsed))
    ids = [item['dialogue_id'] for item in director_flow.units_of(numbered) if item.get('dialogue_id')]
    return dict(seconds=15, text=case['text'], scene_hint=case['scene'], chars=list(case['names']),
                tail='原文动作在当前地点完成或保持当前进度。', director=dict(
                    flow=director_flow.VERSION, cast=list(case['names']),
                    purpose='忠实落实当前原文，不增加情节。',
                    camera=case.get('camera', '中景，平视，缓慢推近。'),
                    start_state='unknown', end_state_text='原文动作在当前地点完成或保持当前进度。',
                    blocks=[[0, 15, numbered, ids]], says=copy.deepcopy(parsed)))


def compile_trial(case, sl):
    people = cards_for(case)
    plan = trial.build_plan([sl], people, visual_style='中文电影摄影')
    refs = [dict(name=row['name'], character_id=row['character_id'], voice=row['voice'],
                 subject=index, picture=index, role='character', path='matrix_person_%d.png' % index)
            for index, row in enumerate(people, 1)]
    refs.append(dict(name=case['scene'], subject=len(refs) + 1,
                     picture=len(refs) + 1, role='scene', path='matrix_scene.png'))
    _, payload, _ = trial.write_request(plan, plan['beats'][0],
        dict(actual_visible='unknown', completed=[], scene=case['scene']), refs,
        user_brief=case['text'])
    action = plan['beats'][0]['action']
    raw = dict(brief='按原文呈现动作、反应和已指定对白。',
        integrated_multimodal_description='[Shot 1] %s%s，镜头持续记录原文动作的发生过程。' %
            (case.get('camera', '平视中景。'), action),
        overall_soundscape='保留原文明确出现的环境声和动作声。', non_diegetic_music='N/A',
        expected_end_state='当前原文动作完成或保持明确的当前进度。',
        timeline=[dict(start=start, end=start + 5, action='推进当前原文动作。',
                       visible_result='原文动作进度清楚可见。') for start in (0, 5, 10)],
        dialogue=[dict(speaker=slot['speaker'], text=slot['text'], start=1 + index * 4,
                       end=3 + index * 4, delivery='自然清楚地说出当前原话。')
                  for index, slot in enumerate(payload['dialogue_slots'])], continuity_notes=[])
    before = copy.deepcopy((raw, payload))
    compiled = trial.compile_reply(raw, payload)
    if (raw, payload) != before:
        raise AssertionError('编译器修改了调用方词稿或请求')
    return compiled['prompt'], len(refs)


def compile_source_fallback(case, sl):
    clip = director_flow._clip(copy.deepcopy(sl), 1, cards_for(case), {}, '', {}, '')
    raw = director_flow.source_fallback(clip)
    return director_flow.assemble(raw, clip), len(clip['references'])


def segment(case, prompt):
    return dict(no=1, prompt=prompt, source_text=case['text'], seconds=15,
                scene=case['scene'], chars=list(case['names']), problems=[], critical_problems=[],
                plan=dict(flow=director_flow.VERSION))


def actual_gate(case, prompt, **updates):
    row = segment(case, prompt)
    row.update(updates)
    return segment_api._render_critical_problems({}, {}, row, prompt, 1, 1)


class StoryPreflightSemanticMatrixTests(unittest.TestCase):
    def check_story(self, case, compiler):
        sl = source_slice(case)
        snapshot = copy.deepcopy(sl)
        prompt, reference_count = compiler(case, sl)
        row = segment(case, prompt)
        final = video_continuity.scope_prompt(prompt, row, sl['director'])
        video_continuity.validate_prompt_references(final,
            ['not_opened_reference_%d.png' % index for index in range(reference_count)])
        self.assertEqual(sl, snapshot)
        self.assertEqual(re.findall(r'<d>\[Chinese\]\s*(.*?)</d>', final, re.S),
                         [words for _, words in case['dialogue']])
        self.assertTrue(all(field + ':' in final for field in director_flow.FIELDS))
        self.assertEqual(director_flow.prompt_problems(final, case['text'], 15, case['scene']), [])
        self.assertEqual(actual_gate(case, final), [],
                         '真实API提交门禁误拦：%s / %s' % (case['key'], compiler.__name__))
        if case.get('camera'):
            # Camera wording originates in the one-line story; transport must
            # preserve its explicit evidence rather than replace the viewpoint.
            if case['key'].startswith('first_person'):
                self.assertIn('第一人称视角', final)
            if case['key'] == 'handheld_selfie':
                self.assertIn('自拍', final)
            if case['key'] == 'surveillance_fixed':
                self.assertIn('监控固定高位俯拍', final)


def _install_semantic_cases():
    for case in CASES:
        for label, compiler in (('chinese_director', compile_trial), ('source_fallback', compile_source_fallback)):
            def run(self, case=case, compiler=compiler):
                self.check_story(case, compiler)
            run.__doc__ = case['text']
            setattr(StoryPreflightSemanticMatrixTests, 'test_%s_%s' % (label, case['key']), run)


_install_semantic_cases()


class StoryPreflightRealBoundaryTests(unittest.TestCase):
    @staticmethod
    def prepared(key='three_people_script_lines', route=compile_source_fallback):
        case = next(row for row in CASES if row['key'] == key)
        prompt, _ = route(case, source_slice(case))
        return case, prompt

    def test_actual_gate_rejects_missing_six_field(self):
        case, prompt = self.prepared()
        self.assertTrue(actual_gate(case, prompt.replace('overall_soundscape:', 'soundscape:')))

    def test_actual_gate_rejects_missing_scene(self):
        case, prompt = self.prepared()
        self.assertTrue(actual_gate(case, prompt, scene=''))

    def test_actual_gate_rejects_out_of_range_duration(self):
        case, prompt = self.prepared()
        for seconds in (3, 16):
            with self.subTest(seconds=seconds):
                self.assertTrue(actual_gate(case, prompt, seconds=seconds))

    def test_actual_gate_rejects_changed_or_missing_dialogue(self):
        case, prompt = self.prepared()
        for broken in (prompt.replace('到齐了。', '人都来了。'),
                       re.sub(r'<d>\[Chinese\].*?</d>', '', prompt, count=1)):
            with self.subTest(prompt=broken[-160:]):
                self.assertTrue(actual_gate(case, broken))

    def test_actual_gate_rejects_reversed_dialogue_order(self):
        case, prompt = self.prepared()
        lines = re.findall(r'<d>\[Chinese\].*?</d>', prompt)
        swapped = prompt.replace(lines[0], '__FIRST_LINE__').replace(lines[1], lines[0]).replace('__FIRST_LINE__', lines[1])
        self.assertTrue(actual_gate(case, swapped))

    def test_actual_gate_rejects_new_departure_after_negation_or_intent(self):
        for key in ('negated_departure', 'dialogue_exit_intent_only'):
            case, prompt = self.prepared(key)
            changed = prompt.replace('overall_soundscape:', '赵禾走出店门。\n\noverall_soundscape:')
            with self.subTest(key=key):
                self.assertTrue(any('离场' in problem for problem in actual_gate(case, changed)))

    def test_actual_gate_uses_current_segment_source_when_slicing_source_is_stale(self):
        case, prompt = self.prepared()
        row = segment(case, prompt)
        tl = dict(slicing=dict(slices=[dict(text='赵禾：另一段话。')]))
        self.assertEqual(segment_api._render_critical_problems({}, tl, row, prompt, 1, 1), [])

    def test_actual_gate_reads_slicing_source_if_saved_segment_source_is_missing(self):
        case, prompt = self.prepared()
        row = segment(case, prompt)
        row.pop('source_text')
        tl = dict(slicing=dict(slices=[dict(text=case['text'])]))
        self.assertEqual(segment_api._render_critical_problems({}, tl, row, prompt, 1, 1), [])

    def test_continuous_segments_keep_handoff_order_and_actual_state_unknown(self):
        first = next(row for row in CASES if row['key'] == 'continuous_handoff_start')
        second = next(row for row in CASES if row['key'] == 'continuous_handoff_finish')
        slices = [source_slice(first), source_slice(second)]
        plan = trial.build_plan(slices, cards_for(first))
        self.assertIn('尚未松手', plan['beats'][0]['action'])
        self.assertIn('松开手', plan['beats'][1]['action'])
        for case, sl in zip((first, second), slices):
            prompt, _ = compile_trial(case, sl)
            self.assertEqual(actual_gate(case, prompt), [])
        self.assertEqual(plan['beats'][1]['intended_start_state'], 'unknown')

    def test_legacy_h3_format_passes_real_validator_and_actual_api_gate(self):
        shots = '赵禾把报告放在桌上。\n赵禾说：“核对完毕。”'
        prompt = '镜头1（15秒｜平视｜固定）：赵禾中景，把报告放在桌上。\n<Subject 1> (S1) says:<d>[Chinese]核对完毕。</d>'
        problems = authoring.h3_problems(prompt, shots, [dict(name='赵禾')])
        self.assertEqual(problems, [])
        self.assertEqual(authoring.h3_critical_problems(prompt, shots, problems, dict(seconds=15), '办公室'), [])
        row = dict(source_text=shots, chars=['赵禾'], seconds=15, scene='办公室')
        self.assertEqual(segment_api._render_critical_problems({}, {}, row, prompt, 1, 1), [])

    def test_legacy_h3_wrong_spoken_words_block_at_actual_api_gate(self):
        shots = '赵禾把报告放在桌上。\n赵禾说：“核对完毕。”'
        prompt = '镜头1（15秒｜平视｜固定）：赵禾中景。\n<Subject 1> (S1) says:<d>[Chinese]明天再说。</d>'
        problems = authoring.h3_problems(prompt, shots, [dict(name='赵禾')])
        self.assertTrue(any('查无原话' in problem for problem in problems))
        row = dict(source_text=shots, chars=['赵禾'], seconds=15, scene='办公室')
        self.assertTrue(segment_api._render_critical_problems({}, {}, row, prompt, 1, 1))

    def test_saved_source_prompt_wrong_speaker_binding_blocks_at_actual_api_gate(self):
        case, prompt = self.prepared()
        changed = prompt.replace('<Subject 1> (S1) says, <d>', '<Subject 2> (S1) says, <d>', 1)
        self.assertNotEqual(changed, prompt, '必须先实际破坏第一句的说话者身份')
        self.assertTrue(actual_gate(case, changed), '保留原话但借用别人的Subject，实际提交门禁必须阻止串角色')

    def test_saved_source_prompt_duplicate_subject_definition_blocks_at_actual_api_gate(self):
        case, prompt = self.prepared()
        changed = prompt.replace('summary:', '<Subject 1> is 钱川, the character from <Picture 2>.\n\nsummary:', 1)
        self.assertTrue(actual_gate(case, changed), '同一个Subject绑定两个不同人物应在提交前阻止')

    def test_vocal_ids_are_independent_of_reference_order_and_may_be_nonnumeric(self):
        case = story('vocal_order', '钱川：先核对。\n赵禾：已经核对。',
                     ('赵禾', '钱川'), '办公室', (('钱川', '先核对。'), ('赵禾', '已经核对。')))
        for route in (compile_trial, compile_source_fallback):
            prompt, _ = route(case, source_slice(case))
            self.assertRegex(prompt, r'<Subject 2>[^\n]*\(S1\)')
            for variant in (prompt, re.sub(r'\(S(\d+)\)', r'(Schar_0\1)', prompt)):
                with self.subTest(route=route.__name__, nonnumeric='Schar_' in variant):
                    self.assertEqual(actual_gate(case, variant), [])

    def test_chinese_director_delivery_wrong_subject_blocks_without_says_keyword(self):
        case, prompt = self.prepared(route=compile_trial)
        changed = prompt.replace('<Subject 1>，音色描述', '<Subject 2>，音色描述', 1)
        self.assertNotEqual(changed, prompt)
        self.assertTrue(actual_gate(case, changed))

    def test_scene_or_undefined_subject_cannot_own_a_character_line(self):
        case, prompt = self.prepared()
        for subject in (4, 99):
            changed = prompt.replace('<Subject 1> (S1) says, <d>',
                                     '<Subject %d> (S1) says, <d>' % subject, 1)
            with self.subTest(subject=subject):
                self.assertTrue(actual_gate(case, changed))

    def test_one_subject_cannot_bind_conflicting_pictures_for_the_same_name(self):
        case, prompt = self.prepared()
        changed = prompt.replace('summary:', '<Subject 1> is 赵禾, the character from <Picture 2>.\n\nsummary:', 1)
        self.assertTrue(actual_gate(case, changed))

    def test_unbound_source_voice_keeps_its_identity_without_borrowing_a_reference(self):
        case = story('offscreen_voice', '门外的人：请开门。', scene='门廊',
                     dialogue=(('门外的人', '请开门。'),))
        for route in (compile_trial, compile_source_fallback):
            prompt, _ = route(case, source_slice(case))
            with self.subTest(route=route.__name__):
                self.assertEqual(actual_gate(case, prompt), [])
        changed = prompt.replace('The source voice "门外的人"', 'The source voice "楼下的人"', 1)
        self.assertNotEqual(changed, prompt)
        self.assertTrue(actual_gate(case, changed))

    def test_named_collective_retains_its_actual_members_and_compound_vocal_ids(self):
        case = story('named_collective', '赵禾和钱川站在门边。\n两人：好的。',
                     ('赵禾', '钱川'), '门廊', (('两人', '好的。'),))
        cards = cards_for(case) + [dict(name='两人', entity_type='group',
                                       group_members=['赵禾', '钱川'], group_count=2)]
        clip = director_flow._clip(source_slice(case), 1, cards, {}, '', {}, '')
        prompt = director_flow.assemble(director_flow.source_fallback(clip), clip)
        self.assertIn('(S1,S2)', prompt)
        self.assertEqual(actual_gate(case, prompt), [])
        changed = prompt.replace('comprising <Subject 1> and <Subject 2>',
                                 'comprising <Subject 1> and <Subject 3>', 1)
        self.assertNotEqual(changed, prompt)
        self.assertTrue(actual_gate(case, changed))

    def test_chinese_identity_definition_uses_the_existing_reference_binding_format(self):
        case, prompt = self.prepared()
        changed = prompt.replace('<Subject 1> is 赵禾, the character from <Picture 1>.',
                                 '<Subject 1>赵禾是<Picture 1>中的人物。', 1)
        self.assertEqual(actual_gate(case, changed), [])


if __name__ == '__main__':
    unittest.main()
