"""Verify local-model provenance and completed story-video artifacts, without models."""
import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
import ipaddress
import re
import hashlib

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _reply(text):
    # Match request_json's accepted reply parsing, including schema repair calls.
    text = str(text).strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
    return json.loads(text)


def _payload(request):
    users = [message for message in request.get('messages', []) if message.get('role') == 'user']
    if not users:
        raise ValueError('missing captured user payload')
    content = users[-1].get('content')
    if isinstance(content, str):
        return json.loads(content)
    if isinstance(content, list):
        for item in content:
            if item.get('type') == 'text':
                try:
                    payload = json.loads(item.get('text', ''))
                    if isinstance(payload, dict):
                        return payload
                except (ValueError, TypeError):
                    pass
    raise ValueError('missing captured JSON payload')


def _matching_replies(directory, accepted):
    for response_path in sorted(directory.glob('call_*/*/response.json'), reverse=True):
        try:
            if _reply(read(response_path).get('text', '')) != accepted:
                continue
            request = read(response_path.with_name('request.json'))
            yield request, _payload(request)
        except (ValueError, KeyError, TypeError, OSError):
            continue


def _accepted_from_local(directory, expected=None):
    accepted = read(directory/'accepted.json')
    if expected is not None:
        assert accepted == expected, 'accepted local plan differs from manifest'
    assert next(_matching_replies(directory, accepted), None) is not None, \
        'accepted text lacks a matching captured local model response: ' + str(directory)
    return accepted


def _plan_signature(plan):
    canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _transport_normalized_prompt(prompt):
    """Apply only the compiler's mechanical Shot spelling/zero-prefix upgrade.

    Leave the frame preamble, every creative word, nonzero action time, dialogue,
    soundscape and music untouched. This is a comparison view, never a rewrite
    of an archived director or submitted H3 graph.
    """
    from cores.story_video_prompts import _normalize_shot_format
    prefix, marker, content = prompt.partition('integrated_multimodal_description: ')
    if not marker:
        return prompt
    description, sound_marker, suffix = content.partition('\n\noverall_soundscape: ')
    if not sound_marker:
        return prompt
    return prefix + marker + _normalize_shot_format(description) + sound_marker + suffix


def _plan_provenance(folder, manifest):
    planning = folder/'planning'
    original = planning/'accepted.json'
    revised = planning/'revision_1/accepted.json'
    review = manifest.get('plan_review')
    has_review = 'plan_review' in manifest
    if has_review:
        assert isinstance(review, dict), 'invalid plan review record'
        advisory = review.get('advisory') is True and manifest.get('inputs', {}).get('flow_version', 1) >= 2
        assert (review.get('passed') is True or advisory) and not review.get('pending'), 'current plan review has not passed'
        if advisory:
            assert manifest.get('approved') and manifest.get('approved_plan_signature') == _plan_signature(manifest['plan']), 'advisory workflow lacks user approval'
        assert review.get('plan_signature') == _plan_signature(manifest['plan']), 'plan review signature is stale'
        source = Path(review['plan_source'])
        assert source.is_absolute(), 'plan source must be an absolute captured path'
        if manifest.get('plan_source'):
            assert source.resolve() == Path(manifest['plan_source']).resolve(), 'plan source records disagree'
    else:
        # Completed pre-gate tasks remain verifiable without inventing a review.
        source = Path(manifest.get('plan_source') or original)
    source = source.resolve()
    assert source in (original.resolve(), revised.resolve()), 'unexpected adopted plan source'
    _accepted_from_local(source.parent, manifest['plan'])
    proof = {'source': str(source), 'plan_signature': _plan_signature(manifest['plan']),
             'review_present': has_review, 'review_verified': False}
    if not has_review:
        return proof
    round_number = review.get('round')
    assert type(round_number) is int and round_number in (1, 2), 'invalid plan review round'
    assert source == (original if round_number == 1 else revised).resolve(), 'review round differs from adopted plan source'
    review_path = Path(review['review_source'])
    assert review_path.is_absolute(), 'review source must be an absolute captured path'
    expected_review = planning/('review_%d' % round_number)/'accepted.json'
    assert review_path.resolve() == expected_review.resolve(), 'unexpected plan review source'
    accepted = _accepted_from_local(review_path.parent)
    assert ((accepted.get('passed') is True and accepted.get('issues') == []) or review.get('advisory') is True), 'captured local review did not pass'
    assert all(review.get(key) == value for key, value in accepted.items()), 'manifest review differs from actual local reply'
    assert any(payload.get('plan') == manifest['plan'] for _, payload in _matching_replies(review_path.parent, accepted)), \
        'local review evaluated a different plan'
    if round_number == 2:
        previous_plan = _accepted_from_local(planning)
        previous_review = _accepted_from_local(planning/'review_1')
        assert previous_review.get('passed') is False and previous_review.get('issues'), 'revision lacks a rejected local review'
        assert any(payload.get('plan') == previous_plan
                   for _, payload in _matching_replies(planning/'review_1', previous_review)), \
            'first local review evaluated a different draft'
        assert any(payload.get('previous_draft') == previous_plan and payload.get('review_feedback') == previous_review
                   for _, payload in _matching_replies(source.parent, manifest['plan'])), \
            'local revision input lacks the original draft and rejected review'
        proof['revision_input_verified'] = True
    proof.update(review_verified=True, review_source=str(review_path), review_round=round_number)
    return proof


def _matching_h3(work, prompt, mode, images):
    result_path = work/'generation_result.json'
    generation = read(result_path) if result_path.is_file() else {}
    for workflow_path in sorted((work/'generation').glob('video_*/workflow.json'), reverse=True):
        try:
            workflow = read(workflow_path)
            request = read(workflow_path.with_name('request.json'))
            h3_inputs = next(node['inputs'] for node in workflow['graph'].values()
                             if node.get('class_type') == 'MiniMaxH3ImageToVideo')
            if (h3_inputs.get('prompt') != prompt or request.get('prompt') != prompt
                    or request.get('mode') != mode or request['options']['images'] != images):
                continue
            if generation.get('seed') is not None and workflow.get('meta', {}).get('seed') != generation['seed']:
                continue
            return workflow_path
        except (ValueError, KeyError, TypeError, StopIteration, OSError):
            continue
    raise AssertionError('missing matching actual H3 graph/request pair: ' + str(work))


def verify(folder):
    from cores.story_video_prompts import compile_written
    folder = Path(folder).resolve()
    manifest = read(folder/'manifest.json')
    count = manifest['inputs']['segment_count']
    assert manifest.get('local_ai_only') is True
    assert [b['index'] for b in manifest['plan']['beats']] == list(range(1, count+1))
    assert [s['index'] for s in manifest['segments']] == list(range(1, count+1))
    assert manifest['output']['full_decode_passed'] is True
    assert Path(manifest['output']['output_path']).is_file(), 'missing completed film'
    assert Path(manifest['output']['raw_path']).is_file(), 'missing completed raw movie'
    plan_provenance = _plan_provenance(folder, manifest)
    expected = 'i2va' if manifest['inputs']['opening_image'] else 't2va'
    requests = []
    for path in folder.rglob('request.json'):
        request = read(path)
        endpoint = request.get('endpoint')
        if not endpoint:
            continue
        host = urlsplit(endpoint).hostname
        assert host == 'localhost' or ipaddress.ip_address(host).is_loopback, endpoint
        requests.append({'path': str(path), 'endpoint': endpoint, 'model': request.get('model')})
    assert requests, 'missing actual local model receipts'
    checks = []
    for segment in manifest['segments']:
        mode = expected if segment['index'] == 1 else 'i2va'
        assert segment['model_mode'] == mode
        director_path = Path(segment['prompt_path'])
        work = director_path.parent
        written = read(director_path)
        accepted = read(work/'writing/accepted.json')
        # This captured payload is the actual local model input, including any
        # bounded feedback. Its dialogue comes from the local story plan.
        compiled_matches = False
        transport_compatibility_applied = False
        for request, payload in _matching_replies(work/'writing', accepted):
            try:
                if payload.get('beat') != manifest['plan']['beats'][segment['index']-1]:
                    continue
                compiled_prompt = compile_written(accepted, payload)['prompt']
                if compiled_prompt == written['prompt']:
                    compiled_matches = True
                    break
                if _transport_normalized_prompt(compiled_prompt) == _transport_normalized_prompt(written['prompt']):
                    compiled_matches = True
                    transport_compatibility_applied = True
                    break
            except (ValueError, KeyError, TypeError):
                continue
        assert compiled_matches, 'director text lacks a matching local model response'
        assert Path(segment['anchor_path']).is_file() and Path(segment['clip_path']).is_file()
        endpoint = _accepted_from_local(work/'endpoint')
        assert endpoint['actual_visible'] == segment['observation']['actual_visible']
        if segment['index'] > 1:
            prior = manifest['segments'][segment['index']-2]
            images = [prior['anchor_path']]
        elif manifest['inputs']['opening_image']:
            images = [str(folder/('opening' + Path(manifest['inputs']['opening_image']).suffix.lower()))]
            assert Path(images[0]).is_file(), 'missing copied opening image'
        else:
            images = []
        workflow_path = _matching_h3(work, written['prompt'], mode, images)
        checks.append({'index': segment['index'], 'mode': mode,
                       'local_director_provenance': True, 'local_endpoint_observation': True,
                       'transport_compatibility_applied': transport_compatibility_applied,
                       'workflow_path': str(workflow_path),
                       'kept_seconds': segment['kept_seconds'],
                       'beat_status': segment['observation']['beat_status'],
                       'risks': segment['observation']['continuity_risks']})
    events = [json.loads(line) for line in (folder/'network_audit.jsonl').read_text(encoding='utf-8').splitlines() if line]
    installed = [e for e in events if e.get('event') == 'guard_installed']
    assert installed, 'worker network guard not installed'
    return {'folder': str(folder), 'completed_segments': count, 'input_mode': expected,
            'plan_provenance': plan_provenance,
            'segments': checks, 'local_model_requests': len(requests),
            'model_endpoints': sorted({r['endpoint'] for r in requests}),
            'guarded_python_pids': sorted({e['pid'] for e in installed}),
            'blocked_network_attempts': len([e for e in events if e.get('event') == 'blocked']),
            'full_decode_passed': True, 'creative_content_from_local_model': True,
            'offline_scope': 'Python process guard + offline caches + local native model files; not physical disconnection',
            'audio_review': 'decode only; voices, words, lip sync and stereo imaging not reviewed',
            'output': manifest['output']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = verify(args.folder)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        with args.output.open('x', encoding='utf-8') as stream:
            stream.write(text)
    print(text)
