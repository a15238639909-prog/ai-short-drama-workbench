"""Read-only discovery/filter regressions; retained fixtures, no model invocation."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cores.finished_products import collect


class FinishedProductsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='finished_products_'))

    def file(self, relative, data=b'fixture'):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    def record(self, relative, data):
        return self.file(relative, json.dumps(data).encode('utf-8'))

    def test_all_composition_sources_exclude_intermediates(self):
        self.file('outputs/成片_测试_第1话.mp4')
        self.file('productions/video/拼接/final.mp4')
        movie = self.file('outputs/story_video/story/movie/film.mp4')
        revision = self.file('outputs/story_video/story/movie_revision_1/film.mp4')
        self.record('outputs/story_video/story/manifest.json', {
            'plan': {'title': '东方仙侠'}, 'history': [{'output': {'output_path': movie}}],
            'output': {'output_path': revision, 'seconds': 30}})
        motion = self.file('outputs/motion/job/transferred.mp4')
        self.record('outputs/motion/job/result.json', {'output_path': motion, 'seconds': 8})
        self.file('outputs/story_video/story/movie/raw.mp4')
        self.file('outputs/motion/job/comparison.mp4')
        self.file('outputs/motion/job/original.mp4')
        self.file('outputs/motion/failed/transferred.mp4')
        self.file('outputs/合成中_attempt.mp4')
        items = collect(self.root)
        self.assertEqual(len(items), 5)
        self.assertEqual({item['source'] for item in items}, {'主流程合成', '视频拼接', '故事视频', '动作迁移'})
        self.assertTrue(all(item['extra']['finished'] for item in items))
        self.assertEqual(next(item for item in items if item['path'] == revision)['extra']['title'], '东方仙侠')

    def test_existing_records_deduplicate_and_reject_missing_or_external(self):
        path = self.file('productions/video/test/final.mp4')
        self.record('data/productions/a.json', {'output': {'path': path, 'duration': 10}})
        self.record('data/productions/b.json', {'kind': 'video', 'status': 'completed', 'files': [path]})
        self.record('data/productions/missing.json', {'output': {'path': 'missing.mp4'}})
        self.record('outputs/motion/bad/result.json', {'output_path': str(self.root.parent / 'external.mp4')})
        self.assertEqual(len(collect(self.root)), 1)

    def test_refresh_finds_new_movies_and_ignores_invalid_records(self):
        self.file('outputs/motion/corrupt/result.json', b'{')
        self.assertEqual(collect(self.root), [])
        self.file('outputs/成片_后来合成.mp4')
        self.assertEqual(len(collect(self.root)), 1)

    def test_history_finished_filter_pagination_and_all_preserved(self):
        from api import history_api as api
        self.file('outputs/成片_一.mp4')
        self.file('outputs/成片_二.mp4')
        self.file('outputs/clip.mp4')
        with patch.multiple(api, ROOT=self.root, _V41_DIRS=[self.root / 'outputs'],
                            _LEGACY_ARCHIVE=self.root / 'absent', _LEGACY_VIDEO_OUT=self.root / 'absent',
                            _LEGACY_PROMPT_IMG=self.root / 'absent', _LEGACY_VCHAR_IMG=self.root / 'absent'), \
                patch.object(api.gen_history, 'records', return_value=[]):
            all_items = api.history_list(None, '', '')['data']
            finished = api.history_list(None, '', 'finished=1&limit=1&offset=1')['data']
        self.assertEqual(all_items['total'], 3)
        self.assertEqual(finished['total'], 2)
        self.assertEqual(len(finished['items']), 1)
        self.assertTrue(finished['items'][0]['extra']['finished'])


if __name__ == '__main__':
    unittest.main()
