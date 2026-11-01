"""New-draft internal continuity, with fake Qwen only and no project writes."""
from contextlib import ExitStack
import copy
import json
import os
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from cores import manual_story, oral_story, source_continuity_review as continuity
from cores import universal_writer as writer


PARTS = ['岑月将玉匣递给顾霄，顾霄双手接稳。',
         '顾霄把玉匣安放在仙舟上，驾舟飞入云层。',
         '岑月抱紧胸前的玉匣，看向陆衡。']
PROSE = '\n\n'.join(PARTS)
CLEAR = {'verdict': 'clear', 'issues': [], 'summary': '未发现有前后证据的内部硬矛盾'}


def issue(before=PARTS[1], after=PARTS[2], entity='玉匣', kind='prop_reversion'):
    return dict(kind=kind, entity=entity, certainty='explicit',
                before=dict(paragraph_id=2, quote=before), after=dict(paragraph_id=3, quote=after),
                detail='玉匣已随顾霄离开，后文未交还却重新由岑月抱着')


def finding(item=None):
    return {'verdict': 'issues', 'issues': [item or issue()], 'summary': '前后持有人矛盾'}


def encoded(value):
    return json.dumps(value, ensure_ascii=False)


class SourceContinuityReviewTests(unittest.TestCase):
    def write(self):
        return writer.write({'story_mode': 'single', 'one_line': '初始旧目标：去集市。'},
                            brief='初始旧目标：去集市。')

    def test_clear_check_never_receives_initial_idea_targets_or_style(self):
        with patch.object(writer, 'call', side_effect=[PROSE, encoded(CLEAR)]) as call, \
             patch.object(writer, 'review_prose', side_effect=AssertionError('legacy idea review forbidden')), \
             patch.object(writer, 'merge_consistency', side_effect=AssertionError('legacy target veto forbidden')):
            result = self.write()
        self.assertEqual(result['prose'], PROSE)
        self.assertFalse(result['review']['production_blocked'])
        self.assertEqual(result['review']['internal_continuity']['status'], 'clear')
        self.assertEqual(set(json.loads(call.call_args_list[1].args[1])), {'新正文编号段落'})
        self.assertNotIn('初始旧目标', call.call_args_list[1].args[1])

    def test_real_quoted_prop_conflict_one_local_revision_and_recheck(self):
        fixed = '岑月松开空着的双手，看向陆衡。'
        patch_reply = {'patches': [{'paragraph_id': 3, 'text': fixed}]}
        with patch.object(writer, 'call', side_effect=[PROSE, encoded(finding()), encoded(patch_reply), encoded(CLEAR)]) as call:
            result = self.write()
        self.assertEqual(call.call_count, 4)
        self.assertEqual(result['prose'], '\n\n'.join(PARTS[:2] + [fixed]))
        self.assertEqual(result['revision']['original'], PROSE)
        self.assertEqual(result['revision']['paragraphs'], [3])
        self.assertEqual(result['review']['verdict'], 'accepted')
        self.assertFalse(result['review']['production_blocked'])
        revision_task = json.loads(call.call_args_list[2].args[1])
        self.assertEqual(revision_task['允许修改的后文段号'], [3])
        self.assertNotIn('故事依据', revision_task)

    def test_invented_citation_is_retained_as_discarded_not_a_veto(self):
        with patch.object(writer, 'call', side_effect=[PROSE, encoded(finding(issue(after='岑月把玉匣重新收进背包。')))]) as call:
            result = self.write()
        self.assertEqual(call.call_count, 2)
        self.assertNotIn('revision', result)
        self.assertEqual(result['prose'], PROSE)
        self.assertFalse(result['review']['production_blocked'])
        review = result['review']['internal_continuity']
        self.assertEqual(review['status'], 'unsupported_ignored')
        self.assertEqual(len(review['discarded_findings']), 1)

    def test_stylistic_opinion_without_citations_cannot_rewrite_source(self):
        opinion = {'kind': 'style', 'detail': '应该加一场打斗才好看'}
        review = continuity.validate_findings({'verdict': 'issues', 'issues': [opinion]}, PROSE)
        self.assertEqual(review['status'], 'unsupported_ignored')
        self.assertFalse(review['issues'])

    def test_quoted_evidence_must_share_entity_and_follow_chronology(self):
        for bad in (issue(entity='铜钥匙'),
                    dict(issue(), before={'paragraph_id': 3, 'quote': PARTS[2]},
                         after={'paragraph_id': 2, 'quote': PARTS[1]})):
            with self.subTest(bad=bad):
                review = continuity.validate_findings(finding(bad), PROSE)
                self.assertEqual(review['status'], 'unsupported_ignored')
        contradictory_protocol = dict(finding(), verdict='clear')
        self.assertEqual(continuity.validate_findings(contradictory_protocol, PROSE)['status'], 'unknown')

    def test_all_four_supported_families_require_real_source_quotes(self):
        cases = [
            ('prop_reversion', '玉匣', PARTS[0], PARTS[2]),
            ('wardrobe_reversion', '玄青外袍', '岑月脱下玄青外袍，裹住玉匣。', '岑月仍穿着玄青外袍站在台边。'),
            ('departed_character', '顾霄', '顾霄驾舟驶入云海，离开渡台。', '顾霄仍站在原地看着岑月。'),
            ('repeated_event', '陆衡', '陆衡第一次把铜钥匙交给岑月。', '陆衡第一次把同一铜钥匙交给岑月。'),
        ]
        for kind, entity, before, after in cases:
            with self.subTest(kind=kind):
                prose = before + '\n\n雨声不停。\n\n' + after
                item = issue(before=before, after=after, entity=entity, kind=kind)
                item['before']['paragraph_id'] = 1
                review = continuity.validate_findings(finding(item), prose)
                self.assertEqual(review['status'], 'confirmed_issues')

    def test_still_wrong_after_one_revision_remains_blocked(self):
        unchanged = {'patches': [{'paragraph_id': 3, 'text': PARTS[2]}]}
        with patch.object(writer, 'call', side_effect=[PROSE, encoded(finding()), encoded(unchanged), encoded(finding())]) as call:
            result = self.write()
        self.assertEqual(call.call_count, 4)
        self.assertTrue(result['review']['production_blocked'])
        self.assertEqual(result['review']['verdict'], 'fail')
        self.assertEqual(result['prose'], PROSE)

    def test_revision_cannot_edit_unrelated_earlier_paragraph(self):
        patch_reply = {'patches': [{'paragraph_id': 1, 'text': '替换整个前情。'}]}
        with patch.object(writer, 'call', side_effect=[PROSE, encoded(finding()), encoded(patch_reply)]) as call:
            result = self.write()
        self.assertEqual(call.call_count, 3)
        self.assertEqual(result['prose'], PROSE)
        self.assertTrue(result['review']['production_blocked'])
        self.assertIn('越过', result['revision_error'])

    def test_unknown_request_failure_is_not_a_fact_error_or_a_pass(self):
        with patch.object(writer, 'call', side_effect=[PROSE, RuntimeError('local checker unavailable')]) as call:
            result = self.write()
        self.assertEqual(call.call_count, 2)
        self.assertEqual(result['review']['verdict'], 'unknown')
        self.assertEqual(result['review']['internal_continuity']['status'], 'unavailable')
        self.assertTrue(result['review']['production_blocked'])

    def test_incomplete_source_generation_stays_a_blocked_unknown_draft(self):
        error = RuntimeError('source output truncated')
        error.partial_text = '岑月把玉匣交给'
        with patch.object(writer, 'call', side_effect=error) as call:
            result = self.write()
        self.assertEqual(call.call_count, 1)
        self.assertEqual(result['prose'], error.partial_text)
        self.assertEqual(result['review']['verdict'], 'unknown')
        self.assertTrue(result['review']['production_blocked'])
        self.assertEqual(result['review']['internal_continuity']['reason'], 'source_generation_incomplete')

    def test_known_conflict_cannot_be_cleared_by_fake_recheck_quotes(self):
        patch_reply = {'patches': [{'paragraph_id': 3, 'text': PARTS[2]}]}
        fake = finding(issue(after='原文不存在的回流动作。'))
        with patch.object(writer, 'call', side_effect=[PROSE, encoded(finding()), encoded(patch_reply), encoded(fake)]):
            result = self.write()
        self.assertTrue(result['review']['production_blocked'])
        self.assertEqual(result['review']['verdict'], 'unknown')
        self.assertTrue(result['review']['unresolved_prior_issues'])

    def test_manual_failed_new_draft_preserves_old_prose_and_original_attempt(self):
        episode = {'no': 1, 'status': 'PLANNED', 'brief': '本话自拟剧情。',
                   'prose': '用户已存旧稿。', 'prose_approved_sig': 'keep-old-approval'}
        saga = {'episodes': [episode]}
        story = {'settings': {'story_mode': 'single', 'writer_table': False}, 'saga': saga}
        unchanged = {'patches': [{'paragraph_id': 3, 'text': PARTS[2]}]}
        with patch.object(manual_story.story_core, 'get_story', return_value=story), \
             patch.object(manual_story.saga_core, 'get_saga', return_value=saga), \
             patch.object(manual_story.saga_core, 'save_saga') as save, \
             patch.object(oral_story, 'plan_episode', return_value={'mode': 'idea', 'context': [], 'events': []}), \
             patch('cores.asset_core.list_assets', return_value=[]), \
             patch.object(writer, 'call', side_effect=[PROSE, encoded(finding()), encoded(unchanged), encoded(finding())]), \
             self.assertRaises(writer.DraftNotReady):
            manual_story.write('SYNTHETIC_ONLY', 1)
        self.assertEqual(episode['prose'], '用户已存旧稿。')
        self.assertEqual(episode['prose_approved_sig'], 'keep-old-approval')
        self.assertEqual(episode['prose_draft'], PROSE)
        self.assertTrue(episode['writing_attempt']['production_blocked'])
        self.assertEqual(episode['writing_attempt']['revision']['original'], PROSE)
        self.assertEqual(save.call_count, 1)

    def test_approving_old_saved_source_never_invokes_new_draft_check(self):
        episode = {'no': 1, 'brief': '旧构想。', 'prose': PROSE}
        with patch.object(manual_story.saga_core, 'get_saga', return_value={'episodes': [episode]}), \
             patch.object(manual_story.saga_core, 'save_saga'), \
             patch.object(continuity, 'check_prose', side_effect=AssertionError('do not recheck old source')):
            manual_story.approve('SYNTHETIC_ONLY', 1)
        self.assertEqual(episode['prose'], PROSE)


if __name__ == '__main__':
    unittest.main()
