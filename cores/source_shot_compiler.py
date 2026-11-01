"""Source-owned actions with a separate, validated photographic description.

Only newly written workbench prompts use this policy. Source assets, saved
prompts and rendered media are untouched; video_action_compiler=legacy opts out.
"""
import copy
import math
import re
from . import source_actor_staging as staging

VERSION = 'source-shots-v1'
FRAMING = ('远景', '全景', '中景', '近景', '特写')
ANGLE = ('平视', '略俯拍', '略仰拍', '侧面', '过肩')
MOVEMENT = ('固定机位', '缓慢推近', '缓慢拉远', '平稳跟拍', '缓慢横移',
            '向上摇镜', '向下摇镜', '水平摇镜', '升降跟拍', '弧线跟拍', '轻微手持跟拍')
DELIVERY = ('自然清楚', '轻声', '急促', '坚定', '迟疑', '平静', '激动')
VISUAL_FIELDS = ('composition', 'lighting', 'texture', 'performance', 'camera_detail')
# Closed states, rather than a second free-form actor/action writer.
PERFORMANCE = ('', '神情平静。', '神情专注。', '神情紧张。', '神情放松。', '神情疲惫。',
               '神情惊讶。', '神情严肃。', '嘴角带着轻微笑意。', '眉头轻蹙。', '手部力度平稳。')
SYSTEM = '''你是中文影视摄影导演，使用当前分段的 source_units 编排摄影并写具体的画面表现。
输出 JSON：style_description、scene_description、soundscape、shots。全部画面文字采用正面、具体、可见或可听的描述。
style_description用一两句落实visual_style中的影像媒介、色彩、反差、颗粒与清晰度；scene_description描写scene_facts和当前原文共同建立的环境空间。soundscape只写当前环境与动作实际产生的声音，声响与原文及对白给定的物件状态一致；静止或停机的物件保留当前外部操作产生的声音。
shots 按叙事先后排列；每项填写 unit_ids、framing、angle、movement、weight、delivery、visual、acting。
每个原文单元按输入顺序使用一次。每镜的unit_ids是连续相邻区间，镜头之间也保持区间顺序；例如U001、U002同镜后接U003，再接U004、U005。中间有对白或其他事件时，相似的前后动作分别安排，不跨过中间单元合并。依据动作重点安排必要切镜。
unit_ids 指向原文，程序会装入中文镜头正文与原对白。景别、角度、运镜从给定选项选择，weight 是本镜头相对时长（1至10）。
visual为摄影补充：composition写画面左右、占比、前后景及空间线条；lighting写光源、方向、色温及受光表面；固定灯具形成稳定光区，人物经过不同光区呈现对应受光；texture写镜头可见的材质；camera_detail写起始构图、运动触发点、跟随对象、移动方向和结束构图，焦点变化围绕当前事件的新信息。静止镜头写清取景对象与人物表演在构图中的位置。细节按景别和情绪选择，近景能看清眉眼口角，全景重点交代身体节奏和空间，镜头运动有明确的信息目标。
current_wardrobe描述本段开场衣装；source_units的opening_wardrobe和ending_wardrobe分别给出该单元前后衣装及衣物包裹物。本段穿脱按原文时点发生，后续镜头沿用变化后的衣装，已裹在物件外的衣服作为包裹面料描写。
camera_detail用镜头、摄影机、机位或焦点作为主语；可以引用当前source_units里已给出的动作作为摄影触发，例如“镜头随陈默起身缓慢上摇，保持上半身构图，最后稳定在侧脸近景”。它只说明摄影怎样跟随，人物动作由原文主干执行一次。
camera_detail与movement采用同一主运镜。固定机位保持摄影机位置，景深和焦点可以变化；移动镜头围绕一个明确目标完成起止构图。
performance只从photography_options.performance中选择一个完整短句或空串，表示当前单一人物的表情或手部力度；没有依据、纯空镜或多人主体时留空。它不是动作句，也不展开身体姿势和视线变化。
例如composition可写“红色车身位于右半画面，左侧湿石板路延伸至暖色橱窗”，lighting可写“冷色路灯照亮灰色风衣肩部，暖光勾出面部边缘”。摄影补充保持静态空间与表面表现，人物动作、接触物、身体移动和姿态变化仅由source_units写入。已有动作也不在摄影字段重写；动作完成后的新姿态同样需要对应的原文依据。scene_description只写空间和陈设，不写人物行动或门窗开合状态。
declared_lighting_states是依据原文形成的文字计划状态，不是实拍观察。当前地点中原文明确熄灭的灯具，在熄灭后只保留仍有依据的窗光、门缝光等光源；风格中的暖色配色不代表该灯重新发光。摄影字段只写当前光源和受光结果，熄灯、开灯等事件由原文时间轴写入。
用“光线均匀柔和、主体清楚可见、机位稳定、空旷的自然环境”等实际画面表达，不写“无阴影、不遮挡、无移动、无人物、未入画”等排除说明。
剧情动作及台词由程序原文插入。visual只补摄影表现和受限表情；不复述原文动作，不增加拿放物品、开关门、进出地点、换装等故事事件，不添加人物或台词。角色外形与衣着按appearance_facts和current_wardrobe；只描述镜头实际看见的部分。soundscape写环境底噪、雨声等实际现场声音；动作声响与当前source_units中的实际行为和物件对应，采用声响描述而非再叙述一次人物行动。
规则、否定式要求、检查过程、JSON状态字段和版本号属于后台，不写入style_description、scene_description、soundscape或visual。没有信息的细节可简短写已有空间或材质的实际状态，不能编造前史或结局。
对白所在镜头给足自然说话时间，delivery 为该镜头的说话语气，从给定选项选择。
场景空镜依然按原文编排；人物对白用适合说话者的景别。起镜承接可信的实际末态。camera_intent是本段摄影要求，依据它选择景别、角度与运镜。
source_units带timing_block时，每镜只能包含同一时间块的单元。block_start和block_end是用户指定的秒数，程序会保留块边界；块内可以继续分镜。
逐字对白和画内原字由原文传输，不在摄影段再抄一遍。输出完整JSON。''' + '\n' + staging.ACTING_INSTRUCTION
_VISUAL_SCHEMA = dict(type='object', additionalProperties=False,
    properties={key: (dict(type='string', enum=list(PERFORMANCE)) if key == 'performance' else
                      dict(type='string', minLength=0, maxLength=350)) for key in VISUAL_FIELDS})
SCHEMA = dict(type='object', additionalProperties=False,
    required=['style_description', 'scene_description', 'soundscape', 'shots'], properties={
    'style_description': dict(type='string', minLength=1, maxLength=400),
    'scene_description': dict(type='string', minLength=1, maxLength=500),
    'soundscape': dict(type='string', minLength=1, maxLength=350),
    'shots': dict(type='array', minItems=1, maxItems=8, items=dict(type='object',
        additionalProperties=False, required=['unit_ids', 'framing', 'angle', 'movement', 'weight', 'delivery', 'visual', 'acting'],
        properties=dict(unit_ids=dict(type='array', minItems=1, items=dict(type='string')),
            framing=dict(type='string', enum=list(FRAMING)), angle=dict(type='string', enum=list(ANGLE)),
            movement=dict(type='string', enum=list(MOVEMENT)),
            weight=dict(type='number', minimum=1, maximum=10), delivery=dict(type='string', enum=list(DELIVERY)),
            visual=_VISUAL_SCHEMA, acting=staging.ACTING_SCHEMA)))})


# Actions have exactly one rendering channel. These bounded guards complement
# the closed performance schema; source authorization never licenses narrating
# an action again in photography. They do not exhaust natural-language semantics.
_EVENTS = (
    r'打开|拉开|推开|掀开|开(?:车)?门|开启(?:车)?门',
    r'关上|关掉|关闭|合上|拉上|关(?:车)?门|闭合',
    r'脱下|脱掉|脱去|换上|换成|穿上|戴上|摘下',
    r'拿起|拾起|捡起|接过', r'放下|递给|交给|扔下|丢下',
    r'走进|走入|进入|冲进|跑进', r'走出|离开|跑出|冲出|逃出',
    r'击打|刺入|砍向|开枪|射击|刺杀|踢向', r'亲吻|接吻|拥抱|抱起',
)


_PHOTOGRAPHIC_ACTIONS = (
    r'抬起|举起|抬手|举手|抬臂|举臂|伸出|伸向|伸手|伸臂',
    r'握住|握着|握紧|握持|抓住|抓紧|攥住|攥紧|扣住|钩住|扶住|扶着|松开|松手|撒手',
    r'夹着|夹住|夹持|夹在|夹于|抱着|抱住|抱在|怀抱|拎着|拎住|捧着|捧住|捧在|按着|挎着|挎在|背着|背在|托着|托住|托在|提着|提住|手持|持着|手握|肩背',
    r'探入|探进|探出|探身|探头|探手|钻入|钻进|钻出|钻到|跨入|跨进|跨出|跨过|迈入|迈出',
    r'坐下|坐进|坐入|坐稳|稳坐|端坐|落座|就座|起身|站起|站稳|蹲下|躺下|俯身|弯腰|弯下|转身|转头|回头|扭头',
    r'站在|站着|坐在|坐着|倚靠|倚在|靠在|卧在|靠上',
    r'走到|走向|走近|靠近|退后|后退|迈步|挪步|挪动|踏入|踏进|踏出|登上|跳下',
    r'看向|望向|转向|瞥向|扫向|垂下眼|抬眼|垂眸|回望|环视',
    r'(?:身体|上半身|下半身|肩膀|头部).{0,8}(?:前倾|后仰|转动|移动|倾入|移入|移出)',
    r'(?:姿态|姿势|站姿|坐姿).{0,8}(?:变为|转为|变成|切换)',
    r'(?:双手|右手|左手|手掌|手指|双腿|右腿|左腿).{0,8}(?:放在|搁在|搭在|交叠|叠放|贴在|压在|撑在|收进|伸入|搁上|垂在|垂落|搭上|停在)',
    r'(?:一名|一个|一位|另一名|另一位|另一个).{0,8}(?:陌生|男子|男人|男生|女子|女人|女生|男孩|女孩|老人|士兵|行人)',
    r'驶离|驶出|驶入|驶向|开走|开车|停车|发动|启动|熄火|加速|刹车|起飞|降落|倒车',
    r'开灯|关灯|熄灯|点亮|熄灭|亮起',
    r'\b(?:opens?|closes?|grabs?|grasps?|holds?|reaches?|enters?|leaves?|steps?|sits?|stands?\s+up|leans?\s+in|puts?\s+on|takes?\s+off)\b',
)
_META_PROSE = re.compile(r'当前(?:分段|原文|语境)|原文(?:动作|行动|对白|编号)|说出原话|自然延续|依次呈现|主体状态连贯|动态节奏自然|source_units?|unit_ids?|declared_lighting_states|文字计划状态|实拍观察')
_DOOR_STATES = (
    r'(?:门|窗)[^，。；\n]{0,10}(?:紧闭|敞开|半开|半掩|开着|关着|开启|关闭|闭合|打开|开合)',
    r'(?:紧闭|敞开|半开|半掩|开启|关闭|闭合|打开)(?:的)?[^，。；\n]{0,10}(?:门|窗)',
)
_SOUND_OBJECTS = re.compile(
    r'(?P<curtain>窗帘|帘子|帘布|帷幔)|(?P<book>书本|书籍|笔记本|本子|课本|册子|书)|'
    r'(?P<door>车门|房门|门扇|门板|大门|木门|铁门|门)|(?P<window>窗户|窗扇|玻璃窗|木窗|窗)')

# Only common, concrete garment names are used for ownership disambiguation.
# Generic "衣服/外套/裤子" do not establish a unique outfit or a new design.
_GARMENT_WORDS = ('西装外套', '西装长裤', '连体工作服', '工作服', '风衣', '大衣', '夹克', '皮衣', '雨衣', '外袍', '中衣',
    '西装', '衬衫', '高领衫', '针织衫', '毛衣', '卫衣', '背心', 'T恤', '长裤', '短裤', '牛仔裤',
    '半身裙', '连衣裙', '长裙', '短裙', '短靴', '长靴', '高跟鞋', '运动鞋', '皮鞋', '披风', '斗篷',
    '长袍', '道袍', '礼服', '旗袍', '汉服', '和服', '校服', '病号服', '宇航服', '盔甲', '铠甲')
_GARMENT_WORD = re.compile('|'.join(re.escape(word) for word in sorted(_GARMENT_WORDS, key=len, reverse=True)))
_GARMENT_PREFIX = re.compile(r'(?:(?:深|浅|暗|亮|淡)?(?:藏蓝|米白|卡其|象牙白|灰|黑|白|蓝|红|绿|棕|褐|紫|黄|青|粉|银|金)(?:色)?|长款|短款|修身|宽松|棉质|羊毛|亚麻|呢料|皮质|针织|长|短)*$')
_LIGHT_SOUND = re.compile(r'(?:光束|灯光|光线|光柱)[^，,。；;！？!?\n]{0,48}(?P<effect>摩擦声|脚步声|撞击声|碰撞声)')
_LIGHT_SOUND_CONTINUATION = re.compile(r'(?:光束|灯光|光线|光柱)[^，,。；;！？!?\n]{0,36}[，,]\s*(?:并|同时|接着)?(?:发出|产生|传来|带着|伴着|伴随)[^，,。；;！？!?\n]{0,16}(?P<effect>摩擦声|脚步声|撞击声|碰撞声)')


def _clothing_ownership(payload):
    """Read the existing positive current-wear contract, never re-add a baseline."""
    from .wardrobe_continuity import positive_clothing_lines
    refs = payload.get('canonical_references') or []
    labels = {}
    for ref in refs:
        if ref.get('role') != 'character' or not ref.get('name'):
            continue
        name = ref['name']
        labels[name] = name
        if type(ref.get('subject')) is int:
            labels['<Subject %d>' % ref['subject']] = name
    state = payload.get('wardrobe_state') or {}
    owners = {}
    source_plan = payload.get('source_wardrobe_plan')
    if source_plan:
        from . import source_wardrobe_plan
        clothing_lines = source_wardrobe_plan.positive_clothing_lines(source_plan, refs)
    else:
        clothing_lines = positive_clothing_lines(state, refs)
    for line in clothing_lines:
        label, marker, clothing = line.partition('：当前衣着为')
        if not marker or label not in labels:
            continue  # Removed garments listed as props are not current wear.
        for word in _GARMENT_WORDS:
            if word in clothing:
                owners.setdefault(word, set()).add(labels[label])
    changed = any(person.get('outfit_replaced') or any(
        isinstance(slot, dict) and slot.get('status') in ('worn', 'removed', 'damaged')
        for slot in (person.get('slots') or {}).values())
        for person in (state.get('characters') or {}).values() if isinstance(person, dict))
    if source_plan:
        changed = any(person.get('changed') for person in source_plan.get('characters', {}).values())
    return owners, labels, changed


def _garment_owner_text(value, ownership, visible_names, source, *, prop_plan=None):
    """Name an exclusive wearer, or reject an explicit actor/outfit conflict."""
    if not isinstance(value, str) or not value:
        return value
    owners, labels, changed = ownership
    if not owners and not changed:
        return value
    label_pattern = '|'.join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    protected = list(re.finditer(label_pattern, value)) if label_pattern else []
    protected += list(re.finditer(r'“[^”]*”|「[^」]*」|"[^"\n]*"', value))
    edits = []
    for match in _GARMENT_WORD.finditer(value):
        if any(item.start() <= match.start() and match.end() <= item.end() for item in protected):
            continue
        word = match.group()
        clause_start = max(value.rfind(mark, 0, match.start()) for mark in '，,。；;\n') + 1
        clause_end = next((index for index in range(match.end(), len(value)) if value[index] in '，,。；;\n'), len(value))
        clause = value[clause_start:clause_end]
        if prop_plan and any(word in prop.get('item', '') and prop.get('object')
                             and prop['object'] in clause for prop in prop_plan.get('props') or []):
            continue  # A wrapper has an owner/holder, not the current wearer's identity.
        candidates = owners.get(word, set())
        if not candidates:
            if changed and word not in source:
                raise ValueError('衣物片段“%s”未获当前穿戴合约确认；已有换装或衣层变化，请省略这项补充，保留原文动作。' % value[max(0, match.start() - 15):match.end() + 18])
            continue
        if len(candidates) != 1:
            continue
        owner = next(iter(candidates))
        if owner not in visible_names:
            raise ValueError('衣物片段“%s”属于%s，但该人物未在本镜头原文中出镜；请省略该衣物补充，保留当前人物的摄影细节。' % (word, owner))
        prefix_match = _GARMENT_PREFIX.search(value[:match.start()])
        start = prefix_match.start() if prefix_match else match.start()
        clause_start = max(value.rfind(mark, 0, start) for mark in '，,。；;\n') + 1
        before = value[clause_start:start]
        named = list(re.finditer(label_pattern, before)) if label_pattern else []
        if named:
            claimed = labels[named[-1].group()]
            if claimed != owner:
                raise ValueError('衣物片段“%s”当前唯一对应%s，句中却绑定%s；请按已确认衣着区分人物，或省略这一补充。' % (value[clause_start:match.end()], owner, claimed))
            continue
        pronoun = re.search(r'(?:她|他|其|人物)(?:身上)?的$', before)
        if pronoun:
            start = clause_start + pronoun.start()
        edits.append((start, owner + '的'))
    for start, text in sorted(edits, reverse=True):
        # When a pronoun was present it is replaced, not stacked with a name.
        tail = re.match(r'(?:她|他|其|人物)(?:身上)?的', value[start:])
        end = start + tail.end() if tail else start
        value = value[:start] + text + value[end:]
    return value


def _planned_garment_text(value, plan):
    """Removed clothes can remain in the image as an explicitly named bundle."""
    if not value or not plan:
        return value
    from .source_wardrobe_plan import _slot
    people = plan.get('characters') or {}
    for prop in plan.get('props') or []:
        for word in _GARMENT_WORDS + ('外套',):
            generic_outer = word == '外套' and prop.get('slot') == 'outerwear'
            if word not in prop.get('item', '') and not generic_outer:
                continue
            for clause in re.split(r'[，,。；;\n]', value):
                if word not in clause:
                    continue
                wearers = {identity for identity, person in people.items()
                           if any(word in piece or (generic_outer and _slot(piece) == 'outerwear')
                                  for piece in person.get('worn', []))}
                named = {identity for identity, person in people.items() if person['name'] in clause}
                if (named and named <= wearers) or (not named and wearers):
                    continue
                bundle = re.search(r'包裹|布包|包覆|裹着|外裹|包在|裹在|包住', clause)
                obj = prop.get('object') or ''
                if not bundle and not (obj and obj in clause):
                    raise ValueError('衣物“%s”已从穿戴转为物件，摄影补充须明确描写包裹物或当前内搭' % word)
    return value


def _validate_light_sounds(value, source, label):
    patterns = (_LIGHT_SOUND, _LIGHT_SOUND_CONTINUATION)
    explicit = {match['effect'] for pattern in patterns for match in pattern.finditer(source)}
    for match in (match for pattern in patterns for match in pattern.finditer(value)):
        # A real footstep under lamplight is not a sound emitted by the light.
        if re.match(r'(?:光束|灯光|光线|光柱)(?:下|里|中|内)', match.group()):
            continue
        if match['effect'] not in explicit:
            raise _photo_error(value, label, match, '普通光束不能自行产生摩擦声、脚步声或撞击声；原文没有指定这个虚构音效')


def _sound_event_object(value, event):
    """Bound common direct objects, not an entire-clip verb-group allowance."""
    own = _SOUND_OBJECTS.search(event.group())
    if own:
        return own.lastgroup
    before = re.split(r'[，。；！？!?\n]', value[:event.start()])[-1]
    after = re.split(r'[，。；！？!?\n]|然后|随后|接着|之后|同时|并且', value[event.end():])[0]
    prior = list(_SOUND_OBJECTS.finditer(before))
    # 把/将 explicitly binds the object before the verb. For a normal verb-object
    # clause prefer the following noun, so 在门前合上书本 binds 书本, not 门.
    if prior and re.search(r'把|将', before[:prior[-1].start()]):
        return prior[-1].lastgroup
    following = _SOUND_OBJECTS.search(after[:20])
    if following:
        return following.lastgroup
    if prior and len(before) - prior[-1].end() <= 10:
        return prior[-1].lastgroup
    return ''


def _source_sound_event(pattern, source, value, event):
    target = _sound_event_object(value, event)
    for clause in re.split(r'[，。；！？!?\n]', source):
        for original in re.finditer(pattern, clause, re.I):
            if not target or _sound_event_object(clause, original) == target:
                return True
    return False


def _static_nearness(match, actors):
    """A door next to a pavement is geometry; a person approaching it is action."""
    raw_prefix = match.group('prefix')
    prefix = raw_prefix.strip()
    # Take the actual subject after its possessive modifier. Exact cast names
    # take precedence over object-looking name suffixes, e.g. the person 顾亭.
    subject = prefix.rsplit('的', 1)[-1]
    if any(name and name in subject for name in actors):
        return match.group()
    if re.search(r'她|他|人物|女子|男子|女孩|男孩|女人|男人|老人|士兵|司机|记者|说话者', subject):
        return match.group()
    if re.search(r'逐渐|缓慢|正在|开始|持续|慢慢|一步步|越来越|迅速|突然|向前|向后|随后|接着|然后', prefix):
        return match.group()
    if re.search(r'(?:门|窗|墙|柱|梁|楼|廊|檐|亭|殿|屋|台|栏|桌|椅|柜|床|路|街|道|坡|阶|桥|河|山|岩|岸|树|石|花坛|水池)$', subject):
        return raw_prefix + '邻近'
    return match.group()


def _photo_error(value, label, match, reason):
    left = max(value.rfind(mark, 0, match.start()) for mark in '，。；\n') + 1
    ends = [value.find(mark, match.end()) for mark in '，。；\n']
    right = min([index for index in ends if index >= 0] or [len(value)])
    fragment = value[left:right].strip()
    if len(fragment) > 110:
        fragment = value[max(left, match.start() - 35):min(right, match.end() + 35)]
    if label.endswith('composition'):
        hint = 'composition仅写主体或物体的左右位置、画幅占比、前后景和空间线条；例如“车身位于右半画面，手部轮廓位于左侧”。'
    elif label == '场景表现':
        hint = 'scene_description仅写街道、建筑、车辆等固定空间；门窗状态和人物姿态由原文时间轴表达。'
    elif label == '现场声音':
        hint = 'soundscape保留环境底噪及当前原文已发生动作的声响；删除原文未发生事件的声音。'
    else:
        hint = '只保留光源、色温、受光表面、材质、焦点或景深；人物行为由原文时间轴写入。'
    return ValueError('%s命中片段“%s”：%s；%s' % (label, fragment, reason, hint))


class PhotographyError(ValueError):
    """Local repair locations; source text is never one of these locations."""
    def __init__(self, errors):
        self.field_paths = [tuple(path) for path, _ in errors]
        self.field_path = self.field_paths[0]
        super().__init__('；'.join('%s：%s' % ('.'.join(map(str, path)), message)
                                 for path, message in errors))


def _camera_trigger_text(value, source, actor_names):
    """Validate camera references without treating them as a second actor action."""
    replacements = []
    # Major events and object interactions stay exclusively in source_units.
    # A camera may follow a body's already-authored movement, with the same actor.
    body_triggers = (r'起身|站起', r'坐下', r'蹲下', r'俯身|弯腰', r'转身', r'转头|回头')

    def nearest_actor(text):
        positions = [(text.rfind(name), name) for name in actor_names if name and name in text]
        return max(positions)[1] if positions else None

    for pattern in body_triggers:
        for match in re.finditer(pattern, value, re.I):
            prefix = re.split(r'[，,。；;！？!?\n]', value[:match.start()])[-1]
            trigger = re.search(r'(?:镜头|摄影机|机位|焦点|对焦)[^，。；\n]{0,28}(?:跟随|随着|伴随|随|在)[^，。；\n]{0,20}$', prefix)
            if not trigger:
                continue
            target = nearest_actor(prefix)
            if target is None:
                candidates = [name for name in actor_names if name and name in source]
                target = candidates[0] if len(candidates) == 1 else None
            if target is None:
                continue
            same_actor = any(nearest_actor(re.split(r'[。；\n]', source[:item.start()])[-1]) == target
                             for item in re.finditer(pattern, source, re.I))
            if not same_actor:
                continue
            replacements.append((match.start(), match.end()))
    for start, end in sorted(set(replacements), reverse=True):
        value = value[:start] + '摄影触发点' + value[end:]
    return value


def _visual_text(value, label, source='', *, max_length=500, channel='visual', allow_empty=False, actor_names=(),
                 lighting_context=None, scene_facts=None, current_scene='', lighting_channel=None):
    from .image_prompt_content import normalize_positive_description
    if not isinstance(value, str) or len(value) > max_length or (not allow_empty and not value.strip()):
        raise ValueError(label + '须为有界的具体画面文字')
    if not value.strip():
        return ''
    if channel == 'soundscape':
        _validate_light_sounds(value, source, label)
    try:
        value = normalize_positive_description(value, label=label)
    except ValueError as error:
        raise ValueError(label + '片段“' + value[:110] + '”：' + str(error)) from error
    if lighting_context:
        from .source_lighting_state import validate_lighting_description
        try:
            validate_lighting_description(value, source_context=lighting_context, scene_facts=scene_facts or {},
                current_scene=current_scene, channel=lighting_channel or channel)
        except ValueError as error:
            raise ValueError(label + '片段“' + value[:110] + '”：' + str(error)) from error
    if re.search(r'\[Shot\s|\bAt\s+\d+:|</?d>|\{\{|<\s*(?:Picture|Video|Audio)\s', value, re.I):
        raise ValueError(label + '不能嵌入传输标签、额外素材或时间轴')
    meta = _META_PROSE.search(value)
    if meta:
        raise _photo_error(value, label, meta, '混入后台模板话，请填写具体可见或可听内容')
    # An explicitly named camera/focus can move; a body/actor cannot acquire
    # a new movement through that allowance. Keep the original prose intact.
    checked = _camera_trigger_text(value, source, actor_names) if channel == 'camera' else value
    checked = re.sub(r'(?:焦点|对焦)(?:从|由)[^，。；\n]{1,25}转向',
        lambda m: m.group().replace('转向', '取景') if not re.search(r'[她他](?!的)', m.group()) else m.group(), checked)
    checked = re.sub(r'(?:镜头|摄影机|机位)(?:缓慢|平稳|逐渐|轻轻|继续|向前|向后|向左|向右|地){0,4}(?:靠近|退后|转向|转动|旋转)',
        lambda m: re.sub(r'靠近|退后|转向|转动|旋转', '取景', m.group()), checked)
    checked = re.sub(r'(?P<prefix>[^，。；\n]{1,32})靠近',
        lambda m: _static_nearness(m, actor_names), checked)
    if channel != 'soundscape':
        for pattern in _DOOR_STATES:
            state = re.search(pattern, checked)
            if state:
                raise _photo_error(value, label, state, '门窗开合状态由对应原文单元写入，不能在摄影补充中提前或冻结状态')
    event_patterns = _EVENTS + _PHOTOGRAPHIC_ACTIONS
    if channel != 'soundscape':
        event_patterns += staging.CORE_EVENT_PATTERNS
    for pattern in event_patterns:
        for event in re.finditer(pattern, checked, re.I):
            if channel == 'soundscape' and _source_sound_event(pattern, source, value, event):
                # The original action may have a simultaneous sound, not a
                # second visual instruction to perform that action again.
                if re.search(r'声|响|音|摩擦|\bsound\b', value, re.I):
                    continue
            raise _photo_error(value, label, event, '含剧情动作、姿态变化或新增人物；动作只由对应原文单元写入')
    if channel != 'soundscape':
        # Catch copied clauses using uncommon verbs too. Source IDs themselves
        # are preserved, including deliberately repeated original actions.
        compact = re.sub(r'[\s，,。；;：:！？!?、]', '', checked if channel == 'camera' else value)
        for clause in re.split(r'[。！？!?\n]', source):
            clause = re.sub(r'[\s，,；;：:、]', '', clause)
            if len(clause) >= 6 and clause in compact:
                raise ValueError(label + '片段“' + value[:110] + '”复述了原文内容“' + clause[:80] + '”，请只保留摄影表现')
    return value.strip()


def _scene_state_text(value, facts, *, complete=False):
    condition = (facts or {}).get('scene_condition')
    if not condition or not value:
        return value
    from .scene_prompt_content import _condition_issues, ScenePromptContentError
    if complete:
        checked = value
    else:
        # Scene maintenance constrains architecture, not a character's old coat.
        checked = '。'.join(clause for clause in re.split(r'[。；;\n]', value)
            if not _GARMENT_WORD.search(clause) and re.search(
                r'场景|房|殿|阁|廊|门|窗|墙|地面|地板|石板|台阶|屋顶|雨棚|'
                r'陈设|杂物|工作台|暴雨|雨后|晴|雪|雨幕|雨帘', clause))
    issues = _condition_issues(checked, condition, facts or {})
    if not complete:
        issues = [issue for issue in issues if not issue.startswith('缺少')]
    if issues:
        raise ScenePromptContentError(issues)
    return value


def _basic_visual():
    return dict.fromkeys(VISUAL_FIELDS, '')


def _basic_plan(shots, payload):
    scene = str(payload['beat']['scene'])
    condition = (payload.get('scene_facts') or {}).get('scene_condition')
    if condition:
        from .scene_condition import positive_condition
        scene += '，' + positive_condition(condition)
    return dict(style_description=str(payload.get('visual_style') or '影像色彩与明暗层次统一。'),
        scene_description=scene,
        soundscape='轻微的现场环境底噪。',
        shots=[dict(shot, visual=_basic_visual()) for shot in shots])


def _user_block_map(payload):
    contract = payload['beat'].get('source_contract') or {}
    if contract.get('timing_source') != 'user':
        return None
    from .director_flow import ordered_content
    from .episode_contract import ContractError

    def invalid(message):
        raise ContractError('用户分镜时间块' + message + '；请核对已保存的分镜时间，原文保留。',
                            stage='video_prompt', code='source_timing')

    blocks = contract.get('blocks') or []
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= 8:
        invalid('须为1至8个连续时间块')
    source, says = payload['beat']['source_order'], contract.get('says') or []
    mapped, cursor, spoken, previous = [], 0, 0, 0.0
    for number, block in enumerate(blocks, 1):
        if not isinstance(block, (list, tuple)) or len(block) != 4:
            invalid('结构不完整')
        begin, finish = block[:2]
        if any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t)
               for t in (begin, finish)) or finish <= begin or abs(begin - previous) > .001:
            invalid('须从0秒按顺序连续覆盖，不能重叠、留空或倒置')
        if not isinstance(block[3], list):
            invalid('对白编号不完整')
        count = len(block[3])
        if spoken + count > len(says):
            invalid('对白记录不完整')
        try:
            rows = ordered_content(dict(blocks=[block], says=says[spoken:spoken + count]))
        except (ValueError, TypeError, KeyError, IndexError, StopIteration):
            invalid('原文与对白记录不一致')
        if not rows:
            invalid('缺少该区间的动作或对白')
        for row in rows:
            current = source[cursor] if cursor < len(source) else {}
            if (current.get('text') != row.get('text') or
                    (current.get('speaker') or '') != (row.get('speaker') or '')):
                invalid('与当前原文顺序不一致')
            mapped.append(dict(timing_block='B%03d' % number, block_start=float(begin), block_end=float(finish)))
            cursor += 1
        spoken += count
        previous = float(finish)
    if cursor != len(source) or spoken != len(says) or abs(previous - float(payload['seconds'])) > .001:
        invalid('没有完整覆盖当前原文及本段总时长')
    return mapped


def _time_windows(source, seconds):
    windows = []
    for unit in source:
        key = unit.get('timing_block')
        if not key:
            return [(None, 0.0, float(seconds), source)]
        if not windows or windows[-1][0] != key:
            windows.append((key, unit['block_start'], unit['block_end'], []))
        windows[-1][3].append(unit)
    return windows


def units(payload):
    result, spoken = [], 0
    slots = payload.get('dialogue_slots') or []
    block_map = _user_block_map(payload)
    for row_index, row in enumerate(payload['beat']['source_order']):
        dialogue = bool(row.get('is_dialogue') or row.get('dialogue_id') or row.get('speaker'))
        if dialogue:
            if spoken >= len(slots) or row['text'] != slots[spoken]['text']:
                raise ValueError('原文单元与固定对白不一致')
            parts = [row['text']]
        else:
            # Preserve every character of the original action, including punctuation.
            parts = re.findall(r'[^。！？!?\n]+[。！？!?\n]*|[。！？!?\n]+', row['text'])
        for text in parts:
            if not text.strip():
                continue
            item = dict(id='U%03d' % (len(result) + 1), text=text.strip(), kind='dialogue' if dialogue else 'action')
            if block_map is not None:
                item.update(block_map[row_index])
            if dialogue:
                item.update(speaker=slots[spoken]['speaker'], dialogue_index=spoken)
            result.append(item)
        if dialogue:
            spoken += 1
    if not result or spoken != len(slots):
        raise ValueError('当前原文单元不完整')
    return result


def model_payload(payload):
    # Future summaries, ending goals and the whole-story user brief cannot leak
    # into this current-action request. The complete payload stays in the adapter.
    validate_timing(payload)
    current_wardrobe = copy.deepcopy(payload.get('wardrobe_state') or {})
    source_units = units(payload)
    if payload.get('source_wardrobe_plan'):
        from . import source_wardrobe_plan
        refs = payload.get('canonical_references') or []
        plan = payload['source_wardrobe_plan']
        current_wardrobe = source_wardrobe_plan.model_state(plan, refs)
        for unit in source_units:
            unit['opening_wardrobe'] = source_wardrobe_plan.model_state(plan, refs)
            if unit['kind'] == 'action':
                plan = source_wardrobe_plan.advance_source(plan, unit['text'])
            unit['ending_wardrobe'] = source_wardrobe_plan.model_state(plan, refs)
    return dict(policy=VERSION, seconds=payload['seconds'], source_units=source_units,
        scene=payload['beat']['scene'], visual_style=payload.get('visual_style', ''),
        scene_facts=copy.deepcopy(payload.get('scene_facts') or {}),
        appearance_facts=copy.deepcopy(payload.get('appearance_facts') or []),
        current_wardrobe=current_wardrobe,
        declared_lighting_states=copy.deepcopy(payload.get('declared_lighting_states') or {}),
        camera_intent=payload.get('camera_intent') or '',
        endpoint_start_state=payload.get('endpoint_start_state') or '',
        acting_actors=[person['name'] for person in payload['story_plan'].get('characters') or [] if person.get('name')],
        photography_options=dict(framing=FRAMING, angle=ANGLE, movement=MOVEMENT, delivery=DELIVERY,
                                 performance=PERFORMANCE))


def action_effort(text):
    """Bounded editing-time estimates: compound handling exceeds a rain insert."""
    families = (
        (r'脱下|脱掉|褪下|褪去|穿上|换上|披上', 2.2),
        (r'包住|包裹|裹进|裹住|包好', 1.8),
        (r'递给|递到|递过去|交给|交到|接过|接住', 1.1),
        (r'拿起|取出|捞起|放入|放进|放在|安放|扣好|扣了|系紧', .9),
        (r'推开|打开|拉开|合上|关闭|关上|撑开', 1.0),
        (r'转身|起身|站起|坐下|走到|来到|登上|升空', .8),
    )
    return min(8.0, .4 + sum(len(re.findall(pattern, text)) * weight for pattern, weight in families))


def validate_timing(payload, source=None):
    """Keep natural speech and pauses fixed before asking for camera choices."""
    source = units(payload) if source is None else source
    budget = payload.get('timing_budget') or {}
    rate = budget.get('speech_characters_per_second', 4.5)
    pause = budget.get('speech_pause_seconds_per_line', .5)
    speech = {unit['id']: max(.3, len(re.sub(r'[^\w\u4e00-\u9fff]', '', unit['text'])) / rate)
              for unit in source if unit['kind'] == 'dialogue'}
    action_floors = {}
    for key, begin, finish, group in _time_windows(source, payload['seconds']):
        seconds = finish - begin
        required = sum(speech.get(unit['id'], 0) for unit in group) + sum(unit['kind'] == 'dialogue' for unit in group) * pause
        actions = sum(unit['kind'] == 'action' for unit in group)
        if required > seconds + .001 or (actions and required >= seconds - .001):
            from .episode_contract import ContractError
            label = ('用户时间块%s（%g–%g秒）的' % (key, begin, finish)) if key else '本段'
            raise ContractError(label + '原对白及自然停顿至少需要%.2f秒，当前仅%g秒；请延长本段或按原话分段，保留完整对白。' %
                (required, seconds), stage='video_prompt', code='dialogue_timing',
                details=dict(required_seconds=required, seconds=seconds, timing_block=key,
                             dialogue_lines=sum(unit['kind'] == 'dialogue' for unit in group)))
        # Remaining action time belongs to this window, never another user block.
        action_units = [unit for unit in group if unit['kind'] == 'action']
        if key or not action_units:
            floor = min(1.0, max(0.0, seconds - required) / actions) if actions else 0.0
            action_floors.update((unit['id'], floor) for unit in action_units)
        else:
            estimates = {unit['id']: action_effort(unit['text']) for unit in action_units}
            total = sum(estimates.values())
            scale = min(1.0, max(0.0, seconds - required) * .95 / total)
            action_floors.update((identity, estimate * scale) for identity, estimate in estimates.items())
    return speech, pause, action_floors


def default_plan(payload):
    source = units(payload)
    if source[0].get('timing_block'):
        groups = [group for _, _, _, group in _time_windows(source, payload['seconds'])]
        return _basic_plan([dict(unit_ids=[u['id'] for u in group], framing='中景' if i else '全景',
            angle='平视', movement='固定机位', weight=2, delivery='自然清楚') for i, group in enumerate(groups)], payload)
    # Up to three source groups, without replaying a source unit to fill time.
    count = min(3, len(source), max(1, int(payload['seconds'] // 3)))
    groups = [source[i * len(source) // count:(i + 1) * len(source) // count] for i in range(count)]
    return _basic_plan([dict(unit_ids=[u['id'] for u in group], framing='中景' if i else '全景',
        angle='平视', movement='固定机位', weight=2, delivery='自然清楚') for i, group in enumerate(groups)], payload)


def compile_actions(plan, payload):
    plan = copy.deepcopy(plan)
    source = units(payload)
    speech_times, pause, action_floors = validate_timing(payload, source)
    if not isinstance(plan, dict) or set(plan) != {'style_description', 'scene_description', 'soundscape', 'shots'}:
        raise ValueError('镜头计划须包括影像风格、场景、现场声音及原文绑定的摄影镜头')
    narrative = '\n'.join(u['text'] for u in source if u['kind'] == 'action')
    shots = plan['shots']
    if not isinstance(shots, list) or not 1 <= len(shots) <= min(8, len(source)):
        raise ValueError('镜头数量超出当前原文范围')
    used = []
    for shot in shots:
        required = {'unit_ids', 'framing', 'angle', 'movement', 'weight', 'delivery', 'visual'}
        if (not isinstance(shot, dict) or not required <= set(shot)
                or set(shot) - required - {'acting'}):
            raise ValueError('每镜头须有原文编号、摄影参数、时长权重、语气和画面细节')
        if not isinstance(shot['unit_ids'], list) or not shot['unit_ids']:
            raise ValueError('每个镜头须绑定原文编号')
        used.extend(shot['unit_ids'])
        for key, options in (('framing', FRAMING), ('angle', ANGLE), ('movement', MOVEMENT), ('delivery', DELIVERY)):
            if shot[key] not in options:
                raise ValueError('摄影选项无效：' + key)
        weight = shot['weight']
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or not 1 <= weight <= 10:
            raise ValueError('镜头时长权重须为1至10')
    expected_ids = [u['id'] for u in source]
    if used != expected_ids:
        raise ValueError('原文编号须按顺序完整使用一次，不能遗漏、重复或提前演下一段。'
                         '正确顺序：%s；当前顺序：%s。请按连续相邻区间分镜，'
                         '相似动作之间有对白时也保留对白原位置。'
                         % ('→'.join(expected_ids), '→'.join(map(str, used))))
    lookup = {u['id']: u for u in source}
    errors = []
    actor_names = tuple(str(person.get('name') or '') for person in payload['story_plan'].get('characters') or [])
    clothing_ownership = _clothing_ownership(payload)
    local_wardrobe = payload.get('source_wardrobe_plan')
    wardrobe_before = {}
    if local_wardrobe:
        from . import source_wardrobe_plan
        projected = local_wardrobe
        for unit in source:
            wardrobe_before[unit['id']] = projected
            if unit['kind'] == 'action':
                projected = source_wardrobe_plan.advance_source(projected, unit['text'])

    def photo(path, value, label, current_source, visible_names=(), **options):
        try:
            if path == ('scene_description',):
                value = _scene_state_text(value, payload.get('scene_facts'), complete=True)
            elif len(path) == 4 and path[:1] == ('shots',):
                value = _scene_state_text(value, payload.get('scene_facts'))
            if len(path) == 4 and path[:1] == ('shots',):
                value = _planned_garment_text(value, local_wardrobe)
                value = _garment_owner_text(value, clothing_ownership, visible_names, current_source,
                                            prop_plan=local_wardrobe)
            return _visual_text(value, label, current_source, actor_names=actor_names,
                lighting_context=payload.get('declared_lighting_states'), scene_facts=payload.get('scene_facts'),
                current_scene=payload['beat']['scene'], lighting_channel=str(path[-1]), **options)
        except ValueError as error:
            errors.append((path, str(error)))
            return ''

    style_text = photo(('style_description',), plan['style_description'], '影像风格', narrative, max_length=400)
    scene_text = photo(('scene_description',), plan['scene_description'], '场景表现', narrative)
    soundscape = photo(('soundscape',), plan['soundscape'], '现场声音', narrative, max_length=350, channel='soundscape')
    for shot_index, shot in enumerate(shots):
        if wardrobe_before:
            local_wardrobe = wardrobe_before[shot['unit_ids'][0]]
            clothing_ownership = _clothing_ownership(dict(payload, source_wardrobe_plan=local_wardrobe))
        if len({lookup[i].get('timing_block') for i in shot['unit_ids']}) > 1:
            raise ValueError('镜头不能跨越用户指定的时间块；请在同一timing_block内分组')
        visual = shot['visual']
        if not isinstance(visual, dict) or set(visual) - set(VISUAL_FIELDS):
            raise ValueError('摄影细节只能包含构图、光影、材质、受限表情和焦点表现')
        local_source = '\n'.join(lookup[i]['text'] for i in shot['unit_ids'] if lookup[i]['kind'] == 'action')
        visible_names = {name for name in actor_names if name and name in local_source}
        visible_names.update(lookup[i].get('speaker') for i in shot['unit_ids'] if lookup[i]['kind'] == 'dialogue')
        for name in tuple(visible_names):
            if name and re.search(re.escape(name) + r'[^，。；\n]{0,18}画外|画外[^，。；\n]{0,18}' + re.escape(name), local_source):
                visible_names.discard(name)
        try:
            shot['acting'] = staging.validate_staging(shot.get('acting', []),
                [lookup[i] for i in shot['unit_ids']], actor_names,
                event_patterns=_EVENTS + (r'开灯|关灯|熄灯|点亮|熄灭|亮起',
                                         r'驶离|驶出|驶入|开走|开车|停车|发动|启动|熄火|起飞|降落'))
            for entry in shot['acting']:
                for phase in ('preparation', 'execution', 'reaction'):
                    entry[phase] = _garment_owner_text(entry[phase], clothing_ownership,
                                                       (entry['actor'],), local_source)
        except ValueError as error:
            errors.append((('shots', shot_index, 'acting'), str(error)))
            shot['acting'] = []
        for key in VISUAL_FIELDS:
            value = visual.get(key, '')
            if key == 'camera_detail' and shot['movement'] != '固定机位' and isinstance(value, str):
                # The selected main movement owns the camera position. Keep
                # useful focus/composition clauses while removing a stray
                # camera-only "fixed" clause that contradicts that movement.
                value = re.sub(r'(?:(?:镜头|摄影机|机位)(?:始终|保持)?(?:固定|静止)|固定机位)'
                               r'[^，。；\n]*[，。；\n]?', '', value).strip()
            path = ('shots', shot_index, 'visual', key)
            if key == 'performance':
                if value not in PERFORMANCE:
                    errors.append((path, '片段“%s”不是给定微表演状态；缺少依据时留空，剧情动作由原文写入' % str(value)[:110]))
                    value = ''
                visual[key] = value
            else:
                visual[key] = photo(path, value, '镜头' + key, local_source, visible_names=visible_names,
                                    max_length=350, allow_empty=True,
                                    channel='camera' if key == 'camera_detail' else 'visual')
                if (key == 'camera_detail' and shot['movement'] == '固定机位'
                        and re.search(r'(?:镜头|摄影机|机位)[^，。；\n]{0,14}'
                                      r'(?:推近|推进|拉远|后撤|横移|跟拍|环绕|上摇|下摇|升起|下降|转动|旋转)',
                                      visual[key])):
                    errors.append((path, '主运镜为固定机位，camera_detail应保持取景位置；'
                                         '焦点和景深可变化，不能同时另写摄影机移动'))
    if errors:
        raise PhotographyError(errors)
    slots = payload.get('dialogue_slots') or []
    refs = payload.get('canonical_references') or []
    people = payload['story_plan'].get('characters') or []
    by_id = {r['character_id']: r['subject'] for r in refs if r['role'] == 'character'}
    names = {p['name']: by_id[p['id']] for p in people if p['id'] in by_id}

    def bind(text):
        if not names:
            return text
        return re.sub('|'.join(re.escape(n) for n in sorted(names, key=len, reverse=True)),
            lambda m: '<Subject %d>' % names[m.group()], text)

    def micro_performance(shot, group):
        value = shot['visual']['performance']
        if not value or shot.get('acting'):
            return ''
        # Do not animate a person in a landscape merely because a cast card
        # exists. Multiple named subjects have no inferred shared expression.
        local = '\n'.join(u['text'] for u in group)
        candidates = {name for name in names if name in local}
        candidates.update(u.get('speaker') for u in group if u.get('speaker') in names)
        if not candidates and len(names) == 1:
            candidates = set(names)
        if len(candidates) != 1:
            return ''
        name = next(iter(candidates))
        spoken = any(u.get('speaker') == name for u in group)
        visible = name in local or bool(re.search(r'(?:^|[，。；\n\s])(?:她|他)(?:的|把|将|在|向|正|仍|轻|缓|抬|伸|握|站|坐|看|说|靠)', local))
        if not (visible or spoken):
            return ''
        core = value.rstrip('。').replace('神情', '').replace('嘴角带着', '')
        if core in narrative or value.rstrip('。') in narrative:
            return ''
        if value == '手部力度平稳。' and not re.search(r'手|指|握|扶|拿|抓|持|把手', local):
            return ''
        return bind(name) + value

    def duration(unit):
        if unit['kind'] == 'dialogue':
            return speech_times[unit['id']] + pause
        return action_floors[unit['id']]

    floors = [sum(duration(lookup[i]) for i in s['unit_ids']) for s in shots]
    seconds = float(payload['seconds'])
    shot_times = []
    for key, begin, finish, group in _time_windows(source, seconds):
        indices = [i for i, shot in enumerate(shots) if lookup[shot['unit_ids'][0]].get('timing_block') == key]
        extra = max(0.0, finish - begin - sum(floors[i] for i in indices))
        total_weight = sum(shots[i]['weight'] for i in indices)
        clock = begin
        for offset, i in enumerate(indices):
            end = finish if offset == len(indices) - 1 else round(clock + floors[i] + extra * shots[i]['weight'] / total_weight, 3)
            shot_times.append((clock, end))
            clock = end
    texts, timeline, dialogue, audit = [], [], [], []
    for index, (shot, (clock, end)) in enumerate(zip(shots, shot_times)):
        group = [lookup[i] for i in shot['unit_ids']]
        sum_duration = sum(duration(u) for u in group)
        cursor, actions = clock, []
        for u in group:
            unit_end = end if u is group[-1] else cursor + (end - clock) * duration(u) / sum_duration
            acting = [item for item in shot.get('acting', []) if item['unit_id'] == u['id']]

            def phase_text(phase):
                return ' '.join(bind(item['actor']) + item[phase] for item in acting if item[phase])

            external_cue = u['kind'] == 'action' and bool(
                re.search(r'听到|听见|看到|看见|发现|注意到|响起|传来', u['text']))
            if phase_text('preparation') and not external_cue:
                actions.append(phase_text('preparation'))
            if u['kind'] == 'dialogue':
                slot = slots[u['dialogue_index']]
                dialogue.append(dict(speaker=slot['speaker'], text=slot['text'], start=round(cursor, 3),
                    end=round(cursor + speech_times[u['id']], 3),
                    delivery='语气自然，吐字清晰。' if shot['delivery'] == '自然清楚' else '语气' + shot['delivery'] + '。'))
            else:
                actions.append(bind(u['text']))
            if phase_text('preparation') and external_cue:
                # A reaction to a knock/discovery belongs after its source cue,
                # never before the audience has heard or seen that stimulus.
                actions.append(phase_text('preparation'))
            for phase in ('execution', 'reaction'):
                if phase_text(phase):
                    actions.append(phase_text(phase))
            cursor = unit_end
        action = ' '.join(actions)
        if index == 0 and payload.get('endpoint_start_state'):
            action = '起镜状态：' + bind(payload['endpoint_start_state']) + '。' + action
        timecode = '' if index == 0 else 'At %02d:%06.3f, ' % (int(clock // 60), clock % 60)
        visual = ' '.join(value for value in (
            micro_performance(shot, group) if key == 'performance' else bind(shot['visual'][key])
            for key in VISUAL_FIELDS) if value)
        opening = bind(style_text + ' ' + scene_text) + ' ' if index == 0 else ''
        texts.append('[Shot %d] %s%s%s，%s，%s。%s %s' % (index + 1, timecode, opening,
            shot['framing'], shot['angle'], shot['movement'], action, visual))
        timeline_action = action or '、'.join(bind(u['speaker']) for u in group if u['kind'] == 'dialogue') + '说话。'
        timeline.append(dict(start=clock, end=end, action=timeline_action, visible_result=timeline_action))
        audit.append(dict(shot=index + 1, unit_ids=list(shot['unit_ids']), start=clock, end=end,
            visual=copy.deepcopy(shot['visual']), acting=copy.deepcopy(shot.get('acting', []))))
        clock = end
    # Legacy transport requires three timing phases in long clips; phases are
    # timing metadata only, and do not duplicate a shot/action in the video text.
    while seconds >= 12 and len(timeline) < 3:
        idx = max(range(len(timeline)), key=lambda i: timeline[i]['end'] - timeline[i]['start'])
        first = timeline[idx]
        mid = round((first['start'] + first['end']) / 2, 3)
        timeline[idx:idx + 1] = [dict(first, end=mid), dict(first, start=mid)]
    return dict(brief=str(payload['beat']['scene']).rstrip('。') + '。',
        integrated_multimodal_description='\n\n'.join(texts),
        overall_soundscape=soundscape,
        non_diegetic_music='N/A', expected_end_state='停在本段最后一个原文动作或原对白完成的位置。',
        timeline=timeline, dialogue=dialogue, continuity_notes=[],
        source_shot_audit=dict(policy=VERSION, visual_policy='source-owned-visual-v2', acting_policy='source-actor-staging-v1', units=source, shots=audit,
            style_description=style_text, scene_description=scene_text, soundscape=soundscape))
