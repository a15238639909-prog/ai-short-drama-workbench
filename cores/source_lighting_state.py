"""Explicit lighting in the written story; these facts are never video evidence."""
import re

_SOURCE = r'顶灯|吊灯|台灯|壁灯|室内灯光|室内灯|照明灯|手电筒|手电'
_OFF = r'熄灭|灭了|关闭|关掉|关上|按灭|关了|灭掉'
_ON = r'亮起|亮了|打开|开启|点亮|按亮|开了'
_EVENT = re.compile(r'(?P<source>' + _SOURCE + r')(?P<gap>[^，。；\n]{0,8}?)(?P<verb>' + _OFF + '|' + _ON + r')|'
                    r'(?P<verb_first>' + _OFF + '|' + _ON + r')(?P<gap_first>[^，。；\n]{0,5}?)(?P<source_last>' + _SOURCE + r')')
_UNREALIZED = re.compile(r'没有|尚未|还未|未能|不要|不能|别|准备|打算|试图|想要|即将|将要|想')
_QUOTED = re.compile(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"|‘[^’]*’')
_CEILING = re.compile(r'顶灯|吊灯|室内灯(?:光)?|照明灯|天花板灯')
_INCANDESCENT = re.compile(r'钨丝(?:灯)?(?:的)?(?:暖光|灯光|光)|钨丝灯')
_EMISSION = re.compile(r'暖光|灯光|照亮|照在|洒在|投射|发光|光晕|高光|亮起|点亮|勾勒|形成.*反光')
_EXTERNAL = re.compile(r'远处|室外|门外|街灯|路灯|窗光|窗外|夕阳|日光|阳光|霓虹|火焰')
_DOORS = re.compile(r'后门|前门|侧门|木门|大门')


def _narrative(row):
    director = row.get('director') or {}
    if director.get('blocks'):
        from .director_flow import ordered_content
        # Invalid edited dialogue must not be guessed into a completed event.
        try:
            text = '\n'.join(unit['text'] for unit in ordered_content(director)
                             if not (unit.get('speaker') or unit.get('dialogue_id')))
        except (ValueError, KeyError, TypeError, IndexError, StopIteration):
            return ''
    else:
        text = '\n'.join(line for line in str(row.get('text') or '').splitlines()
                         if not re.match(r'\s*(?:【D\d+】|[^：:\n]{1,20}[：:])', line))
    return _QUOTED.sub('', text)


def declared_lighting_states(slices, index):
    """Only explicit source events up to this segment, not inferred observations."""
    states = {}
    for number, row in enumerate(slices[:index + 1]):
        scene = str(row.get('scene_hint') or '')
        for clause in re.split(r'[，。；\n]', _narrative(row)):
            for match in _EVENT.finditer(clause):
                source = match['source'] or match['source_last']
                gap = match['gap'] if match['source'] else match['gap_first']
                if _UNREALIZED.search(clause[:match.end()]) or re.search(_SOURCE, gap or ''):
                    continue
                verb = match['verb'] or match['verb_first']
                key = (scene, '顶灯' if _CEILING.fullmatch(source) else source.replace('手电筒', '手电'))
                states[key] = dict(scene=scene, source=key[1], status='off' if re.fullmatch(_OFF, verb) else 'on',
                                   evidence=clause.strip(), source_index=number)
    current = str(slices[index].get('scene_hint') or '') if 0 <= index < len(slices) else ''
    linked = [current]
    if index > 0:
        previous = str(slices[index - 1].get('scene_hint') or '')
        shared_doors = set(_DOORS.findall(_narrative(slices[index - 1]))) & set(_DOORS.findall(_narrative(slices[index])))
        if current != previous and shared_doors:
            linked.append(previous)
    return dict(kind='declared_story_plan', current_scene=current, linked_scenes=linked, states=list(states.values()))


def _family(source):
    return '顶灯' if _CEILING.search(source) else source.replace('手电筒', '手电')


def validate_lighting_description(value, *, source_context, scene_facts, current_scene, channel):
    """Reject a contradicted physical light source; ordinary color style stays free."""
    if not value or not isinstance(source_context, dict):
        return value
    linked = set(source_context.get('linked_scenes') or [current_scene]) | {current_scene}
    states = [state for state in source_context.get('states', []) if state.get('scene') in linked]
    off = [state for state in states if state.get('status') == 'off']
    if not off:
        return value
    facts = str((scene_facts or {}).get('light') or '')
    other_warm = any(state.get('status') == 'on' and _family(state.get('source', '')) != '顶灯' for state in states)
    other_warm |= bool(re.search(r'(?:台灯|壁灯|蜡烛|油灯|火把)[^，。；]{0,8}(?:暖|黄|金|亮)', facts))
    for clause in re.split(r'[，。；\n]', value):
        if not _EMISSION.search(clause):
            continue
        # A simultaneous switch-off sound/description does not restore light.
        if re.search(_OFF, clause) and not re.search(_ON, clause):
            continue
        for state in off:
            source = state.get('source', '')
            ceiling = _family(source) == '顶灯'
            direct = bool(_CEILING.search(clause)) if ceiling else bool(re.search(re.escape(source) + r'(?:筒)?', clause))
            generic_indoor = ceiling and not other_warm and bool(re.search(r'室内(?:的)?暖光', clause) or
                (_INCANDESCENT.search(clause) and not _EXTERNAL.search(clause)))
            if direct or generic_indoor:
                raise ValueError('%s命中“%s”：剧本计划中%s的%s已熄灭，摄影补充应使用当前已给定光源；此记录不是实拍观察。' %
                                 (channel, clause.strip(), state['scene'], source))
            # A cold, dark room has no generic indoor warm source to invent.
            cold_dark = re.search(r'冷|青|蓝', facts) and re.search(r'暗|微光', facts) and not re.search(r'暖|黄|金', facts)
            if ceiling and state['scene'] == current_scene and cold_dark and not other_warm and '暖光' in clause and not _EXTERNAL.search(clause) and not re.search(r'手电|台灯|壁灯|蜡烛|油灯|火把', clause):
                raise ValueError('%s命中“%s”：当前室内只给定冷色微光，不能恢复未点亮的暖光。' % (channel, clause.strip()))
    return value


def safe_style_description(style, *, source_context, scene_facts, current_scene):
    """Remove conflicting physical-light clauses, preserving medium and finish."""
    kept = []
    for clause in re.split(r'[，,；;]', str(style or '')):
        if not clause.strip():
            continue
        try:
            validate_lighting_description(clause, source_context=source_context, scene_facts=scene_facts,
                                          current_scene=current_scene, channel='style_description')
        except ValueError:
            continue
        kept.append(clause.strip())
    return '，'.join(kept) or '自然的光色与影像层次。'
