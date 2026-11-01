"""Creation-only wardrobe versions; legacy continuations keep their snapshots."""
import copy
from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'

from cores import project_settings, runtime_reset, store, story_core
from cores import story_video_core as core, story_video_reference as reference
from cores import wardrobe_continuity as wardrobe


class WardrobePolicyVersioningTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='wardrobe_policy_versioning_'))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(store, 'DATA', self.folder / 'data'))

    def admission(self, normalized):
        self.stack.enter_context(patch.object(core, 'validate_inputs', return_value=copy.deepcopy(normalized)))
        self.stack.enter_context(patch.object(core, '_busy'))
        self.stack.enter_context(patch.object(core, 'capabilities', return_value=dict(missing=[])))
        self.stack.enter_context(patch.dict(core.runtime.RUNNERS, {}))
        return self.stack.enter_context(patch.object(core.runtime, 'create_task',
            side_effect=lambda kind, title, inputs: dict(id='WARDROBE_TEST_TASK', kind=kind,
                title=title, inputs=copy.deepcopy(inputs))))

    def task(self, inputs, status='failed'):
        return dict(id='WARDROBE_TEST_TASK', kind=core.KIND, status=status,
            inputs=copy.deepcopy(inputs), created=9999999999,
            checkpoint=dict(story_video_folder=str(self.folder / 'unstarted-job')))

    def test_new_story_pins_top_level_policy_after_inheriting_global_defaults(self):
        defaults = dict(style='写实摄影', wardrobe_policy='outdated-global-value')
        store.save_json(store.DATA / 'global_defaults.json', defaults)
        story = story_core.create_story('成年修表师归还工具。')
        saved = story_core.get_story(story['story_id'])
        self.assertEqual(story['wardrobe_policy'], wardrobe.POLICY)
        self.assertEqual(saved['wardrobe_policy'], wardrobe.POLICY)
        self.assertTrue(wardrobe.enabled(saved))
        self.assertEqual(store.load_json(store.DATA / 'global_defaults.json', {}), defaults)

    def test_legacy_story_read_save_and_settings_edit_never_add_policy(self):
        original = dict(story_id='WARDROBE_OLD_STORY', title='旧项目',
            one_line='成年修表师归还工具。', settings=dict(style='写实摄影'), created=9999999999)
        story_core.save_story(original)
        saved = story_core.get_story(original['story_id'])
        before = copy.deepcopy(saved)
        project_settings.get(saved)
        self.assertEqual(saved, before)
        self.assertFalse(wardrobe.enabled(saved))
        project_settings.save(saved, dict(style='电影级写实'))
        saved = story_core.get_story(original['story_id'])
        self.assertNotIn('wardrobe_policy', saved)
        self.assertNotIn('wardrobe_policy', saved['settings'])
        self.assertNotIn('wardrobe_policy', saved['project_settings'])
        self.assertFalse(wardrobe.enabled(saved))

    def test_project_settings_do_not_override_new_story_metadata(self):
        story = story_core.create_story('成年修表师归还工具。')
        project_settings.save(story, dict(style='写实摄影', wardrobe_policy='editable-settings-value'))
        saved = story_core.get_story(story['story_id'])
        self.assertEqual(saved['wardrobe_policy'], wardrobe.POLICY)
        self.assertTrue(wardrobe.enabled(saved))

    def test_new_reference_admission_pins_policy_without_mutating_caller(self):
        inputs = dict(input_mode='reference', story_direction='成年修表师归还工具。')
        before = copy.deepcopy(inputs)
        normalized = dict(inputs, flow_version=3)
        creator = self.admission(normalized)
        result = core.start(inputs)
        self.assertEqual(result['inputs'], dict(normalized, wardrobe_policy=wardrobe.POLICY))
        self.assertEqual(inputs, before)
        self.assertEqual(creator.call_count, 1)

    def test_raw_legacy_marker_cannot_disable_new_reference_policy(self):
        normalized = dict(input_mode='reference', story_direction='成年修表师归还工具。', flow_version=3)
        creator = self.admission(normalized)
        for raw_marker in (None, '', wardrobe.POLICY):
            with self.subTest(policy=raw_marker):
                inputs = dict(normalized, wardrobe_policy=raw_marker)
                before = copy.deepcopy(inputs)
                result = core.start(inputs)
                self.assertEqual(result['inputs']['wardrobe_policy'], wardrobe.POLICY)
                self.assertEqual(inputs, before)
        self.assertEqual(creator.call_count, 3)

    def test_nonreference_admission_keeps_existing_generation_inputs(self):
        for mode in ('text', 'image'):
            with self.subTest(mode=mode), ExitStack() as stack:
                normalized = dict(input_mode=mode, story_direction='成年修表师归还工具。', flow_version=2)
                stack.enter_context(patch.object(core, 'validate_inputs', return_value=copy.deepcopy(normalized)))
                stack.enter_context(patch.object(core, '_busy'))
                stack.enter_context(patch.object(core, 'capabilities', return_value=dict(missing=[])))
                stack.enter_context(patch.dict(core.runtime.RUNNERS, {}))
                creator = stack.enter_context(patch.object(core.runtime, 'create_task', return_value='unchanged'))
                result = core.start(dict(normalized, wardrobe_policy='unsupported'))
                self.assertEqual(result, 'unchanged')
                self.assertEqual(creator.call_args.args[2], normalized)

    def test_rewrite_inherits_source_policy_including_absence(self):
        for source_marker in (None, wardrobe.POLICY):
            with self.subTest(policy=source_marker), ExitStack() as stack:
                inputs = dict(input_mode='reference', story_direction='成年修表师归还工具。')
                if source_marker is not None:
                    inputs['wardrobe_policy'] = source_marker
                before = copy.deepcopy(inputs)
                task = self.task(inputs)
                normalized = dict(input_mode='reference', story_direction=inputs['story_direction'], flow_version=3)
                stack.enter_context(patch.object(core.runtime, 'load', return_value=task))
                stack.enter_context(patch.object(core, '_load', return_value={}))
                stack.enter_context(patch.dict(core._CHILDREN, {}, clear=True))
                stack.enter_context(patch.object(core, 'validate_inputs', return_value=normalized))
                stack.enter_context(patch.object(core, '_busy'))
                stack.enter_context(patch.object(core, 'capabilities', return_value=dict(missing=[])))
                stack.enter_context(patch.dict(core.runtime.RUNNERS, {}))
                creator = stack.enter_context(patch.object(core.runtime, 'create_task',
                    side_effect=lambda kind, title, submitted: dict(inputs=copy.deepcopy(submitted))))
                result = core.action(task['id'], 'rewrite')
                self.assertEqual(result['inputs'].get('wardrobe_policy'), source_marker)
                self.assertEqual('wardrobe_policy' in result['inputs'], source_marker is not None)
                self.assertEqual(task['inputs'], before)
                self.assertEqual(creator.call_count, 1)

    def test_rewrite_explicit_empty_policy_stays_legacy(self):
        inputs = dict(input_mode='reference', story_direction='成年修表师归还工具。', wardrobe_policy='')
        task = self.task(inputs)
        before = copy.deepcopy(inputs)
        self.admission(dict(input_mode='reference', flow_version=3))
        with patch.object(core.runtime, 'load', return_value=task), \
             patch.object(core, '_load', return_value={}), \
             patch.dict(core._CHILDREN, {}, clear=True):
            result = core.action(task['id'], 'rewrite')
        self.assertNotIn('wardrobe_policy', result['inputs'])
        self.assertFalse(wardrobe.enabled(result['inputs']))
        self.assertEqual(task['inputs'], before)

    def test_retry_and_resume_preserve_unversioned_inputs_and_frozen_requests(self):
        for action in ('retry', 'resume'):
            with self.subTest(action=action), ExitStack() as stack:
                inputs = dict(input_mode='reference', story_direction='成年修表师归还工具。',
                    frozen_request=dict(prompt='旧冻结提示词', references=['old-ref'], key='old-request'))
                task = self.task(inputs)
                before = copy.deepcopy(task['inputs'])
                stack.enter_context(patch.object(core.runtime, 'load', return_value=task))
                stack.enter_context(patch.object(core.runtime, 'list_tasks', return_value=[]))
                stack.enter_context(patch.object(core.runtime, 'save'))
                stack.enter_context(patch.object(core.runtime, 'action', return_value=task))
                stack.enter_context(patch.dict(core.runtime.RUNNERS, {}))
                stack.enter_context(patch.dict(core._CHILDREN, {}, clear=True))
                stack.enter_context(patch('models.gpu_manager.assert_local_run_allowed'))
                starter = stack.enter_context(patch.object(core, 'start', side_effect=AssertionError('no new task')))
                result = core.action(task['id'], action)
                self.assertEqual(result['inputs'], before)
                self.assertNotIn('wardrobe_policy', result['inputs'])
                starter.assert_not_called()

    def test_approve_and_redo_keep_existing_task_and_manifest_policy_absent(self):
        for action in ('approve', 'redo'):
            with self.subTest(action=action), ExitStack() as stack:
                inputs = dict(input_mode='reference', flow_version=3, story_direction='成年修表师归还工具。')
                task = self.task(inputs, status='waiting_user' if action == 'approve' else 'completed')
                manifest = dict(plan=dict(title='旧计划'), inputs=copy.deepcopy(inputs),
                    inputs_sha256='old-input-signature', decision='story',
                    segments=[dict(index=1, clip_path='old-test-video.mp4')])
                before = copy.deepcopy((task['inputs'], manifest['inputs'], manifest['inputs_sha256']))
                stack.enter_context(patch.object(core.runtime, 'load', return_value=task))
                stack.enter_context(patch.object(core, '_load', return_value=manifest))
                stack.enter_context(patch.object(core, '_save'))
                stack.enter_context(patch.object(core, '_busy'))
                stack.enter_context(patch.object(core.runtime, 'save'))
                stack.enter_context(patch.object(core.runtime, '_queue'))
                stack.enter_context(patch.dict(core.runtime.RUNNERS, {}))
                stack.enter_context(patch.dict(core._CHILDREN, {}, clear=True))
                result = core.action(task['id'], action, segment=1)
                self.assertEqual((result['inputs'], manifest['inputs'], manifest['inputs_sha256']), before)
                self.assertNotIn('wardrobe_policy', result['inputs'])
                self.assertNotIn('wardrobe_policy', manifest)

    def test_old_queued_reference_without_manifest_is_dispatched_without_upgrade(self):
        task = self.task(dict(input_mode='reference', story_direction='成年修表师归还工具。'), status='queued')
        before = copy.deepcopy(task)
        with patch.object(core.runtime, 'load', return_value=task), \
             patch.object(runtime_reset, 'bind_thread'), patch.object(runtime_reset, 'assert_current'), \
             patch.object(reference, 'run_job', return_value='legacy-reference') as runner:
            self.assertEqual(core.run_job(task['id']), 'legacy-reference')
        self.assertEqual(task, before)
        self.assertFalse((self.folder / 'unstarted-job/manifest.json').exists())
        runner.assert_called_once_with(task['id'])

    def test_unknown_reference_policy_is_rejected_before_new_or_old_worker_runs(self):
        inputs = dict(input_mode='reference', story_direction='成年修表师归还工具。', wardrobe_policy='unsupported')
        creator = self.admission(dict(input_mode='reference', flow_version=3))
        with self.assertRaisesRegex(ValueError, '衣着连续性版本未知'):
            core.start(inputs)
        creator.assert_not_called()
        task = self.task(inputs, status='queued')
        before = copy.deepcopy(task)
        with patch.object(core.runtime, 'load', return_value=task), \
             patch.object(runtime_reset, 'bind_thread'), patch.object(runtime_reset, 'assert_current'), \
             patch.object(reference, 'run_job', side_effect=AssertionError('no unknown-policy worker')):
            with self.assertRaisesRegex(ValueError, '衣着连续性版本未知'):
                core.run_job(task['id'])
        self.assertEqual(task, before)


if __name__ == '__main__':
    unittest.main()
