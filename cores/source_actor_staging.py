"""Bounded actor performance alongside source-owned story events.

These pure checks accept body/face micro actions, not a second story writer.
Source units and dialogue are never edited; the caller inserts each original
event once and places the accepted phases around it.
"""
import re

from .image_prompt_content import assert_positive_description


PHASES = ('preparation', 'execution', 'reaction')
_FIELDS = {'unit_id', 'actor', *PHASES}
ACTING_SCHEMA = dict(type='array', maxItems=3, items=dict(
    type='object', additionalProperties=False,
    required=['unit_id', 'actor', *PHASES], properties=dict(
        unit_id=dict(type='string', minLength=1),
        actor=dict(type='string', minLength=1),
        **{phase: dict(type='string', maxLength=180) for phase in PHASES})))

ACTING_INSTRUCTION = '''
acting是每镜可选的表演细节数组，最多三项；常规空镜采用空数组。
每项包含unit_id、actor、preparation、execution、reaction。unit_id属于本镜，actor从acting_actors选择，并在该原文单元中具名出镜或明确发声。
preparation写原事件发生前的目光、眉眼、呼吸或短暂停顿；execution写原事件进行中的手指、肩部、重心等细小变化；reaction写事件后的表情或呼吸变化。每项只展开有依据的阶段，其余阶段填空字符串，每阶段至多180字。
三阶段是原事件内部的局部表演细节，不是把原动作重演三次。原文已有的伸手、低头、抬头、转头、转身、走动和身体姿态由原文通道呈现；换词重说同一动作或再摆一次相同姿势仍属重复。表演补充沿用前后明确的视线与姿态，视线变化服务于当前事件，未有剧情目标时不补游离的看向、扫视或转向。
结合景别、情绪和可用时长，每项选择一至两个有用的细节；三阶段按需要填写。自然表演随事件变化，避免每镜重复同一套眨眼、抿唇或呼吸动作。程序按preparation、原动作一次、execution、reaction排列，原动作由单独通道承载。
各阶段写具体、正面、可见的短句，例如“目光短暂下移，眉头轻蹙”“指尖轻轻收拢，重心略向前移”“呼吸逐渐放缓，嘴角稍稍放松”。阶段文字直接从表演开始，程序另加对应演员标记。
原文已经指定的视线目标保持一致，微表演落实眼神聚焦、眼睑张力与眉眼变化；视线目标的转移及收回由原文动作表达。衣物和物件操作完整保留在原文主干，阶段只补局部身体反应。
原文的剧情核心动作和对白由程序按顺序写入一次。这里描写身体细节，原事件、物件接触与持有变化、穿脱、出入地点、攻击及开关灯等事件仍由原文单元承载。人物身份、衣着、持物与场景沿用当前原文事实。
同一actor和unit_id只编写一项。多人原文按各自具名人物分配；含糊的多人代词保持空数组。摄影、焦点、光色和运镜写入摄影字段；逐字对白由dialogue_slots传输。
合法结构示例：
"acting":[{"unit_id":"U001","actor":"陈默","preparation":"眉间轻收，呼吸短暂停顿。","execution":"指腹轻微发力，手腕短暂停顿。","reaction":"眉间渐渐放松。"}]
示例只说明字段与局部表演写法，人物、单元和细节按当前事件选择。修复时删除被指出的剧情操作或物件操作，保留有依据的身体局部变化；不把原动作换词后再补回阶段。
'''

# Unlike photographic fields, this channel permits small changes of gaze,
# fingers, breathing and weight. Changes to story objects/location stay owned
# by the original source, even when the same event appears there explicitly.
_CORE_EVENTS = (
    r'打开|拉开|推开|掀开|开(?:车)?门|开启|关上|关掉|关闭|合上|拉上|闭合',
    r'脱下|脱掉|脱去|换上|换成|穿上|戴上|摘下|脱衣|更衣|换衣',
    r'拿起|拿出|取出|抽出|掏出|拾起|捡起|接过|放下|放入|放进|递给|递出|交给|扔下|丢下|抛出',
    r'走进|走入|进入|冲进|跑进|走出|离开|跑出|冲出|逃出|跨入|跨出|迈入|迈出',
    r'走到|走向|走近|后退|退后|迈步|半步|一步|坐下|起身|站起|蹲下|躺下',
    r'伸出|伸向|伸手|伸臂|抬手|举手|抬臂|举臂|低头|抬头|侧头|转头|扭头|回头|'
    r'转身|转体|俯身|弯腰|弯下|抬脚|举脚',
    r'(?:抬起|举起|举高|抬高)(?:(?:了|着|右|左|双|两|一只|一条|自己的|她的|他的)){0,3}(?:手|臂|胳膊)|'
    r'(?:右手|左手|双手|两手|手臂|右臂|左臂|双臂|胳膊)[^，。；\n]{0,8}(?:抬起|举起|举高|抬高)',
    r'击打|刺入|砍向|开枪|射击|刺杀|踢向|亲吻|接吻|拥抱|抱起',
    r'开灯|关灯|熄灯|点亮|熄灭|亮起|点燃|吹灭|断电|通电',
    r'握住|握着|握紧|抓住|抓紧|持有|手持|手握|拿着|拎着|捧着|抱着|扶住|扶着|'
    r'松开|松手|撒手|敲击|轻敲|拍打|触碰|按住|压住|压着|按压|抵住|撑住|捏住',
    r'(?:开|关|拿|放|递|脱|穿|戴|摘|推|拉|捡|拾)(?:了|过|着)(?:一下|一把|一件)?'
    r'(?:门|窗|灯|书|伞|杯|刀|剑|枪|钥匙|外套|衣|帽)',
    r'\b(?:opens?|opened|opening|closes?|closed|closing|grabs?|grabbing|takes?|taking|'
    r'puts?|putting|enters?|entering|leaves?|leaving|attacks?|attacking|shoots?|shooting)\b',
)
CORE_EVENT_PATTERNS = _CORE_EVENTS
_NEW_PERSON = re.compile(
    r'(?:一名|一位|一个|另一名|另一位|另一个|其他|新来的).{0,10}'
    r'(?:陌生|男子|男人|男生|女子|女人|女生|男孩|女孩|老人|士兵|行人|人物)|'
    r'有人|另有人|新增人物|新角色|凭空|突然出现', re.I)
_CAMERA_META = re.compile(
    r'镜头|摄影|机位|构图|景别|焦点|景深|推近|拉远|切镜|转场|'
    r'画面(?!(?:左侧|右侧|左方|右方|上方|下方))|'
    r'原文|剧情|程序|后台|规则|参考图|输出|格式|版本|时长|'
    r'\b(?:unit_ids?|source_units?|acting|preparation|execution|reaction|json|camera|focus|shot)\b|'
    r'[{}<>]|\[\s*Shot', re.I)
_SPEECH = re.compile(r'[“”「」『』‘’"\']|说出|说道|说着|说了|开口|问道|回答|答道|喊出|喊道|叫道|念出|哼唱|唱出|吐出')
_WARDROBE_OR_PROP = re.compile(
    r'服装|衣装|衣服|穿戴|穿着|外套|大衣|上衣|衬衫|长袍|披风|披肩|裙|裤|鞋|靴|帽|'
    r'袖口|衣角|领口|腰带|腰封|护肩|项链|戒指|手镯|文件袋|背包|手提包|换装|'
    r'(?:夹|抱|拎|捧|提|托|背|挎|握|拿|持|扶|扛|捏|按|抓|扣|挂)(?:着|住|紧|持|在|于)')
_LIGHT_STATE = re.compile(r'灯|烛|火光|窗光|光源|照明|灯光|亮度|日光|月光|阴影|光线|照亮')
_OBJECT_OPERATION = re.compile(
    r'(?:把|将)[^，。；\n]{1,22}|'
    r'(?:转动|拨动|拧动|旋动|凑近|贴近|移至|移到|挪到|推向|拉向|压住|托稳|递到)[^，。；\s]{1,14}|'
    r'(?:目光|视线)[^，。；\n]{0,8}(?:移向|转向|投向|扫过|扫向|跟随|收回|移开|回到|从)|'
    r'(?:收回|移开)(?:目光|视线)')
_MICRO = re.compile(
    r'目光|视线|眼神|眼睑|眼皮|眼角|眼眶|眼睛|双眼|眨.{0,3}眼|眉|唇|嘴角|面颊|'
    r'鼻翼|表情|神情|脸上|呼吸|气息|呼气|吸气|屏息|吞咽|肩|重心|手指|指尖|指腹|'
    r'手掌|掌心|手背|手腕|手部|肘|下颌|下巴|喉结|停顿')
_PRONOUN = re.compile(r'(?:^|[，。；：\s])(?:她|他)(?=[的把将在向正仍轻缓抬伸握站坐看说靠停听等呼吸眉眼手肩]|[，。；\s]|$)')
_BODY_CLOSURE = re.compile(
    r'(?:眼睑|眼皮|双眼|眼睛|嘴唇|唇瓣)(?:(?:轻轻|缓缓|短暂|微微|略微|逐渐|稍稍|自然|地)){0,3}(?:闭合|合上)|'
    r'(?:闭合|合上)(?:了)?(?:双眼|眼睛|嘴唇)')
_LIMB_TRAVEL = re.compile(
    r'(?:手臂|胳膊|双臂|左臂|右臂|双手|左手|右手)[^，。；\n]{0,8}'
    r'(?:前伸|伸直|收回|抬高|抬起|落下|举起|伸出|张开)')


def _copied_source_clause(value, source, actor):
    """An inflected or uncommon source verb must not gain a second channel."""
    compact = lambda text: re.sub(r'[\s，,。；;：:！？!?、]', '', text.replace(actor, ''))
    original = compact(source)
    for clause in re.split(r'[，,。；;！？!?\n]', value):
        phrase = compact(clause)
        if len(phrase) >= 6 and phrase in original:
            return clause.strip()
    return ''


def _fail(message):
    raise ValueError('表演补充：' + message)


def _named(text, names):
    if not names:
        return set()
    pattern = '|'.join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    return set(re.findall(pattern, text))


def _unit_actors(unit, names):
    text = unit.get('text') or ''
    present = _named(text, names)
    speaker = unit.get('speaker')
    if speaker in names:
        present.add(speaker)
    dialogue = unit.get('dialogue')
    if isinstance(dialogue, dict) and dialogue.get('speaker') in names:
        present.add(dialogue['speaker'])
    if unit.get('offscreen') is True or unit.get('onscreen') is False:
        return set()
    excluded = set()
    for name in tuple(present):
        if re.search(re.escape(name) + r'[^，。；\n]{0,16}(?:画外音|画外声音|画外说话)|'
                     r'(?:画外音|画外声音)[^，。；\n]{0,16}' + re.escape(name), text):
            present.discard(name)
            excluded.add(name)
    if not present and not excluded and len(names) == 1 and _PRONOUN.search(text):
        present = set(names)
    return present


def validate_staging(entries, local_units, actor_names, *, event_patterns=()):
    """Validate one shot's performances; return independent, source-safe dicts.

    The only permitted text normalization removes a redundant leading actor
    name once. All remaining characters and all source-unit data are retained.
    These lexical guards are deliberately bounded, not a semantic-proof claim.
    """
    if entries is None:
        return []
    if not isinstance(entries, list) or len(entries) > 3:
        _fail('acting须为最多三项的数组')
    names = {name for name in actor_names if isinstance(name, str) and name.strip()}
    units = {unit.get('id'): unit for unit in local_units if isinstance(unit, dict)}
    patterns = tuple(_CORE_EVENTS) + tuple(event_patterns)
    accepted, used = [], set()
    for index, entry in enumerate(entries):
        label = 'acting[%d]' % index
        if not isinstance(entry, dict) or set(entry) != _FIELDS:
            _fail(label + '须仅包括unit_id、actor及三个表演阶段')
        unit_id, actor = entry['unit_id'], entry['actor']
        if not isinstance(unit_id, str) or unit_id not in units:
            _fail(label + '引用了本镜头以外的原文单元')
        if not isinstance(actor, str) or actor not in names:
            _fail(label + '演员不是本段已知人物')
        if actor not in _unit_actors(units[unit_id], names):
            _fail(label + '演员没有在该原文单元中明确出镜或发声，含糊代词不能指定多人表演')
        if (unit_id, actor) in used:
            _fail(label + '重复同一人物的同一原文单元')
        result = dict(unit_id=unit_id, actor=actor)
        for phase in PHASES:
            value = entry[phase]
            if not isinstance(value, str) or len(value) > 180:
                _fail(label + '.' + phase + '须为180字以内的文字')
            if value.startswith(actor):
                value = value[len(actor):].lstrip('，,:： \t')
            if value.strip():
                assert_positive_description(value, label=label + '.' + phase)
                copied = _copied_source_clause(value, units[unit_id].get('text') or '', actor)
                if copied:
                    _fail(label + '.' + phase + '复述原文片段“%s”；原动作只执行一次，表演补充只保留新的局部表情或力度' % copied)
                movement = _LIMB_TRAVEL.search(value) or re.search(r'声带|内心|血液|心脏', value)
                if movement:
                    _fail(label + '.' + phase + '片段“%s”涉及整体肢体位移或不可见的内部状态；动作沿用原文，补充只写可见的局部表演' % movement.group())
                if _named(value, names - {actor}) or _NEW_PERSON.search(value):
                    _fail(label + '.' + phase + '包含其他人物或新角色')
                if _CAMERA_META.search(value) or _SPEECH.search(value):
                    _fail(label + '.' + phase + '包含摄影、元指令、引用标签或对白')
                match = _WARDROBE_OR_PROP.search(value) or _LIGHT_STATE.search(value)
                if match:
                    _fail(label + '.' + phase + '片段“%s”涉及衣着、持物接触关系或光源状态；删除该片段，仅保留身体局部表演，原状态沿用原文及对应事实通道' % match.group())
                match = _OBJECT_OPERATION.search(value)
                if match:
                    _fail(label + '.' + phase + '片段“%s”包含剧情或物件操作；删除该操作，保留指腹、手腕、眉眼或呼吸的局部变化，原动作由原文通道写入一次' % match.group())
                if not _MICRO.search(value):
                    _fail(label + '.' + phase + '须为具体的目光、表情、呼吸或身体微动作')
            result[phase] = value
        combined = ''.join(result[phase] for phase in PHASES)
        if not combined.strip():
            _fail(label + '至少填写一个可见的表演阶段')
        # Closing an eyelid is a micro expression, not a door/object event.
        event_text = _BODY_CLOSURE.sub('', combined)
        for pattern in patterns:
            match = (pattern.search(event_text) if isinstance(pattern, re.Pattern)
                     else re.search(pattern, event_text, re.I))
            if match:
                _fail(label + '片段“%s”包含剧情核心动作、物件持有或光源变化；删除该操作，只保留身体局部表演，原文事件由程序写入一次' % match.group())
        used.add((unit_id, actor))
        accepted.append(result)
    return accepted
