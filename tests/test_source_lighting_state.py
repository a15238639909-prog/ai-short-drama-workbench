"""Explicit story-plan light state is independent of observed video state."""
import unittest

from cores.source_lighting_state import declared_lighting_states, safe_style_description, validate_lighting_description


def row(text, scene='修复室'):
    return dict(scene_hint=scene, text=text)


class SourceLightingTests(unittest.TestCase):
    def context(self):
        return declared_lighting_states([row('顶灯突然熄灭。许宁走到后门。'), row('林岚从后门走到巷口。', '后巷')], 1)

    def validate(self, text, context=None, scene='修复室', facts=None, channel='lighting'):
        return validate_lighting_description(text, source_context=context or declared_lighting_states([row('顶灯突然熄灭。')], 0),
            scene_facts=facts or dict(light='高窗与门缝冷色微光，室内大面积深暗'), current_scene=scene, channel=channel)

    def test_source_events_include_current_and_explicit_on_overrides_off(self):
        rows = [row('顶灯突然熄灭。'), row('林岚打开台灯。'), row('顶灯再次亮起。')]
        before = declared_lighting_states(rows, 0)
        self.assertEqual([(x['source'], x['status']) for x in before['states']], [('顶灯', 'off')])
        after = declared_lighting_states(rows, 2)
        self.assertEqual({x['source']: x['status'] for x in after['states']}, {'顶灯': 'on', '台灯': 'on'})
        self.assertEqual(before['kind'], 'declared_story_plan')
        self.assertNotIn('verified', before)
        aliases = declared_lighting_states([row('顶灯熄灭。'), row('室内灯光重新亮起。')], 1)
        self.assertEqual([(x['source'], x['status']) for x in aliases['states']], [('顶灯', 'on')])

    def test_dialogue_intention_negation_and_non_light_closing_do_not_turn_on_lights(self):
        dialogue = row('顶灯突然熄灭。\n【D1】林岚：把顶灯打开。')
        dialogue['director'] = dict(blocks=[[0, 12, dialogue['text'], [1]]], says=[['林岚', '把顶灯打开。']])
        cases = [dialogue, row('关上木门，合上书本。'), row('她准备打开台灯。'), row('顶灯没有熄灭。'), row('她说“把台灯打开”。')]
        context = declared_lighting_states(cases, 4)
        self.assertEqual([(x['source'], x['status']) for x in context['states']], [('顶灯', 'off')])

    def test_same_named_lamps_in_different_scenes_are_separate(self):
        context = declared_lighting_states([row('顶灯熄灭。'), row('顶灯亮起。', '门厅')], 1)
        self.assertEqual({x['scene']: x['status'] for x in context['states']}, {'修复室': 'off', '门厅': 'on'})

    def test_direct_and_generic_indoor_light_cannot_resurrect(self):
        for text in ('顶灯暖光照亮肩部。', '钨丝灯光在耳饰上形成高光。', '室内暖光在木桌上反射。'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.validate(text)

    def test_known_switch_off_sound_is_not_a_restored_light(self):
        self.assertEqual(self.validate('顶灯熄灭时的电流滋滋声。', channel='soundscape'), '顶灯熄灭时的电流滋滋声。')

    def test_door_transition_keeps_adjacent_room_dark_but_allows_outside_warmth(self):
        context = self.context()
        self.assertEqual(context['linked_scenes'], ['后巷', '修复室'])
        with self.assertRaises(ValueError):
            self.validate('门缝透出的室内暖光照亮侧脸。', context, scene='后巷')
        for text in ('远处窗光形成温暖光晕。', '门外路灯暖光照亮湿地面。', '远处钨丝灯暖光照亮街面。', '手电光照亮路面。'):
            self.assertEqual(self.validate(text, context, scene='后巷'), text)

    def test_unrelated_scene_and_no_context_do_not_ban_warm_light(self):
        context = declared_lighting_states([row('顶灯熄灭。'), row('她停在咖啡馆内。', '咖啡馆')], 1)
        self.assertEqual(self.validate('室内暖光照亮木桌。', context, scene='咖啡馆'), '室内暖光照亮木桌。')
        self.assertEqual(validate_lighting_description('室内暖光。', source_context={}, scene_facts={}, current_scene='室内', channel='lighting'), '室内暖光。')

    def test_other_explicit_warm_source_is_preserved_while_ceiling_stays_off(self):
        context = declared_lighting_states([row('顶灯熄灭。她打开台灯。')], 0)
        self.assertEqual(self.validate('台灯暖光照亮纸张。', context), '台灯暖光照亮纸张。')
        self.assertEqual(self.validate('钨丝灯暖光照亮纸张。', context), '钨丝灯暖光照亮纸张。')
        with self.assertRaises(ValueError):
            self.validate('顶灯暖光照亮纸张。', context)

    def test_style_fallback_preserves_medium_and_grain(self):
        context = declared_lighting_states([row('顶灯突然熄灭。')], 0)
        result = safe_style_description('写实悬疑电影摄影，冷青环境光与钨丝灯暖光，轻微胶片颗粒，自然镜头锐度',
            source_context=context, scene_facts={}, current_scene='修复室')
        self.assertEqual(result, '写实悬疑电影摄影，轻微胶片颗粒，自然镜头锐度')

    def test_handlight_has_independent_state(self):
        context = declared_lighting_states([row('周远关掉手电筒。')], 0)
        with self.assertRaises(ValueError):
            self.validate('手电光照亮地面。', context)
        context = declared_lighting_states([row('周远关掉手电筒。'), row('周远重新打开手电筒。')], 1)
        self.assertEqual(self.validate('手电光照亮地面。', context), '手电光照亮地面。')


if __name__ == '__main__':
    unittest.main()
