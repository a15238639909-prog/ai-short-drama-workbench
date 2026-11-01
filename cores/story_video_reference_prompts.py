"""Chinese multi-reference requests preserving the complete director rules.

No model calls, production monkeypatches, media selection or quality rerolls.
The adapter changes language/age/reference transport only; the caller controls
the original plan, storyboard review, bounded revision and observation loop.
"""
import copy
import math
import re

from . import story_video_prompts as base
from . import story_video_quality as quality
from . import wardrobe_continuity as wardrobe


MAX_REFERENCES = 8
REFERENCE_VISUAL_POLICY = 'reference-images-only-v1'


FIELDS = ('subject_definitions', 'summary', 'retention_analysis',
          'detailed_description', 'overall_soundscape', 'non_diegetic_music')
CONTENT_FIELDS = ('integrated_multimodal_description', 'overall_soundscape',
                  'non_diegetic_music')
I2VA_FIRST_FRAME = ('对于目标视频，在第0.00秒，完整沿用来自[Shot 1]的'
                    '<Picture 1>作为首帧。')

_CHINESE_POLICY = '''
本次所有自然语言内容使用简体中文，包括外貌、分镜、动作、声音、配乐和delivery；只保留schema字段名与枚举值、unknown、[Shot N]、At时间标记、<Picture N>、<Subject N>、S编号及N/A等必要技术标识。不翻译用户指定原话。
这是通用模板：角色数量、身份、年龄和物种均来自用户目标与素材，不预设固定人数，也不把儿童改成成年人或把动物、物体拟成人。年龄不能从单图精确判断时使用可见年龄段并记录未知，不编造具体年龄。真人写实描述不等于所有角色成年；无人物题材同样按输入的主体和环境拍摄，不强加人物。
人物参考只固定身份、脸、发型、体态和服装，不永久锁定站位、姿势或持物。拼图中同一人物的正面、侧面、背面和面部特写是一个人的设定，不能生成四个副本、分屏、排版线、文字标签或灰色摄影背景。
'''

_REFERENCE_SCENE_POLICY = '''
人物卡的纯白、灰色、摄影棚或其他背景只属于设定图，不提供故事世界的地点、建筑、陈设和现场光。没有场景参考图时，按照本轮故事已经写明的地点、时代、世界观和动作空间建立可拍环境；故事留白时补足与其情节一致的必要环境，而不是默认纯白或空白摄影背景。只有故事明确要求白底空间时才安排白底。已有真实首帧或上一段实际画面时，先承接其中已经确认的环境，再依剧情建立有依据的转场；不得用人物卡背景重置实际场景。场景图只提供其对应地点的环境，故事到达其他地点时按剧情建立新环境，不把全片固定在首图地点。
'''


def sanitize_reference_observation(material, image_role_bindings):
    """Return only role-supported reference facts; keep raw evidence elsewhere.

    Character sheets do not establish a scene or an already completed action.
    Mixed free-text summaries are intentionally replaced rather than reused as
    world facts. This function neither invents a location nor mutates its inputs.
    """
    if not isinstance(material, dict) or not isinstance(image_role_bindings, list):
        raise ValueError('参考观察与素材角色绑定须为对象和数组')
    roles = [row for row in image_role_bindings if isinstance(row, dict)]
    has_scene = any(row.get('role') in ('scene_anchor', 'scene') for row in roles)
    people = [row for row in roles if row.get('role') == 'character']
    identities = {row['character_id'] for row in people if isinstance(row.get('character_id'), str)}
    environment = re.compile(r'背景|摄影棚|影棚|棚拍|布景|环境|墙面|地面|天花板|家具|'
                             r'光线|灯光|光照|窗光|天光|站在|坐在|趴在|位于')
    result = dict(actual_visible='这是独立参考素材的身份外貌事实集合，尚未建立剧情开场状态。',
        unknowns=['人物当前剧情位置、姿势、手中物与动作进度未知。'],
        characters=[], props=[], scene='unknown', water='unknown', actions=[], completed=[],
        missing=[], continuity_risks=['设定图姿势与摄影背景不能当作已发生的剧情。'],
        beat_status='unknown', keep_end_seconds=None, keep_end_reason='参考素材不提供视频时间点。')
    for row in material.get('characters', []) if isinstance(material.get('characters'), list) else []:
        if not isinstance(row, dict) or not people or (identities and row.get('id') not in identities):
            continue
        fragments = re.split(r'[，,。；;\r\n]+', str(row.get('appearance') or ''))
        appearance = '，'.join(part.strip() for part in fragments if part.strip() and not environment.search(part))
        result['characters'].append(dict(id=row.get('id') or 'unknown', appearance=appearance or 'unknown',
            position='unknown', pose='unknown', hands='unknown',
            visibility=row.get('visibility') if row.get('visibility') in ('visible', 'partial', 'offscreen', 'unknown') else 'unknown',
            certainty=row.get('certainty') if row.get('certainty') in ('observed', 'last_known', 'unknown') else 'unknown'))
    if has_scene:
        for key in ('scene', 'water'):
            if isinstance(material.get(key), str) and material[key].strip():
                result[key] = material[key]
        result['actual_visible'] += '环境事实仅取场景参考。'
    else:
        result['unknowns'].append('未提供场景参考，故事环境尚未由图片确认。')
    return copy.deepcopy(result)


def _replace_once(system, old, new):
    if old not in system:
        raise RuntimeError('上游导演规则已变化，不能静默丢失中文适配：' + old[:55])
    return system.replace(old, new)


def _director_system():
    # Keep every production paragraph. Checked literal substitutions make future
    # upstream changes visible rather than silently replacing the entire system.
    system = base._WRITE_SYSTEM
    changes = (
        ('真人写实时，英文正文必须明确live-action, photorealistic, real adult actors，并描述真人表演与实际摄影质感；其他风格同样用英文落实用户的visual_style。',
         '真人写实时，中文正文明确真人写实摄影，并描述符合角色年龄的真实表演与实际摄影质感；其他风格同样用中文落实用户的visual_style，不把儿童、动物或其他角色强制改为成年人。'),
        ('在镜头英文正文直接写清成年身份、关键发色', '在镜头中文正文直接写清设定身份与年龄段、关键发色'),
        ('如wide shot交代空间、medium shot呈现互动、close-up展示线索或表情',
         '如远景交代空间、中景呈现互动、近景或特写展示线索或表情'),
        ('格式为[Shot N] At 00:SS.SSS, the camera cuts to...',
         '格式为[Shot N] At 00:SS.SSS, 镜头切至……'),
        ('15秒段落在英文镜头正文中自然说明first five seconds、next five seconds和final five seconds各自发生的动作发展',
         '15秒段落在中文镜头正文中自然说明前五秒、中间五秒和最后五秒各自发生的动作发展'),
        ('英文镜头正文6～8秒通常约140～220词，11～15秒通常约180～260词，均为软目标，按实际容量增减。',
         '中文镜头正文按实际可拍容量写清动作，15秒通常约300～550个汉字作为软目标，按剧情和信息密度增减；不得套用英文词数验证中文长度。'),
        ('delivery写非空英文说话者身份、语气和表演', 'delivery写非空中文说话者身份、语气和表演'),
        ('overall_soundscape用英文写现场环境与物理动作声', 'overall_soundscape用中文写现场环境与物理动作声'),
        ('non_diegetic_music只写英文乐器、速度、节奏', 'non_diegetic_music只写中文乐器、速度、节奏'),
    )
    for old, new in changes:
        system = _replace_once(system, old, new)
    return system + _CHINESE_POLICY


WRITE_SYSTEM = _director_system()


def reference_visual_rule(has_scene_reference, *, wardrobe_policy=None):
    """Simple image authority for new tasks; this is an instruction, not a lock claim."""
    character_rule = ('人物的脸、发型、发色、肤色、体型、服装和饰品全部按各自人设图，'
        '不重新设计、美化或补造服饰配饰。看不清或本地观察未知时直接遵循原图，不补写外貌。'
        '动作、表情、姿势和位置可随故事变化；上一段画面提供动作与空间衔接，'
        '其中的外形漂移不作为新的人设。')
    if wardrobe.enabled(dict(wardrobe_policy=wardrobe_policy)):
        character_rule = ('人物身份、脸、发型、发色、肤色和体型按各自人设图；'
            '初始衣装只作尚未发生已确认变化时的基线，当前穿戴按已确认衣着状态延续。'
            '动作、表情、姿势和位置随故事变化，不把随机外貌漂移当成换装。')
    background_rule = ('场景参考用于其对应地点，其他地点按故事生成场景，回访时沿用参考。'
        if has_scene_reference else
        '未提供场景参考时，根据故事地点、时代与剧情生成场景。')
    return character_rule + background_rule


def _wardrobe_request(request, policy):
    if not wardrobe.enabled(dict(wardrobe_policy=policy)):
        return request
    system, payload, schema = request
    system = system.replace(
        '人物参考只固定身份、脸、发型、体态和服装，不永久锁定站位、姿势或持物。',
        '人物参考固定身份、脸、发型和体态，当前穿戴按已确认衣着状态延续，不永久锁定站位、姿势或持物。')
    system = system.replace(
        '此优先级只用于姿势、位置、持物和已发生的动作，人物固定外观只按对应人设图。',
        '此优先级用于姿势、位置、持物和已发生的动作；人物稳定身份按人设图，当前穿戴按已确认衣着状态延续。')
    system = system.replace(
        '人物外形漂移如实记录，后续外观仍按对应人设图。',
        '人物身份外形漂移如实记录，不能重定义身份；已确认的衣着变化延续，不重置回原图衣装。')
    system = system.replace(
        '人物身份、脸、发型和服装以人物设定为准；',
        '人物身份、脸和发型以人物设定为准，当前衣着按已确认穿戴状态延续；')
    payload['wardrobe_policy'] = policy
    if 'canonical_references' in payload:
        payload['wardrobe_state'] = wardrobe.model_state(
            payload.get('wardrobe_state'), payload['canonical_references'])
    return system + '\n' + wardrobe.authority_rules(), payload, schema


def _image_authority_system(system):
    # New tasks alone remove inherited T2VA appearance rewriting/priority conflicts.
    # Legacy request strings remain byte-for-byte compatible with stage caches.
    system = '\n'.join(
        ('visual_style中的摄影、运镜要求照常执行；通用风格词不重新设计人物，人物外观按人设原图。'
         if line.startswith('visual_style是用户指定') else line)
        for line in system.split('\n') if not line.startswith('t2va首段必须读取'))
    system = system.replace(
        '事实优先级：当前实际画面与actual_state > 前段有证据的最后已知状态 > 原计划。',
        '动态状态优先级：当前实际画面与actual_state > 前段有证据的最后已知状态 > 原计划；'
        '此优先级只用于姿势、位置、持物和已发生的动作，人物固定外观只按对应人设图。')
    system = system.replace(
        '实际已有漂移要如实承接并记录continuity_notes，不能突然把道具重置到最初参考图。',
        '道具的实际变化如实承接并记录continuity_notes，不能突然重置；'
        '人物外形漂移如实记录，后续外观仍按对应人设图。')
    return system


def plan_request(brief, style, count, seconds, opening_observation=None, *, flow_optimization=False,
                 reference_visual_policy=None, scene_reference_provided=False, wardrobe_policy=None):
    system, payload, schema = base.plan_request(brief, style, count, seconds,
                                               opening_observation)
    payload['content_language'] = '简体中文'
    system += _CHINESE_POLICY
    if flow_optimization is True:
        payload['flow_optimization'] = True
        system += _REFERENCE_SCENE_POLICY
    if reference_visual_policy == REFERENCE_VISUAL_POLICY:
        payload.update(reference_visual_policy=reference_visual_policy,
                       scene_reference_provided=scene_reference_provided)
        system += '\n' + reference_visual_rule(scene_reference_provided, wardrobe_policy=wardrobe_policy)
    return _wardrobe_request((system, payload, schema), wardrobe_policy)


def review_request(plan, brief, style, count, seconds, opening_observation=None,
                   blocking_only=False, *, reference_visual_policy=None,
                   scene_reference_provided=False, wardrobe_policy=None):
    system, payload, schema = base.review_request(plan, brief, style, count,
        seconds, opening_observation, blocking_only=blocking_only)
    system += _CHINESE_POLICY
    if reference_visual_policy == REFERENCE_VISUAL_POLICY:
        payload.update(reference_visual_policy=reference_visual_policy,
                       scene_reference_provided=scene_reference_provided)
        system += '\n' + reference_visual_rule(scene_reference_provided, wardrobe_policy=wardrobe_policy)
    return _wardrobe_request((system, payload, schema), wardrobe_policy)


def observe_request(stage, previous_state=None, beat=None, image_role_bindings=None):
    references_only = str(stage).strip().lower() == 'references'
    system, payload, schema = base.observe_request(
        'opening' if references_only else stage, previous_state, beat)
    system += _CHINESE_POLICY
    if references_only:
        system += '''\n本次输入是独立参考素材，不是一个真实瞬间，也不是已经发生的剧情。按image_role_bindings分别读取：character是同一主体的多视图或特写，只记录已展示的稳定身份、年龄段、面部、毛发或服装等特征；其position、pose、hands均写unknown，不能把设定图站姿当作故事开场事实。scene_anchor/scene只提供环境，不能从空场景断言人物已离开。设定图摄影背景不属于剧情场景。actual_visible明确注明这是参考素材事实集合，actions与completed保持空数组，keep_end_seconds为null。主体数量按独立身份映射计算，不按视图数量计算；未提供人物时不增加人物，不把儿童改成成年人。'''
        payload['stage'] = 'references'
    else:
        system += '''\n如画面具有可辨面部的角色，逐一在appearance、pose或continuity_risks如实记录本次可见的面部朝向与可辨性、年龄感、发型和服装；如发生漂移如实记录，不因提示词或设定图说过就认定身份完全一致。被遮挡、背影、太小或模糊的脸保持未知，不宣称锁脸。无人物或主体没有可辨面部时，此项为不适用，不增加人物或强求人脸。actual_visible可概括本次实际可辨的视觉类型，如真人实拍感、三维、二维或未知；只凭附图，不把用户期望当成视觉事实。'''
    if image_role_bindings is not None:
        payload['image_role_bindings'] = copy.deepcopy(image_role_bindings)
    return system, payload, schema


def _references(references):
    if not isinstance(references, list) or not 1 <= len(references) <= MAX_REFERENCES:
        raise ValueError('ref2va须提供1～8张已绑定参考图')
    refs = copy.deepcopy(references)
    for ref in refs:
        if not isinstance(ref, dict):
            raise ValueError('每项参考须为对象')
        if ref.get('role') == 'scene':
            ref['role'] = 'scene_anchor'
        if ref.get('role') not in ('character', 'scene_anchor', 'previous_frame'):
            raise ValueError('未知参考作用')
        number = ref.get('picture')
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise ValueError('Picture编号须为正整数')
    refs.sort(key=lambda ref: ref['picture'])
    if [ref['picture'] for ref in refs] != list(range(1, len(refs) + 1)):
        raise ValueError('Picture编号须从1连续且唯一')
    characters = [ref for ref in refs if ref['role'] == 'character']
    subjects = [ref.get('subject') for ref in characters]
    if any(isinstance(subject, bool) or not isinstance(subject, int) for subject in subjects) or sorted(subjects) != list(range(1, len(characters) + 1)):
        raise ValueError('人物Subject编号须从1连续且唯一')
    for role in ('scene_anchor', 'previous_frame'):
        if sum(ref['role'] == role for ref in refs) > 1:
            raise ValueError('每段最多一张场景和一张实际末帧')
    if any(ref.get('subject') not in (None, '') for ref in refs if ref['role'] != 'character'):
        raise ValueError('场景和末帧不能定义额外人物Subject')
    return refs


def write_request(plan, beat, actual_state, seconds, mode='i2va', visual_style='',
                  references=None, *, flow_optimization=False, reference_visual_policy=None,
                  scene_reference_provided=False, wardrobe_policy=None, wardrobe_state=None):
    mode = str(mode).strip().lower()
    if mode not in ('i2va', 't2va', 'ref2va'):
        raise ValueError('mode须为i2va、t2va或ref2va')
    _, payload, schema = base.write_request(plan, beat, actual_state, seconds,
        mode='t2va' if mode == 'ref2va' else mode, visual_style=visual_style)
    payload.update(mode=mode, content_language='简体中文', automatic_normalization=True,
                   endpoint_policy='真实末帧，不筛选、不裁短、不补拍')
    system = WRITE_SYSTEM + '\n如提供user_brief，它是原始用户目标；结合完整story_plan落实其中的趣味、题材、场景转换、摄影与结尾要求，不能因为计划细节未展开就丢弃用户要求。具体镜头仍须依据实际状态和本段容量安排。'
    if mode == 'ref2va':
        image_authority = reference_visual_policy == REFERENCE_VISUAL_POLICY
        if image_authority:
            payload.update(reference_visual_policy=reference_visual_policy,
                           scene_reference_provided=scene_reference_provided)
            system = _image_authority_system(system)
        payload['canonical_references'] = _references(references)
        if flow_optimization is True:
            payload['flow_optimization'] = True
            system += _REFERENCE_SCENE_POLICY
        if image_authority:
            system += ('\nmode=ref2va时保留上述全部导演、动作因果、中文对白时序、完整时长及末态续写规则。'
                '它是多图参考生成，不能宣称第一帧硬锁定。canonical_references中character用<Subject N>'
                '指代对应唯一人物，正文和delivery可以使用Subject标签，但不自行写Picture定义、保留分析或六字段包装。'
                '不把人物三视图画成多个角色。首镜接续实际姿态和动作，按剧情切镜。'
                + reference_visual_rule(scene_reference_provided, wardrobe_policy=wardrobe_policy))
        else:
            system += '''\nmode=ref2va时保留上述全部导演、动作因果、中文对白时序、完整时长及末态续写规则。它是多图参考生成，不能宣称第一帧硬锁定。canonical_references中character用<Subject N>指代对应唯一人物，正文和delivery可以使用Subject标签，但不自行写Picture定义、保留分析或六字段包装。人物身份、脸、发型和服装以人物设定为准；actual_state及实际末帧负责当前姿势、持物、位置和已经发生的动作，不让末帧中的外形漂移重定义人设。场景图仅在故事到达其对应地点时提供该地空间、陈设、光线与美术，不限制每镜必须同一机位；转去其他地点按完整story_plan和beat建立新环境，不能把每段强拉回参考图地点，回访该地点时再延续参考外观。连续动作仍须建立可见通道或清楚的剧情转场，不用人物瞬移代替过渡。不照搬灰底人物拼图或把多个视图当多个角色。首镜根据实际末态接续，切镜服务剧情，不为了展示人设而摆拍。'''
        previous = next((ref for ref in payload['canonical_references'] if ref['role'] == 'previous_frame'), None)
        if previous and flow_optimization is True:
            payload['continuation_contract'] = dict(policy='continue-observed-last-frame-v1',
                reference_kind='soft', previous_frame_path=previous.get('path'),
                previous_observation=copy.deepcopy(actual_state))
            system += '''\ncontinuation_contract给出上一段实际末态及其真实尾帧软参考。本段首镜先接续尾帧中可见的姿态、位置、手中物、承托关系和动作进度，再推进本段新的动作；actual_state与previous_observation中已实际完成的动作不重新开始，不为重建场面而重复走近、迎接、拿起或完成同一次交接。计划里尚未实际发生的目标仍是待拍目标，未知保持未知，不能把计划ending当成既成事实。开场连续性不等于固定机位：接续后可按剧情切多个景别、展示反应或改变地点，转场须有可见通道、动作过渡或明确的剧情时间地点变化。人物卡只约束稳定外形，不恢复其旧姿势；尾帧提供动作续接线索，不宣称多图参考硬锁首帧或已保证人物一致。'''
    if not payload['is_final']:
        system += '''\n本段还有后续：若当前出场主体具有可辨面部且下一段需要保持身份，优先让所需主要角色在段尾正常行动或互动中面部清楚可辨，采用符合剧情的景别和角度；单人、多人均以实际人数为准，无人物场景不适用露脸要求。可通过有剧情意义的抬眼、回应、侧身交流自然做到，不要求直视镜头或突然扭头；不能为露脸牺牲核心行动。末帧采用真实视频最后一帧，露脸是生成目标，不是已锁脸或已验收的保证。'''
    if wardrobe.enabled(dict(wardrobe_policy=wardrobe_policy)):
        payload['wardrobe_state'] = copy.deepcopy(wardrobe_state)
    return _wardrobe_request((system, payload, schema), wardrobe_policy)


def revision_request(written, payload, feedback):
    system, new_payload, schema = write_request(payload['story_plan'], payload['beat'],
        payload['actual_state'], payload['seconds'], payload['mode'],
        visual_style=payload.get('visual_style', ''),
        references=payload.get('canonical_references'),
        flow_optimization=payload.get('flow_optimization') is True,
        reference_visual_policy=payload.get('reference_visual_policy'),
        scene_reference_provided=payload.get('scene_reference_provided', False),
        wardrobe_policy=payload.get('wardrobe_policy'), wardrobe_state=payload.get('wardrobe_state'))
    # Preserve prior caller evidence/advisories without carrying a previous
    # compiled prompt into the creative body or changing source dialogue slots.
    new_payload.update(copy.deepcopy(payload))
    new_payload.update(previous_reply=copy.deepcopy(written),
        storyboard_feedback=copy.deepcopy(feedback),
        correction='按本地分镜预审修正当前词稿，保留全部原对白、用户事件及合格内容；这是唯一一次局部修订',
        correction_scope='仅当前段因果、对白时序、可拍性与容量，不更换故事或生成素材')
    return system, new_payload, schema


storyboard_request = quality.storyboard_request
validate_storyboard = quality.validate_storyboard


def _label(value):
    return re.sub(r'[<>\r\n]', ' ', str(value or '')).strip()


def _check_references(text, refs):
    allowed = {('subject', str(ref['subject'])) for ref in refs if ref['role'] == 'character'}
    allowed |= {('picture', str(ref['picture'])) for ref in refs}
    pattern = re.compile(r'<\s*(Subject|Picture|Audio|Video)\s*(\d+)\s*>', re.I)
    actual = {(match[1].lower(), str(int(match[2]))) for match in pattern.finditer(text)}
    if not actual <= allowed:
        raise ValueError('引用了未绑定素材：' + str(sorted(actual - allowed)))
    if re.search(r'<\s*(Subject|Picture|Audio|Video)\b', pattern.sub('', text), re.I):
        raise ValueError('参考标签格式不完整')


def _assert_chinese(text, field, allow_na=False):
    if allow_na and text.strip() == 'N/A':
        return
    if not re.search(r'[\u4e00-\u9fff]', text):
        raise ValueError(field + '须使用中文自然语言')
    if re.search(r'live[- ]action|photorealistic|real adult actors|the camera|wide shot|medium shot|close[- ]up|first five seconds', text, re.I):
        raise ValueError(field + '混入旧英文自然语言规则')


def _timeline(obj, seconds):
    timeline = obj.get('timeline')
    if not isinstance(timeline, list) or len(timeline) < (3 if seconds >= 12 else 1):
        raise ValueError('时间轴须覆盖本段完整发展阶段')
    last = 0.0
    for item in timeline:
        if not isinstance(item, dict):
            raise ValueError('时间轴项目须为对象')
        start, end = item.get('start'), item.get('end')
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
               for value in (start, end)) or not 0 <= start < end <= seconds:
            raise ValueError('时间轴重叠或越界')
        if abs(start - last) > .001:
            raise ValueError('时间轴须从0秒连续覆盖整段，不留空等区间')
        if any(not isinstance(item.get(field), str) or not item[field].strip()
               for field in ('action', 'visible_result')):
            raise ValueError('时间轴须说明动作及可见结果')
        last = end
    if abs(last - seconds) > .001:
        raise ValueError('时间轴必须覆盖到实际片段结束')


def _reference_fields(refs, *, flow_optimization=False, reference_visual_policy=None,
                      scene_reference_provided=False, wardrobe_policy=None):
    definitions, retention = [], []
    image_authority = reference_visual_policy == REFERENCE_VISUAL_POLICY
    for ref in refs:
        picture = ref['picture']
        if ref['role'] == 'character':
            subject = ref['subject']
            identity = _label(ref.get('character_id') or 'char_%02d' % subject)
            if wardrobe.enabled(dict(wardrobe_policy=wardrobe_policy)):
                definitions.append('<Subject %d>是人物%s，面容、发型与体型沿用<Picture %d>；图内多视图对应同一位人物。' %
                                   (subject, identity, picture))
                retention.append('<Subject %d>：面容、发型与体型沿用人物参考。' % subject)
            elif image_authority:
                definitions.append('<Subject %d>是人物%s，外观完整沿用<Picture %d>；图内多视图是同一人。' %
                                   (subject, identity, picture))
                retention.append('<Subject %d>：外观按人设原图；姿势、位置、持物和动作随本段真实状态变化。' % subject)
            else:
                definitions.append('<Subject %d>是人物%s，其身份、面部、发型和服装以<Picture %d>为准；图内三视图与面部特写均为此同一人。' %
                                   (subject, identity, picture))
                retention.append('<Subject %d>：保留身份外形，年龄以设定为准，姿势、位置和持物随本段真实状态与动作变化。' % subject)
        elif ref['role'] == 'scene_anchor':
            definitions.append('<Picture %d>提供当前地点的建筑结构、陈设、现场光线与美术。' % picture)
            retention.append('<Picture %d>：当前地点的空间与现场美术。' % picture)
        else:
            definitions.append('<Picture %d>是本组上一段的真实最后一帧，提供当前姿势、位置、持物与动作衔接。' % picture)
            if wardrobe.enabled(dict(wardrobe_policy=wardrobe_policy)):
                retention.append('<Picture %d>：首镜的可见姿态、位置与持物。' % picture)
            elif flow_optimization is True:
                retention.append('<Picture %d>：首镜姿态、空间布局、持物与动作进度。' % picture)
            else:
                retention.append('<Picture %d>：首镜的可见姿态与动作关系。' % picture)
    return '\n'.join(definitions), '\n'.join(retention)


_CHINESE_SHOT_RANGE = re.compile(
    r'(?<![\w.+\-～~—])(?P<start>\d+(?:\.\d{1,3})?)\s*[～~—-]\s*'
    r'(?P<end>\d+(?:\.\d{1,3})?)\s*秒\s*[：:]\s*'
    r'(?P<label>\[Shot\s+(?P<shot>\d+)\])', re.I)
_EXISTING_CUT = re.compile(r'\s*At\s+(\d{1,2}):(\d{1,2}(?:\.\d{1,3})?)\s*,', re.I)


def _normalize_chinese_shot_ranges(description, seconds):
    """Move only a shot's explicit preceding Chinese range into its At label.

    Neither timeline nor relative prose supplies a missing cut. Existing At
    anchors remain authoritative and must agree with any explicit range.
    """
    adjustments = []

    def replace(match):
        start, end, shot = float(match['start']), float(match['end']), int(match['shot'])
        if not 0 <= start < end <= seconds or (shot == 1 and start != 0):
            raise ValueError('镜头前中文秒范围须合法且位于本段时长内，首镜须从0秒开始')
        label = '[Shot %d]' % shot
        tail = description[match.end():]
        existing = _EXISTING_CUT.match(tail)
        if existing:
            existing_start = int(existing[1]) * 60 + float(existing[2])
            if abs(existing_start - start) > 1e-9:
                raise ValueError('镜头前中文秒范围与已有At切点冲突')
            replacement = label  # Preserve the existing At text verbatim.
        elif re.match(r'\s*At\b', tail, re.I):
            raise ValueError('已有At切点格式不合法，不能用中文秒范围覆盖')
        else:
            replacement = label + ' At %02d:%06.3f,' % (int(start // 60), start % 60)
        adjustments.append(dict(kind='explicit_chinese_shot_range', shot=shot,
            start_seconds=start, end_seconds=end, source_start=match.start(),
            source_end=match.end(), from_text=match.group(), to_text=replacement))
        return replacement

    normalized = _CHINESE_SHOT_RANGE.sub(replace, description)
    if adjustments:
        # A range labels the whole following shot; its explicit end must not
        # be silently moved to a contradictory subsequent cut or film ending.
        canonical = base._normalize_shot_format(normalized)
        shots = list(base._SHOT.finditer(canonical))
        for index, shot in enumerate(shots):
            ranges = [row for row in adjustments if row['shot'] == int(shot[1])]
            if not ranges:
                continue
            stop = seconds
            if index + 1 < len(shots):
                following = shots[index + 1]
                cut = _EXISTING_CUT.match(canonical[following.end():])
                if not cut:
                    continue  # The shared compiler rejects the missing cut.
                stop = int(cut[1]) * 60 + float(cut[2])
            if any(abs(row['end_seconds'] - stop) > 1e-9 for row in ranges):
                raise ValueError('镜头前中文秒范围结束与下一切点或片段结尾冲突')
    return normalized, adjustments


_DIALOGUE_QUOTE = re.compile(r'“[^”\r\n]*”|「[^」\r\n]*」|『[^』\r\n]*』|"[^"\r\n]*"|\'[^\'\r\n]*\'')
_SPEECH_VERB = (r'(?:回答道|回应道|说道|问道|答道|喊道|叫道|念道|唱道|说出|读出|念出|唱出|喊出|'
                r'回答|回应|询问|复述|朗读|说|问|答|喊|叫|念|唱)'
                r'(?:了)?(?:一句|一声)?\s*[：:，,]?\s*')
_SPEECH_END = re.compile(_SPEECH_VERB + r'$')
_WRITTEN_CONTEXT = re.compile(r'写着|写下|书写|字样|文字|字幕|题字|刻着|印着|标着|标注|显示|'
                              r'屏幕|标牌|牌面|纸上|书页|书面|题名|书名|店名|歌名|名为|标题|名字|命名')
_VOICE_CONTEXT = re.compile(r'(?:音色(?:描述)?|声线(?:描述)?|声音描述|声音特征)\s*[：:][^；;。\r\n]*$')


def _speech_letters(text):
    return ''.join(char for char in text if char.isalnum())


def _dialogue_overlap_evidence(text, words):
    """Locate punctuation-insensitive text overlap, without classifying it as speech."""
    positions = [index for index, char in enumerate(text) if char.isalnum()]
    compact = ''.join(text[index] for index in positions)
    evidence, offset = [], 0
    while words and (found := compact.find(words, offset)) >= 0:
        start, stop = positions[found], positions[found + len(words) - 1] + 1
        evidence.append(dict(source_start=start, source_end=stop, from_text=text[start:stop]))
        offset = found + len(words)
    return evidence


def _normalize_dialogue_text(result, content, slots):
    """Remove only proven spoken copies; ordinary lexical overlap is advisory.

    All spans refer to the original field, never the caller's object. The fixed
    dialogue rows retain their words, identity, order and timing. Quoted writing
    and voice metadata remain evidence, rather than guessed extra speech.
    """
    groups = {}
    for index, slot in enumerate(slots):
        words = _speech_letters(slot['text'])
        if words:
            group = groups.setdefault(words, dict(text=slot['text'], line_indices=[]))
            group['line_indices'].append(index)
    if not groups:
        return
    adjustments = result.setdefault('automatic_adjustments', [])
    fields = [(field, value) for field, value in content.items()]
    fields += [('dialogue[%d].delivery' % index, line['delivery'])
               for index, line in enumerate(result['dialogue'])]
    for field, original in fields:
        candidates = []
        for match in _DIALOGUE_QUOTE.finditer(original):
            words = _speech_letters(match.group()[1:-1])
            if words not in groups:
                continue
            prefix = original[:match.start()]
            clause = re.split(r'[。！？!?；;\r\n]', prefix)[-1]
            cue = _SPEECH_END.search(prefix)
            # Quotes alone do not establish speech: object names and visible
            # text can contain the same words. Only a quote-only delivery may
            # omit the speaking verb, since its field already owns performance.
            remainder = original[:match.start()] + original[match.end():]
            remainder = re.sub(r'<Subject\s+\d+>', '', remainder, flags=re.I)
            quote_only_delivery = field.startswith('dialogue[') and not _speech_letters(remainder)
            if not cue and not quote_only_delivery:
                continue
            if not cue and (_WRITTEN_CONTEXT.search(clause) or _VOICE_CONTEXT.search(clause)):
                continue
            start = cue.start() if cue else match.start()
            candidates.append(dict(source_start=start, source_end=match.end(),
                from_text=original[start:match.end()], form='quoted_complete_line', **groups[words]))
        for words, group in groups.items():
            letters = r'[\s，,、：:…—-]*'.join(re.escape(char) for char in words)
            # A speaking verb and a whole utterance boundary are both required.
            # Thus “好” never deletes the “好” in “好奇”, “好看” or a voice trait.
            spoken = re.compile(_SPEECH_VERB + letters + r'(?=$|[，,。.!！?？；;\r\n])')
            for match in spoken.finditer(original):
                if any(match.start() < row['source_end'] and match.end() > row['source_start']
                       for row in candidates):
                    continue
                candidates.append(dict(source_start=match.start(), source_end=match.end(),
                    from_text=match.group(), form='attributed_complete_line', **group))
        candidates.sort(key=lambda row: (row['source_start'], -row['source_end']))
        accepted = []
        for row in candidates:
            if not accepted or row['source_start'] >= accepted[-1]['source_end']:
                accepted.append(row)
        value = original
        for row in reversed(accepted):
            start, stop = row['source_start'], row['source_end']
            separator = '，' if (start and stop < len(value) and
                re.match(r'[\u4e00-\u9fff]', value[start-1]) and
                re.match(r'[\u4e00-\u9fff]', value[stop])) else ''
            value = value[:start] + separator + value[stop:]
        if accepted:
            value = re.sub(r'([，,：:；;])(?:\s*[，,：:；;])+', r'\1', value)
            if field.startswith('dialogue['):
                value = value.strip(' \t\r\n，,。.!！?？；;：:、')
                if not re.search(r'[\u4e00-\u9fff]', value):
                    value = (value + '，' if value else '') + '自然清楚地说话'
            elif not re.search(r'[\u4e00-\u9fffA-Za-z0-9]', value):
                value = 'N/A' if field == 'non_diegetic_music' else '现场环境声。'
            adjustments.append(dict(kind='dialogue_text_deduplicated', field=field,
                                    count=len(accepted), matches=accepted))
        for words, group in groups.items():
            overlap = _dialogue_overlap_evidence(value, words)
            if overlap:
                adjustments.append(dict(kind='dialogue_text_overlap_warning', field=field,
                    count=len(overlap), matches=overlap, **group,
                    evidence_scope='normalized_field',
                    reason='文字重合无法确定为重复发声，保留原描述并继续，尚未验证成片发声'))
        if field in content:
            content[field] = value
            result[field] = value
        else:
            result['dialogue'][int(re.search(r'\[(\d+)\]', field)[1])]['delivery'] = value
    if not adjustments:
        result.pop('automatic_adjustments', None)


def compile_written(obj, payload):
    """Mechanical Chinese transport; no creative repair, media choice or mutation."""
    if not isinstance(obj, dict) or not isinstance(payload, dict):
        raise ValueError('写词结果与payload须为对象')
    mode = payload.get('mode', 'i2va')
    if mode not in ('i2va', 't2va', 'ref2va'):
        raise ValueError('未知生成模式')
    refs = _references(payload.get('canonical_references')) if mode == 'ref2va' else []
    seconds = base._seconds(payload.get('seconds'))
    # Reuse production natural-speech normalization, while suppressing ONLY its
    # inappropriate English/all-adult style insertion. Never patch the module.
    timing_payload = dict(payload, visual_style='')
    result = base._automatic_normalize_written(obj, timing_payload)
    content = {}
    forbidden_headers = re.compile(r'(?im)^\s*(?:' + '|'.join(FIELDS + CONTENT_FIELDS +
        ('prompt', 'timeline', 'expected_end_state', 'brief', 'dialogue', 'continuity_notes')) + r')\s*:')
    for field in CONTENT_FIELDS:
        value = result.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(field + '须为非空字符串')
        if forbidden_headers.search(value) or re.search(r'</?d>|\{\{|For the target video,', value, re.I):
            raise ValueError('正文不能混入传输字段、首帧说明或手写对白标签')
        _assert_chinese(value, field, allow_na=(field == 'non_diegetic_music'))
        if mode == 'ref2va':
            _check_references(value, refs)
            if re.search(r'<\s*(?:Picture|Video|Audio)\s+\d+\s*>', value, re.I):
                raise ValueError('导演正文仅用Subject指代人物，Picture作用由程序绑定')
        elif re.search(r'<\s*(?:Subject|Picture|Video|Audio)\s+\d+\s*>', value, re.I):
            raise ValueError('图生或文生正文不能自行定义参考素材')
        content[field] = value.strip()
    from .story_video_shot_consistency import assert_shot_consistency
    assert_shot_consistency(content['integrated_multimodal_description'], refs)
    for field in ('brief', 'expected_end_state'):
        value = result.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(field + '须为非空中文字符串')
        _assert_chinese(value, field)
    _timeline(result, seconds)
    slots = payload.get('dialogue_slots') or []
    lines = result.get('dialogue')
    if not isinstance(lines, list) or len(lines) != len(slots):
        raise ValueError('dialogue须逐项覆盖固定原话')
    for line, slot in zip(lines, slots):
        if any(line.get(key) != slot[key] for key in ('speaker', 'text')):
            raise ValueError('对白说话者、原字和顺序须与计划一致')
        delivery = line.get('delivery')
        if not isinstance(delivery, str):
            raise ValueError('delivery须为非空中文身份、语气与表演')
        delivery = re.sub(r'\s*' + re.escape('(' + slot['speaker_id'] + ')'), '', delivery).strip()
        _assert_chinese(delivery, 'delivery')
        if re.search(r'</?d>|\{\{|\(S\d+\)', delivery, re.I):
            raise ValueError('delivery不能自行绑定对白标签与编号')
        if mode == 'ref2va':
            _check_references(delivery, refs)
        elif re.search(r'<\s*(?:Subject|Picture|Video|Audio)\s+\d+\s*>', delivery, re.I):
            raise ValueError('图生或文生delivery不能引用额外素材')
        line['delivery'] = delivery
    _normalize_dialogue_text(result, content, slots)
    description, range_adjustments = _normalize_chinese_shot_ranges(content[CONTENT_FIELDS[0]], seconds)
    description = base._normalize_shot_format(description)
    if range_adjustments:
        result.setdefault('automatic_adjustments', []).extend(range_adjustments)
    style = str(payload.get('visual_style') or '')
    image_authority = mode == 'ref2va' and payload.get('reference_visual_policy') == REFERENCE_VISUAL_POLICY
    if not image_authority and ('真人' in style or re.search(r'live.action|photoreal', style, re.I)) and not re.search(
            r'真人|真实演员|实拍|写实摄影', description):
        if not re.match(r'\[Shot 1\](?:\s|$)', description):
            raise ValueError('镜头正文须从[Shot 1]开始')
        description = re.sub(r'\[Shot 1\]\s*', '[Shot 1] 真人写实电影摄影。', description, count=1)
        result.setdefault('automatic_adjustments', []).append(
            dict(kind='requested_live_action_style_zh', source='visual_style'))
    result[CONTENT_FIELDS[0]] = description
    events = []
    for line, slot in zip(lines, slots):
        delivery = line.get('delivery')
        _assert_chinese(delivery, 'delivery')
        start = line['start']
        events.append((start, 'At %02d:%06.3f, %s (%s), <d>[%s] %s</d>.' %
            (int(start // 60), start % 60, delivery.rstrip('，。,. '),
             slot['speaker_id'], slot['language'], slot['text'])))
    # This also checks consecutive shots and every cut/anchor's real time bounds.
    bound_description = base._insert_dialogue(description, events, seconds)
    if mode == 'ref2va':
        definitions, retention = _reference_fields(refs, flow_optimization=payload.get('flow_optimization') is True,
            reference_visual_policy=payload.get('reference_visual_policy'),
            scene_reference_provided=payload.get('scene_reference_provided', False),
            wardrobe_policy=payload.get('wardrobe_policy'))
        fields = dict(subject_definitions=definitions, summary=result['brief'].strip(),
            retention_analysis=retention, detailed_description=bound_description,
            overall_soundscape=content['overall_soundscape'], non_diegetic_music=content['non_diegetic_music'])
        result.update(fields=fields, reference_audit=refs)
        prompt = '\n\n'.join(field + ':\n' + fields[field] for field in FIELDS)
        if wardrobe.enabled(payload):
            result = wardrobe.apply_compiled(result, payload.get('wardrobe_state'), refs)
            prompt = result['prompt']
        _check_references(prompt, refs)
    else:
        content[CONTENT_FIELDS[0]] = bound_description
        prompt = '\n\n'.join(field + ': ' + content[field] for field in CONTENT_FIELDS)
        if mode == 'i2va':
            prompt = I2VA_FIRST_FRAME + '\n\n' + prompt
    spoken = re.findall(r'<d>\s*\[[^\]\n]+\]\s*(.*?)\s*</d>', prompt, re.S)
    if spoken != [line['text'] for line in lines] or prompt.count('<d>') != len(lines):
        raise ValueError('编译对白须逐句对应原话与顺序')
    result['prompt'] = prompt
    return result


def validate_written(obj, payload):
    return compile_written(obj, payload)
