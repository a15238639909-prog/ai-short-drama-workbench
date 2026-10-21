"""Zero-model task-runtime tests; scratch media are retained, never deleted."""
from contextlib import ExitStack, nullcontext
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from cores import story_video_reference as reference
from cores import universal_writer as uw


def fixture(scene=True, guided=False):
    folder = Path(tempfile.mkdtemp(prefix='reference_runtime_zero_model_'))
    names = ('伊莱恩', '赛琳娜', '诺亚', '莉娅')
    cards = []
    for index, name in enumerate(names, 1):
        image = folder / ('uploaded_%d.png' % index)
        image.write_bytes(('portrait%d' % index).encode())
        cards.append(dict(id='char_%02d' % index, name=name, image_path=str(image),
            personality='好奇但留意同伴的回应', voice='清朗的少年男声' if index % 2 else '明亮自然的少女声'))
    scene_path = folder / 'uploaded_scene.png'
    scene_path.write_bytes(b'new fantasy scene')
    source = '纸上写着“返回”。\n莉娅说：“可以。”\n赛琳娜说：“好。”\n诺亚说：“明白。”\n伊莱恩说：“嗯。”'
    inputs = dict(input_mode='reference', flow_version=3, character_cards=cards,
        initial_scene=str(scene_path) if scene else '', story_input='story', story_direction=source,
        style='西方奇幻、14～15岁少年、真人写实电影质感', segment_count=2, segment_seconds=15,
        aspect_ratio='16:9', quality_mode='guided' if guided else 'automatic')
    task = dict(id='fixture_reference', kind='story_video', runtime_generation='initial',
        status='running', inputs=inputs, checkpoint=dict(story_video_folder=str(folder)))
    planned_cards = [dict(id=card['id'], name=card['name'], appearance='14～15岁少年，依照参考外貌', role='同行者')
                     for card in cards]
    beats = []
    for index, lines in enumerate(([(names[3], '可以。'), (names[1], '好。')],
                                  [(names[2], '明白。'), (names[0], '嗯。')]), 1):
        beats.append(dict(index=index, seconds=15, goal='理解并回应线索', action='自然观察与交流',
            turn='线索得到确认', emotion='专注', ending='保持自然互动', cast=[c['id'] for c in cards],
            dialogue=[dict(speaker=speaker, text=text) for speaker, text in lines]))
    plan = dict(title='本地契约样片', logline='测试素材不用于真实创作', ending='完成回应',
                props=[], invariants=[], characters=planned_cards, beats=beats)
    return folder, task, plan


class Handle:
    def __init__(self, task, stages):
        self.tid, self.task, self.stages = task['id'], task, stages

    def step(self, *args):
        self.stages.append(args)

    def set(self, **values):
        self.task.update(values)


class ReferenceRuntimeTests(unittest.TestCase):
    def _flow(self, scene=True, guided=False, quality_failure=None, pause_second=False):
        folder, task, plan = fixture(scene, guided)
        stages, requests, generated = [], [], []
        initial = [True]

        def request_json(request, directory, images=None, validator=None):
            system, payload, _ = request
            directory = Path(directory)
            requests.append(dict(path=str(directory), payload=copy.deepcopy(payload), images=images))
            stage = payload.get('stage')
            if stage == 'references':
                self.assertEqual(len(images), 5 if scene else 4)
                return dict(actual_visible='新上传的四位14～15岁少年及参考场景', characters=[],
                    scene='新西方奇幻场景', actions=[], completed=[], continuity_risks=[])
            if 'count' in payload and stage != 'review':
                self.assertIn(task['inputs']['story_direction'], payload['brief'])
                result = copy.deepcopy(plan)
                validator(result, payload)
                return result
            if stage == 'review':
                return dict(passed=False, issues=[dict(severity='advisory')])
            if 'dialogue_slots' in payload:
                if pause_second and initial[0] and payload['beat']['index'] == 2:
                    raise reference.core.ControlStop()
                self.assertNotIn('少年男声', str(payload))
                return dict(integrated_multimodal_description='[Shot 1] 中景展现四位14～15岁少年自然观察。前五秒查看线索，中间五秒交流回应。\n\n[Shot 2] At 00:10.000, 镜头切至侧前方近景，最后五秒呈现人物的选择和表情。',
                    overall_soundscape='自然风声与脚步声。', non_diegetic_music='N/A', brief='自然理解并回应。',
                    expected_end_state='四位少年继续自然互动。',
                    timeline=[dict(start=start, end=start + 5, action='自然交流', visible_result='人物反应可见') for start in (0, 5, 10)],
                    dialogue=[dict(speaker=line['speaker'], text=line['text'], start=.5 + index * 3,
                        end=2 + index * 3, delivery='自然回应') for index, line in enumerate(payload['dialogue_slots'])],
                    continuity_notes=[])
            if stage == 'storyboard_review':
                if quality_failure:
                    trace = directory / 'call_1_fixture' / 'text_fixture'
                    reference._save(trace / 'request.json', dict(request='same optional review'))
                    if quality_failure in ('length', 'empty'):
                        reference._save(trace / 'response.json', dict(text='{' if quality_failure == 'length' else '',
                            meta=dict(finish_reason='length' if quality_failure == 'length' else 'stop')))
                        raise RuntimeError(reference.EMPTY_OR_LENGTH)
                    raise RuntimeError('本地文字模型服务不可用')
                return dict(passed=True, issues=[], event_contract=[])
            if stage == 'segment_acceptance':
                return dict(passed=False, events=[], critical_issues=[])
            if stage in ('opening', 'segment'):
                return dict(actual_visible='实际画面 ' + str(directory), characters=[], scene='实际场景',
                            actions=['已观察的动作'], completed=[], continuity_risks=[])
            raise AssertionError((stage, list(payload)))

        def generate(mode, prompt, **options):
            reference.h3_client._http('POST', '/prompt', {})
            generated.append(dict(mode=mode, prompt=prompt, options=copy.deepcopy(options)))
            raw = folder / ('mock_native_%d.mp4' % len(generated))
            raw.write_bytes(prompt.encode('utf-8'))
            return dict(output_path=str(raw))

        def adopt(raw, directory, end_seconds):
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True)
            clip, anchor = directory / 'clip.mp4', directory / 'anchor.png'
            clip.write_bytes(Path(raw).read_bytes())
            anchor.write_bytes(str(directory).encode())
            return dict(raw_path=str(raw), clip_path=str(clip), anchor_path=str(anchor), kept_seconds=end_seconds,
                clip_sha256=reference._sha(clip), anchor_sha256=reference._sha(anchor))

        def samples(clip, directory, seconds):
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True)
            result = []
            for index in range(6):
                path = directory / ('sample_%d.png' % index)
                path.write_bytes(str(index).encode())
                result.append(dict(path=str(path), frame_index=index, time_seconds=index * 2.9))
            return result

        patches = [patch.object(reference.core.runtime, 'load', side_effect=lambda tid: task),
            patch.object(reference.core.runtime, 'TaskHandle', side_effect=lambda tid: Handle(task, stages)),
            patch.object(reference.runtime_reset, 'bind_thread'), patch.object(reference.runtime_reset, 'assert_current'),
            patch.object(reference.gpu_manager, 'assert_local_run_allowed'), patch.object(reference.gpu_manager, 'acquire'),
            patch.object(reference.client, 'preserve_release'),
            patch.object(reference.client, '_native_profile', side_effect=lambda *a: nullcontext()),
            patch.object(reference.core, 'request_json', side_effect=request_json),
            patch.object(reference.h3_client, '_http', return_value={}),
            patch.object(reference.h3_client, 'generate', side_effect=generate),
            patch.object(reference.media, 'adopt', side_effect=adopt), patch.object(reference.media, 'sample_frames', side_effect=samples),
            patch.object(reference.media, 'join', side_effect=lambda rows, directory: dict(output_path=str(directory / 'film.mp4'),
                seconds=sum(row['kept_seconds'] for row in rows))),
            patch.object(reference.asr, 'capability', return_value=dict(available=True)),
            patch.object(reference.asr, 'review', return_value=dict(status='failed', transcript='仅ASR未匹配，不阻断')),
            patch.object(reference.quality, 'segment_acceptance_request', side_effect=lambda *a, **kw:
                ('只记录实际证据', dict(stage='segment_acceptance'), {})),
            patch.object(reference.core, '_url', side_effect=lambda path: '/files/' + Path(path).name)]
        with ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            with patch.object(uw, 'plan') as plan_spy, patch.object(uw, 'write') as write_spy:
                if guided or pause_second:
                    with self.assertRaises(reference.core.ControlStop):
                        reference.run_job(task['id'])
                    first = reference._read(folder / 'manifest.json')
                    if guided:
                        self.assertEqual(first['decision'], 'story')
                        self.assertEqual(generated, [])
                        first.update(approved=True, approved_plan_signature=reference.core.plan_signature(first['plan']))
                        reference._save(folder / 'manifest.json', first)
                        task.update(status='running')
                    else:
                        self.assertEqual(len(first['segments']), 1)
                        before = copy.deepcopy(first['segments'][0])
                        initial[0] = False
                output = reference.run_job(task['id'])
                result = reference._read(folder / 'manifest.json')
                if pause_second:
                    self.assertEqual(result['segments'][0], before)
                plan_spy.assert_not_called()
                write_spy.assert_not_called()
        return folder, task, result, output, stages, requests, generated

    def test_fixed_source_four_teens_real_task_output_scene_removed_and_voice_bound(self):
        folder, task, result, output, stages, requests, generated = self._flow()
        self.assertEqual(result['source_story'], task['inputs']['story_direction'])
        self.assertEqual((folder / 'story.txt').read_bytes(), task['inputs']['story_direction'].encode('utf-8'))
        self.assertEqual(len(generated), 2)
        self.assertEqual(output['seconds'], 30)
        self.assertEqual(result['output'], output)
        self.assertGreater(len(stages), 0)
        self.assertNotIn('quality_passed', output)
        first, second = [reference._read(Path(row['prompt_path'])) for row in result['segments']]
        self.assertIn('是场景参考', first['prompt'])
        self.assertNotIn('是场景参考', second['prompt'])
        self.assertIn('真实最后一帧', second['prompt'])
        self.assertIn('14～15岁', second['prompt'])
        self.assertNotIn('real adult actors', second['prompt'])
        self.assertEqual(first['voice_binding_audit'][0]['character_id'], 'char_04')
        self.assertEqual(first['voice_binding_audit'][0]['speaker_id'], 'S1')
        second_refs = reference._read(Path(result['segments'][1]['prompt_path']).with_name('inputs.json'))['references']
        self.assertEqual(second_refs[-1]['path'], result['segments'][0]['anchor_path'])
        self.assertEqual(second_refs[-1]['picture'], 5)
        self.assertEqual([row['asr']['status'] for row in result['segments']], ['failed', 'failed'])

    def test_scene_optional_and_guided_story_approval_uses_existing_signature(self):
        folder, task, result, output, stages, requests, generated = self._flow(scene=False, guided=True)
        self.assertEqual(len(generated[0]['options']['images']), 4)
        self.assertEqual(len(generated[1]['options']['images']), 5)
        self.assertTrue(result['approved'])
        self.assertEqual(output['seconds'], 30)

    def test_pause_resume_reuses_first_video_source_and_plan(self):
        folder, task, result, output, stages, requests, generated = self._flow(pause_second=True)
        self.assertEqual(len(generated), 2)
        self.assertEqual(len([row for row in requests if row['payload'].get('stage') == 'references']), 1)
        self.assertEqual(len([row for row in requests if 'count' in row['payload']
                              and row['payload'].get('stage') != 'review']), 1)

    def test_optional_empty_and_length_reports_are_unavailable_without_rerolls(self):
        for failure in ('length', 'empty'):
            with self.subTest(failure=failure):
                folder, task, result, output, stages, requests, generated = self._flow(quality_failure=failure)
                self.assertEqual(len(generated), 2)
                self.assertTrue(all(row['storyboard']['status'] == 'unavailable' for row in result['segments']))
                self.assertTrue(all('passed' not in row['storyboard'] for row in result['segments']))

    def test_service_unavailable_is_not_swallowed_as_optional_quality(self):
        with self.assertRaisesRegex(RuntimeError, '服务不可用'):
            self._flow(quality_failure='network')

    def test_only_latest_native_response_can_prove_optional_truncation(self):
        folder = Path(tempfile.mkdtemp(prefix='reference_quality_trace_'))
        old = folder / 'call_1_old' / 'text_old'
        reference._save(old / 'request.json', {})
        reference._save(old / 'response.json', dict(text='{', meta=dict(finish_reason='length')))
        self.assertIsNotNone(reference._latest_failed_quality_response(folder))
        new = folder / 'call_1_new' / 'text_new'
        reference._save(new / 'request.json', {})
        later = old.joinpath('request.json').stat().st_mtime_ns + 1000000
        os.utime(new / 'request.json', ns=(later, later))
        self.assertIsNone(reference._latest_failed_quality_response(folder))

    def test_idea_mode_uses_formal_uw_once_and_preserves_writer_settings(self):
        folder, task, _ = fixture()
        task['inputs']['story_input'] = 'idea'
        stages = []
        manifest = dict(actual_model_calls=0, reference_policy=dict(max_text_vision_calls=36))
        session = reference._Session(task['id'], Handle(task, stages), folder, task['inputs'], manifest)
        cards = reference.characters.normalize_cards(task['inputs']['character_cards'])
        source = '莉娅说：“可以。”'

        def author_plan(one, settings, n_eps, model):
            self.assertEqual(n_eps, 1)
            self.assertIn(cards[0]['personality'], str(settings))
            self.assertNotIn(cards[0]['voice'], str(settings))
            model('正式完整故事模板', '创作输入', mt=6400, temperature=.7, reasoning=True)
            return dict(problems=[], n=1, rows=[dict(no=1, one_line='本地情节', events=['本地情节'], landing='本地结尾')],
                design=dict(synopsis='本地梗概', people=[]))

        def author_write(settings, ep, previous, model):
            self.assertEqual(os.environ['V41_PROSE_TEMPLATE'], 'B')
            self.assertEqual(os.environ['V41_MODEL_REVIEW'], '1')
            actual = uw.writing_input(settings, ep=1)
            self.assertIn(cards[0]['personality'], str(actual))
            model('正式小说正文B模板', '正文输入', mt=9000, temperature=.75, reasoning=True)
            return dict(prose=source, review=dict(verdict='pass'))

        with patch.object(session, 'guard'), patch.object(reference.client, 'preserve_release'), \
             patch.object(reference.qwen_client, 'chat', return_value=('本地原答', dict(finish_reason='stop'))) as chat, \
             patch.object(uw, 'plan', side_effect=author_plan) as plan, \
             patch.object(uw, 'write', side_effect=author_write) as write:
            with session.models():
                result = reference._story(session, cards, dict(actual_visible='新素材', characters=[], scene='奇幻场景'))
        self.assertEqual(result, source)
        self.assertEqual(plan.call_count, 1)
        self.assertEqual(write.call_count, 1)
        self.assertEqual(manifest['actual_model_calls'], 2)
        self.assertEqual(chat.call_args.kwargs['temperature'], .75)
        self.assertEqual(chat.call_args.kwargs['max_tokens'], 9000)
        self.assertTrue(chat.call_args.kwargs['reasoning'])

    def test_persisted_h3_submission_does_not_resubmit_after_lost_result(self):
        folder, task, _ = fixture()
        manifest = dict(seeds=[11, 12])
        session = reference._Session(task['id'], Handle(task, []), folder, task['inputs'], manifest)
        work = folder / 'pending'
        reference._save(work / 'video_submission.json', dict(input_signature='fixed', submissions=1))
        def resubmit(*args, **kwargs):
            reference.h3_client._http('POST', '/prompt', {})
        with patch.object(session, 'guard'), patch.object(reference.gpu_manager, 'acquire'), \
             patch.object(reference.client, 'preserve_release'), \
             patch.object(reference.client, '_native_profile', side_effect=lambda *a: nullcontext()), \
             patch.object(reference.h3_client, 'generate', side_effect=resubmit), \
             patch.object(reference.h3_client, '_http') as http:
            with self.assertRaisesRegex(RuntimeError, '禁止自动再次生成'):
                reference._generate_once(session, work, dict(prompt='中文正文'), [],
                    dict(width=1024, height=576, ratio='16:9'), 1, 1, 'fixed')
            http.assert_not_called()

    def test_unavailable_quality_marker_is_reused_without_model_call(self):
        folder, task, _ = fixture()
        session = reference._Session(task['id'], Handle(task, []), folder, task['inputs'], {})
        request = ('可选审稿', dict(stage='review'), {})
        review_folder = folder / 'optional_review'
        def failed(request, directory, images=None, validator=None):
            native = Path(directory) / 'call_1_fixture' / 'text_fixture'
            reference._save(native / 'request.json', {})
            reference._save(native / 'response.json', dict(text='{', meta=dict(finish_reason='length')))
            raise RuntimeError(reference.EMPTY_OR_LENGTH)
        with patch.object(session, 'guard'), patch.object(reference.core, 'request_json', side_effect=failed) as call:
            first = session.review(request, review_folder)
            second = session.review(request, review_folder)
        self.assertEqual(first, second)
        self.assertEqual(first['status'], 'unavailable')
        self.assertNotIn('passed', first)
        self.assertNotIn('event_contract', first)
        self.assertEqual(call.call_count, 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
