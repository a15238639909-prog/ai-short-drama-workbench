"""Reference continuation regression checks. All model/media work is mocked."""
from contextlib import ExitStack, nullcontext
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from cores import story_video_reference as reference
try:
    from tests.test_story_video_reference_runtime import fixture, Handle
except ImportError:
    from test_story_video_reference_runtime import fixture, Handle


class ReferenceContinueTests(unittest.TestCase):
    def _flow(self, unavailable=(), asr_missing=False, audio=None, revision_failure=False,
              storyboard_failed=False, pause=False, mutate=None, failure=None, revision_binding_failure=False,
              mutate_plan=None, source_text=None):
        folder, task, plan = fixture()
        if source_text is not None:
            task['inputs']['story_direction'] = source_text
        stages, requests, generated, joined = [], [], [], []
        paused = [False]
        self.artifacts = dict(folder=folder, generated=generated, joined=joined, task=task)

        def request_json(request, directory, images=None, validator=None):
            system, payload, _ = request
            directory, stage = Path(directory), payload.get('stage')
            requests.append(dict(path=str(directory), payload=copy.deepcopy(payload), images=copy.deepcopy(images)))
            if failure and stage == failure[0]:
                raise failure[1]
            if stage in unavailable or revision_failure and directory.name == 'writing_revision_1':
                raise RuntimeError('本地模型格式修复仍未通过：测试报告字段不符')
            if stage == 'references':
                return dict(actual_visible='参考素材事实', characters=[], scene='参考场景',
                    actions=[], completed=[], continuity_risks=[])
            if 'count' in payload and stage != 'review':
                result = copy.deepcopy(plan)
                if mutate_plan:
                    mutate_plan(result)
                for attempt in range(2):
                    call = directory / ('call_%d_fixture' % (attempt + 1))
                    trace = call / 'text_fixture'
                    reference._save(trace / 'request.json', dict(system=system, payload=payload))
                    reference._save(trace / 'response.json', dict(text=json.dumps(result, ensure_ascii=False),
                        meta=dict(finish_reason='stop')))
                    try:
                        validator(result, payload)
                        break
                    except ValueError as error:
                        reference._save(call / 'contract_error.json', dict(error=str(error)))
                        if attempt:
                            raise RuntimeError('本地模型格式修复仍未通过：' + str(error)) from error
                return result
            if stage == 'review':
                return dict(passed=False, issues=[dict(severity='advisory', reason='普通节奏意见')])
            if 'dialogue_slots' in payload:
                if pause and not paused[0] and payload['beat']['index'] == 2:
                    paused[0] = True
                    raise reference.core.ControlStop()
                result = dict(integrated_multimodal_description='[Shot 1] 中景观察同行者，人物自然交流。\n\n[Shot 2] At 00:10.000, 侧前方近景呈现反应。',
                    overall_soundscape='风声与脚步声。', non_diegetic_music='N/A', brief='自然交流。',
                    expected_end_state='同行者保持自然互动。',
                    timeline=[dict(start=start, end=start + 5, action='自然交流', visible_result='人物反应可见')
                              for start in (0, 5, 10)],
                    dialogue=[dict(speaker=line['speaker'], text=line['text'], start=.5 + index * 3,
                        end=2 + index * 3, delivery='自然回应') for index, line in enumerate(payload['dialogue_slots'])],
                    continuity_notes=[])
                if revision_binding_failure and directory.name == 'writing_revision_1':
                    result['dialogue'][0]['delivery'] = '<Subject 2>，自然回应'
                    try:
                        validator(result, payload)
                    except ValueError as error:
                        raise RuntimeError('本地模型格式修复仍未通过：' + str(error)) from error
                    self.fail('不同说话者的Subject应在导演validator内拒绝')
                return result
            if stage == 'storyboard_review':
                return dict(passed=not (storyboard_failed or revision_failure),
                    issues=[dict(severity='blocking', reason='测试审稿意见')] if storyboard_failed or revision_failure else [],
                    event_contract=[])
            if stage in ('segment', 'opening'):
                return dict(actual_visible='实际画面', characters=[], scene='实际场景',
                    actions=['观察到的动作'], completed=[], continuity_risks=[])
            raise AssertionError((stage, directory))

        def generate(mode, prompt, **options):
            reference.h3_client._http('POST', '/prompt', {})
            generated.append(dict(mode=mode, prompt=prompt, options=copy.deepcopy(options)))
            raw = folder / ('native_%d.mp4' % len(generated))
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

        def join(rows, directory):
            joined.append(copy.deepcopy(rows))
            return dict(output_path=str(directory / 'film.mp4'), seconds=sum(row['kept_seconds'] for row in rows))

        patches = [patch.object(reference.core.runtime, 'load', side_effect=lambda tid: task),
            patch.object(reference.core.runtime, 'TaskHandle', side_effect=lambda tid: Handle(task, stages)),
            patch.object(reference.runtime_reset, 'bind_thread'), patch.object(reference.runtime_reset, 'assert_current'),
            patch.object(reference.gpu_manager, 'assert_local_run_allowed'), patch.object(reference.gpu_manager, 'acquire'),
            patch.object(reference.client, 'preserve_release'),
            patch.object(reference.client, '_native_profile', side_effect=lambda *a: nullcontext()),
            patch.object(reference.core, 'request_json', side_effect=request_json),
            patch.object(reference.h3_client, '_http', return_value={}),
            patch.object(reference.h3_client, 'generate', side_effect=generate),
            patch.object(reference.media, 'adopt', side_effect=adopt),
            patch.object(reference.media, 'sample_frames', side_effect=samples), patch.object(reference.media, 'join', side_effect=join),
            patch.object(reference.asr, 'capability', return_value=dict(available=not asr_missing, reason='测试ASR能力')),
            patch.object(reference.asr, 'review', return_value=audio or dict(status='passed', transcript='测试逐句识别')),
            patch.object(reference.core, '_url', side_effect=lambda path: '/files/' + Path(path).name)]
        with ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            if pause:
                with self.assertRaises(reference.core.ControlStop):
                    reference.run_job(task['id'])
                first = reference._read(folder / 'manifest.json')
                self.assertEqual(len(first['segments']), 1)
                previous = copy.deepcopy(first['segments'][0])
                if mutate:
                    mutate(folder, task, first)
            output = reference.run_job(task['id'])
            manifest = reference._read(folder / 'manifest.json')
            if pause:
                self.assertEqual(manifest['segments'][0], previous)
        return folder, task, manifest, output, requests, generated, joined

    def test_missing_asr_capability_and_unavailable_recognition_finish_without_rerolls(self):
        result = self._flow(asr_missing=True, audio=dict(status='unavailable', issues=[
            dict(code='asr_unavailable', reason='未安装测试ASR')]))
        _, _, manifest, output, _, generated, joined = result
        self.assertEqual(len(generated), 2)
        self.assertEqual(len(joined), 1)
        self.assertEqual(output['seconds'], 30)
        self.assertEqual(output['quality_status'], 'completed_with_warnings')
        self.assertTrue(output['generation_completed'])
        self.assertNotIn('quality_passed', output)
        self.assertFalse(manifest['quality_summary']['quality_gate'])
        self.assertTrue(all(row['asr']['status'] == 'unavailable' for row in manifest['segments']))

    def test_reference_and_actual_observations_fail_unknown_but_exact_tail_and_cards_continue(self):
        folder, task, manifest, output, requests, generated, joined = self._flow(
            unavailable=('references', 'segment', 'opening'))
        self.assertEqual(manifest['opening_observation']['characters'], [])
        for row in manifest['segments']:
            self.assertEqual(row['observation']['beat_status'], 'unknown')
            self.assertEqual(row['observation']['completed'], [])
            self.assertEqual(row['observation']['characters'], [])
        second = reference._read(Path(manifest['segments'][1]['prompt_path']).with_name('inputs.json'))
        self.assertEqual(second['references'][-1]['path'], manifest['segments'][0]['anchor_path'])
        self.assertEqual(len(generated[0]['options']['images']), 5)
        self.assertEqual(len(generated[1]['options']['images']), 5)
        self.assertNotIn(manifest['reference_assets'][-1]['path'], generated[1]['options']['images'])
        self.assertEqual([ref['role'] for ref in second['references']].count('character'), 4)
        self.assertTrue(output['generation_completed'])

    def test_failed_director_revision_preserves_first_valid_draft_and_raw_verdict(self):
        folder, _, manifest, _, requests, generated, _ = self._flow(revision_failure=True)
        self.assertEqual(len(generated), 2)
        for row in manifest['segments']:
            work = Path(row['prompt_path']).parent
            original = reference._read(work / 'director_raw.json')
            self.assertIn('integrated_multimodal_description', original)
            self.assertFalse(row['storyboard']['passed'])
            self.assertTrue((work / 'writing_revision_warning.json').exists())
        revisions = [req for req in requests if Path(req['path']).name == 'writing_revision_1']
        self.assertEqual(len(revisions), 2)

    def test_unavailable_preflight_has_no_fabricated_event_contract(self):
        _, _, manifest, output, _, generated, _ = self._flow(unavailable=('storyboard_review',))
        self.assertEqual(len(generated), 2)
        for row in manifest['segments']:
            self.assertEqual(row['storyboard']['status'], 'unavailable')
            self.assertNotIn('passed', row['storyboard'])
            self.assertEqual(row['visual_review']['status'], 'unavailable')
            self.assertNotIn('event_contract', row['storyboard'])
        self.assertEqual(output['quality_status'], 'completed_with_warnings')

    def test_revision_voice_binding_failure_is_repaired_within_optional_director_contract(self):
        _, _, manifest, _, _, generated, _ = self._flow(storyboard_failed=True, revision_binding_failure=True)
        self.assertEqual(len(generated), 2)
        for row in manifest['segments']:
            work = Path(row['prompt_path']).parent
            self.assertIn('说话者不符', reference._read(work / 'writing_revision_warning.json')['reason'])
            self.assertNotIn('<Subject 2>', reference._read(work / 'director_raw.json')['dialogue'][0]['delivery'])

    def test_failed_preflight_after_one_revision_is_preserved_and_does_not_reroll(self):
        _, _, manifest, _, requests, generated, joined = self._flow(storyboard_failed=True)
        self.assertEqual(len(generated), 2)
        self.assertEqual(len(joined), 1)
        self.assertTrue(all(row['storyboard']['passed'] is False for row in manifest['segments']))
        self.assertEqual(len([req for req in requests if Path(req['path']).name == 'writing_revision_1']), 2)

    def test_resume_reuses_first_segment_and_preserves_its_bindings(self):
        _, _, manifest, _, _, generated, joined = self._flow(pause=True, unavailable=('segment', 'opening'))
        self.assertEqual(len(generated), 2)
        self.assertEqual(len(joined), 1)
        self.assertEqual([row['index'] for row in joined[0]], [1, 2])

    def test_resume_rejects_changed_inputs_and_changed_media(self):
        def inputs(folder, task, manifest):
            task['inputs']['style'] += '改变'
        def media(folder, task, manifest):
            Path(manifest['segments'][0]['anchor_path']).write_bytes(b'changed')
        for mutate, message in ((inputs, '任务输入变化'), (media, '末帧变化')):
            with self.subTest(message=message), self.assertRaisesRegex(RuntimeError, message):
                self._flow(pause=True, mutate=mutate)
            self.assertEqual(len(self.artifacts['generated']), 1)
            self.assertEqual(self.artifacts['joined'], [])

    def test_guard_required_service_and_source_media_errors_propagate(self):
        errors = [RuntimeError('PUBG正在运行，任务已保留并暂停'),
                  RuntimeError('本地模型格式修复仍未通过：质量回复与当前请求签名不一致，不能复用旧缓存'),
                  RuntimeError('本地文字模型服务不可用')]
        for error in errors:
            with self.subTest(error=str(error)), self.assertRaises(type(error)):
                self._flow(failure=('segment', error))
            self.assertEqual(len(self.artifacts['generated']), 1)
            self.assertEqual(self.artifacts['joined'], [])
        for issue in (dict(code='missing_video', reason='文件丢失'),
                      dict(code='asr_worker_error', reason='音轨提取失败：测试')):
            with self.subTest(issue=issue), self.assertRaisesRegex(RuntimeError, '视频或音轨文件处理失败'):
                self._flow(audio=dict(status='unavailable', issues=[issue]))
            self.assertEqual(self.artifacts['joined'], [])

    def test_empty_observation_requires_latest_native_evidence_and_marker_keeps_binding(self):
        folder, task, _ = fixture()
        session = reference._Session(task['id'], Handle(task, []), folder, task['inputs'], {})
        request = ('可选观察', dict(stage='opening'), {})
        with patch.object(session, 'guard'), patch.object(reference.core, 'request_json',
                side_effect=RuntimeError(reference.EMPTY_OR_LENGTH)):
            with self.assertRaisesRegex(RuntimeError, '为空或被截断'):
                session.review(request, folder / 'report')
        marker_folder = folder / 'old_marker'
        reference._save(marker_folder / 'unavailable.json', dict(status='unavailable', reason='历史记录',
            request_sha256=reference._signature(dict(request=request, images=[]))))
        with patch.object(session, 'ask') as ask:
            old = session.review(request, marker_folder)
            self.assertEqual(old['reason'], '历史记录')
            ask.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, '输入变化'):
            session.review(('可选观察', dict(stage='segment'), {}), marker_folder)

    def test_cached_exhausted_signature_failure_is_not_optional(self):
        folder, task, _ = fixture()
        session = reference._Session(task['id'], Handle(task, []), folder, task['inputs'], {})
        request = ('可选观察', dict(stage='opening'), {})
        work = folder / 'cached_failure'
        reference._save(work / 'binding.json', dict(system=request[0], payload=request[1], schema=request[2], images=[]))
        reference._save(work / 'call_2_fixture/contract_error.json', dict(error='质量回复与当前请求签名不一致，不能复用旧缓存'))
        with self.assertRaisesRegex(RuntimeError, '签名不一致'):
            session.review(request, work)


if __name__ == '__main__':
    unittest.main(verbosity=2)
