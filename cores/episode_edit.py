"""Non-destructive, version-bound episode edit lists. No model calls or source writes."""
import hashlib
import json
import math
import re
import subprocess
import threading
import time
from functools import lru_cache
from pathlib import Path

from . import store, story_core

_LOCK = threading.RLock()
_ROOT = Path(__file__).resolve().parent.parent


def _identity(sid, ep):
    sid = str(sid or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', sid):
        raise ValueError('项目编号无效')
    try:
        if isinstance(ep, bool) or str(int(ep)) != str(ep):
            raise ValueError()
        ep = int(ep)
        if ep < 1:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError('集数必须为正整数') from None
    return sid, ep


def _path(sid, ep):
    sid, ep = _identity(sid, ep)
    return store.DATA / 'episode_edits' / sid / ('episode_%d.json' % ep)


def _load(sid, ep):
    path = _path(sid, ep)
    if not path.exists():
        return dict(enabled=False, revision=0, items=[])
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or not isinstance(data.get('enabled'), bool)
                or not isinstance(data.get('items'), list)
                or not isinstance(data.get('revision'), int)):
            raise ValueError()
        return data
    except (ValueError, TypeError):
        raise ValueError('剪辑列表损坏，请检查剪辑记录；原片和成片均已保留') from None


@lru_cache(maxsize=256)
def _file_info(path, size, mtime_ns):
    """Cache only an unchanged file version; regenerated files are probed again."""
    p = Path(path)
    with p.open('rb') as source:
        content_hash = hashlib.file_digest(source, 'sha256').hexdigest()
    probed = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                             '-of', 'json', path], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=60)
    if probed.returncode:
        raise ValueError('视频无法读取')
    data = json.loads(probed.stdout)
    video = next((s for s in data.get('streams', []) if s.get('codec_type') == 'video'), {})
    duration = float(video.get('duration') or data.get('format', {}).get('duration') or 0)
    if not video or not math.isfinite(duration) or duration <= 0:
        raise ValueError('视频没有有效画面或时长')
    after = p.stat()
    if (after.st_size, after.st_mtime_ns) != (size, mtime_ns):
        raise ValueError('源视频正在更新，请稍后重新打开剪辑')
    fingerprint = hashlib.sha256(('%s|%s|%s|%s' %
                                 (path, size, mtime_ns, content_hash)).encode('utf-8')).hexdigest()
    return dict(duration=duration, has_audio=any(s.get('codec_type') == 'audio'
                                                for s in data.get('streams', [])),
                fingerprint=fingerprint)


def sources(sid, ep):
    """Read the timeline without ep_timeline's legacy-data migration side effect."""
    sid, ep = _identity(sid, ep)
    story = story_core.get_story(sid)
    if not story:
        raise ValueError('项目不存在')
    episodes = (story.get('saga') or {}).get('episodes') or []
    episode = next((e for e in episodes if int(e.get('no') or 0) == ep), None)
    timeline = ((episode or {}).get('timeline') or
                (story.get('timeline') if ep == 1 or not episodes else None) or {})
    rows = []
    for scene in timeline.get('scenes') or []:
        for segment in scene.get('segments') or []:
            video = str(segment.get('video') or '').strip()
            row = dict(segment_no=int(segment.get('no') or 0), scene_no=int(scene.get('no') or 1),
                       path=video, duration=0, has_audio=False, fingerprint='', usable=False, error='')
            try:
                if not video:
                    raise ValueError('本段尚未生成视频')
                p = Path(video)
                if not p.is_absolute():
                    p = _ROOT / p
                p = p.resolve()
                row['path'] = str(p)
                stat = p.stat()
                row.update(_file_info(str(p), stat.st_size, stat.st_mtime_ns))
                if segment.get('stale_video'):
                    raise ValueError('本段视频已过期，请重新生成后再选择区间')
                row['usable'] = True
            except (OSError, ValueError, TypeError, subprocess.TimeoutExpired) as error:
                row['error'] = str(error) or '视频暂不可用'
            rows.append(row)
    return sorted(rows, key=lambda r: (r['segment_no'], r['scene_no']))


def _validated(items, available):
    if not isinstance(items, list) or not 1 <= len(items) <= 200:
        raise ValueError('剪辑列表需包含 1 至 200 个区间；恢复原拼接请用恢复按钮')
    normalized = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError('第 %d 个剪辑区间无效' % index)
        matches = [s for s in available if s['segment_no'] == item.get('segment_no')
                   and ('scene_no' not in item or s['scene_no'] == item['scene_no'])]
        if not matches:
            raise ValueError('第 %d 个剪辑区间的源段不存在' % index)
        version = str(item.get('fingerprint') or '')
        current = [s for s in matches if s['fingerprint'] == version and version]
        if not current:
            raise ValueError('第 %d 个剪辑区间的源视频版本已改变，请重新选择区间' % index)
        if len(current) != 1:
            raise ValueError('第 %d 个剪辑区间来源不唯一，请指定场景' % index)
        source = current[0]
        if not source['usable']:
            raise ValueError('第 %d 个剪辑区间不可用：%s' % (index, source['error']))
        try:
            if isinstance(item.get('in'), bool) or isinstance(item.get('out'), bool):
                raise ValueError()
            start, end = float(item['in']), float(item['out'])
            if not all(map(math.isfinite, (start, end))) or not 0 <= start < end <= source['duration'] + .001:
                raise ValueError()
            # At least one output frame. Round only a <=1ms displayed duration discrepancy.
            end = min(end, source['duration'])
            if end - start < 1 / 24:
                raise ValueError()
        except (TypeError, ValueError, KeyError, OverflowError):
            raise ValueError('第 %d 个区间越界或过短：需 0 ≤ 入点 < 出点 ≤ %.3f 秒，至少一帧' %
                             (index, source['duration'])) from None
        normalized.append(dict(segment_no=source['segment_no'], scene_no=source['scene_no'],
                               fingerprint=version, **{'in': start, 'out': end}))
    return normalized


def _view(sid, ep, data, available):
    errors = []
    items = data['items'] if data['enabled'] else []
    if data['enabled']:
        try:
            _validated(items, available)
        except ValueError as error:
            errors.append(str(error))
    total = sum(float(x['out']) - float(x['in']) for x in items) if items and not errors else 0
    return dict(sid=sid, ep=int(ep), enabled=data['enabled'], revision=data['revision'],
                sources=available, items=items, errors=errors, total_duration=total)


def get_edit(sid, ep=1):
    with _LOCK:
        return _view(sid, ep, _load(sid, ep), sources(sid, ep))


def _check_revision(data, revision):
    if isinstance(revision, bool) or revision != data['revision']:
        raise ValueError('剪辑列表已改变，请刷新后再保存，避免覆盖另一页面的修改')


def save_edit(sid, ep, items, revision):
    with _LOCK:
        previous = _load(sid, ep)
        _check_revision(previous, revision)
        available = sources(sid, ep)
        normalized = _validated(items, available)
        data = dict(enabled=True, revision=previous['revision'] + 1,
                    updated=time.time(), items=normalized)
        store.atomic_write(_path(sid, ep), data)
        return _view(sid, ep, data, available)


def reset_edit(sid, ep, revision):
    with _LOCK:
        previous = _load(sid, ep)
        _check_revision(previous, revision)
        available = sources(sid, ep)
        data = dict(enabled=False, revision=previous['revision'] + 1,
                    updated=time.time(), items=[])
        store.atomic_write(_path(sid, ep), data)
        return _view(sid, ep, data, available)


def render_plan(sid, ep, segs=None):
    """None preserves legacy whole-clip behavior, including batch recovery."""
    with _LOCK:
        data = _load(sid, ep)
        if not data['enabled']:
            return None
        available = sources(sid, ep)
        items = _validated(data['items'], available)
        wanted = {int(no) for no in (segs or [])}
        result = []
        for item in items:
            if wanted and item['segment_no'] not in wanted:
                continue
            source = next(s for s in available if s['segment_no'] == item['segment_no']
                          and s['scene_no'] == item['scene_no'])
            result.append(dict(item, path=source['path'], duration=item['out'] - item['in'],
                               has_audio=source['has_audio']))
        if not result:
            raise ValueError('所选段不在已保存的剪辑列表中，请修改列表或恢复原拼接')
        return result


def verify_plan(sid, ep, plan):
    """Do not publish if a source was regenerated while the encoder was reading it."""
    _validated(plan, sources(sid, ep))
