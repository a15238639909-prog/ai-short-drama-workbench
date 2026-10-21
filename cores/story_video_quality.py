"""Pure, evidence-bound story-video quality requests and adoption decisions.

No files, models or networks are accessed.  The caller supplies clip samples in
the same order as the attached images and owns bounded repairs and retries.
"""
import copy
import hashlib
import json
import math
import re


def signature(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _object(**fields):
    return {'type': 'object', 'properties': fields, 'required': list(fields),
            'additionalProperties': False}


def _text():
    return {'type': 'string', 'minLength': 1}


def _array(item, **limits):
    return dict(type='array', items=item, **limits)


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + '须为有限数字')
    return float(value)


def _seconds(value):
    value = _number(value, 'seconds')
    if not 4 <= value <= 15:
        raise ValueError('seconds须在4～15之间')
    return value


def _string(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + '须为非空字符串')
    return value


def _fields(value, names, label):
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValueError(label + '字段不符')


def _list(value, label, maximum=None, minimum=0):
    if not isinstance(value, list) or len(value) < minimum or (maximum is not None and len(value) > maximum):
        raise ValueError(label + '项目数不符')
    return value


def _bound_request(system, payload, schema):
    payload = copy.deepcopy(payload)
    schema = copy.deepcopy(schema)
    # Bind content, instructions AND schema: a schema upgrade cannot reuse an
    # accepted verdict from a semantically different request.
    token = signature({'version': 1, 'system': system, 'payload': payload, 'schema': schema})
    payload['request_signature'] = token
    schema['properties']['request_signature'] = dict(_text(), enum=[token])
    return system, payload, schema


def _binding(obj, payload):
    if obj.get('request_signature') != payload.get('request_signature'):
        raise ValueError('质量回复与当前请求签名不一致，不能复用旧缓存')
    if type(obj.get('passed')) is not bool:
        raise ValueError('passed须为布尔值')


def _contract(events, seconds):
    events = _list(events, 'event_contract', maximum=3, minimum=1)
    ids = set()
    for event in events:
        _fields(event, ('id', 'source_quote', 'before', 'action', 'after', 'start', 'end', 'kind'), '关键事件')
        for field in ('id', 'source_quote', 'before', 'action', 'after'):
            _string(event[field], 'event.' + field)
        if event['id'] in ids:
            raise ValueError('关键事件id重复')
        ids.add(event['id'])
        start, end = (_number(event[field], 'event.' + field) for field in ('start', 'end'))
        if event['kind'] not in ('state', 'transition'):
            raise ValueError('关键事件kind不符')
        if not 0 <= start <= end <= seconds or (event['kind'] == 'transition' and start == end):
            raise ValueError('关键事件时间窗口越界或无动作时长')
    return events


_STORYBOARD_SYSTEM = '''你是独立的分镜预审员，只输出规定JSON，原样回填request_signature。此时视频尚未生成，不得声称任何画面已完成。
核对用户brief、原beat、actual_state与导演written。只阻挡影响用户目标的核心问题：关键动作或结果被省掉、因果相反、警告或回应晚于其所针对的动作、无法自然完成的操作、与已知首帧事实相冲突。景别和切镜由剧情决定，不设固定配额；轻微风格、服装、措辞或审美差异写advisory，不阻挡。
对白须同时核对逐字原话和written.dialogue的实际规划start/end，结合镜头正文与timeline判断先后。不能因为台词数组原字正确，就放过“先拿走，后来才提醒别碰”这样的时序问题。对白可与动作并行，但警告、选择、结果的因果须成立。
必须逐句填写dialogue_timing，line_index从0开始完整且不重复覆盖written.dialogue。先判断语用：提醒/制止对应before，结果确认对应after，确实与动作并行才用overlap，无因果依赖的闲聊才可independent；“先别碰”不能归为独立闲聊。依赖项timeline_index引用真实timeline项，boundary选该项start或end作锚点；reason须逐字引用该项action或visible_result的一个原文短语，可用引号。不虚构时间。before要求本句end不晚于锚点，after要求start不早于锚点，overlap要求与该timeline区间实际相交；independent的timeline_index为-1。发现时序冲突时返回passed=false并列blocking问题，让作者修订，不能为迁就稿件改换无关锚点或放行。
引号内可能是画面文字，不自动等于必须说出的台词。只核对用户明确要求说出的原话与本段written.dialogue已经安排的对白，不另加必须朗读画面文字、必须同时逐字展示并朗读的条件。用户明确说出的台词须保持原字。
issues每项severity为blocking或advisory，reason引用可核对的冲突，correction只给局部可操作修复方向，不重写用户故事。passed只取决于没有blocking问题。
event_contract选1～3个本段必须在实际视频中证明的用户核心事件，全部视为required。source_quote只从source_quote_choices选择，其来源仅为用户brief原句或片段；不得取自beat、story_plan或written，编剧和导演自加细节不能升格为用户硬要求。用户brief简短时可引用整句，从计划中选择能够实现原目标的主要结果，不把所有微动作塞进required。before/action/after只定义用户明确要求的核心结果所必需的可见过程，不另加更具体的完成条件。
编导自加的准备姿势、过渡动作、构图安排若不影响用户核心结果，只是表演或观察风险，不作为required，也不因抽样没有拍到它们就阻挡已经发生的核心事件；可列advisory。用户明确要求的核心动作、结果及其必要因果仍须严格验收，不得因此放宽。core_continuity仅限有明确矛盾使用户核心结果实际不成立。不可把预期当事实。
kind=transition用于拿取、交接、击碎、跨门等变化，需检查之前状态、动作过程及可见结果；kind=state用于可从明确画面判断的状态。start/end给0到seconds内的预期证据窗口，非实际观察时间。正文中尚未完成的结果不能列为已发生。'''


def storyboard_request(brief, style, plan, beat, written, actual_state, seconds):
    seconds = _seconds(seconds)
    _string(brief, 'brief')
    if not all(isinstance(value, dict) for value in (plan, beat, written)):
        raise ValueError('plan、beat、written须为JSON对象')
    issue = _object(severity={'type': 'string', 'enum': ['blocking', 'advisory']},
                    reason=_text(), correction=_text())
    # Only the human brief defines required story outcomes. Planner/director
    # transitions remain context and cannot become acceptance requirements.
    sources = [brief] + re.split(r'[。\n！？!?]', brief) + re.split(r'[。\n！？!?；;，,]', brief)
    choices = list(dict.fromkeys(text.strip() for text in sources if isinstance(text, str) and text.strip()))
    event = _object(id=_text(), source_quote=dict(_text(), enum=choices), before=_text(), action=_text(), after=_text(),
                    start={'type': 'number', 'minimum': 0, 'maximum': seconds},
                    end={'type': 'number', 'minimum': 0, 'maximum': seconds},
                    kind={'type': 'string', 'enum': ['state', 'transition']})
    lines = _list(written.get('dialogue', []), 'written.dialogue')
    timeline = _list(written.get('timeline', []), 'written.timeline')
    timing = _object(line_index={'type': 'integer', 'enum': list(range(len(lines))) or [0]},
                     relation={'type': 'string', 'enum': ['before', 'after', 'overlap', 'independent']},
                     timeline_index={'type': 'integer', 'enum': [-1] + list(range(len(timeline)))},
                     boundary={'type': 'string', 'enum': ['start', 'end']}, reason=_text())
    schema = _object(request_signature=_text(), passed={'type': 'boolean'},
                     issues=_array(issue, maxItems=8),
                     event_contract=_array(event, minItems=1, maxItems=3),
                     dialogue_timing=_array(timing, minItems=len(lines), maxItems=len(lines)))
    payload = dict(stage='storyboard_review', brief=brief, style=style or '',
                   story_plan=plan, beat=beat, written=written,
                   actual_state=actual_state, seconds=seconds, source_quote_choices=choices)
    return _bound_request(_STORYBOARD_SYSTEM, payload, schema)


def _dialogue_timing_conflicts(obj, payload):
    lines = payload['written'].get('dialogue', [])
    timeline = payload['written'].get('timeline', [])
    timings = _list(obj['dialogue_timing'], 'dialogue_timing', minimum=len(lines), maximum=len(lines))
    seen, conflicts = set(), []
    for timing in timings:
        _fields(timing, ('line_index', 'relation', 'timeline_index', 'boundary', 'reason'), '对白时序')
        index, timeline_index = timing['line_index'], timing['timeline_index']
        if type(index) is not int or index not in range(len(lines)) or index in seen:
            raise ValueError('dialogue_timing须逐句完整且不重复覆盖written.dialogue')
        seen.add(index)
        relation, boundary = timing['relation'], timing['boundary']
        if relation not in ('before', 'after', 'overlap', 'independent') or boundary not in ('start', 'end'):
            raise ValueError('对白时序relation或boundary不符')
        reason = _string(timing['reason'], 'dialogue_timing.reason')
        text = lines[index].get('text', '')
        # A narrow guard covers explicit prohibitions/completion declarations;
        # the independent reviewer still decides other speech acts in context.
        expected = ('before' if re.search(r'(?:先别|千万别|不要|别)(?:碰|动|拿|摸|打开|靠近|过去|伸手)', text)
                    else 'after' if re.search(r'(?:拿到了|拿到啦|完成了|搞定了|得手了)[！!。\.\s]*$', text)
                    else None)
        if expected and relation != expected:
            conflicts.append('台词第%d句“%s”是明确%s语，不能标为%s' %
                (index + 1, text, '提醒/制止' if expected == 'before' else '结果确认', relation))
        if relation == 'independent':
            if type(timeline_index) is not int or timeline_index != -1:
                raise ValueError('independent对白的timeline_index须为-1')
            continue
        if type(timeline_index) is not int or timeline_index not in range(len(timeline)):
            raise ValueError('对白时序须引用真实timeline项')
        anchor = timeline[timeline_index]
        descriptions = [anchor.get(field, '') for field in ('action', 'visible_result')]
        quotes = re.findall(r'[“「"]([^”」"]+)[”」"]', reason)
        if not any(description and (description in reason or
                (len(reason.strip()) >= 8 and reason.strip() in description) or any(
                len(quote.strip()) >= 2 and quote.strip() in description for quote in quotes))
                for description in descriptions if isinstance(description, str)):
            raise ValueError('对白时序reason须引用真实timeline动作或结果原文')
        start, end = (_number(lines[index].get(field), 'dialogue.' + field) for field in ('start', 'end'))
        event_start, event_end = (_number(anchor.get(field), 'timeline.' + field) for field in ('start', 'end'))
        if not 0 <= start < end <= payload['seconds'] or not 0 <= event_start < event_end <= payload['seconds']:
            raise ValueError('对白或引用timeline时间越界')
        target = event_start if boundary == 'start' else event_end
        valid = (end <= target + 1e-6 if relation == 'before' else
                 start >= target - 1e-6 if relation == 'after' else
                 start < event_end and end > event_start)
        if not valid:
            conflicts.append('台词第%d句“%s”规划%.3f～%.3f秒，%s要求锚点%s=%.3f秒；真实动作：%s' %
                (index + 1, text, start, end, relation, boundary, target, anchor.get('action', '')))
    return conflicts


def validate_storyboard(obj, payload):
    _fields(obj, ('request_signature', 'passed', 'issues', 'event_contract', 'dialogue_timing'), '分镜预审')
    _binding(obj, payload)
    issues = _list(obj['issues'], 'issues', maximum=8)
    for issue in issues:
        _fields(issue, ('severity', 'reason', 'correction'), '预审问题')
        if issue['severity'] not in ('blocking', 'advisory'):
            raise ValueError('预审severity不符')
        _string(issue['reason'], 'reason')
        _string(issue['correction'], 'correction')
    if obj['passed'] != (not any(issue['severity'] == 'blocking' for issue in issues)):
        raise ValueError('预审passed必须与blocking问题一致')
    conflicts = _dialogue_timing_conflicts(obj, payload)
    if conflicts and obj['passed']:
        raise ValueError('分镜存在真实对白时序冲突，不能放行；应返回passed=false及blocking修复意见：' + '；'.join(conflicts))
    for event in _contract(obj['event_contract'], payload['seconds']):
        if event['source_quote'] not in payload['brief']:
            raise ValueError('source_quote只能来自用户brief，不能将编剧或导演新增动作升级为硬要求')


_CRITICAL_CATEGORIES = ['blank_or_unusable', 'duplicated_main_subjects', 'wrong_scene',
                        'missing_main_subject', 'severe_anatomy', 'core_continuity', 'style_mismatch']

_ACCEPTANCE_SYSTEM = '''你是视频关键事件验收员，只输出规定JSON，原样回填request_signature。只依据附带抽样画面及明确提供的真实音频核对资料，不把written、event_contract或endpoint文字当成视频已经完成的证据。sample_records顺序对应附图顺序，sample_id只能使用其真实id。结果只覆盖给出的抽样，不能宣称全帧或口型审听完成。
逐项检查全部event_contract。observed表示有实际图像证据，contradicted表示有明确相反画面，insufficient_evidence表示遮挡、漏采样、只有预备动作或无法判断。unknown不能算成功。每条before/action/after证据须写sample_id及这张图真正可见的visible事实，不能填期望描述。
transition通常confirmation_mode=sequence，需真实前态、动作过程和结果的三组按时间严格递增证据。若关键变化可由明确前后结果确认，可用decisive_result，至少用不同时间的前态和后态，explanation说明为什么该结果足以确认；准备动作、光效、姿势变化、附属物受损不自动证明用户要求的目标事件完成。只有静态目标才可用state，至少有一张清楚结果图。不得为凑齐证据重复同一帧充当前中后，不因原计划写了“成功”而放行。
来源优先级是用户brief定义的核心结果高于编导补充细节。event_contract.source_quote只能来自用户原文；编导自加的准备姿势、过渡动作或构图安排若不影响核心结果，不作为required或critical_issue，未抽到这类动作只能记录观察风险，不能阻挡已被证实的用户核心事件。也不能把宽泛来源引文扩张为用户没有要求的额外微动作。
检查实际采用末端是否仍满足用户核心事件结果、必要人物数量、关键持物和空间关系。opening_state、endpoint仅帮助定位与身份匹配，冲突时以附图事实为准。轻微服装或风格漂移可解释，不单独阻挡；空白、主要人物复制、完全错误场景、严重肢体异常等必须有实际画面证据才列critical_issues。core_continuity只限明确矛盾使核心结果实际不成立，不能以单个准备动作没被抽到为由拒绝已经实现的核心事件；用户原文明示的核心动作与结果仍须严格核对。
audio_review若有真实ASR识别文本和逐句实际时间，可与written.dialogue规划和图中动作时序核对警告是否已经太晚，但不得凭图推断是否实际说话、音色、口型或声轨合格。画面引号文字不自动视为必须朗读的台词；仅核对written.dialogue已安排的对白，不从brief或画面文字额外增加必须发声内容。未提供的音频事实保持未知，最终音频是否通过由调用者单独核验。
passed仅在全部required事件observed且critical_issues为空时为true。若缺关键证据，follow_up_times最多给3个0到last_frame_seconds内的定点补查时间，仅建议一次；不能请求截短、重写全故事或全帧扫描。已通过时follow_up_times必须为空。'''


def _samples(records, seconds, endpoint):
    _list(records, 'sample_records', minimum=1)
    ids = set()
    times = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('sample_records须为对象数组')
        for field in ('id', 'path'):
            _string(record.get(field), 'sample.' + field)
        if record['id'] in ids:
            raise ValueError('sample_id重复')
        ids.add(record['id'])
        time = _number(record.get('time_seconds'), 'sample.time_seconds')
        if not 0 <= time <= seconds:
            raise ValueError('样本须位于实际采用片内，不可验收采用范围之外的raw帧')
        times[record['id']] = time
    last = max(times.values())
    if isinstance(endpoint, dict) and 'time_seconds' in endpoint:
        endpoint_time = _number(endpoint['time_seconds'], 'endpoint.time_seconds')
        if not 0 <= endpoint_time <= seconds:
            raise ValueError('采用末帧时间越界')
        last = min(last, endpoint_time)
        if any(time > last for time in times.values()):
            raise ValueError('样本晚于实际采用末帧')
    return times, last


def segment_acceptance_request(contract, written, sample_records, endpoint, opening_state,
                               seconds, audio_review=None):
    seconds = _seconds(seconds)
    _contract(contract, seconds)
    if not isinstance(written, dict):
        raise ValueError('written须为JSON对象')
    times, last = _samples(sample_records, seconds, endpoint)
    evidence = _object(sample_id={'type': 'string', 'enum': list(times)}, visible=_text())
    event = _object(id={'type': 'string', 'enum': [item['id'] for item in contract]},
                    status={'type': 'string', 'enum': ['observed', 'contradicted', 'insufficient_evidence']},
                    confirmation_mode={'type': 'string', 'enum': ['sequence', 'decisive_result', 'state']},
                    before=_array(evidence), action=_array(evidence), after=_array(evidence),
                    explanation=_text())
    issue = _object(category={'type': 'string', 'enum': _CRITICAL_CATEGORIES},
                    reason=_text(), evidence=_array(evidence, minItems=1))
    schema = _object(request_signature=_text(), passed={'type': 'boolean'},
                     events=_array(event, minItems=len(contract), maxItems=len(contract)),
                     critical_issues=_array(issue, maxItems=6),
                     follow_up_times=_array({'type': 'number', 'minimum': 0, 'maximum': last},
                                            maxItems=3, uniqueItems=True))
    payload = dict(stage='segment_acceptance', event_contract=contract, written=written,
                   sample_records=sample_records, endpoint=endpoint, opening_state=opening_state,
                   seconds=seconds, last_frame_seconds=last, audio_review=audio_review,
                   evidence_scope='attached adopted-clip samples only; not full-frame or lip-sync review')
    return _bound_request(_ACCEPTANCE_SYSTEM, payload, schema)


def _evidence(items, times):
    result = []
    for item in _list(items, '证据'):
        _fields(item, ('sample_id', 'visible'), '证据')
        if item['sample_id'] not in times:
            raise ValueError('证据引用了未提供的sample_id')
        _string(item['visible'], 'visible')
        result.append(times[item['sample_id']])
    return result


def validate_acceptance(obj, payload):
    _fields(obj, ('request_signature', 'passed', 'events', 'critical_issues', 'follow_up_times'), '画面验收')
    _binding(obj, payload)
    times, last = _samples(payload['sample_records'], payload['seconds'], payload['endpoint'])
    contracts = {event['id']: event for event in _contract(payload['event_contract'], payload['seconds'])}
    events = _list(obj['events'], 'events', minimum=len(contracts), maximum=len(contracts))
    seen = set()
    for event in events:
        _fields(event, ('id', 'status', 'confirmation_mode', 'before', 'action', 'after', 'explanation'), '事件验收')
        if event['id'] not in contracts or event['id'] in seen:
            raise ValueError('事件验收须完整且无重复覆盖当前契约')
        seen.add(event['id'])
        if event['status'] not in ('observed', 'contradicted', 'insufficient_evidence'):
            raise ValueError('事件status不符')
        if event['confirmation_mode'] not in ('sequence', 'decisive_result', 'state'):
            raise ValueError('confirmation_mode不符')
        _string(event['explanation'], 'explanation')
        before, action, after = (_evidence(event[field], times) for field in ('before', 'action', 'after'))
        if event['status'] == 'contradicted' and not (before or action or after):
            raise ValueError('contradicted须有实际相反画面证据')
        if event['status'] != 'observed':
            continue
        if not after:
            raise ValueError('observed须有结果证据')
        if contracts[event['id']]['kind'] == 'state':
            if event['confirmation_mode'] != 'state':
                raise ValueError('静态目标须使用state确认模式')
        elif event['confirmation_mode'] == 'sequence':
            if not before or not action or not max(before) < min(action) or not max(action) < min(after):
                raise ValueError('transition须有按序不同时间的前中后证据')
        elif event['confirmation_mode'] == 'decisive_result':
            if not before or not max(before) < min(after):
                raise ValueError('结果确认须有不同时间的明确前态与后态')
        else:
            raise ValueError('transition不能只用一张静态画面确认')
    issues = _list(obj['critical_issues'], 'critical_issues', maximum=6)
    for issue in issues:
        _fields(issue, ('category', 'reason', 'evidence'), 'critical_issue')
        if issue['category'] not in _CRITICAL_CATEGORIES:
            raise ValueError('critical_issue类别不符')
        _string(issue['reason'], 'reason')
        if not _evidence(issue['evidence'], times):
            raise ValueError('critical_issue须有可见图像证据')
    passed = all(event['status'] == 'observed' for event in events) and not issues
    if obj['passed'] != passed:
        raise ValueError('验收passed须与全部关键事件和critical_issues一致；unknown不能通过')
    follow_up = _list(obj['follow_up_times'], 'follow_up_times', maximum=3)
    values = [_number(time, 'follow_up_time') for time in follow_up]
    if len(set(values)) != len(values) or any(not 0 <= time <= last for time in values):
        raise ValueError('补查时间须唯一且位于0到实际采用末帧之间')
    if passed and follow_up:
        raise ValueError('已通过的验收不能再要求补查')


def adoption_decision(visual, audio, *, audio_required=True):
    reasons = []
    if not isinstance(visual, dict) or visual.get('passed') is not True:
        reasons.append('画面关键事件验收未通过')
    if isinstance(visual, dict):
        events = visual.get('events')
        if not isinstance(events, list) or not events:
            reasons.append('缺少逐事件画面验收')
        else:
            for event in events:
                if event.get('status') != 'observed':
                    reasons.append('%s：%s' % (event.get('id', '关键事件'),
                                   event.get('explanation') or '没有充分画面证据'))
        for issue in visual.get('critical_issues', []):
            reasons.append(issue.get('reason') or '存在严重画面问题')
    if audio_required and (not isinstance(audio, dict) or audio.get('status') != 'passed'):
        detail = (audio.get('reason') or audio.get('error')) if isinstance(audio, dict) else None
        reasons.append('台词音频核对未通过' + ('：' + str(detail) if detail else '（缺失或不可用也不能跳过）'))
        if isinstance(audio, dict):
            for issue in audio.get('issues', [])[:3]:
                if not isinstance(issue, dict):
                    continue
                description = str(issue.get('reason') or issue.get('code') or '逐句核对失败')
                if issue.get('expected'):
                    description += '；原话：' + str(issue['expected'])
                reasons.append('台词核对：' + description)
    return {'passed': not reasons, 'reasons': list(dict.fromkeys(reasons))}


def sampling_times(contract, seconds, max_samples=7):
    """Suggest bounded evidence times; these are targets, never observed PTS."""
    seconds = _seconds(seconds)
    _contract(contract, seconds)
    if isinstance(max_samples, bool) or not isinstance(max_samples, int) or max_samples < 3:
        raise ValueError('max_samples须为至少3的整数')
    last = max(0, seconds - 1 / 24)
    candidates = [0, last]
    for event in contract:
        start, end = event['start'], event['end']
        candidates.extend((max(0, start - .25), (start + end) / 2, min(last, end + .25)))
    times = sorted({round(min(last, max(0, value)), 6) for value in candidates})
    if len(times) <= max_samples:
        return times
    return [times[round(index * (len(times) - 1) / (max_samples - 1))]
            for index in range(max_samples)]
