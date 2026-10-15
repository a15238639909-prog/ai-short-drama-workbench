"""Verifier fixtures only: no models, real tasks, image rendering or deletion."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cores import story_video_prompts as prompts

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('verify_story_video_under_test', ROOT/'tools/verify_story_video.py')
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def local_reply(directory, accepted, payload, vision=False):
    save(directory/'accepted.json', accepted)
    trace = directory/'call_1_synthetic'/'vision_1' if vision else directory/'call_1_synthetic'/'text_1'
    content = json.dumps(payload, ensure_ascii=False)
    if vision:
        content = [{'type': 'text', 'text': content}, {'type': 'text', 'text': 'Observation image 1'}]
    save(trace/'request.json', {'endpoint': 'http://127.0.0.1:8080/v1/chat/completions',
                              'model': 'synthetic-fixture-no-model',
                              'messages': [{'role': 'system', 'content': 'synthetic'}, {'role': 'user', 'content': content}]})
    save(trace/'response.json', {'text': '```json\n' + json.dumps(accepted, ensure_ascii=False) + '\n```'})


class VerifyStoryVideoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = Path(tempfile.mkdtemp(prefix='verify_story_video_synthetic_'))

    @classmethod
    def tearDownClass(cls):
        print('RETAINED_SYNTHETIC_VERIFIER_RECORDS=' + str(cls.folder))

    def fixture(self, name, image_input=False, plan_receipt=True):
        folder = self.folder/name
        folder.mkdir()
        opening = str(folder/'user_opening.png') if image_input else ''
        if image_input:
            (folder/'opening.png').write_bytes(b'synthetic file-existence fixture only')
        plan = {'title': '隔离验收测试', 'characters': [{'id': 'C1', 'name': '测试角色'}],
                'beats': [{'index': 1, 'dialogue': [{'speaker': 'C1', 'text': '先等等。'}]},
                          {'index': 2, 'dialogue': []}]}
        if plan_receipt:
            local_reply(folder/'planning', plan, {'count': 2, 'brief': 'synthetic test'})
        else:
            save(folder/'planning/accepted.json', plan)
        segments = []
        for beat in plan['beats']:
            index = beat['index']
            work = folder/'segments'/('%02d_take_1' % index)
            work.mkdir(parents=True)
            mode = 'i2va' if image_input or index > 1 else 't2va'
            payload = prompts.write_request(plan, beat, {}, 8, mode)[1]
            reply = {'integrated_multimodal_description': '[Shot 1] A medium shot follows the adult at the doorway.',
                     'overall_soundscape': 'Footsteps on stone.', 'non_diegetic_music': 'N/A',
                     'expected_end_state': 'synthetic', 'brief': 'synthetic', 'continuity_notes': [],
                     'timeline': [{'start': 0, 'end': 8, 'action': 'synthetic', 'visible_result': 'synthetic'}],
                     'dialogue': [{'speaker': line['speaker'], 'text': line['text'], 'start': 1, 'end': 2,
                                   'delivery': 'The adult says quietly'} for line in beat['dialogue']]}
            local_reply(work/'writing', reply, payload)
            written = prompts.compile_written(reply, payload)
            save(work/'director.json', written)
            observation = {'actual_visible': 'synthetic observation %d' % index,
                           'beat_status': 'completed', 'continuity_risks': []}
            local_reply(work/'endpoint', observation, {'stage': 'opening'}, vision=True)
            anchor, clip = work/'anchor.png', work/'clip.mp4'
            anchor.write_bytes(b'synthetic file-existence fixture only')
            clip.write_bytes(b'synthetic file-existence fixture only')
            images = [segments[-1]['anchor_path']] if index > 1 else [str(folder/'opening.png')] if image_input else []
            seed = 700 + index
            valid = work/'generation/video_001_valid'
            request = {'endpoint': 'http://127.0.0.1:8190', 'mode': mode,
                       'prompt': written['prompt'], 'options': {'images': images}}
            save(valid/'request.json', request)
            save(valid/'workflow.json', {'graph': {'1': {'class_type': 'MiniMaxH3ImageToVideo',
                                                        'inputs': {'prompt': written['prompt']}}}, 'meta': {'seed': seed}})
            # A newer unrelated trace must not be paired with the accepted graph.
            wrong = work/'generation/video_999_unrelated'
            save(wrong/'request.json', dict(request, prompt='unrelated', options={'images': ['wrong.png']}))
            save(wrong/'workflow.json', {'graph': {'1': {'class_type': 'MiniMaxH3ImageToVideo',
                                                        'inputs': {'prompt': 'unrelated'}}}, 'meta': {'seed': 999}})
            save(work/'generation_result.json', {'seed': seed})
            segments.append({'index': index, 'model_mode': mode, 'prompt_path': str(work/'director.json'),
                             'anchor_path': str(anchor), 'clip_path': str(clip), 'kept_seconds': 8,
                             'observation': observation})
        film, raw = folder/'film.mp4', folder/'raw.mp4'
        film.write_bytes(b'synthetic file-existence fixture only')
        raw.write_bytes(b'synthetic file-existence fixture only')
        save(folder/'manifest.json', {'inputs': {'segment_count': 2, 'opening_image': opening},
                                     'local_ai_only': True, 'plan': plan, 'segments': segments,
                                     'output': {'full_decode_passed': True, 'output_path': str(film), 'raw_path': str(raw)}})
        (folder/'network_audit.jsonl').write_text(json.dumps({'event': 'guard_installed', 'pid': 12345}) + '\n', encoding='utf-8')
        return folder

    def verify(self, folder):
        with patch.dict(os.environ, {'V41_NO_MODEL': '1'}):
            return verifier.verify(folder)

    def reviewed_fixture(self, name, revised=False, revision_receipt=True):
        folder = self.fixture(name)
        path = folder/'manifest.json'
        manifest = json.loads(path.read_text(encoding='utf-8'))
        source = folder/'planning/accepted.json'
        round_number = 1
        if revised:
            original = manifest['plan']
            manifest['plan'] = dict(original, title='本地改写合成记录')
            source = folder/'planning/revision_1/accepted.json'
            failed = {'passed': False, 'issues': [{'beat': 1, 'category': 'pacing',
                       'reason': 'synthetic issue', 'repair_direction': 'synthetic direction'}], 'strengths': []}
            local_reply(folder/'planning/review_1', failed, {'stage': 'review', 'plan': original})
            if revision_receipt:
                local_reply(source.parent, manifest['plan'], {'previous_draft': original, 'review_feedback': failed})
            else:
                save(source, manifest['plan'])
            round_number = 2
        accepted = {'passed': True, 'issues': [], 'strengths': ['synthetic strength']}
        review_source = folder/'planning'/('review_%d' % round_number)/'accepted.json'
        local_reply(review_source.parent, accepted, {'stage': 'review', 'plan': manifest['plan']})
        manifest['plan_source'] = str(source)
        manifest['plan_review'] = dict(accepted, round=round_number, pending=False,
                                       plan_signature=verifier._plan_signature(manifest['plan']),
                                       review_source=str(review_source), plan_source=str(source))
        save(path, manifest)
        return folder

    def legacy_zero_fixture(self, name):
        folder = self.fixture(name)
        work = folder/'segments/01_take_1'
        accepted = json.loads((work/'writing/accepted.json').read_text(encoding='utf-8'))
        accepted['integrated_multimodal_description'] = accepted['integrated_multimodal_description'].replace(
            '[Shot 1]', '[Shot 1] At 00:00.000,', 1)
        captured = json.loads((work/'writing/call_1_synthetic/text_1/request.json').read_text(encoding='utf-8'))
        payload = json.loads(captured['messages'][1]['content'])
        local_reply(work/'writing', accepted, payload)
        written = prompts.compile_written(accepted, payload)
        written['prompt'] = written['prompt'].replace(
            'integrated_multimodal_description: [Shot 1]',
            'integrated_multimodal_description: [Shot 1] At 00:00.000,', 1)
        save(work/'director.json', written)
        trace = work/'generation/video_001_valid'
        graph = json.loads((trace/'workflow.json').read_text(encoding='utf-8'))
        graph['graph']['1']['inputs']['prompt'] = written['prompt']
        save(trace/'workflow.json', graph)
        request = json.loads((trace/'request.json').read_text(encoding='utf-8'))
        request['prompt'] = written['prompt']
        save(trace/'request.json', request)
        return folder

    def test_v3_fenced_reply_and_matching_graph_pair(self):
        report = self.verify(self.fixture('text'))
        self.assertEqual(report['input_mode'], 't2va')
        self.assertEqual([row['mode'] for row in report['segments']], ['t2va', 'i2va'])
        self.assertTrue(all('video_001_valid' in row['workflow_path'] for row in report['segments']))
        self.assertTrue(report['creative_content_from_local_model'])

    def test_image_start_binds_copied_opening_then_adopted_anchor(self):
        report = self.verify(self.fixture('image', image_input=True))
        self.assertEqual([row['mode'] for row in report['segments']], ['i2va', 'i2va'])

    def test_missing_plan_response_is_not_local_provenance(self):
        with self.assertRaises(AssertionError):
            self.verify(self.fixture('unproven_plan', plan_receipt=False))

    def test_wrong_actual_h3_image_is_rejected(self):
        folder = self.fixture('wrong_image')
        path = folder/'segments/02_take_1/generation/video_001_valid/request.json'
        request = json.loads(path.read_text(encoding='utf-8'))
        request['options']['images'] = ['not-the-adopted-anchor.png']
        save(path, request)
        with self.assertRaises(AssertionError):
            self.verify(folder)

    def test_current_original_plan_review_has_local_provenance(self):
        report = self.verify(self.reviewed_fixture('reviewed_original'))
        proof = report['plan_provenance']
        self.assertTrue(proof['review_present'])
        self.assertTrue(proof['review_verified'])
        self.assertEqual(proof['review_round'], 1)

    def test_revised_plan_uses_actual_revision_reply(self):
        report = self.verify(self.reviewed_fixture('reviewed_revision', revised=True))
        proof = report['plan_provenance']
        self.assertEqual(proof['review_round'], 2)
        self.assertIn('revision_1', proof['source'])
        self.assertTrue(proof['review_verified'])
        self.assertTrue(proof['revision_input_verified'])
        with self.assertRaises(AssertionError):
            self.verify(self.reviewed_fixture('unproven_revision', revised=True, revision_receipt=False))

    def test_pending_stale_or_wrong_plan_review_is_rejected(self):
        for problem in ('pending', 'signature', 'review_payload'):
            with self.subTest(problem=problem):
                folder = self.reviewed_fixture('invalid_review_' + problem)
                path = folder/'manifest.json'
                manifest = json.loads(path.read_text(encoding='utf-8'))
                if problem == 'pending':
                    manifest['plan_review']['pending'] = True
                elif problem == 'signature':
                    manifest['plan_review']['plan_signature'] = 'stale-signature'
                else:
                    review_source = Path(manifest['plan_review']['review_source'])
                    accepted = json.loads(review_source.read_text(encoding='utf-8'))
                    local_reply(review_source.parent, accepted, {'stage': 'review', 'plan': {'different': True}})
                save(path, manifest)
                with self.assertRaises(AssertionError):
                    self.verify(folder)

    def test_legacy_zero_prefix_is_mechanical_but_creative_and_graph_stay_exact(self):
        report = self.verify(self.legacy_zero_fixture('legacy_zero'))
        self.assertTrue(report['segments'][0]['transport_compatibility_applied'])
        for problem in ('creative_word', 'graph_prompt'):
            with self.subTest(problem=problem):
                folder = self.legacy_zero_fixture('legacy_zero_' + problem)
                work = folder/'segments/01_take_1'
                if problem == 'creative_word':
                    path = work/'director.json'
                    written = json.loads(path.read_text(encoding='utf-8'))
                    written['prompt'] = written['prompt'].replace('moving adult', 'different adult').replace('adult at the doorway', 'adult at the window')
                    save(path, written)
                else:
                    path = work/'generation/video_001_valid/workflow.json'
                    workflow = json.loads(path.read_text(encoding='utf-8'))
                    workflow['graph']['1']['inputs']['prompt'] = workflow['graph']['1']['inputs']['prompt'].replace(' At 00:00.000,', '', 1)
                    save(path, workflow)
                with self.assertRaises(AssertionError):
                    self.verify(folder)


if __name__ == '__main__':
    unittest.main()
