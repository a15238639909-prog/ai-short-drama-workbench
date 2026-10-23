"""Conservative text checks for mutually exclusive views within one shot.

This module checks authoring instructions, never generated pixels or anatomy.
Unresolved subjects and views with reflections are deliberately left alone.
"""
import re


_SHOT = re.compile(r'\[\s*Shot\s+(\d+)\s*\]', re.I)
_CLAUSE = re.compile(r'[^，,。；;\r\n]+')
_QUOTED = re.compile(r'“[^”\r\n]*”|「[^」\r\n]*」|『[^』\r\n]*』|"[^"\r\n]*"')
_REFLECTION = re.compile(r'镜子|镜面|倒影|反射|分屏|画中画')
_OTHER_PERSON = re.compile(r'另一(?:名|位|个)?(?:人|女子|男子|女孩|男孩|少年|少女)|其他人|旁人')
_PRONOUN = re.compile(r'[他她它]')
_NEGATION = re.compile(r'(?:不再|没有|并未|未曾|并不|不是|不要|避免|禁止|无需|不应|不能|未|不)[^，,。；;\r\n]{0,6}$')
_BACK = re.compile(r'背对(?:着)?镜头|背向(?:着)?镜头')
_TURN = re.compile(r'转过身|转身|回过头|回头|扭头|转头|侧过脸|转过脸|转向镜头|面向镜头|'
                   r'(?:镜头|机位).{0,12}(?:绕至|绕到|移至|移到|转至|转到|切至|切到).{0,8}(?:正面|前方|侧面|侧前方)')
_FACE = re.compile(r'(?:正脸|面部|脸部|五官|双眼|瞳孔|眼眸|眼睛|眼神|嘴唇|口型).{0,8}'
                   r'(?:清晰可见|清楚可见|清晰呈现|清楚呈现|特写)|'
                   r'(?:特写|近景).{0,8}(?:正脸|双眼|瞳孔|眼眸|眼睛|眼神|嘴唇|口型)|'
                   r'睫毛.{0,18}(?:脸颊|面颊).{0,12}(?:阴影|影子)')


def _alias_pattern(value):
    pattern = re.escape(value)
    if re.match(r'[A-Za-z0-9_]', value):
        pattern = r'(?<![A-Za-z0-9_])' + pattern
    if re.search(r'[A-Za-z0-9_]$', value):
        pattern += r'(?![A-Za-z0-9_])'
    return pattern


def _identities(references):
    identities = []
    for index, reference in enumerate(references):
        if not isinstance(reference, dict) or reference.get('role') != 'character':
            continue
        aliases = [_alias_pattern(str(reference[field]).strip())
                   for field in ('name', 'character_id', 'id')
                   if reference.get(field) and str(reference[field]).strip()]
        subject = reference.get('subject')
        if type(subject) is int and subject > 0:
            aliases.append(r'<\s*Subject\s*%d\s*>' % subject)
        if aliases:
            identities.append(dict(key=index,
                label=str(reference.get('name') or reference.get('character_id') or subject),
                aliases=re.compile('|'.join(aliases), re.I)))
    return identities


def _mentions(text, identities):
    return [identity for identity in identities if identity['aliases'].search(text)]


def assert_shot_consistency(text, references=()):
    """Reject a resolved subject's incompatible visible face in a back view.

    Reference identities resolve names, Subject labels and unambiguous single
    subject pronouns. Each Shot starts with an unknown view. A turn or a camera
    move can reveal the face; speech alone does not require a visible face.
    """
    if not isinstance(text, str):
        raise ValueError('镜头正文须为字符串')
    identities = _identities(references)
    if not identities:
        return
    shots = list(_SHOT.finditer(text))
    for index, shot in enumerate(shots):
        stop = shots[index + 1].start() if index + 1 < len(shots) else len(text)
        original = text[shot.end():stop]
        body = _QUOTED.sub(lambda match: ' ' * len(match.group()), original)
        if _REFLECTION.search(body):
            continue
        named = _mentions(body, identities)
        fallback = named[0] if len(named) == 1 else identities[0] if len(identities) == 1 else None
        if _OTHER_PERSON.search(body) or len(set(_PRONOUN.findall(body))) > 1:
            fallback = None
        back_views = {}
        for clause_match in _CLAUSE.finditer(body):
            clause = clause_match.group()
            mentioned = _mentions(clause, identities)
            subject = mentioned[0] if len(mentioned) == 1 else fallback if not mentioned else None
            if subject is None:
                continue
            events = [(match.start(), 'back', match) for match in _BACK.finditer(clause)]
            events.extend((match.start(), 'turn', match) for match in _TURN.finditer(clause))
            events.extend((match.start(), 'face', match) for match in _FACE.finditer(clause))
            for _, kind, match in sorted(events, key=lambda event: event[0]):
                negated = _NEGATION.search(clause[:match.start()]) is not None
                if kind == 'back':
                    if negated:
                        if re.search(r'不再\s*$', clause[:match.start()]):
                            back_views.pop(subject['key'], None)
                    else:
                        back_views[subject['key']] = original[clause_match.start():clause_match.end()].strip()
                elif kind == 'turn':
                    if not negated and not re.search(r'不|未', match.group()):
                        back_views.pop(subject['key'], None)
                elif not negated and subject['key'] in back_views:
                    face_clause = original[clause_match.start():clause_match.end()].strip()
                    error = ValueError('第%s镜人物%s仍背对镜头，却要求可见面部细节；'
                        '请说明真实转头、转身或机位变化：%s；%s' %
                        (shot[1], subject['label'], back_views[subject['key']], face_clause))
                    error.evidence = dict(shot=int(shot[1]), subject=subject['label'],
                        back_clause=back_views[subject['key']], face_clause=face_clause)
                    raise error
