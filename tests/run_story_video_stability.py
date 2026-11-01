"""Bounded story-video regression suite. Scratch files are retained, no AI."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from cores import runtime_reset


class IsolatedResult(unittest.TextTestResult):
    """Never use production reset state, or leak a test generation to another."""
    def startTest(self, test):
        self.state_patch = patch.object(runtime_reset, 'STATE',
            Path(tempfile.mkdtemp(prefix='story_suite_reset_')) / 'runtime_reset.json')
        self.state_patch.start()
        runtime_reset.bind_thread(None)
        super().startTest(test)

    def stopTest(self, test):
        runtime_reset.bind_thread(None)
        self.state_patch.stop()
        super().stopTest(test)


def main():
    folder = BASE / 'outputs/release_checks/story_video_stability_20261026'
    folder.mkdir(parents=True, exist_ok=True)
    modules = ['tests.' + path.stem for path in sorted((BASE/'tests').glob('test_story_video*.py'))]
    modules += ['tests.test_episode_contract', 'tests.test_scene_reference_policy',
                'tests.test_pov_reference_binding', 'tests.test_runtime_cache_reset']
    loader, suite, seen = unittest.TestLoader(), unittest.TestSuite(), set()
    duplicates = 0

    def add(tests):
        nonlocal duplicates
        for test in tests:
            if isinstance(test, unittest.TestSuite):
                add(test)
            elif test.id() in seen:
                duplicates += 1
            else:
                seen.add(test.id())
                suite.addTest(test)

    for module in modules:
        add(loader.loadTestsFromName(module))
    with (folder/'full_python_checks.log').open('w', encoding='utf-8') as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=IsolatedResult).run(suite)
    summary = dict(modules=len(modules), unique_tests=result.testsRun, duplicates_omitted=duplicates,
        failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
        successful=result.wasSuccessful(), failed_ids=[test.id() for test, _ in result.failures+result.errors],
        actual_ai_calls=0, synthetic_media_only=True)
    (folder/'python_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print('SUMMARY', json.dumps(summary, ensure_ascii=False))
    for test, trace in result.failures+result.errors:
        print('FAILED', test.id(), '\n', trace[-3000:])
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
