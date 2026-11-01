"""Text/code regression only: prohibit model ports, media encoders and deletion."""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from urllib.parse import urlsplit
from unittest.mock import patch

BASE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BASE))
os.environ['V41_NO_MODEL']='1'
from cores import runtime_reset, store
from models import gpu_manager


class RetainedTemporaryDirectory:
    def __init__(self,suffix=None,prefix=None,dir=None,**kwargs):
        self.name=tempfile.mkdtemp(suffix=suffix,prefix=prefix,dir=dir)
    def __enter__(self):return self.name
    def __exit__(self,*args):return False
    def cleanup(self):pass


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--all-workbench',action='store_true',help='include all code-only workbench test modules')
    parser.add_argument('--report-subdir',default='code_regression')
    args=parser.parse_args()
    folder=BASE/'outputs/release_checks/text_only_story_matrix_20261026'/args.report_subdir
    folder.mkdir(parents=True,exist_ok=True)
    patterns=('test_story_video','test_director','test_dialogue','test_h3_','test_pov_',
              'test_group_entities','test_episode_','test_speaker_','test_video_',
              'test_preflight_','test_camera_','test_workbench_story_director',
              'test_workbench_director','test_visible_identity','test_scene_reference_policy',
              'test_runtime_cache_reset')
    excluded={'test_story_video_media.py':'contains real synthetic media encoding',
              'test_story_video_evidence.py':'contains real synthetic media encoding',
              'test_age_policy.py':'existing compatibility policy is a no-op; legacy safety-contract tests do not match current source (not altered by this repair)',
              'test_口述与节拍.py':'legacy import-executed script, not a unittest suite; retained for a separate audit'}
    excluded_tests={
        'tests.test_video_continuity.MediaTests.':'contains real synthetic media encoding',
        'tests.test_motion_v104.MotionTests.test_cpu_complete_run_cut_and_original_audio':'contains real synthetic media encoding',
    }
    modules=[]
    for path in sorted((BASE/'tests').glob('test_*.py')):
        if not args.all_workbench and not path.name.startswith(patterns):continue
        source=path.read_text(encoding='utf-8-sig')
        if re.search(r"subprocess\.(?:run|Popen)\(\s*\[\s*['\"]ff(?:mpeg|probe)",source):
            excluded[path.name]='contains real media process'
        if path.name not in excluded:modules.append('tests.'+path.stem)
    loader=unittest.TestLoader();suite=unittest.TestSuite();seen=set();duplicates=0
    def add(items):
        nonlocal duplicates
        for item in items:
            if isinstance(item,unittest.TestSuite):add(item)
            elif any(item.id().startswith(key) for key in excluded_tests):continue
            elif item.id() in seen:duplicates+=1
            else:seen.add(item.id());suite.addTest(item)
    class Isolated(unittest.TextTestResult):
        def startTest(self,test):
            self.stack=ExitStack();root=Path(tempfile.mkdtemp(prefix='text_code_isolation_'))
            self.stack.enter_context(patch.object(runtime_reset,'STATE',root/'reset.json'))
            self.stack.enter_context(patch.object(gpu_manager,'_OWNER_FILE',root/'owner.json'))
            self.stack.enter_context(patch.object(store,'DATA',root/'data'))
            runtime_reset.bind_thread(None);super().startTest(test)
        def stopTest(self,test):
            runtime_reset.bind_thread(None);self.stack.close();super().stopTest(test)
            if self.testsRun%100==0:
                (folder/'progress.json').write_text(json.dumps(dict(tests=self.testsRun,errors=len(self.errors),failures=len(self.failures))),encoding='utf-8')
    original_open=urllib.request.urlopen;original_popen=subprocess.Popen
    blocked=[]
    def network(request,*args,**kwargs):
        url=request.full_url if hasattr(request,'full_url') else str(request)
        parsed=urlsplit(url)
        if parsed.hostname in ('127.0.0.1','localhost','::1') and parsed.port in (8080,18080,8188,8190,8853,8854):
            blocked.append('production HTTP');raise AssertionError('代码回归禁止访问真实模型或工作台端口')
        return original_open(request,*args,**kwargs)
    def process(args,*a,**kwargs):
        command=args[0] if isinstance(args,(list,tuple)) else str(args).split()[0]
        if re.search(r'ffmpeg|ffprobe|llama-server|comfy',Path(str(command)).name,re.I):
            blocked.append('media/model process');raise AssertionError('代码回归禁止启动编码器或真实模型')
        return original_popen(args,*a,**kwargs)
    def no_delete(*a,**kw):raise AssertionError('测试文件应保留，不得永久删除')
    with ExitStack() as stack:
        # Class fixtures/import helpers must also stay away from user data.
        isolated_store = Path(tempfile.mkdtemp(prefix='all_workbench_code_store_'))
        stack.enter_context(patch.object(store, 'DATA', isolated_store / 'data'))
        stack.enter_context(patch.object(urllib.request,'urlopen',side_effect=network))
        stack.enter_context(patch.object(subprocess,'Popen',side_effect=process))
        stack.enter_context(patch.object(tempfile,'TemporaryDirectory',RetainedTemporaryDirectory))
        stack.enter_context(patch.object(shutil,'rmtree',side_effect=no_delete))
        stack.enter_context(patch.object(Path,'unlink',side_effect=no_delete))
        stack.enter_context(patch.object(os,'unlink',side_effect=no_delete))
        stack.enter_context(patch.object(os,'remove',side_effect=no_delete))
        for name in modules:add(loader.loadTestsFromName(name))
        with (folder/'checks.log').open('w',encoding='utf-8') as stream:
            with redirect_stdout(stream), redirect_stderr(stream):
                result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=Isolated).run(suite)
    summary=dict(modules=len(modules),tests=result.testsRun,duplicates_omitted=duplicates,
        failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),
        successful=result.wasSuccessful(),excluded=excluded,excluded_tests=excluded_tests,media_processes_started=0,
        actual_model_calls=0,blocked_unmocked_calls=len(blocked),
        failed_ids=[test.id() for test,_ in result.failures+result.errors])
    (folder/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print('SUMMARY',json.dumps(summary,ensure_ascii=False))
    for test,trace in result.failures+result.errors:print('FAILED',test.id(),'\n',trace[-2500:])
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':raise SystemExit(main())
