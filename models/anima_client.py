"""Independent Anima text-to-image branch; Krea's graph and callers stay unchanged."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid
from cores.image_prompt_content import POSITIVE_OUTPUT_RULE

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / 'models' / 'anima_workflow.json'
SYSTEM = ROOT / 'presets' / 'instructions' / 'Anima_英文词组.txt'
SHEET_SYSTEM = ROOT / 'presets' / 'instructions' / 'Anima_人物三视图.txt'
MODULE_ROOT = ROOT / 'presets' / 'anima_modules'
OUTPUT_ROOT = ROOT / 'outputs' / 'anima'
DEFAULT_ARTIST = '@chigusa_minori'
ARTISTS = (
    {'value': DEFAULT_ARTIST, 'label': '当前默认（原图风格）'},
    {'value': '@as109', 'label': '女孩系列'},
    {'value': '@yoneyama mai', 'label': '米山舞'},
    {'value': '@billyhhyb', 'label': '3D模型'},
    {'value': '@ohako', 'label': '仿手绘'},
    {'value': '@yogisya', 'label': '唯美国漫插画'},
    {'value': '@tianliang duohe fangdongye', 'label': '可爱画风'},
    {'value': '@fajyobore', 'label': '现代线条细节丰富动漫'},
    {'value': '@kim hyung tae', 'label': '干净的游戏插画'},
)
QUALITY_PREFIX = ('masterpiece', 'best quality', 'score_9', 'score_8', 'highres',
                  'absurdres', 'anime screenshot', 'official art')
FIXED_PREFIX = ', '.join((DEFAULT_ARTIST, *QUALITY_PREFIX))
BODY_INSTRUCTION = ('\n你只编写画面正文：人物、动作、镜头、环境、材质和光影，使用英文标签和简短句子。'
                    '用户选择的画师与固定质量前缀由程序另行拼接，直接交付英文画面正文。'
                    '\n【统一作品人物规则】已有作品与角色的身份只用作品通用英文名和角色通用英文名表达，'
                    '例如“七龙珠、人造人18号”写Dragon Ball, Android 18。'
                    '原作外观由图像模型依据名称还原；发型、发色、五官、体型、服装和配饰词仅翻译用户明确给出的定制。'
                    '具名角色其余正文按用户输入整理动作、表情、场景与画风，人设图保留既定视角与分区。'
                    '只有作品名时只转换作品名，人物以用户指名为准。原创人物按用户给定设计整理。') + POSITIVE_OUTPUT_RULE


def local_guard():
    from cores import runtime_reset
    runtime_reset.assert_current()
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


def compose_prompt(text, artist=None):
    from cores.image_prompt_content import image_prompt_content
    value = english_prompt(text)
    if not value.strip('，, '):
        raise ValueError('请填写Anima画面正文，不能只有画师或质量前缀')
    value = image_prompt_content(value, normalize=True)
    if artist is not None and (not isinstance(artist, str) or artist not in {item['value'] for item in ARTISTS}):
        raise ValueError('请选择支持的Anima画师')
    quality = {tag.casefold() for tag in QUALITY_PREFIX}
    body, prior_artists = [], []
    for tag in re.split(r'[,，]', value):
        tag = tag.strip()
        if not tag:
            continue
        if tag.startswith('@'):
            prior_artists.append(tag)
        elif tag.casefold() not in quality:
            body.append(tag)
    if not body:
        raise ValueError('请填写Anima画面正文，不能只有画师或质量前缀')
    selected = [artist] if artist is not None else prior_artists or [DEFAULT_ARTIST]
    body = ', '.join(body)
    return {'prompt': ', '.join([*selected, *QUALITY_PREFIX, *([body] if body else [])]),
            'body': body, 'artist': ', '.join(selected)}


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


def build_workflow(prompt, width=1024, height=1024, seed=None, artist=None):
    prompt = compose_prompt(prompt, artist)['prompt']
    width, height = int(width), int(height)
    if min(width, height) < 256 or max(width, height) > 2048 or width * height > 2359296:
        raise ValueError('Anima直出尺寸超范围：边长256～2048，总像素最多1536×1536')
    width, height = (max(256, round(v / 8) * 8) for v in (width, height))
    seed = uuid.uuid4().int % (2**31) if seed is None else int(seed)
    if not 0 <= seed < 2**63:
        raise ValueError('种子超范围')
    wf = json.loads(WORKFLOW.read_text(encoding='utf-8'))
    wf['11']['inputs']['text'] = prompt
    wf['12']['inputs']['text'] = ''
    wf['28']['inputs'].update(width=width, height=height)
    wf['63']['inputs']['seed'] = seed
    wf['99']['inputs']['filename_prefix'] = 'Anima-Workbench-' + uuid.uuid4().hex[:12]
    return wf


def is_character_sheet(text):
    return bool(re.search(r'三视图|人设(?:图|板)|人物设定(?:图|板)|角色设定(?:图|板)|character\s+(?:design\s+)?sheet|turnaround', str(text), re.I))


def sheet_layout_valid(prompt, portrait=False):
    value = str(prompt).lower()
    checks = (r'front', r'profile|side view|side full', r'back(?:[ -]full[ -]body)?[ -]view|back[ -]full[ -]body|rear', r'full.body', r'panel|left|center')
    return all(re.search(p, value) for p in checks) and (not portrait or bool(re.search(r'portrait|headshot|head.and.shoulders|close.up', value)))


def prompt_modules(text, sheet=False, identities=False):
    """Load short relevant rules, rather than the full source template."""
    selected = ['01_common']
    rules = (
        ('02_identity', identities or bool(re.search(r'作品|角色|《|龙珠|dragon[ _]ball', text, re.I))),
        ('03_customization', bool(re.search(r'换|穿|服装|衣|发型|发色|瞳色|配饰|定制|spacesuit|wear|outfit|jacket|dress|hair|eyes', text, re.I))),
        ('04_action', bool(re.search(r'站|走|跑|坐|拿|持|看|笑|动作|表情|互动|standing|walking|running|sitting|holding|smil', text, re.I))),
        ('05_camera_layout', sheet or bool(re.search(r'镜头|机位|视角|构图|全身|特写|景|shot|view|portrait|close.up', text, re.I))),
        ('06_scene', bool(re.search(r'场景|街|城市|森林|海边|月球|工坊|房间|废墟|天气|雨|雪|阳光|window|forest|city|moon|workshop|room|ruins|rain|snow', text, re.I))),
        ('07_rendering', bool(re.search(r'画风|风格|写实|水彩|手绘|赛璐璐|光照|材质|3d|cg|photoreal|watercolor|cel.shad|lighting|texture', text, re.I))),
    )
    selected.extend(name for name, enabled in rules if enabled)
    content = '\n'.join((MODULE_ROOT / (name + '.txt')).read_text(encoding='utf-8').strip() for name in selected)
    return selected, content


def write_prompt(text, system=None, artist=None):
    from cores.age_policy import assert_model_request, assert_model_response
    from cores.authoring import _q
    text = str(text or '').strip()
    if not text:
        raise ValueError('先写要画什么')
    selected_artist = DEFAULT_ARTIST if artist is None else artist
    if not isinstance(selected_artist, str) or selected_artist not in {item['value'] for item in ARTISTS}:
        raise ValueError('请选择支持的Anima画师')
    assert_model_request(text, media='image_prompt')
    local_guard()
    idle_guard()
    instruction = str(system or SYSTEM.read_text(encoding='utf-8')).strip()
    custom = str(system or '').strip()
    compact_regular = instruction.startswith('【Anima预设：常规单图】')
    compact_sheet = instruction.startswith('【Anima预设：人设图】')
    compact_preset = compact_regular or compact_sheet
    four_view_preset = custom.startswith('【Anima预设：人设三视图＋面部特写】')
    gpt_preset = custom.startswith('【GPT优化指令词：Anima】')
    sheet = compact_sheet or four_view_preset or is_character_sheet(text)
    portrait = compact_sheet or four_view_preset or bool(re.search(r'特写|headshot|portrait|close.up', text, re.I))
    if sheet:
        # A separate main instruction prevents landscape/cinematic rules leaking into sheets.
        instruction = custom if four_view_preset or gpt_preset or compact_sheet else SHEET_SYSTEM.read_text(encoding='utf-8')
        if compact_preset and instruction.startswith('【Anima预设：人设图】'):
            portrait = True
        if compact_preset and re.search(r'只(?:要|需)?三视图|仅三视图|three views only', text, re.I):
            portrait = False
            instruction += '\n本次交付只采用正面、侧面、背面三个全身分区。'
        if custom and not (four_view_preset or gpt_preset or compact_preset) and custom != SYSTEM.read_text(encoding='utf-8').strip():
            instruction += '\n用户附加指令：\n' + custom
        if not four_view_preset and not compact_sheet:
            instruction += '\n本次布局：' + ('四区：正面全身、90度侧面全身、背面全身、单张正脸特写。' if portrait else '三区：正面全身、90度侧面全身、背面全身。')
    identities = []
    if compact_preset:
        from cores import anima_names
        identities = anima_names.resolve(text)
        _, modules = prompt_modules(text, sheet=sheet, identities=bool(identities))
        instruction += '\n【本次相关规则】\n' + modules
        if identities:
            instruction += '\n【本地已确认身份】以下触发词逐项原样采用，身份由这些词表达：\n' + '\n'.join(row['trigger'] for row in identities)
    query_options = {'_check_context': True} if gpt_preset or compact_preset else {}
    from cores.image_prompt_content import image_prompt_content
    output = _q(instruction + BODY_INSTRUCTION, text, mt=1100, temperature=0.5, _tries=1, **query_options)
    output = re.sub(r'^```(?:text|plaintext)?\s*|\s*```$', '', str(output).strip(), flags=re.I)
    output = image_prompt_content(output, normalize=True)
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
    if identities:
        anima_names.validate_output(identities, output)
    assert_model_response(text, output)
    return compose_prompt(output, selected_artist)['prompt']


def generate(prompt, base_w=1024, base_h=1024, seed=None, artist=None):
    from cores.age_policy import assert_model_request
    from . import gpu_manager, krea_client
    wf = build_workflow(prompt, base_w, base_h, seed, artist=artist)
    composed = compose_prompt(wf['11']['inputs']['text'])
    prompt = composed['prompt']
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
                            from cores import runtime_reset
                            runtime_reset.assert_current()
                            OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
                            dst = OUTPUT_ROOT / src.name
                            shutil.copy2(src, dst)
                            return {'output_path': str(dst), 'output_file': dst.name,
                                    'seed': wf['63']['inputs']['seed'], 'prompt_id': pid,
                                    'prompt': prompt, 'artist': composed['artist']}
                time.sleep(2)
            raise RuntimeError('Anima生成超时，请检查ComfyUI任务状态')
        finally:
            gpu_manager.release('krea')
