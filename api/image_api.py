# -*- coding: utf-8 -*-
"""P401：🎨 出图词——一句话或整段提示词 → 超清图（和人设图 / 场景图同一条链）。"""
import os
from pathlib import Path
from api import get, post
from api._shared import _RESP
import threading

ROOT = Path(__file__).resolve().parent.parent
_ANIMA_LOCK = threading.Lock()
_MOSS_LOCK = threading.Lock()
_KREA_PROMPT_LOCK = threading.Lock()


@post('/api/image/krea2/prompt')
def krea2_long_prompt(h, path, d):
    from models import long_image_prompt
    if not isinstance(d, dict):
        return _RESP({'ok': False, 'error': '请求内容须为对象'}, 400)
    if not _KREA_PROMPT_LOCK.acquire(blocking=False):
        return _RESP({'ok': False, 'error': 'Krea2写词任务正在进行，请稍后重试'}, 409)
    try:
        result = long_image_prompt.write_prompt(d.get('text'), d.get('system'), 'krea2')
        return _RESP({'ok': True, 'data': result})
    except Exception as error:
        return _RESP({'ok': False, 'error': str(error)[:240]}, 400)
    finally:
        _KREA_PROMPT_LOCK.release()


@get('/api/image/anima/config')
def anima_config(h, path, q):
    from models import anima_client
    return _RESP({'ok': True, 'data': {'system': anima_client.SYSTEM.read_text(encoding='utf-8'),
                                     'name': 'Anima·英文词组',
                                     'artists': [dict(item) for item in anima_client.ARTISTS],
                                     'default_artist': anima_client.DEFAULT_ARTIST,
                                     'quality_prefix': list(anima_client.QUALITY_PREFIX),
                                     'fixed_prefix': anima_client.FIXED_PREFIX}})


@post('/api/image/anima/compose')
def anima_compose(h, path, d):
    from models import anima_client
    try:
        if not isinstance(d, dict):
            raise ValueError('请求内容须为对象')
        return _RESP({'ok': True, 'data': anima_client.compose_prompt(d.get('text'), d.get('artist'))})
    except (ValueError, TypeError) as error:
        return _RESP({'ok': False, 'error': str(error)[:240]}, 400)


@post('/api/image/anima/prompt')
def anima_prompt(h, path, d):
    from models import anima_client
    if not _ANIMA_LOCK.acquire(blocking=False):
        return _RESP({'ok': False, 'error': 'Anima任务正在进行，请稍后重试'}, 409)
    try:
        prompt = anima_client.write_prompt((d or {}).get('text'), (d or {}).get('system'),
                                           artist=(d or {}).get('artist'))
        return _RESP({'ok': True, 'data': dict(anima_client.compose_prompt(prompt), model='anima')})
    except Exception as e:
        return _RESP({'ok': False, 'error': str(e)[:240]}, 400)
    finally:
        _ANIMA_LOCK.release()


def anima_image(d):
    from models import anima_client
    from cores import gen_history
    if not _ANIMA_LOCK.acquire(blocking=False):
        return _RESP({'ok': False, 'error': 'Anima任务正在进行，请稍后重试'}, 409)
    try:
        prompt = anima_client.english_prompt(d.get('text'))
        res = anima_client.generate(prompt, base_w=d.get('base_w') or 1024,
                                    base_h=d.get('base_h') or 1024, seed=d.get('seed'),
                                    artist=d.get('artist'))
        prompt = res['prompt']
        out = res['output_path']
        import struct
        with open(out, 'rb') as f:
            header = f.read(24)
        if header[:8] != b'\x89PNG\r\n\x1a\n':
            raise RuntimeError('Anima返回的图片不是有效PNG')
        width, height = struct.unpack('>II', header[16:24])
        rel = Path(out).resolve().relative_to(ROOT).as_posix()
        gen_history.record('image', out, prompt, source='出图词·Anima',
                           extra={'model': 'anima', 'artist': res['artist'], 'seed': res['seed'],
                                  'width': width, 'height': height})
        return _RESP({'ok': True, 'data': {'path': rel, 'url': '/files/' + rel, 'prompt': prompt,
                                          'artist': res['artist'],
                                          'model': 'anima', 'seed': res['seed'], 'width': width, 'height': height}})
    except (ValueError, TypeError) as e:
        return _RESP({'ok': False, 'error': str(e)[:240]}, 400)
    except Exception as e:
        return _RESP({'ok': False, 'error': 'Anima出图失败：' + str(e)[:240]}, 500)
    finally:
        _ANIMA_LOCK.release()


@post('/api/image/moss/prompt')
def moss_prompt(h, path, d):
    from models import moss_prompt as writer
    if not isinstance(d, dict):
        return _RESP({'ok': False, 'error': '请求内容须为对象'}, 400)
    if not _MOSS_LOCK.acquire(blocking=False):
        return _RESP({'ok': False, 'error': 'Moss任务正在进行，请稍后重试'}, 409)
    try:
        options = {'upscale': d['upscale']} if 'upscale' in d else {}
        result = writer.write_prompt(d.get('text'), d.get('system'),
                                     base_w=d.get('base_w'), base_h=d.get('base_h'), **options)
        return _RESP({'ok': True, 'data': result})
    except Exception as error:
        return _RESP({'ok': False, 'error': str(error)[:240]}, 400)
    finally:
        _MOSS_LOCK.release()


def moss_image(d):
    from models import moss_client
    from cores import gen_history
    prompt = str(d.get('text') or '').strip()
    if not prompt:
        return _RESP({'ok': False, 'error': '先写要画什么'}, 400)
    if not _MOSS_LOCK.acquire(blocking=False):
        return _RESP({'ok': False, 'error': 'Moss任务正在进行，请稍后重试'}, 409)
    try:
        options = {'upscale': d['upscale']} if 'upscale' in d else {}
        result = moss_client.generate(prompt, base_w=d.get('base_w'),
                                      base_h=d.get('base_h'), seed=d.get('seed'), **options)
        output = Path(result['output_path']).resolve()
        relative = output.relative_to(ROOT.resolve()).as_posix()
        import struct
        with output.open('rb') as image:
            header = image.read(24)
        if len(header) < 24 or header[:8] != b'\x89PNG\r\n\x1a\n':
            raise RuntimeError('Moss返回的图片不是有效PNG')
        width, height = struct.unpack('>II', header[16:24])
        seed = result['seed']
        gen_history.record('image', str(output), prompt, source='出图词·Moss',
                           extra={'model': 'moss', 'seed': seed, 'width': width, 'height': height})
        return _RESP({'ok': True, 'data': {'path': relative, 'url': '/files/' + relative,
                                          'prompt': prompt, 'model': 'moss', 'seed': seed,
                                          'width': width, 'height': height}})
    except (ValueError, TypeError) as error:
        return _RESP({'ok': False, 'error': str(error)[:240]}, 400)
    except Exception as error:
        return _RESP({'ok': False, 'error': 'Moss出图失败：' + str(error)[:240]}, 500)
    finally:
        _MOSS_LOCK.release()


@post("/api/image/generate")
def image_generate(h, path, d):
    """{text, kind: character|scene|free, story_id?, style?} → {path, url, prompt}。同步：出一张约 2～3 分钟。"""
    model = str((d or {}).get('model') or 'krea2')
    if model == 'anima':
        return anima_image(d)
    if model == 'moss':
        return moss_image(d or {})
    if model != 'krea2':
        return _RESP({'ok': False, 'error': '不支持的出图模型'}, 400)
    from cores import story_core, project_settings, prompt_writer, gen_history
    from cores.authoring import _strip_neg
    from models import krea_client
    text = str((d or {}).get("text") or "").strip()
    kind = str((d or {}).get("kind") or "free").strip()
    sid = str((d or {}).get("story_id") or "").strip()
    if not text:
        return _RESP({"ok": False, "error": "先写要画什么"}, 400)
    st = story_core.get_story(sid) if sid else None
    P = project_settings.get(st or {})
    style = str((d or {}).get("style") or "").strip()
    if style:
        P = dict(P, style=style)
    if kind in ("character", "scene"):
        prompt = prompt_writer.write(kind, text, P)
        prompt = _strip_neg(prompt)
    else:
        prompt = text
    try:
        _bw = int((d or {}).get("base_w") or 0)
        _bh = int((d or {}).get("base_h") or 0)
    except Exception:
        _bw, _bh = 0, 0
    if _bw and _bh and (_bw * _bh > 2560 * 2560 or min(_bw, _bh) < 256):
        return _RESP({"ok": False, "error": "出底尺寸超范围（长边最多 2560）"}, 400)
    generation_options = {'base_w': _bw or None, 'base_h': _bh or None}
    if (d or {}).get('seed') is not None:
        generation_options['seed'] = d['seed']
    try:
        res = krea_client.generate(prompt, **generation_options)
    except Exception as e:
        return _RESP({"ok": False, "error": "出图失败：%s" % str(e)[:200]}, 500)
    out = str(res.get("output_path") or "")
    _w = _h = 0
    try:
        import struct
        with open(out, "rb") as _f:
            _hd = _f.read(24)
        if _hd[:8] == b"\x89PNG\r\n\x1a\n":
            _w, _h = struct.unpack(">II", _hd[16:24])
    except Exception:
        pass
    try:
        rel = str(Path(out).resolve().relative_to(ROOT)).replace("\\", "/")
    except Exception:
        rel = out.replace("\\", "/")
    gen_history.record("image", out, prompt, source="出图词", story_id=sid,
                       extra={"kind": kind, "style": P.get("style") or ""})
    return _RESP({"ok": True, "data": {"path": rel, "url": "/files/" + rel, "prompt": prompt, "style": P.get("style") or "", "width": _w, "height": _h}})
