"""Opt-in local text-only exercise. No image/video creation or task submission."""
import argparse
from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
from unittest.mock import patch

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from tests.test_video_preflight_story_matrix import CASES,cards_for,source_slice
from cores import authoring,director_flow,workbench_story_director,workbench_story_director_trial as trial
from api import segment_api
from models import qwen_client,gpu_manager,h3_client,krea_client,story_video_client

PUBG_COMMAND="$pubg = Get-Process -Name 'TslGame','TslGame_BE','PUBG' -ErrorAction SilentlyContinue\nif ($pubg) { 'PUBG_RUNNING' } else { 'PUBG_NOT_RUNNING' }"


def get_json(url):
    with urllib.request.urlopen(url,timeout=4) as response:return json.load(response)


def idle():
    active=get_json('http://127.0.0.1:8853/api/tasks/active')
    if active.get('data',{}).get('active'):raise RuntimeError('工作台还有活动任务，未运行文字实测')
    for p in (BASE/'data/generation_jobs').glob('*.json'):
        job=json.loads(p.read_text(encoding='utf-8-sig'))
        if job.get('status') in ('queued','running','retrying','waiting_resource','waiting_task'):
            raise RuntimeError('还有待执行生成任务，未运行文字实测')
    for port in (8188,8190):
        try:queue=get_json('http://127.0.0.1:%d/queue'%port)
        except OSError:continue
        if queue.get('queue_running') or queue.get('queue_pending'):
            raise RuntimeError('模型队列仍在工作，未抢占显卡')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--allow-local-ai',action='store_true')
    parser.add_argument('--max-calls',type=int,default=36)
    parser.add_argument('--native-only',action='store_true')
    parser.add_argument('--case-keys',nargs='+')
    parser.add_argument('--report-subdir',default='live_text');args=parser.parse_args()
    if not args.allow_local_ai:raise SystemExit('Only runs with explicit --allow-local-ai; no media calls supported.')
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.report_subdir):raise SystemExit('Invalid report subdirectory')
    out=BASE/'outputs/release_checks/text_only_story_matrix_20261026'/args.report_subdir
    out.mkdir(parents=True,exist_ok=True)
    summary=dict(model=qwen_client.QWEN_MODEL,endpoint=qwen_client.QWEN_URL,actual_text_calls=0,
                 image_calls=0,video_calls=0,project60_modified=False,cases=[],status='running')
    def save(): (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    original_chat=qwen_client.chat
    def checked_chat(system,user,**options):
        check=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',PUBG_COMMAND],
            capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=15,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if check.returncode or check.stdout.strip()!='PUBG_NOT_RUNNING':
            raise RuntimeError('PUBG正在运行或状态无法确认，文字实测暂停，未启动新调用')
        if summary['actual_text_calls']>=args.max_calls:raise RuntimeError('已达到文字实测调用上限')
        summary['actual_text_calls']+=1;number=summary['actual_text_calls'];save()
        started=time.monotonic()
        print('TEXT_CALL',number,'START',flush=True)
        result=original_chat(system,user,**options)
        (out/('call_%02d.json'%number)).write_text(json.dumps(dict(system=system,user=user,
            reply=result,seconds=round(time.monotonic()-started,2)),ensure_ascii=False,indent=2),encoding='utf-8')
        print('TEXT_CALL',number,'DONE',flush=True)
        return result
    def no_media(*a,**kw):raise AssertionError('文字实测禁止任何图片、视频或视觉模型调用')
    prior={name:os.environ.get(name) for name in ('V41_NO_MODEL','V41_GPU_FORCE')}
    try:
        idle()
        # The supported force flag reclaims only this confirmed idle workbench
        # claim; PUBG and every subsequent model call remain guarded normally.
        owner=gpu_manager.owner()
        if owner and int(owner.get('pid') or 0)!=os.getpid():
            summary['idle_owner_reclaimed']={k:owner.get(k) for k in ('who','pid')}
            os.environ['V41_GPU_FORCE']='1'
        os.environ.pop('V41_NO_MODEL',None)
        with ExitStack() as stack:
            stack.enter_context(patch.object(qwen_client,'chat',side_effect=checked_chat))
            for module,names in ((h3_client,('generate','start_h3')), (krea_client,('generate','generate_one','start')),
                                 (story_video_client,('video','vision'))):
                for name in names:
                    if hasattr(module,name):stack.enter_context(patch.object(module,name,side_effect=no_media))
            wanted=args.case_keys or ['daily_tea','work_handover','fantasy_seed','xianxia_sword','combat_shield',
                    'same_words_two_speakers','written_sign','explicit_scene_transition']
            for key in wanted:
                case=next(copy.deepcopy(case) for case in CASES if case['key']==key)
                report=dict(key=key,input=case['text'],route='formal_chinese_director',status='running')
                summary['cases'].append(report);save()
                try:
                    sl=source_slice(case);people=cards_for(case)
                    plan=trial.build_plan([sl],people,visual_style='中文电影摄影')
                    refs=[dict(name=row['name'],character_id=row['character_id'],voice=row['voice'],
                        subject=i,picture=i,role='character',path='text_only_person_%d.png'%i)
                        for i,row in enumerate(people,1)]
                    refs.append(dict(name=case['scene'],subject=len(refs)+1,picture=len(refs)+1,
                                     role='scene',path='text_only_scene.png'))
                    request=trial.write_request(plan,plan['beats'][0],dict(actual_visible='unknown',characters=[],scene='unknown',actions=[],completed=[]),refs,user_brief=case['text'])
                    clip=director_flow._clip(sl,1,people,{},'',{},'')
                    trace=[]
                    prompt,compiled,fallback=workbench_story_director._request(authoring._q,request,clip,refs,trace)
                    row=dict(source_text=case['text'],chars=case['names'],seconds=15,scene=case['scene'],
                             plan=dict(flow='source-v1'),problems=[],critical_problems=[])
                    problems=segment_api._render_critical_problems({}, {}, row,prompt,1,1)
                    report.update(status='passed' if not problems else 'failed',problems=problems,source_fallback=fallback)
                    (out/(key+'.json')).write_text(json.dumps(dict(report=report,prompt=prompt,trace=trace),
                        ensure_ascii=False,indent=2),encoding='utf-8')
                except Exception as error:
                    report.update(status='failed',error=str(error));save()
                    if 'PUBG' in str(error) or '显卡正被' in str(error):raise
                save();print('CASE',key,report['status'],flush=True)
            pov_cases=[] if args.native_only else [('pov_only_me',[('我','辛苦了。')]),
                              ('pov_alternating',[('我','修好了吗？'),('阿宁','修好了。'),('我','谢谢。')])]
            for key,turns in pov_cases:
                report=dict(key=key,route='actual_pov_prompt',status='running');summary['cases'].append(report);save()
                try:
                    action='我主观视角，阿宁把修好的手表放到柜台上，抬眼看向镜头。'
                    source=action+'\n'+'\n'.join('%s：%s'%line for line in turns)
                    settings=dict(pov='第一视角POV',camera_owner='我')
                    person=dict(name='阿宁',age=30,voice_h3_en='Warm clear voice, medium pace.',voice_h3_age='30')
                    settings['_beat']=dict(act=action,cast=['阿宁'],director=dict(camera='我主观视角，眼睛高度',
                        cast=['阿宁'],seconds=15,blocks=[[0,15,action,list(range(1,len(turns)+1))]],
                        says=turns,end_state={'阿宁':dict(pos='柜台后',hands='放下手表',posture='站立',facing='镜头',side='中央')}))
                    prompt=authoring.h3_prompt(source,[person],'修表店',settings,scene_image=True,seconds=15)
                    row=dict(source_text=source,chars=['阿宁'],scene='修表店',seconds=15,plan={},problems=[],critical_problems=[])
                    problems=segment_api._render_critical_problems(dict(settings=settings),{},row,prompt,1,1)
                    report.update(status='passed' if not problems else 'failed',problems=problems,input=source)
                    (out/(key+'.json')).write_text(json.dumps(dict(report=report,prompt=prompt),ensure_ascii=False,indent=2),encoding='utf-8')
                except Exception as error:report.update(status='failed',error=str(error))
                save();print('CASE',key,report['status'],flush=True)
        summary['status']='completed'
    except Exception as error:
        summary.update(status='blocked',error=str(error))
        print('BLOCKED',str(error),flush=True)
    finally:
        gpu_manager.release('qwen')
        for name,value in prior.items():
            if value is None:os.environ.pop(name,None)
            else:os.environ[name]=value
        save()
    print(json.dumps({k:v for k,v in summary.items() if k!='cases'},ensure_ascii=False),flush=True)
    return 0 if summary['status']=='completed' and all(row['status']=='passed' for row in summary['cases']) else 1


if __name__=='__main__':raise SystemExit(main())
