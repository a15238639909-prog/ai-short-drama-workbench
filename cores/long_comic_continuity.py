"""Deterministic panel state for version-two long comics."""
import copy
import re


FIELDS = ('clothing', 'held_items', 'injury', 'location')
LABELS = {'clothing': '穿戴', 'held_items': '持物', 'injury': '伤势', 'location': '位置'}
_EVENTS = {
    'clothing': r'脱下|脱掉|脱去|褪下|穿上|穿好|穿回|换上|换下|换成|换为|换好|披上|披好|披回|解开|解下|系上|系紧|扣上|摘下|戴上|戴好|取下|拉开|套上|裹上|卸下',
    'held_items': r'拿起|拿出|拿回|握住|接过|接住|递给|放下|放入|放回|放到|捡起|收起|丢下|丢掉|拾起|取出|背上|扛起|提起|抱起|交给|交出|拔出|插回|装入',
    'injury': r'受伤|划伤|擦伤|流血|包扎|擦破|恢复|愈合|止血|痊愈|击中|撞伤|割伤|刺伤|扭伤|摔伤|烧伤',
    'location': r'走到|走进|走出|走向|进入|来到|离开|到达|移到|移动|退到|退回|跑到|跑进|冲进|转到|站到|坐到|坐下|躺下|停在|落在|靠近|攀上|跃到|跨过',
}


def initial_state(characters):
    return {person['id']: dict(clothing=person.get('clothing') or '未知',
                              held_items='未知', injury='未知', location='未知') for person in characters}


def validate_state(state, characters):
    identities = {person['id'] for person in characters}
    if not isinstance(state, dict) or set(state) != identities:
        raise ValueError('漫画衔接状态须覆盖全部已确认人物')
    for value in state.values():
        if (not isinstance(value, dict) or set(value) != set(FIELDS)
                or any(not isinstance(item, str) or not item.strip() or len(item) > 1000 for item in value.values())):
            raise ValueError('漫画人物状态字段无效')
    return copy.deepcopy(state)


def source_evidence_context(story, page_number=None, chapter_number=None):
    """Use an explicit chapter/page slice; never borrow another numbered page."""
    if story is None:
        return ''
    if not isinstance(story, str):
        raise ValueError('原故事动作依据必须为文字')
    numeral = r'[一二三四五六七八九十两\d]+'
    def number(value):
        if value.isdecimal():
            return int(value)
        digits = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
                  '六': 6, '七': 7, '八': 8, '九': 9}
        if '十' in value:
            left, right = value.split('十', 1)
            return digits.get(left, 1) * 10 + digits.get(right, 0)
        return digits.get(value)
    if chapter_number is not None:
        chapters = list(re.finditer(r'(?:^|[\n。])\s*第(' + numeral + r')章(?=[：:\s]|$)', story))
        if chapters:
            chosen = next((index for index, match in enumerate(chapters) if number(match[1]) == chapter_number), None)
            if chosen is None:
                return ''
            story = story[chapters[chosen].end():chapters[chosen + 1].start() if chosen + 1 < len(chapters) else len(story)]
    if page_number is not None:
        pages = list(re.finditer(r'第(' + numeral + r')页', story))
        if pages:
            return '\n'.join(story[match.end():pages[index + 1].start() if index + 1 < len(pages) else len(story)]
                             for index, match in enumerate(pages) if number(match[1]) == page_number)
    return story


def _check_description_cast(description, selected, characters):
    occupied = []
    for person in sorted(characters, key=lambda row: len(str(row.get('name') or '')), reverse=True):
        for value in (person.get('name'), person['id']):
            if not isinstance(value, str) or len(value) < 2:
                continue
            before = r'(?<![A-Za-z0-9_])' if re.match(r'[A-Za-z0-9_]', value) else ''
            after = r'(?![A-Za-z0-9_])' if re.search(r'[A-Za-z0-9_]$', value) else ''
            for match in re.finditer(before + re.escape(value) + after, description, re.I):
                if any(match.start() < end and match.end() > start for start, end in occupied):
                    continue
                occupied.append(match.span())
                if person['id'] not in selected:
                    raise ValueError('本格画面提到人物“%s”，请将其加入本格出场人物，或从可见画面描述中移除' % value)


def _held_objects(value, characters):
    """Only normalize explicit object phrases, without guessing semantic aliases."""
    objects = set()
    value = re.sub(r'[（(](?:左|右|双|两)手[）)]', '', value)
    for part in re.split(r'[,，、;；\n]|(?=(?:左手|右手|双手|两手))', value):
        part = part.strip()
        for person in characters:
            for name in (person.get('name'), person['id']):
                if name and part.startswith(name):
                    part = part[len(name):].lstrip('：: ')
        prefix = (r'^(?:(?:左|右|双|两)手|手中|手里|怀中|肩上|手握|'
                  r'拿着|握着|持有|持着|拿住|握住|提着|抱着|背着|扛着|携带着?|'
                  r'仍然?|继续|已经?|正在?|紧紧|牢牢|稳稳|轻轻|各自|分别|用|在|的|地|[:：\s])+')
        part = re.sub(prefix, '', part)
        part = re.sub(r'^(?:另(?:外)?一|[一二两三四五六七八九十\d]+)\s*(?:把|个|支|本|张|件|只|枚|条|根|盏|台|套)', '', part)
        part = part.strip(' 。.：:')
        if (not part or re.fullmatch(r'(?:未知|unknown|none|无|空|空空|空着|空手|双手空空|两手空空|无物|没有物品|未持物|未拿物品|空无一物)', part, re.I)
                or part.startswith(('没有持', '没有拿', '未持有', '无持物', '无物品'))):
            continue
        objects.add(re.sub(r'\s+', '', part).casefold())
    return objects


def _multiple_objects_evidence(description, item):
    count = r'各自|各(?:拿|握|持|接)|分别|另一(?:把|个|支|本|张|件|只|枚|条|根|盏|台|套)|[两二三四五六七八九十2-9]\s*(?:把|个|支|本|张|件|只|枚|条|根|盏|台|套)'
    return any(item in re.sub(r'\s+', '', clause).casefold() and re.search(count, clause)
               and re.search(_EVENTS['held_items'], clause)
               for clause in re.split(r'[。！？；;\n]', description))


def _check_held_items(before, after, panel, characters, evidence_context=None):
    def owners(state):
        result = {}
        for identity, values in state.items():
            for item in _held_objects(values['held_items'], characters):
                result.setdefault(item, set()).add(identity)
        return result
    old, new = owners(before), owners(after)
    changed = {row['character_id'] for row in panel['state_changes'] if row['field'] == 'held_items'}
    for item, holders in new.items():
        previous = old.get(item, set())
        if holders == previous:
            continue  # 合法多件物品的已保存持有状态继续接续，不需每格重新拿起。
        if _multiple_objects_evidence(panel['description'] + '\n' + (evidence_context or ''), item):
            continue
        if previous and holders - previous and not (previous | holders) <= changed:
            raise ValueError('物品“%s”交接须在同格同时更新原持有人和接收者的held_items，原持有人须移除该物品' % item)
        if len(holders) > 1:
            raise ValueError('物品“%s”不能同时由多个人持有；交接须移除原持有人状态，多件同类物品须有本格明确数量或各自持有依据' % item)


def panels(value, characters, text_validator, *, evidence_context=None):
    if not isinstance(value, list) or not 1 <= len(value) <= 4:
        raise ValueError('每页须为1至4格')
    identities = {person['id'] for person in characters}
    result = []
    for number, panel in enumerate(value, 1):
        if not isinstance(panel, dict) or panel.get('number') != number:
            raise ValueError('格编号须从1连续')
        selected = panel.get('character_ids')
        if (not isinstance(selected, list) or not all(isinstance(identity, str) for identity in selected)
                or len(set(selected)) != len(selected) or not set(selected) <= identities):
            raise ValueError('每格出场人物须为不重复的已有人物ID数组')
        description = text_validator(panel.get('description', ''), '分格画面', 1800, True)
        _check_description_cast(description, selected, characters)
        lines = panel.get('dialogue')
        if not isinstance(lines, list) or len(lines) > 6:
            raise ValueError('每格对白须为数组，最多6句')
        dialogue = []
        for line in lines:
            if not isinstance(line, dict) or line.get('speaker') not in selected:
                raise ValueError('对白人物必须属于本格出场人物')
            dialogue.append(dict(speaker=line['speaker'], text=text_validator(line.get('text', ''), '对白', 180, True)))
        changes = panel.get('state_changes', [])
        if not isinstance(changes, list) or len(changes) > 48:
            raise ValueError('状态变更须为数组，每格最多48项')
        normalized, changed = [], set()
        for change in changes:
            if not isinstance(change, dict) or change.get('character_id') not in selected:
                raise ValueError('状态变更人物必须属于本格出场人物')
            field = change.get('field')
            if not isinstance(field, str) or field not in FIELDS:
                raise ValueError('状态字段只支持clothing、held_items、injury、location')
            identity = change['character_id']
            if (identity, field) in changed:
                raise ValueError('同格同一人物的同一状态只能变更一次')
            evidence = text_validator(change.get('evidence', ''), '状态变更依据', 1000, True)
            if ((evidence not in description and (not evidence_context or evidence not in evidence_context))
                    or not re.search(_EVENTS[field], evidence)):
                raise ValueError('状态变更须逐字引用本格画面或本页原故事中与该字段有关的动作依据')
            normalized.append(dict(character_id=identity, field=field,
                value=text_validator(change.get('value', ''), '状态变更结果', 1000, True), evidence=evidence))
            changed.add((identity, field))
        result.append(dict(number=number, character_ids=list(selected), description=description,
            dialogue=dialogue, narration=text_validator(panel.get('narration', ''), '旁白', 240), state_changes=normalized))
    return result


def recalculate(pages, characters, opening, text_validator, *, evidence_context=None):
    state = validate_state(opening, characters)
    result = copy.deepcopy(pages)
    for page in result:
        page['opening_state'] = copy.deepcopy(state)
        context = source_evidence_context(evidence_context, page['number'])
        page['panels'] = panels(page['panels'], characters, text_validator, evidence_context=context)
        selected = []
        updates = {}
        for panel in page['panels']:
            panel['opening_state'] = copy.deepcopy(state)
            selected.extend(identity for identity in panel['character_ids'] if identity not in selected)
            for change in panel['state_changes']:
                state[change['character_id']][change['field']] = change['value']
                if change['field'] == 'clothing':
                    updates[change['character_id']] = change['value']
            _check_held_items(panel['opening_state'], state, panel, characters, context)
            panel['ending_state'] = copy.deepcopy(state)
        page['character_ids'] = selected
        page['ending_state'] = copy.deepcopy(state)
        page['wardrobe'] = {identity: page['opening_state'][identity]['clothing'] for identity in selected}
        page['wardrobe_updates'] = updates
    return result, state


def compile_prompt(project, page):
    from . import moss_comic_layout
    return moss_comic_layout.compile_prompt(project, page)
