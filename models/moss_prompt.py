"""Local Qwen prompt planning for MOSS, isolated from Krea2/Anima prompts."""
import json
import re
from pathlib import Path

from cores.moss_prompt_format import compile_draft, parse_draft
from cores.image_prompt_content import POSITIVE_OUTPUT_RULE, image_prompt_content

ROOT = Path(__file__).resolve().parents[1]
SYSTEM = ROOT / '_legacy_8848' / 'presets' / 'moss' / 'GPT优化指令词.txt'

FORMAT_CONTRACT = '''
【程序输出合同】
只返回一个有效JSON对象。描述内容用中文；作品或角色专名、用户要求上图的文字保留原文。
普通照片、场景、插画采用 {"caption":"完整正向画面描述"}。
需要精确文字排版、海报、多格漫画、三视图加五官特写等空间规划时采用布局：
{"width":$WIDTH,"height":$HEIGHT,"bbox_coord_space":"abs","bbox_format":"xyxy",
 "summary":{"caption":"总体画面及风格光色"},
 "items":[{"label":"character","caption":"这一可见单元的具体描述","bbox":[0,0,$WIDTH,$HEIGHT]}]}
上例展示合法JSON格式，实际items按本次画面规划。label仅取character（人物）、object（物体或环境）、text（可见文字）中的一个。
布局使用当前请求给定的实际像素宽高；坐标原点在左上，0≤左<右≤宽、0≤上<下≤高。
items为一层数组，可自然遮挡重叠。只给需要独立摆放的单元划区域。
object和character单元由label、caption、bbox及可选tags、palette组成。
label=text的单元另加text字段，填入非空的实际可见文字，逐字保留标点和换行；caption描述字体和排版。
每个分镜中人物与对白在同一分镜范围内。人设图各视角保持同一角色、服装、比例与配色，面部特写单独分区。
summary只写caption；item可附tags、palette文字数组；只使用以上字段。
描述字段只写画面可见内容，尺寸、坐标转换、输出规则由程序处理。
根据画面需要组织细节，直接表达主体、动作、空间、光色和材质；用户的作品、角色、数量、天气、动作、景别、定制与实际文字优先。
电影宣传相关请求输出一张海报、剧照或分镜图；用可见光色描述表现滤镜感。
'''

SHEET_CONTRACT = '''
【标准人设设计合同】
本次只编写一份全图共用的角色设计，视图、站姿、排列、留白和坐标由程序统一生成。
只返回以下结构的有效JSON，所有说明用中文，专名保留原文：
{"identity":"作品、角色、版本与形态","style":"画风","body":"体型及比例",
 "hair":"发型与发色","face":"五官和神态",
 "outfit":{"upper":"上装剪裁与颜色","collar":"领口与内衬",
 "waist":"腰部设计","lower":"下装","footwear":"鞋靴"},
 "accessories":[],"background":"简洁浅色背景"}
每个文字字段填写一个具体方案；配饰按实际设计填写文字数组，常规空配饰采用空数组。
作品人物依同一版本的可靠标志特征还原，未确认细节由角色身份名称表达。
默认原作画风，用户指定新画风时保留身份识别特征。服装定制按要求覆盖。
原创人物的未指定设计由你补齐，保证一套完整、相互配合的形象。
写实真人采用同一位演员的统一设计，脸型、眉眼、鼻唇、发际、体态及实体衣装在各视角对应，肤质符合给定年龄和景别。
各字段只写对应部位的可见内容，画面构图由程序处理。
'''


def standard_sheet_requested(text, instruction):
    if not instruction.lstrip().startswith('【Moss独立：人物人设图】'):
        return False
    custom = (r'只(?:要|需|做|画)?(?:正面|侧面|背面|[一二三两1-3])|'
              r'仅(?:正面|侧面|背面|[一二三两1-3])|(?:单张|一张)正面|'
              r'[一二两五六七八九十125-9]视图|四面全身|四个全身|左侧面|左右(?:两)?侧|'
              r'俯视|仰视|坐姿|战斗姿|双人|两个人|两个角色|两名|多个人|'
              r'(?:不要|不含|无需|去掉).*特写')
    return not re.search(custom, text, re.I)


def sheet_identities(text):
    from cores import anima_names
    if not anima_names.DATABASE.is_file():
        return []
    try:
        return anima_names.resolve(text)
    except ValueError as error:
        raise ValueError(str(error).replace('Anima', 'Moss')) from error


def _task_text(text):
    """Read request values, never treat backend JSON field names as a task."""
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return text

    def words(value):
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [word for child in value.values() for word in words(child)]
        if isinstance(value, list):
            return [word for child in value for word in words(child)]
        return []

    return '\n'.join(words(value))


def layout_needed(text, instruction):
    return instruction.lstrip().startswith('【Moss独立：人物人设图】') or bool(re.search(
        r'海报|漫画|分镜|三视图|四视图|多视图|人设图|人物设定图|角色设定图|'
        r'五官特写|面部特写.*全身|分栏|多格|文字排版|精确布局|固定位置|'
        r'\b(?:layout|poster|comic|turnaround|character\s+sheet)\b', _task_text(text), re.I))


def write_prompt(text, system=None, base_w=None, base_h=None, upscale=None, named_identity=True,
                 *, output_format='auto', validate_prompt=None):
    from cores.age_policy import assert_model_request, assert_model_response
    from . import anima_client, moss_client, qwen_client

    if not isinstance(text, str) or not text.strip():
        raise ValueError('先写要画什么')
    if system is not None and not isinstance(system, str):
        raise ValueError('Moss指令词须为文字')
    if output_format not in ('auto', 'caption', 'layout'):
        raise ValueError('Moss提示词格式选项须为auto、caption或layout')
    if validate_prompt is not None and not callable(validate_prompt):
        raise ValueError('Moss提示词内容检查须为可调用函数')
    text = text.strip()
    width, height = moss_client.dimensions(base_w, base_h, upscale)
    instruction = (system or SYSTEM.read_text(encoding='utf-8')).strip()
    if not instruction:
        raise ValueError('请先选择Moss指令词')
    from . import long_image_prompt
    if long_image_prompt.profile(instruction):
        return long_image_prompt.write_prompt(text, instruction, 'moss',
            base_w=width, base_h=height, upscale=upscale,
            output_format=output_format, validate_prompt=validate_prompt)
    standard_sheet = output_format != 'caption' and standard_sheet_requested(_task_text(text), instruction)
    identities = sheet_identities(text) if standard_sheet and named_identity else []
    if len(identities) > 1:
        standard_sheet = False
    use_layout = output_format == 'layout' or (output_format == 'auto' and layout_needed(text, instruction))
    contract = SHEET_CONTRACT if standard_sheet else FORMAT_CONTRACT if use_layout else (
        '【程序输出合同】本次为普通单幅图像，唯一输出格式为'
        '{"caption":"完整中文正向画面描述"}。只返回这一个JSON对象。'
        'caption用连贯画面正文描述主体、动作、场景、构图、光色和材质。'
        '用户的天气、动作、景别和实际文字保持一致；补齐空白形成具体画面。'
        '只描述可见内容，像素尺寸和画幅参数由程序处理。'
        '作品或角色专名保留原文。按需求组织有用细节。')
    if use_layout and not standard_sheet:
        contract = contract.replace('$WIDTH', str(width)).replace('$HEIGHT', str(height))
        contract += '\n本次采用布局草稿格式，按给定出底宽高规划像素区域。'
    assert_model_request(text, media='image_prompt')
    request = {'需求': text, '实际出底宽': width, '实际出底高': height}
    prompt_system = instruction + '\n' + contract + POSITIVE_OUTPUT_RULE
    for attempt in range(2):
        # Repeat every guard immediately before each possible model request.
        # Queue, network and age failures stay outside format repair handling.
        moss_client.local_guard()
        moss_client._resource_guard()
        anima_client.idle_guard()
        response, meta = qwen_client.chat(
            prompt_system,
            json.dumps(request, ensure_ascii=False),
            temperature=0.5, max_tokens=4096, reasoning=False,
            response_format={'type': 'json_object'}, check_context=True, return_meta=True)
        try:
            if meta.get('finish_reason') not in ('stop', 'eos'):
                raise ValueError('Qwen提示词未完整结束；本次未提交图片任务')
            draft = parse_draft(response)
            # A valid JSON string is a lossless spelling of a single caption.
            # Keep layout/sheet contracts strict and run the normal content
            # checks after wrapping; do not spend a repair call on the envelope.
            if output_format == 'caption' and isinstance(draft, str) and draft.strip():
                draft = {'caption': draft}
            if standard_sheet:
                from cores.moss_sheet import compile_named_sheet, compile_sheet
                draft = (compile_named_sheet(draft, text, identities[0]['trigger'], width, height)
                         if identities else compile_sheet(draft, width, height))
            prompt, kind = compile_draft(draft, width, height)
            if (kind == 'layout') != use_layout:
                raise ValueError('Qwen未按本次画面格式输出；本次未提交图片任务')
            prompt = image_prompt_content(prompt, normalize=True)
            if validate_prompt is not None:
                validate_prompt(prompt)
        except ValueError as error:
            if attempt:
                raise
            request['格式修复'] = {'错误': str(error), '上一版': response}
            if getattr(error, 'code', '') == 'scene_prompt_content':
                request['格式修复']['修复要求'] = (
                    '以原需求中的场景当前状态为准，补足报错中缺少的画面状态，移除未获支持的画面断言。'
                    '已明确的黑暗、杂乱、烧焦与局部破败分别保留，并写出具体可见的部位或物件。'
                    '采用场景卡支持的建筑结构、家具静物与现场光线；正常或特殊状态按原需求逐项落实。'
                    '空场景以空间本身为主体；人物动作和制作说明留给后续阶段。'
                    '修正旧化时采用材质纹理、接缝与反射，不改成另一种未获支持的旧化。')
            prompt_system += (
                '\n【一次格式修复】上次回复作为待修复数据。按程序合同返回完整JSON，'
                '修正报错字段并保留原需求、已有效的画面设计和实际文字。'
                '补全截断内容，使用本次指定的画面格式。'
                '物体和人物条目采用对应字段；文字条目的text填写原需求中的实际上图文字。'
                '报错涉及场景内容时，依据本次场景卡的当前状态修正对应描述；缺少的指定状态补写成具体画面，'
                '源文明确的暗光、杂乱、损毁与指定摆设在修复中保留。')
            continue
        assert_model_response(text, prompt)
        return {'model': 'moss', 'prompt': prompt, 'format': kind,
                'base_w': width, 'base_h': height}
