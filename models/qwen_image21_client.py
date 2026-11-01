"""Qwen Image 2.1 reference editing on an already running local ComfyUI.

The graph follows the user's editing workflow nodes 685/675/676/681/705/679/678.
It deliberately omits the optional LoRA and expansion branches. This adapter
never starts/stops services, clears model memory, or interrupts the global queue.
Original inputs and every produced artifact are retained.
"""
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import shutil
import time
import urllib.parse
import uuid


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / 'outputs' / 'qwen_image21'
MODEL_FILE = 'Qwen21-rayQwen21_v1_int8_convrot.safetensors'
ENCODER_FILE = 'qwen3vl_8b_int8_convrot.safetensors'
VAE_FILE = 'qwen_image_2.1_vae_bf16.safetensors'
SOURCE_WORKFLOW = '▶▷Qwen-image21-功能流(高分局部编辑&扩图).json'
SOURCE_NODES = ('685', '675', '676', '681', '705', '679', '678')
SAVE_NODE = '800'
NEGATIVE_PROMPT = ('artifacts, gpt-image, washed-out colors, low quality, low resolution, '
                   'AI slop, deviantart, sloppy lines, rough sketch, blurry, indistinct, '
                   'missing fingers, badly drawn hands, wrong number of fingers')


class QwenImage21Error(RuntimeError):
    def __init__(self, message, *, prompt_id=None):
        super().__init__(message)
        self.prompt_id = prompt_id


def _references(references):
    if not isinstance(references, (tuple, list)) or len(references) > 3:
        raise ValueError('身份参考图须为数组，最多3张')
    return list(references)


def _input_name(value):
    if not isinstance(value, str) or not value or any(c in value for c in ('\\', ':', '\x00')):
        raise ValueError('ComfyUI输入图名无效')
    pieces = value.split('/')
    if any(piece in ('', '.', '..') for piece in pieces):
        raise ValueError('ComfyUI输入图名须为input目录内的相对路径')
    return value


def build_workflow(image, prompt, references=(), seed=None, resolution=0):
    """Build an API graph from names already placed in ComfyUI input; no I/O."""
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16000:
        raise ValueError('编辑指令须为1至16000字的文字')
    if seed is None:
        seed = uuid.uuid4().int % (2 ** 63)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2 ** 64:
        raise ValueError('种子须为0至2^64-1之间的整数')
    if isinstance(resolution, bool) or not isinstance(resolution, int) or resolution < 0:
        raise ValueError('分辨率须为非负整数；0沿用待改图尺寸')
    images = [_input_name(image)] + [_input_name(item) for item in _references(references)]
    graph = {
        '685': {'class_type': 'UNETLoader', 'inputs': {
            'unet_name': MODEL_FILE, 'weight_dtype': 'default'}},
        '675': {'class_type': 'CLIPLoader', 'inputs': {
            'clip_name': ENCODER_FILE, 'type': 'qwen_image', 'device': 'default'}},
        '676': {'class_type': 'VAELoader', 'inputs': {'vae_name': VAE_FILE}},
        '681': {'class_type': 'QwenImage21Cache', 'inputs': {
            'model': ['685', 0], 'device': 'auto', 'dtype': 'default'}},
        '705': {'class_type': 'TextEncodeQwenImage21', 'inputs': {
            'clip': ['675', 0], 'vae': ['676', 0], 'prompt': prompt,
            'negative_prompt': NEGATIVE_PROMPT, 'resolution': resolution}},
        '679': {'class_type': 'KSampler', 'inputs': {
            'model': ['681', 0], 'positive': ['705', 0], 'negative': ['705', 1],
            'latent_image': ['705', 2], 'seed': seed, 'steps': 25, 'cfg': 1,
            'sampler_name': 'euler', 'scheduler': 'simple', 'denoise': 1}},
        '678': {'class_type': 'VAEDecode', 'inputs': {
            'samples': ['679', 0], 'vae': ['676', 0]}},
        SAVE_NODE: {'class_type': 'SaveImage', 'inputs': {
            'images': ['678', 0], 'filename_prefix': 'QwenImage21-Workbench-' + uuid.uuid4().hex}},
    }
    for index, name in enumerate(images, 1):
        node = str(800 + index)
        graph[node] = {'class_type': 'LoadImage', 'inputs': {'image': name}}
        graph['705']['inputs']['images.image_%d' % index] = [node, 0]
    return graph


def _local_service():
    from . import krea_client
    try:
        parsed = urllib.parse.urlsplit(krea_client.COMFY_URL)
        hostname = parsed.hostname or ''
        local = hostname == 'localhost' or ipaddress.ip_address(hostname).is_loopback
        valid = (local and parsed.scheme in ('http', 'https') and parsed.port == 8188
                 and parsed.path in ('', '/') and not parsed.query and not parsed.fragment
                 and parsed.username is None and parsed.password is None)
    except ValueError:
        valid = False
    if not valid:
        raise QwenImage21Error('Qwen图片编辑仅连接本机已运行的8188服务，请核对ComfyUI地址')


def _queue_idle():
    from . import krea_client
    try:
        queue = krea_client._http('GET', '/queue', timeout=10)
    except Exception as error:
        raise QwenImage21Error('无法确认ComfyUI队列状态，未提交编辑任务') from error
    if (not isinstance(queue, dict) or not isinstance(queue.get('queue_running'), list)
            or not isinstance(queue.get('queue_pending'), list)):
        raise QwenImage21Error('ComfyUI队列状态未知，未提交编辑任务')
    if queue['queue_running'] or queue['queue_pending']:
        raise QwenImage21Error('ComfyUI已有任务，请等完成后再编辑图片')


def _capabilities(graph):
    from . import krea_client
    available = krea_client._http('GET', '/object_info', timeout=30)
    if not isinstance(available, dict):
        raise QwenImage21Error('无法读取ComfyUI节点信息')
    missing = sorted({node['class_type'] for node in graph.values()} - set(available))
    if missing:
        raise QwenImage21Error('ComfyUI缺少Qwen Image 2.1节点：' + '、'.join(missing))
    for node, field, expected in (('UNETLoader', 'unet_name', MODEL_FILE),
                                  ('CLIPLoader', 'clip_name', ENCODER_FILE),
                                  ('VAELoader', 'vae_name', VAE_FILE)):
        try:
            choices = available[node]['input']['required'][field][0]
        except (KeyError, TypeError, IndexError) as error:
            raise QwenImage21Error('无法确认Qwen Image 2.1模型清单') from error
        if not isinstance(choices, list) or expected not in choices:
            raise QwenImage21Error('ComfyUI未发现所需模型：' + expected)


def _image_info(path, *, png=False):
    from PIL import Image
    try:
        with path.open('rb') as stream, Image.open(stream) as image:
            if png and image.format != 'PNG':
                raise ValueError('not PNG')
            width, height = image.size
            image_format = image.format
            image.verify()
        if width <= 0 or height <= 0:
            raise ValueError('empty image')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        return dict(width=width, height=height, format=image_format, sha256=digest.hexdigest())
    except (OSError, ValueError, SyntaxError) as error:
        raise QwenImage21Error('图片无法读取或不是有效PNG' if png else '参考图片无法读取或已损坏') from error


def _prepare_inputs(image_path, references):
    formats = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp', 'BMP': '.bmp', 'TIFF': '.tiff'}
    batch = uuid.uuid4().hex
    rows = []
    for index, value in enumerate([image_path] + _references(references)):
        if not isinstance(value, (str, Path)) or not str(value):
            raise ValueError('请提供待改图片和参考图的文件路径')
        path = Path(value).resolve()
        if not path.is_file():
            raise ValueError('待改图片或参考图不存在')
        info = _image_info(path)
        if info['format'] not in formats:
            raise ValueError('参考图须为PNG、JPEG、WEBP、BMP或TIFF')
        name = 'qwen_image21/%s_%d%s' % (batch, index, formats[info['format']])
        rows.append(dict(path=path, name=name, info=info, role='target' if index == 0 else 'reference'))
    return rows


def _directories():
    from cores import paths
    from . import krea_client
    configured = paths.get('comfy_output')
    if not configured or not krea_client.COMFY_DIR:
        raise QwenImage21Error('请先配置ComfyUI目录和comfy_output，编辑器不会自动启动服务')
    input_root = (Path(krea_client.COMFY_DIR) / 'ComfyUI' / 'input').resolve()
    output_root = Path(configured).resolve()
    if not input_root.is_dir() or not output_root.is_dir():
        raise QwenImage21Error('已配置的ComfyUI输入或输出目录不存在')
    return input_root, output_root


def _copy_inputs(rows, input_root):
    from cores import runtime_reset
    for row in rows:
        runtime_reset.assert_current()
        destination = (input_root / row['name']).resolve()
        if not destination.is_relative_to(input_root):
            raise QwenImage21Error('输入图片路径超出ComfyUI输入目录')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with row['path'].open('rb') as source, destination.open('xb') as target:
            shutil.copyfileobj(source, target)
        if _image_info(destination)['sha256'] != row['info']['sha256']:
            raise QwenImage21Error('参考图片在复制期间发生变化，请重新提交；原文件已保留')


def _store_result(image, graph, prompt_id, rows, output_root, elapsed):
    from cores import runtime_reset
    if not isinstance(image, dict) or image.get('type', 'output') != 'output':
        raise QwenImage21Error('编辑结果不是ComfyUI输出图片', prompt_id=prompt_id)
    filename = image.get('filename')
    subfolder = image.get('subfolder') or ''
    if (not isinstance(filename, str) or not filename or
            any(char in filename for char in ('/', '\\', ':', '\x00')) or
            Path(filename).suffix.lower() != '.png' or not isinstance(subfolder, str)):
        raise QwenImage21Error('编辑结果文件名无效', prompt_id=prompt_id)
    source = (output_root / subfolder / filename).resolve()
    if not source.is_relative_to(output_root):
        raise QwenImage21Error('编辑结果路径超出ComfyUI输出目录', prompt_id=prompt_id)
    if not source.is_file():
        raise QwenImage21Error('编辑结果图片文件不存在', prompt_id=prompt_id)
    _image_info(source, png=True)
    runtime_reset.assert_current()
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    destination = OUTPUT_ROOT / ('qwen21_' + uuid.uuid4().hex + '.png')
    with source.open('rb') as reader, destination.open('xb') as target:
        shutil.copyfileobj(reader, target)
    info = _image_info(destination, png=True)
    runtime_reset.assert_current()
    seed = graph['679']['inputs']['seed']
    resolution = graph['705']['inputs']['resolution']
    metadata = dict(model='qwen_image21', model_file=MODEL_FILE, encoder=ENCODER_FILE, vae=VAE_FILE,
        source_workflow=SOURCE_WORKFLOW, source_nodes=list(SOURCE_NODES), prompt_id=prompt_id,
        seed=seed, resolution=resolution, steps=25, cfg=1, sampler='euler', scheduler='simple',
        denoise=1, created_at=time.time(), elapsed_seconds=elapsed, output_file=destination.name,
        prompt_sha256=hashlib.sha256(graph['705']['inputs']['prompt'].encode('utf-8')).hexdigest(),
        inputs=[dict(role=row['role'], **row['info']) for row in rows], **info)
    meta_path = destination.with_suffix('.json')
    with meta_path.open('x', encoding='utf-8') as stream:
        json.dump(metadata, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    runtime_reset.assert_current()
    return dict(output_path=str(destination), output_file=destination.name,
                metadata_path=str(meta_path), model='qwen_image21', prompt_id=prompt_id,
                seed=seed, resolution=resolution, width=info['width'], height=info['height'],
                sha256=info['sha256'], input_count=len(rows))


def _wait(prompt_id, timeout):
    from cores import runtime_reset
    from . import krea_client
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runtime_reset.assert_current()
        try:
            history = krea_client._http('GET', '/history/' + prompt_id, timeout=30)
        except runtime_reset.Cancelled:
            raise
        except Exception as error:
            raise QwenImage21Error(
                '查询编辑进度失败，后台任务状态未知；不会自动重试或中断，请按任务编号核对队列',
                prompt_id=prompt_id) from error
        if not isinstance(history, dict):
            raise QwenImage21Error('ComfyUI返回了未知任务状态', prompt_id=prompt_id)
        item = history.get(prompt_id)
        if item is not None:
            if not isinstance(item, dict) or not isinstance(item.get('status'), dict):
                raise QwenImage21Error('ComfyUI返回了未知编辑状态', prompt_id=prompt_id)
            status = item['status']
            if status.get('status_str') == 'error':
                raise QwenImage21Error('Qwen Image 2.1编辑执行失败，请检查该任务；不会自动重试',
                                       prompt_id=prompt_id)
            if status.get('completed') is True or status.get('status_str') == 'success':
                images = item.get('outputs', {}).get(SAVE_NODE, {}).get('images')
                if not isinstance(images, list) or len(images) != 1:
                    raise QwenImage21Error('编辑任务完成但没有唯一的最终PNG输出', prompt_id=prompt_id)
                return images[0]
        time.sleep(2)
    raise QwenImage21Error('Qwen Image 2.1编辑等待超时，原任务未被自动中断，请核对队列', prompt_id=prompt_id)


def edit(image_path, prompt, references=(), seed=None, resolution=0, timeout=1200):
    """Edit one image using up to three identity references; retain all originals."""
    from cores import runtime_reset
    from cores.age_policy import assert_model_request
    from . import anima_client, gpu_manager, krea_client, moss_client
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('等待时间须为正数秒')
    _local_service()
    rows = _prepare_inputs(image_path, references)
    graph = build_workflow(rows[0]['name'], prompt, [row['name'] for row in rows[1:]], seed, resolution)
    assert_model_request(prompt, media='image_prompt')
    input_root, output_root = _directories()
    moss_client.local_guard()
    with gpu_manager.GPU_LOCK:
        moss_client._resource_guard()
        if not krea_client.up():
            raise QwenImage21Error('本机8188未运行，编辑器不会自动启动或重启ComfyUI')
        gpu_manager.acquire('krea', note='Qwen Image 2.1 reference edit')
        prompt_id = None
        try:
            anima_client.idle_guard()
            _queue_idle()
            _capabilities(graph)
            _copy_inputs(rows, input_root)
            moss_client.local_guard()
            moss_client._resource_guard()
            anima_client.idle_guard()
            _queue_idle()
            runtime_reset.assert_current()
            submitted = krea_client._http('POST', '/prompt', {'prompt': graph}, timeout=60)
            if not isinstance(submitted, dict) or submitted.get('node_errors'):
                raise QwenImage21Error('编辑工作流未通过ComfyUI校验，未自动重试')
            prompt_id = submitted.get('prompt_id')
            if (not isinstance(prompt_id, str) or not prompt_id or
                    any(char not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for char in prompt_id)):
                raise QwenImage21Error('ComfyUI没有返回有效编辑任务编号')
            started = time.monotonic()
            image = _wait(prompt_id, timeout)
            runtime_reset.assert_current()
            return _store_result(image, graph, prompt_id, rows, output_root, time.monotonic() - started)
        except runtime_reset.Cancelled as error:
            error.prompt_id = prompt_id
            raise
        finally:
            gpu_manager.release('krea')
