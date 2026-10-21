"""Reference-only cast compatibility; no services or local models are invoked."""
import copy
import unittest

from cores import story_video_characters as characters
from cores import story_video_reference as reference
from cores import story_video_reference_prompts as prompts


def fixture():
    cards = [dict(id=identity, name=name, image_path=identity + '.png',
                  personality='勇敢并关心同伴', voice='清朗自然的少年声')
             for identity, name in (('hero', '阿澈'), ('mage', '星砂'))]
    source = '阿澈说：“走吧。”\n星砂说：“来了。”'
    contract = characters.prepare_source_contract(source, cards)
    plan = dict(title='无模型验证', logline='两人与无声守卫经过遗迹', ending='两人离开遗迹',
        characters=[dict(id=row['id'], name=row['name'], appearance='依照参考图', role='同伴') for row in cards],
        props=[dict(id='guardian', name='守卫石像', appearance='持斧的无声石像', count=1)],
        invariants=[], beats=[dict(index=index, seconds=15, goal='经过遗迹', action='两人观察守卫石像并继续前行。',
            turn='决定离开', emotion='警觉', ending='两人向出口移动', cast=['hero', 'mage', 'guardian'],
            dialogue=[dict(speaker=identity, text=text)])
            for index, (identity, text) in enumerate((('hero', '走吧。'), ('mage', '来了。')), 1)])
    payload = prompts.plan_request(source, '真人写实电影摄影', 2, 15, {})[1]
    return plan, payload, contract, cards


class ReferencePropCastTests(unittest.TestCase):
    def test_declared_silent_prop_is_allowed_without_mutating_original(self):
        plan, payload, contract, cards = fixture()
        snapshots = copy.deepcopy((plan, payload, contract, cards))
        reference.validate_reference_plan(plan, payload, contract, cards)
        self.assertEqual((plan, payload, contract, cards), snapshots)
        self.assertIn('guardian', plan['beats'][0]['cast'])

    def test_unknown_cast_id_still_fails(self):
        plan, payload, contract, cards = fixture()
        plan['beats'][0]['cast'].append('unknown_guardian')
        with self.assertRaisesRegex(ValueError, '分段人物或时长不符'):
            reference.validate_reference_plan(plan, payload, contract, cards)

    def test_prop_cannot_speak_even_if_it_appears_in_original_cast(self):
        for speaker in ('guardian', '守卫石像'):
            plan, payload, contract, cards = fixture()
            plan['beats'][0]['dialogue'][0]['speaker'] = speaker
            with self.subTest(speaker=speaker), self.assertRaisesRegex(ValueError, '对白说话者必须属于本段人物'):
                reference.validate_reference_plan(plan, payload, contract, cards)

    def test_character_membership_timing_and_source_dialogue_remain_strict(self):
        for invalid in ('missing_speaker', 'seconds', 'source_words', 'source_character'):
            plan, payload, contract, cards = fixture()
            if invalid == 'missing_speaker':
                plan['beats'][0]['cast'].remove('hero')
            elif invalid == 'seconds':
                plan['beats'][0]['seconds'] = 14
            elif invalid == 'source_words':
                plan['beats'][0]['dialogue'][0]['text'] = '快走。'
            else:
                plan['characters'][0]['name'] = '陌生人'
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                reference.validate_reference_plan(plan, payload, contract, cards)

    def test_prop_character_id_overlap_cannot_remove_a_real_character(self):
        plan, payload, contract, cards = fixture()
        plan['props'].append(dict(id='hero', name='重叠测试', appearance='重复ID仅用于验证', count=1))
        reference.validate_reference_plan(plan, payload, contract, cards)
        self.assertIn('hero', plan['beats'][0]['cast'])


if __name__ == '__main__':
    unittest.main()
