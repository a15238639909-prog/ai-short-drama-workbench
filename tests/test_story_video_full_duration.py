"""Full-duration planning and adoption; all model calls mocked, fixtures retained."""
import copy
import json
import unittest

from cores import story_video_core as core, story_video_prompts as prompts, story_video_media as media
from tests.test_story_video_core import plan_fixture, written_fixture
from tests.test_story_video_staged import StagedFlowTests


def full_written(payload):
    result = written_fixture(payload)
    result['timeline'] = [dict(start=phase['start'], end=phase['end'],
        action='continuous purposeful action', visible_result='visible state changes')
        for phase in payload['timing_budget']['motion_phases']]
    result['integrated_multimodal_description'] = (
        '[Shot 1] Cinematic 3D, the adult woman begins turning the ring during the first five seconds. '
        'Over the next five seconds she guides flowing water into the channel. '
        'During the final five seconds she follows the spreading flow and steps toward its outlet, '
        'still responding naturally as the segment ends.')
    return result


class FullDurationContractTests(unittest.TestCase):
    def request(self):
        plan = plan_fixture()
        for beat in plan['beats']:
            beat['seconds'] = 15
        return prompts.write_request(plan, plan['beats'][0], None, 15, 't2va')

    def test_phases_cover_fifteen_seconds_and_reach_writer(self):
        _, planned, _ = prompts.plan_request('女修疏通山泉', '3D film', 2, 15)
        _, written, schema = self.request()
        phases = written['timing_budget']['motion_phases']
        self.assertEqual([(p['start'], p['end']) for p in phases], [(0, 5), (5, 10), (10, 15)])
        self.assertEqual(planned['timing_budget']['motion_phases'], phases)
        self.assertEqual(schema['properties']['timeline']['minItems'], 3)

    def test_incomplete_or_gapped_long_timeline_is_rejected(self):
        _, payload, schema = self.request()
        valid = full_written(payload)
        core.validate_schema(valid, schema)
        core.validate_written(valid, payload)
        for defect in ('gap', 'late_start', 'early_end'):
            changed = copy.deepcopy(valid)
            if defect == 'gap':
                changed['timeline'][1]['start'] = 5.5
            elif defect == 'late_start':
                changed['timeline'][0]['start'] = .5
            else:
                changed['timeline'][-1]['end'] = 12
            with self.subTest(defect=defect), self.assertRaisesRegex(ValueError, '动作时间轴'):
                core.validate_written(changed, payload)


class FullDurationFlowTests(StagedFlowTests):
    def test_short_cut_suggestion_and_warning_never_trim_or_regenerate(self):
        self.stage_inputs()
        self.task['inputs']['segment_seconds'] = 15
        previous_text = self.text.side_effect
        previous_vision = self.vision.side_effect
        previous_adopt = media.adopt.side_effect

        def text(system, payload, *args, **kwargs):
            if payload.get('stage') == 'review':
                return previous_text(system, payload, *args, **kwargs)
            if 'count' in payload:
                plan = plan_fixture()
                for beat in plan['beats']:
                    beat['seconds'] = payload['seconds']
                return json.dumps(plan)
            if 'story_plan' in payload:
                self.writer_states.append(copy.deepcopy(payload))
                return json.dumps(full_written(payload))
            return previous_text(system, payload, *args, **kwargs)

        def vision(system, payload, *args, **kwargs):
            value = json.loads(previous_vision(system, payload, *args, **kwargs))
            if payload['stage'] == 'segment':
                self.assertEqual(payload['retention_policy'], 'full_duration')
                value.update(keep_end_seconds=2, retry_required=True, retry_reason='Tail may be idle')
            return json.dumps(value)

        def adopt(source, folder, **kwargs):
            result = previous_adopt(source, folder, **kwargs)
            result.update(duration=kwargs['end_seconds'], kept_seconds=kwargs['end_seconds'])
            return result

        self.text.side_effect = text
        self.vision.side_effect = vision
        media.adopt.side_effect = adopt
        with self.assertRaises(core.ControlStop):
            core.run_job(self.task['id'])
        self.assertEqual(len(self.video_calls), 1)
        first = self.manifest()['segments'][0]
        self.assertEqual(first['kept_seconds'], 15)
        self.assertEqual(first['retention_policy'], 'full_duration')
        self.approve()
        core.run_job(self.task['id'])
        self.assertEqual(len(self.video_calls), 2, 'warnings must never generate extra takes')
        self.assertEqual([c.kwargs['end_seconds'] for c in media.adopt.call_args_list], [15, 15])
        self.assertEqual(self.video_calls[1]['first_frame'], first['anchor_path'])
        self.assertEqual([r['kept_seconds'] for r in self.join.call_args.args[0]], [15, 15])
