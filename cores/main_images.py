"""Model selection and prompt bindings for the main character/scene assets.

Absent project fields and absent historical job snapshots retain Krea2. The
ContextVar is scoped to one admitted worker; it never changes project defaults.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]
PRESETS = ROOT / '_legacy_8848' / 'presets' / 'moss'
SCENE_PROMPT_POLICY = 'scene-film-set-opening-v6'
SCENE_REFERENCE_INSTRUCTION = (
    '为视频主流程写一张自然、真实、干净、简洁、清晰的场景参考图提示词。'
    '画面呈现故事中已经落成的真实地点，尺度、构造、材料与光源彼此协调。'
    '依据场景卡呈现剧情地点，主空间占据画面主体，构图舒展，视觉中心明确。'
    '镜头退至能容纳主体结构的位置，采用自然的广角全景，完整交代主要空间边界和出入口。'
    '室内交代可见顶面、两侧墙体、地面及尽头；室外交代地形、道路与远景边界。'
    '前景开阔，主要通道连贯，必要陈设按用途集中整齐布置在各自区域。'
    '按剧情用途安排连贯的活动区域、主次区域和出入口，完整交代空间关系。'
    '灯具、座椅、桌柜等可数陈设写清总数及各自位置，同一组陈设的数量与分布前后一致。'
    '原文已给数量时沿用原数；数量缺省时按场所功能、尺度和剧情需要选定具体数量。'
    '走廊以建筑和通道为主，灯具集中于必要照明位置；起居空间以一组用途明确的家具形成布景。'
    '教室、剧场、餐厅等需要多组家具的场所按实际功能布置，建筑的开阔感由空间比例和构造体现。'
    '正常场所的表面洁净、构件完整，材质保留自然纹理和合理反射，陈设符合日常使用与维护状态。'
    '默认采用写实建筑或环境摄影，自然色彩，前中远景清晰。'
    '光照符合时段和现场光源，照明柔和，明暗区域的结构与材质细节清楚。'
    '用户明确指定的画风、光照、天气、旧损和布置按对应要求呈现。'
    '场景卡中的“场景当前状态”由后台依据原始需求确定，优先于自动设计稿的氛围修饰。'
    '具体地点、布局、材质、时段与天气沿用场景事实。'
    '正文使用原文地点全称，呈现该专业场所的用途与构造；固定结构与室外天气按给定事实呈现。'
    '室内通过可见窗外、门外或相连室外空间呈现天气，建筑内部保持对应的正常使用状态。'
    '画面主体是固定空间、建筑、地形和固有陈设。'
    '画面采用这个地点开场时的陈设状态，物件表面与摆放符合给定的当前状态。'
    '原文明确指定的特殊表面状态和布置按对应部位呈现。'
    '用约150至300字中文正向描述可见内容，必要细节按复杂度补充。'
)
_CURRENT = ContextVar('main_image_options', default=None)
_KREA = {'model': 'krea2', 'quality': 'workflow'}
_CHAR_FIELDS = ('name', 'age', 'sex', 'char_type', 'height', 'appearance_details',
                'face_type', 'face_spec', 'face_features', 'build', 'hair',
                'clothing', 'clothing_requirement', 'outfit_base', 'outfit_preset',
                'look_full', 'sheet_ratio_override', 'sheet_body_override',
                'entity_type', 'group_count', 'group_members', 'group_appearance')
_SCENE_FIELDS = ('name', 'space', 'contract_text', 'layout', 'design', 'scale', 'depth',
                 'materials', 'main_light', 'light', 'time', 'weather', 'landmarks',
                 'interior', 'time_of_day', 'scale_class', 'intent', 'fixtures',
                 'ground', 'walls', 'door', 'window', 'furniture', 'regions',
                 'fixed_landmarks', 'base_light_color', 'location_map', 'place_preset', 'scene_condition')


def snapshot(settings=None, *, kind=None):
    settings = settings or {}
    if kind not in (None, 'character', 'scene'):
        raise ValueError('绘图用途只支持人物或场景')
    if kind is None and any(settings.get(key) for key in
                            ('character_image_model', 'scene_image_model')):
        return {name: snapshot(settings, kind=name) for name in ('character', 'scene')}
    field = (kind or 'character') + '_image_model'
    model = str(settings.get(field) or settings.get('image_model') or 'krea2').strip()
    if model == 'krea2':
        return dict(_KREA)
    if model != 'moss':
        raise ValueError('主流程绘图模型只支持Moss或Krea2')
    quality = str(settings.get('image_quality') or '2x').strip().lower()
    if quality not in ('2x', '4k'):
        raise ValueError('Moss清晰度只支持2x或4k')
    width, height = (1920, 1088) if quality == '4k' else (1360, 768)
    return {'model': 'moss', 'base_w': width, 'base_h': height,
            'upscale': 2, 'quality': quality}


@contextmanager
def frozen(options):
    # Old recovery requests have no image snapshot. Never consult a newly
    # changed project default while resuming one of those historical jobs.
    options = options or _KREA

    def checked(value):
        if not isinstance(value, dict):
            raise ValueError('绘图任务模型快照无效')
        selected = snapshot({'image_model': value.get('model'),
                             'image_quality': value.get('quality')})
        if selected['model'] == 'moss':
            for key in ('base_w', 'base_h', 'upscale'):
                if key in value and value[key] != selected[key]:
                    raise ValueError('Moss任务快照尺寸或放大参数不匹配')
        return selected

    if 'character' in options or 'scene' in options:
        if not all(name in options for name in ('character', 'scene')):
            raise ValueError('绘图任务快照缺少人物或场景模型')
        selected = {name: checked(options[name]) for name in ('character', 'scene')}
    else:
        # Historical flat snapshots freeze the same model for both asset types.
        selected = checked(options)
    token = _CURRENT.set(selected)
    try:
        yield dict(selected)
    finally:
        _CURRENT.reset(token)


def current(settings=None, *, kind='character'):
    if kind not in ('character', 'scene'):
        raise ValueError('绘图用途只支持人物或场景')
    value = _CURRENT.get()
    if value is None:
        return snapshot(settings, kind=kind)
    return dict(value[kind] if kind in value else value)


def cached_prompt(card, settings=None, field='image_prompt'):
    """Read a compatible draft, scaling only native layout coordinates."""
    card = card or {}
    prompt = str(card.get(field) or '')
    options = current(settings, kind='scene' if card.get('scene_id') else 'character')
    if not prompt.strip() or (card.get(field + '_model') or 'krea2') != options['model']:
        return ''
    if options['model'] == 'moss':
        stamp = card.get(field + '_facts_hash') if field == 'image_prompt' else None
        if stamp and stamp != _facts_hash(card, settings):
            return ''
        if field == 'image_prompt' and not stamp and any(
                (settings or {}).get(key) for key in ('content_tendencies', 'custom_content_scale')):
            # Historical automatic drafts cannot prove they include this scale.
            # Explicit user overrides still take the separate path above.
            return ''
        width, height = card.get(field + '_base_w'), card.get(field + '_base_h')
        if not width or not height:
            return ''
        if (width, height) != (options['base_w'], options['base_h']):
            from .moss_prompt_format import rescale_compiled
            prompt = rescale_compiled(prompt, width, height,
                                      options['base_w'], options['base_h'])
    return prompt


def bind_prompt(card, prompt, settings=None, field='image_prompt'):
    """Return a card copy with an explicitly model/canvas-bound draft."""
    result = dict(card or {})
    options = current(settings, kind='scene' if result.get('scene_id') else 'character')
    result[field] = str(prompt or '')
    result[field + '_model'] = options['model']
    for key in ('base_w', 'base_h', 'upscale'):
        result[field + '_' + key] = options.get(key)
    return result


def _save_prompt(sid, card, kind, prompt, settings):
    from . import asset_core
    saved = asset_core.get_asset(sid, kind, card.get('character_id' if kind == 'characters' else 'scene_id'))
    if saved:
        saved = bind_prompt(saved, prompt, settings)
        if kind == 'scenes' and isinstance(card.get('scene_condition'), dict):
            saved['scene_condition'] = card['scene_condition']
        saved['image_prompt_facts_hash'] = _facts_hash(card, settings)
        asset_core.save_asset(sid, kind, saved)


def _project_facts(settings, *, kind='character'):
    from . import style_presets
    settings = settings or {}
    result = {key: settings[key] for key in
              ('world_type', 'style', 'visual_strength', 'custom_guidance', 'look_panel')
              if settings.get(key)}
    style = settings.get('style') or '电影级写实'
    if kind == 'scene':
        result['画风说明'] = {
            '2d': '二维绘画场景，建筑轮廓、空间透视、笔触与分层光色清晰。',
            '3d': '三维场景渲染，建筑体积、物理材质与环境光照形成清晰层次。',
        }.get(style_presets.style_kind(style),
              '写实场景摄影，透视自然，前中远景清晰，材质与色彩真实，光照符合现场光源和时段。')
    else:
        result['画风说明'] = style_presets.style_description(style)
        # Keep the character image settings identical to Krea's figure layer.
        # The project block already resolves editable presets and custom text;
        # story/shot guidance does not belong in an image prompt request.
        from . import prompt_writer
        for key in ('content_tendencies', 'custom_content_scale'):
            if settings.get(key):
                result[key] = settings[key]
        scale = prompt_writer._project_block(settings)['scale']
        if scale:
            result['人设图尺度说明'] = scale
    return result


def _facts_hash(card, settings):
    from .generation_recovery import fingerprint
    if not card.get('scene_id'):
        from .asset_core import normalize_character
        card = normalize_character(card)
    else:
        from .asset_core import effective_scene_card
        card = effective_scene_card(card)
    fields = _SCENE_FIELDS if card.get('scene_id') else _CHAR_FIELDS
    payload = {'card': {key: card.get(key) for key in fields},
               'project': _project_facts(settings, kind='scene' if card.get('scene_id') else 'character')}
    if card.get('scene_id'):
        # Expire automatically written scene words when the board contract
        # changes. Adopted images and explicitly supplied overrides stay intact.
        payload['scene_prompt_policy'] = SCENE_PROMPT_POLICY
    return fingerprint(payload)


def write_character(sid, card, settings=None):
    from . import authoring, group_entities
    from .age_policy import assert_allowed
    settings = settings or {}
    assert_allowed(cards=(card,), settings=settings, media='image_prompt')
    manual = cached_prompt(card, settings, 'prompt_override')
    if manual:
        assert_allowed(cards=(card,), settings=settings, text=manual, media='image_prompt')
        return manual
    if current(settings)['model'] == 'krea2':
        prompt = authoring.write_char_sheet(sid, card, settings)
        _save_prompt(sid, card, 'characters', prompt, settings)
        return prompt
    cached = cached_prompt(card, settings)
    if cached:
        assert_allowed(cards=(card,), settings=settings, text=cached, media='image_prompt')
        return cached
    if group_entities.is_group(card):
        # An unresolved collective remains one shared group frame. It must not
        # become four views of a newly invented individual.
        prompt = group_entities.group_sheet_prompt(card, settings)
    else:
        from models import moss_prompt
        from . import style_presets
        facts = {key: card[key] for key in _CHAR_FIELDS if card.get(key) not in (None, '', '自动')}
        facts['人设比例要求'] = style_presets.sheet_ratio_text(card)
        facts['原始身材或手动覆盖'] = style_presets.sheet_body_text(card)
        system = (PRESETS / 'Moss·人物人设图.txt').read_text(encoding='utf-8')
        system += ('\n【主流程人物卡】人物卡是当前原创角色的事实来源，姓名仅是此项目内的身份标签；'
                   '姓名相同不代表现有作品角色。具体年龄、五官、发型、身体比例、服装、'
                   '颜色和配饰以卡片为准，手动覆盖仅覆盖对应部位。项目画风决定表现媒介。'
                   '身体只保留原始设计含义，缺省身高与头身数保持概括。服装遮挡的身体结构'
                   '通过实体衣装轮廓呈现。只补齐未提供的视觉细节。标准版为三视图加正脸头肩特写。')
        options = current(settings)
        result = moss_prompt.write_prompt(
            json.dumps({'人物卡': facts, '项目视觉设定': _project_facts(settings)}, ensure_ascii=False),
            system=system, base_w=options['base_w'], base_h=options['base_h'],
            upscale=options['upscale'], named_identity=False)
        prompt = result['prompt']
    assert_allowed(cards=(card,), settings=settings, text=prompt, media='image_prompt')
    _save_prompt(sid, card, 'characters', prompt, settings)
    return prompt


def write_scene(sid, card, settings=None):
    from .age_policy import assert_allowed
    from .asset_core import effective_scene_card
    card = effective_scene_card(card)
    settings = settings or {}
    manual = cached_prompt(card, settings, 'prompt_override')
    if manual:
        assert_allowed(settings=settings, text=manual, media='image_prompt')
        return manual
    from . import asset_core, story_core
    from .scene_condition import condition_for_scene, positive_condition
    from .scene_prompt_content import ScenePromptContentError, validate_scene_prompt
    story = story_core.get_story(sid) or {}
    scene_names = [str(item.get('name') or '') for item in asset_core.list_assets(sid, 'scenes')]
    condition = condition_for_scene(story, card, scene_names=scene_names)
    card = dict(card, scene_condition=condition)
    if current(settings, kind='scene')['model'] == 'krea2':
        from . import quality_core
        from .scene_condition import project_condition_facts
        names = [str(item.get('name') or '') for item in asset_core.list_assets(sid, 'characters')]
        projected = project_condition_facts(asset_core.scene_image_card(card, names), condition)
        projected['layout'] = asset_core.scene_layout_for_image(projected.get('layout'))
        prompt = quality_core.compile_scene_prompt(dict(projected, _project=settings),
            style=settings.get('style') or '电影级写实', purpose_details=positive_condition(condition),
            appearance_only=True, clear_reference=True)
        from .image_prompt_content import image_prompt_content
        prompt = image_prompt_content(prompt.rstrip('。 ') + '。' + positive_condition(condition)
            + _project_facts(settings, kind='scene')['画风说明'], normalize=True)
        validate_scene_prompt(prompt, card, condition=condition)
        return prompt
    cached = cached_prompt(card, settings)
    if cached:
        assert_allowed(settings=settings, text=cached, media='image_prompt')
        try:
            validate_scene_prompt(cached, card, condition=condition)
        except ScenePromptContentError:
            # An automatic draft must pass the current opening-state contract.
            # Explicit user overrides take the independent path above.
            pass
        else:
            return cached
    from models import moss_prompt
    facts = _scene_facts(sid, card)
    facts['场景当前状态'] = positive_condition(condition)
    if condition.get('fixed_facts'):
        facts['原文固定构造'] = condition['fixed_facts']
    system = SCENE_REFERENCE_INSTRUCTION
    options = current(settings, kind='scene')
    from functools import partial
    result = moss_prompt.write_prompt(
        json.dumps({'场景卡': facts, '项目视觉设定': _project_facts(settings, kind='scene')}, ensure_ascii=False),
        system=system, base_w=options['base_w'], base_h=options['base_h'],
        upscale=options['upscale'], named_identity=False, output_format='caption',
        validate_prompt=partial(validate_scene_prompt, facts=facts, condition=condition))
    prompt = result['prompt']
    assert_allowed(settings=settings, text=prompt, media='image_prompt')
    _save_prompt(sid, card, 'scenes', prompt, settings)
    return prompt


def _scene_facts(sid, card):
    from . import asset_core, authoring, prompt_writer
    source = asset_core.effective_scene_card(card)
    names = [str(item.get('name') or '') for item in asset_core.list_assets(sid, 'characters')]
    # Share the established design-to-board cleaning. In particular, remove
    # the play/action column before a layout reaches the Moss planner.
    source = asset_core.scene_image_card(source, names)
    source['layout'] = asset_core.scene_layout_for_image(source.get('layout'))
    from .scene_condition import project_condition_facts
    source = project_condition_facts(source, card.get('scene_condition') or {})
    facts = {key: source[key] for key in _SCENE_FIELDS
             if key not in ('design', 'scene_condition', 'intent') and source.get(key) not in (None, '')}
    # The legacy one-line builder supplies defaults for missing time/interior.
    # Use it only with explicit card facts, keeping unknown conditions unknown.
    if source.get('interior') and (source.get('time_of_day') or source.get('time')):
        facts['场景基本信息'] = prompt_writer.scene_plate_one_line(source)
    return facts


def generate(prompt, settings=None, quality='formal', *, kind='character'):
    options = current(settings, kind=kind)
    if options['model'] == 'moss':
        from models import moss_client
        result = moss_client.generate(prompt, base_w=options['base_w'],
                                      base_h=options['base_h'], upscale=options['upscale'])
    else:
        from models import krea_client
        result = krea_client.generate(prompt)
    return dict(result, **generation_parameters(result, settings, kind=kind))


def generation_parameters(result, settings=None, *, kind='character'):
    """Include effective sampling dimensions and actual PNG dimensions if present."""
    options = current(settings, kind=kind)
    data = {'model': options['model'], 'image_quality': options['quality']}
    if options['model'] == 'moss':
        data.update(base_width=options['base_w'], base_height=options['base_h'],
                    upscale=options['upscale'])
    else:
        from models import krea_client
        graph = json.loads(krea_client.WORKFLOW.read_text(encoding='utf-8'))
        base = graph[krea_client.N_LATENT]['inputs']
        data.update(base_width=base['width'], base_height=base['height'], upscale=krea_client.UPSCALE)
    for key in ('seed', 'width', 'height'):
        if result.get(key) is not None:
            data[key] = result[key]
    path = result.get('output_path')
    if path:
        try:
            with Path(path).open('rb') as image:
                header = image.read(24)
            if len(header) == 24 and header[:8] == b'\x89PNG\r\n\x1a\n':
                data['width'], data['height'] = struct.unpack('>II', header[16:24])
        except OSError:
            pass
    return data


def record_visual(sid, visual, result, settings=None, *, kind='character'):
    from . import asset_core
    visual = dict(visual, **generation_parameters(result, settings, kind=kind))
    asset_core.save_asset(sid, 'visuals', visual)
    return visual
