"""In-memory checks for conservative shot view consistency."""
import copy
import unittest

from cores.story_video_shot_consistency import assert_shot_consistency


def references():
    return [dict(role='character', subject=1, character_id='person_a', name='艾宁')]


class ShotConsistencyTests(unittest.TestCase):
    def test_project50_back_view_and_visible_eyelash_shadow_are_rejected(self):
        text = ('[Shot 1] 中景。她背对镜头，黑色盘发清晰可见，身穿浅色丝质长裙。'
                '她缓缓垂下眼帘，睫毛在苍白的脸颊上投下淡淡的阴影。随后双手交叠身前。')
        with self.assertRaisesRegex(ValueError, '第1镜人物艾宁') as caught:
            assert_shot_consistency(text, references())
        self.assertEqual(caught.exception.evidence['shot'], 1)
        self.assertIn('背对镜头', caught.exception.evidence['back_clause'])
        self.assertIn('睫毛', caught.exception.evidence['face_clause'])

    def test_subject_label_and_explicit_visible_face_are_rejected(self):
        with self.assertRaises(ValueError):
            assert_shot_consistency('[Shot 1] <Subject 1>背向镜头。<Subject 1>的正脸清晰可见。', references())

    def test_short_ascii_ids_do_not_match_subject_labels_or_at_markers(self):
        people = [dict(role='character', subject=1, character_id='a', name='艾宁'),
                  dict(role='character', subject=2, character_id='b', name='苏禾')]
        text = ('[Shot 1] <Subject 1>背对镜头，<Subject 1>的睫毛在脸颊上投下阴影。'
                '\n[Shot 2] At 00:02.000, 双人中景。')
        with self.assertRaises(ValueError):
            assert_shot_consistency(text, people)
        assert_shot_consistency('[Shot 1] <Subject 1>背对镜头。<Subject 2>的正脸清晰可见。', people)
        with self.assertRaises(ValueError):
            assert_shot_consistency('[Shot 1] a 背对镜头。a 的正脸清晰可见。', people)

    def test_real_turn_before_face_is_allowed(self):
        for transition in ('她转过身', '她回过头', '她侧过脸', '她不再背对镜头'):
            with self.subTest(transition=transition):
                assert_shot_consistency('[Shot 1] 艾宁背对镜头。' + transition + '，她的正脸清晰可见。', references())

    def test_camera_moves_to_front_are_allowed(self):
        for transition in ('镜头绕到她的正面', '机位移至人物侧前方', '镜头从背后绕至正面'):
            with self.subTest(transition=transition):
                assert_shot_consistency('[Shot 1] 艾宁背对镜头。' + transition + '，她的双眼清晰可见。', references())

    def test_later_shot_resets_orientation(self):
        assert_shot_consistency('[Shot 1] 艾宁背对镜头。\n[Shot 2] At 00:03.500, 正脸特写。', references())

    def test_different_persons_face_is_allowed(self):
        people = references() + [dict(role='character', subject=2, character_id='person_b', name='苏禾')]
        assert_shot_consistency('[Shot 1] 艾宁背对镜头。苏禾的正脸清晰可见。', people)

    def test_ambiguous_multiple_person_pronoun_is_not_guessed(self):
        people = references() + [dict(role='character', subject=2, name='苏禾')]
        assert_shot_consistency('[Shot 1] 艾宁和苏禾背对镜头。她的正脸清晰可见。', people)
        assert_shot_consistency('[Shot 1] 艾宁背对镜头。苏禾背对镜头。她的正脸清晰可见。', people)

    def test_explicit_same_person_in_multiple_person_shot_is_rejected(self):
        people = references() + [dict(role='character', subject=2, name='苏禾')]
        with self.assertRaises(ValueError):
            assert_shot_consistency('[Shot 1] 艾宁背对镜头。苏禾看向门口。艾宁的正脸清晰可见。', people)

    def test_unbound_other_person_disables_pronoun_guessing(self):
        assert_shot_consistency('[Shot 1] 艾宁背对镜头。另一名女子走来。她的正脸清晰可见。', references())

    def test_mirror_reflection_and_split_screen_are_allowed(self):
        for surface in ('镜子', '镜面', '倒影', '反射', '分屏', '画中画'):
            with self.subTest(surface=surface):
                assert_shot_consistency('[Shot 1] 艾宁背对镜头。' + surface + '中，她的正脸清晰可见。', references())

    def test_back_view_speech_and_head_lowering_do_not_require_face_visibility(self):
        assert_shot_consistency('[Shot 1] 艾宁背对镜头，缓缓垂下眼帘，轻声说话。画外声音清晰。', references())

    def test_negative_face_instruction_is_not_a_conflict(self):
        for instruction in ('不要拍正脸特写', '正脸不可见', '避免双眼特写', '不显示正脸特写'):
            with self.subTest(instruction=instruction):
                assert_shot_consistency('[Shot 1] 艾宁背对镜头。' + instruction + '。', references())

    def test_negated_turn_does_not_reveal_face(self):
        for transition in ('她没有转身', '她始终不回头', '镜头不绕到正面'):
            with self.subTest(transition=transition), self.assertRaises(ValueError):
                assert_shot_consistency('[Shot 1] 艾宁背对镜头。' + transition + '，她的正脸清晰可见。', references())

    def test_turning_away_then_visible_face_remains_a_conflict(self):
        with self.assertRaises(ValueError):
            assert_shot_consistency('[Shot 1] 艾宁转身背对镜头。她的正脸清晰可见。', references())

    def test_written_quotes_do_not_establish_orientation(self):
        assert_shot_consistency('[Shot 1] 牌子写着“艾宁背对镜头”。艾宁的正脸清晰可见。', references())

    def test_hand_height_is_neither_changed_nor_restricted(self):
        for action in ('双手抬到胸前', '右手高举过头顶', '双手垂在身侧'):
            text = '[Shot 1] 艾宁背对镜头，' + action + '。'
            people = references()
            before = copy.deepcopy((text, people))
            self.assertIsNone(assert_shot_consistency(text, people))
            self.assertEqual((text, people), before)

    def test_scene_reference_does_not_add_another_person(self):
        people = references() + [dict(role='scene_anchor', subject=2, name='大厅')]
        with self.assertRaises(ValueError):
            assert_shot_consistency('[Shot 1] 她背对镜头。她的眼睛清晰可见。', people)

    def test_unknown_identity_without_references_is_not_guessed(self):
        assert_shot_consistency('[Shot 1] 她背对镜头。她的正脸清晰可见。')


if __name__ == '__main__':
    unittest.main()
