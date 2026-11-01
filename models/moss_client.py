"""Moss adapter for the user's successful two-stage ComfyUI workflow."""
import json
import ipaddress
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / 'models' / 'moss_workflow.json'
OUTPUT_ROOT = ROOT / 'outputs' / 'moss'
DEFAULT_BASE_SIZE = (768, 1360)
MAX_BASE_PIXELS = int(1.2 * 1024 * 1024)
MAX_2X_BASE_PIXELS = 1920 * 1920
UPSCALE = 1.2


def upscale_factor(upscale=None):
    if upscale is None:
        return UPSCALE
    if isinstance(upscale, bool) or upscale not in (1.2, 2, '1.2', '2'):
        raise ValueError('Moss放大倍数只支持1.2或2')
    return float(upscale)


def dimensions(base_w=None, base_h=None, upscale=None):
    factor = upscale_factor(upscale)
    if base_w is None and base_h is None:
        return DEFAULT_BASE_SIZE
    if base_w is None or base_h is None:
        raise ValueError('Moss出底宽高必须同时填写')
    try:
        if isinstance(base_w, bool) or isinstance(base_h, bool):
            raise ValueError
        width, height = int(str(base_w)), int(str(base_h))
    except (TypeError, ValueError) as exc:
        raise ValueError('Moss出底宽高必须为整数') from exc
    if min(width, height) < 256 or max(width, height) > 2048:
        raise ValueError('Moss出底边长须为256至2048像素')
    width, height = round(width / 16) * 16, round(height / 16) * 16
    limit = MAX_2X_BASE_PIXELS if factor == 2 else MAX_BASE_PIXELS
    if width * height > limit:
        raise ValueError('Moss出底总像素超范围：原工作流最多1.2MP，双阶段×2最多1920×1920')
    return width, height


def output_dimensions(base_w=None, base_h=None, upscale=None):
    factor = upscale_factor(upscale)
    width, height = dimensions(base_w, base_h, factor)
    return round(width / 16 * factor) * 16, round(height / 16 * factor) * 16


def build_workflow(prompt, base_w=None, base_h=None, seed=None, upscale=None):
    value = str(prompt or '').strip()
    if not value:
        raise ValueError('请先填写Moss出图提示词')
    from cores.image_prompt_content import image_prompt_content
    image_prompt_content(value)
    factor = upscale_factor(upscale)
    width, height = dimensions(base_w, base_h, factor)
    try:
        if isinstance(seed, bool):
            raise ValueError
        actual_seed = uuid.uuid4().int % (2**31) if seed is None else int(str(seed))
    except (TypeError, ValueError) as exc:
        raise ValueError('Moss种子必须为非负整数') from exc
    if not 0 <= actual_seed <= 1125899906842624:
        raise ValueError('Moss种子超出工作流允许范围')
    workflow = json.loads(WORKFLOW.read_text(encoding='utf-8'))
    workflow['121']['inputs']['scale_by'] = factor
    workflow['129']['inputs']['text'] = value
    from cores.moss_prompt_format import rescale_compiled
    final_width, final_height = output_dimensions(width, height, factor)
    final_prompt = rescale_compiled(value, width, height, final_width, final_height)
    if final_prompt != value:
        # Absolute layout boxes describe pixels, so the enlarged sampling stage
        # needs conditioning at its own canvas size. Caption workflows keep 129.
        workflow['moss_layout_refine'] = {
            'class_type': 'CLIPTextEncode',
            'inputs': {'text': final_prompt, 'clip': list(workflow['129']['inputs']['clip'])}}
        workflow['122']['inputs']['positive'] = ['moss_layout_refine', 0]
    workflow['132']['inputs']['seed'] = actual_seed
    workflow['132'].pop('is_changed', None)
    workflow['141']['inputs']['filename_prefix'] = 'Moss-Workbench-' + uuid.uuid4().hex[:12]
    if base_w is not None:
        workflow['126']['inputs'].update(width=width, height=height)
    return workflow


def local_guard():
    from cores import runtime_reset
    from . import gpu_manager, krea_client
    runtime_reset.assert_current()
    if os.environ.get('V41_NO_MODEL'):
        raise RuntimeError('V41_NO_MODEL=1：本次检查不允许启动模型')
    krea_client._game_mode_guard()
    gpu_manager.assert_local_run_allowed()


def _qwen_endpoints():
    from . import gpu_manager, qwen_client
    configured = str(qwen_client.QWEN_URL).rstrip('/')
    try:
        parsed = urllib.parse.urlsplit(configured)
        hostname = parsed.hostname or ''
        local = hostname == 'localhost'
        if not local:
            try:
                local = ipaddress.ip_address(hostname).is_loopback
            except ValueError:
                local = False
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    except ValueError as exc:
        raise RuntimeError('无法确认文字服务地址，Moss已暂停') from exc
    endpoints = []
    if local and parsed.scheme in ('http', 'https'):
        endpoints.append((configured, hostname, port))
    for port in (gpu_manager.QWEN_PORT, 8080, 18080):
        if not any(endpoint[2] == port for endpoint in endpoints):
            endpoints.append(('http://127.0.0.1:' + str(port), '127.0.0.1', port))
    return endpoints


def _windows_listening_ports():
    try:
        result = subprocess.run(['netstat', '-ano', '-p', 'tcp'], capture_output=True,
                                text=True, errors='replace', timeout=8,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ValueError('netstat failed')
        ports = set()
        for line in result.stdout.splitlines():
            fields = line.split()
            if not fields or fields[0].upper() != 'TCP':
                continue
            if len(fields) != 5:
                raise ValueError('invalid netstat TCP row')
            if fields[3].upper() == 'LISTENING':
                port = int(fields[1].rsplit(':', 1)[1])
                if not 1 <= port <= 65535:
                    raise ValueError('invalid listening port')
                ports.add(port)
        return ports
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError('无法确认本机文字服务监听状态，Moss已暂停') from exc


def _qwen_slots_idle(base, hostname, port, listening_confirmed=False):
    if not listening_confirmed:
        try:
            connection = socket.create_connection((hostname, port), timeout=2)
        except ConnectionRefusedError:
            return
        except OSError as exc:
            raise RuntimeError('无法确认文字服务是否空闲，Moss已暂停') from exc
        connection.close()
    try:
        with urllib.request.urlopen(base + '/slots', timeout=5) as response:
            slots = json.loads(response.read().decode('utf-8'))
        if isinstance(slots, dict):
            slots = slots.get('slots')
        if not isinstance(slots, list) or not slots or any(
                not isinstance(slot, dict) or not isinstance(slot.get('is_processing'), bool)
                for slot in slots):
            raise ValueError('unknown slot state')
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError('文字服务存在但无法确认空闲，Moss已暂停') from exc
    if any(slot['is_processing'] for slot in slots):
        raise RuntimeError('文字模型正在处理请求，请等完成后再生成Moss图片')


def _resource_guard():
    from cores import runtime_reset
    from . import gpu_manager
    current = gpu_manager.owner()
    if current and current.get('pid') == os.getpid() and current.get('thread_id'):
        thread_id = current['thread_id']
        same_runtime = (not current.get('runtime_generation') or
                        current['runtime_generation'] == runtime_reset.capture())
        other_alive = (thread_id != threading.get_ident() and
                       any(thread.ident == thread_id and thread.is_alive()
                           for thread in threading.enumerate()))
        if same_runtime and other_alive and current.get('who') != 'qwen':
            raise RuntimeError('工作台另一项模型任务正在运行，请等完成后再生成Moss图片')
    endpoints = _qwen_endpoints()
    if not endpoints:
        return
    listening_ports = _windows_listening_ports() if os.name == 'nt' else None
    for base, hostname, port in endpoints:
        if listening_ports is not None:
            if port not in listening_ports:
                continue
            _qwen_slots_idle(base, hostname, port, listening_confirmed=True)
        else:
            _qwen_slots_idle(base, hostname, port)


def _required_models(workflow):
    from . import krea_client
    model_root = Path(krea_client.COMFY_DIR) / 'ComfyUI' / 'models'
    required = [('diffusion_models', workflow['134']['inputs']['unet_name']),
                ('text_encoders', workflow['133']['inputs']['clip_name']),
                ('vae', workflow['128']['inputs']['vae_name'])]
    missing = [name for folder, name in required
               if not (model_root / folder / Path(name.replace('\\', '/'))).is_file()]
    if missing:
        raise RuntimeError('Moss模型文件未找到：' + '、'.join(missing))


def _required_nodes(workflow):
    from . import krea_client
    available = krea_client._http('GET', '/object_info', timeout=30)
    missing = sorted({node['class_type'] for node in workflow.values()} - set(available))
    if missing:
        raise RuntimeError('ComfyUI缺少Moss工作流节点：' + '、'.join(missing))
    clip_types = available['CLIPLoader']['input']['required']['type'][0]
    if 'moss_image' not in clip_types:
        raise RuntimeError('当前ComfyUI不支持Moss文本编码器，请使用已验证的ComfyUI版本')


def _unload_idle_models():
    from cores import runtime_reset
    from . import krea_client
    runtime_reset.assert_current()
    body = json.dumps({'unload_models': True, 'free_memory': True}).encode('utf-8')
    request = urllib.request.Request(krea_client.COMFY_URL + '/free', data=body,
                                     method='POST', headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=30):
        pass


def _image_result(image, workflow, prompt_id, base_w, base_h):
    from cores import paths, runtime_reset
    from . import krea_client
    output_dir = Path(paths.get('comfy_output') or
                      str(Path(krea_client.COMFY_DIR) / 'ComfyUI' / 'output')).resolve()
    filename = str(image.get('filename') or '')
    if not filename or Path(filename).name != filename:
        raise RuntimeError('Moss返回的图片文件名无效')
    source = (output_dir / str(image.get('subfolder') or '') / filename).resolve()
    if not source.is_relative_to(output_dir):
        raise RuntimeError('图片路径超出ComfyUI输出目录')
    if not source.is_file():
        raise RuntimeError('Moss生成完成但图片文件不存在')
    with source.open('rb') as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != b'\x89PNG\r\n\x1a\n':
        raise RuntimeError('Moss返回的图片不是有效PNG')
    width, height = struct.unpack('>II', header[16:24])
    if not width or not height:
        raise RuntimeError('Moss返回的图片尺寸无效')
    runtime_reset.assert_current()
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    destination = OUTPUT_ROOT / filename
    shutil.copy2(source, destination)
    factor = workflow['121']['inputs']['scale_by']
    base_width, base_height = dimensions(base_w, base_h, factor)
    return {'output_path': str(destination), 'output_file': destination.name,
            'model': 'moss', 'prompt_id': prompt_id, 'seed': workflow['132']['inputs']['seed'],
            'stage2_seed': workflow['122']['inputs']['noise_seed'],
            'width': width, 'height': height, 'base_width': base_width, 'base_height': base_height,
            'upscale': factor}


def generate(prompt, base_w=None, base_h=None, seed=None, upscale=None):
    from cores import runtime_reset
    from cores.age_policy import assert_model_request
    from . import anima_client, gpu_manager, krea_client
    workflow = build_workflow(prompt, base_w, base_h, seed, upscale)
    assert_model_request(prompt, media='image_prompt')
    _required_models(workflow)
    local_guard()
    with gpu_manager.GPU_LOCK:
        _resource_guard()
        gpu_manager.acquire('krea', note='Moss image')
        try:
            anima_client.idle_guard()
            _resource_guard()
            gpu_manager.claim('krea')
            if not krea_client.up():
                krea_client.start_comfy()
            _required_nodes(workflow)
            local_guard()
            anima_client.idle_guard()
            _unload_idle_models()
            runtime_reset.assert_current()
            result = krea_client._http('POST', '/prompt', {'prompt': workflow}, timeout=60)
            prompt_id = result.get('prompt_id')
            if not prompt_id:
                raise RuntimeError('Moss工作流未被接受：' + str(result.get('node_errors') or '未返回任务编号')[:240])
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                runtime_reset.assert_current()
                item = krea_client._http('GET', '/history/' + prompt_id, timeout=30).get(prompt_id)
                if item:
                    status = item.get('status') or {}
                    if status.get('status_str') == 'error':
                        errors = [message[1].get('exception_message', '')
                                  for message in status.get('messages', [])
                                  if message[0] == 'execution_error']
                        raise RuntimeError('Moss出图失败：' + '; '.join(errors)[:240])
                    images = (item.get('outputs', {}).get('141', {}).get('images') or [])
                    if images:
                        return _image_result(images[0], workflow, prompt_id, base_w, base_h)
                time.sleep(2)
            raise RuntimeError('Moss生成超时，请检查ComfyUI任务状态')
        finally:
            gpu_manager.release('krea')
