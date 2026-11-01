"""Episode edit-list endpoints. All edits are non-destructive and version-bound."""
from api import get, post
from cores import episode_edit
from urllib.parse import parse_qs


def _call(function, *args):
    try:
        return {'ok': True, 'data': function(*args)}
    except (ValueError, OSError) as error:
        return {'ok': False, 'error': str(error)}


def _query(query, name, default=''):
    if isinstance(query, str):
        query = parse_qs(query)
    query = query or {}
    value = query.get(name, default)
    return value[0] if isinstance(value, list) and value else value


@get('/api/episode/edit')
def get_edit(h, path, query):
    return _call(episode_edit.get_edit, _query(query, 'sid'), _query(query, 'ep', '1'))


@post('/api/episode/edit/save')
def save_edit(h, path, data):
    return _call(episode_edit.save_edit, data.get('sid'), data.get('ep', 1),
                 data.get('items'), data.get('revision'))


@post('/api/episode/edit/reset')
def reset_edit(h, path, data):
    return _call(episode_edit.reset_edit, data.get('sid'), data.get('ep', 1), data.get('revision'))
