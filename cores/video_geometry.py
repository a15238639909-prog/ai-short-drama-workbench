"""Local H3 geometry: preserve source aspect within the native 32px grid."""
from contextlib import contextmanager
import math


def dimensions(width, height, max_pixels=1152*640):
    width, height = int(width), int(height)
    if width <= 0 or height <= 0:
        raise ValueError('图片或视频尺寸无效')
    divisor = math.gcd(width, height)
    x, y = width//divisor, height//divisor
    unit = math.floor(math.sqrt(max_pixels/(1024*x*y)))
    if unit >= 1 and max(32*x*unit, 32*y*unit) <= 1536:
        w, h = 32*x*unit, 32*y*unit
    else:
        scale = min(math.sqrt(max_pixels/(width*height)), 1536/max(width, height))
        w, h = max(32, round(width*scale/32)*32), max(32, round(height*scale/32)*32)
        while w*h > max_pixels:
            if w >= h: w -= 32
            else: h -= 32
    return {'width': w, 'height': h, 'ratio': '%d:%d' % (x, y),
            'source_width': width, 'source_height': height}


def from_image(path):
    from PIL import Image, ImageOps
    with Image.open(path) as im:
        return dimensions(*ImageOps.exif_transpose(im).size)


def preserve_display_ratio(source, target, geometry):
    """Exact display ratio for uncommon source shapes off the native grid."""
    import shutil
    import subprocess
    w, h = geometry['width'], geometry['height']
    x, y = geometry['source_width'], geometry['source_height']
    if w*y == h*x:
        shutil.copy2(source, target)
        return
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-n', '-i', str(source),
        '-map', '0:v:0', '-map', '0:a:0?', '-vf', 'setdar=dar=%d/%d:max=65535' % (x, y),
        '-c:v', 'libx264', '-crf', '18', '-preset', 'fast', '-c:a', 'copy',
        '-movflags', '+faststart', str(target)], check=True, timeout=180,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


@contextmanager
def native_size(client, geometry):
    before = client._sized
    client._sized = lambda ratio, tier=None: (geometry['width'], geometry['height'])
    try:
        yield
    finally:
        client._sized = before
