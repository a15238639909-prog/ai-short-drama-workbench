"""Compile one character design into a deterministic MOSS turnaround draft.

Qwen describes the character once. This module supplies views and whitespace;
it neither invents per-view clothing nor invokes a model or external service.
"""

_PLAN_FIELDS = {'identity', 'style', 'body', 'hair', 'face', 'background',
                'outfit', 'accessories'}
_TEXT_FIELDS = ('identity', 'style', 'body', 'hair', 'face', 'background')
_OUTFIT_FIELDS = ('upper', 'collar', 'waist', 'lower', 'footwear')
_BASE_WIDTH, _BASE_HEIGHT = 1360, 768
_BOXES = ((40, 64, 328, 704), (360, 64, 648, 704),
          (680, 64, 968, 704), (1016, 144, 1320, 464))
_FULL_BODY_VIEWS = (
    '正面站立全身，身体和面部朝向观者，清楚呈现正面轮廓',
    '面向画面右侧的90度侧面站立全身，身体与头部整体朝右，'
    '鼻尖指向画面右侧，呈现完整侧脸轮廓，肩胸、腰胯与双脚一起侧向，'
    '单侧眉眼、鼻梁、唇线与下颌构成清楚的纯侧面',
    '背面站立全身，后脑、肩背和衣装背部朝向观者，清楚呈现背面轮廓',
)


def _need(condition, message):
    if not condition:
        raise ValueError('Moss人设图：' + message)


def _fields(value, expected, name):
    _need(isinstance(value, dict), name + '须为对象')
    unknown = value.keys() - expected
    missing = expected - value.keys()
    _need(not unknown, name + '含未知字段：' + '、'.join(sorted(map(str, unknown))))
    _need(not missing, name + '缺少字段：' + '、'.join(sorted(missing)))


def _text(value, name):
    _need(isinstance(value, str) and bool(value.strip()), name + '须为非空文字')
    return value.strip()


def _design(plan):
    _fields(plan, _PLAN_FIELDS, '角色设计')
    result = {name: _text(plan[name], name) for name in _TEXT_FIELDS}
    _fields(plan['outfit'], set(_OUTFIT_FIELDS), 'outfit')
    result['outfit'] = {name: _text(plan['outfit'][name], 'outfit.' + name)
                        for name in _OUTFIT_FIELDS}
    _need(isinstance(plan['accessories'], list), 'accessories须为文字数组')
    result['accessories'] = [_text(item, 'accessories[%d]' % index)
                             for index, item in enumerate(plan['accessories'])]
    return result


def _scaled_box(box, width, height):
    # Integer arithmetic encloses all pixel edges without floating-point drift.
    left, top, right, bottom = box
    result = [left * width // _BASE_WIDTH, top * height // _BASE_HEIGHT,
              (right * width + _BASE_WIDTH - 1) // _BASE_WIDTH,
              (bottom * height + _BASE_HEIGHT - 1) // _BASE_HEIGHT]
    _need(0 <= result[0] < result[2] <= width and
          0 <= result[1] < result[3] <= height, '画布不足以安排四个视角')
    return result


def compile_sheet(plan, width, height, *, lighting_caption=None):
    """Return an abs/xyxy draft accepted by moss_prompt_format.compile_draft.

    All design fields are required. The single shared summary owns the complete
    costume; full-body items add only deterministic views and posture. A separate
    portrait caption selects head/shoulder details from that same design.
    """
    _need(all(type(value) is int and value > 0 and value % 16 == 0
              for value in (width, height)), '画布宽高须为16的正整数倍')
    design = _design(plan)
    outfit = design['outfit']
    parts = [
        '一张三视图加五官特写的角色设定参考板，左侧依次是正面全身、'
        '朝右90度纯侧面全身、背面全身，右侧为明显放大的正脸头肩特写；'
        '三个完整站立人像与一个局部头像组成全页，右侧头像的面部尺度大于左侧全身图中的头部',
        '人物身份：' + design['identity'], '画风：' + design['style'],
        '体型比例：' + design['body'], '发型：' + design['hair'],
        '五官：' + design['face'], '上装：' + outfit['upper'],
        '领口：' + outfit['collar'], '腰部：' + outfit['waist'],
        '下装：' + outfit['lower'], '鞋靴：' + outfit['footwear'],
    ]
    if design['accessories']:
        parts.append('配饰：' + '、'.join(design['accessories']))
    parts.extend([
        '背景：' + design['background'],
        '全图采用这一份角色设计，各视角为同一人物、同一套服装、同一配色与配饰',
        (_text(lighting_caption, '布光') if lighting_caption is not None
         else '柔和均匀照明，角色轮廓与衣装结构清楚'),
        '前三个全身视图为平视正交式设定稿，头顶与鞋底分别处于共同水平基线，'
        '保持同一身高与头身比，完整呈现头脚',
        '全身人物自然中性站立，双臂稍离躯干，双手自然下垂',
        '各视角独立安排，四周留出整洁边距，视图之间有清楚间隔',
    ])
    items = [
        {'label': 'character',
         'caption': design['identity'] + '；' + view + '；'
                    '采用全图统一角色设定，自然中性站姿，平视正交式全身视图',
         'bbox': _scaled_box(box, width, height)}
        for view, box in zip(_FULL_BODY_VIEWS, _BOXES[:3])
    ]
    portrait = '；'.join([
        design['identity'], '正脸头肩特写，面部直面观者，双眼正视镜头',
        '明显放大的面部近景，完整发型、脸部和颈肩占满此分区，画幅下沿裁切在上胸',
        '发型：' + design['hair'], '五官：' + design['face'],
        '可见衣领：' + outfit['collar'],
        '与全图统一角色设计一致',
    ])
    items.append({'label': 'character', 'caption': portrait,
                  'bbox': _scaled_box(_BOXES[3], width, height)})
    return {'width': width, 'height': height,
            'bbox_coord_space': 'abs', 'bbox_format': 'xyxy',
            'summary': {'caption': '；'.join(parts) + '。'}, 'items': items}


def compile_named_sheet(plan, request, trigger, width, height):
    """Keep locally confirmed named identities instead of speculative anatomy.

    Names and the verbatim user request carry canonical appearance and explicit
    customizations. Qwen still supplies style/background; its invented costume
    details cannot replace a known character's source design.
    """
    design = _design(plan)
    request = _text(request, '人物需求')
    trigger = _text(trigger, '已确认角色名称')
    design.update(
        identity=trigger + '；本次人物、形态与定制：' + request,
        body='指定角色形态的体型与头身比例',
        hair='本次指定角色形态及定制对应的发型与发色',
        face='本次指定角色形态及定制对应的五官与神态',
        outfit={key: '本次指定角色版本与服装定制对应的' + label
                for key, label in zip(_OUTFIT_FIELDS, ('上装', '领口与内衬', '腰部设计', '下装', '鞋靴'))},
        accessories=[])
    return compile_sheet(design, width, height)
