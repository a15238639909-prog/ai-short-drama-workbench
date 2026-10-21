"""Windows checkpoint contention: real retained files, no inference or cleanup."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from cores import store, story_video_core as core, story_video_reference as reference


def denied(code):
    error = PermissionError(13, 'simulated Windows file sharing conflict')
    error.winerror = code
    return error


class CheckpointWriteTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='story_checkpoint_retained_'))

    def writers(self):
        # Text/image modes share core._save; reference has its own entry point.
        return (('text', core._save), ('image', core._save), ('reference', reference._save))

    def checkpoint(self, mode):
        path = self.root / mode / 'manifest.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        old = dict(inputs=dict(input_mode=mode), segments=[dict(index=1)],
                   generation_completed=False)
        path.write_text(json.dumps(old), encoding='utf-8')
        return path, old

    def test_three_modes_use_the_shared_atomic_writer(self):
        for mode, save in self.writers():
            with self.subTest(mode=mode):
                path, old = self.checkpoint(mode)
                with patch.object(store, 'save_json', wraps=store.save_json) as shared:
                    save(path, old)
                shared.assert_called_once_with(path, old)
                self.assertEqual(json.loads(path.read_text(encoding='utf-8')), old)

    def test_temporary_windows_denial_retries_same_pending_data(self):
        original_replace = store.os.replace
        for mode, save in self.writers():
            for code in (5, 32):
                with self.subTest(mode=mode, winerror=code):
                    path, old = self.checkpoint(mode)
                    new = dict(old, segments=[dict(index=1), dict(index=2)])
                    attempts = []

                    def replace(source, destination):
                        attempts.append(str(source))
                        self.assertEqual(json.loads(Path(source).read_text(encoding='utf-8')), new)
                        self.assertEqual(json.loads(path.read_text(encoding='utf-8')), old)
                        if len(attempts) <= 2:
                            raise denied(code)
                        return original_replace(source, destination)

                    with patch.object(store.os, 'replace', side_effect=replace), \
                            patch.object(store.time, 'sleep') as backoff:
                        save(path, new)
                    self.assertEqual(len(attempts), 3)
                    self.assertEqual(len(set(attempts)), 1)
                    self.assertEqual(backoff.call_count, 2)
                    self.assertEqual(json.loads(path.read_text(encoding='utf-8')), new)

    def test_persistent_denial_preserves_progress_and_pending_data(self):
        for mode, save in self.writers():
            with self.subTest(mode=mode):
                path, old = self.checkpoint(mode)
                new = dict(old, segments=[dict(index=1), dict(index=2)])
                sources = []

                def replace(source, destination):
                    sources.append(Path(source))
                    raise denied(5)

                with patch.object(store.os, 'replace', side_effect=replace), \
                        patch.object(store.time, 'sleep'):
                    with self.assertRaises(PermissionError):
                        save(path, new)
                self.assertEqual(len(sources), 8)
                self.assertEqual(json.loads(path.read_text(encoding='utf-8')), old)
                self.assertEqual(json.loads(sources[-1].read_text(encoding='utf-8')), new)
                self.assertFalse(json.loads(path.read_text(encoding='utf-8'))['generation_completed'])
                self.assertNotIn('output', json.loads(path.read_text(encoding='utf-8')))

    def test_unrelated_permission_error_is_not_silently_ignored(self):
        for mode, save in self.writers():
            with self.subTest(mode=mode):
                path, old = self.checkpoint(mode)
                with patch.object(store.os, 'replace', side_effect=denied(87)) as replace, \
                        patch.object(store.time, 'sleep') as backoff:
                    with self.assertRaises(PermissionError):
                        save(path, dict(old, segments=[]))
                self.assertEqual(replace.call_count, 1)
                backoff.assert_not_called()
                self.assertEqual(json.loads(path.read_text(encoding='utf-8')), old)

    def test_stale_writing_file_is_preserved_instead_of_reused(self):
        path, old = self.checkpoint('reference')
        stale = path.with_name('manifest.json.writing')
        stale.write_text('previous interrupted checkpoint retained', encoding='utf-8')
        reference._save(path, old)
        self.assertEqual(stale.read_text(encoding='utf-8'), 'previous interrupted checkpoint retained')
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')), old)

    def test_parallel_writers_and_progress_reader_only_observe_complete_json(self):
        path, _ = self.checkpoint('shared')
        stop = threading.Event()
        observed, failures = [], []

        def read_progress():
            while not stop.is_set():
                try:
                    data = json.loads(path.read_text(encoding='utf-8'))
                    if 'token' in data:
                        self.assertEqual(data['payload'], [data['token']] * 128)
                    observed.append(data)
                except PermissionError:
                    # Readers can also meet an OS sharing denial; never parse partial data.
                    pass
                except Exception as error:
                    failures.append(error)
                    stop.set()
                time.sleep(0.001)

        def write_progress(mode, save):
            for index in range(12):
                token = mode + ':' + str(index)
                save(path, dict(token=token, payload=[token] * 128,
                                generation_completed=False, segments=[]))

        reader = threading.Thread(target=read_progress)
        reader.start()
        try:
            with ThreadPoolExecutor(max_workers=3) as workers:
                futures = [workers.submit(write_progress, mode, save) for mode, save in self.writers()]
                for future in futures:
                    future.result(timeout=15)
        finally:
            stop.set()
            reader.join(timeout=5)
        self.assertFalse(reader.is_alive())
        self.assertFalse(failures, repr(failures))
        self.assertTrue(observed)
        final = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(final['payload'], [final['token']] * 128)
        self.assertFalse(final['generation_completed'])


if __name__ == '__main__':
    unittest.main()
