"""Final H3 transport uses visible facts; model/media calls stay mocked."""
import copy
import os
import re
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from api import segment_api as api
from cores import video_continuity as vc, observed_identity_continuity as identity
from tests import test_workbench_reference_transport as transport_cases
_REAL_SCOPE = vc.scope_prompt


class PositiveTransportTests(unittest.TestCase):
    def test_final_scope_preserves_source_words_and_excludes_internal_rules(self):
        original = transport_cases.WorkbenchReferenceTransportTests.standard_prompt().replace(
            '<Subject 1> (S1) holds the repaired watch.',
            '<Subject 1> (S1) holds the repaired watch. <d>[Chinese] 不要动。</d>')
        state = 'Alice站在柜台旁，右手托着手表。'
        scoped = vc.scope_prompt(original, {'plan': {'flow': 'source-v1'}}, {'start_state': state})
        self.assertIn(state, scoped)
        self.assertEqual(scoped.count('<d>[Chinese] 不要动。</d>'), 1)
        for token in ('Reference scope (', 'Declared shot context:', 'Wardrobe continuity (', 'do not'):
            self.assertNotIn(token, scoped)

    def test_identity_rules_stay_available_to_backend_not_final_model_text(self):
        original = transport_cases.WorkbenchReferenceTransportTests.standard_prompt()
        fields = {key: value for key, value in re.findall(
            r'(?ms)^(subject_definitions|summary|retention_analysis|detailed_description|overall_soundscape|non_diegetic_music):\n(.*?)(?=\n\n\w+:\n|\Z)', original)}
        result = identity.apply_compiled_authority({'fields': fields})
        self.assertIn('身份与外观权威', identity.RULES)
        self.assertNotIn('Identity authority (', result['prompt'])
        self.assertNotIn('actual_state', result['prompt'])

    def test_real_submission_keeps_internal_scope_in_snapshot_and_dialogue_verbatim(self):
        helper = transport_cases.WorkbenchReferenceTransportTests(methodName='runTest')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        case = helper.case
        original = helper.standard_prompt().replace('<Subject 1> (S1) holds the repaired watch.',
            '<Subject 1> (S1) holds the repaired watch. <d>[Chinese] 不要动。</d>')
        helper.select(['Alice', 'Hall'], original)
        case.current()['plan']['prompt_content_policy'] = 'positive-visual-v1'
        previous, _ = case.previous_fixture()
        case.scope.side_effect = _REAL_SCOPE
        with patch.object(vc, 'previous_segment', return_value=previous):
            rendered = case.render()
        request = case.current()['render_request']
        final = rendered['h3_prompt_sent']
        self.assertEqual(final, request['final_prompt'])
        self.assertEqual(request['reference_scope_version'], vc.REFERENCE_SCOPE_VERSION)
        self.assertEqual(request['prompt_content_policy'], 'positive-visual-v1')
        self.assertEqual(request['handoff_context']['previous_segment'], previous['segment_no'])
        self.assertEqual(final.count('<d>[Chinese] 不要动。</d>'), 1)
        self.assertEqual(case.current()['prompt'], original)
        for token in ('Reference scope (', 'Continuity handoff:', 'Declared shot context:',
                      'Current wardrobe contract:', 'Wardrobe continuity ('):
            self.assertNotIn(token, final)

    def test_legacy_view_uses_positive_fixed_templates_without_losing_dialogue(self):
        body = ('本段场景：安静的店里。\n人物站位：阿宁站在柜台旁。\n'
                '镜头1（0–8秒，中景）：阿宁（平静）说：「不要动。」\n环境音：钟表滴答。')
        final = api.h3_view(body, 8, [('阿宁', 'char'), ('店里', 'scene')], {'阿宁': '平稳的成年女声'},
            style='Krea2·厚涂插画')
        self.assertEqual(final.count('「不要动。」'), 1)
        visual = final.replace('「不要动。」', '')
        for token in ('不许', '绝不', '禁止', '不能', '不是', '没有配乐'):
            self.assertNotIn(token, visual)
        self.assertIn('二维绘制', final)
        self.assertIn('色块和笔触', final)


if __name__ == '__main__':
    unittest.main()
