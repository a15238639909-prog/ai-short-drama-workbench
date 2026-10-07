"""Bounded local media uploads for the standalone motion-transfer page."""
from pathlib import Path
import math
import uuid
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
UPLOADS = ROOT / 'outputs' / 'motion_uploads'
LIMITS = {'video': 250 * 1024 * 1024, 'character': 20 * 1024 * 1024, 'scene': 20 * 1024 * 1024}


def receive(handler):
    """Read raw bytes in chunks instead of buffering a base64 video in JSON."""
    kind = str(handler.headers.get('X-Media-Kind') or '')
    if kind not in LIMITS:
        raise ValueError('请选择原视频、人物照片或场景照片')
    name = unquote(str(handler.headers.get('X-Filename') or '')).replace('\\', '/').rsplit('/', 1)[-1]
    ext = Path(name).suffix.lower()
    allowed = {'.mp4', '.mov', '.mkv', '.webm'} if kind == 'video' else {'.png', '.jpg', '.jpeg', '.webp'}
    if ext not in allowed:
        raise ValueError('视频支持mp4/mov/mkv/webm，图片支持png/jpg/webp')
    try:
        size = int(handler.headers.get('Content-Length') or 0)
    except (TypeError, ValueError):
        raise ValueError('无法读取上传文件大小')
    if not 0 < size <= LIMITS[kind]:
        raise ValueError('视频最大250MB，人物或场景图片最大20MB')
    UPLOADS.mkdir(parents=True, exist_ok=True)
    dest = UPLOADS / (kind + '_' + uuid.uuid4().hex + ext)
    partial = dest.with_suffix(ext + '.part')
    previous_timeout = handler.connection.gettimeout()
    try:
        handler.connection.settimeout(60)
        remaining = size
        with partial.open('xb') as stream:
            while remaining:
                chunk = handler.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError('上传中断，请重新选择文件')
                stream.write(chunk)
                remaining -= len(chunk)
        if kind == 'video':
            from .motion_core import probe
            info = probe(partial)
            video = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'), None)
            duration = float(info.get('format', {}).get('duration') or 0)
            if not video or not math.isfinite(duration) or duration < 4:
                raise ValueError('原视频需要包含至少4秒的有效画面')
            meta = {'duration': duration, 'width': video.get('width'), 'height': video.get('height')}
        else:
            from PIL import Image
            with Image.open(partial) as im:
                if im.format not in ('PNG', 'JPEG', 'WEBP') or max(im.size) > 20000:
                    raise ValueError('图片格式或尺寸不支持')
                meta = {'width': im.width, 'height': im.height}
                im.verify()
        partial.replace(dest)
        return {'kind': kind, 'name': name, 'path': str(dest),
                'url': '/files/' + dest.relative_to(ROOT).as_posix(), 'bytes': size, **meta}
    except (ValueError, OSError, TimeoutError) as exc:
        raise ValueError('上传失败：' + str(exc)[:200]) from exc
    finally:
        handler.connection.settimeout(previous_timeout)
        if partial.exists():
            partial.unlink()
