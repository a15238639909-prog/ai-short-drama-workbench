"""Runtime reset barrier shared by the server and its generation subprocesses.

Only transient execution state lives here. Project files and generated media
are never removed. A worker keeps the generation in which it was started, so
clearing the workbench cannot turn an old retry into a new model request.
"""
import json
import os
import threading
import time
import uuid
from pathlib import Path

STATE = Path(__file__).resolve().parents[1] / 'cache' / 'runtime_reset.json'
LOCK = threading.RLock()
_local = threading.local()


class Cancelled(RuntimeError):
    pass


def _read():
    try:
        value = json.loads(STATE.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or not value.get('generation'):
            raise ValueError('invalid runtime reset state')
        return value
    except FileNotFoundError:
        return {'generation': 'initial', 'clearing': False}
    except (OSError, ValueError) as exc:
        # An unreadable barrier must not silently let a cancelled worker run.
        raise Cancelled('无法读取清理状态，请重新点击清理缓存') from exc


def _write(value):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_name(STATE.name + '.' + uuid.uuid4().hex + '.next')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, STATE)


def generation():
    return _read()['generation']


def capture():
    return getattr(_local, 'generation', None) or os.environ.get('V41_RUNTIME_GENERATION') or generation()


def worker(target):
    """Capture at submission, rather than at eventual thread execution."""
    epoch = capture()
    def run(*args, **kwargs):
        bind_thread(epoch)
        try:
            assert_current()
            return target(*args, **kwargs)
        except Cancelled:
            return None
        finally:
            bind_thread(None)
    return run


def admission(target):
    import functools
    @functools.wraps(target)
    def call(*args, **kwargs):
        with LOCK:
            assert_current()
            return target(*args, **kwargs)
    return call


def clear_jobs(jobs):
    now = time.time()
    cancelled = 0
    for job in list(jobs.values()):
        if not isinstance(job, dict):
            continue
        if job.get('running') or job.get('status') in ('queued', 'running', 'retrying', 'waiting_resource', 'waiting_task'):
            cancelled += 1
        job.update(cleared_at=now, cancel=True, running=False, status='cancelled',
                   step='', done=0, total=0, percent=0, err='', error='',
                   eta_seconds=None, updated_at=now)
    return cancelled


def job_view(job):
    if job and job.get('cleared_at'):
        return {'running': False, 'status': 'cancelled', 'cleared_at': job['cleared_at'],
                'step': '', 'done': 0, 'total': 0, 'percent': 0, 'err': '', 'logs': []}
    return job


def bind_thread(epoch):
    if epoch is None:
        if hasattr(_local, 'generation'):
            del _local.generation
    else:
        _local.generation = str(epoch)


def assert_current(epoch=None):
    state = _read()
    if epoch is None:
        epoch = getattr(_local, 'generation', None)
        if epoch is None:
            epoch = os.environ.get('V41_RUNTIME_GENERATION') or state['generation']
            bind_thread(epoch)
    if state.get('clearing') or str(epoch) != state['generation']:
        raise Cancelled('任务已被清理取消，请重新开始新任务')
    return True


def assert_stop_allowed():
    # The reset request itself may stop models while the start gate is closed.
    # An older worker must not stop a new task's model after reset has finished.
    if capture() != generation():
        raise Cancelled('任务已被清理取消')


def begin():
    with LOCK:
        # The API serializes resets. A fresh click can also recover a previous
        # failed reset or a server shutdown during cleanup.
        state = {'generation': uuid.uuid4().hex, 'clearing': True,
                 'started_at': time.time()}
        _write(state)
        return state['generation']


def finish(epoch, error=''):
    with LOCK:
        state = _read()
        if state['generation'] != epoch:
            raise Cancelled('清理状态已改变，请重新检查')
        state.update(clearing=bool(error), error=str(error), finished_at=time.time())
        _write(state)


def recycle_transient(path):
    """Remove only a named runtime marker, through the Windows Recycle Bin."""
    import subprocess
    path = Path(path).resolve()
    cache = STATE.parent.resolve()
    if path.parent != cache or path.name not in ('game_mode.flag', 'current_task.json'):
        raise ValueError('禁止清理非运行标记文件：' + str(path))
    if not path.exists():
        return False
    if not path.is_file() or path.is_symlink():
        raise RuntimeError('运行标记不是普通文件，已保留：' + str(path))
    if os.name != 'nt':
        raise RuntimeError('无法使用 Windows 回收站，文件已保留：' + str(path))
    script = ("Add-Type -AssemblyName Microsoft.VisualBasic; "
              "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
              "$env:V41_RECYCLE_PATH, 'OnlyErrorDialogs', 'SendToRecycleBin', 'ThrowException')")
    env = dict(os.environ, V41_RECYCLE_PATH=str(path))
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            env=env, capture_output=True, text=True, timeout=30,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode or path.exists():
        raise RuntimeError('无法移入回收站，文件已保留：' + str(path))
    return True
