"""CPU-only file, handoff and merge regression tests; sandboxes are retained."""
import copy
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from cores import video_continuity as vc, episode_merge as em


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='video_handoff_test_'))
        self.video = self.root / 'prior.mp4'
        self.video.write_bytes(b'fixture')

    def segment(self, n, **values):
        return dict(no=n, video=str(self.video), **values)

    def timeline(self, segments):
        return {'scenes': [{'no': 1, 'segments': segments}]}

    def test_immediate_previous_never_jumps_missing_clip(self):
        tl = self.timeline([self.segment(1), {'no': 2}, {'no': 3}])
        self.assertIsNone(vc.previous_segment({}, tl, 1, 1, 3))
        tl['scenes'][0]['segments'].pop(1)
        self.assertIsNone(vc.previous_segment({}, tl, 1, 1, 3))
        self.assertEqual(vc.previous_segment({}, tl, 1, 1, 2)['segment_no'], 1)

    def test_cross_episode_requires_complete_previous_episode(self):
        previous = self.timeline([self.segment(1), self.segment(2)])
        previous['slicing'] = {'slices': [{}, {}]}
        story = {'saga': {'episodes': [{'no': 1, 'timeline': previous}]}}
        current = self.timeline([{'no': 1}])
        self.assertEqual(vc.previous_segment(story, current, 2, 1, 1)['episode'], 1)
        previous['scenes'][0]['segments'][0]['stale_video'] = True
        self.assertIsNone(vc.previous_segment(story, current, 2, 1, 1))
        previous['scenes'][0]['segments'].pop(0)
        self.assertIsNone(vc.previous_segment(story, current, 2, 1, 1))

    def test_cross_scene_global_numbering_does_not_skip_gap(self):
        tl = {'scenes': [{'no': 1, 'segments': [self.segment(5)]},
                         {'no': 2, 'segments': [{'no': 7}]}]}
        self.assertIsNone(vc.previous_segment({}, tl, 1, 2, 7))
        tl['scenes'][1]['segments'][0]['no'] = 6
        self.assertEqual(vc.previous_segment({}, tl, 1, 2, 6)['segment_no'], 5)

    def test_location_change_uses_frame_without_copying_old_room(self):
        previous = {'episode': 1, 'segment_no': 2, 'scene_no': 1,
                    'segment': self.segment(2, scene='旧房间')}
        prompt = 'subject_definitions:\n<Subject 1> person\n\nsummary:\ncontinue\n\nretention_analysis:\nidentity\n\ndetailed_description:\n[Shot 1] The scene opens.\n\noverall_soundscape:\nquiet\n\nnon_diegetic_music:\nNone.'
        result = vc.handoff_prompt(prompt, 3,
                                  {'scene': '街道', 'chars': ['阿宁']}, previous)
        self.assertIn('<Picture 3>是紧接本段的前一段视频末帧', result)
        self.assertIn('此前地点中已出现实体的状态参考', result)
        self.assertNotIn('Do not', result)
        detail = result.split('detailed_description:\n', 1)[1].split('\n\noverall_soundscape:', 1)[0]
        self.assertTrue(detail.startswith('[Shot 1]'))
        self.assertNotIn('Declared shot context', result)
        self.assertIn('<Picture 3>', result.split('\n\nsummary:', 1)[0])
        self.assertIn('<Picture 3>', result.split('retention_analysis:', 1)[1].split('detailed_description:', 1)[0])
        self.assertNotIn('Continuity handoff', result.split('non_diegetic_music:', 1)[1])

    def test_final_reference_check_rejects_unbound_image_number(self):
        with self.assertRaisesRegex(ValueError, '未绑定'):
            vc.validate_prompt_references('<Subject 1> from <Picture 3>', ['one.png', 'two.png'])
        vc.validate_prompt_references('<Subject 1> from <Picture 1>', ['one.png'])

    def test_transitional_set_and_historical_appearance_do_not_reset_state(self):
        previous = {'episode': 1, 'segment_no': 2, 'scene_no': 1,
                    'segment': self.segment(2, scene='店里')}
        fields = ['subject_definitions', 'summary', 'retention_analysis', 'detailed_description',
                  'overall_soundscape', 'non_diegetic_music']
        values = ['<Subject 1> is 阿宁, the character from <Picture 1>.\n<Subject 2> is 院子, the environment from <Picture 2>.',
                  'walk outside', '<Subject 1> fully_preserved\n<Subject 2> fully_preserved',
                  '[Shot 1] They walk outside with the closed box.', 'quiet', 'N/A']
        prompt = '\n\n'.join(k + ':\n' + v for k, v in zip(fields, values))
        director = {'scene_transition': {'from': '店里', 'to': '院子'}, 'start_state': '门已打开，盒子在林海手中'}
        merged = vc.handoff_prompt(prompt, 3, {'scene': '院子'}, previous, director, appearance_number=4)
        scoped = vc.scope_prompt(merged, {'plan': {'flow': 'source-v1'}}, director)
        self.assertNotIn('Do not copy the previous room', scoped)
        self.assertIn('形状、色彩、材质和标记参考', scoped)
        self.assertNotIn('Reference scope (', scoped)
        self.assertNotIn('Declared shot context', scoped)
        self.assertNotIn('Do not', scoped)
        self.assertIn('门已打开', scoped)
        vc.validate_prompt_references(scoped, ['actor', 'yard', 'last', 'appearance'])

    def test_old_scene_reference_retains_room_without_restoring_movable_objects(self):
        fields = ['subject_definitions', 'summary', 'retention_analysis', 'detailed_description',
                  'overall_soundscape', 'non_diegetic_music']
        values = ['<Subject 1> is 阿宁, the character from <Picture 1>.\n<Subject 2> is 店里, the environment from <Picture 2>.',
                  'close the box', '<Subject 1> fully_preserved\n<Subject 2> fully_preserved',
                  '[Shot 1]\n<Subject 1> closes the box. <d>[Chinese] 收好。</d>', 'quiet', 'N/A']
        original = '\n\n'.join(k + ':\n' + v for k, v in zip(fields, values))
        scoped = vc.scope_prompt(original, {'plan': {'flow': 'source-v1'}}, {'start_state': '表已经放入盒内。'})
        self.assertIn('<Subject 2> is 店里, the environment from <Picture 2>.', scoped)
        self.assertNotIn('Reference scope (', scoped)
        self.assertIn('表已经放入盒内', scoped)
        self.assertEqual(scoped.count('<d>[Chinese] 收好。</d>'), 1)
        self.assertNotIn('Reference scope', scoped.split('non_diegetic_music:', 1)[1])
        self.assertEqual(vc.scope_prompt(scoped, {'plan': {'flow': 'source-v1'}}), scoped)
        self.assertEqual(vc.scope_prompt(original, {'plan': {'flow': 'legacy'}}), original)
        declared = vc.scope_prompt(original, {'plan': {'flow': 'source-v1'},
            'source_text': '阿宁双手端盒，灰色衬衫的领口随呼吸微动。'})
        self.assertIn('有翻领、前襟与纽扣的衬衫', declared)
        mentioned = vc.scope_prompt(original, {'plan': {'flow': 'source-v1'},
            'source_text': '阿宁：林海需要一件衬衫。'})
        self.assertNotIn('有翻领、前襟与纽扣的衬衫', mentioned)
        unworn = vc.scope_prompt(original, {'plan': {'flow': 'source-v1'},
            'source_text': '阿宁没有穿着衬衫，换上了蓝色外套。'})
        self.assertNotIn('有翻领、前襟与纽扣的衬衫', unworn)

    def test_signature_changes_with_source_reference_or_predecessor(self):
        segment = {'prompt': 'a', 'seconds': 4}
        previous = {'episode': 1, 'scene_no': 1, 'segment_no': 1, 'segment': self.segment(1)}
        initial = vc.segment_signature(segment, {'steps': 25}, previous)
        self.video.write_bytes(b'different fixture')
        self.assertNotEqual(initial, vc.segment_signature(segment, {'steps': 25}, previous))
        self.assertNotEqual(vc.segment_signature(segment, {}),
                            vc.segment_signature(dict(segment, prompt='b'), {}))


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg required')
class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix='video_media_test_'))
        cls.silent = cls.root / 'silent.mp4'
        cls.sound = cls.root / 'sound.mp4'
        base = ['ffmpeg', '-v', 'error', '-nostdin', '-f', 'lavfi', '-i', 'color=c=blue:s=160x96:r=24:d=2']
        subprocess.run(base + ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(cls.silent)], check=True, capture_output=True, timeout=30)
        subprocess.run(base + ['-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-c:v', 'libx264',
                              '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(cls.sound)],
                       check=True, capture_output=True, timeout=30)

    def test_actual_video_duration_decode_and_damage(self):
        info = vc.validate_video(self.silent, 2)
        self.assertEqual(info['check'], 'decoded')
        self.assertEqual(info['visual_quality'], 'not_reviewed')
        self.assertFalse(info['audio'])
        with self.assertRaisesRegex(ValueError, '时长异常'):
            vc.validate_video(self.silent, 10)
        damaged = self.root / 'damaged.mp4'
        damaged.write_bytes(self.silent.read_bytes()[:50])
        with self.assertRaises(ValueError):
            vc.validate_video(damaged)

    def test_historical_appearance_is_a_decoded_frame_and_cache_is_stable(self):
        from PIL import Image
        previous = {'segment': {'video': str(self.silent)}}
        frame = Path(vc.prepare_handoff(previous))
        with Image.open(frame) as img:
            self.assertEqual(img.size, (160, 96))
            pixel = img.convert('RGB').getpixel((80, 48))
            self.assertGreater(pixel[2], pixel[0] + 100)
        stamp = frame.stat().st_mtime_ns
        self.assertEqual(vc.prepare_handoff(previous), str(frame))
        self.assertEqual(frame.stat().st_mtime_ns, stamp)

    def test_merge_mixed_audio_and_silent_preserves_accepted_film(self):
        target = self.root / 'accepted.mp4'
        target.write_bytes(b'accepted film must remain')
        with patch.object(em, 'episode_videos', return_value=[(1, str(self.silent)), (2, str(self.sound))]), \
                patch.object(em, 'film_title', return_value='fixture'), \
                patch.object(em, '_target_size', return_value=(160, 96)):
            output = em.merge_episode('TEST', 2, dst=str(target))
        self.assertEqual(target.read_bytes(), b'accepted film must remain')
        self.assertNotEqual(output, str(target))
        info = vc.validate_video(output, 4)
        self.assertTrue(info['audio'])

    def test_failed_merge_keeps_accepted_film(self):
        target = self.root / 'kept.mp4'
        target.write_bytes(b'old')
        with patch.object(em, 'episode_videos', return_value=[(1, str(self.root / 'missing.mp4'))]), \
                patch.object(em, 'film_title', return_value='fixture'):
            with self.assertRaisesRegex(RuntimeError, '合成输入视频无效'):
                em.merge_episode('TEST', 2, dst=str(target))
        self.assertEqual(target.read_bytes(), b'old')


if __name__ == '__main__':
    unittest.main()
