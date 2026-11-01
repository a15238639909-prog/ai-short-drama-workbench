"""Opt-in local Qwen long-form presets; old writers remain independent."""
import json
import re
import copy

from cores.image_prompt_content import POSITIVE_OUTPUT_RULE, image_prompt_content
from cores.moss_prompt_format import compile_draft, parse_draft

_MARKER = re.compile(r'^【GPT长文：(Moss|Krea2)：(自然单图|精细人设)】$')
_LITERAL = re.compile(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"(?:\\.|[^"\\])*"')


def profile(system):
    if not isinstance(system, str) or not system.strip():
        return None
    match = _MARKER.fullmatch(system.strip().splitlines()[0].strip())
    return {'model': match[1].lower(), 'sheet': match[2] == '精细人设'} if match else None


def _string(minimum=1, maximum=1000):
    return {'type': 'string', 'minLength': minimum, 'maxLength': maximum}


def _object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties),
            'additionalProperties': False}


def _schema(sheet, layout):
    if sheet:
        return _object({
            'identity': _string(5, 180), 'style': _string(50, 200),
            'body': _string(120, 300), 'hair': _string(120, 300),
            'face': _string(220, 450),
            'outfit': _object({'upper': _string(220, 450), 'collar': _string(100, 260),
                'waist': _string(100, 260), 'lower': _string(180, 400),
                'footwear': _string(120, 300)}),
            'accessories': {'type': 'array', 'items': _string(1, 220), 'maxItems': 6},
            'background': _string(180, 380)})
    properties = {
        'shot': _object({name: _string(15, 180) for name in
            ('scene_and_time', 'framing', 'action_and_gaze', 'support_and_contact', 'lighting')}),
        'paragraphs': {'type': 'array', 'minItems': 10, 'maxItems': 12,
                       'items': _string(180, 360)}}
    if layout:
        properties['items'] = {'type': 'array', 'minItems': 1, 'maxItems': 16,
            'items': {'anyOf': [
                _object({'label': {'type': 'string', 'enum': ['object', 'character']},
                    'caption': _string(1, 600), 'bbox': {'type': 'array',
                        'minItems': 4, 'maxItems': 4, 'items': {'type': 'integer'}}}),
                _object({'label': {'type': 'string', 'enum': ['text']},
                    'caption': _string(1, 400), 'text': _string(1, 1000),
                    'bbox': {'type': 'array', 'minItems': 4, 'maxItems': 4,
                             'items': {'type': 'integer'}}})]}}
    return _object(properties)


_COMMON = '''
【本次程序合同】一次返回符合指定结构的完整JSON，所有画面说明用中文，实际文字与角色专名保留原文。
用户给出的身份、年龄、体型、画风、穿搭、数量、天气和动作是本次事实。先确定唯一方案，后面的细节沿用这一方案。选择一个具体的时段、镜头高度、景别、人物朝向、动作瞬间、视线落点和光源方向。已确认的服装结构与体型以原设为准，原创角色的空白才补齐。
正文直接描绘可见内容。画面用途决定构图：普通写真围绕人物与当下环境展开，只有明确要求上图文字时才设计文字排版。每项事实集中写清，新增段落增加具体画面信息。摄影中的皮肤、衣料和环境细节服从画面可见尺度；动漫或手绘沿用用户或原作的表现媒介。
每段直接从人物、衣服、物件、位置、光照或颜色开始，像描述一张已经拍好的照片。直接写选定结果，例如“青色真丝旗袍的肩部泛出一片宽而柔的亮面，肘侧褶皱压成深青色，盘扣沿右襟排列”。“真丝或棉麻”先选定一种，再写对应质感；“可能来自窗光”先确定光源，再写受光位置。人物数量与同伴是否入画也在shot里确定，正文逐段保持一致。
正文只描写实际画面。构图的作用、创作理由、审美评价与观众感受留在内部；把“增强纵深、提升真实感、使观者身临其境”换成具体的前后位置、清晰范围和受光细节。把“没有摆拍僵硬”落实成肩部松弛、手指轻扶、目光与呼吸状态。衣料、发型、姿势和光线采用唯一确定的说法，普通照片中的文字安排仅来自用户明确要求。
'''
_NATURAL = '''
shot是内部拍摄方案，先写清唯一的时间与场景、构图景别、人物动作与视线、重心接触关系、现场光照。paragraphs是10至12段完整画面正文，每段180至360字符，整篇充分展开到约2000至3000中文字符，按题材分配细节。
各段分别围绕本图的视觉重点展开，可包括主体与环境关系、构图与取景、外形穿搭、动作表情、接触与受力、前中远景、材质、现场光照、配色和影像质感。段落职责按本次画面安排，镜头、姿态、服装与光照始终沿用shot。
人物在一个自然瞬间做一件事。站坐走动的重心与动作相配，手指接触实际持物，视线落到具体对象，人物间的反应在同一瞬间成立。用可见状态表达自然感。
'''
_SHEET = '''
本次只写一套充分细化的角色设计，程序安排正面全身、朝右90度侧面全身、背面全身和放大正脸头肩特写。
identity确定作品人物或原创身份与版本。style写表现媒介和成像质感。body写既定体态与自然站立的形体转折，衣物覆盖处由剪裁、褶皱和轮廓呈现。hair写发型结构、发际、发束走向与发色。face充分展开同一张脸的脸型、眉眼眼睑、鼻唇、下颌、肤色和年龄感；神态自然平稳。
身高和头身比采用用户给出的明确数值，缺省时以既定体态的自然轮廓定性表达。每个字符串只写该部位的中文画面内容，字段结构由外层JSON负责。
outfit分上装、领口内搭、腰部、下装与鞋履。每项写其独有的设计，从正侧背可见的裁片、肩线、省道、袖口、里衬、开合、缝线、织纹、厚度、垂感、扣件与配色中选与该服装有关的细节。各部位有真实的连接关系。配饰按实际角色设计填写，已有款式的细化沿用原有轮廓与部件。
background写适合造型的简洁背景和完整统一布光方案：有方向的大面积柔光主光、合宜的环境补光及适度轮廓光，受光面与暗侧形成柔和的体积转折，暗侧五官与衣装结构仍可辨。同一套布光逻辑贯穿各视角。既定肌肉与柔美线条通过实际形体、衣料受力与阴影表现。
全套设计充分展开为长文，各字段只写本部位的可见事实，维持同一个人物和同一套服装。
'''


def _validate(value, schema, name='回复'):
    kind = schema.get('type')
    if kind == 'object':
        if not isinstance(value, dict) or set(value) != set(schema['properties']):
            raise ValueError(name + '字段不完整或含有未知字段')
        for key, child in schema['properties'].items():
            _validate(value[key], child, name + '.' + key)
    elif kind == 'array':
        if not isinstance(value, list) or not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 10000):
            raise ValueError(name + '数量未达到完整展开要求')
        for item in value:
            _validate(item, schema['items'], name)
    elif kind == 'string':
        if not isinstance(value, str) or not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 100000):
            raise ValueError(name + '正文长度未达到完整展开要求')
        if 'enum' in schema and value not in schema['enum']:
            raise ValueError(name + '类型不正确')
        if schema.get('pattern') and re.fullmatch(schema['pattern'], value) is None:
            raise ValueError(name + '混入了备选字词或字段语法，请写明确的中文画面内容')
    elif kind == 'integer':
        if type(value) is not int:
            raise ValueError(name + '须为整数')
    elif 'anyOf' in schema:
        for option in schema['anyOf']:
            try:
                _validate(value, option, name)
                return
            except ValueError:
                pass
        raise ValueError(name + '布局区域字段不正确')


def _visual_descriptions(draft, sheet):
    if sheet:
        return ([draft[key] for key in ('style', 'body', 'hair', 'face', 'background')] +
                list(draft['outfit'].values()) + draft['accessories'])
    return draft['paragraphs'] + [item['caption'] for item in draft.get('items', [])]


def _clean_presentation(value):
    """Remove process commentary/negative quality appendices, not visual facts."""
    literals = []
    def keep(match):
        literals.append(match.group())
        return '\x00L%d\x00' % (len(literals) - 1)
    text = _LITERAL.sub(keep, value)
    text = re.sub(r'(?:没有|无|避免|未见)(?:明显的?|刻意的?|多余的?|过度的?|强烈的?)?(?:噪点|模糊|摆拍|僵硬|冗余|拥挤|畸变|水印)[^，。；\n]*[，,]?', '', text)
    text = re.sub(r'(?:(?<=，)|(?<=。)|^)(?:(?:增强|增加|提升)(?:了)?(?:画面(?:的)?|人物(?:的)?)?(?:真实感|立体感|层次感|空间感|空间纵深感|纵深感|动感|故事性|视觉冲击力|代入感|趣味性|触觉联想)|使观者|让观者|给人)[^，。；\n]*(?:[，,]|(?=[。；\n])|$)', '', text)
    text = re.sub(r'(?:(?<=。)|^)(?:这种|这样的)(?:处理|设计|构图|配色|质感)[^。\n]*。', '', text)
    text = re.sub(r'[，,]+(?=[。；])', '', text).strip('，,；; ')
    for index, literal in enumerate(literals):
        text = text.replace('\x00L%d\x00' % index, literal)
    return text


def _clean_draft(draft, sheet):
    draft = copy.deepcopy(draft)
    if sheet:
        for key in ('style', 'body', 'hair', 'face', 'background'):
            draft[key] = _clean_presentation(draft[key])
        draft['outfit'] = {key: _clean_presentation(value) for key, value in draft['outfit'].items()}
        draft['accessories'] = [_clean_presentation(value) for value in draft['accessories']]
    else:
        draft['paragraphs'] = [_clean_presentation(value) for value in draft['paragraphs']]
        for item in draft.get('items', []):
            item['caption'] = _clean_presentation(item['caption'])
    return draft


def _problem_fields(draft, sheet, request=''):
    if sheet:
        fields = [((key,), draft[key]) for key in ('style', 'body', 'hair', 'face', 'background')]
        fields += [(('outfit', key), value) for key, value in draft['outfit'].items()]
        fields += [(('accessories', i), value) for i, value in enumerate(draft['accessories'])]
    else:
        fields = [(('paragraphs', i), value) for i, value in enumerate(draft['paragraphs'])]
        fields += [(('items', i, 'caption'), item['caption']) for i, item in enumerate(draft.get('items', []))]
    issues = []
    for path, paragraph in fields:
        reason = None
        if re.search(r'''["'](?:identity|style|body|hair|face|outfit|upper|collar|waist|lower|footwear|accessories|background|paragraphs|shot)["']\s*:''', paragraph):
            reason = '正文混入了JSON字段语法，各部位内容分别放回对应字段，字段值写具体中文描述'
        visible = _LITERAL.sub('', paragraph)
        uncertain = re.search(r'或者|或是|可能|也许|如有|(?:左侧|右侧|左窗|右灯|主光|窗光|光源|机位|镜头|面料|发型|真丝|棉麻|木质|金属|清晨|傍晚|阴天|正面|侧面|红色|蓝色|绿色|黑色|白色)[^，。；\n]{0,20}或', visible)
        if uncertain:
            reason = '画面描述含备选方案：' + uncertain.group() + '；选定一个具体可见的结果，沿用同一动作、机位和光源'
        if reason:
            issues.append((path, reason))
    if sheet and not re.search(r'身高[^，。；\n]{0,12}\d|头身(?:比)?[^，。；\n]{0,12}\d|\d(?:\.\d+)?\s*(?:厘米|cm|头身|米)', request, re.I):
        if re.search(r'身高[^，。；\n]{0,12}\d|头身(?:比)?[^，。；\n]{0,12}\d|\d(?:\.\d+)?\s*(?:厘米|cm|头身)', draft['body'], re.I):
            issues.append((('body',), '用户未给定具体身高或头身数，按既定体态定性描述轮廓，保留原有身材含义'))
    return issues


def _concrete_descriptions(draft, sheet, request=''):
    issues = _problem_fields(draft, sheet, request)
    if issues:
        raise ValueError(issues[0][1])


def _at(value, path):
    for key in path:
        value = value[key]
    return value


def _field_schema(schema, path):
    for key in path:
        schema = schema['items'] if isinstance(key, int) else schema['properties'][key]
        if 'anyOf' in schema:
            # Both item variants give caption the same visual-text role.
            schema = schema['anyOf'][0]
    return schema


def write_prompt(text, system, model, *, base_w=None, base_h=None, upscale=None,
                 output_format='auto', validate_prompt=None):
    from cores.age_policy import assert_model_request, assert_model_response
    from cores.moss_sheet import compile_sheet
    from models import anima_client, moss_client, moss_prompt, qwen_client

    selected = profile(system)
    if not selected or selected['model'] != model:
        raise ValueError('请选择当前模型的GPT长文预设')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('先写要画什么')
    if output_format not in ('auto', 'caption', 'layout'):
        raise ValueError('长文提示词格式须为auto、caption或layout')
    if validate_prompt is not None and not callable(validate_prompt):
        raise ValueError('内容校验须为可调用函数')
    text = text.strip()
    width, height = moss_client.dimensions(base_w, base_h, upscale) if model == 'moss' else (1360, 768)
    standard = selected['sheet'] and moss_prompt.standard_sheet_requested(text, '【Moss独立：人物人设图】')
    # Explicit custom views use the existing free-layout contract, rather than
    # silently replacing a seated/two-view request with the standard board.
    if selected['sheet'] and not standard:
        return moss_prompt.write_prompt(text, system.replace('【GPT长文：', '【自定义长文：', 1),
            base_w=base_w, base_h=base_h, upscale=upscale,
            output_format='caption' if output_format == 'caption' else 'layout',
            validate_prompt=validate_prompt) if model == 'moss' else _custom_krea(text, system, validate_prompt)
    layout = model == 'moss' and output_format != 'caption' and (output_format == 'layout' or standard or
        moss_prompt.layout_needed(text, system) or bool(re.search(r'封面|文字排版', text)))
    schema = _schema(standard, layout and not standard)
    contract = _SHEET if standard else _NATURAL
    if layout and not standard:
        contract += ('\nitems采用一层布局区域，bbox为当前画布左上原点的像素坐标[左,上,右,下]。'
            '总体摄影方案由paragraphs承载，items只写需要独立摆放的主体和文字，'
            '文字区域label=text且text逐字填写实际文案，其余区域采用object或character。'
            f'本次画布{width}×{height}，各坐标位于画布内。')
    prompt_system = system + _COMMON + contract + POSITIVE_OUTPUT_RULE
    request = {'需求': text}
    assert_model_request(text, media='image_prompt')
    calls = 0
    def call(system_text, payload, response_schema):
        nonlocal calls
        moss_client.local_guard()
        moss_client._resource_guard()
        anima_client.idle_guard()
        calls += 1
        return qwen_client.chat(system_text, json.dumps(payload, ensure_ascii=False),
            temperature=0.5, max_tokens=8192, reasoning=False, check_context=True, return_meta=True,
            response_format={'type': 'json_schema', 'json_schema': {
                'name': 'long_image_design', 'strict': True, 'schema': response_schema}})
    for attempt in range(2):
        response, meta = call(prompt_system, request, schema)
        try:
            if meta.get('finish_reason') not in ('stop', 'eos'):
                raise ValueError('长文未完整结束，请重新生成；本次未提交图片')
            draft = parse_draft(response)
            _validate(draft, schema)
            draft = _clean_draft(draft, standard)
            issues = _problem_fields(draft, standard, text)
            if issues and calls < 2:
                unique = {path: reason for path, reason in issues}
                targets = {'field_%d' % i: {'path': list(path), '原文': _at(draft, path), '修复要求': reason}
                           for i, (path, reason) in enumerate(unique.items())}
                repair_schema = _object({'field_%d' % i: _field_schema(schema, path)
                                         for i, path in enumerate(unique)})
                repaired, repair_meta = call(
                    '你是中文画面正文校订员。只修复给出的字段，保留用户事实和原有有效设计。'
                    '含备选的描述选定一个结果；混有JSON字段片段的值只保留本部位描述；'
                    '身高与头身数仅采用用户已给定的数值。每个字段继续写充分具体的可见细节。'
                    '只返回指定field键对应的修正版字符串。' + POSITIVE_OUTPUT_RULE,
                    {'用户需求': text, '统一方案': draft.get('shot', draft.get('identity')),
                     '待修字段': targets}, repair_schema)
                if repair_meta.get('finish_reason') not in ('stop', 'eos'):
                    raise ValueError('局部修复未完整结束，请重新生成')
                fixes = parse_draft(repaired)
                _validate(fixes, repair_schema)
                draft = copy.deepcopy(draft)
                for i, path in enumerate(unique):
                    _at(draft, path[:-1])[path[-1]] = fixes['field_%d' % i]
                draft = _clean_draft(draft, standard)
            _concrete_descriptions(draft, standard, text)
            if standard:
                board = compile_sheet(draft, width, height,
                    lighting_caption='各视角沿用同一套已设计的柔光布光，明暗转折呈现既定体态，五官与衣装结构清楚')
                if model == 'moss' and output_format != 'caption':
                    prompt, kind = compile_draft(board, width, height)
                else:
                    prompt = board['summary']['caption'] + '\n\n' + '\n\n'.join(item['caption'] for item in board['items'])
                    kind = 'caption'
            else:
                prose = '\n\n'.join(draft['paragraphs'])
                target = {'width': width, 'height': height, 'bbox_coord_space': 'abs',
                    'bbox_format': 'xyxy', 'summary': {'caption': prose}, 'items': draft['items']} if layout else {'caption': prose}
                prompt, kind = compile_draft(target, width, height)
            prompt = image_prompt_content(prompt, normalize=True)
            if validate_prompt is not None:
                validate_prompt(prompt)
            assert_model_response(text, prompt)
            result = {'model': model, 'prompt': prompt, 'format': kind}
            if model == 'moss':
                result.update(base_w=width, base_h=height)
            return result
        except ValueError as error:
            if attempt or calls >= 2:
                raise
            request['修复'] = {'错误': str(error), '上一版': response,
                '要求': '保留原需求和有效设计，按原结构完整展开，仅以具体中文正向画面描述修复报错部位。'}


def _custom_krea(text, system, validate_prompt=None):
    """Preserve explicitly requested nonstandard boards as a plain-text design."""
    from cores.age_policy import assert_model_request, assert_model_response
    from models import anima_client, moss_client, qwen_client
    assert_model_request(text, media='image_prompt')
    moss_client.local_guard()
    moss_client._resource_guard()
    anima_client.idle_guard()
    raw, meta = qwen_client.chat(system + _COMMON + POSITIVE_OUTPUT_RULE +
        '\n只返回{"caption":"完整中文长文"}，视角、姿势与视图数量采用本次用户要求。',
        text, max_tokens=8192, temperature=0.5, reasoning=False,
        response_format={'type': 'json_object'}, check_context=True, return_meta=True)
    if meta.get('finish_reason') not in ('stop', 'eos'):
        raise ValueError('长文未完整结束，请重新生成')
    prompt, kind = compile_draft(parse_draft(raw), 1360, 768)
    if kind != 'caption':
        raise ValueError('Krea2自定义人设须返回完整中文正文')
    prompt = image_prompt_content(prompt, normalize=True)
    if validate_prompt is not None:
        validate_prompt(prompt)
    assert_model_response(text, prompt)
    return {'model': 'krea2', 'format': 'caption', 'prompt': prompt}
