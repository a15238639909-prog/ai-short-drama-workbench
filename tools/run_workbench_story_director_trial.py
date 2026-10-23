"""Opt-in, retained adult-fixture pilot; never changes the workbench's defaults.

Run through cores/story_video_offline.py. The local writer/director create every
creative output. The normal segment renderer submits each saved request once.
"""
import copy
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['V41_QWEN_PORT'] = '18080'
os.environ['V41_QWEN_URL'] = 'http://127.0.0.1:18080'


def run(folder):
    from cores import store, story_core, saga_core, asset_core
    from cores import story_video_reference as reference, story_video_reference_prompts as prompts
    from cores import story_video_characters as characters, story_video_core as core
    from cores import story_video_media as media, story_video_continuation as continuation
    from cores import workbench_story_director_trial as adapter
    from models import story_video_client as client, gpu_manager
    from api import segment_api as segment

    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    parent_path = os.environ.get('TRIAL_PARENT_FOLDER', '')
    parent = Path(parent_path).resolve() if parent_path else None
    parent_manifest = json.loads((parent / 'manifest.json').read_text(encoding='utf-8')) if parent else None
    count = 3 if parent else 2
    def save(name, value):
        store.save_json(folder / name, value)
    def read(name):
        return json.loads((folder / name).read_text(encoding='utf-8'))
    def transport_source(beat):
        try:
            return adapter.main_source_text(beat)
        except ValueError as error:
            evidence = getattr(error, 'evidence', {})
            expected = [(line['speaker'], line['text']) for line in beat.get('dialogue', [])]
            parsed = evidence.get('parsed_dialogue') or []
            if ([words for _, words in parsed] != [words for _, words in expected]
                    or any(speaker not in (owner, '待核对说话人')
                           for (speaker, _), (owner, _) in zip(parsed, expected))):
                raise
            # Keep unresolved source ownership as evidence. The independent
            # fixed slots still bind exact speakers, words, order and timing;
            # never duplicate speech just to satisfy a legacy quote parser.
            save('source_transport_warnings/beat_%02d.json' % beat['index'],
                 dict(status='warning', quality_gate=False, evidence=evidence,
                      policy='retain-ambiguous-source-use-validated-dialogue-slots'))
            return beat['action']
    # All project metadata is process-local. Existing main projects are untouched.
    store.DATA = folder / 'isolated_data'
    for name in ('stories', 'assets', 'tasks', 'narrative', 'states', 'profiles', 'manifests'):
        (store.DATA / name).mkdir(parents=True, exist_ok=True)
    original = json.loads((ROOT / 'outputs/story_video/xianxia_local_test_20261018/manifest.json').read_text(encoding='utf-8'))['config']
    cards = characters.normalize_cards(original['cards'])
    scene = dict(name='云海庭院', path=str(ROOT / 'outputs/story_video/xianxia_local_test_20261018/references/scene_cloud_courtyard.png'))
    style = '东方仙侠，真人写实电影摄影，两位成年修士，人物外形和正常整套衣着完整按照人物参考图，中文自然对白。'
    direction = ('两位成年修士陆青尘和苏晚棠，在云海庭院发生一个轻松、有趣、有小反转的仙侠小故事。'
                 '只用这两位人物，在同一个庭院内完成，有明确的起因、行动、回应和结果。'
                 '全片30秒，分两段各15秒；给动作和反应留足时间，中文对白简短自然。'
                 '安排服务剧情的景别变化、人物特写和可见动作，切镜不固定次数；人物穿着参考图里的完整衣服。'
                 '具体事件、道具、笑点、台词和结尾都由本地AI构思，不复用旧片故事。')
    if parent:
        direction = direction.replace('全片30秒，分两段各15秒', '全片45秒，分三段各15秒')
        direction += '已完成首段逐字保留；由本地AI把尚未拍摄的后续故事安排为后面两段，忠实原台词和结尾，不重复已完成的动作。'
    inputs = dict(story_input='idea', story_direction=direction, style=style,
                  segment_count=count, segment_seconds=15,
                  reference_visual_policy=prompts.REFERENCE_VISUAL_POLICY, initial_scene=scene['path'])
    if parent:
        inputs['parent_trial'] = str(parent)
    manifest_path = folder / 'manifest.json'
    manifest = read('manifest.json') if manifest_path.exists() else dict(
        version='workbench-story-director-pilot-v1', task_id=folder.name, inputs=inputs,
        local_ai_only=True, production_defaults_changed=False, actual_model_calls=0,
        reference_policy=dict(max_text_vision_calls=24, max_h3_submissions=2),
        segments=copy.deepcopy(parent_manifest['segments'][:1]) if parent else [], logs=[], created=time.time(),
        geometry=dict(width=1024, height=576, ratio='16:9'), seeds=[2026102101, 2026102102, 2026102103][:count])
    if manifest['inputs'] != inputs:
        raise ValueError('试验输入变化，原目录保留，请使用新目录')
    class Handle:
        tid = folder.name
        def step(self, label, done=0, total=count, detail=''):
            print(label, flush=True)
            save('status.json', dict(stage=label, done=done, total=total, updated=time.time()))
        def set(self, **values):
            print(json.dumps(values, ensure_ascii=False), flush=True)
    session = reference._Session(folder.name, Handle(), folder, inputs, manifest)
    if manifest.get('error'):
        manifest.setdefault('recovered_errors', []).append(manifest.pop('error'))
    manifest['status'] = 'running'
    session.checkpoint()
    sid = 'TRIAL_' + folder.name
    story = story_core.get_story(sid)
    if not story:
        story = dict(story_id=sid, title='中文多镜头导演接入主流程试验',
                     settings=dict(story_mode='single', style=style), saga=dict(episodes=[dict(no=1)]))
        story_core.save_story(story)
        for index, card in enumerate(cards, 1):
            asset_core.save_asset(sid, 'characters', dict(character_id=card['id'], name=card['name'],
                behavior_anchor=card['personality'], voice=card['voice'], created=index))
            asset_core.save_asset(sid, 'visuals', dict(visual_id='V_' + card['id'], owner_id=card['id'],
                status='adopted', kind='character', path=card['image_path'], created=index))
        asset_core.save_asset(sid, 'scenes', dict(scene_id='COURTYARD', name=scene['name']))
        asset_core.save_asset(sid, 'visuals', dict(visual_id='V_COURTYARD', owner_id='COURTYARD',
            status='adopted', kind='scene', path=scene['path']))
    # Resume checks must use the same parameters as the actual submission.
    story.setdefault('settings', {}).update(video_steps=10, video_ratio='16:9', video_size_tier='0.7')
    story_core.save_story(story)
    # Only this dedicated child overrides its output path, not the server.
    saga_core.seg_video_path = lambda _sid, _ep, _sc, number, **kw: str(folder / 'segments' / ('%02d' % number) / 'main_render.mp4')
    try:
        with session.models():
            first_refs = characters.references_for_segment(cards, scene=scene)
            observed = prompts.observe_request('references')
            observed[1].update(image_role_bindings=[dict(image_index=index, role=ref['role'],
                **{key: ref[key] for key in ('character_id', 'subject') if key in ref})
                for index, ref in enumerate(first_refs, 1)], canonical_characters=[dict(id=c['id'], name=c['name']) for c in cards])
            if parent:
                material = json.loads((parent / 'reference_observation/result.json').read_text(encoding='utf-8'))
                save('reused_reference_observation.json', dict(source=str(parent / 'reference_observation/result.json'), observation=material))
            else:
                material = session.cached('reference_observation', dict(request=observed),
                    lambda: session.review(observed, folder / 'reference_observation/model', [r['path'] for r in first_refs]))
            if material.get('status') == 'unavailable':
                material = continuation.unknown_observation(material)
            material = prompts.sanitize_reference_observation(material, observed[1]['image_role_bindings'])
            if parent:
                source = (parent / 'story.txt').read_text(encoding='utf-8')
                if not (folder / 'story.txt').exists():
                    (folder / 'story.txt').write_text(source, encoding='utf-8')
            else:
                source = reference._story(session, cards, material)
            source_contract = session.cached('source_contract', dict(source=source, cards=cards),
                lambda: characters.prepare_source_contract(source, cards, strict=False))
            parent_plan = json.loads((parent / 'plan.json').read_text(encoding='utf-8')) if parent else None
            plan_source = transport_source(parent_plan['beats'][1]) if parent else source
            planning = prompts.plan_request(plan_source, style, 2, 15,
                parent_manifest['segments'][0]['observation'] if parent else material,
                flow_optimization=True, reference_visual_policy=prompts.REFERENCE_VISUAL_POLICY,
                scene_reference_provided=True)
            planning[1].update(canonical_characters=characters.authoring_characters(cards), source_contract=source_contract)
            planning = (planning[0] + '\ncanonical_characters是唯一稳定人名/ID，逐句发声严格沿用source_contract.dialogue及其顺序。', planning[1], planning[2])
            if parent:
                planning[1].update(completed_prefix=parent_plan['beats'][:1],
                    task='这里只规划尚未拍摄的后续两段，每段15秒。完整源故事用于理解，不重演completed_prefix，保留两句原话和结尾。通过实际反应、动作展开和景别安排拍清已有事件，不新增支线。')
            def get_plan():
                if not parent:
                    return reference._request_plan(session, planning, folder / 'planning/model', source_contract, cards)
                def validate_existing_entities(candidate, payload):
                    normalized, _ = adapter.normalize_observed_silent_entities(
                        candidate, cards, parent_manifest['segments'][0]['observation'])
                    core.validate_schema(normalized, reference._plan_usable_schema(planning[2]))
                    reference._validate_usable_reference_plan(normalized, payload, cards)
                return session.ask(planning, folder / 'planning/model', validator=validate_existing_entities)
            remaining = session.cached('planning', dict(request=planning), get_plan)
            if parent:
                remaining, adjustments = adapter.normalize_observed_silent_entities(
                    remaining, cards, parent_manifest['segments'][0]['observation'])
                save('planning/observed_entity_transport.json', dict(adjustments=adjustments, creative_changes=False))
                save('planning/source_validation.json', reference._plan_source_audit(
                    remaining, planning[1], source_contract, cards, folder / 'planning/model'))
            if parent:
                plan = copy.deepcopy(remaining)
                plan['beats'] = copy.deepcopy(parent_plan['beats'][:1]) + [dict(beat, index=beat['index']+1) for beat in remaining['beats']]
                manifest['parent_trial'] = str(parent)
                manifest['reused_first_clip'] = dict(path=parent_manifest['segments'][0]['clip_path'],
                    sha256=parent_manifest['segments'][0]['clip_sha256'], parent_model_calls=parent_manifest['actual_model_calls'])
            else:
                plan = remaining
            save('plan.json', plan)
            timeline = saga_core.ep_timeline(sid, 1) or {}
            if not timeline.get('scenes'):
                segments, slices = [], []
                for beat in plan['beats']:
                    text = transport_source(beat)
                    names = [card['name'] for card in cards if card['id'] in beat['cast']]
                    segments.append(dict(no=beat['index'], scene=scene['name'], chars=names, seconds=15,
                        source_text=text, prompt='', plan=dict(flow='source-v1'), critical_problems=[]))
                    slices.append(dict(text=text, seconds=15, scene_hint=scene['name'], director=dict(
                        flow='source-v1', cast=names, start_state='', target_state=beat['ending'])))
                if parent:
                    old_story = json.loads((parent / 'isolated_data/stories' / ('TRIAL_' + parent.name + '.json')).read_text(encoding='utf-8'))
                    old_timeline = old_story['saga']['episodes'][0]['timeline']
                    segments[0] = copy.deepcopy(old_timeline['scenes'][0]['segments'][0])
                    slices[0] = copy.deepcopy(old_timeline['slicing']['slices'][0])
                timeline = dict(scenes=[dict(no=1, name=scene['name'], segments=segments)], slicing=dict(slices=slices))
                saga_core.save_ep_timeline(sid, 1, timeline)
            else:
                # A source-adaptation repair cannot change submitted requests.
                for beat in plan['beats']:
                    row = timeline['scenes'][0]['segments'][beat['index']-1]
                    if row.get('render_request'):
                        continue
                    text = transport_source(beat)
                    if row.get('source_text') != text:
                        save('segments/%02d/source_transport_normalization.json' % beat['index'],
                            dict(before=row.get('source_text'), after=text,
                                 reason='固定对白已在动作原文出现，不再次拼接原话', creative_changes=False))
                        row['source_text'] = text
                        timeline['slicing']['slices'][beat['index']-1]['text'] = text
                saga_core.save_ep_timeline(sid, 1, timeline)
            for beat in plan['beats']:
                index = beat['index']
                if any(row['index'] == index for row in manifest['segments']):
                    continue
                previous = manifest['segments'][-1] if manifest['segments'] else None
                state = previous['observation'] if previous else material
                # Same agreed main policy: first scene entry gets the scene;
                # continuation gets identities only, even though vision sees the end.
                refs = characters.references_for_segment(cards, scene=scene if index == 1 else None)
                voices = {card['id']: card['voice'] for card in cards}
                for ref in refs:
                    if ref['role'] == 'character':
                        ref['voice'] = voices[ref['character_id']]
                work = folder / 'segments' / ('%02d' % index)
                request = adapter.write_request(plan, beat, state, refs, seconds=15,
                    visual_style=style, user_brief=source, scene_name=scene['name'])
                def compile_bound(candidate, payload):
                    return adapter.compile_reply(candidate, payload)
                session.stage('第%d/%d段：本地中文多镜头导演写词' % (index, count), index - 1)
                draft = session.ask(request, work / 'writing', validator=compile_bound)
                compiled = compile_bound(draft, request[1])
                save('segments/%02d/director.json' % index, compiled)
                save('segments/%02d/request_payload.json' % index, request[1])
                timeline = saga_core.ep_timeline(sid, 1)
                current = timeline['scenes'][0]['segments'][index-1]
                current.update(prompt=compiled['prompt'], refs=[dict(n=r['name']) for r in refs],
                    director_contract=dict(start_state=state.get('actual_visible', ''), target_state=beat['ending']))
                saga_core.save_ep_timeline(sid, 1, timeline)
                issues = segment._render_critical_problems(story_core.get_story(sid), timeline, current, compiled['prompt'], 1, index)
                save('segments/%02d/main_preflight.json' % index, dict(issues=issues))
                if issues:
                    raise RuntimeError('主流程兼容校验未通过，尚未提交视频：' + '；'.join(issues))
                if not segment.is_segment_current(sid, 1, 1, index, size_tier='0.7'):
                    session.stage('第%d/%d段：主流程提交H3，人物图%s' % (index, count, '＋场景图' if index == 1 else '（不加场景或尾帧）'), index-1)
                    started = time.time()
                    (work / 'generation').mkdir(parents=True, exist_ok=True)
                    with gpu_manager.GPU_LOCK:
                        session.guard()
                        gpu_manager.acquire('h3', note='workbench story-director pilot')
                        with client._native_profile(client._frames(15), work / 'generation', manifest['geometry']):
                            result = segment.render_segment(sid, 1, 1, index, size_tier='0.7',
                                override=dict(seed=manifest['seeds'][index-1], ratio='16:9', steps=10), log_fn=lambda x: print(x, flush=True))
                    client.preserve_release('h3')
                    save('segments/%02d/main_result.json' % index, result)
                    save('segments/%02d/render_timing.json' % index, dict(seconds=time.time()-started))
                current = saga_core.ep_timeline(sid, 1)['scenes'][0]['segments'][index-1]
                adopted = media.adopt(current['video'], work / 'adopted')
                samples = media.sample_at_times(adopted['clip_path'], work / 'samples', [0, 7, 14.8])
                observation = prompts.observe_request('segment', state, beat)
                observation[1].update(canonical_characters=[dict(id=c['id'], name=c['name']) for c in cards])
                session.stage('第%d/%d段：本地观察实际画面与尾帧' % (index, count), index-1)
                visible = session.review(observation, work / 'observation',
                    images=[row['path'] for row in samples] + [adopted['anchor_path']])
                if visible.get('status') == 'unavailable':
                    visible = continuation.unknown_observation(visible)
                manifest['segments'].append(dict(adopted, index=index, observation=visible,
                    prompt_path=str(work / 'director.json'), used_refs=current.get('used_refs'),
                    render_request=current.get('render_request'), shot_count=compiled['prompt'].count('[Shot ')))
                session.checkpoint()
            session.stage('合并%d段主流程试验成片' % count, count)
            joined = media.join(manifest['segments'], folder / 'joined')
            manifest.update(status='completed', joined=joined, finished=time.time())
            session.checkpoint()
            save('status.json', dict(stage='completed', done=count, total=count, joined=joined))
            print(json.dumps(dict(completed=True, joined=joined), ensure_ascii=False), flush=True)
    except Exception as error:
        manifest.update(status='paused_with_evidence', error=str(error))
        session.checkpoint()
        save('status.json', dict(stage='paused_with_evidence', error=str(error), done=len(manifest['segments']), total=count))
        raise


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: run_workbench_story_director_trial.py OUTPUT_FOLDER')
    run(sys.argv[1])
