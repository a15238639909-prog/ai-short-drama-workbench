"""Zero-model regressions for compact original-director transport."""
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from cores import workbench_story_director as release


os.environ['V41_NO_MODEL'] = '1'
ROOT = Path(__file__).resolve().parents[1]


def request_fixture():
    cards = [dict(character_id='a', name='阿岚', appearance='成年青年，沿用原图。',
                  voice='清朗温和的成年男声'),
             dict(character_id='b', name='顾亭', appearance='成年青年，沿用原图。',
                  voice='温柔清晰的成年女声')]
    slices = []
    for index in range(9):
        action = ('第%d段，' % (index + 1)) + '阿岚沿着石阶扶稳茶杯，查看脚边木箱后再向前移动。' * 18
        words = '第%d层石阶到了，慢一点。' % (index + 1)
        source = action + '\n顾亭：“' + words + '”\n阿岚在台阶边停下。'
        slices.append(dict(seconds=9, scene_hint='石阶', text=source,
            source_order=[dict(id='a%d' % index, text=action, dialogue_id=None),
                          dict(id='d%d' % index, text=words, dialogue_id=index + 1, speaker='顾亭'),
                          dict(id='z%d' % index, text='阿岚在台阶边停下。', dialogue_id=None)],
            director=dict(flow='source-v1', purpose='保持平稳，继续沿石阶前进。',
                cast=['阿岚', '顾亭'], target_state='两人在台阶边停下。',
                blocks=[[0, 4, action, []], [4, 9, source, [index + 1]]],
                says=[['顾亭', words]])))
    plan = release.original.build_plan(slices, cards, visual_style='写实摄影')
    state = dict(actual_visible='阿岚和顾亭站在石阶上，茶杯仍在阿岚手中。',
        scene='石阶', characters=[dict(id='a', pose='站立', certainty='observed')],
        completed=['已走上第一层石阶。'], unknowns=[])
    refs = [dict(role='character', character_id=card['character_id'], name=card['name'],
                 path=__file__, picture=index + 1, subject=index + 1, voice=card['voice'])
            for index, card in enumerate(cards)]
    refs.append(dict(role='scene', name='石阶', path=__file__, picture=3))
    request = release.original.write_request(plan, plan['beats'][1], state, refs,
        seconds=9, visual_style='写实摄影', scene_name='石阶', user_brief='两人在石阶上扶稳茶杯。')
    return request, refs


class DirectorContextBudgetTests(unittest.TestCase):
    def assert_preserved(self, original, compact):
        for key in ('source_text', 'source_order'):
            self.assertEqual(compact['beat'][key], original['beat'][key])
        for key in ('actual_state', 'dialogue_slots', 'canonical_references', 'timing_budget',
                    'seconds', 'is_final', 'user_brief', 'reference_visual_policy'):
            self.assertEqual(compact[key], original[key])
        self.assertEqual(compact['story_plan']['characters'], original['story_plan']['characters'])
        self.assertEqual(compact['story_plan']['ending'], original['story_plan']['ending'])
        self.assertNotIn('source_contract', compact['beat'])
        self.assertNotIn('references', compact['workbench_trial'])
        for beat in compact['story_plan']['beats']:
            self.assertFalse(set(beat) & {'source_text', 'source_order', 'source_contract', 'action', 'dialogue'})

    def test_model_gets_compact_copy_and_compiler_gets_unchanged_complete_payload(self):
        request, refs = request_fixture()
        before = copy.deepcopy(request)
        sent, trace = [], []

        def query(system, user, **kwargs):
            sent.append(json.loads(user))
            self.assertTrue(kwargs['_check_context'])
            self.assertEqual(kwargs['response_format']['schema'], request[2])
            return '{}'

        def compile_reply(parsed, payload):
            self.assertIs(payload, request[1])
            self.assertIn('source_contract', payload['beat'])
            return dict(prompt='已编译测试词')

        with patch.object(release.original, 'compile_reply', side_effect=compile_reply), \
                patch.object(release.identity_continuity, 'apply_compiled_authority', side_effect=lambda value: value):
            prompt, compiled, fallback = release._request(query, request, {}, refs, trace)
        self.assertEqual(prompt, '已编译测试词')
        self.assertFalse(fallback)
        self.assertEqual(request, before)
        self.assert_preserved(request[1], sent[0])
        self.assertLess(len(json.dumps(sent[0])), len(json.dumps(request[1])) * .4)
        self.assertEqual(trace[0]['payload'], sent[0])
        self.assertTrue(compiled)

    def test_format_repair_reuses_compact_copy_with_exact_dialogue_and_order(self):
        request, refs = request_fixture()
        before = copy.deepcopy(request)
        sent, trace = [], []

        def query(system, user, **kwargs):
            sent.append(json.loads(user))
            self.assertTrue(kwargs['_check_context'])
            return '格式错误' if len(sent) == 1 else '{}'

        with patch.object(release.original, 'compile_reply', return_value=dict(prompt='修复后的测试词')) as compiler, \
                patch.object(release.identity_continuity, 'apply_compiled_authority', side_effect=lambda value: value):
            release._request(query, request, {}, refs, trace)
        self.assertEqual(len(sent), 2)
        for payload in sent:
            self.assert_preserved(request[1], payload)
            self.assertLess(len(json.dumps(payload)), len(json.dumps(request[1])) * .4)
        self.assertEqual(sent[1]['previous_reply'], '格式错误')
        self.assertEqual(sent[0]['story_plan'], sent[1]['story_plan'])
        self.assertEqual(request, before)
        self.assertIs(compiler.call_args.args[1], request[1])

    def test_global_goal_does_not_repeat_fallback_action_or_oversized_purpose(self):
        request, _ = request_fixture()
        payload = request[1]
        payload['story_plan']['beats'][0]['goal'] = payload['story_plan']['beats'][0]['action']
        payload['story_plan']['beats'][1]['goal'] = '场景方向。' * 100
        payload['beat']['goal'] = payload['beat']['action']
        before = copy.deepcopy(payload)
        compact = release._model_payload(payload)
        self.assertNotIn('goal', compact['story_plan']['beats'][0])
        self.assertNotIn('goal', compact['story_plan']['beats'][1])
        self.assertNotEqual(compact['beat']['goal'], payload['beat']['action'])
        self.assert_preserved(payload, compact)
        self.assertEqual(payload, before)

    def test_project50_saved_requests_keep_all_current_source_and_reduce_payload(self):
        paths = sorted((ROOT / 'outputs/节拍表/STORY_050_e1_连续写词').glob('clip_*.json'))
        if not paths:
            self.skipTest('项目50本地只读日志未随代码分发')
        checked = 0
        for path in paths:
            data = json.loads(path.read_text(encoding='utf-8'))
            for attempt in data.get('attempts') or []:
                payload = attempt.get('payload')
                if not payload or 'story_plan' not in payload:
                    continue
                with self.subTest(trace=path.name):
                    before = copy.deepcopy(payload)
                    compact = release._model_payload(payload)
                    self.assert_preserved(payload, compact)
                    full_plan = any(set(beat) & {'source_text', 'source_order', 'source_contract', 'action', 'dialogue'}
                                    for beat in payload['story_plan'].get('beats') or [])
                    if full_plan:
                        self.assertLess(len(json.dumps(compact)), len(json.dumps(payload)) * .4)
                    else:
                        self.assertEqual(compact, payload)
                    self.assertEqual(payload, before)
                    checked += 1
                break
        self.assertGreater(checked, 0)

    def test_compaction_is_idempotent_for_already_compact_requests(self):
        request, _ = request_fixture()
        compact = release._model_payload(request[1])
        before = copy.deepcopy(compact)
        self.assertEqual(release._model_payload(compact), compact)
        self.assertEqual(compact, before)


if __name__ == '__main__':
    unittest.main()
