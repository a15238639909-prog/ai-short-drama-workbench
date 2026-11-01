"""Current prompt -> actual render preflight across old/new transport and caches."""
import copy
import unittest
from api import segment_api
from cores import authoring
from tests import test_video_preflight_story_matrix as matrix


class PromptFormatDispatchTests(unittest.TestCase):
    def legacy(self, source='赵禾：收好了。', spoken=None):
        spoken = [('赵禾', 1, '收好了。')] if spoken is None else spoken
        prompt = ('<Subject1>赵禾是<Picture 1>中的人物。\n<Subject2>钱川是<Picture 2>中的人物。\n'
                  '【机位】平视中景。\n0—15秒：两人在工作室交接盒子。\n' +
                  '\n'.join('%s说：<Subject %d> (S%d) says:<d>[Chinese]%s</d>' % (name, n, n, quote)
                            for name, n, quote in spoken))
        return source, prompt

    def gate(self, source, prompt, *, flow='', stale=True, settings=None):
        problem = '画面稿里有 1 句台词，镜头行里一句都没有'
        row = dict(source_text=source, chars=['赵禾','钱川'], seconds=15, scene='工作室',
                   plan=dict(flow=flow), problems=[problem] if stale else [],
                   critical_problems=[problem] if stale else [])
        before = copy.deepcopy(row)
        result = segment_api._render_critical_problems(dict(settings=settings or {}), {}, row, prompt, 1, 1)
        self.assertEqual(row, before, 'A check must not rewrite project rows')
        return result

    def test_fixed_legacy_prompt_ignores_stale_critical_and_soft_lists(self):
        source, prompt = self.legacy()
        self.assertEqual(self.gate(source,prompt), [])

    def test_current_missing_line_is_still_rejected_even_when_cache_is_empty(self):
        source, prompt = self.legacy(spoken=[])
        self.assertTrue(self.gate(source,prompt,stale=False))

    def test_native_format_without_flow_metadata_does_not_use_old_shot_parser(self):
        case = next(case for case in matrix.CASES if case['key']=='three_people_script_lines')
        prompt,_ = matrix.compile_source_fallback(case,matrix.source_slice(case))
        for flow in ('', 'source-v1', 'old-profile'):
            with self.subTest(flow=flow):
                self.assertEqual(self.gate(case['text'],prompt,flow=flow), [])
                self.assertEqual(authoring.h3_problems(prompt,case['text'],matrix.cards_for(case)), [])
                self.assertEqual(authoring.h3_critical_problems(prompt,case['text'],
                    ['画面稿里有 3 句台词，镜头行里一句都没有'],dict(seconds=15),'工作室'), [])

    def test_repeated_words_belong_to_separate_speakers_in_source_order(self):
        source,prompt=self.legacy('赵禾：好了。\n钱川：好了。', [('赵禾',1,'好了。'),('钱川',2,'好了。')])
        self.assertEqual(self.gate(source,prompt), [])
        wrong=prompt.replace('钱川说：<Subject 2> (S2)','赵禾说：<Subject 1> (S1)')
        self.assertTrue(self.gate(source,wrong))

    def test_pov_camera_voice_does_not_borrow_the_visible_person(self):
        source='我：辛苦了。'
        _,prompt=self.legacy(source,[('我',3,'辛苦了。')])
        settings=dict(pov='第一视角POV',camera_owner='我')
        self.assertEqual(self.gate(source,prompt,settings=settings), [])
        self.assertTrue(self.gate(source,prompt.replace('<Subject 3> (S3)','<Subject 1> (S1)'),settings=settings))

    def test_missing_or_extra_legacy_lines_and_changed_words_do_not_pass(self):
        source='赵禾：收好了。\n钱川：谢谢。'
        for spoken in ([('赵禾',1,'收好了。')], [('赵禾',1,'收好啦。'),('钱川',2,'谢谢。')],
                       [('钱川',2,'谢谢。'),('赵禾',1,'收好了。')],
                       [('赵禾',1,'收好了。'),('钱川',2,'谢谢。'),('赵禾',1,'收好了。')]):
            with self.subTest(spoken=spoken):
                _,prompt=self.legacy(source,spoken)
                self.assertTrue(self.gate(source,prompt,stale=False))

    def test_missing_current_source_keeps_unresolved_block(self):
        _,prompt=self.legacy()
        self.assertTrue(self.gate('',prompt))


if __name__=='__main__':unittest.main()
