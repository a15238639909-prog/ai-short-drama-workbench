"""Small, deterministic scene-state contract; never reads files or calls a model.

Each dimension is independent: darkness changes illumination, scorching changes
the named surface, and disorder changes arrangement. Model-written design prose
is not evidence of the user's requested condition.
"""
import copy
import re


VERSION = 2
_CARD_FIELDS = (
    'space', 'contract_text', 'light', 'depth', 'layout', 'landmarks', 'furniture',
    'fixtures', 'ground', 'walls', 'door', 'window', 'materials', 'regions',
    'fixed_landmarks', 'base_light_color', 'main_light', 'time', 'weather',
    'interior', 'time_of_day', 'scale', 'scale_class', 'states',
)
_ROOM_WORDS = ('宫殿大厅', '古堡大厅', '大厅', '大殿', '广场', '礼堂', '卧室',
               '寝室', '寝宫', '船舱', '客舱', '书房', '厨房', '客厅', '走廊',
               '庭院', '城堡', '宫殿', '仓库', '教室', '浴室', '地牢')
_PART = re.compile(
    r'(?:[东西南北左右前后](?:侧|边|面|部)?|第[一二三四五六七八九十\d]+根)?'
    r'(?:木质|石质|金属|玻璃|木|石|铁|砖|青铜|水泥)?'
    r'(?:天花板|门框|窗框|窗台|墙壁|墙面|墙体|外墙|内墙|地板|地面|屋顶|屋架|'
    r'横梁|柱子|柱身|石柱|木柱|立柱|柱体|大门|木门|铁门|门板|窗户|墙纸|'
    r'挂毯|地毯|沙发|书桌|桌子|椅子|陈设物品|陈设|家具|东西|台阶|楼梯|栏杆|建筑|墙|门|柱)')
_KINDS = {
    'scorch': r'烧焦|焦黑|焦痕|焦灼|灼烧痕迹|烧灼痕迹|charred|scorch(?:ed|ing)?',
    'soot': r'烟熏|烟灰|煤灰|soot(?:y)?',
    'wear': r'破败|陈旧|老旧|残旧|年久失修|荒废失修|废弃多年|磨损|旧损|斑驳|weathered|worn|dilapidated',
    'rust': r'锈蚀|生锈|铁锈|锈斑|rust(?:y|ed)?',
    'peeling': r'剥落|脱落|起皮|peeling',
    'crack': r'裂纹|裂缝|开裂|龟裂|crack(?:ed|s)?',
    'moss': r'青苔|苔藓|moss(?:y)?',
    'fade': r'褪色|掉色|faded',
    'dust': r'积灰|积尘|灰尘|尘土|蒙尘|dust(?:y)?',
    'stain': r'污渍|污迹|污损|污垢|脏污|stain(?:ed|s)?|dirty',
}
_EFFECT_LABELS = {
    'scorch': '保留烧焦痕迹', 'soot': '留有烟熏痕迹', 'wear': '保留局部旧损',
    'rust': '留有锈蚀', 'peeling': '表层局部剥落', 'crack': '留有局部裂纹',
    'moss': '长有青苔', 'fade': '表层褪色', 'dust': '覆有积灰', 'stain': '留有污渍',
}


def _text(value):
    return str(value or '').strip() if not isinstance(value, (dict, list, tuple)) else ''


def _negated(text, start):
    """Only nearby explicit negations, not a general language interpreter."""
    prefix = re.split(r'[，,。.!！？;；\n、]', text[:start])[-1][-12:]
    # "不是废墟而是烧焦" must not negate the condition after "而是".
    prefix = re.split(r'而是|但是|但|不过', prefix)[-1]
    return bool(re.search(r'(?:没有|并无|并非|不是|不要|不得|不能|无需|避免|禁止|未曾|未|无|不)'
                          r'(?:任何|明显|出现|发生|呈现|显得|再|是|要|会|被|曾|过|的|\s){0,4}$', prefix))


def _matches(pattern, text):
    return [m for m in re.finditer(pattern, text, re.I) if not _negated(text, m.start())]


_WEATHER = (
    ('clear', r'雨后初晴|雨过天晴|雨(?:已经|已|终于|渐渐)?停|放晴|转晴|晴天|晴朗|晴空', '晴朗天气'),
    ('rain', r'暴雨|大雨|倾盆大雨|小雨|细雨|阴雨|下雨|雨(?:中|天|夜|幕|帘|势|水|丝|声|滴)', '降雨天气'),
    ('snow', r'暴雪|大雪|小雪|风雪|下雪|雪天|雪幕', '降雪天气'),
    ('fog', r'浓雾|大雾|雾天|迷雾笼罩', '雾天'),
)


def weather_state(text):
    """Finite explicit weather evidence; a multi-stage story stays unresolved."""
    found = [(kind, m, label) for kind, pattern, label in _WEATHER
             for m in _matches(pattern, _text(text))]
    if not found or len({kind for kind, _, _ in found}) != 1:
        return {}
    kind, match, label = found[-1]
    if kind == 'rain' and _matches(r'暴雨|倾盆大雨', _text(text)):
        label = '暴雨天气'
    elif kind == 'clear' and re.fullmatch(r'雨(?:已经|已|终于|渐渐)?停', match.group()):
        label = '降雨已经停止'
    return {'kind': kind, 'description': label, 'evidence': match.group()}


def _target(clause, match, scene_name):
    before = list(_PART.finditer(clause[:match.start()]))
    after = list(_PART.finditer(clause[match.end():]))
    if before and (not after or match.start() - before[-1].end() <= 8):
        result = before[-1].group()
        return '陈设物品' if result in ('陈设', '东西') else result
    if after and after[0].start() <= 8:
        result = after[0].group()
        return '陈设物品' if result in ('陈设', '东西') else result
    # A modifier of a person or carried object is not a condition of the room.
    if re.search(r'衣服|衣袖|外套|衣角|头发|皮肤|手臂|脸|尸体|纸张|信纸|书本|照片', clause):
        return None
    if scene_name and (scene_name in clause or any(w in clause for w in _ROOM_WORDS if w in scene_name)):
        return '场景表面'
    # Continuations such as "墙面烧焦，仍留有焦痕" are surface statements.
    return '场景表面'


def build_condition(scene_name, source_text='', *, origin='user'):
    """Parse explicit, finite condition vocabulary; return JSON-compatible data."""
    name = _text(scene_name)
    source = _text(source_text)
    # Direct callers can provide a descriptive scene name alone. A derived
    # model name cannot authorize an exception when an original story exists.
    evidence = source or (name if origin in ('user', 'manual_card', 'user_edit', 'direct_card') else '')
    evidence = opening_scene_source({'source_text': evidence})
    condition = {
        'version': VERSION, 'scene_name': name, 'lighting': 'auto', 'order': 'tidy',
        'structure': 'intact', 'surface_effects': [],
        'spatial': 'open' if re.search(r'大厅|大殿|广场|礼堂|plaza|great hall', name, re.I) else 'by_use',
        'source_text': source, 'origin': _text(origin) or 'user',
    }
    if _matches(r'黑暗|昏暗|幽暗|阴暗|漆黑|很暗|光线暗|暗淡|低照度|微光|dark|dim(?:ly lit)?', evidence):
        condition['lighting'] = 'dark'
    elif _matches(r'明亮|灯火通明|亮堂|bright(?:ly lit)?', evidence):
        condition['lighting'] = 'bright'
    if _matches(r'杂乱|凌乱|散乱|乱七八糟|messy|clutter(?:ed)?|disorder(?:ly)?', evidence):
        condition['order'] = 'messy'
    if _matches(r'坍塌|倒塌|塌陷|垮塌|坍毁|倒毁|废墟|collapsed?|ruins?', evidence):
        condition['structure'] = 'collapsed'
    elif _matches(r'(?:墙体|墙壁|屋顶|屋架|横梁|立柱|柱子|结构|建筑|门窗)'
                  r'(?:已经|严重|局部|被|受到|出现|有|的|了|\s){0,3}'
                  r'(?:破损|损坏|断裂|断塌)|(?:破损|损坏|断裂)的?(?:墙体|屋顶|横梁|立柱|建筑)', evidence):
        condition['structure'] = 'damaged'
    if _matches(r'狭小|狭窄|紧凑|局促|逼仄|窄小|compact|cramped|narrow', evidence):
        condition['spatial'] = 'compact'
    elif _matches(r'开阔|宽敞|宽阔|宽大|辽阔|spacious|open space', evidence):
        condition['spatial'] = 'open'
    seen = set()
    for clause in re.split(r'[，,。.!！？;；\n]', evidence):
        for kind, pattern in _KINDS.items():
            for match in _matches(pattern, clause):
                target = _target(clause, match, name)
                if target is None or (kind, target) in seen:
                    continue
                seen.add((kind, target))
                condition['surface_effects'].append({'kind': kind, 'target': target, 'evidence': clause.strip()})
                if kind == 'wear' and re.search(r'破败|年久失修|荒废失修|废弃多年|dilapidated', match.group(), re.I):
                    for related in ('peeling', 'crack'):
                        if (related, target) not in seen:
                            seen.add((related, target))
                            condition['surface_effects'].append({'kind': related, 'target': target, 'evidence': clause.strip()})
    return condition


def positive_condition(condition):
    """Visible positive statements suitable for a model's scene prompt."""
    c = condition if isinstance(condition, dict) else {}
    effects = [x for x in c.get('surface_effects', []) if isinstance(x, dict) and x.get('kind') in _EFFECT_LABELS]
    parts = [{'intact': '主体结构完整，建筑保持正常维护状态',
              'damaged': '主体结构保留，明确受损部位呈局部损坏状态',
              'collapsed': '建筑呈明确坍塌状态'}
             .get(c.get('structure'), '主体结构完整，建筑保持正常维护状态')]
    if effects and c.get('structure') == 'intact':
        parts[0] = '主体结构完整'
    requested_states = _required_opening_residue(c)
    if c.get('order') == 'messy':
        parts.append('活动陈设错位摆放，局部物件散置，建筑轮廓和主要通行关系清楚')
    else:
        parts.append('陈设排列整齐有序' if effects or requested_states else
                     '场景干净整洁，可见构件表面维护良好，陈设排列整齐有序，主要通道连贯清楚')
    parts.append({'open': '空间开阔，通行区域宽敞',
                  'compact': '空间紧凑，尺度符合场所用途',
                  'by_use': '空间尺度符合场所用途，主体与通行空间比例合理'}
                 .get(c.get('spatial'), '空间尺度符合场所用途'))
    light = {'dark': '环境照明昏暗，物体轮廓与空间层次仍可辨认',
             'bright': '环境照明明亮，物体和空间层次清楚'}.get(c.get('lighting'))
    if light:
        parts.append(light)
    for effect in effects:
        parts.append((_text(effect.get('target')) or '场景表面') + _EFFECT_LABELS[effect['kind']])
    for kind, subject in requested_states:
        if kind == '散落物品':
            parts.append('可见散落的' + subject)
        else:
            parts.append(subject + {'床品湿痕': '留有湿痕', '床品污渍': '留有污渍',
                                   '床品血渍': '留有血渍', '床品凌乱': '呈凌乱状态'}[kind])
    weather = c.get('weather') or {}
    if weather.get('description'):
        parts.append('室外为' + weather['description'])
    if c.get('place_identity'):
        parts.append('地点为' + c['place_identity'])
    if c.get('fixed_facts'):
        parts.append('固定构造包括' + '、'.join(c['fixed_facts']))
    return '；'.join(parts) + '。'


_LIGHT_FIELDS = ('light', 'main_light', 'base_light_color')
_VISUAL_LIGHT_FIELDS = (
    'space', 'contract_text', 'depth', 'fixtures', 'landmarks', 'layout', 'furniture',
    'ground', 'walls', 'door', 'window', 'materials', 'regions', 'fixed_landmarks',
    'design', 'intent', 'location_map',
)
_LIGHT_DARK = re.compile(r'黑暗|昏暗|幽暗|阴暗|漆黑|暗淡|微弱|微光|低照度|黯淡|dark|dim\b', re.I)
_LIGHT_BRIGHT = re.compile(r'明亮|亮堂|灯火通明|通体透亮|充足.{0,3}光|强光|强烈.{0,3}光|bright', re.I)
_LIGHT_CUE = re.compile(
    r'明亮|亮堂|灯火通明|黑暗|昏暗|幽暗|阴暗|漆黑|暗淡|黯淡|微光|低照度|'
    r'自然光|阳光|日光|月光|光线|光照|灯光|烛光|光束|光柱|光斑|光晕|光芒|'
    r'暖光|冷光|白光|黄光|金光|红光|蓝光|照明|bright|dark|dim\b|sunlight|lighting', re.I)
_LIGHT_ACTION = re.compile(r'透入|透进|照入|投下|投射|照亮|洒落|射入|映照|映亮|照射|'
                           r'透过|穿过|穿透|反射|折射|散发|发出|泛出|泛着|流入|倾泻|洒在')
_LIGHT_STRUCTURE = re.compile(
    r'高窗|拱窗|窗洞|窗格|窗户|天窗|门洞|拱门|大门|宫门|柱列|石柱|木柱|立柱|'
    r'梁架|屋架|横梁|穹顶|屋顶|天花板|墙面|墙壁|地面|地板|屏风|烛台|灯具|'
    r'吊灯|壁灯|宫灯|灯笼|大厅|大殿|房间|船舱|卧室')


def _lighting_subject(clause):
    """Keep the construction that carried a conflicting light description."""
    cue = _LIGHT_CUE.search(clause)
    action = _LIGHT_ACTION.search(clause)
    starts = [m.start() for m in (cue, action) if m]
    prefix = clause[:min(starts)] if starts else ''
    if _LIGHT_STRUCTURE.search(prefix):
        return re.sub(r'(?:被|将|把|有|在|从|由|向|以|呈|的|为|提供|带来|充满)+$', '', prefix).strip()
    # Light-first sentences ("自然光从高窗透入") still retain their
    # architectural nouns, without inventing dimensions or a new light source.
    return '、'.join(dict.fromkeys(m.group() for m in _LIGHT_STRUCTURE.finditer(clause)))


def _project_lighting_text(value, lighting):
    tokens = re.split(r'([，,。.!！？;；\n｜|])', value)
    changed = False
    for index in range(0, len(tokens), 2):
        clause = tokens[index]
        has_dark = bool(_LIGHT_DARK.search(clause))
        has_bright = bool(_LIGHT_BRIGHT.search(clause))
        conflict = (has_bright or (bool(_LIGHT_CUE.search(clause)) and not has_dark)) if lighting == 'dark' else has_dark
        if not conflict:
            continue
        tokens[index] = _lighting_subject(clause)
        changed = True
    if not changed:
        return value
    return _join_projected_clauses(tokens, value)


def _join_projected_clauses(tokens, original):
    kept = []
    for index in range(0, len(tokens), 2):
        if tokens[index].strip():
            if kept:
                kept.append(tokens[index - 1] if index else '，')
            kept.append(tokens[index].strip())
    result = ''.join(kept)
    if result and original.endswith(('。', '.', '！', '!', '？', '?')):
        result += original[-1]
    return result


_ORDER_STATE = r'井然有序|井井有条|整齐有序|整洁有序|整整齐齐|整洁|整齐|有序|规整|齐整'
_ORDER_MOVABLE = re.compile(r'活动陈设|陈设|家具|物品|物件|桌椅|座椅|木椅|桌子|椅子|书桌|茶几|器物|摆件|杂物|道具')
_ORDER_BUILDING = re.compile(r'柱列|石柱|木柱|立柱|梁架|屋架|横梁|穹顶|屋顶|天花板|墙面|墙壁|地面|地板|'
                             r'高窗|窗洞|窗格|门洞|拱门|建筑|结构|楼梯|台阶')
_ORDER_GLOBAL = re.compile(r'整体|场景|室内|屋内|厅内|房间|大厅|空间')

# These are temporary states of furnishings, not the natural roughness of
# stone or folds in cloth. Source-owned exceptions stay tied to their subject.
_BED_NOUNS = r'床单|床品|床罩|被单|被套|被褥|被子|枕套|枕头|寝具|床铺|床面'
_BEDDING = re.compile(_BED_NOUNS)
_BED_FRAME = re.compile(r'四柱床|木质床架|木制床架|床架|双人床|单人床|大床|木床|床(?=铺着|铺有|上铺)')
_BED_SUBJECTS = (
    ('床单', re.compile(r'床单|床罩|被单')),
    ('被褥', re.compile(r'被套|被褥|被子')),
    ('枕头', re.compile(r'枕套|枕头')),
    ('床品', re.compile(r'床品|寝具|床铺|床面')),
)
_BED_WET = re.compile(
    r'湿痕|湿渍|水渍|浸湿|打湿|湿透|濡湿|潮湿|湿润|变湿|'
    r'湿(?:的)?(?:(?:白色|米白|灰色|蓝色|棉布|亚麻){0,3})(?:' + _BED_NOUNS + r')|'
    r'(?:' + _BED_NOUNS + r')(?:已经|已|变得|有些|略微|微微|稍微|仍然|很|有点|了){0,3}湿(?:了)?')
_BED_DIRTY = re.compile(r'污渍|污迹|脏污|汗渍|污痕|污斑')
_BED_BLOOD = re.compile(r'血渍|血迹|血污')
_BED_DISORDER = re.compile(r'凌乱|杂乱|散乱|乱成一团|揉成一团|皱成一团|胡乱')
_SCATTERED = re.compile(r'散落|散置|散乱|丢着|扔着|胡乱堆放|随地堆放')
_BELONGINGS = (
    ('衣物', re.compile(r'衣物|衣服|衣裤|外套|披风|衬衫|上衣|长裤|裤子|裙子|长裙|短裙|内衣|围巾|手套')),
    ('皮带', re.compile(r'皮带|腰带')),
    ('领带', re.compile(r'领带')),
)
_RESIDUE_SUPPORT = re.compile(r'床尾|床沿|床边|床面|床铺|地板|地面|地毯|桌面|茶几|椅背|椅子|沙发|层板')
_LATER_STATE = re.compile(
    r'(?:结束|完成|使用|散场|离场|离开|打斗|战斗|争执|互动|动作)后|'
    r'(?:随后|之后|后来|最终|最后)[^，。；\n]{0,24}(?:变得|变成|变湿|留下|弄乱|弄湿|散落)|'
    r'(?:因|经过|随着)[^，。；\n]{0,12}(?:打斗|战斗|争执|追逐|翻找|互动|动作)[^，。；\n]{0,12}(?:变得|留下|凌乱|湿痕)')


def opening_scene_source(condition):
    """Exclude explicit later-action results from initial-state authority.

    This is deliberately finite: it handles marked post-event clauses, not an
    inferred timeline or a rewrite of the user's story.
    """
    source = _text(condition.get('source_text')) if isinstance(condition, dict) else ''
    return '，'.join(clause.strip() for clause in re.split(r'[，,。.!！？;；\n]', source)
                    if clause.strip() and not _LATER_STATE.search(clause))


def _residue_states(clause):
    states = []
    if _BEDDING.search(clause):
        subjects = [name for name, pattern in _BED_SUBJECTS if pattern.search(clause)]
        for kind, pattern in (('床品湿痕', _BED_WET), ('床品污渍', _BED_DIRTY),
                              ('床品血渍', _BED_BLOOD), ('床品凌乱', _BED_DISORDER)):
            if _matches(pattern.pattern, clause):
                states.extend((kind, subject) for subject in subjects)
    if _matches(_SCATTERED.pattern, clause):
        states.extend(('散落物品', name) for name, pattern in _BELONGINGS if pattern.search(clause))
    return states


def _required_opening_residue(condition):
    source = opening_scene_source(condition)
    return tuple(dict.fromkeys(state
                               for clause in re.split(r'[，,。.!！？;；\n]', source)
                               for state in _residue_states(clause)))


def missing_opening_residue(text, condition):
    """Require explicitly requested initial textile/clothing states.

    Generic disorder specifies no particular object. Only affirmative opening
    source states carry requirements, separate from model-written card prose.
    """
    if not isinstance(condition, dict) or condition.get('order') not in ('tidy', 'messy'):
        return ()
    observed = {state for clause in re.split(r'[，,。.!！？;；\n｜|]', _text(text))
                for state in _residue_states(clause) if not _LATER_STATE.search(clause)}
    missing = []
    for kind, subject in _required_opening_residue(condition):
        if (kind, subject) in observed:
            continue
        if kind.startswith('床品') and subject == '床品' and any(k == kind for k, _ in observed):
            continue
        suffix = {'床品湿痕': '湿痕', '床品污渍': '污渍', '床品血渍': '血渍',
                  '床品凌乱': '凌乱', '散落物品': '散落'}[kind]
        missing.append('缺少' + subject + suffix)
    return tuple(dict.fromkeys(missing))


def unsupported_opening_residue(text, condition):
    """Return unsupported temporary furnishing states in an empty scene.

    A messy-room request permits scattered personal clothing, but cannot
    authorize wet bedding. A specific requested object permits that object,
    not all other clothing. Ordinary tabletop books/cups, cloth folds and
    natural water/foliage do not match these state classes.
    """
    if not isinstance(condition, dict) or condition.get('order') not in ('tidy', 'messy'):
        return ()
    source = opening_scene_source(condition)
    supported = {state for clause in re.split(r'[，,。.!！？;；\n]', source)
                 for state in _residue_states(clause)}
    messy = condition.get('order') == 'messy' and (
        not condition.get('source_text') or bool(_matches(
            r'杂乱|凌乱|散乱|乱七八糟|messy|clutter(?:ed)?|disorder(?:ly)?', source)))
    issues = []
    for clause in re.split(r'[，,。.!！？;；\n｜|]', _text(text)):
        states = _residue_states(clause)
        if states and _LATER_STATE.search(clause):
            issues.append('后续动作痕迹')
            continue
        for kind, subject in states:
            if ((kind, subject) in supported or
                    (kind.startswith('床品') and (kind, '床品') in supported) or
                    (messy and kind in ('散落物品', '床品凌乱'))):
                continue
            issues.append('散落' + subject if kind == '散落物品' else kind)
    return tuple(dict.fromkeys(issues))


def _project_opening_text(value, condition):
    tokens = re.split(r'([，,。.!！？;；\n｜|])', value)
    changed = False
    for index in range(0, len(tokens), 2):
        clause = tokens[index]
        issues = unsupported_opening_residue(clause, condition)
        if not issues:
            continue
        if _BEDDING.search(clause) and any(issue.startswith('床品') for issue in issues):
            # Retain source materials/colours rather than replace the bedding
            # with a differently designed bed. Ordinary folds are untouched.
            anchor = _BED_FRAME.search(clause)
            prefix = clause[:anchor.end()] if anchor else ''
            if any(pattern.search(prefix) for pattern in (_BED_WET, _BED_DIRTY, _BED_BLOOD, _BED_DISORDER)):
                prefix = ''
            bedding_text = clause[anchor.end():] if prefix else clause
            bedding = re.findall(
                r'(?:(?:白色|米白色?|乳白色?|浅灰色?|深灰色?|灰色|蓝色|红色|纯棉|棉麻|棉布|'
                r'亚麻|丝绸|粗糙|柔软|厚实|薄)[的]?)*(?:床单|床品|床罩|被单|被套|被褥|被子|枕套|枕头|寝具|床铺|床面)', bedding_text)
            tokens[index] = (prefix + '，' if prefix else '') + '、'.join(dict.fromkeys(bedding)) + '保持洁净平整'
        else:
            # Keep the support and its preceding placement/material. A belt
            # on the floor is transient; the dark wooden floor is not.
            support = _RESIDUE_SUPPORT.search(clause)
            prefix = clause[:support.end()] if support else ''
            if any(pattern.search(prefix) for _, pattern in _BELONGINGS):
                prefix = support.group() if support else ''
            tokens[index] = prefix
        changed = True
    return _join_projected_clauses(tokens, value) if changed else value


def _project_order_text(value):
    tokens = re.split(r'([，,。.!！？;；\n｜|])', value)
    changed = False
    for index in range(0, len(tokens), 2):
        clause = tokens[index]
        if not re.search(_ORDER_STATE, clause):
            continue
        if not _ORDER_MOVABLE.search(clause):
            if _ORDER_BUILDING.search(clause) or not _ORDER_GLOBAL.search(clause):
                continue
        # Remove arrangement state, retaining the objects and their materials.
        projected = re.sub(r'(?:摆放|排列|布置|陈列|排布|收纳|保持|显得|呈现|呈)?'
                           r'(?:得很|得|十分|非常|相当|依旧|仍然)?(?:' + _ORDER_STATE + r')(?:地|的)?', '', clause)
        projected = re.sub(r'(?:整体|摆放|排列|布置|陈列|排布|保持|显得|呈现|且|而|的|地)+$', '', projected).strip()
        if re.fullmatch(r'(?:整体|场景|室内|屋内|厅内|空间|保持|呈现|十分|非常|相当|且|而|的|地)*', projected):
            projected = ''
        tokens[index] = projected
        changed = True
    return _join_projected_clauses(tokens, value) if changed else value


def project_condition_facts(facts, condition):
    """Copy image facts and project lighting, order and opening-state facts.

    This removes conflicting automatic light prose while preserving windows,
    columns and other construction subjects. The saved card is never mutated.
    """
    projected = copy.deepcopy(facts)
    if isinstance(projected, dict) and isinstance(condition, dict):
        if condition.get('weather'):
            weather = condition['weather']
            current = weather_state(projected.get('weather'))
            if (current.get('kind') != weather['kind'] or
                    (weather['description'] == '暴雨天气' and current.get('description') != '暴雨天气')):
                projected['weather'] = weather['description']
        if condition.get('place_identity'):
            projected['name'] = condition['place_identity']
    lighting = condition.get('lighting') if isinstance(condition, dict) else None
    messy = isinstance(condition, dict) and condition.get('order') == 'messy'
    opening = isinstance(condition, dict) and condition.get('order') in ('tidy', 'messy')
    if not isinstance(projected, dict) or (lighting not in ('dark', 'bright') and not opening):
        return projected
    if lighting in ('dark', 'bright'):
        current = {'dark': '环境照明昏暗，物体轮廓与空间层次仍可辨认',
                   'bright': '环境照明明亮，物体和空间层次清楚'}[lighting]
        for key in _LIGHT_FIELDS:
            projected[key] = current

    def project(value):
        if isinstance(value, str):
            text = _project_lighting_text(value, lighting) if lighting in ('dark', 'bright') else value
            text = _project_order_text(text) if messy else text
            return _project_opening_text(text, condition) if opening else text
        if isinstance(value, list):
            return [project(item) for item in value]
        if isinstance(value, tuple):
            return tuple(project(item) for item in value)
        if isinstance(value, dict):
            return {key: project(item) for key, item in value.items()}
        return value

    for key in _VISUAL_LIGHT_FIELDS:
        if key in projected:
            projected[key] = project(projected[key])
    return projected


def _aliases(names):
    candidates = {}
    for name in names:
        for alias in (name,) + tuple(w for w in _ROOM_WORDS if name.endswith(w)):
            if alias:
                candidates.setdefault(alias, set()).add(name)
    # Ambiguous short names cannot identify a particular room.
    return {alias: next(iter(owners)) for alias, owners in candidates.items() if len(owners) == 1}


def _scope_text(source, scene_name, scene_names):
    names = list(dict.fromkeys([_text(n) for n in scene_names if _text(n)] + [scene_name]))
    aliases = _aliases(names)
    if not aliases:
        return source
    pattern = re.compile('|'.join(re.escape(x) for x in sorted(aliases, key=len, reverse=True)))
    clauses = re.split(r'[，,。.!！？;；\n]', source)
    scoped, current = [], set()
    has_scene_reference = any(pattern.search(clause) for clause in clauses)
    for clause in clauses:
        if not clause.strip():
            continue
        mentions = list(pattern.finditer(clause))
        owners = {aliases[m.group()] for m in mentions}
        if not mentions:
            if scene_name in current or (not has_scene_reference and len(names) == 1):
                scoped.append(clause.strip())
            elif re.search(r'所有场景|各个场景|全部场景|每个场景|这些场景|各场景', clause):
                scoped.append(clause.strip())
            continue
        if len(owners) == 1:
            current = owners
            if scene_name in owners:
                scoped.append(clause.strip())
            continue
        # "大厅和书房都很暗" explicitly shares the state. Otherwise each
        # location owns its own segment, including a modifier before its name.
        if re.search(r'都|均|全部|共同', clause):
            current = owners
            if scene_name in owners:
                scoped.append(clause.strip())
            continue
        bounds = [0]
        for previous, mention in zip(mentions, mentions[1:]):
            middle = clause[previous.end():mention.start()]
            separators = list(re.finditer(r'以及|然后|随后|接着|而|但|与|和|及|到', middle))
            bounds.append(previous.end() + separators[-1].end() if separators else mention.start())
        bounds.append(len(clause))
        for index, mention in enumerate(mentions):
            if aliases[mention.group()] == scene_name:
                scoped.append(clause[bounds[index]:bounds[index + 1]].strip())
        current = {aliases[mentions[-1].group()]}
    return '，'.join(scoped)


def _card_text(card, fields):
    return '，'.join(dict.fromkeys(_text(card.get(key)) for key in fields if _text(card.get(key))))


def _base_condition_for_scene(story, card, *, scene_names=()):
    """Resolve authority, then scope the original input to a single scene.

    Stored model conditions and automatic design/space prose do not authorize
    exceptions. A snapshot supplies evidence of per-field user changes.
    """
    story = story if isinstance(story, dict) else {}
    card = card if isinstance(card, dict) else {}
    name = _text(card.get('name') or card.get('scene_name'))
    settings = story.get('settings') if isinstance(story.get('settings'), dict) else {}
    source = (_text(settings.get('initial_story_input')) or _text(story.get('initial_story_input'))
              or _text(settings.get('one_line')) or _text(story.get('one_line')))
    if card.get('user_made'):
        text = _card_text(card, _CARD_FIELDS)
        return build_condition(name, text or name, origin='manual_card')
    snapshot = card.get('design_fact_snapshot')
    if isinstance(snapshot, dict):
        changed = [key for key in _CARD_FIELDS if key in snapshot and key in card and card[key] != snapshot[key]]
        if changed:
            text = _card_text(card, changed)
            if set(changed) <= {'light', 'main_light', 'base_light_color'}:
                # Editing a light field changes illumination only. Preserve the
                # other independently authorized dimensions of the original.
                base_text = _scope_text(source, name, scene_names) if source else _card_text(card, _CARD_FIELDS)
                condition = build_condition(name, base_text, origin='story' if source else 'direct_card')
                condition['lighting'] = build_condition(name, text, origin='lighting_edit')['lighting']
                condition['source_text'] = '，'.join(filter(None, (base_text, text)))
                condition['origin'] = 'user_edit'
                return condition
            # Replacing the spatial description can deliberately restore a
            # normal condition across the dimensions described in that field.
            return build_condition(name, text, origin='user_edit')
    elif card.get('edited_by_user'):
        return build_condition(name, _card_text(card, _CARD_FIELDS), origin='user_edit')
    if source:
        return build_condition(name, _scope_text(source, name, scene_names), origin='story')
    return build_condition(name, _card_text(card, _CARD_FIELDS), origin='direct_card')


_GENERIC_PLACE_NAMES = set(_ROOM_WORDS) | {
    '库房', '实验室', '装卸台', '回廊', '渡台', '山门', '码头', '房间', '办公室', '店铺',
}
_PLACE_BOUNDARY = re.compile(
    r'必须|务必|需要|应当|应该|准备|打算|计划|负责|决定|'
    r'取出|拿出|交给|送入|送到|护送|守住|推开|打开|离开|'
    r'经过|进入|走进|来到|返回|穿过|抵达|踏入|走到|出发|造成|'
    r'的|从|经|到|在|为|是|把|将|让|使|一座|一间|一处')
_PLACE_NON_NOUN = re.compile(
    r'必须|务必|需要|应当|应该|准备|打算|计划|负责|决定|才能|不能|'
    r'你|我|他|她|它|三人|两人|众人|弟子|人物|随后|然后|正在|'
    r'搬运|保护|取出|拿出|交接|交给|递给|送入|送到|护送|守住|'
    r'走向|奔向|跑向|赶往|推开|打开|关上|走进|进入|穿过')


def _place_identity(name, source, *, scene_names=()):
    # Recover a shortened asset label, e.g. a professional qualifier before a
    # generic room name. Only verbatim source wording can supply that qualifier.
    # Already named sites such as 藏印阁/观云渡台 are authoritative identities.
    # A verbatim occurrence inside "三人必须把藏印阁内的..." does not make
    # that leading story action part of the site's name.
    if name not in _GENERIC_PLACE_NAMES:
        return name
    known = [candidate for candidate in scene_names
             if isinstance(candidate, str) and candidate != name and candidate.endswith(name)
             and candidate in source and not _PLACE_NON_NOUN.search(candidate)]
    if len(set(known)) == 1:
        return known[0]
    candidates = []
    for match in re.finditer(r'([\u4e00-\u9fffA-Za-z0-9]{0,14})' + re.escape(name), source):
        prefix = _PLACE_BOUNDARY.split(match.group(1))[-1]
        prefix = re.sub(r'^(?:(?:现代|普通|正常|干净|整洁|开阔|宽敞|明亮|昏暗|高大|幽暗))+', '', prefix)
        if prefix and not _PLACE_NON_NOUN.search(prefix):
            candidates.append(prefix + name)
    return min(candidates, key=len) if candidates else name


def _fixed_scene_source(source, scene_name, scene_names):
    """Fixed structures need a local scene anchor, not the last plot location.

    Global weather can span paragraphs independently. A later film-wide note
    about indoor windows must not require a door on an outdoor landing stage.
    """
    names = list(dict.fromkeys([_text(name) for name in scene_names if _text(name)] + [scene_name]))
    aliases = _aliases(names)
    labels = [label for label, owner in aliases.items() if owner == scene_name]
    if not labels:
        return ''
    pattern = re.compile('|'.join(re.escape(label) for label in sorted(labels, key=len, reverse=True)))
    result, paragraphs = [], []
    for paragraph in str(source or '').splitlines():
        current = []
        for sentence in re.split(r'(?<=[。！？!?])', paragraph):
            # A film-wide instruction ends inherited location context even
            # when the user entered the entire story on one physical line.
            if (re.search(r'全片|本片|整部|影片|电影|镜头|画面|正文|成片|请由', sentence)
                    and not pattern.search(sentence)
                    and not re.search(r'所有场景|各个场景|全部场景|每个场景|这些场景|各场景', sentence)):
                if current:
                    paragraphs.append(''.join(current))
                    current = []
            else:
                current.append(sentence)
        if current:
            paragraphs.append(''.join(current))
    for paragraph in paragraphs:
        if (pattern.search(paragraph)
                or re.search(r'所有场景|各个场景|全部场景|每个场景|这些场景|各场景', paragraph)):
            scoped = _scope_text(paragraph, scene_name, scene_names)
            if scoped:
                result.append(scoped)
    return '，'.join(result)


def condition_for_scene(story, card, *, scene_names=()):
    """Resolve maintenance plus source-bound place/weather/fixed construction.

    Original weather can apply across connected locations. Explicit local
    weather wins; conflicting stages without a unique binding stay unknown.
    Model-written card atmosphere never supplies authority for these additions.
    """
    story = story if isinstance(story, dict) else {}
    card = card if isinstance(card, dict) else {}
    # Recover a missing label only from an exact supplied catalog ID/name pair.
    # An arbitrary scene ID is never treated as a generated place phrase.
    if not _text(card.get('name') or card.get('scene_name')):
        matches = [name for name in scene_names if isinstance(name, str)
                   and str(card.get('scene_id') or '') == 'SCENE_' + name]
        if len(matches) == 1:
            card = dict(card, name=matches[0])
    condition = _base_condition_for_scene(story, card, scene_names=scene_names)
    settings = story.get('settings') if isinstance(story.get('settings'), dict) else {}
    original = (_text(settings.get('initial_story_input')) or _text(story.get('initial_story_input'))
                or _text(settings.get('one_line')) or _text(story.get('one_line')))
    local = condition.get('source_text', '')
    source = local if condition.get('origin') in {'manual_card', 'direct_card', 'user_edit'} else original
    name = condition.get('scene_name', '')
    condition['weather'] = weather_state(local) or weather_state(source)
    condition['place_identity'] = _place_identity(name, source, scene_names=scene_names) if name else ''
    # These are source nouns, not a new automatically invented floor plan.
    fixed_source = (_fixed_scene_source(original, name, scene_names)
                    if condition.get('origin') == 'story' else local)
    fixed = [m.group() for m in _PART.finditer(fixed_source)
             if m.group() not in {'建筑', '陈设', '陈设物品', '家具', '东西'}
             and not (m.group() == '门' and
                      (fixed_source[max(0, m.start() - 1):m.start()] in {'宗', '师', '掌', '法', '部', '专', '同'}
                       or fixed_source[m.end():m.end() + 1] in {'派', '徒', '生', '人', '下', '规', '主', '类', '第', '风'}))]
    if re.search(r'有顶|雨棚|遮雨棚|顶棚', fixed_source):
        fixed.append('有顶遮雨结构')
    condition['fixed_facts'] = list(dict.fromkeys(fixed))
    return condition
