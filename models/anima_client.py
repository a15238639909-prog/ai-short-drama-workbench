"""Independent Anima text-to-image branch; Krea's graph and callers stay unchanged."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / 'models' / 'anima_workflow.json'
SYSTEM = ROOT / 'presets' / 'instructions' / 'Anima_英文词组.txt'
SHEET_SYSTEM = ROOT / 'presets' / 'instructions' / 'Anima_人物三视图.txt'
OUTPUT_ROOT = ROOT / 'outputs' / 'anima'


def local_guard():
    if os.environ.get('V41_NO_MODEL'):
        raise RuntimeError('V41_NO_MODEL=1：本次检查不允许启动模型')
    check = subprocess.run(['powershell', '-NoProfile', '-Command',
        "$pubg=Get-Process -Name 'TslGame','TslGame_BE','PUBG' -ErrorAction SilentlyContinue; if ($pubg) {'PUBG_RUNNING'} else {'PUBG_NOT_RUNNING'}"],
        capture_output=True, text=True, timeout=12, creationflags=0x08000000)
    if check.returncode or check.stdout.strip() != 'PUBG_NOT_RUNNING':
        raise RuntimeError('检测到PUBG运行或无法确认游戏状态，已暂停本地模型')
    from . import krea_client
    krea_client._game_mode_guard()


def english_prompt(text):
    value = str(text or '').strip()
    if not value:
        raise ValueError('请先生成或填写Anima英文词组')
    if re.search(r'[\u3400-\u9fff]', value):
        raise ValueError('Anima使用英文词组，请先点“生成”转换中文需求')
    return value


def idle_guard():
    """Never stop another queued Comfy job to make room for a quick image."""
    import urllib.request
    import urllib.error
    from . import krea_client
    for base in (krea_client.COMFY_URL, 'http://127.0.0.1:8190'):
        try:
            with urllib.request.urlopen(base + '/queue', timeout=3) as response:
                queue = json.load(response)
        except urllib.error.URLError as error:
            reason = error.reason
            if isinstance(reason, ConnectionRefusedError) or getattr(reason, 'winerror', None) == 10061:
                continue
            raise RuntimeError('无法确认绘图/视频服务是否空闲，请稍后重试') from error
        if queue.get('queue_running') or queue.get('queue_pending'):
            raise RuntimeError('已有绘图或视频任务，请等它完成后再使用Anima')


def build_workflow(prompt, width=1024, height=1024, seed=None):
    prompt = english_prompt(prompt)
    width, height = int(width), int(height)
    if min(width, height) < 256 or max(width, height) > 2048 or width * height > 2359296:
        raise ValueError('Anima直出尺寸超范围：边长256～2048，总像素最多1536×1536')
    width, height = (max(256, round(v / 8) * 8) for v in (width, height))
    seed = uuid.uuid4().int % (2**31) if seed is None else int(seed)
    if not 0 <= seed < 2**63:
        raise ValueError('种子超范围')
    wf = json.loads(WORKFLOW.read_text(encoding='utf-8'))
    wf['11']['inputs']['text'] = prompt
    wf['28']['inputs'].update(width=width, height=height)
    wf['63']['inputs']['seed'] = seed
    wf['99']['inputs']['filename_prefix'] = 'Anima-Workbench-' + uuid.uuid4().hex[:12]
    return wf


def is_character_sheet(text):
    return bool(re.search(r'三视图|人设(?:图|板)|人物设定(?:图|板)|角色设定(?:图|板)|character\s+(?:design\s+)?sheet|turnaround', str(text), re.I))


def sheet_layout_valid(prompt, portrait=False):
    value = str(prompt).lower()
    checks = (r'front', r'profile|side view|side full', r'back(?:[ -]full[ -]body)?[ -]view|rear', r'full.body', r'panel|left|center')
    return all(re.search(p, value) for p in checks) and (not portrait or bool(re.search(r'portrait|headshot|head.and.shoulders|close.up', value)))


def write_prompt(text, system=None):
    from cores.age_policy import assert_model_request, assert_model_response
    from cores.authoring import _q
    text = str(text or '').strip()
    if not text:
        raise ValueError('先写要画什么')
    assert_model_request(text, media='image_prompt')
    local_guard()
    idle_guard()
    instruction = str(system or SYSTEM.read_text(encoding='utf-8')).strip()
    custom = str(system or '').strip()
    four_view_preset = custom.startswith('【Anima预设：人设三视图＋面部特写】')
    sheet = four_view_preset or is_character_sheet(text)
    portrait = four_view_preset or bool(re.search(r'特写|headshot|portrait|close.up', text, re.I))
    if sheet:
        # A separate main instruction prevents landscape/cinematic rules leaking into sheets.
        # A genuinely custom user instruction remains an explicit additional requirement.
        instruction = custom if four_view_preset else SHEET_SYSTEM.read_text(encoding='utf-8')
        if custom and not four_view_preset and custom != SYSTEM.read_text(encoding='utf-8').strip():
            instruction += '\n用户附加指令：\n' + custom
        if not four_view_preset:
            instruction += '\n本次布局：' + ('四区：正面全身、90度侧面全身、背面全身、单张正脸特写。' if portrait else '三区：正面全身、90度侧面全身、背面全身。')
    output = _q(instruction, text, mt=1100, temperature=0.5, _tries=1)
    output = re.sub(r'^```(?:text|plaintext)?\s*|\s*```$', '', str(output).strip(), flags=re.I)
    output = re.sub(r'^(?:positive prompt|prompt)\s*:\s*', '', output, flags=re.I)
    output = re.sub(r'\s*\n\s*', ', ', output).replace('，', ',')
    output = re.sub(r'\s+', ' ', output).strip()
    # Only normalize generated tags. Manually saved prompts remain untouched.
    tags, seen = [], set()
    for tag in output.split(','):
        tag = tag.strip()
        key = tag.casefold()
        if tag and key not in seen:
            tags.append(tag)
            seen.add(key)
    output = ', '.join(tags)
    output = english_prompt(output)
    if sheet and not sheet_layout_valid(output, portrait):
        raise ValueError('Anima人物板提示词缺少正面、侧面、背面或分区信息，请重新生成提示词；尚未出图')
    assert_model_response(text, output)
    return output


def generate(prompt, base_w=1024, base_h=1024, seed=None):
    from cores.age_policy import assert_model_request
    from . import gpu_manager, krea_client
    wf = build_workflow(prompt, base_w, base_h, seed)
    assert_model_request(prompt, media='image_prompt')
    comfy_root = Path(krea_client.COMFY_DIR) / 'ComfyUI'
    required = [('diffusion_models', wf['44']['inputs']['unet_name']),
                ('text_encoders', wf['45']['inputs']['clip_name']),
                ('vae', wf['15']['inputs']['vae_name'])]
    missing = [name for folder, name in required if not (comfy_root / 'models' / folder / name).is_file()]
    if missing:
        raise RuntimeError('Anima模型文件未找到：' + '、'.join(missing))
    local_guard()
    idle_guard()
    with gpu_manager.GPU_LOCK:
        gpu_manager.acquire('krea', note='Anima quick image')
        try:
            gpu_manager.claim('krea')
            if not krea_client.up():
                krea_client.start_comfy()
            result = krea_client._http('POST', '/prompt', {'prompt': wf}, timeout=60)
            pid = result.get('prompt_id')
            if not pid:
                raise RuntimeError('Anima工作流未被接受：' + str(result.get('node_errors') or result)[:240])
            deadline = time.time() + 1200
            while time.time() < deadline:
                item = krea_client._http('GET', '/history/' + pid, timeout=30).get(pid)
                if item:
                    status = item.get('status') or {}
                    if status.get('status_str') == 'error':
                        errors = [m[1].get('exception_message', '') for m in status.get('messages', []) if m[0] == 'execution_error']
                        raise RuntimeError('Anima出图失败：' + '; '.join(errors)[:240])
                    for image in (item.get('outputs', {}).get('99', {}).get('images') or []):
                        src = (comfy_root / 'output' / image.get('subfolder', '') / image['filename']).resolve()
                        if not src.is_relative_to((comfy_root / 'output').resolve()):
                            raise RuntimeError('图片路径超出ComfyUI输出目录')
                        if src.is_file():
                            OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
                            dst = OUTPUT_ROOT / src.name
                            shutil.copy2(src, dst)
                            return {'output_path': str(dst), 'output_file': dst.name,
                                    'seed': wf['63']['inputs']['seed'], 'prompt_id': pid}
                time.sleep(2)
            raise RuntimeError('Anima生成超时，请检查ComfyUI任务状态')
        finally:
            gpu_manager.release('krea')
