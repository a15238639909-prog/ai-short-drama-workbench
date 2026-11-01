"""Bounded review of a newly generated prose draft against that draft alone.

The local model supplies semantic findings. Code verifies exact ordered source
citations; stylistic opinions, plans and invented quotations cannot veto prose.
No saved/user-approved prose is rechecked by this module automatically.
"""
import copy
import os


KINDS = {'prop_reversion', 'wardrobe_reversion', 'departed_character', 'repeated_event'}
SYSTEM = '''你只核对本次新正文内部已经写明的硬连续性，不核对初始构想、目标、篇幅、文风、审美或必须发生的情节。
完整阅读全部编号段落，检查四类事实：同一道具明确转移后无交还却回到原持有人；明确脱下的同件衣物无穿回却恢复穿着；明确离开的角色无返回却重新参加现场；已经完成的重要动作在状态未复原时又作为首次过程重新发生。
正常往返、明确再次操作、换衣、交还、回忆、比喻和对白转述不算矛盾。原文未交代清楚就记unknown，不能凭常识补造事实。若中间已有能解释变化的行动，不报问题。
每项硬问题必须是同一实体的两个互相冲突的实际状态，给出前后段号及逐字引文，引用足够上下文明确同一实体；detail解释矛盾，不提出剧情扩写建议。entity须是前后引文中共同出现的原文称呼。只有前后明确成立才certainty=explicit。
只返回JSON：{"verdict":"clear或issues或unknown","issues":[{"kind":"prop_reversion或wardrobe_reversion或departed_character或repeated_event","entity":"同一道具、衣物或人物原文称呼","certainty":"explicit","before":{"paragraph_id":1,"quote":"前文逐字引文"},"after":{"paragraph_id":3,"quote":"后文逐字引文"},"detail":"仅解释正文内部硬矛盾"}],"summary":"仅说明本次内部核对结果"}。
clear表示读完正文未发现有证据的硬问题；issues表示上述引用能证明硬矛盾；unknown表示无法完成核对或事实不够明确。不要把没有引文的建议列成问题。'''


def validate_findings(obj, prose):
    from . import universal_writer as uw
    if (not isinstance(obj, dict) or obj.get('verdict') not in {'clear', 'issues', 'unknown'}
            or not isinstance(obj.get('issues'), list)):
        raise ValueError('正文内部核对没有返回有效结论与问题数组')
    parts = uw.paragraphs(prose)
    supported, discarded = [], []
    for raw in obj['issues']:
        item = copy.deepcopy(raw)
        try:
            if not isinstance(item, dict) or item.get('kind') not in KINDS:
                raise ValueError('不是正文内部硬连续性问题')
            if item.get('certainty') != 'explicit' or not str(item.get('detail') or '').strip():
                raise ValueError('没有明确事实判断')
            entity = str(item.get('entity') or '').strip()
            if len(entity) < 2 or entity in {'他们', '她们', '它们', '人物', '角色'}:
                raise ValueError('没有可核对的同一实体称呼')
            positions = []
            for key in ('before', 'after'):
                citation = item.get(key)
                if not isinstance(citation, dict):
                    raise ValueError('缺少前后逐字引文')
                no, quote = citation.get('paragraph_id'), citation.get('quote')
                if type(no) is not int or no not in parts or not isinstance(quote, str):
                    raise ValueError('引用段号或引文格式无效')
                if len(quote.strip()) < 4 or quote not in parts[no]:
                    raise ValueError('引文不在对应正文段落中')
                if not entity or entity not in quote or quote.strip() == entity:
                    raise ValueError('前后引文没有共同实体及实际状态依据')
                positions.append((no, parts[no].index(quote)))
            if positions[0] >= positions[1]:
                raise ValueError('前后引文没有成立的时间顺序')
            if item not in supported:
                supported.append(item)
        except ValueError as error:
            discarded.append(dict(finding=raw, reason=str(error)))
    status = ('unknown' if obj['verdict'] == 'unknown' or (obj['verdict'] == 'clear' and supported) else
              'confirmed_issues' if supported else
              'unsupported_ignored' if discarded else
              'clear' if obj['verdict'] == 'clear' else 'unknown')
    return dict(status=status, issues=supported, discarded_findings=discarded,
                model_summary=str(obj.get('summary') or ''),
                checker='local_model_with_exact_ordered_prose_citations',
                scope='new_prose_internal_only', prose_sig=uw.signature(prose), raw_review=obj)


def check_prose(prose, model=None):
    from . import universal_writer as uw
    raw = ''
    try:
        raw = uw.call(SYSTEM, uw.dump({'新正文编号段落': uw.paragraphs(prose)}),
                      4200, .1, model, task='state_commit')
        result = validate_findings(uw.parse_object(raw), prose)
        result['raw_response'] = raw
        return result
    except Exception as error:
        return dict(status='unavailable', issues=[], discarded_findings=[], error=str(error),
                    error_type=type(error).__name__, raw_response=raw,
                    scope='new_prose_internal_only', prose_sig=uw.signature(prose))


def _review(prose, continuity):
    from . import universal_writer as uw
    status = continuity['status']
    blocked = status in {'confirmed_issues', 'unknown', 'unavailable'}
    issues = copy.deepcopy(continuity.get('issues') or [])
    if status in {'unknown', 'unavailable'}:
        issues.append(dict(detail='新正文内部连续性未完成核对：' +
                           str(continuity.get('error') or continuity.get('model_summary') or status)))
    return dict(verdict=('fail' if status == 'confirmed_issues' else 'unknown') if blocked else 'accepted',
                source_authority=True, production_blocked=blocked,
                summary='仅核对新正文自身前后硬连续性，不回审初始构想；' + status,
                issues=issues, outcomes=[], state=[], checks=[],
                internal_continuity=continuity, prose_sig=uw.signature(prose))


def review_new_draft(prose, model=None, on_step=None):
    """One local check, at most one cited paragraph revision, then one recheck."""
    from . import universal_writer as uw
    if on_step:
        on_step('只核对新正文内部已写明的道具、衣物、离场与动作连续性')
    continuity = check_prose(prose, model)
    result = dict(prose=prose, review=_review(prose, continuity))
    if continuity['status'] != 'confirmed_issues':
        return result
    if str(os.environ.get('V41_NO_REVISION') or '').strip() in {'1', 'on', 'true'}:
        result['revision_skipped'] = True
        return result
    result['revision'] = dict(original=prose, original_review=copy.deepcopy(result['review']), paragraphs=[])
    try:
        if on_step:
            on_step('仅局部修订有前后引文的硬矛盾，本轮最多一次')
        allowed = {issue['after']['paragraph_id'] for issue in continuity['issues']}
        task = {'原文段落': uw.paragraphs(prose), '已核实的正文内部硬矛盾': continuity['issues'],
                '允许修改的后文段号': sorted(allowed),
                '修订范围': '只消除上述前后状态矛盾，保留其余原文、对白、事件和画风；依据仅为这份正文。'}
        system = uw.instruction('局部修订') + '\n本次只处理正文内部已引用的硬矛盾；只修改允许的后文段落。不参考初始构想或外部目标，不增加事件，不改写其他段落。'
        raw = uw.call(system, uw.dump(task), 7000, .3, model, task='universal_prose')
        result['revision']['raw_response'] = raw
        patch = uw.parse_object(raw)
        changes = patch.get('patches') if isinstance(patch, dict) else None
        if not isinstance(changes, list) or any(not isinstance(p, dict) or p.get('paragraph_id') not in allowed for p in changes):
            raise ValueError('局部修订越过已引用的后文段落，原草稿保留')
        revised, changed = uw.apply_local_revision(prose, patch)
        result['revision']['paragraphs'] = changed
        result['prose'] = revised
        recheck = check_prose(revised, model)
        result['review'] = _review(revised, recheck)
        # An unresolved recheck cannot clear a previously confirmed contradiction.
        if recheck['status'] != 'clear':
            result['review']['production_blocked'] = True
            result['review']['verdict'] = 'fail' if recheck['status'] == 'confirmed_issues' else 'unknown'
            result['review']['unresolved_prior_issues'] = copy.deepcopy(continuity['issues'])
    except Exception as error:
        result['revision_error'] = str(error)
        result['review']['production_blocked'] = True
    return result
