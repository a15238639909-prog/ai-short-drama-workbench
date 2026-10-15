"""Discover completed movies from the existing composition outputs, without moving files."""
import json
from pathlib import Path


def _read(path):
    try:
        data = path.read_text(encoding='utf-8-sig')
        result = json.loads(data)
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def collect(root):
    root = Path(root).resolve()
    movies = {}

    def add(value, source, title='', prompt='', seconds=None, story_id=''):
        if not value or not isinstance(value, str):
            return
        path = Path(value)
        path = (root / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_relative_to(root) or path.suffix.lower() not in ('.mp4', '.webm', '.mov'):
            return
        try:
            stat = path.stat()
            if not path.is_file() or not stat.st_size:
                return
        except OSError:
            return
        key = str(path).casefold()
        extra = dict(finished=True, title=title or path.stem)
        if seconds:
            extra['seconds'] = seconds
        movies[key] = dict(path=str(path), kind='video', ts=stat.st_mtime,
                           source=source, prompt=prompt, story_id=story_id, extra=extra)

    # Main episode/selected-segment merges only publish this name after validation.
    for path in (root / 'outputs').glob('成片_*.mp4'):
        add(str(path), '主流程合成', path.stem.removeprefix('成片_'))
    for path in (root / 'productions' / 'video').glob('*/final.mp4'):
        add(str(path), '视频拼接', path.parent.name)
    # Older formal-production records can point to a custom destination.
    for path in (root / 'data' / 'productions').glob('*.json'):
        record = _read(path)
        output = record.get('output') or {}
        if isinstance(output, dict):
            add(output.get('output_path') or output.get('path'), '视频拼接',
                seconds=output.get('duration'), story_id=record.get('story_id', ''))
        if record.get('kind') == 'video' and record.get('status') == 'completed':
            for value in record.get('files') or []:
                add(value, '视频拼接', story_id=record.get('story_id', ''))
    for path in (root / 'outputs' / 'story_video').glob('*/manifest.json'):
        manifest = _read(path)
        plan = manifest.get('plan') or {}
        prompt = (manifest.get('inputs') or {}).get('idea') or ''
        versions = [row.get('output') for row in (manifest.get('history') or []) if isinstance(row, dict)]
        versions.append(manifest.get('output'))
        for output in versions:
            if isinstance(output, dict):
                add(output.get('output_path'), '故事视频', output.get('title') or plan.get('title', ''),
                    prompt=prompt, seconds=output.get('seconds'))
    for path in (root / 'outputs' / 'motion').glob('*/result.json'):
        result = _read(path)
        add(result.get('output_path'), '动作迁移', '动作迁移 · ' + path.parent.name,
            prompt=result.get('prompt', ''), seconds=result.get('seconds'))
    return list(movies.values())
