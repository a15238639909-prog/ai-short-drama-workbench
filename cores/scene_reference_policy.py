"""Select a set image at each planned scene entrance without rewriting a story.

The timeline, rather than completed videos, determines an entrance.  Reference
transport uses these helpers only for new requests; frozen requests are owned by
the renderer's existing recovery rules.
"""
import re


VERSION = 'scene-entry-only-v1'
_PICTURE = re.compile(r'<Picture\s+(\d+)>')
_SUBJECT = r'<Subject\s*(\d+)>'
_FIELDS = ('subject_definitions', 'summary', 'retention_analysis',
           'detailed_description', 'overall_soundscape', 'non_diegetic_music')


def is_scene_only(segment):
    """Mirror the explicit empty-cast cases used by the main reference pack.

    Camera-owner filtering still belongs to authoring.h3_char_order.  An absent
    cast on an old row is not evidence that this row is an empty establishing shot.
    """
    row = segment or {}
    empty = not row.get('chars')
    return bool(row.get('no_chars') or (row.get('shot') and empty)
                or ('chars' in row and empty
                    and (row.get('plan') or {}).get('flow') == 'source-v1'))


def _scene_hint(segment, scene):
    for source, keys in ((segment, ('scene', 'location', 'scene_hint')),
                         (scene, ('scene_hint', 'location', 'name', 'scene'))):
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ''


def _scene_key(sid, ep, segment, scene, cards):
    from . import reference_ready
    hint = _scene_hint(segment, scene)
    if not hint:
        return None
    card = reference_ready.resolve_scene_card(sid, ep, hint, cards)
    if card and card.get('scene_id'):
        return ('id', str(card['scene_id']))
    # Exact explicit names are useful for legacy rows without a catalog.  A
    # camera-position alias cannot accidentally match another name by substring.
    return ('name', hint)


def _rows(timeline):
    rows = []
    for scene in (timeline or {}).get('scenes') or []:
        if not isinstance(scene, dict):
            continue
        segments = [row for row in scene.get('segments') or [] if isinstance(row, dict)]
        try:
            numbers = [int(row['no']) for row in segments]
            if len(set(numbers)) == len(numbers):
                segments = sorted(segments, key=lambda row: int(row['no']))
        except (KeyError, TypeError, ValueError):
            pass  # A legacy plan without unique numbers retains its explicit order.
        rows.extend((scene, row) for row in segments)
    return rows


def include_scene(sid, g, ep=None, timeline=None):
    """True on A,A,B,B,A entrances: True,False,True,False,True.

    No video, frame, completion status or model output participates.  A missing
    row/location, ambiguous copied row or unavailable timeline keeps its scene
    image conservatively.  A supplied timeline prevents a second timeline read.
    """
    if not isinstance(g, dict):
        return True
    from . import asset_core, saga_core
    if timeline is None:
        timeline = saga_core.ep_timeline(sid, int(ep or 1))
    rows = _rows(timeline)
    positions = [index for index, (_, row) in enumerate(rows) if row is g]
    cards = asset_core.list_assets(sid, 'scenes') or []
    if not positions:
        for identity in ('segment_id', 'clip_id', 'id', 'uid'):
            if g.get(identity) is not None:
                positions = [index for index, (_, row) in enumerate(rows)
                             if row.get(identity) == g[identity]]
                if positions:
                    break
    if not positions and g.get('no') is not None:
        target = _scene_key(sid, ep, g, {}, cards)
        positions = [index for index, (scene, row) in enumerate(rows)
                     if str(row.get('no')) == str(g['no'])
                     and (target is None or _scene_key(sid, ep, row, scene, cards) == target)]
    if len(positions) != 1 or positions[0] == 0:
        return True
    index = positions[0]
    scene, current = rows[index]
    previous_scene, previous = rows[index - 1]
    current_key = _scene_key(sid, ep, current, scene, cards)
    previous_key = _scene_key(sid, ep, previous, previous_scene, cards)
    return current_key is None or previous_key is None or current_key != previous_key


def _definition_region(prompt):
    headers = list(re.finditer(r'(?m)^(' + '|'.join(_FIELDS) + r'):[ \t]*', prompt))
    if headers:
        definitions = [match for match in headers if match[1] == 'subject_definitions']
        if len(definitions) != 1:
            return None
        match = definitions[0]
        end = next((item.start() for item in headers if item.start() > match.start()), len(prompt))
        return match.end(), end
    return 0, len(prompt)


def strip_scene_images(prompt, removed_picture_numbers):
    """Remove only proven scene-image bindings, retaining environment Subjects.

    The caller removes the same one-based image slots from the transport list.
    Surviving Picture numbers close the gaps.  Removed scene Picture references
    elsewhere become their proven environment Subject; action words, dialogue
    text and Subject numbers stay intact.  A custom/character/ambiguous binding
    raises ValueError so the caller can retain its complete original image pack.
    This function does not mutate the source prompt or accept guessed identities.
    """
    removed = {int(number) for number in removed_picture_numbers}
    if any(number < 1 for number in removed):
        raise ValueError('场景图片编号必须从1开始')
    if not removed:
        return prompt
    if not isinstance(prompt, str):
        raise ValueError('场景引用提示词必须是文字')
    region = _definition_region(prompt)
    if region is None:
        raise ValueError('缺少可确认的场景主体定义，保留完整参考图')
    start, end = region
    definitions = prompt[start:end]
    bindings, rewritten = {}, []
    english = re.compile(r'^(\s*' + _SUBJECT + r'\s+is\s+.+?,\s+the environment)'
                         r'\s+from\s+<Picture\s+(\d+)>([.。]?)(.*)$')
    chinese = re.compile(r'^(\s*' + _SUBJECT + r'\s*是\s*)'
                         r'<Picture\s+(\d+)>([^\n。]*?场景)(.*)$')
    for line in definitions.splitlines(keepends=True):
        plain = line.rstrip('\r\n')
        ending = line[len(plain):]
        match = english.match(plain)
        if match and int(match[3]) in removed:
            number, subject = int(match[3]), int(match[2])
            changed = match[1] + (match[4] or '.') + match[5]
        else:
            match = chinese.match(plain)
            if match and int(match[3]) in removed:
                number, subject = int(match[3]), int(match[2])
                tail = match[5]
                # This exact generated suffix describes the removed picture,
                # not the story's set.  Preserve any other authored set details.
                tail = re.sub(r'^[，,]\s*保留参考图中的空间结构、材质、光线和色调[。.]?', '。', tail)
                changed = match[1] + match[4] + (tail or '。')
            else:
                rewritten.append(line)
                continue
        if number in bindings and bindings[number] != subject:
            raise ValueError('同一场景图片绑定多个主体，保留完整参考图')
        bindings[number] = subject
        rewritten.append(changed + ending)
    if set(bindings) != removed:
        raise ValueError('待移除图片没有明确场景绑定，保留完整参考图')
    # An image cannot supply both a character and a scene merely because its
    # numeric slot happens to match; reject that conflicting custom request.
    for line in definitions.splitlines():
        if not re.match(r'^\s*' + _SUBJECT, line) or '<d>' in line:
            continue
        if any(int(number) in removed for number in _PICTURE.findall(line)):
            known = english.match(line) or chinese.match(line)
            if not known or int(known[3]) not in removed:
                raise ValueError('待移除图片同时绑定非场景主体，保留完整参考图')
    updated = prompt[:start] + ''.join(rewritten) + prompt[end:]
    # Dialogue is an immutable source contract.  Literal reference tokens there
    # cannot be safely changed or interpreted as a scene binding.
    dialogue = re.compile(r'<d>.*?</d>', re.S)
    if any(_PICTURE.search(match[0]) for match in dialogue.finditer(updated)):
        raise ValueError('对白含图片标记，保留完整参考图和对白原文')
    def replace(match):
        number = int(match[1])
        if number in removed:
            return '<Subject %d>' % bindings[number]
        return '<Picture %d>' % (number - sum(old < number for old in removed))
    return _PICTURE.sub(replace, updated)
