"""Recompile retained real text replies with current code; never call a model."""
import copy
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.environ['V41_NO_MODEL'] = '1'
from api import segment_api
from cores import authoring, director_flow, workbench_story_director as release
from cores import workbench_story_director_trial as trial
from tests.test_video_preflight_story_matrix import CASES, cards_for, source_slice


def main():
    folder = BASE / 'outputs/release_checks/text_only_story_matrix_20261026'
    cases = {case['key']: case for case in CASES}
    reports = []
    for directory in ('live_text_native_1', 'live_remaining_native_1'):
        summary = json.loads((folder / directory / 'summary.json').read_text(encoding='utf-8'))
        for recorded in summary['cases']:
            case = copy.deepcopy(cases[recorded['key']])
            saved = json.loads((folder / directory / (case['key'] + '.json')).read_text(encoding='utf-8'))
            replies = [row['raw'] for row in saved['trace'] if row.get('raw')]
            if not replies:
                raise AssertionError('No actual retained model reply: ' + case['key'])
            sl, people = source_slice(case), cards_for(case)
            plan = trial.build_plan([sl], people, visual_style='中文电影摄影')
            refs = [dict(name=card['name'], character_id=card['character_id'], voice=card['voice'],
                         subject=i, picture=i, role='character', path='retained_person_%d.png' % i)
                    for i, card in enumerate(people, 1)]
            refs.append(dict(name=case['scene'], subject=len(refs)+1, picture=len(refs)+1,
                             role='scene', path='retained_scene.png'))
            request = trial.write_request(plan, plan['beats'][0],
                dict(actual_visible='unknown', characters=[], scene='unknown', actions=[], completed=[]),
                refs, user_brief=case['text'])
            clip = director_flow._clip(sl, 1, people, {}, '', {}, '')
            trace, count = [], [0]
            def replay(*args, **kwargs):
                index = min(count[0], len(replies)-1)
                count[0] += 1
                return replies[index]
            # Deliberately repeat an invalid retained response to exhaust repair
            # and verify the source fallback, rather than inventing a good draft.
            prompt, _, fallback = release._request(replay, request, clip, refs, trace)
            row = dict(source_text=case['text'], chars=case['names'], seconds=15,
                       scene=case['scene'], plan=dict(flow='source-v1'), problems=[], critical_problems=[])
            problems = segment_api._render_critical_problems({}, {}, row, prompt, 1, 1)
            reports.append(dict(key=case['key'], passed=not problems, problems=problems,
                original_status=recorded['status'], source_fallback=fallback,
                replayed_replies=count[0], original_evidence=directory))
    summary = dict(kind='Offline replay of actual retained local-model replies, not fresh model calls',
        cases=len(reports), passed=sum(row['passed'] for row in reports),
        failed=[row for row in reports if not row['passed']],
        source_fallbacks=[row['key'] for row in reports if row['source_fallback']],
        actual_model_calls=0, image_calls=0, video_calls=0, reports=reports)
    (folder / 'retained_text_replay_final.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in summary.items() if key != 'reports'}, ensure_ascii=False))
    return 0 if not summary['failed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
