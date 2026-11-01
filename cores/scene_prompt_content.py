"""Small, source-bound content checks for empty scene reference captions.

The checks reject a few demonstrated unsupported additions. They do not rewrite
captions, infer world history, or replace the source card's stated condition.
"""
import re
from .scene_condition import (_negated, _WEATHER, unsupported_opening_residue,
                              missing_opening_residue)


def _affirmed(pattern, text):
    return [m for m in pattern.finditer(text) if not _negated(text, m.start())]


class ScenePromptContentError(ValueError):
    code = 'scene_prompt_content'

    def __init__(self, issues):
        if isinstance(issues, str):
            issues = [issues]
        self.issues = tuple(issues)
        super().__init__('场景提示词内容：场景事实未支持' + '；'.join(self.issues) +
                         '。按场景卡的维护状况与固有陈设重写，保留已确认的结构和光照。')


_EXCLUDED_KEYS = {'design', 'plays', 'image_prompt', 'prompt_override',
                  'style', '画风', '画风说明', '项目视觉设定'}
_CLAUSE = re.compile(r'[，,。；;、\n]+')
_NEGATIVE_SOURCE = re.compile(r'不要|不许|不得|禁止|避免|无需|不添加|不需要|没有|未见|并无|毫无|不带|无明显')
_GENERAL_DECAY = re.compile(r'废弃|荒废|年久失修|废墟|破败|残破|严重损毁|毁坏严重|坍塌|残垣')
_INTERIOR = re.compile(r'室内|走廊|长廊|会客室|客厅|卧室|书房|病房|大堂|大厅|大殿|议事厅|教室|房间|殿内|馆内|宅内')
_NATURAL = re.compile(r'森林|树林|山谷|山道|溪流|溪边|河畔|湖畔|瀑布|湿地|花园|庭院|林中|山林|公园|岩洞|洞穴|苔原')
_SURFACES = (
    ('wall', re.compile(r'墙面|墙壁|墙皮|墙漆|石壁|墙缝')),
    ('floor', re.compile(r'地面|地板|地砖|石板|石砖|砖缝|地缝|台阶|石阶')),
    ('door', re.compile(r'木门|门板|门框|大门|门扇')),
    ('window', re.compile(r'窗框|窗台|窗格')),
    ('metal', re.compile(r'金属|铜制|铁制|钢制|铁艺|铜器')),
    ('textile', re.compile(r'挂毯|地毯|窗帘|织物|布料|沙发')),
    ('wood', re.compile(r'木头|木材|木质|木制|木梁|木柱')),
)
_DAMAGE = (
    ('磨损旧化', re.compile(r'岁月痕迹|岁月(?:的)?侵蚀|磨损|磨痕|磨蚀|风化痕迹')),
    ('褪色', re.compile(r'褪色|退色')),
    ('裂损', re.compile(r'漆面[^，。；\n]{0,5}(?:裂|脱落)|裂纹|裂缝|开裂|龟裂|破损')),
    ('剥落', re.compile(r'剥落|墙皮[^，。；\n]{0,5}脱落')),
    ('锈蚀', re.compile(r'锈迹|生锈|锈蚀|锈斑|氧化痕迹|氧化层')),
    ('积灰污损', re.compile(
        r'积灰|积尘|落灰|污渍|污迹|脏污|泥垢|灰垢|'
        r'(?:地面|地板|墙面|墙壁|窗台|桌面|门框|石板|石砖|缝隙)[^，。；\n]{0,16}(?:灰尘|尘土)|'
        r'(?:覆盖|堆积|积满|落着|散落)[^，。；\n]{0,8}(?:灰尘|尘土)')),
)
_MOSS = re.compile(r'(?:地砖|石板|石砖|墙缝|石缝|地缝|砖缝|缝隙|石壁|墙面)[^，。；\n]{0,16}(?:青苔|苔藓)|'
                   r'(?:青苔|苔藓)[^，。；\n]{0,10}(?:石缝|砖缝|地面|石板|墙)')
_STORY_RESIDUE = re.compile(
    r'(?:刚|刚刚|刚才)(?:被)?(?:脱下|脱掉|褪下|放下|搁下|喝过|拿过|丢下|扔下)|'
    r'(?:人物|某人|[他她]们?|主人|访客|男子|女子|男人|女人)[^，。；\n]{0,8}拿过|'
    r'(?:动作|剧情)(?:结束|完成)后(?:所)?留下')
_VISIBLE_PERSON = re.compile(
    r'(?:一|两|二|三|四|五|[1-5])(?:位|名|个)[^，。；\n]{0,35}'
    r'(?:人物|男子|女子|女性|男性|男人|女人|青年|老人|访客|居民|女仆|侍女|守卫)|'
    r'(?:人物|男子|女子|女性|男性|男人|女人|老人|青年|访客|居民|主人|女仆|侍女|守卫)'
    r'[^，。；\n]{0,14}(?:坐在|站在|站立|坐着|站着|行走|走在|倚靠|凝视|注视)|'
    r'(?:人物|人形|男子|女子|女性|男性|男人|女人)(?:的)?(?:肖像|剪影|身影|倒影)|人影|人形投影')
_ART_SUBJECT = re.compile(
    r'(?:油画|壁画|画作|挂画|画像)(?:上|中|里|内|所描绘|描绘|绘有|画着)'
    r'[^，。；\n]{0,30}(?:人物|男子|女子|人形|人影|肖像|剪影)|'
    r'(?:人像|人物|男子|女子|女性|男性)[^，。；\n]{0,5}(?:雕像|雕塑|石像|铜像|浮雕)')
_META = re.compile(r'具体形象[^，。；\n]{0,8}待后续卡片定义|待后续(?:卡片)?定义|'
                   r'后续(?:视频|镜头|阶段)[^，。；\n]{0,12}(?:安排|定义|处理)|此处表现为')


def _source_strings(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for key, child in value.items() if key not in _EXCLUDED_KEYS
                and not str(key).endswith(('_hash', '_sig')) for text in _source_strings(child)]
    if isinstance(value, (list, tuple)):
        return [text for child in value for text in _source_strings(child)]
    return []


def _source_clauses(facts):
    # A user request to avoid a condition cannot authorize that condition.
    return [clause.strip() for text in _source_strings(facts)
            for clause in _CLAUSE.split(text) if clause.strip()
            and not _NEGATIVE_SOURCE.search(clause)]


def _surface_scope(clause):
    return {name for name, pattern in _SURFACES if pattern.search(clause)}


def _supported_damage(clause, source, pattern):
    scope = _surface_scope(clause)
    for fact in source:
        if pattern.search(fact):
            fact_scope = _surface_scope(fact)
            if not scope or not fact_scope or scope & fact_scope:
                return True
    return False


_CONDITION_DAMAGE = (
    ('wear', '磨损旧化', re.compile(_DAMAGE[0][1].pattern + r'|旧损')),
    ('fade', '褪色', re.compile(_DAMAGE[1][1].pattern + r'|掉色')),
    ('crack', '裂损', re.compile(_DAMAGE[2][1].pattern + r'|破碎')),
    ('peeling', '剥落', _DAMAGE[3][1]),
    ('rust', '锈蚀', _DAMAGE[4][1]),
    ('dust', '积灰', re.compile(r'积灰|积尘|落灰|灰垢|蒙尘|(?:表面|地面|地板|墙面|墙壁|窗台|桌面|门框|石板|石砖|缝隙)[^，。；\n]{0,16}(?:灰尘|尘土)|(?:覆盖|堆积|积满|落着|散落)[^，。；\n]{0,8}(?:灰尘|尘土)')),
    ('stain', '污损', re.compile(r'污渍|污迹|脏污|泥垢|污垢')),
    ('scorch', '烧焦痕迹', re.compile(r'烧焦|焦黑|焦痕|炭化|碳化|灼烧痕迹')),
    ('soot', '烟熏污损', re.compile(r'烟熏痕迹|烟熏黑|烟灰(?!缸)|烟渍|烟尘附着')),
    ('moss', '石材缝隙青苔', re.compile(r'青苔|苔藓')),
)
# These are physical parts, rather than a vocabulary of every possible caption.
# Keeping door frames separate from door panels prevents a local effect from
# becoming permission to damage the entire room.
_CONDITION_PARTS = (
    ('wall', r'墙面|墙壁|墙体|石壁|墙皮|墙漆|墙缝|外墙|内墙|墙纸|墙'),
    ('floor', r'地面|地板|地砖|石板|石砖|砖缝|地缝'),
    ('step', r'台阶|石阶|楼梯'),
    ('doorframe', r'门框'),
    ('door', r'木门|铁门|大门|门扇|门板|门'),
    ('windowframe', r'窗框'),
    ('window', r'窗台|窗格|玻璃窗|窗户'),
    ('pillar', r'柱子|立柱|石柱|木柱|支柱|柱身|柱体|柱'),
    ('ceiling', r'天花板|吊顶|屋顶|穹顶|天花|屋架'),
    ('beam', r'木梁|横梁|梁架'),
    ('railing', r'栏杆'),
    ('tapestry', r'挂毯'),
    ('carpet', r'地毯'),
    ('curtain', r'窗帘'),
    ('sofa', r'沙发'),
    ('chair', r'扶手椅|椅子|座椅|木椅'),
    ('table', r'桌面|桌子|茶几|书桌|餐桌'),
    ('shelf', r'书架|书柜|柜子'),
    ('trolley', r'推车'),
    ('props', r'陈设|物品|东西|家具|摆件|药瓶|花瓶|烛台|杯子|杯碟|玻璃杯|酒杯'),
    ('metal', r'金属|铜器|铁艺'),
    ('wood', r'木材|木头|木质|木制'),
    ('textile', r'织物|布料'),
)
_PART_PATTERN = re.compile('|'.join('(?P<%s>%s)' % item for item in _CONDITION_PARTS))
_PART_POSITION = re.compile(r'(?:东北|西北|东南|西南|东|西|南|北|左|右)(?:侧|边|面)$')
_BUILDING_PARTS = {'wall', 'floor', 'step', 'doorframe', 'door', 'windowframe', 'window', 'pillar', 'ceiling', 'beam', 'railing'}
_COLLAPSE = re.compile(r'坍塌|倒塌|塌陷|坍毁|垮塌|废墟|残垣|断壁|断柱|(?:柱子|立柱|屋顶|穹顶|墙体)[^，。；\n]{0,8}(?:倒下|崩塌|断裂)')
_STRUCTURAL_DAMAGE = re.compile(r'破败|残破|严重损毁|毁坏严重|建筑破损|墙体破损')
_DISORDER = re.compile(r'杂乱|凌乱|散乱|乱七八糟|杂物堆积|杂物成堆|横七竖八|物品乱堆')
_MESSY_ARRANGEMENT = re.compile(_DISORDER.pattern + r'|错位(?:摆放|排列)|散置|散放|散落|随意摆放')
_ABANDONED = re.compile(r'废弃|荒废|年久失修')
_ACTIVE_FIRE = re.compile(r'熊熊(?:大火|燃烧|烈火)|大火(?:燃烧|吞噬|蔓延)|陷入火海|烈焰吞噬|(?:建筑|大厅|宫殿|柱子|墙面|墙壁|屋顶)[^，。；\n]{0,10}(?:正在燃烧|燃起大火|燃烧着)')
_MAIN_LIGHT_SPACE = re.compile(r'整体|整个|大厅|室内|房间|主空间|中央(?:通道|区域|空间)|环境|光线')
_LOCAL_LIGHT = re.compile(r'一(?:盏|点|束|小块|小片|缕)|几点|少量|少许|局部|高光|反光|角落|边缘|火焰|炉火|烛火|灯芯')


def _lighting_conflict(clause, lighting):
    if lighting == 'dark':
        for match in re.finditer(r'灯火通明|充满阳光|明亮(?:的)?(?:自然光|阳光|日光)|明亮|亮堂', clause):
            before = clause[max(0, match.start() - 18):match.start()]
            after = clause[match.end():match.end() + 10]
            # A small source or reflection can be bright inside a dark room.
            # A bright primary room or broad daylight is a different claim.
            if match.group() in {'灯火通明', '充满阳光'}:
                return True
            if _LOCAL_LIGHT.search(before) or re.match(r'(?:的)?(?:高光|反光)', after):
                continue
            if re.search(r'自然光|阳光|日光', match.group()):
                return True
            if _MAIN_LIGHT_SPACE.search(before) or _MAIN_LIGHT_SPACE.search(after):
                return True
    elif lighting == 'bright':
        for match in re.finditer(r'伸手不见五指|漆黑|黑暗|昏暗|幽暗', clause):
            before = clause[max(0, match.start() - 18):match.start()]
            after = clause[match.end():match.end() + 10]
            if re.search(r'局部|角落|一角|阴影|边缘|暗处', before):
                continue
            if match.group() == '伸手不见五指' or _MAIN_LIGHT_SPACE.search(before) or _MAIN_LIGHT_SPACE.search(after):
                return True
    return False


def _part_mentions(text):
    result = []
    for match in _PART_PATTERN.finditer(text):
        position = _PART_POSITION.search(text[max(0, match.start() - 4):match.start()])
        start = match.start() - len(position.group(0)) if position else match.start()
        result.append((start, match.end(), match.lastgroup,
                       position.group(0)[:-1] if position else ''))
    # A material adjective directly attached to a named object is not a second
    # object, and cannot expand a local wall effect to all wooden furniture.
    return [part for index, part in enumerate(result)
            if part[2] not in {'metal', 'wood', 'textile'} or index + 1 == len(result)
            or result[index + 1][2] in {'metal', 'wood', 'textile'}
            or text[part[1]:result[index + 1][0]] not in {'', '的'}]


def _effect_subjects(clause, match):
    """Find the nearest subject and any explicitly enumerated sibling parts."""
    mentions = _part_mentions(clause)
    inside = [part for part in mentions if part[0] < match.end() and part[1] > match.start()]
    if inside:
        return inside
    before = [part for part in mentions if part[1] <= match.start()]
    after = [part for part in mentions if part[0] >= match.end()]
    if before:
        selected = [before[-1]]
        for part in reversed(before[:-1]):
            if not re.fullmatch(r'\s*(?:、|和|与|及|以及)\s*', clause[part[1]:selected[0][0]]):
                break
            selected.insert(0, part)
        return selected
    if after:
        selected = [after[0]]
        for part in after[1:]:
            if not re.fullmatch(r'\s*(?:、|和|与|及|以及)\s*', clause[selected[-1][1]:part[0]]):
                break
            selected.append(part)
        return selected
    return []


def _effect_allows_subject(effect, subject):
    target = str(effect.get('target') or '').strip()
    if target in {'场景表面', '整体表面', '所有表面', '全场景表面'}:
        return True
    if target in {'建筑', '建筑物', '建筑表面', '建筑物表面'}:
        return subject[2] in _BUILDING_PARTS
    if target == '陈设物品':
        return subject[2] not in _BUILDING_PARTS
    return any(part[2] == subject[2] and (not part[3] or part[3] == subject[3])
               for part in _part_mentions(target))


def _condition_supports_effect(kind, clause, match, effects, inherited=()):
    allowed_kinds = {kind}
    # A charred coating can have slight flaking or fine heat cracks on the same
    # part. This does not grant general wear, contamination, or structural loss.
    if kind in {'peeling', 'crack'} and re.search(r'轻微|细微|细小|局部|漆层|漆面|表层', clause) and not re.search(
            r'严重|大面积|大块|深裂|贯穿|断裂|崩塌', clause):
        allowed_kinds.add('scorch')
    allowed = [effect for effect in effects if isinstance(effect, dict) and effect.get('kind') in allowed_kinds]
    subjects = _effect_subjects(clause, match) or inherited
    if subjects:
        return all(any(_effect_allows_subject(effect, part) for effect in allowed) for part in subjects)
    # Unknown named props can still be checked against the exact source target;
    # lack of a recognized surface must never act as a wildcard.
    return any(str(effect.get('target') or '') in {'场景表面', '整体表面', '所有表面', '全场景表面'}
               or (str(effect.get('target') or '').strip()
                   and str(effect['target']).strip() in clause) for effect in allowed)


def _present_effects(kind, clause, match, effects, inherited):
    # A denial of a requested effect does not fulfill the positive requirement.
    if re.search(r'(?:没有|并无|未见|不带|无)(?:明显|任何|的)?$', clause[max(0, match.start() - 12):match.start()]):
        return set()
    subjects = _effect_subjects(clause, match) or inherited
    return {index for index, effect in enumerate(effects)
            if isinstance(effect, dict) and effect.get('kind') == kind
            and (any(_effect_allows_subject(effect, part) for part in subjects) if subjects
                 else _condition_supports_effect(kind, clause, match, [effect]))}


def _missing_effects(effects, present):
    """A dilapidation group needs one visible old surface, explicit kinds each."""
    old_source = re.compile(r'破败|年久失修|荒废失修|废弃多年|dilapidated', re.I)
    direct_old = {'wear': re.compile(r'磨损|磨痕|旧损|worn|weathered', re.I),
                  'peeling': re.compile(r'剥落|脱落|起皮|peeling', re.I),
                  'crack': re.compile(r'裂纹|裂缝|开裂|龟裂|crack', re.I)}
    labels = {kind: label for kind, label, _ in _CONDITION_DAMAGE}
    groups, required = {}, []
    for index, effect in enumerate(effects):
        if not isinstance(effect, dict) or effect.get('kind') not in labels:
            continue
        kind = effect['kind']
        target = str(effect.get('target') or '')
        evidence = str(effect.get('evidence') or '')
        if kind in direct_old and old_source.search(evidence):
            groups.setdefault(target, set()).add(index)
            if direct_old[kind].search(evidence):
                required.append(({index}, target + labels[kind]))
        else:
            required.append(({index}, target + labels[kind]))
    required.extend((members, target + '局部旧损') for target, members in groups.items())
    return ['缺少指定表面状态「%s」' % label for members, label in required if not members & present]


def _condition_clauses(prompt):
    for sentence in re.split(r'[。；;\n]+', prompt):
        previous = []
        for clause in re.split(r'[，,]+', sentence):
            parts = _part_mentions(clause)
            # A nearby named part or prop may carry into an omitted-subject
            # continuation. A new subject or sentence stops the carry.
            inherited = (previous[-1:] if not parts and previous
                         and re.match(r'\s*(?:保留|留有|留着|局部|表面|仍|其|并|可见|呈现|带有|覆盖)', clause)
                         else [])
            yield clause, inherited
            previous = parts


def _condition_issues(prompt, condition, facts):
    effects = condition.get('surface_effects') or []
    source_text = str(condition.get('source_text') or '')
    name = str(condition.get('scene_name') or (facts.get('name', '') if isinstance(facts, dict) else ''))
    natural = bool(_NATURAL.search(name or source_text)) and not _INTERIOR.search(name or source_text)
    issues = list(unsupported_opening_residue(prompt, condition))
    issues.extend(missing_opening_residue(prompt, condition))
    present = set()
    for clause, inherited in _condition_clauses(prompt):
        if not clause.strip():
            continue
        if _lighting_conflict(clause, condition.get('lighting', 'auto')):
            issues.append('光照明暗矛盾「%s」' % clause.strip()[:90])
        for kind, label, pattern in _CONDITION_DAMAGE:
            for match in _affirmed(pattern, clause):
                present.update(_present_effects(kind, clause, match, effects, inherited))
                if not (kind == 'moss' and natural) and not _condition_supports_effect(kind, clause, match, effects, inherited):
                    issues.append('%s「%s」' % (label, clause.strip()[:90]))
        if _affirmed(_COLLAPSE, clause) and condition.get('structure', 'intact') != 'collapsed':
            issues.append('坍塌损毁「%s」' % clause.strip()[:90])
        for match in _affirmed(_STRUCTURAL_DAMAGE, clause):
            if condition.get('structure', 'intact') not in {'damaged', 'collapsed'} and not (
                    match.group() in {'破败', '残破'}
                    and _condition_supports_effect('wear', clause, match, effects, inherited)):
                issues.append('结构破败「%s」' % clause.strip()[:90])
        if _affirmed(_DISORDER, clause) and condition.get('order', 'tidy') != 'messy':
            issues.append('陈设杂乱「%s」' % clause.strip()[:90])
        if _affirmed(_ABANDONED, clause) and not _ABANDONED.search(source_text):
            issues.append('失修废弃「%s」' % clause.strip()[:90])
        if _affirmed(_ACTIVE_FIRE, clause) and not _ACTIVE_FIRE.search(source_text):
            issues.append('正在着火「%s」' % clause.strip()[:90])
    if condition.get('order') == 'messy' and not _affirmed(_MESSY_ARRANGEMENT, prompt):
        issues.append('缺少指定陈设状态「陈设物品应有可见的杂乱或错位摆放」')
    issues.extend(_missing_effects(effects, present))
    weather = condition.get('weather') or {}
    if weather.get('kind'):
        kinds = {kind for kind, pattern, _ in _WEATHER
                 if _affirmed(re.compile(pattern, re.I), prompt)}
        if kinds - {weather['kind']}:
            issues.append('天气与原文不一致「%s」' % weather.get('description', weather['kind']))
        if weather['kind'] not in kinds:
            issues.append('缺少指定天气「%s」' % weather.get('description', weather['kind']))
    identity = condition.get('place_identity') or ''
    if identity and identity != condition.get('scene_name') and identity not in prompt:
        issues.append('缺少原文专业地点「%s」' % identity)
    for fact in condition.get('fixed_facts') or []:
        pattern = {'有顶遮雨结构': r'有顶|顶棚|雨棚|遮雨棚|棚顶|屋顶',
                   '柱子': r'柱', '窗户': r'窗', '墙面': r'墙',
                   '地板': r'地面|地板|地砖|石板',
                   '地面': r'地面|地板|地砖|石板'}.get(fact, re.escape(fact))
        if fact.endswith('门'):
            pattern = re.escape(fact[:-1]) + r'.{0,3}门'
        if not _affirmed(re.compile(pattern), prompt):
            issues.append('缺少原文固定构造「%s」' % fact)
    return issues


def validate_scene_prompt(prompt, facts, *, condition=None):
    """Return the original caption or reject unsupported finite additions.

    Facts are the curated scene card, not a model's draft or a system template.
    With a condition snapshot, lighting, order, structure and surface effects
    have independent permissions. Without one, retain the legacy source checks.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise ScenePromptContentError(['空的场景描述'])
    source = _source_clauses(facts)
    general_decay = any(_GENERAL_DECAY.search(clause) for clause in source)
    issues = []
    for clause in _CLAUSE.split(prompt):
        if not clause.strip():
            continue
        if _VISIBLE_PERSON.search(clause) and not _ART_SUBJECT.search(clause):
            issues.append('场景底图中的人物「%s」' % clause.strip()[:90])
        if _META.search(clause):
            issues.append('制作元指令「%s」' % clause.strip()[:90])
        if _STORY_RESIDUE.search(clause):
            issues.append('剧情动作残留「%s」' % clause.strip()[:90])
        if condition is None and not general_decay:
            for label, pattern in _DAMAGE:
                if pattern.search(clause) and not _supported_damage(clause, source, pattern):
                    issues.append('%s「%s」' % (label, clause.strip()[:90]))
    # Natural moss is a location feature. Indoor stonework moss is a different
    # claim, requiring explicit source support or an abandoned-state baseline.
    names = str(facts.get('name') or '') if isinstance(facts, dict) else ''
    source_text = '\n'.join(source)
    interior = bool(_INTERIOR.search(names or source_text))
    natural = bool(_NATURAL.search(names or source_text)) and not interior
    if condition is None and not general_decay and not natural and _MOSS.search(prompt) and not re.search(r'青苔|苔藓', source_text):
        issues.append('石材缝隙青苔「%s」' % _MOSS.search(prompt).group(0)[:90])
    if condition is not None:
        issues.extend(_condition_issues(prompt, condition, facts))
    if issues:
        raise ScenePromptContentError(list(dict.fromkeys(issues)))
    return prompt
