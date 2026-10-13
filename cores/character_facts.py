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
