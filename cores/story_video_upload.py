"""Bounded image upload for story-video; interrupted files are retained."""
from pathlib import Path
from urllib.parse import quote, unquote
import uuid

ROOT = Path(__file__).resolve().parent.parent
UPLOADS = ROOT / 'outputs' / 'story_video_uploads'
MAX_BYTES = 20 * 1024 * 1024
MAX_SIDE = 20000


def receive(handler):
    name = unquote(str(handler.headers.get('X-Filename') or '')).replace('\\', '/').rsplit('/', 1)[-1]
    extension = Path(name).suffix.lower()
    if extension not in ('.png', '.jpg', '.jpeg', '.webp'):
        raise ValueError('起始图支持PNG、JPG和WebP')
    try:
        size = int(handler.headers.get('Content-Length') or 0)
    except (ValueError, TypeError):
        raise ValueError('无法读取图片大小')
    if not 0 < size <= MAX_BYTES:
        raise ValueError('请选择不超过20MB的图片')
    UPLOADS.mkdir(parents=True, exist_ok=True)
    destination = UPLOADS / ('opening_' + uuid.uuid4().hex + extension)
    partial = destination.with_suffix(extension + '.part')
    if destination.resolve().parent != UPLOADS.resolve() or partial.resolve().parent != UPLOADS.resolve():
        raise ValueError('上传目标目录不正确')
    previous_timeout = handler.connection.gettimeout()
    try:
        handler.connection.settimeout(60)
        remaining = size
        with partial.open('xb') as stream:
            while remaining:
                chunk = handler.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError('上传中断，已有部分文件保留，可重新上传')
                stream.write(chunk)
                remaining -= len(chunk)
        from PIL import Image
        with Image.open(partial) as image:
            if image.format not in ('PNG', 'JPEG', 'WEBP') or not 0 < min(image.size) <= max(image.size) <= MAX_SIDE:
                raise ValueError('图片格式或尺寸不支持')
            dimensions = {'width': image.width, 'height': image.height}
            image.verify()
        partial.rename(destination)
        relative = destination.relative_to(ROOT).as_posix()
        return {'ok': True, 'data': {'path': str(destination), 'url': '/files/' + quote(relative, safe='/'),
                                     'name': name, 'bytes': size, **dimensions}}
    except (ValueError, OSError, TimeoutError) as error:
        # No cleanup/unlink: failed and partial uploads stay in our own directory.
        raise ValueError('上传失败：' + str(error)[:250]) from error
    finally:
        handler.connection.settimeout(previous_timeout)
