"""Two paid takes only: preserve the original five-shot film's authored text.

Run only through story_video_offline.py.  Importing this file never infers.
The only text calls observe the true last frames of original clips 2 and 4.
"""
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import run_fantasy_combat_pilot as pilot

VERSION = 'frozen-combat-scope-repair-v1'
REUSE, RENDER = (1, 2, 4), (3, 5)
ARRIVAL_RULE = (' This environment picture defines the arrival destination only. '
    'Begin at the established departure location when the written action starts there, '
    'retaining its existing door and architecture from preceding video evidence until the characters move away. '
    'Do not redesign the departure shop or replace its door with architecture from the destination. '
    'Descriptive wording does not authorize rebuilding an established location without a written alteration action.')
BEGINNING = ' Beginning state constraints (not additional dialogue): '
FIELDS = ('subject_definitions', 'summary', 'retention_analysis', 'detailed_description',
          'overall_soundscape', 'non_diegetic_music')


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _copy_exact(source, target):
    source, target = Path(source), Path(target)
    expected = pilot._sha(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and pilot._sha(target) != expected:
        raise ValueError('冻结文件已变化，未覆盖：' + str(target))
    if not target.exists():
        shutil.copy2(source, target)
    if pilot._sha(target) != expected:
        raise ValueError('复制结果与原件不一致：' + str(target))
    return dict(source=str(source), path=str(target), sha256=expected)


def endpoint_facts(observation):
    """Only the single terminal image's literal sentence may enter the patch."""
    if not isinstance(observation, dict) or observation.get('status') == 'unavailable':
        return ''
    value = observation.get('actual_visible')
    if (not isinstance(value, str) or not value.strip() or value.strip().lower() in ('unknown', '未知')
            or re.search(r'图\s*[2-9]|图\s*1\s*[-～~、与和]\s*\d|\[Shot|<d>|<Subject|<Picture', value, re.I)):
        return ''
    # Same quote stripping as the existing scope writer: observation never adds dialogue.
    return re.sub(r'[‘“\"「『][^’”\"」』\n]*[’”\"」』]', '', value).strip()


def patch_frozen_prompt(prompt, index, observation):
    if index not in RENDER:
        raise ValueError('本次只允许修复第3、5段程序scope')
    headers = list(re.finditer(r'(?m)^(' + '|'.join(FIELDS) + r'):\n', prompt))
    if [row[1] for row in headers] != list(FIELDS):
        raise ValueError('旧词不是完整固定六区，未编造替代词')
    body_start = prompt.index('[Shot 1]', headers[3].end())
    body_end = headers[4].start()
    if prompt.count(BEGINNING) != 1 or prompt.index(BEGINNING) >= body_start:
        raise ValueError('旧Beginning位置不唯一，未改动导演正文')
    old_start = prompt.index(BEGINNING)
    old_end = prompt.rfind('\n', old_start, body_start)  # preserve the original newline before Shot 1
    if old_end < old_start:
        raise ValueError('缺少scope与镜头正文的分隔')
    facts = endpoint_facts(observation)
    replacement = BEGINNING + facts if facts else ''
    value = prompt[:old_start] + replacement + prompt[old_end:]
    arrival_count = value.count(ARRIVAL_RULE)
    if (index == 3 and arrival_count != 2) or (index == 5 and arrival_count != 0):
        raise ValueError('arrival scope与已审计的旧版不符，未扩大替换范围')
    if index == 3:
        value = value.replace(ARRIVAL_RULE, '')
    # Prove all authored shots, cuts, spoken lines and both sound sections are byte-identical.
    protected = prompt[body_start:]
    if value[value.index('[Shot 1]'):] != protected:
        raise ValueError('修复意外改动了冻结导演正文或声音')
    audit = dict(index=index, original_sha256=digest(prompt), patched_sha256=digest(value),
        removed_arrival_rule_count=arrival_count if index == 3 else 0,
        beginning_before=prompt[old_start:old_end], beginning_after=replacement,
        true_endpoint_facts_used=bool(facts), authored_suffix_sha256=digest(protected),
        authored_shots_cuts_dialogue_audio_unchanged=True,
        creative_changes=False, allowed_changes=['injected_arrival_scope', 'injected_beginning_facts'])
    return value, audit


@contextmanager
def exact_submission(session, work, index, expected):
    """Verify the actual Comfy graph before its one permitted paid submission."""
    from cores import store
    from models import h3_client
    with pilot.single_submission(session, work, index):
        original = h3_client._http
        def checked(method, endpoint, data=None, **kwargs):
            if method.upper() == 'POST' and endpoint == '/prompt':
                graph = (data or {}).get('prompt') or {}
                texts = [node.get('inputs', {}).get('prompt') for node in graph.values()
                         if isinstance(node, dict) and isinstance(node.get('inputs', {}).get('prompt'), str)]
                if texts.count(expected.strip()) != 1:
                    raise RuntimeError('实际H3图中的提示词偏离冻结补丁，尚未提交')
                store.save_json(work / 'actual_payload_audit.json', dict(index=index,
                    final_prompt_sha256=digest(expected.strip()), prompt_exact_match=True,
                    workflow_sha256=pilot._signature(graph)))
            return original(method, endpoint, data, **kwargs)
        h3_client._http = checked
        try:
            yield
        finally:
            h3_client._http = original


def run(folder, baseline):
    os.environ.update(V41_QWEN_PORT='18080', V41_QWEN_URL='http://127.0.0.1:18080')
    from cores import story_video_offline as offline
    folder, baseline = Path(folder).resolve(), Path(baseline).resolve()
    if folder == baseline or folder.is_relative_to(baseline):
        raise ValueError('修复输出必须在原片目录之外')
    folder.mkdir(parents=True, exist_ok=True)
    offline.install(folder / 'offline_network_audit.jsonl')
    from cores import store, story_core, saga_core, asset_core
    from cores import story_video_reference as reference, story_video_media as media
    from api import segment_api as segment
    from models import story_video_client as client, gpu_manager

    config = pilot.validate_config(pilot._read(baseline / 'config.json'))
    config['frozen_input_dir'] = str(baseline)
    identity = pilot._signature(config)
    path = folder / 'manifest.json'
    manifest = pilot._read(path) if path.exists() else dict(version=VERSION,
        task_id=folder.name, config_sha256=identity, created=time.time(), segments=[], logs=[],
        actual_model_calls=0, actual_h3_submissions=0, local_ai_only=True,
        production_defaults_changed=False, geometry=config['geometry'], seeds=config['seeds'],
        reused_segment_indices=list(REUSE), regenerated_segment_indices=list(RENDER),
        reference_policy=dict(max_text_vision_calls=4, max_h3_submissions=2, max_takes_per_segment=1))
    if manifest['version'] != VERSION or manifest['config_sha256'] != identity:
        raise ValueError('修复输入变化，已有结果保留')
    def save(name, value): store.save_json(folder / name, value)
    _copy_exact(baseline / 'config.json', folder / 'config.json')
    config = pilot._freeze_config(config, folder, manifest)
    source, plan = pilot.freeze_baseline_inputs(config, folder, manifest)
    old = pilot._read(baseline / 'manifest.json')
    rows = {row['index']: row for row in old['segments']}
    if set(rows) != set(range(1, 6)):
        raise ValueError('原片没有完整5段，未开始试测')
    evidence = []
    for index in range(1, 6):
        for name in ('director.json', 'main_result.json'):
            evidence.append(_copy_exact(baseline / 'segments' / ('%02d' % index) / name,
                folder / 'baseline_evidence' / ('%02d' % index) / name))
    save('baseline_evidence/files.json', evidence)
    store.DATA = folder / 'isolated_data'
    for name in ('stories', 'assets', 'tasks', 'narrative', 'states', 'profiles', 'manifests'):
        (store.DATA / name).mkdir(parents=True, exist_ok=True)
    sid = 'TRIAL_' + folder.name
    story = dict(story_id=sid, title=plan['title'], settings=dict(story_mode='single', style=config['style'],
        video_steps=10, video_ratio='16:9', video_size_tier='0.7'), saga=dict(episodes=[dict(no=1)]))
    if not story_core.get_story(sid):
        story_core.save_story(story)
        for card in config['cards']:
            asset_core.save_asset(sid, 'characters', dict(character_id=card['id'], name=card['name'],
                behavior_anchor=card['personality'], voice=card['voice']))
            asset_core.save_asset(sid, 'visuals', dict(visual_id='V_' + card['id'], owner_id=card['id'],
                status='adopted', kind='character', path=card['image_path']))
        for scene in config['scenes']:
            asset_core.save_asset(sid, 'scenes', dict(scene_id=scene['id'], name=scene['name'], description=scene['description']))
            asset_core.save_asset(sid, 'visuals', dict(visual_id='V_' + scene['id'], owner_id=scene['id'],
                status='adopted', kind='scene', path=scene['path']))
    timeline = saga_core.ep_timeline(sid, 1)
    if not timeline or not timeline.get('scenes'):
        timeline = pilot.build_timeline(plan, config, save)
        saga_core.save_ep_timeline(sid, 1, timeline)
    saga_core.seg_video_path = lambda _sid, _ep, _sc, number, **kw: str(folder / 'segments' / ('%02d' % number) / 'main_render.mp4')
    class Handle:
        tid = folder.name
        def step(self, label, done=0, total=5, detail=''):
            print(label, flush=True)
            save('status.json', dict(stage=label, done=done, total=total, updated=time.time()))
        def set(self, **values): print(json.dumps(values, ensure_ascii=False), flush=True)
    session = reference._Session(folder.name, Handle(), folder, dict(segment_count=5), manifest)
    manifest.update(status='running')
    session.checkpoint()
    try:
        with session.models():
            for index in range(1, 6):
                if any(row['index'] == index for row in manifest['segments']):
                    continue
                work = folder / 'segments' / ('%02d' % index)
                work.mkdir(parents=True, exist_ok=True)
                if index in REUSE:
                    row = copy.deepcopy(rows[index])
                    copy_record = _copy_exact(row['clip_path'], work / 'adopted' / 'clip.mp4')
                    anchor = _copy_exact(row['anchor_path'], work / 'adopted' / 'anchor.png')
                    row.update(clip_path=copy_record['path'], anchor_path=anchor['path'],
                        reused_original=True, reuse_copy=copy_record, prompt_path=str(folder / 'baseline_evidence' / ('%02d' % index) / 'director.json'))
                    timeline = saga_core.ep_timeline(sid, 1)
                    pilot._row(timeline, index)[1].update(video=row['clip_path'])
                    saga_core.save_ep_timeline(sid, 1, timeline)
                    manifest['segments'].append(row)
                    session.checkpoint()
                    continue
                original_previous = rows[index - 1]
                observe_request, images = pilot.terminal_observation_request(config, original_previous)
                session.stage('第%d段：仅观察原第%d段真实末帧' % (index, index - 1), index - 1)
                observation = session.cached('endpoint_before_%02d' % index,
                    dict(request=observe_request, image_sha256=pilot._sha(images[0])),
                    lambda: session.review(observe_request, work / 'endpoint_observation', images))
                save('segments/%02d/endpoint_observation.json' % index, observation)
                original_result = pilot._read(folder / 'baseline_evidence' / ('%02d' % index) / 'main_result.json')
                original_prompt = original_result['render_request']['final_prompt']
                prompt, audit = patch_frozen_prompt(original_prompt, index, observation)
                save('segments/%02d/patch_before_after.json' % index, audit)
                (work / 'original_prompt.txt').write_text(original_prompt, encoding='utf-8')
                (work / 'compiled_prompt.txt').write_text(prompt, encoding='utf-8')
                timeline = saga_core.ep_timeline(sid, 1)
                scene_no, current = pilot._row(timeline, index)
                refs = pilot.references_for_index(config, index)
                if not current.get('render_request'):
                    current.update(prompt=prompt, refs=[dict(n=ref['name']) for ref in refs],
                        director_contract=dict(start_state='', target_state=plan['beats'][index - 1]['ending']))
                    timeline['slicing']['slices'][index - 1]['director'].update(current['director_contract'])
                    saga_core.save_ep_timeline(sid, 1, timeline)
                issues = segment._render_critical_problems(story_core.get_story(sid), timeline, current, current['prompt'], 1, index)
                save('segments/%02d/main_preflight.json' % index, dict(issues=issues,
                    prompt_sha256=digest(prompt), authored_suffix_unchanged=True))
                if issues:
                    raise RuntimeError('主流程提交契约未通过，保留全部结果：' + '；'.join(issues))
                if not segment.is_segment_current(sid, 1, scene_no, index, size_tier='0.7'):
                    session.stage('第%d段：冻结原导演，仅修scope，H3单次生成' % index, index - 1)
                    (work / 'generation').mkdir(parents=True, exist_ok=True)
                    with exact_submission(session, work, index, prompt), gpu_manager.GPU_LOCK:
                        session.guard()
                        gpu_manager.acquire('h3', note='frozen combat scope repair; max two clips')
                        try:
                            with client._native_profile(client._frames(15), work / 'generation', config['geometry']):
                                result = segment.render_segment(sid, 1, scene_no, index, size_tier='0.7',
                                    override=dict(seed=config['seeds'][index - 1], ratio='16:9', steps=10), log_fn=lambda value: print(value, flush=True))
                        finally:
                            client.preserve_release('h3')
                    save('segments/%02d/main_result.json' % index, result)
                current = pilot._row(saga_core.ep_timeline(sid, 1), index)[1]
                sent = current['render_request']['final_prompt']
                if digest(sent) != digest(prompt):
                    raise RuntimeError('实际提交词与冻结补丁哈希不同，原片已保留')
                if not (work / 'main_result.json').exists():
                    # A process can end after main saved the video but before
                    # this wrapper persisted its result. Recover evidence only.
                    save('segments/%02d/main_result.json' % index, dict(video=current['video'],
                        render_request=current['render_request'], used_refs=current.get('used_refs'),
                        h3_prompt_sent=sent, h3_prompt_sha256=digest(sent),
                        snapshot_origin='completed_timeline_recovery_without_new_submission'))
                adopted = media.adopt(current['video'], work / 'adopted')
                samples = media.sample_at_times(adopted['clip_path'], work / 'samples', [0, 7, 14.8])
                manifest['segments'].append(dict(adopted, index=index, reused_original=False,
                    scene_name=pilot.scene_for_index(config, index)['name'], used_refs=current.get('used_refs'),
                    render_request=current['render_request'], prompt_path=str(work / 'compiled_prompt.txt'),
                    samples=samples, final_prompt_sha256=digest(sent)))
                session.checkpoint()
            session.stage('合并原片1/2/4与修复片3/5，检查完整音画', 5)
            joined = media.join(manifest['segments'], folder / 'joined')
            manifest.update(status='completed', joined=joined, finished=time.time())
            session.checkpoint()
            save('status.json', dict(stage='completed', done=5, total=5, joined=joined))
            save('verification.json', dict(status='completed', reused_segments=list(REUSE),
                generated_segments=list(RENDER), actual_h3_submissions=manifest['actual_h3_submissions'],
                actual_text_vision_calls=manifest['actual_model_calls'], full_decode_passed=joined.get('full_decode_passed'),
                author_planning_director_calls=0, production_defaults_changed=False,
                scope='只修程序注入范围；镜头创意、对白、切点和音轨提示词冻结；完整视觉效果待人工检查。'))
    except Exception as error:
        manifest.update(status='paused_with_evidence', error=str(error))
        session.checkpoint()
        raise


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('usage: run_frozen_combat_repair.py OUTPUT_FOLDER BASELINE_FOLDER')
    run(sys.argv[1], sys.argv[2])
