"""Independent long-comic project endpoints; workers start only on explicit run."""
from api import get, post
from api._shared import _RESP
from cores import long_comic_core as comic, runtime_reset

PREFIX = '/api/long-comic/project/'
SOURCE_PREFIX = '/api/long-comic/sources/'


def _project_id(path, suffix=''):
    identity = path[len(PREFIX):]
    if suffix:
        identity = identity[:-len(suffix)]
    comic._path(identity)
    return identity


def _body(data):
    if not isinstance(data, dict):
        raise ValueError('请求内容须为对象')
    return data


def _response(callback):
    try:
        return _RESP({'ok': True, 'data': callback()})
    except (comic.BusyError, runtime_reset.Cancelled) as error:
        return _RESP({'ok': False, 'error': str(error)[:600]}, 409)
    except (ValueError, TypeError) as error:
        return _RESP({'ok': False, 'error': str(error)[:600]}, 400)
    except Exception as error:
        return _RESP({'ok': False, 'error': '漫画操作失败，原文件已保留：' + str(error)[:400]}, 500)


@get('/api/long-comic/projects')
def list_projects(handler, path, query):
    return _response(lambda: {'projects': comic.list_projects()})


@get('/api/long-comic/sources')
def list_sources(handler, path, query):
    return _response(lambda: {'sources': comic.list_character_sources()})


@get({'prefix': SOURCE_PREFIX, 'suffix': '/characters', 'count': 5})
def source_characters(handler, path, query):
    def read():
        from urllib.parse import unquote
        identity = unquote(path[len(SOURCE_PREFIX):-len('/characters')])
        if not identity or identity in ('.', '..') or any(value in identity for value in ('/', '\\', '\x00')):
            raise ValueError('人物来源项目无效')
        return {'characters': comic.get_source_characters(identity)}
    return _response(read)


@post('/api/long-comic/project')
def create_project(handler, path, data):
    return _response(lambda: {'project': comic.create_project(_body(data))})


@get({'prefix': PREFIX, 'count': 4})
def get_project(handler, path, query):
    return _response(lambda: {'project': comic.get_project(_project_id(path))})


@post({'prefix': PREFIX, 'suffix': '/save', 'count': 5})
def save_project(handler, path, data):
    return _response(lambda: {'project': comic.update_project(_project_id(path, '/save'), _body(data))})


@post({'prefix': PREFIX, 'suffix': '/run', 'count': 5})
def run_project(handler, path, data):
    def run():
        payload = _body(data)
        project = comic.start_job(_project_id(path, '/run'), payload.get('action'),
            chapter=payload.get('chapter', 1), page=payload.get('page'))
        return {'project': project}
    return _response(run)


@post({'prefix': PREFIX, 'suffix': '/pause', 'count': 5})
def pause_project(handler, path, data):
    return _response(lambda: {'project': comic.pause_job(_project_id(path, '/pause'))})


@post({'prefix': PREFIX, 'suffix': '/reference', 'count': 5})
def bind_reference(handler, path, data):
    def bind():
        payload = _body(data)
        return {'project': comic.bind_reference(_project_id(path, '/reference'),
            payload.get('character_id'), payload.get('image_data'))}
    return _response(bind)


@post({'prefix': PREFIX, 'suffix': '/edit', 'count': 5})
def edit_page(handler, path, data):
    def run():
        payload = _body(data)
        options = {key: payload[key] for key in
                   ('base_version', 'region', 'character_ids', 'prompt', 'seed') if key in payload}
        return {'project': comic.start_job(_project_id(path, '/edit'), 'edit_page',
            chapter=payload.get('chapter', 1), page=payload.get('page'), edit=options)}
    return _response(run)


@post({'prefix': PREFIX, 'suffix': '/adopt', 'count': 5})
def adopt_version(handler, path, data):
    def adopt():
        payload = _body(data)
        return {'project': comic.adopt_version(_project_id(path, '/adopt'),
            payload.get('chapter', 1), payload.get('page'), payload.get('version'))}
    return _response(adopt)


@post({'prefix': PREFIX, 'suffix': '/action', 'count': 5})
def task_action(handler, path, data):
    def run():
        payload = _body(data)
        identity = _project_id(path, '/action')
        action = payload.get('action')
        if action == 'pause':
            result = comic.pause_job(identity)
        elif action == 'cancel':
            result = comic.cancel_job(identity)
        elif action == 'resume':
            project = comic.get_project(identity)
            job = project.get('job') or {}
            if job.get('status') not in ('paused', 'interrupted', 'failed', 'cancelled'):
                raise ValueError('当前漫画任务无需恢复')
            if job.get('action') == 'edit_page':
                raise ValueError('修图任务请先核对候选和后台状态，再重新选择区域提交；不会自动重做整章')
            result = comic.start_job(identity, job.get('action', 'generate'),
                chapter=job.get('chapter', 1), page=job.get('page'))
        else:
            raise ValueError('任务操作须为pause、resume或cancel')
        return {'project': result}
    return _response(run)


@post({'prefix': PREFIX, 'suffix': '/export', 'count': 5})
def export_project(handler, path, data):
    def export():
        payload = _body(data)
        return comic.export_project(_project_id(path, '/export'),
            chapter=payload.get('chapter'), kind=payload.get('kind', 'zip'))
    return _response(export)
