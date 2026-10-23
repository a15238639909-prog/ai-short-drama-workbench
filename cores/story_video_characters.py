"""Four-field characters, exact source dialogue and final voice binding.

No model, network, image conversion, media selection or production mutation.
The only creative instruction here is a generic, duration-derived prose
addendum. Final voice descriptions are bound by identity after authoring.
"""
import copy
import math
from pathlib import Path
import re

from . import episode_contract


MAX_REFERENCES = 8
SOURCE_VERSION = 'story-video-character-source-v1'


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + '须为非空字符串')
    return value.strip()


def _path(value, label):
    if isinstance(value, Path):
        value = str(value)
    return _text(value, label)


def normalize_cards(rows):
    """Normalize four user fields plus an optional internal ID; never write files.

    Input: image_path (or image/path), name, personality, voice; optional id.
    Output: [{id, image_path, name, personality, voice}], preserving input order.
    IDs are allocated once from this input order, not regenerated from names.
    Existing IDs are preserved; the caller owns saving/reusing this result.
    Voice accepts a nonempty Chinese description, rather than a fixed UI enum.
    """
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_REFERENCES:
        raise ValueError('须提供1～8张人物卡')
    cards = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError('人物卡须为对象')
        image = next((row[key] for key in ('image_path', 'image', 'path')
                      if row.get(key)), None)
        identity = _text(row.get('id') or row.get('char_id') or 'char_%02d' % index, '人物ID')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', identity):
            raise ValueError('人物ID须为稳定的字母、数字、下划线或连字符标识')
        voice = _text(row.get('voice'), '声音描述')
        if not re.search(r'[\u4e00-\u9fff]', voice):
            raise ValueError('声音描述须为中文')
        if re.search(r'<(?:/?d\b|Subject\b|Picture\b)|\{\{|\(S\d+\)', voice, re.I):
            raise ValueError('声音描述不能包含参考、对白或说话人绑定标签')
        cards.append(dict(id=identity, image_path=_path(image, '人物图路径'),
            name=_text(row.get('name'), '人物名字'),
            personality=_text(row.get('personality'), '人物性格'), voice=voice))
    owners = {}
    for card in cards:
        for label in (card['id'], card['name']):
            if label in owners and owners[label] != card['id']:
                raise ValueError('人物名字与ID必须可唯一匹配：' + label)
            owners[label] = card['id']
    if len({card['id'] for card in cards}) != len(cards) or len({card['name'] for card in cards}) != len(cards):
        raise ValueError('人物ID与名字不能重复')
    return cards


def authoring_characters(cards):
    """Stable identity/personality only. Voice and file paths never reach UW."""
    return [{key: card[key] for key in ('id', 'name', 'personality')}
            for card in normalize_cards(cards)]


def _identity_index(cards):
    normalized = normalize_cards(cards)
    return normalized, {label: card['id'] for card in normalized
                        for label in (card['id'], card['name'])}


def _resolve_speaker(value, aliases):
    if not isinstance(value, str) or value not in aliases:
        raise episode_contract.ContractError('说话者不能唯一匹配固定人物卡，已保留来源：' + str(value),
            code='speaker_unresolved', details={'speaker': value})
    return aliases[value]


def references_for_segment(cards, scene=None, previous_frame=None):
    """First segment: characters+optional scene; later: characters+real previous frame.

    scene may be a path or {path/image_path, name}. previous_frame may be a
    path or an adopted row with anchor_path (also accepts path/image_path).
    This function only binds supplied material; it cannot select a video frame.
    """
    normalized = normalize_cards(cards)
    refs = [dict(role='character', picture=number, subject=number,
                 character_id=card['id'], name=card['name'], path=card['image_path'])
            for number, card in enumerate(normalized, 1)]
    number = len(refs) + 1
    if previous_frame is not None:
        path = previous_frame
        if isinstance(previous_frame, dict):
            path = next((previous_frame[key] for key in ('anchor_path', 'path', 'image_path')
                         if previous_frame.get(key)), None)
        refs.append(dict(role='previous_frame', picture=number,
                         path=_path(path, '真实末帧路径')))
    elif scene is not None:
        path, name = scene, ''
        if isinstance(scene, dict):
            path = scene.get('path') or scene.get('image_path')
            name = str(scene.get('name') or '')
        refs.append(dict(role='scene_anchor', picture=number,
                         path=_path(path, '首段场景路径'), name=name))
    if not 1 <= len(refs) <= MAX_REFERENCES:
        raise ValueError('人物、场景或真实末帧参考合计须为1～8张')
    return refs


_FRONT_SAY = (r'(?:说道|问道|答道|喊道|笑道|叹道|应道|回道|低语|'
              r'回答|反问|回应|解释|补充|嘟囔|嘀咕|惊呼|轻呼|大喊|'
              r'说|问|答|喊|吼|嚷)')
_FRONT_SAY_END = re.compile(_FRONT_SAY + r'[：:，,]?\s*$')
_FRONT_LINK = re.compile(r'^(?:(?:这时|此时|随后|接着|忽然|突然|于是|随即|只见|只听|而|但|却)\s*)*')
# An unnamed comma clause may continue a named subject only when it starts
# with a clear action/adverb. A new noun or pronoun does not inherit a speaker.
_FRONT_ACTION = re.compile(r'^(?:(?:正要|正|又|也|便|就|才|仍|还|随即|随手|连忙|急忙|立刻|'
                           r'轻声|低声|大声|高声|小声|笑着|哭着|轻轻|慢慢|终于|只|一边)\s*)*'
                           r'(?:' + _FRONT_SAY + r'|看|望|瞄|握|抓|拿|举|放|收|伸|转|回|点|摇|'
                           r'抬|低|拉|推|拍|擦|甩|挥|把|将|走|跑|退|停|蹲|站|坐|抿|咬|吸|叹|笑|摆)')
_FRONT_NONCURRENT = re.compile(
    r'(?:没(?:有|能|敢)?|未曾|不曾|并未|从未|未|不肯|不愿|不想|想要|正要|准备|打算|险些|差点)'
    r'(?:开口|出声|大声|高声|低声|轻声|小声|地|\s)*' + _FRONT_SAY + r'[：:，,]?\s*$'
    r'|(?:回忆里|回忆中|记忆里|记忆中|心里|心中|心底|默念|暗想|腹诽)')


def _front_speech_evidence(source, quote_pos, names):
    """Read explicit pre-quote grammar, never a nearest-name/distance guess.

    Names in an action's object (甲看着乙的手) cannot replace its subject.
    A new named subject at a comma boundary can replace it (甲举剑，乙招手，笑道).
    This deliberately narrow pass never classifies a quotation as speech.
    """
    if source[quote_pos:quote_pos + 1] not in ('“', '「'):
        return None
    boundary = list(re.finditer(r'[。！？!?；;\r\n”」]', source[:quote_pos]))
    start = boundary[-1].end() if boundary else 0
    prefix = source[start:quote_pos]
    if not _FRONT_SAY_END.search(prefix):
        return None
    if _FRONT_NONCURRENT.search(prefix):
        return dict(blocked=True, text=prefix, start=start, end=quote_pos)
    name_pattern = re.compile('|'.join(re.escape(name) for name in sorted(names, key=len, reverse=True)))
    subject, subject_start = None, None
    for clause in re.finditer(r'[^，,：:]+', prefix):
        text = clause.group().lstrip()
        leading = len(clause.group()) - len(text)
        link = _FRONT_LINK.match(text)
        text = text[link.end():]
        match = name_pattern.match(text)
        if match:
            rest = text[match.end():]
            # Possession/groups and a named object of a preposition are not
            # unambiguous singular human subjects of the following speech.
            if not rest or re.match(r'^(?:的|们|和|与|及|、|被|让|叫|请|听见|听到)', rest):
                subject = None
            else:
                subject = match.group()
                subject_start = start + clause.start() + leading + link.end()
        elif not _FRONT_ACTION.match(text):
            subject = None
    if not subject:
        return None
    return dict(speaker=subject, text=source[subject_start:quote_pos],
                start=subject_start, end=quote_pos)


def _prefer_explicit_front_speakers(source, numbered, report, names):
    """Correct only existing spoken rows; retain raw classifier and source bytes."""
    original = copy.deepcopy(report)
    report = copy.deepcopy(report)
    audit, corrected = [], set()
    for row in report.get('dialogue', []):
        pos = row.get('pos')
        if not isinstance(pos, int):
            continue
        proof = _front_speech_evidence(source, pos, names)
        if not proof:
            continue
        if proof.get('blocked'):
            report['issues'].append(dict(code='source_not_current_speech',
                dialogue_id=row['id'], pos=pos, evidence=proof))
            continue
        before = row['speaker']
        row['speaker'], row['evidence'] = proof['speaker'], proof['text']
        row['front_attribution'] = dict(source_start=proof['start'], source_end=proof['end'],
                                       quote_pos=pos, rule='explicit_front_subject_and_speech')
        corrected.add(row['id'])
        audit.append(dict(dialogue_id=row['id'], original_speaker=before,
            speaker=row['speaker'], changed=before != row['speaker'],
            evidence=proof['text'], source_start=proof['start'], source_end=proof['end'], quote_pos=pos))
        numbered = re.sub(r'^(【D' + str(row['id']) + r'】)' + re.escape(before) + '：',
                          lambda match: match[1] + row['speaker'] + '：', numbered, count=1, flags=re.M)
    report['issues'] = [issue for issue in report.get('issues', [])
                        if not (issue.get('code') == 'speaker_unresolved' and issue.get('dialogue_id') in corrected)]
    report['original_classifier_report'] = original
    report['speaker_attribution_audit'] = audit
    return numbered, [(row['speaker'], row['text']) for row in report['dialogue']], report


_UNQUOTED_BOUNDARY = re.compile(r'[。！？!?；;，,\r\n]')
_UNQUOTED_EXCLUDED_CONTEXT = re.compile(
    r'心里|心中|心底|默念|暗想|腹诽|回忆里|回忆中|记忆里|记忆中|'
    r'纸上|纸条上|信上|书上|墙上|门上|牌上|写着|写下|刻着|印着|标着|贴着|'
    r'如果|假如|倘若|也许|可能|希望|打算|准备|正要|将要|将会|以后|明天|'
    r'没(?:有|能|敢)?|未曾|不曾|并未|从未|未|不肯|不愿|不想|不会|不再|不能|别|不要')
_UNQUOTED_INDIRECT = re.compile(r'^(?:了|过|着|话|书|法|者|的话|的是|起来|起了|到了|到过|服|明了|问题|那个问题|这个问题)')


def _unquoted_source_rows(source, report, names):
    """Opt-in, narrow direct-speech extraction with exact source offsets.

    A punctuation boundary ends one candidate; following clauses are never
    guessed to belong to it. Existing quoted/script speech stays classifier
    owned. Unsupported attribution is evidence only, never an invented voice.
    """
    protected = [(match.start(), match.end()) for match in re.finditer(
        r'“[^”\n]*”|「[^」\n]*」|‘[^’\n]*’|\"[^\"\n]*\"', source)]
    for row in report.get('dialogue', []):
        pos = row.get('pos')
        if isinstance(pos, int) and source[pos:pos + 1] not in ('“', '「'):
            end = source.find('\n', pos)
            protected.append((pos, end if end >= 0 else len(source)))
    verified, unresolved, audit = [], [], []
    for match in re.finditer(_FRONT_SAY, source):
        if any(start <= match.start() < end for start, end in protected):
            continue
        start = match.end()
        separator = re.match(r'[：:]?[ \t]*', source[start:])
        start += separator.end()
        if start >= len(source) or source[start] in '“「‘\"：:':
            continue
        boundary = _UNQUOTED_BOUNDARY.search(source, start)
        end = boundary.start() if boundary else len(source)
        while end > start and source[end - 1].isspace():
            end -= 1
        if start == end:
            continue
        sentence_start = max((m.end() for m in re.finditer(
            r'[。！？!?；;\r\n”」]', source[:match.start()])), default=0)
        prefix = source[sentence_start:start]
        text = source[start:end]
        entry = dict(text=text, text_start=start, text_end=end,
                     source_start=sentence_start, source_end=end,
                     evidence=source[sentence_start:end], rule='explicit_unquoted_speech')
        # Do not turn writing, thoughts, intention, negation, or reported speech
        # into verified spoken lines. These exclusions are retained for audit.
        grammar = prefix
        for name in sorted(names, key=len, reverse=True):
            grammar = grammar.replace(name, '人物')
        if _UNQUOTED_EXCLUDED_CONTEXT.search(grammar) or _UNQUOTED_INDIRECT.match(text):
            audit.append(dict(entry, status='excluded', reason='not_verified_current_direct_speech'))
            protected.append((start, end))
            continue
        proof = _front_speech_evidence(source[:start] + '“' + source[start:], start, names)
        if proof and not proof.get('blocked'):
            row = dict(entry, speaker=proof['speaker'], pos=start,
                       attribution_start=proof['start'], attribution_end=start)
            verified.append(row)
            audit.append(dict(row, status='verified'))
            protected.append((start, end))
        else:
            unresolved.append(dict(entry, status='unknown', reason='speaker_unresolved'))
            audit.append(dict(entry, status='unknown', reason='speaker_unresolved'))
            protected.append((start, end))
    return verified, unresolved, audit


def _continuing_source_contract(prose, numbered, report, normalized, aliases):
    """Keep usable evidence and quarantine uncertainty without fabricating it."""
    report = copy.deepcopy(report)
    original_numbered = numbered
    extra, unresolved, audit = _unquoted_source_rows(
        prose, report, [card['name'] for card in normalized])
    invalid_ids = {issue.get('dialogue_id') for issue in report.get('issues', [])}
    verified = []
    for row in report.get('dialogue', []):
        pos = row.get('pos')
        uncertain_front = False
        if isinstance(pos, int) and prose[pos:pos + 1] in ('“', '「'):
            start = max((m.end() for m in re.finditer(r'[。！？!?；;\r\n”」]', prose[:pos])), default=0)
            prefix = prose[start:pos]
            proof = _front_speech_evidence(prose, pos, [card['name'] for card in normalized])
            uncertain_front = bool(_FRONT_SAY_END.search(prefix) and
                                   (not proof or proof.get('blocked')))
        if uncertain_front or row.get('id') in invalid_ids or row.get('speaker') not in aliases:
            unresolved.append(dict(copy.deepcopy(row), status='unknown', reason='speaker_unresolved'))
            if row.get('id') not in invalid_ids:
                report.setdefault('issues', []).append(dict(code='speaker_unresolved',
                    dialogue_id=row.get('id'), pos=pos, rule='unverified_front_attribution'))
        else:
            verified.append(copy.deepcopy(row))
    verified.extend(extra)
    verified.sort(key=lambda row: row.get('pos', 0))
    rows = [dict(speaker_id=aliases[row['speaker']], speaker=row['speaker'], text=row['text'])
            for row in verified]
    report['evidence_policy'] = 'repair-then-continue-source-v1'
    report['original_numbered_source'] = original_numbered
    report['unquoted_attribution_audit'] = audit
    report['verified_dialogue'] = verified
    report['unresolved_sources'] = copy.deepcopy(unresolved)
    status = 'partially_unavailable' if unresolved else 'verified'
    report['evidence_status'] = status
    if extra or unresolved:
        # The original prose is untouched and distinct from the ordered evidence
        # appendix. Unknown lines are never assigned a name in this appendix.
        numbered = prose + '\n\n【已核实发声来源】\n' + '\n'.join(
            '【D%d】%s：%s' % (index, row['speaker'], row['text'])
            for index, row in enumerate(rows, 1))
    return dict(version=SOURCE_VERSION, numbered_source=numbered, dialogue=rows,
                source_report=report, evidence_status=status, unresolved_sources=unresolved)


def prepare_source_contract(prose, cards, *, strict=True):
    """Use the production quote classifier, preserving its source evidence.

    No new model classifier and no guessed speakers. By default unresolved
    speakers raise ContractError with the original source report in details.
    strict=False additionally recognizes narrow unquoted direct speech and
    preserves unknown evidence without stopping or assigning a guessed voice.
    Dialogue remains exact and unsplit, so the subsequent plan must distribute
    whole original lines within its real timing budget or report the conflict.
    """
    if not isinstance(prose, str) or not prose.strip():
        raise ValueError('正文不能为空')
    normalized, aliases = _identity_index(cards)
    source_cards = [{'name': card['name']} for card in normalized]
    numbered, dialogue, report = episode_contract.prepare_source(prose, source_cards,
                                                                split_long_dialogue=False)
    numbered, dialogue, report = _prefer_explicit_front_speakers(
        prose, numbered, report, [card['name'] for card in normalized])
    if not strict:
        return _continuing_source_contract(prose, numbered, report, normalized, aliases)
    if report.get('issues'):
        raise episode_contract.ContractError('正文对白来源存在未确定说话者，原文和报告已保留',
            code='speaker_unresolved', details={'source_report': report, 'numbered_source': numbered})
    rows = []
    for speaker, words in dialogue:
        try:
            identity = _resolve_speaker(speaker, aliases)
        except episode_contract.ContractError as error:
            raise episode_contract.ContractError(str(error), code='speaker_unresolved',
                details={'source_report': report, 'numbered_source': numbered,
                         'speaker': speaker}) from error
        rows.append(dict(speaker_id=identity, speaker=speaker, text=words))
    return dict(version=SOURCE_VERSION, numbered_source=numbered, dialogue=rows,
                source_report=report)


def validate_plan_source(plan, source_contract, cards):
    """Reject invented/lost/reordered speech or uncertain identity; never repair."""
    normalized, aliases = _identity_index(cards)
    by_id = {card['id']: card for card in normalized}
    if not isinstance(plan, dict) or not isinstance(source_contract, dict):
        raise ValueError('计划与来源契约须为对象')
    planned_characters = plan.get('characters')
    if not isinstance(planned_characters, list):
        raise ValueError('计划缺少人物身份列表')
    seen = set()
    for row in planned_characters:
        if not isinstance(row, dict) or row.get('id') not in by_id or row['id'] in seen:
            raise episode_contract.ContractError('计划人物ID与固定人物卡不一致', code='character_source_mismatch')
        if row.get('name') != by_id[row['id']]['name']:
            raise episode_contract.ContractError('计划人物名字与固定ID不一致', code='character_source_mismatch')
        seen.add(row['id'])
    # Cards are the allowed cast, rather than a requirement that every person
    # speaks or appears. A source speaker must nevertheless exist in the plan.
    expected = [(row['speaker_id'], row['text']) for row in source_contract.get('dialogue', [])]
    if any(identity not in seen for identity, _ in expected):
        raise episode_contract.ContractError('计划漏掉原文说话者', code='character_source_mismatch')
    actual = []
    beats = plan.get('beats')
    if not isinstance(beats, list):
        raise ValueError('计划缺少段落')
    for beat in beats:
        if not isinstance(beat, dict) or not isinstance(beat.get('dialogue'), list):
            raise ValueError('计划每段须包含dialogue数组')
        for line in beat['dialogue']:
            if not isinstance(line, dict) or not isinstance(line.get('text'), str):
                raise ValueError('对白须包含逐字原话')
            identity = _resolve_speaker(line.get('speaker'), aliases)
            if identity not in seen:
                raise episode_contract.ContractError('计划对白说话者未列入人物', code='character_source_mismatch')
            actual.append((identity, line['text']))
    if actual != expected:
        raise episode_contract.ContractError('计划对白与正文发声来源不一致：禁止漏句、改字、换人、乱序或把书写文字变成对白',
            code='dialogue_source_mismatch', details={'expected': expected, 'actual': actual})


def _default_compile(raw, payload):
    from . import story_video_reference_prompts
    return story_video_reference_prompts.compile_written(raw, payload)


def bind_delivery(raw, payload, cards, refs, compiler=None):
    """Bind fixed voice+Subject to each exact line, then compile Chinese H3.

    Returns the compiled result, including voice_binding_audit. Both raw and
    payload remain unchanged. The caller must retain the untouched model reply.
    The same supplied ref mapping must be used for the actual H3 submission.
    """
    if not isinstance(raw, dict) or not isinstance(payload, dict):
        raise ValueError('词稿与payload须为对象')
    if payload.get('mode') != 'ref2va':
        raise ValueError('人物与场景/末帧参考须使用ref2va')
    normalized, aliases = _identity_index(cards)
    by_id = {card['id']: card for card in normalized}
    if not isinstance(refs, list) or not refs:
        raise ValueError('缺少当前参考映射')
    subject_by_id = {}
    for ref in refs:
        if ref.get('role') != 'character':
            continue
        identity, subject = ref.get('character_id'), ref.get('subject')
        if identity not in by_id or identity in subject_by_id or isinstance(subject, bool) or not isinstance(subject, int) or subject < 1:
            raise ValueError('人物参考与稳定身份无法唯一绑定')
        subject_by_id[identity] = subject
    if len(set(subject_by_id.values())) != len(subject_by_id):
        raise ValueError('人物Subject不能重复')
    bound = copy.deepcopy(raw)
    if 'voice_binding_audit' in bound:
        raise ValueError('音色须从原始词稿绑定一次，不能再次绑定已编译结果')
    lines, slots = bound.get('dialogue'), payload.get('dialogue_slots')
    if not isinstance(lines, list) or not isinstance(slots, list) or len(lines) != len(slots):
        raise ValueError('词稿须逐项覆盖全部原对白槽')
    audit = []
    for index, (line, slot) in enumerate(zip(lines, slots)):
        if not isinstance(line, dict) or any(line.get(key) != slot.get(key) for key in ('speaker', 'text')):
            raise ValueError('绑定音色不能改动原说话者、原话或顺序')
        identity = _resolve_speaker(line['speaker'], aliases)
        if identity not in subject_by_id:
            raise ValueError('发声人物缺少本段Subject参考')
        delivery = _text(line.get('delivery'), '当句表演说明')
        subject = subject_by_id[identity]
        tags = re.findall(r'<Subject\s+(\d+)>', delivery, re.I)
        if any(int(number) != subject for number in tags):
            raise ValueError('原delivery引用了与该句说话者不符的Subject，不能静默换人')
        performance = re.sub(r'<Subject\s+\d+>\s*[，,:：;；]?\s*', '', delivery, flags=re.I).strip()
        if not performance:
            raise ValueError('当句表演说明不能只有人物标签')
        final_delivery = '<Subject %d>，音色描述：%s；%s' % (subject, by_id[identity]['voice'], performance)
        line['delivery'] = final_delivery
        audit.append(dict(line_index=index, character_id=identity,
            name=by_id[identity]['name'], subject=subject, speaker_id=slot.get('speaker_id'),
            voice=by_id[identity]['voice'], original_delivery=delivery,
            final_delivery=final_delivery))
    compile_payload = copy.deepcopy(payload)
    compile_payload['canonical_references'] = copy.deepcopy(refs)
    result = (compiler or _default_compile)(bound, compile_payload)
    if [(line['speaker'], line['text']) for line in result['dialogue']] != [
            (line['speaker'], line['text']) for line in raw['dialogue']]:
        raise ValueError('编译改变了原说话者或逐字台词')
    for row, line in zip(audit, result['dialogue']):
        if row['final_delivery'] != line['delivery']:
            row['pre_normalization_delivery'] = row['final_delivery']
            row['final_delivery'] = line['delivery']
    result['voice_binding_audit'] = audit
    return result


def shortfilm_addendum(segment_count, seconds):
    """Local UW system addendum; no fixed characters, location, plot or length."""
    if isinstance(segment_count, bool) or not isinstance(segment_count, int) or segment_count < 1:
        raise ValueError('段数须为正整数')
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('每段时长须为有限正数')
    total = segment_count * seconds
    return '''
本轮是供视频制作的通用短片源正文，下列短片要求优先于小说模板中每个地点首次出现先铺陈、每个人首次出场先写一整段详细外貌的要求。
目标总时长为%g秒，随后分成%d段、每段%g秒。按实际可拍动作、自然发声与反应容量创作，不把长篇情节塞进短片，不靠加速说话或旁白解释省略的因果。正文不是全文朗读稿。
场景与外貌只取辨认主体必需的信息，简短融入正在发生的行动；图片已经给定的衣着、头发与背景不逐项重新介绍。尽早进入有意图的行动与交流，关键线索、选择、道具去向和结果清楚，结尾回应前面的事情。
名字和稳定身份采用本轮人物设定；性格通过选择、行动、回应和当句表演体现，不给每个人写履历，不把性格词反复当旁白。人物数量、物种、年龄、题材与地点只取本轮输入，不预设成人或固定组合。声音描述由最终程序绑定，本阶段不另编音色设定。
正文仍是可阅读的完整故事，不写镜头编号、H3字段、分镜或自评。实际说出口的每句对白旁须明确出声者姓名，例如姓名说：引号内原话；不用无明确归属的他说或她说造成说话者悬空。对话依剧情自然产生，不设固定句数。
写在纸上、招牌上的文字，心声、回忆和拟声均须有明确叙述用途，不用人物姓名加冒号的对白格式伪装它们，也不自动让人物朗读。后续以这份冻结正文为剧情和逐字发声来源，只作摄影、表演和必要衔接，不另编主线或把画内文字改成台词。
''' % (total, segment_count, seconds)
