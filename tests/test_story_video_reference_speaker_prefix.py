"""No-model regression for reference-only, source-evidenced attribution."""
import copy
import hashlib
import unittest

from cores import episode_contract
from cores import story_video_characters as characters


STORY = '''《零魔力的破晓一剑》

最后一束日光正在退入群山。太阳晶体被古老遗迹中的守卫石像吞进胸口；天一黑，山谷就再也等不到清晨。十五岁的莱恩曾因零魔力被拒绝成为勇者，如今却提着铁匠铺的旧铁剑，站在通往遗迹的悬桥前。银发法师米娅和铜发弓手诺尔跟在他身后。诺尔瞄了一眼剑刃，问：“拿这把破剑去救太阳？”莱恩笑道：“太阳又没说只准法师救。”

三人跑上悬桥，脚下的木板突然断裂。诺尔把绳箭射进对岸门柱，米娅抓紧绳索，莱恩一把拉住差点滑落的诺尔。三人借着绳索荡过缺口，跌进遗迹大门。身后的断桥摇晃着停住，门内却传来沉重的石头摩擦声。

大厅中央，一尊举斧石像缓缓起身，胸口的金色太阳晶体照亮三张年轻的脸。米娅挥杖射出紫色法术，光芒却被胸口尽数吸入。石像的眼睛陡然亮起，巨斧砸碎了他们刚刚站过的石阶。米娅急喊：“停手，它在吃魔法！”

诺尔翻滚躲进石柱后，射出一支普通铁箭。刚才拦住法术的光罩竟让箭穿了过去，箭头擦过石像胸甲的旧裂缝，溅出一簇火星。莱恩看见那道裂缝，握紧毫无光芒的铁剑，说：“它挡魔法，不挡铁。掩护我！”米娅随即把一团耀眼的紫光抛向远处，诱得石像转头追赶。

诺尔用绳箭缠住斧柄，把巨斧拉偏。斧头卡进石柱的瞬间，莱恩冲上倾斜的斧柄，跃到石像胸前。他将铁剑插进旧裂缝，双手死死扳住剑柄。剑刃绷弯，太阳晶体终于被撬出。石像眼里的光熄灭，沉重的身躯开始下坠。诺尔甩出绳索，大喊：“接住！”莱恩抱住晶体抓紧绳子，被同伴拽回地面，石像在他们身后轰然跪倒。

三人冲出遗迹，回到断桥边的石台。米娅把太阳晶体嵌进空荡荡的日轮底座，金光沿着遗迹纹路奔向群山。黎明重新涌入山谷，远处村庄的屋顶一片金亮。米娅看着莱恩磨破的手套，轻声说：“这回，谁还说你不能当勇者？”莱恩举起剩下半截的铁剑，正要摆个威风的姿势，诺尔伸手拍掉他肩上的灰，笑道：“勇者，回去先赔我的箭。”三人对视着笑起来，晨风吹起蓝、紫、绿三件披风。'''


def cards(names=('莱恩', '米娅', '诺尔')):
    return [dict(id='person_%d' % index, name=name, image_path='fixture_%d.png' % index,
                 personality='勇敢且关心同伴', voice='清朗自然的少年声')
            for index, name in enumerate(names, 1)]


class ReferenceSpeakerPrefixTests(unittest.TestCase):
    def test_actual_failed_story_keeps_every_word_and_raw_classifier_evidence(self):
        _, original_lines, original_report = episode_contract.prepare_source(
            STORY, [{'name': card['name']} for card in cards()], split_long_dialogue=False)
        original_snapshot = copy.deepcopy(original_report)
        contract = characters.prepare_source_contract(STORY, cards())
        self.assertEqual([row['speaker'] for row in contract['dialogue']],
                         ['诺尔', '莱恩', '米娅', '莱恩', '诺尔', '米娅', '诺尔'])
        self.assertEqual([row['text'] for row in contract['dialogue']], [words for _, words in original_lines])
        report = contract['source_report']
        self.assertEqual(report['source_original'], STORY)
        self.assertEqual(report['source_digest'], hashlib.sha256(STORY.encode('utf-8')).hexdigest())
        self.assertEqual(report['original_classifier_report'], original_snapshot)
        self.assertEqual(original_report, original_snapshot)
        self.assertEqual(report['issues'], [])
        # The base ledger now recognizes this explicit prefix correctly too.
        # Keep the untouched classifier evidence, without requiring its former
        # bug (stealing the next quote's 莱恩 prefix) to remain present.
        self.assertEqual(original_snapshot['dialogue'][0]['speaker'], '诺尔')
        self.assertIn('【D1】诺尔：拿这把破剑去救太阳？', contract['numbered_source'])
        self.assertEqual(len(report['speaker_attribution_audit']), 7)
        for audit in report['speaker_attribution_audit']:
            self.assertEqual(STORY[audit['source_start']:audit['source_end']], audit['evidence'])
            self.assertEqual(STORY[audit['quote_pos']], '“')
        self.assertEqual([row['pos'] for row in report['dialogue']],
                         [row['pos'] for row in original_snapshot['dialogue']])

    def test_renaming_all_characters_has_identical_generic_behavior(self):
        source = STORY
        for old, new in zip(('莱恩', '米娅', '诺尔'), ('阿澈', '星砂', '梓舟')):
            source = source.replace(old, new)
        contract = characters.prepare_source_contract(source, cards(('阿澈', '星砂', '梓舟')))
        self.assertEqual([row['speaker'] for row in contract['dialogue']],
                         ['梓舟', '阿澈', '星砂', '阿澈', '梓舟', '星砂', '梓舟'])

    def test_object_name_is_not_subject_and_explicit_comma_subject_can_change(self):
        cast = cards(('阿澈', '星砂', '梓舟'))
        for source, expected in (
            ('阿澈看着星砂的手，轻声说：“小心。”', '阿澈'),
            ('阿澈举起铁剑，正要摆个姿势，梓舟伸手拍掉他的灰，笑道：“走吧。”', '梓舟'),
            ('阿澈说：“抓紧。”星砂收起法杖。', '阿澈'),
            ('阿澈看见裂缝，握紧剑柄，说：“这里。”星砂把火光抛向远处。', '阿澈'),
        ):
            with self.subTest(source=source):
                contract = characters.prepare_source_contract(source, cast)
                self.assertEqual([row['speaker'] for row in contract['dialogue']], [expected])

    def test_uncertain_pronoun_has_no_inherited_speaker(self):
        for source in ('她说：“门锁住了。”', '阿澈看着星砂的手，她轻声说：“门锁住了。”'):
            with self.subTest(source=source), self.assertRaises(episode_contract.ContractError) as caught:
                characters.prepare_source_contract(source, cards(('阿澈', '星砂')))
            self.assertEqual(caught.exception.details['source_report']['source_original'], source)

    def test_signage_thought_memory_and_unsaid_quotes_are_not_promoted(self):
        source = ('纸上写着“返回”。门上刻着：“请安静”。'
                  '阿澈心里想：“回家吧。”星砂没说出口的“抱歉”，被她咽了回去。'
                  '阿澈想起当年留下的“等我回来”。\n梓舟说：“走吧。”')
        contract = characters.prepare_source_contract(source, cards(('阿澈', '星砂', '梓舟')))
        self.assertEqual([(row['speaker'], row['text']) for row in contract['dialogue']], [('梓舟', '走吧。')])
        self.assertEqual(contract['source_report']['source_original'], source)

    def test_explicit_negated_or_future_speech_cannot_silently_become_dialogue(self):
        for source in ('阿澈没有说：“回家吧。”', '阿澈正要说：“回家吧。”'):
            with self.subTest(source=source):
                try:
                    contract = characters.prepare_source_contract(source, cards(('阿澈', '星砂')))
                except episode_contract.ContractError as error:
                    self.assertEqual(error.details['source_report']['source_original'], source)
                else:
                    self.assertEqual(contract['dialogue'], [])

    def test_explicit_script_and_post_quote_attribution_stay_original(self):
        for source in ('阿澈：出发。', '“出发。”星砂说。'):
            _, old, _ = episode_contract.prepare_source(source, [{'name': card['name']}
                for card in cards(('阿澈', '星砂'))], split_long_dialogue=False)
            contract = characters.prepare_source_contract(source, cards(('阿澈', '星砂')))
            self.assertEqual([(row['speaker'], row['text']) for row in contract['dialogue']], old)


if __name__ == '__main__':
    unittest.main()
