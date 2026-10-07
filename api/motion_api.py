"""Small local motion-transfer tool using existing task persistence."""
import threading
from api import get, post
from api._shared import _RESP
from cores import motion_core, runtime_core

_LOCK = threading.Lock()
runtime_core.RUNNERS['motion_transfer'] = motion_core.run


@get('/api/motion/status')
def status(h, path, q):
    tasks = [t for t in runtime_core.list_tasks(200) if t.get('kind') == 'motion_transfer']
    return _RESP({'ok': True, 'data': {'task': tasks[0] if tasks else None}})


@post('/api/motion/start')
def start(h, path, data):
    from models import anima_client
    from api import saga_api
    with _LOCK:
        try:
            if any(t.get('status') in ('running', 'queued') for t in runtime_core.list_tasks(200)):
                raise ValueError('已有任务运行或排队，请等任务结束')
            if any(j.get('running') for j in saga_api._JOBS.values()):
                raise ValueError('故事项目正在生成，请等任务结束')
            args = motion_core.validate(data or {})
            anima_client.local_guard()
            anima_client.idle_guard()
            task = runtime_core.create_task('motion_transfer', '动作迁移对照测试', args)
            return _RESP({'ok': True, 'data': {'task': task}})
        except Exception as exc:
            return _RESP({'ok': False, 'error': str(exc)[:400]}, 400)


@post('/api/motion/cancel')
def cancel(h, path, data):
    from models import h3_client
    tid = str((data or {}).get('id') or '')
    task = runtime_core.load(tid) if tid.startswith('task_') and tid[5:].isdigit() else None
    if not task or task.get('kind') != 'motion_transfer':
        return _RESP({'ok': False, 'error': '任务不存在'}, 404)
    was_rendering = task.get('status') == 'running' and task.get('step_label') == 'H3动作迁移生成中'
    runtime_core.action(tid, 'cancel')
    if was_rendering:
        h3_client.request_cancel()
    return _RESP({'ok': True, 'data': {'cancelled': True}})
