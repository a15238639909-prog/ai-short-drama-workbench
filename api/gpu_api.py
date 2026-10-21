# -*- coding: utf-8 -*-
"""Cancel runtime work, release local models and return the workbench to idle."""
import sys
import threading
import time
from pathlib import Path

from api import post
from api._shared import _RESP
from cores import runtime_reset, runtime_core, store

ROOT = Path(__file__).resolve().parents[1]
FLAG = ROOT / 'cache' / 'game_mode.flag'
TASK = ROOT / 'cache' / 'current_task.json'
_CLEAR_LOCK = threading.Lock()


def paused():
    return FLAG.exists()


def _legacy_clear():
    # Reuse loaded compatibility tools; do not import a second server instance.
    app = next((getattr(module, '_APP', None)
                for name in ('__main__', 'server')
                for module in [sys.modules.get(name)]
                if module and getattr(module, '_APP', None)), None)
    if app is None:
        return 0
    for name in ('AUTO_JOB_STOP', 'GEN_STOP'):
        event = getattr(app, name, None)
        if event:
            event.set()
    jobs = getattr(app, 'VC_JOBS', {})
    count = runtime_reset.clear_jobs(jobs)
    for job in jobs.values():
        job['status'] = 'cancelled'
    with getattr(app, 'VC_QUEUE_LOCK', threading.RLock()):
        getattr(app, 'VC_QUEUE', []).clear()
        getattr(app, 'VC_PROGRESS', {}).update(active=False, total=0, done=0,
            current='', start=0, itemStart=0)
    with getattr(app, 'OP_LOCK', threading.RLock()):
        getattr(app, 'OPS', {}).clear()
    app.clear_activity()
    with getattr(app, 'AUTO_JOB_LOCK', threading.RLock()):
        job = getattr(app, 'AUTO_JOB', {})
        job.update(status='idle', stage='', message='空闲', progress=0,
                   current=0, total=0, error='', cleared_at=time.time())
        if getattr(app, 'AUTO_JOB_STATE_FILE', None):
            store.save_json(Path(app.AUTO_JOB_STATE_FILE), job)
    director = getattr(app, 'video_gen_core', None)
    if director:
        director.stop_all()
        manager = getattr(director, 'MANAGER', None)
        if manager:
            with director.STATE_LOCK:
                for task in manager.tasks.values():
                    if task.get('status') in ('queued', 'loading', 'running'):
                        task['status'] = 'cancelled'
                        count += 1
                    task.update(cleared_at=time.time(), cancelRequested=True,
                                done=0, total=0, progress=0, detail='空闲')
                    director._save_task(task)
                manager.current_id = None
    return count


@post('/api/gpu/clear')
@post('/api/gpu/pause')
@post('/api/gpu/resume')
def r_clear(h, path, d):
    """Old pause/resume clients cancel too; nothing is automatically refired."""
    if not _CLEAR_LOCK.acquire(blocking=False):
        return _RESP({'ok': False, 'error': '正在清理缓存，请稍候'}, 409)
    epoch = None
    try:
        epoch = runtime_reset.begin()
        runtime_reset.bind_thread(epoch)
        from api import saga_api, timeline, video_api, story
        from cores import story_video_core
        from models import gpu_manager, h3_client
        h3_client.request_cancel()
        modules = (saga_api, timeline, video_api, story)
        cancelled = sum(module.clear_runtime_jobs() for module in modules)
        cancelled += _legacy_clear()
        children = story_video_core.cancel_all_children()
        if children.get('errors'):
            raise RuntimeError('故事视频进程未完全退出：' + '; '.join(children['errors']))
        cancelled += runtime_core.clear_all_tasks()['cancelled']
        killed = gpu_manager.stop_registered_models()
        for name, stop in (('qwen', gpu_manager.stop_qwen),
                           ('krea', gpu_manager.stop_comfy), ('h3', gpu_manager.stop_h3)):
            if stop():
                killed.append(name)
        gpu_manager.reset_owner()
        recycled = []
        with runtime_reset.LOCK:
            for marker in (FLAG, TASK):
                if runtime_reset.recycle_transient(marker):
                    recycled.append(marker.name)
        for module in modules:
            module.clear_runtime_jobs()
        runtime_core.clear_all_tasks()
        store.save_json(store.DATA / 'gpu_state.json',
                        {'mode': 'normal', 'paused_tasks': [], 'at': time.time()})
        runtime_reset.finish(epoch)
        return _RESP({'ok': True, 'data': {'paused': False, 'cancelled': cancelled,
            'tasks': [], 'done': 0, 'total': 0, 'percent': 0,
            'killed': sorted(set(killed)), 'recycled': recycled}})
    except Exception as error:
        if epoch:
            runtime_reset.finish(epoch, str(error))
        return _RESP({'ok': False, 'error': '清理未完成，已拦住旧任务：' + str(error)}, 500)
    finally:
        _CLEAR_LOCK.release()
