"""Additional generation-edge audit using normal fixtures; no real model/media."""
import copy
import os
import unittest

os.environ['V41_NO_MODEL'] = '1'
from api import segment_api
from tests import test_before_submit_text_repair as repair_fixture


class FullAuditGenerationEdgesTests(unittest.TestCase):
    def setUp(self):
        self.case = repair_fixture.BeforeSubmitTextRepairTests(methodName='runTest')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_successful_target_repair_clears_its_timeline_failure_only(self):
        case = self.case
        case.timeline['prompt_failures'] = {'2': '旧漏句提醒', '3': '另一个待补段'}
        self.assertEqual(case.prepare()[-1], [])
        self.assertEqual(case.timeline['prompt_failures'], {'3': '另一个待补段'})
        self.assertFalse(case.row().get('prompt_repair_pending'))

    def test_duration_edit_during_slow_repair_is_not_overwritten(self):
        case = self.case
        proposal = copy.deepcopy(case.writer.return_value)
        def slow_writer(*args, **kwargs):
            case.row()['seconds'] = 12
            return proposal
        case.writer.side_effect = slow_writer
        problems = case.prepare()[-1]
        self.assertEqual(case.row()['seconds'], 12)
        self.assertTrue(problems)
        self.assertEqual(case.row()['prompt'], case.bad)

    def test_visible_cast_edit_during_slow_repair_is_not_overwritten(self):
        case = self.case
        proposal = copy.deepcopy(case.writer.return_value)
        def slow_writer(*args, **kwargs):
            case.row()['chars'] = ['阿宁', '成年顾客']
            return proposal
        case.writer.side_effect = slow_writer
        problems = case.prepare()[-1]
        self.assertEqual(case.row()['chars'], ['阿宁', '成年顾客'])
        self.assertTrue(problems)

    def test_valid_legacy_pov_transport_is_not_misclassified_by_old_plan_flow(self):
        case = self.case
        row = copy.deepcopy(case.row())
        row.update(prompt=case.good, plan=dict(flow='source-v1'), prompt_edited=True)
        self.assertEqual(segment_api._render_critical_problems(case.story, case.timeline,
            row, case.good, 1, 2), [])


if __name__ == '__main__':
    unittest.main()
