"""Real cached-prompt retry dispatch, safe adult fixtures and no model/media IO."""
import copy
import os
import unittest
from contextlib import ExitStack
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from api import segment_api
from cores import authoring, asset_core, saga_core, story_core


class CachedPromptRepairChainTests(unittest.TestCase):
    def setUp(self):
        self.sid = 'SAFE_POV_CACHE_RETRY'
        self.settings = dict(pov='第一视角POV', camera_owner='我')
        self.cards = [dict(name='阿宁', age=30, appearance='成年修表师，普通工作服')]
        self.source = '我主观视角，阿宁把手表放在柜台上。\n阿宁：请拿好。'
        self.prompt = ('<Subject1>阿宁是<Picture 1>中的人物。\n'
                       '<Subject 2>是<Picture 2>修表店场景。\n'
                       '【机位】我主观视角，眼睛高度。\n'
                       '0—8秒：我主观视角，阿宁把手表放在柜台上。\n'
                       '阿宁说：<Subject 1> (S1) says:<d>[Chinese]请拿好。</d>\n'
                       '这一段结束时：手表在柜台上。')
        self.rows = [dict(no=n, prompt=self.prompt, source_text=self.source,
                         chars=['阿宁'], scene='修表店', seconds=8, plan={},
                         video='kept_clip_%d.mp4' % n, tail='手表在柜台上。') for n in (1, 2, 3)]
        self.timeline = dict(scenes=[dict(no=1, segments=self.rows)], slicing=dict(slices=[
            dict(text=self.source, seconds=8, scene_hint='修表店', director=dict(flow='source-v1'))
            for _ in self.rows]))
        self.story = dict(settings=self.settings, saga=dict(episodes=[dict(
            no=1, prose=self.source, pictures=self.source, timeline=self.timeline)]))
        self.saved = []
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(authoring, 'apply_slicing_drift', return_value=(0, 0)))
        self.stack.enter_context(patch.object(authoring, 'total_segments', return_value=3))
        self.stack.enter_context(patch.object(authoring, '_ep', return_value=(self.story['saga'], self.story['saga']['episodes'][0])))
        self.stack.enter_context(patch.object(authoring, 'cast_cards', return_value=copy.deepcopy(self.cards)))
        self.stack.enter_context(patch.object(authoring, '_dbg'))
        self.stack.enter_context(patch.object(story_core, 'get_story', return_value=self.story))
        self.stack.enter_context(patch.object(asset_core, 'list_assets', side_effect=lambda sid, kind:
            copy.deepcopy(self.cards) if kind == 'characters' else []))
        self.stack.enter_context(patch.object(saga_core, 'ep_timeline', side_effect=lambda *args: self.timeline))
        self.stack.enter_context(patch.object(saga_core, 'save_ep_timeline', side_effect=self.save))
        self.stack.enter_context(patch.object(authoring, 'build_timeline', side_effect=self.build))

    def save(self, sid, ep, timeline):
        self.timeline = timeline
        self.saved.append(copy.deepcopy(timeline))
        return True

    def build(self, sid, ep, replies):
        for reply in replies:
            row = next(row for row in self.rows if row['no'] == reply['n'])
            row.update(prompt=reply['prompt'], source_text=reply['source_text'],
                       problems=reply.get('problems', []), critical_problems=[])
            row.pop('prompt_repair_pending', None)

    def test_valid_legacy_pov_cache_is_reused_despite_source_v1_frozen_slices(self):
        before = copy.deepcopy(self.timeline)
        self.assertEqual(segment_api._render_critical_problems(self.story, self.timeline,
            self.rows[0], self.prompt, 1, 1), [])
        with patch.object(authoring, 'make_h3_prompts', side_effect=AssertionError('Valid cached POV must not be rewritten')) as writer:
            self.assertEqual(authoring.next_prompts(self.sid, 1, 3), ([], 3))
        writer.assert_not_called()
        self.assertEqual(self.timeline, before)
        self.assertFalse(self.saved)

    def test_only_broken_second_cache_is_repaired_first_and_third_stay_exact(self):
        self.rows[1]['prompt'] = self.prompt.split('阿宁说：', 1)[0]
        original_first, original_third = copy.deepcopy(self.rows[0]), copy.deepcopy(self.rows[2])
        targets = []
        def write(sid, ep, *, start, count, **kwargs):
            targets.extend(range(start + 1, start + count + 1))
            return [dict(n=n, prompt=self.prompt, source_text=self.source) for n in targets]
        with patch.object(authoring, 'make_h3_prompts', side_effect=write):
            written, total = authoring.next_prompts(self.sid, 1, 3)
        self.assertEqual((written, total), ([2], 3))
        self.assertEqual(targets, [2])
        self.assertEqual(self.rows[0], original_first)
        self.assertEqual(self.rows[2], original_third)
        self.assertEqual(segment_api._render_critical_problems(self.story, self.timeline,
            self.rows[1], self.rows[1]['prompt'], 1, 2), [])

    def test_legacy_cache_without_flow_metadata_still_repairs_its_actual_missing_speech(self):
        for frozen in self.timeline['slicing']['slices']:
            frozen.pop('director')
        self.rows[1]['prompt'] = self.prompt.split('阿宁说：', 1)[0]
        targets = []
        def write(sid, ep, *, start, count, **kwargs):
            targets.extend(range(start + 1, start + count + 1))
            return [dict(n=n, prompt=self.prompt, source_text=self.source) for n in targets]
        with patch.object(authoring, 'make_h3_prompts', side_effect=write):
            written, total = authoring.next_prompts(self.sid, 1, 3)
        self.assertEqual((written, total), ([2], 3))
        self.assertEqual(targets, [2])

    def test_recovered_cache_clears_old_pending_reminder_without_rewriting_video(self):
        self.rows[1]['prompt_repair_pending'] = '旧漏句记录'
        self.timeline['prompt_failures'] = {'2': '旧漏句记录'}
        videos = [row['video'] for row in self.rows]
        with patch.object(authoring, 'make_h3_prompts', side_effect=AssertionError('Recovered cache must stay')):
            self.assertEqual(authoring.next_prompts(self.sid, 1, 3), ([], 3))
        self.assertNotIn('prompt_repair_pending', self.rows[1])
        self.assertFalse(self.timeline['prompt_failures'])
        self.assertEqual([row['video'] for row in self.rows], videos)


if __name__ == '__main__':
    unittest.main()
