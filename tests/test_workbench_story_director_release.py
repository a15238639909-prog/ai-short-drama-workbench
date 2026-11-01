"""Production original-director transport checks. No AI or video is invoked."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from cores import workbench_story_director as release


def fixture():
    cards = [dict(character_id='a', name='阿岚', appearance='成年青年，沿用原图。',
                  voice='清朗温和的成年男声', _has_image=True),
             dict(character_id='b', name='顾亭', appearance='成年青年，沿用原图。',
                  voice='温柔清晰的成年女声', _has_image=True)]
    slices = []
    for index, (seconds, speaker, text, did) in enumerate(((6, '阿岚', '慢一点。', 9),
                                                        (9, '顾亭', '这回稳了。', 12))):
        slices.append(dict(seconds=seconds, scene_hint='竹庭', chars=['阿岚', '顾亭'],
            text='阿岚扶稳茶杯。\n%s：%s' % (speaker, text),
            director=dict(flow='source-v1', camera='中景至人物近景，按原文拍清承托。',
                purpose='扶稳茶杯后交换提醒。', cast=['阿岚', '顾亭'],
                start_state='计划尾态：杯子已经被放在秘密石台。', target_state='茶杯平稳停住。',
                blocks=[[0, seconds / 2, '阿岚扶稳茶杯。', []],
                        [seconds / 2, seconds, '【D%d】%s：%s' % (did, speaker, text), [did]]],
                says=[[speaker, text]])))
    # These regressions retain the accepted legacy director/rollback contract.
    # The new default source-owned route has its own integration suite.
    settings = dict(video_action_compiler='legacy', style='写实摄影', _director_reference_paths={name: __file__ for name in ('阿岚', '顾亭', '竹庭')},
                    _director_user_brief='在竹庭扶稳茶杯并自然交流。')
    return slices, cards, settings


def response(payload):
    seconds = payload['seconds']
    third = seconds / 3
    return dict(brief='茶杯停稳，人物自然回应。',
        integrated_multimodal_description=(
            '[Shot 1] 中景交代两人在庭中扶稳茶杯，动作有持续的承托过程。\n\n'
            '[Shot 2] At 00:%06.3f, 镜头切至人物近景，说话者侧头自然提醒对方。\n\n'
            '[Shot 3] At 00:%06.3f, 镜头切至双人中景，茶杯平稳停住，两人交换目光。') % (third, third * 2),
        overall_soundscape='庭中风声与茶杯触及木桌的轻响。', non_diegetic_music='N/A',
        expected_end_state='茶杯平稳停住。',
        timeline=[dict(start=third * i, end=third * (i + 1), action='原文承托与自然回应持续。',
                       visible_result='茶杯平稳停住。') for i in range(3)],
        dialogue=[dict(speaker=line['speaker'], text=line['text'], start=.3, end=1.8,
                       delivery='自然清楚地说话。') for line in payload['dialogue_slots']],
        continuity_notes=[])


class OriginalDirectorReleaseTests(unittest.TestCase):
    def test_chinese_multishot_exact_source_voice_blocks_and_current_references(self):
        slices, cards, settings = fixture()
        before = copy.deepcopy((slices, cards, settings))
        requests, rows, traces = [], [], []
        def q(system, user, **kwargs):
            payload = json.loads(user)
            requests.append((system, payload))
            return json.dumps(response(payload), ensure_ascii=False)
        with patch.object(Path, 'write_text', side_effect=lambda text, **kwargs: traces.append(json.loads(text))), \
             patch.object(Path, 'mkdir'):
            result = release.make_prompts(slices, cards, ['竹庭'], q, settings=settings,
                on_prompt=rows.append, trace_dir='not_created',
                actual_state_for=lambda index: dict(actual_visible='unknown', scene='unknown'))
        self.assertEqual(len(requests), 2)
        self.assertEqual(result, rows)
        self.assertEqual([len(payload['canonical_references']) for _, payload in requests], [3, 2])
        self.assertIn('中文镜头正文', requests[0][0])
        self.assertIn('[Shot 3] At 00:04.000', result[0]['prompt'])
        self.assertIn('清朗温和的成年男声', result[0]['prompt'])
        self.assertIn('温柔清晰的成年女声', result[1]['prompt'])
        self.assertIn('(S2)', result[1]['prompt'])
        self.assertEqual(result[0]['prompt'].count('慢一点。'), 1)
        self.assertEqual(result[1]['prompt'].count('这回稳了。'), 1)
        self.assertEqual(result[0]['director_contract']['blocks'][1][3], [9])
        self.assertEqual(result[1]['director_contract']['blocks'][1][3], [12])
        self.assertEqual(result[0]['source_text'], slices[0]['text'])
        self.assertEqual(result[0]['plan']['director_profile'], release.PROFILE)
        self.assertEqual(result[0]['director_contract']['writer_profile'], release.PROFILE)
        self.assertTrue(traces[0]['attempts'][0]['compiled']['prompt'])
        self.assertTrue(traces[0]['attempts'][0]['raw'])
        expected = copy.deepcopy(before)
        for source in expected[0]:
            source['director']['intended_start_state'] = source['director']['start_state']
            source['director']['start_state'] = ''
        self.assertEqual((slices, cards, settings), expected)

    def test_unknown_observation_continues_without_promoting_written_tail(self):
        slices, cards, settings = fixture()
        first_before = copy.deepcopy(slices[0])
        seen, indices = [], []
        def observe(index):
            indices.append(index)
            raise RuntimeError('可选观察不可用')
        def q(system, user, **kwargs):
            payload = json.loads(user)
            seen.append(payload)
            return json.dumps(response(payload), ensure_ascii=False)
        rows = release.make_prompts(slices, cards, ['竹庭'], q, settings=settings,
            start=1, count=1, prev_tail='计划尾态：杯子已经被放在秘密石台。', actual_state_for=observe)
        self.assertEqual(indices, [1])
        self.assertEqual(rows[0]['n'], 2)
        self.assertEqual(seen[0]['actual_state']['actual_visible'], 'unknown')
        self.assertNotIn('秘密石台', json.dumps(seen[0]['actual_state'], ensure_ascii=False))
        self.assertEqual(rows[0]['director_contract']['start_state'], '')
        self.assertIn('秘密石台', rows[0]['director_contract']['intended_start_state'])
        self.assertEqual(slices[1]['director']['start_state'], '')
        self.assertEqual(slices[0], first_before)
        self.assertTrue(rows[0]['problems'])

    def test_bad_format_has_two_repairs_then_source_fallback_without_stopping(self):
        slices, cards, settings = fixture()
        calls, failures = [], []
        def q(system, user, **kwargs):
            calls.append(json.loads(user))
            return '模型答复格式错误'
        rows = release.make_prompts(slices, cards, ['竹庭'], q, settings=settings,
                                    count=1, on_error=lambda *args: failures.append(args))
        self.assertEqual(len(calls), 3)
        self.assertEqual(failures, [])
        self.assertTrue(rows[0]['source_fallback'])
        self.assertEqual(rows[0]['critical_problems'], [])
        self.assertEqual(rows[0]['prompt'].count('慢一点。'), 1)
        self.assertEqual(rows[0]['director_contract']['blocks'][1][3], [9])
        self.assertIn('<Subject 1>', rows[0]['prompt'])
        self.assertTrue(rows[0]['problems'])

    def test_missing_picture_or_subjective_mode_uses_existing_compatibility_route(self):
        slices, cards, settings = fixture()
        for changes in (dict(_director_reference_paths={}), dict(pov='第一视角POV')):
            cfg = dict(settings, **changes)
            with self.subTest(changes=changes), patch.object(release.legacy, 'make_prompts', return_value=['legacy']) as old:
                rows = release.make_prompts(slices, cards, ['竹庭'], lambda *_args, **_kw: self.fail('无图/POV不调用新导演'), settings=cfg)
                self.assertEqual(rows[0], 'legacy')
                self.assertTrue(old.called)
        self.assertTrue(release.enabled({}))
        self.assertFalse(release.enabled(dict(video_director_profile='legacy')))
        self.assertEqual(release.profile_info()['scope'], 'new-prompts')
        self.assertIsNone(release.profile_info(dict(video_director_profile='legacy')))

    def test_four_second_silent_scene_has_no_fixed_human_or_segment_count(self):
        sl = dict(seconds=4, scene_hint='山谷', text='薄雾沿溪水移动。', chars=[],
            director=dict(flow='source-v1', cast=[], camera='远景', purpose='薄雾自然移动。',
                blocks=[[0, 4, '薄雾沿溪水移动。', []]], says=[]))
        def q(system, user, **kwargs):
            payload = json.loads(user)
            value = response(payload)
            value['integrated_multimodal_description'] = '[Shot 1] 远景，薄雾沿溪水自然移动，持续四秒。'
            return json.dumps(value, ensure_ascii=False)
        rows = release.make_prompts([sl], [], ['山谷'], q,
            settings=dict(video_action_compiler='legacy', _director_reference_paths={'山谷': __file__}, style='写实摄影'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['seconds'], 4)
        self.assertEqual(rows[0]['chars'], [])
        self.assertNotIn('character from', rows[0]['prompt'])
        self.assertIn('the environment from <Picture 1>', rows[0]['prompt'])

    def test_final_preflight_failure_is_repaired_before_returning_to_renderer(self):
        slices, cards, settings = fixture()
        attempts = []
        def query(system, user, **kwargs):
            payload = json.loads(user)
            attempts.append(payload)
            value = response(payload)
            if len(attempts) == 1:
                value['integrated_multimodal_description'] += '\n阿岚走出门外。'
            return json.dumps(value, ensure_ascii=False)
        rows = release.make_prompts(slices, cards, ['竹庭'], query, settings=settings, count=1)
        self.assertEqual(len(attempts), 2)
        self.assertIn('离场', attempts[1]['format_error'])
        self.assertFalse(rows[0]['source_fallback'])
        self.assertNotIn('走出门外', rows[0]['prompt'])

    def test_final_preflight_exhaustion_uses_exact_source_and_continues(self):
        slices, cards, settings = fixture()
        calls = []
        def query(system, user, **kwargs):
            payload = json.loads(user)
            calls.append(payload)
            value = response(payload)
            value['integrated_multimodal_description'] += '\n阿岚走出门外。'
            return json.dumps(value, ensure_ascii=False)
        rows = release.make_prompts(slices, cards, ['竹庭'], query, settings=settings, count=1)
        self.assertEqual(len(calls), 3)
        self.assertTrue(rows[0]['source_fallback'])
        self.assertNotIn('走出门外', rows[0]['prompt'])
        self.assertEqual(rows[0]['prompt'].count('慢一点。'), 1)
        self.assertEqual(release.legacy.prompt_problems(rows[0]['prompt'],
            slices[0]['text'], slices[0]['seconds'], '竹庭'), [])

    def test_auxiliary_trace_access_denied_keeps_completed_prompt(self):
        slices, cards, settings = fixture()
        def query(system, user, **kwargs):
            return json.dumps(response(json.loads(user)), ensure_ascii=False)
        with patch.object(Path, 'mkdir', side_effect=PermissionError('[WinError 5] 拒绝访问')):
            rows = release.make_prompts(slices, cards, ['竹庭'], query,
                settings=settings, count=1, trace_dir='unused')
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['source_fallback'])
        self.assertTrue(any('日志未保存' in warning for warning in rows[0]['problems']))

    def test_unavailable_text_service_uses_confirmed_source_for_each_slot(self):
        slices, cards, settings = fixture()
        calls = []
        def unavailable(*args, **kwargs):
            calls.append(1)
            raise TimeoutError('本地文字服务连接超时')
        rows = release.make_prompts(slices, cards, ['竹庭'], unavailable, settings=settings)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['source_fallback'] for row in rows))
        self.assertEqual(rows[0]['prompt'].count('慢一点。'), 1)
        self.assertEqual(rows[1]['prompt'].count('这回稳了。'), 1)

    def test_game_guard_is_never_converted_to_source_fallback(self):
        slices, cards, settings = fixture()
        def blocked(*args, **kwargs):
            raise RuntimeError('PUBG正在运行')
        with self.assertRaisesRegex(RuntimeError, 'PUBG'):
            release.make_prompts(slices, cards, ['竹庭'], blocked, settings=settings, count=1)

    def test_optional_observation_os_error_continues_but_user_stop_propagates(self):
        slices, cards, settings = fixture()
        def query(system, user, **kwargs):
            return json.dumps(response(json.loads(user)), ensure_ascii=False)
        def unavailable(index):
            raise TimeoutError('可选观察超时')
        rows = release.make_prompts(slices, cards, ['竹庭'], query,
            settings=settings, count=1, actual_state_for=unavailable)
        self.assertEqual(len(rows), 1)
        def canceled(index):
            raise RuntimeError('已取消')
        with self.assertRaisesRegex(RuntimeError, '已取消'):
            release.make_prompts(slices, cards, ['竹庭'], query,
                settings=settings, count=1, actual_state_for=canceled)


if __name__ == '__main__':
    unittest.main()
