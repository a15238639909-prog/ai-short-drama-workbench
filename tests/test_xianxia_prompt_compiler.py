"""CPU integration of wardrobe, staging, scene facts and source action timing."""
import copy
import json
import os
import unittest

os.environ['V41_NO_MODEL'] = '1'

from cores import director_flow, episode_contract, source_actor_staging as staging
from cores import source_shot_compiler as compiler, source_wardrobe_plan as wardrobe
from cores import workbench_story_director as writer
from cores.scene_condition import condition_for_scene
from tests import test_source_shot_compiler as source_fixture


CARDS = [dict(character_id='lin', id='lin', name='林岚', age=34, sex='女',
              role='character', subject=1, _has_image=True,
              appearance='34岁成年女性，深色短发，神情干练。',
              clothing='深灰外套、白衬衫、黑长裤', voice='清晰自然的成年女声')]
REMOVE = '林岚脱下深灰外套，将胶片盒裹进衣料里。'
RAIN = '雨水落在门外的石板上。'


def segment(text, seconds=15, says=(), blocks=None, timing_source='automatic'):
    if blocks is None:
        for number, (speaker, speech) in enumerate(says, 1):
            text += '\n【D%d】%s：%s' % (number, speaker, speech)
        blocks = [[0, seconds, text, list(range(1, len(says) + 1))]]
    return dict(text=text, seconds=seconds, scene_hint='竹庭', chars=['林岚'],
        director=dict(flow='source-v1', camera='平视中景，按原文呈现动作。', purpose='呈现当前事件。',
            cast=['林岚'], start_state='', target_state='', blocks=blocks,
            says=[list(line) for line in says], timing_source=timing_source,
            source_contract_version=episode_contract.VERSION))


def raw_payload(text=RAIN + REMOVE, says=()):
    sl = segment(text, says=says)
    return dict(seconds=sl['seconds'], beat=dict(scene='竹庭',
                    source_order=director_flow.ordered_content(sl['director']), source_contract=sl['director']),
                dialogue_slots=[dict(speaker=speaker, text=speech) for speaker, speech in says],
                story_plan=dict(characters=copy.deepcopy(CARDS)), canonical_references=copy.deepcopy(CARDS),
                source_wardrobe_plan=wardrobe.build_start_plan([sl], 0, CARDS),
                visual_style='写实摄影', scene_facts={})


def write(slices):
    calls = []
    settings = dict(style='写实摄影',
                    _director_reference_paths={'林岚': __file__, '竹庭': __file__})

    def q(system, user, **options):
        payload = json.loads(user)
        if 'field_values' in payload:
            raise AssertionError('Neutral CPU photography unexpectedly required repair: ' + str(payload['field_values']))
        calls.append(copy.deepcopy(payload))
        result = source_fixture.model_reply(payload)
        result['soundscape'] = '现场轻微的环境底噪。'
        for shot in result['shots']:
            shot['acting'] = []
            shot['visual']['lighting'] = '柔和环境光照亮人物与地面。'
        return json.dumps(result, ensure_ascii=False)

    result = writer.make_prompts(slices, copy.deepcopy(CARDS), ['竹庭'], q, settings=settings)
    return result, calls


def rain_facts():
    condition = condition_for_scene(
        {'settings': {'initial_story_input': '暴雨持续。林岚经过整洁的库房与走廊来到有顶装卸台。'}},
        {'name': '装卸台', 'auto_made': True}, scene_names=('库房', '走廊', '装卸台'))
    return dict(name='装卸台', scene_condition=condition)


class XianxiaPromptCompilerTests(unittest.TestCase):
    def test_writer_routes_planned_wardrobe_and_bundle_to_next_clip(self):
        slices = [segment(REMOVE), segment('林岚抱着裹着外套的胶片盒走向门旁。')]
        before = copy.deepcopy(slices)
        rows, calls = write(slices)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]['current_wardrobe']['characters']['lin']['worn'], ['白衬衫', '黑长裤'])
        self.assertEqual(calls[1]['current_wardrobe']['evidence_kind'], 'planned_source')
        retention = rows[1]['prompt'].split('retention_analysis:\n', 1)[1].split('\n\ndetailed_description:', 1)[0]
        clothing_lines = [line for line in retention.splitlines() if '当前衣着为' in line]
        self.assertTrue(clothing_lines)
        self.assertTrue(all('深灰外套' not in line for line in clothing_lines))
        self.assertIn('胶片盒外裹着深灰外套', retention)
        self.assertFalse(any(row['source_fallback'] for row in rows))
        self.assertEqual([sl['text'] for sl in slices], [sl['text'] for sl in before])

    def test_unit_opening_and_ending_wardrobe_follow_actual_source_order(self):
        payload = raw_payload()
        before = copy.deepcopy(payload)
        request = compiler.model_payload(payload)
        rain, removal = request['source_units']
        self.assertIn('深灰外套', request['current_wardrobe']['characters']['lin']['worn'])
        self.assertIn('深灰外套', rain['ending_wardrobe']['characters']['lin']['worn'])
        self.assertIn('深灰外套', removal['opening_wardrobe']['characters']['lin']['worn'])
        self.assertNotIn('深灰外套', removal['ending_wardrobe']['characters']['lin']['worn'])
        self.assertEqual(removal['ending_wardrobe']['props'][0]['object'], '胶片盒')
        self.assertEqual(payload, before)

    def test_removed_coat_cannot_return_as_character_hem_or_fabric(self):
        plan = wardrobe.build_start_plan([dict(text=REMOVE), dict(text='林岚走向门旁。')], 1, CARDS)
        for value in ('林岚的外套下摆轻轻摆动。', '外套布纹贴合林岚的腰身。'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                compiler._planned_garment_text(value, plan)

    def test_removed_coat_can_be_explicitly_described_as_bundle(self):
        plan = wardrobe.build_start_plan([dict(text=REMOVE), dict(text='林岚抱着胶片盒。')], 1, CARDS)
        value = '裹着深灰外套的胶片盒轮廓清楚，包裹表面的布纹自然。'
        self.assertEqual(compiler._planned_garment_text(value, plan), value)

    def test_staging_cannot_copy_actual_source_handling_or_retract_arm(self):
        units = [dict(id='U001', kind='action',
                      text='林岚接过胶片盒，指尖按了按厚实的布料，确认形状稳固。')]
        for value in ('指尖按了按厚实的布料，确认形状稳固。', '手臂缓缓收回。'):
            entry = dict(unit_id='U001', actor='林岚', preparation='', execution=value, reaction='')
            with self.subTest(value=value), self.assertRaises(ValueError):
                staging.validate_staging([entry], units, ['林岚'])

    def test_staging_keeps_fingertip_effort_without_a_second_object_action(self):
        entry = dict(unit_id='U001', actor='林岚', preparation='眉间轻收。',
                     execution='指腹轻微发力，手腕短暂停顿。', reaction='呼吸稍缓。')
        result = staging.validate_staging([entry], [dict(id='U001', kind='action', text='林岚扶稳胶片盒。')], ['林岚'])
        self.assertEqual(result[0]['execution'], entry['execution'])

    def test_scene_state_rejects_unsupported_clutter_and_post_rain_sun(self):
        facts = rain_facts()
        self.assertEqual(facts['scene_condition']['weather']['kind'], 'rain')
        for value in ('有顶装卸台地面堆满杂物。', '有顶装卸台雨后初晴，阳光洒在地面上。'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                compiler._scene_state_text(value, facts, complete=True)

    def test_scene_state_accepts_positive_clean_space_in_current_rain(self):
        value = '有顶装卸台的屋顶完整，地面整洁，通道清晰，雨棚外暴雨持续。'
        self.assertEqual(compiler._scene_state_text(value, rain_facts(), complete=True), value)

    def test_optional_photography_needs_no_full_weather_or_building_damage_from_coat(self):
        for value in ('柔和侧光照亮人物脸部。', '旧外套表面的布纹清楚。'):
            with self.subTest(value=value):
                self.assertEqual(compiler._scene_state_text(value, rain_facts(), complete=False), value)

    def test_complex_handling_has_more_time_than_rain_and_exact_speech_remains(self):
        source = RAIN + '林岚脱下深灰外套，将胶片盒包裹住，系紧衣袖，递给同事。'
        rows, _ = write([segment(source, seconds=15, says=[('林岚', '收好了。')])])
        audit = rows[0]['director_compilation']['source_shot_audit']
        lookup = {unit['id']: unit for unit in audit['units']}
        durations = {lookup[shot['unit_ids'][0]]['text']: shot['end'] - shot['start'] for shot in audit['shots']}
        complex_text = next(text for text in durations if '脱下' in text)
        self.assertGreater(durations[complex_text], durations[RAIN])
        self.assertEqual(rows[0]['prompt'].count('<d>[Chinese] 收好了。</d>'), 1)
        self.assertEqual(audit['shots'][-1]['end'], 15)

    def test_manual_time_windows_and_original_dialogue_stay_unchanged(self):
        text = RAIN + '\n' + REMOVE + '\n【D7】林岚：收好了。'
        blocks = [[0, 2, RAIN, []], [2, 15, REMOVE + '\n【D7】林岚：收好了。', [7]]]
        sl = segment(text, says=[('林岚', '收好了。')], blocks=copy.deepcopy(blocks), timing_source='user')
        rows, calls = write([sl])
        self.assertEqual(sl['director']['blocks'], blocks)
        units = calls[0]['source_units']
        self.assertEqual((units[0]['block_start'], units[0]['block_end']), (0, 2))
        self.assertTrue(all((u['block_start'], u['block_end']) == (2, 15) for u in units[1:]))
        shots = rows[0]['director_compilation']['source_shot_audit']['shots']
        self.assertEqual((shots[0]['start'], shots[0]['end']), (0, 2))
        self.assertEqual(shots[1]['start'], 2)
        self.assertEqual(shots[-1]['end'], 15)
        self.assertEqual(rows[0]['prompt'].count('<d>[Chinese] 收好了。</d>'), 1)


if __name__ == '__main__':
    unittest.main()
