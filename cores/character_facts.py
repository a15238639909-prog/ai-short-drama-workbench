"""Explicit user facts for automatic card creation."""
import re
import unicodedata

_NUMBER = r'(?:\d{1,4}|[0-9]{1,4})'
_AGE = _NUMBER + r'(?:\s*[-—~to]\s*' + _NUMBER + r')?'
_ROLE = r'(?:the|boy|girl|young\s+man|young\s+woman|elder|man|woman|brother|sister|master|teacher|doctor|owner|worker|manager)'
_PREFIX = re.compile(r'(?<![\d])(' + _AGE + r')\s*(?:age|years?\s*old)\s*(?:' + _ROLE + r'\s*)*$')
_SUFFIX = re.compile(r'^[\s(,:\s]*?(?:(?:m|f)[,\s]*)?(?:age|currently|is|is\s+)?[:\s]*(' + _AGE + r')\s*(?:age|years?\s*old)(?!\s+ago|before|after|of)')
_CN_NUMBER = r'(?:\d{1,4}|[零一二两三四五六七八九十百]{1,6})'
_CN_SUFFIX = re.compile(r'^[\s(,:]*?(?:年龄|今年|现年)?[:\s]*(' + _CN_NUMBER + r')\s*岁(?!前|后|时|那年)')
_CN_PREFIX = re.compile(r'(?<![\d零一二两三四五六七八九十百])(' + _CN_NUMBER + r')\s*岁(?:的)?(?:男|女|男性|女性)?\s*$')

def explicit_ages(source, names):
    source = unicodedata.normalize('NFKC', str(source or ''))
    names = sorted({str(n).strip() for n in names if str(n).strip()}, key=len, reverse=True)
    found = {}
    if not names:
        return found
    for hit in re.finditer('|'.join(re.escape(n) for n in names), source):
        name = hit.group()
        before = re.split(r'[,\.\;\!\?\n]', source[:hit.start()])[-1]
        previous = list(re.finditer('|'.join(re.escape(n) for n in names), before))
        prefix = _PREFIX.search(before)
        if prefix and previous and not before[previous[-1].end():prefix.start()].strip(' (,:'):
            prefix = None
        suffix = _SUFFIX.match(source[hit.end():])
        values = {re.sub(r'\s+', '', m.group(1)) for m in (prefix, suffix) if m}
        cn_suffix = _CN_SUFFIX.match(source[hit.end():])
        cn_prefix = _CN_PREFIX.search(before)
        if cn_prefix and previous and not before[previous[-1].end():cn_prefix.start()].strip(' (,:'):
            cn_prefix = None
        from .age_policy import _number
        values |= {str(_number(m.group(1))) for m in (cn_prefix, cn_suffix) if m and _number(m.group(1)) is not None}
        if name in found:
            values.add(found[name])
        if len(values) > 1:
            raise ValueError('%s has multiple explicit ages (%s), please confirm; original data unchanged' % (name, ' and '.join(sorted(values))))
        if values:
            found[name] = values.pop()
    return found

def lock_ages(cards, source):
    fixed = explicit_ages(source, [c.get('name', '') for c in cards if isinstance(c, dict)])
    return [dict(c, age=fixed[c['name']]) if c.get('name') in fixed else dict(c) for c in cards]


_INITIAL_LABEL = re.compile(r'(?:初始|开场|首次出场)(?:时的?|的)?(?:衣着|服装|装束|穿着)\s*(?:为|是|[：:])\s*(.*)')
_LATER_WARDROBE = re.compile(r'随后|后来|之后|接着|最终|最后|此时|这时|'
                              r'脱下|脱掉|褪下|褪去|解下|换上|换成|改穿|穿上|披上')
_CLOTHING_STATE = re.compile(r'撕破|破碎|破损|破烂|残破|烧焦|湿透|浸湿|湿漉|湿淋|湿贴|脏污|沾满泥')


def _first_character_context(source, name, names):
    """Only this person's first local appearance, before later wardrobe events."""
    source = str(source or '')
    hit = re.search(re.escape(name), source)
    if not hit:
        return ''
    tail = source[hit.end():]
    other = [match.start() for candidate in names if candidate != name
             for match in re.finditer(re.escape(candidate), tail)]
    end = min(other + [len(tail)])
    paragraph = re.split(r'\n\s*\n|[；;\n]', tail[:end], maxsplit=1)[0]
    return _LATER_WARDROBE.split(paragraph, maxsplit=1)[0]


def _garment_items(value):
    # Share the state chain's garment vocabulary, including 外袍 and 中衣.
    from . import source_wardrobe_plan as wardrobe
    result = []
    for piece in wardrobe._pieces(str(value or '')):
        piece = re.sub(r'^(?:(?:上身|下身|脚上|脚|身上)?(?:身穿|穿着|身着|穿)|'
                       r'(?:初始|开场)?(?:衣着|服装|装束)\s*(?:为|是|[：:]))\s*', '', piece).strip(' ，,：:（）()。')
        if wardrobe._GARMENT.search(piece) and piece not in result:
            result.append(piece)
    return result


def _context_clothes(context, name, names):
    from . import oral_story
    explicit = _INITIAL_LABEL.search(context)
    if explicit:
        return _garment_items(explicit[1])
    wear = re.search(r'(?:身穿|穿着|身着|衣着为|服装为|穿)([^。！？；;\n]*)', context)
    if wear:
        items = _garment_items(wear[1])
        if items:
            return items
    if re.match(r'^[\s（(，,:：]*(?:年龄|今年|现年)?\s*' + _CN_NUMBER + r'\s*岁', context):
        # Definition commas separate age/hair/role from garment nouns. The
        # wardrobe splitter intentionally keeps such commas in ordinary prose.
        return [item for part in re.split(r'[，,、]', context)
                if not re.search(r'拿着|抱着|捧着|手持|拎着|提着|挂在', part)
                for item in _garment_items(part)]
    # Existing fact extraction covers bare character definitions such as
    # 顾霄，45岁男性，深绿斗篷. Its temporal window is restricted here first.
    hints = oral_story.outfit_hints(name + context, names).get(name) or []
    return _garment_items('；'.join(hints))


def lock_initial_clothing(cards, source='', prose=''):
    """Lock supported opening clothes while leaving model-designed blanks free.

    Only new design dictionaries are returned. A transient marker keeps the
    subsequent card-creation path from re-extracting later story clothes.
    """
    from . import source_wardrobe_plan as wardrobe
    names = [str(card.get('name') or '') for card in cards if isinstance(card, dict) and card.get('name')]
    result = []
    for card in cards:
        fixed = dict(card)
        name = str(card.get('name') or '')
        user_context = _first_character_context(source, name, names)
        required = _context_clothes(user_context, name, names)
        opening = _first_character_context(prose, name, names)
        if not required and opening:
            required = _context_clothes(opening, name, names)
        states = [piece.strip(' ，,') for piece in re.split(r'[，,。；;]', opening)
                  if _CLOTHING_STATE.search(piece)
                  and (wardrobe._GARMENT.search(piece) or re.search(r'衣袖|衣摆|衣领|袖口', piece))]
        if not required and not states:
            result.append(fixed)
            continue
        slots = {wardrobe._slot(match[0]) for item in required for match in wardrobe._GARMENT.finditer(item)}
        complete = ('outfit' in slots or ('bottom' in slots and bool({'top', 'outerwear'} & slots)))
        designed = str(card.get('clothing') or '')
        if any(re.search(r'撕破|破碎|破损|破烂|残破|烧焦', state) for state in states):
            designed = re.sub(r'完好无损|完整无损|完好的?|崭新的?', '', designed)
        if any(re.search(r'湿透|浸湿|湿漉|湿淋|湿贴', state) for state in states):
            designed = re.sub(r'干爽的?|干燥的?', '', designed)
        pieces = list(required)
        for piece in wardrobe._pieces(designed):
            kinds = {wardrobe._slot(match[0]) for match in wardrobe._GARMENT.finditer(piece)}
            if kinds and not complete and not kinds.intersection(slots) and piece not in pieces:
                pieces.append(piece)
        pieces.extend(state for state in states if state not in pieces)
        fixed.update(clothing='；'.join(pieces), clothing_requirement='自定义',
                     clothing_from_brief=True, _initial_clothing_locked=True)
        # Every explicit opening item remains verbatim, in source order.
        cursor = -1
        for item in required:
            position = fixed['clothing'].find(item, cursor + 1)
            if position < 0:
                raise ValueError('首次出场服装事实未完整保留：' + name)
            cursor = position
        result.append(fixed)
    return result

def check_design_scale(cards, settings):
    scale = ' '.join(str((settings or {}).get(k) or '') for k in
                     ('genre', 'content_tendencies', 'custom_content_scale', 'custom_guidance'))
    if not re.search(r'adult|sexy|nsfw|nude|explicit', scale):
        return
    from .age_policy import _number, AgePolicyError
    for card in cards:
        raw = str(card.get('age') or '').strip()
        numbers = [_number(n) for n in re.findall(_NUMBER, raw)]
        if numbers:
            raise AgePolicyError('Retained explicit age %s for %s; switch to non-adult content to continue, system will not auto-adjust' % (raw, card.get('name')))

def check_source_scale(source, settings):
    ages = re.findall('(' + _AGE + r')\s*(?:age|years?\s*old)', str(source or ''))
    check_design_scale([{'name': 'source characters', 'age': age} for age in ages], settings)
