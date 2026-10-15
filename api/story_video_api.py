"""Standalone local story-video endpoints; model/task policy belongs to the core."""
from importlib import import_module
import re
from urllib.parse import parse_qs

from api import get, post


def _core():
    return import_module('cores.story_video_core')


def _response(call, *args):
    try:
        result = call(*args)
        if not isinstance(result, dict):
            raise RuntimeError('故事视频服务返回格式不正确')
        return {'ok': True, 'data': result}
    except (ValueError, TypeError) as error:
        return {'ok': False, 'error': str(error)[:600], '_status': 400}
    except (ImportError, FileNotFoundError) as error:
        return {'ok': False, 'error': '故事视频功能尚未准备好：' + str(error)[:400], '_status': 503}
    except RuntimeError as error:
        return {'ok': False, 'error': str(error)[:600], '_status': 409}
    except Exception as error:
        return {'ok': False, 'error': str(error)[:600], '_status': 500}


def _tid(value, optional=False):
    value = str(value or '').strip()
    if optional and not value:
        return ''
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ValueError('任务编号无效')
    return value


@get('/api/story-video/capabilities')
def capabilities(h, path, query):
    return _response(lambda: _core().capabilities())


@post('/api/image-drop')
def image_drop(h, path, data):
    def run():
        from cores.image_drop import read_image
        host, port = h.server.server_address[:2]
        request_host = str(h.headers.get('Host') or '')
        if request_host not in ('127.0.0.1:%d' % port, 'localhost:%d' % port, '[::1]:%d' % port):
            raise ValueError('请通过本机工作台拖入图片')
        return read_image((data or {}).get('url'), 'http://' + request_host)
    return _response(run)


@post('/api/story-video/start')
def start(h, path, data):
    def run():
        if not isinstance(data, dict):
            raise ValueError('请提交故事方向和生成设置')
        # start() owns admission, PUBG checks and the independent worker lifecycle.
        return _core().start(data)
    return _response(run)


@get('/api/story-video/status')
def status(h, path, query):
    def run():
        values = parse_qs(str(query or ''))
        return _core().status(_tid((values.get('id') or [''])[0], optional=True))
    return _response(run)


@post('/api/story-video/action')
def action(h, path, data):
    def run():
        if not isinstance(data, dict):
            raise ValueError('任务操作格式不正确')
        act = str(data.get('action') or '').strip()
        if act not in ('pause', 'resume', 'cancel', 'retry', 'approve', 'rewrite', 'redo'):
            raise ValueError('不支持这项任务操作')
        return _core().action(_tid(data.get('id')), act, data.get('segment'))
    return _response(run)
