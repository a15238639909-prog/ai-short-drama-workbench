"""Exercise the public authoring dispatcher without running a model or media."""
import copy
import json
import os
os.environ['V41_NO_MODEL']='1'
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from cores import authoring, asset_core, story_core, saga_core, reference_ready, director_flow
from cores import workbench_story_director as release, workbench_director_runtime as runtime
from api import segment_api
from tests.test_workbench_story_director_release import fixture, response


class WorkbenchDirectorDispatchTests(unittest.TestCase):
    def context(self, settings_override=None, slices_override=None, prose_override=None):
        slices, cards, settings=fixture()
        if slices_override is not None:
            slices = copy.deepcopy(slices_override)
        settings.pop('_director_reference_paths',None)
        settings.update(settings_override or {})
        episode=dict(no=1, prose='阿岚扶稳茶杯。阿岚说：“慢一点。”', pictures=slices[0]['text'],
                     brief='扶稳茶杯并交流。')
        if prose_override is not None:
            episode['prose'] = prose_override
        scenes=[dict(scene_id='place',name='竹庭')]
        visuals=[dict(status='adopted',owner_id=row['character_id'],kind='character',path=__file__)
                 for row in cards]+[dict(status='adopted',owner_id='place',kind='scene',path=__file__)]
        def assets(_sid,kind):
            return copy.deepcopy(dict(characters=cards,scenes=scenes,visuals=visuals).get(kind,[]))
        stack=ExitStack()
        stack.enter_context(patch.object(authoring,'_ep',return_value=({},episode)))
        stack.enter_context(patch.object(story_core,'get_story',return_value=dict(settings=settings)))
        stack.enter_context(patch.object(authoring,'cast_cards',return_value=copy.deepcopy(cards)))
        stack.enter_context(patch.object(asset_core,'list_assets',side_effect=assets))
        stack.enter_context(patch.object(authoring,'scenes_in_text',return_value=scenes))
        stack.enter_context(patch.object(authoring,'_set_camera_owner'))
        stack.enter_context(patch.object(authoring,'confirmed_arrangement',return_value=None))
        stack.enter_context(patch.object(authoring,'frozen_slicing',
            return_value=dict(sig=authoring._pics_sig(episode['pictures']))))
        stack.enter_context(patch.object(authoring,'episode_slices',return_value=copy.deepcopy(slices)))
        stack.enter_context(patch.object(reference_ready,'canonical_scene_slices',side_effect=lambda sid,ep,rows,**kw:rows))
        stack.enter_context(patch.object(saga_core,'ep_timeline',return_value={}))
        self.addCleanup(stack.close)
        return settings

    def test_public_writer_uses_original_chinese_multishot_and_actual_picture_map(self):
        self.context()
        requests=[]
        def q(system,user,**kwargs):
            payload=json.loads(user)
            requests.append((system,payload))
            return json.dumps(response(payload),ensure_ascii=False)
        with patch.object(authoring,'_q',side_effect=q), \
             patch.object(director_flow,'make_prompts',side_effect=AssertionError('legacy writer not selected')), \
             patch.object(runtime,'actual_state_for',return_value=runtime.unknown()) as actual:
            rows=authoring.make_h3_prompts('DISPATCH_FIXTURE',1,start=0,count=1)
        self.assertEqual(len(requests),1)
        self.assertIn('中文镜头正文',requests[0][0])
        self.assertIn('[Shot 3]',rows[0]['prompt'])
        self.assertIn('清朗温和的成年男声',rows[0]['prompt'])
        self.assertEqual(rows[0]['prompt'].count('慢一点。'),1)
        self.assertEqual(rows[0]['plan']['director_profile'],release.PROFILE)
        self.assertEqual(len(requests[0][1]['canonical_references']),3)
        actual.assert_called_once_with('DISPATCH_FIXTURE',1,0)

    def test_explicit_legacy_override_keeps_existing_dispatch_contract(self):
        self.context(dict(video_director_profile='legacy'))
        with patch.object(director_flow,'make_prompts',return_value=['old']) as old, \
             patch.object(release,'make_prompts',side_effect=AssertionError('explicit old route')), \
             patch.object(runtime,'actual_state_for',side_effect=AssertionError('no optional observer')):
            rows=authoring.make_h3_prompts('DISPATCH_FIXTURE',1,start=0,count=1)
        self.assertEqual(rows,['old'])
        self.assertEqual(old.call_args.kwargs['start'],0)
        self.assertEqual(old.call_args.kwargs['count'],1)

    def test_public_first_person_unknown_voice_reaches_real_preflight_in_every_segment(self):
        from cores import seam_v2
        slices, _, _ = fixture()
        for row in slices:
            source_speaker, quote = row['director']['says'][0]
            row['text'] = '我主观视角，阿岚扶稳茶杯。\n待核对说话人：' + quote
            row['director']['says'][0][0] = '待核对说话人'
            row['director']['camera'] = '我主观视角，眼睛高度'
            row['director']['blocks'][1][2] = row['director']['blocks'][1][2].replace(source_speaker, '待核对说话人')
        settings = self.context(dict(pov='第一视角POV', camera_owner='我'),
                                slices_override=slices)
        reply = '【机位】我主观视角，眼睛高度。\n0—6秒：我主观视角，阿岚扶稳茶杯。\n这一段结束时：茶杯在桌上。'
        with patch.object(authoring, '_q', return_value=reply), \
             patch.object(authoring, 'plan_segments', return_value={}), \
             patch.object(authoring, 'fresh_shotlist', return_value=None), \
             patch.object(authoring, 'h3_voice_tech', return_value='Natural adult voice.'), \
             patch.object(seam_v2, 'on', return_value=False), \
             patch.object(release, 'make_prompts', side_effect=AssertionError('POV must keep its camera constraints')):
            rows = authoring.make_h3_prompts('SAFE_POV_SOURCE_VOICE', 1, start=0, count=2)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row['critical_problems'], [])
            self.assertEqual(segment_api._render_critical_problems(
                dict(settings=settings), {}, row, row['prompt'], 1, row['n']), [])
            quote = slices[row['n'] - 1]['director']['says'][0][1]
            self.assertEqual(row['prompt'].count('<d>[Chinese]' + quote + '</d>'), 1)
            self.assertNotIn('待核对说话人嘴唇开合', row['prompt'])
            self.assertEqual(row['chars'], ['阿岚'])


if __name__=='__main__':
    unittest.main()

