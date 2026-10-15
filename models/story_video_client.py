"""Local-only model gateway for automatic stories; shared clients stay unchanged.

The worker serializes these calls. The GPU lock covers inference and the
temporary H3 geometry profile. Model processes may stay warm between calls;
acquire() checks PUBG and ownership on every call. Shared clients perform the
startup memory guard only when they actually need to load a model.
"""
import base64
from contextlib import contextmanager
import io
import ipaddress
import json
import math
import os
from pathlib import Path
import time
from urllib.parse import urlsplit
import uuid

from models import gpu_manager, h3_client, qwen_client

FPS = 24
WIDTH, HEIGHT = 1152, 640
PREVIEW_STEPS = 10
PROFILE = 'automatic-story-native-v1'
OFFLINE_ENV = {
    'HF_HUB_OFFLINE': '1',
    'TRANSFORMERS_OFFLINE': '1',
    'HF_DATASETS_OFFLINE': '1',
    'HF_HUB_DISABLE_TELEMETRY': '1',
    'NO_PROXY': '*',
    'no_proxy': '*',
}


def _save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                     default=str), encoding='utf-8')


def _loopback(url):
    parsed = urlsplit(str(url))
    host = parsed.hostname or ''
    try:
        local = host.lower() == 'localhost' or ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host.lower() == 'localhost'
    if (parsed.scheme not in ('http', 'https') or not local
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('自动故事只允许本机loopback模型端点，禁止云端地址或回退')
    return str(url)


@contextmanager
def _offline():
    before = {key: os.environ.get(key) for key in OFFLINE_ENV}
    os.environ.update(OFFLINE_ENV)
    try:
        yield
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def preserve_release(who=None):
    """Release this process's ownership by updating, never deleting, its file."""
    with gpu_manager.GPU_LOCK:
        current = gpu_manager.owner()
        if current and int(current.get('pid') or 0) == os.getpid():
            _save(gpu_manager._OWNER_FILE, {
                'who': who or current.get('who'), 'pid': None,
                'released_at': time.time(), 'released_by_pid': os.getpid(),
            })


def _new_trace(root, kind, request):
    if root is None:
        raise ValueError('自动故事模型调用必须提供本地trace_dir')
    folder = Path(root) / ('%s_%d_%s' % (kind, time.time_ns(), uuid.uuid4().hex[:8]))
    folder.mkdir(parents=True, exist_ok=False)
    _save(folder / 'request.json', request)
    return folder


def _user(payload):
    return payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)


def _image(path):
    from PIL import Image, ImageOps
    value = str(path)
    if '://' in value or value.startswith('data:'):
        raise ValueError('故事视觉输入必须是已有本地图片文件')
    source = Path(path).resolve()
    if not source.is_file() or source.stat().st_size > 20 * 1024 * 1024:
        raise ValueError('故事视觉图片不存在或超过20MB')
    with Image.open(source) as opened:
        if opened.width * opened.height > 40_000_000:
            raise ValueError('故事视觉图片超过4000万像素')
        im = ImageOps.exif_transpose(opened)
        if im.mode in ('RGBA', 'LA') or (im.mode == 'P' and 'transparency' in im.info):
            rgba = im.convert('RGBA')
            im = Image.new('RGB', rgba.size, 'white')
            im.paste(rgba, mask=rgba.getchannel('A'))
        else:
            im = im.convert('RGB')
        im.thumbnail((1280, 1280))
        encoded = io.BytesIO()
        im.save(encoded, format='JPEG', quality=90)
    return 'data:image/jpeg;base64,' + base64.b64encode(encoded.getvalue()).decode('ascii')


def _ask(kind, system, content, trace_dir, max_tokens, schema):
    endpoint = _loopback(qwen_client.QWEN_URL)
    if not isinstance(system, str) or not system.strip():
        raise ValueError('自动故事模型调用需要明确system指令')
    if isinstance(max_tokens, bool) or not 1 <= int(max_tokens) <= 16000:
        raise ValueError('自动故事回复额度须为1至16000 tokens')
    try:
        task_payload = json.loads(content) if isinstance(content, str) else {}
    except (ValueError, TypeError):
        task_payload = {}
    deliberate = kind == 'text' and bool(task_payload.get('count') or task_payload.get('correction'))
    options = dict(temperature=0.5 if deliberate else 0.1,
                   max_tokens=max(int(max_tokens), 10000) if deliberate else int(max_tokens), reasoning=deliberate,
                   return_meta=True, timeout=900)
    if schema is not None:
        options['response_format'] = {'type': 'json_object', 'schema': schema}
    request = {
        'endpoint': endpoint + '/v1/chat/completions',
        'model': qwen_client.QWEN_MODEL,
        'messages': [{'role': 'system', 'content': system},
                     {'role': 'user', 'content': content}],
        'options': options,
    }
    folder = _new_trace(trace_dir, kind, request)
    started = time.perf_counter()
    with gpu_manager.GPU_LOCK:
        try:
            with _offline():
                # acquire checks PUBG for warm calls without requiring another
                # 12 GB of free RAM after the model has already loaded.
                gpu_manager.acquire('qwen', note='automatic story ' + kind)
                reply, meta = qwen_client.chat(system, content, **options)
            _save(folder / 'response.json', {'text': reply, 'meta': meta,
                  'elapsed_seconds': time.perf_counter() - started})
            (folder / 'response.txt').write_text(str(reply), encoding='utf-8')
            if not isinstance(reply, str) or not reply.strip() or meta.get('finish_reason') == 'length':
                raise RuntimeError('本地故事模型回复为空或被截断，已保留原始回复')
            return reply
        except Exception as error:
            _save(folder / 'error.json', {'type': type(error).__name__, 'error': str(error),
                  'elapsed_seconds': time.perf_counter() - started})
            raise
        finally:
            preserve_release('qwen')


def text(system, payload, trace_dir, max_tokens=3600, schema=None):
    return _ask('text', system, _user(payload), trace_dir, max_tokens, schema)


def vision(system, payload, images, trace_dir, max_tokens=1800, schema=None):
    images = list(images or [])
    if not 1 <= len(images) <= 9:
        raise ValueError('自动故事视觉调用需要1至9张本地图片')
    content = [{'type': 'text', 'text': _user(payload)}]
    for number, path in enumerate(images, 1):
        content.extend([{'type': 'text', 'text': 'Observation image %d' % number},
                        {'type': 'image_url', 'image_url': {'url': _image(path)}}])
    return _ask('vision', system, content, trace_dir, max_tokens, schema)


def _frames(seconds):
    if isinstance(seconds, bool):
        raise ValueError('故事视频时长须为4至15秒')
    seconds = float(seconds)
    if not math.isfinite(seconds) or not 4 <= seconds <= 15:
        raise ValueError('故事视频时长须为4至15秒')
    desired = math.ceil(seconds * FPS)
    return 5 + 17 * math.ceil((desired - 5) / 17)


@contextmanager
def _native_profile(frame_count, folder, geometry=None):
    """Temporary in-process overrides, held under GPU_LOCK by video()."""
    sized, aligned, build = h3_client._sized, h3_client._aligned_frames, h3_client.build_workflow

    def traced_build(*args, **kwargs):
        graph, meta = build(*args, **kwargs)
        # Save the actual graph after H3's own local image bindings are made.
        _save(folder / 'workflow.json', {'graph': graph, 'meta': meta})
        return graph, meta

    h3_client._sized = lambda ratio, tier=None: ((geometry['width'], geometry['height']) if geometry else (WIDTH, HEIGHT))
    h3_client._aligned_frames = lambda seconds: frame_count
    h3_client.build_workflow = traced_build
    try:
        yield
    finally:
        h3_client._sized, h3_client._aligned_frames, h3_client.build_workflow = sized, aligned, build


def video(prompt, first_frame=None, seconds=15, trace_dir=None, recovery_key=None, seed=None, geometry=None):
    endpoint = _loopback(h3_client.H3_URL)
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('自动故事视频需要本地写作模型生成的提示词')
    if not recovery_key or not isinstance(recovery_key, str):
        raise ValueError('自动故事视频需要稳定的recovery_key以恢复原任务')
    frame_count = _frames(seconds)
    images = []
    if first_frame is not None:
        if '://' in str(first_frame) or str(first_frame).startswith('data:'):
            raise ValueError('故事视频首帧必须是已有本地图片文件')
        first = Path(first_frame).resolve()
        if not first.is_file():
            raise ValueError('故事视频首帧不存在')
        images = [str(first)]
    mode = 'i2va' if images else 't2va'
    options = dict(images=images, seconds=float(seconds), ratio=(geometry or {}).get('ratio', '16:9'), seed=seed, steps=PREVIEW_STEPS,
                   recovery_key=PROFILE + ':' + recovery_key)
    folder = _new_trace(trace_dir, 'video', {
        'endpoint': endpoint, 'mode': mode, 'prompt': prompt, 'options': options,
        'profile': PROFILE, 'width': (geometry or {}).get('width', WIDTH), 'height': (geometry or {}).get('height', HEIGHT), 'steps': PREVIEW_STEPS, 'quality': 'preview',
        'model_frames': frame_count, 'model_seconds': frame_count / FPS,
    })
    started = time.perf_counter()
    with gpu_manager.GPU_LOCK:
        try:
            with _offline():
                gpu_manager.acquire('h3', note='automatic story video')
                with _native_profile(frame_count, folder, geometry):
                    raw = h3_client.generate(mode, prompt, **options)
            _save(folder / 'response.json', {'result': raw,
                  'elapsed_seconds': time.perf_counter() - started})
            result = dict(raw)
            # Shared meta.seconds rounds to an integer. Report this profile's
            # actual native grid; core media adoption probes and trims the file.
            result.update(frames=frame_count, seconds=frame_count / FPS,
                          requested_seconds=float(seconds), width=(geometry or {}).get('width', WIDTH), height=(geometry or {}).get('height', HEIGHT),
                          fps=FPS, steps=PREVIEW_STEPS, quality='preview', profile=PROFILE, trace_dir=str(folder.resolve()))
            _save(folder / 'result.json', result)
            return result
        except Exception as error:
            _save(folder / 'error.json', {'type': type(error).__name__, 'error': str(error),
                  'elapsed_seconds': time.perf_counter() - started})
            raise
        finally:
            preserve_release('h3')
