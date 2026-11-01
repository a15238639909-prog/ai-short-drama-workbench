"""Reference snapshots, explicit version adoption and bounded comic-image edits.

Importing this module performs no I/O or model calls. A completed edit is a
candidate; it never changes the page's adopted version or narrative state.
"""
import base64
import binascii
import copy
import hashlib
import io
import json
from pathlib import Path
import time
import uuid
import warnings

from PIL import Image, ImageChops, ImageDraw, ImageOps


MAX_REFERENCE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 16 * 1024 * 1024
MAX_MODEL_EDGE = 1024
_PAGE_CONTENT = ('number', 'summary', 'scene', 'character_ids', 'wardrobe', 'wardrobe_updates',
                 'opening_state', 'ending_state', 'panels', 'prompt')


def _core():
    from . import long_comic_core
    return long_comic_core


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def content_signature(project, page):
    """Hash rendering content, excluding image history and operational flags."""
    return _digest(dict(project={key: project.get(key) for key in
        ('story', 'style', 'characters', 'continuity_version', 'base_w', 'base_h')},
        page={key: page.get(key) for key in _PAGE_CONTENT}))


def _project_root(project_id):
    core = _core()
    core._path(project_id)
    root = (core.OUTPUT_ROOT / project_id).resolve()
    if not root.is_relative_to(core.OUTPUT_ROOT.resolve()):
        raise ValueError('漫画输出目录越界')
    return root


def _read_image(source, *, png=False):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(source) as image:
                if png and image.format != 'PNG':
                    raise ValueError('图片须为PNG')
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError('图片像素超过1600万上限')
                image.load()
                return ImageOps.exif_transpose(image).convert('RGBA')
    except (OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError('图片无法读取或像素过大，原文件保留') from error


def _writable(project):
    core = _core()
    if project.get('archived'):
        raise ValueError('项目已归档，请先取消归档')
    if (project.get('job') or {}).get('status') in core.ACTIVE:
        raise core.BusyError('请等当前任务结束或暂停后再操作')


def _fresh(chapter, page):
    _core()._require_current_chapter(chapter)
    if page.get('needs_redo') or page.get('prompt_dirty'):
        raise ValueError('本页已有内容修改，请先单页重做，不能采用或修正过期图片')


def _version(project, page, number):
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise ValueError('版本号须为正整数')
    row = next((item for item in page.get('versions', []) if item.get('version') == number), None)
    if not row or row.get('status') != 'completed':
        raise ValueError('所选版本不是本页已完成版本')
    path = Path(row.get('path') or '').resolve()
    if not path.is_relative_to(_project_root(project['id'])):
        raise ValueError('版本图片不属于本漫画项目')
    if not path.is_file() or _core()._sha(path) != row.get('sha256'):
        raise ValueError('版本图片丢失或校验失败，原记录保留')
    return row, _read_image(path, png=True)


def _compatible(project, page, version):
    signature = version.get('content_signature')
    if signature:
        valid = signature == content_signature(project, page)
    else:
        # Legacy Moss versions predate the content signature.
        valid = version.get('kind') != 'edit' and bool(page.get('prompt')) and version.get('prompt') == page['prompt']
    if not valid:
        raise ValueError('所选版本对应旧内容，请先重做，不能绕过过期保护')


def bind_reference(project_id, character_id, image_data):
    if not isinstance(image_data, str):
        raise ValueError('参考图须为PNG/JPEG/WEBP的base64 dataURL')
    header, separator, encoded = image_data.partition(',')
    formats = {'data:image/png;base64': 'PNG', 'data:image/jpeg;base64': 'JPEG',
               'data:image/webp;base64': 'WEBP'}
    if not separator or header not in formats or len(encoded) > 4 * ((MAX_REFERENCE_BYTES + 2) // 3):
        raise ValueError('参考图须为PNG/JPEG/WEBP，解码后不超过8MiB')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError('参考图base64数据无效') from error
    if not raw or len(raw) > MAX_REFERENCE_BYTES:
        raise ValueError('参考图解码后须不超过8MiB')
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != formats[header]:
                raise ValueError('参考图内容与dataURL格式不符')
        normalized = _read_image(io.BytesIO(raw))
    except OSError as error:
        raise ValueError('参考图无法读取') from error
    core = _core()
    with core._LOCK:
        project = core._recover(core._load(project_id))
        _writable(project)
        if not isinstance(character_id, str) or character_id not in {row['id'] for row in project['characters']}:
            raise ValueError('参考图人物不属于本漫画项目')
        directory = _project_root(project_id) / 'references'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ('ref_' + uuid.uuid4().hex + '.png')
        if not path.resolve().is_relative_to(_project_root(project_id)):
            raise ValueError('参考图目录越界')
        with path.open('xb') as stream:
            normalized.save(stream, format='PNG')
        reference = dict(id=path.stem, path=str(path), url=core._url(path), sha256=core._sha(path),
                         width=normalized.width, height=normalized.height, created=time.time())
        project.setdefault('character_references', {})[character_id] = reference
        core._save(project)
        return copy.deepcopy(project)


def adopt_version(project_id, chapter, page, version):
    core = _core()
    with core._LOCK:
        project = core._recover(core._load(project_id))
        _writable(project)
        selected_chapter = core._chapter(project, chapter)
        selected_page = core._page(selected_chapter, page)
        _fresh(selected_chapter, selected_page)
        selected, image = _version(project, selected_page, version)
        image.close()
        _compatible(project, selected_page, selected)
        selected_page['current_version'] = version
        core._save(project)
        return copy.deepcopy(project)


def _reference(project_id, identity, row):
    if not isinstance(row, dict):
        raise ValueError('请先为所选人物绑定参考图')
    path = Path(row.get('path') or '').resolve()
    root = _project_root(project_id)
    if not path.is_relative_to(root) or not path.is_relative_to(root / 'references'):
        raise ValueError('参考图不属于本漫画项目')
    if not path.is_file() or _core()._sha(path) != row.get('sha256'):
        raise ValueError('人物参考图丢失或校验失败，请重新绑定')
    image = _read_image(path, png=True)
    if (image.width, image.height) != (row.get('width'), row.get('height')):
        raise ValueError('人物参考图尺寸记录不符')
    image.close()
    return dict(copy.deepcopy(row), character_id=identity)


def prepare_edit(project, chapter, page, options):
    """Validate and freeze only server-owned paths and reference snapshots."""
    _fresh(chapter, page)
    if not isinstance(options, dict):
        raise ValueError('请提供局部修正参数')
    version, image = _version(project, page, options.get('base_version'))
    if page.get('current_version') != version['version']:
        raise ValueError('底图版本已改变，请刷新后重新框选')
    _compatible(project, page, version)
    region = options.get('region')
    if (not isinstance(region, list) or len(region) != 4 or
            any(isinstance(value, bool) or not isinstance(value, int) for value in region)):
        raise ValueError('选区须为四个原图像素整数坐标')
    x1, y1, x2, y2 = region
    if not 0 <= x1 < x2 <= image.width or not 0 <= y1 < y2 <= image.height:
        raise ValueError('选区为空或超出原图范围')
    image.close()
    identities = options.get('character_ids')
    present = set(page.get('character_ids') or [])
    cast = {row['id'] for row in project['characters']}
    if (not isinstance(identities, list) or not 1 <= len(identities) <= 3 or
            not all(isinstance(identity, str) for identity in identities) or
            len(set(identities)) != len(identities) or not set(identities) <= cast & present):
        raise ValueError('请选择本页实际出场的1至3个人物')
    prompt = _core()._text(options.get('prompt'), '中文修正指令', 2000, True)
    seed = options.get('seed')
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2 ** 64):
        raise ValueError('修正种子须为0至2^64-1之间的整数')
    references = [_reference(project['id'], identity, project.get('character_references', {}).get(identity))
                  for identity in identities]
    signature = content_signature(project, page)
    return dict(base_version=version['version'], region=list(region), character_ids=list(identities),
        prompt=prompt, seed=seed, references=references, content_signature=signature,
        source_sha256=version['sha256'], source_signature=_digest(dict(
            base_version=version['version'], sha256=version['sha256'], content_signature=signature)))


def _frozen_current(project, job):
    core = _core()
    chapter = core._chapter(project, job['chapter'])
    page = core._page(chapter, job['page'])
    _fresh(chapter, page)
    frozen = job['edit']
    version, image = _version(project, page, frozen['base_version'])
    if (page.get('current_version') != frozen['base_version'] or
            version['sha256'] != frozen['source_sha256'] or
            content_signature(project, page) != frozen['content_signature']):
        image.close()
        raise ValueError('底图或页面内容已改变，修正结果不能应用')
    for reference in frozen['references']:
        _reference(project['id'], reference['character_id'], reference)
    return page, version, image


def _fit(image, *, target=False):
    image = image.copy()
    if target:
        longest = max(image.size)
        scale = max(768, min(MAX_MODEL_EDGE, longest)) / longest
        size = tuple(max(32, min(MAX_MODEL_EDGE, round(edge * scale / 32) * 32))
                     for edge in image.size)
        return image.resize(size, Image.Resampling.LANCZOS)
    image.thumbnail((MAX_MODEL_EDGE, MAX_MODEL_EDGE), Image.Resampling.LANCZOS)
    return image


def _save_png(image, path):
    with path.open('xb') as stream:
        image.save(stream, format='PNG')


def _instruction(project, frozen, crop_box, model_size):
    people = {row['id']: row for row in project['characters']}
    parts = ['图1是需要修正的漫画局部。按用户要求调整指定人物，其余人物、场景、机位、动作、'
             '对白、旁白和气泡保持原样。身份参考仅提供人物身份，不移植参考图的衣服、伤势、道具、背景或文字。'
             '除用户明确要求调整的部分外，保留图1当前衣着、持物、伤势和表情。']
    x1, y1, x2, y2 = frozen['region']
    left, top, right, bottom = crop_box
    scale_x, scale_y = model_size[0] / (right - left), model_size[1] / (bottom - top)
    local_region = [round((x1 - left) * scale_x), round((y1 - top) * scale_y),
                    round((x2 - left) * scale_x), round((y2 - top) * scale_y)]
    parts.append('图1允许调整的范围为像素框%s；修正后保留该框附近的原画面衔接。' % local_region)
    for offset, identity in enumerate(frozen['character_ids'], 2):
        person = people[identity]
        parts.append('图%d是人物%s（ID %s）的身份参考，绑定到图1中的该人物，保持其脸型、五官和发型识别特征。'
                     '文字识别特征：%s。' % (offset, person['name'], identity, person.get('appearance', '')[:600]))
    parts.append('用户修正要求：' + frozen['prompt'])
    return '\n'.join(parts)


def _blend(base, edited, crop_box, region):
    left, top, right, bottom = crop_box
    x1, y1, x2, y2 = region
    resized = edited.resize((right - left, bottom - top), Image.Resampling.LANCZOS)
    patch = resized.crop((x1 - left, y1 - top, x2 - left, y2 - top))
    width, height = patch.size
    feather = min(16, (min(width, height) - 1) // 2)
    mask = Image.new('L', patch.size, 255 if feather == 0 else 0)
    if feather:
        draw = ImageDraw.Draw(mask)
        for inset in range(feather + 1):
            draw.rectangle((inset, inset, width - 1 - inset, height - 1 - inset),
                           fill=round(255 * inset / feather))
    merged = base.copy()
    merged.paste(patch, (x1, y1), mask)
    outside = ((0, 0, base.width, y1), (0, y2, base.width, base.height),
               (0, y1, x1, y2), (x2, y1, base.width, y2))
    for box in outside:
        if box[2] > box[0] and box[3] > box[1]:
            difference = ImageChops.difference(base.crop(box), merged.crop(box))
            if any(channel.getbbox() is not None for channel in difference.split()):
                raise ValueError('选区外像素发生变化，修正候选未采用')
    return merged, feather


def run_edit(project_id, job_id):
    from models import qwen_image21_client
    core = _core()
    with core._LOCK:
        project = core._ensure_job(project_id, job_id, boundary=True)
        job = copy.deepcopy(project['job'])
        page, version, base = _frozen_current(project, job)
    frozen = job['edit']
    x1, y1, x2, y2 = frozen['region']
    margin = max(32, round(max(x2 - x1, y2 - y1) * .3))
    crop_box = (max(0, x1 - margin), max(0, y1 - margin),
                min(base.width, x2 + margin), min(base.height, y2 + margin))
    directory = _project_root(project_id) / 'edits' / job_id
    directory.mkdir(parents=True, exist_ok=True)
    if not directory.resolve().is_relative_to(_project_root(project_id)):
        raise ValueError('修正临时图目录越界')
    crop = _fit(base.crop(crop_box), target=True)
    crop_path = directory / 'crop.png'
    _save_png(crop, crop_path)
    reference_paths, snapshots = [], []
    for index, reference in enumerate(frozen['references'], 1):
        image = _fit(_read_image(Path(reference['path']), png=True))
        path = directory / ('reference_%d.png' % index)
        _save_png(image, path)
        reference_paths.append(path)
        snapshots.append(dict(reference, model_input_sha256=core._sha(path)))
    instruction = _instruction(project, frozen, crop_box, crop.size)
    core._progress(project_id, job_id, '局部人物修正：等待Qwen Image 2.1', 0, 1)
    result = core._model_call(project_id, job_id, lambda: qwen_image21_client.edit(
        crop_path, instruction, references=reference_paths, seed=frozen['seed'], resolution=0))
    edited = _read_image(Path(result.get('output_path') or ''), png=True)
    merged, feather = _blend(base, edited, crop_box, frozen['region'])
    with core._LOCK:
        current = core._ensure_job(project_id, job_id)
        current_page, source_version, checked_image = _frozen_current(current, current['job'])
        checked_image.close()
        target = _project_root(project_id) / ('chapter_%03d' % job['chapter']) / ('page_%03d' % job['page'])
        target.mkdir(parents=True, exist_ok=True)
        number = max((row['version'] for row in current_page['versions']), default=0) + 1
        path = target / ('v%03d.png' % number)
        while path.exists():
            number += 1
            path = target / ('v%03d.png' % number)
        if not path.resolve().is_relative_to(_project_root(project_id)):
            raise ValueError('修正候选输出目录越界')
        _save_png(merged, path)
        core._ensure_job(project_id, job_id)
        candidate = dict(version=number, kind='edit', status='completed', candidate=True,
            model='qwen_image21', prompt=current_page['prompt'], edit_prompt=frozen['prompt'],
            seed=result.get('seed'), prompt_id=result.get('prompt_id'), base_version=frozen['base_version'],
            source_signature=frozen['source_signature'], source_sha256=frozen['source_sha256'],
            content_signature=frozen['content_signature'], reference_snapshots=snapshots,
            character_ids=frozen['character_ids'], region=frozen['region'], crop_box=list(crop_box),
            model_input_size=list(crop.size), feather=feather, outside_pixels_changed=0,
            path=str(path), url=core._url(path), sha256=core._sha(path),
            width=merged.width, height=merged.height, created=time.time(), finished=time.time())
        current_page['versions'].append(candidate)
        current['job'].update(done=1, total=1, candidate_version=number, model_prompt_id=result.get('prompt_id'))
        core._save(current)
