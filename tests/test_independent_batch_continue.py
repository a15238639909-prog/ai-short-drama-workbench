"""Run real batch loops with text/byte receipts; no AI, images or encoders."""
from contextlib import ExitStack
import copy
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

os.environ['V41_NO_MODEL'] = '1'
from cores import runtime_core as runtime, runtime_reset, long_comic_core as long_comic
from cores import story_core, narrative_core, project_settings, project_prompt
from production import comic_core, video_core, video_plan
from models import krea_client


class Handle:
    def __init__(self, task):
        self.task = task
    def set(self, **changes):
        self.task.update(copy.deepcopy(changes))
    def _get(self):
        return copy.deepcopy(self.task)
    def step(self, *args):
        pass
    def check_pause(self):
        if self.task.get('status') in ('paused', 'cancelled'):
            raise runtime.TaskPaused()


class IndependentBatchTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='independent_batch_code_only_'))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(runtime_reset, 'STATE', self.root / 'reset.json'))
        self.stack.enter_context(patch.object(runtime, 'TASKS_DIR', self.root / 'tasks'))
        self.stack.enter_context(patch.object(runtime, 'PRODUCTIONS_DIR', self.root / 'productions'))
        self.addCleanup(runtime_reset.bind_thread, None)
        runtime_reset.bind_thread(None)
        self.story = dict(story_id='STORY_FIXTURE', title='测试故事', character_ids=[], scene_ids=[])
        self.episode = dict(adopted=True, events=[], units=[dict(unit_id='unit_%d' % n) for n in (1, 2, 3)])
        for owner, name, value in ((story_core, 'get_story', self.story),
                (narrative_core, 'load_episode', self.episode), (project_settings, 'get', {}),
                (project_settings, 'style_of', '写实'), (project_prompt, 'video_visual_block', '写实')):
            self.stack.enter_context(patch.object(owner, name, return_value=value))
        self.task = dict(id='task_fixture', status='running', inputs=dict(story_id='STORY_FIXTURE',
            target_seconds=45, target_pages=3, outdir=str(self.root / 'comic')), checkpoint={})
        self.handle = Handle(self.task)

    def comic(self, failure=None, pdf_failure=False):
        pages = [dict(number=n, event_ids=[]) for n in (1, 2, 3)]
        def generate(prompt, **kwargs):
            if failure and int(prompt) == failure[0]:
                raise failure[1]
            output = self.root / ('raw_%s.bin' % prompt)
            output.write_bytes(('mock response ' + prompt).encode())
            return dict(output_path=str(output))
        with ExitStack() as stack:
            plan = stack.enter_context(patch.object(comic_core, 'generate_page_beats', return_value=pages))
            stack.enter_context(patch.object(comic_core, 'generate_panel_plan', side_effect=lambda page: [page]))
            stack.enter_context(patch.object(comic_core, 'compile_page_prompt', side_effect=lambda s,e,p,*a: str(p['number'])))
            generation = stack.enter_context(patch.object(krea_client, 'generate', side_effect=generate))
            stack.enter_context(patch.object(comic_core, 'letter_page',
                side_effect=lambda source,target,panels: Path(target).write_bytes(Path(source).read_bytes())))
            pdf = stack.enter_context(patch.object(comic_core, 'build_pdf',
                side_effect=RuntimeError('mock PDF failure') if pdf_failure else None))
            result = runtime._run_comic(self.handle, self.task)
        return result, generation.call_count, plan.call_count, pdf.call_count

    def video(self, failure=None, merge_failure=False, tail_failure=False):
        def plan(story, episode, unit, index):
            return dict(event_ids=[], director_id='d%d' % index, state_snapshot_id='s%d' % index,
                        director_brief=dict(shots=[], story_script='当前故事'))
        def produce(story_id, unit, *args, **kwargs):
            number = int(unit['unit_id'].split('_')[-1])
            if failure and number == failure[0]:
                raise failure[1]
            path = self.root / ('clip_%d.bin' % number)
            path.write_bytes(('mock clip %d' % number).encode())
            return dict(path=str(path), take_id='take%d' % number, pack=dict(reference_pack_id='p%d' % number))
        def extract(path, target):
            if tail_failure:
                raise RuntimeError('mock frame extraction failed')
            marker = self.root / (Path(path).stem + '_tail.bin')
            marker.write_bytes(b'mock tail receipt')
            return str(marker), 14.9
        with ExitStack() as stack:
            stack.enter_context(patch.object(video_plan, 'build_sequence_plan', side_effect=plan))
            generation = stack.enter_context(patch.object(video_core, 'produce_sequence', side_effect=produce))
            stack.enter_context(patch.object(video_plan, 'extract_end_frame', side_effect=extract))
            stack.enter_context(patch.object(video_plan, 'write_trace'))
            merge = stack.enter_context(patch.object(video_core, 'concat_sequences',
                return_value=dict(path='mock assembled output'),
                side_effect=RuntimeError('mock concat failure') if merge_failure else None))
            result = runtime._run_video_formal(self.handle, self.task)
        return result, generation, merge

    def test_comic_middle_failure_continues_and_resume_reuses_successful_pages(self):
        first, calls, plans, pdfs = self.comic((2, ValueError('mock panel failure')))
        self.assertEqual((calls, plans, pdfs), (3, 1, 1))
        self.assertFalse(first['generation_completed'])
        self.assertEqual(first['pages'], 2)
        self.assertEqual(set(first['failed_items']), {'2'})
        retained = {path: Path(path).read_bytes() for path in first['images']}
        second, calls, plans, pdfs = self.comic()
        self.assertEqual((calls, plans, pdfs), (1, 0, 1))
        self.assertTrue(second['generation_completed'])
        self.assertEqual(second['failed_items'], {})
        self.assertTrue(all(Path(path).read_bytes() == value for path, value in retained.items()))

    def test_pdf_failure_keeps_pages_and_retries_assembly_without_generation(self):
        first, _, _, _ = self.comic(pdf_failure=True)
        self.assertFalse(first['generation_completed'])
        self.assertIsNone(first['pdf'])
        self.assertEqual(first['pages'], 3)
        second, calls, plans, _ = self.comic()
        self.assertTrue(second['generation_completed'])
        self.assertEqual((calls, plans), (0, 0))

    def test_video_middle_failure_still_generates_later_unit_and_merges_saved_clips(self):
        first, generation, merge = self.video((2, RuntimeError('mock item failure')))
        self.assertEqual(generation.call_count, 3)
        self.assertIsNone(generation.call_args_list[2].kwargs['previous_end_state'])
        self.assertFalse(first['generation_completed'])
        self.assertEqual(len(first['sequences']), 2)
        self.assertEqual(merge.call_args.args[1], 30)
        second, generation, merge = self.video()
        self.assertEqual(generation.call_count, 1)
        self.assertEqual(generation.call_args.args[1]['unit_id'], 'unit_2')
        self.assertTrue(second['generation_completed'])
        self.assertEqual(second['failed_items'], {})
        self.assertEqual(len(merge.call_args.args[0]), 3)
        self.assertIn('3', second['continuity_warnings'])

    def test_video_merge_and_tail_failure_keep_outputs_without_rerenders(self):
        first, generation, _ = self.video(merge_failure=True, tail_failure=True)
        self.assertEqual(generation.call_count, 3)
        self.assertFalse(first['generation_completed'])
        self.assertIsNone(first['output'])
        self.assertEqual(len(first['continuity_warnings']), 2)
        second, generation, _ = self.video()
        self.assertEqual(generation.call_count, 0)
        self.assertTrue(second['generation_completed'])
        self.assertEqual(set(second['continuity_warnings']), {'2', '3'})
        self.assertTrue(all('未自动重拍' in warning for warning in second['continuity_warnings'].values()))

    def test_cancel_gpu_and_changed_input_never_become_item_failures(self):
        for error in (runtime.TaskPaused(), runtime_reset.Cancelled('cancelled'),
                      RuntimeError('PUBG正在运行'), RuntimeError('输入已变化')):
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                self.video((1, error))
        self.task['checkpoint'] = {}
        self.comic()
        self.story['title'] = 'changed source'
        with self.assertRaisesRegex(RuntimeError, '输入已变化'):
            self.comic()

    def test_runtime_reports_partial_and_resume_is_available(self):
        task = dict(self.task, id='task_partial', kind='fixture', status='queued', runtime_generation='initial')
        runtime.save(task)
        with patch.dict(runtime.RUNNERS, fixture=lambda h,t: dict(generation_completed=False, clips=['kept'])):
            runtime._run(task['id'])
        saved = runtime.load(task['id'])
        self.assertEqual(saved['status'], 'partial')
        self.assertFalse(saved['output']['generation_completed'])
        with patch.object(runtime, '_queue') as queue:
            runtime.action(task['id'], 'resume')
        self.assertEqual(runtime.load(task['id'])['status'], 'queued')
        queue.assert_called_once_with(task['id'])


class LongComicBatchContinueTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='long_comic_batch_code_only_'))
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(runtime_reset, 'STATE', self.root / 'reset.json'))
        self.addCleanup(runtime_reset.bind_thread, None); runtime_reset.bind_thread(None)
        self.lock = threading.Lock()
        self.stack.enter_context(patch.object(long_comic, '_RUN_LOCK', self.lock))
        self.stack.enter_context(patch.object(long_comic, '_THREADS', {}))
        self.state = dict(id='LC_FIXTURE', chapter_count=1, chapters=[dict(number=1,
            pages=[dict(number=n, versions=[], current_version=None, status='planned') for n in (1,2,3)])])
        self.stack.enter_context(patch.object(long_comic, '_load', side_effect=lambda pid: copy.deepcopy(self.state)))
        def save(project):
            self.state.clear(); self.state.update(copy.deepcopy(project))
        self.stack.enter_context(patch.object(long_comic, '_save', side_effect=save))
        self.stack.enter_context(patch.object(long_comic, '_plan_chapter'))
        self.stack.enter_context(patch.object(long_comic, '_v2', return_value=False))
        self.stack.enter_context(patch.object(long_comic, '_current', side_effect=lambda page:
            next((row for row in page['versions'] if row['status']=='completed'), None)))

    def run_batch(self, fail=None):
        job_id = 'run%d' % (len(self.state.get('jobs', [])) + 1)
        self.state.setdefault('jobs', []).append(job_id)
        self.state['job'] = dict(id=job_id, action='generate', chapter=1, status='queued',
                                pause_requested=False, cancel_requested=False, done=0, total=3)
        attempts=[]
        def render(pid, jid, chapter, number, **kwargs):
            page=self.state['chapters'][0]['pages'][number-1]
            if page['current_version']:
                return
            attempts.append(number)
            version=dict(version=len(page['versions'])+1,status='generating')
            page['versions'].append(version)
            if fail and number == fail[0]:
                raise fail[1]
            version['status']='completed';page.update(current_version=version['version'],status='completed')
        self.lock.acquire()
        with patch.object(long_comic, '_render_page', side_effect=render):
            long_comic._run_job('LC_FIXTURE',job_id,'initial')
        return copy.deepcopy(self.state),attempts

    def test_failed_middle_page_does_not_stop_later_pages_and_only_missing_page_resumes(self):
        first,attempts=self.run_batch((2,ValueError('mock malformed panel')))
        self.assertEqual(attempts,[1,2,3])
        self.assertEqual(first['job']['status'],'partial')
        self.assertEqual(first['job']['done'],2)
        self.assertEqual(first['chapters'][0]['pages'][1]['versions'][0]['status'],'failed')
        self.assertEqual(set(first['job']['failed_pages']),{'2'})
        second,attempts=self.run_batch()
        self.assertEqual(attempts,[2])
        self.assertEqual(second['job']['status'],'completed')
        self.assertEqual(second['chapters'][0]['pages'][0],first['chapters'][0]['pages'][0])
        self.assertEqual(second['chapters'][0]['pages'][2],first['chapters'][0]['pages'][2])

    def test_pause_and_game_protection_stop_before_later_page_submissions(self):
        first,attempts=self.run_batch((1,long_comic._Paused('暂停')))
        self.assertEqual(first['job']['status'],'paused');self.assertEqual(attempts,[1])
        second,attempts=self.run_batch((1,RuntimeError('PUBG正在运行')))
        self.assertEqual(second['job']['status'],'failed');self.assertEqual(attempts,[1])


if __name__ == '__main__':
    unittest.main()
