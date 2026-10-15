"""Pure request builders for locally planned, observed and continued H3 stories.

No model, filesystem or network calls live here.  The caller owns inference,
bounded repairs, media timestamps and adoption of an observed ending.
"""
import copy
import math
import re


I2VA_FIRST_FRAME = (
    'For the target video, at 0.00 seconds into the target video, '
    '<Picture 1> (from [Shot 1]) is fully referenced.'
)


def _object(**fields):
    return {'type': 'object', 'properties': fields, 'required': list(fields),
            'additionalProperties': False}


def _text():
    return {'type': 'string', 'minLength': 1}


def _strings(**limits):
    return dict(type='array', items=_text(), **limits)


def _seconds(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('seconds须为4～15之间的有限数字')
    value = float(value)
    if not math.isfinite(value) or not 4 <= value <= 15:
        raise ValueError('seconds须为4～15之间的有限数字')
    return value


def _timing_budget(seconds):
    # Match episode_contract's 4.5 counted characters/s + .5 s per line.
    lines = 1 if seconds <= 10 else 2
    speech = round(min(5.0, seconds * .4), 3)
    characters = max(0, math.floor((speech - .5 * lines) * 4.5))
    return {'seconds': seconds, 'max_dialogue_lines': lines,
            'max_dialogue_seconds': speech,
            'max_dialogue_characters_total': characters,
            'speech_characters_per_second': 4.5,
            'speech_pause_seconds_per_line': .5,
            'target_visible_events': 2 if seconds <= 6 else 3,
            'motion_phases': [
                {'start': 0, 'end': round(seconds / 3, 3), 'focus': '开始有效行动或互动'},
                {'start': round(seconds / 3, 3), 'end': round(seconds * 2 / 3, 3), 'focus': '行动持续发展，出现回应或状态变化'},
                {'start': round(seconds * 2 / 3, 3), 'end': seconds, 'focus': '继续行动并在段尾到达可续接结果'}],
            'max_camera_cuts': 1}


def _pacing_budget(count):
    enforced = count >= 6
    return {'enforced': enforced,
            'main_resolution_not_before': math.ceil(.8 * count) if enforced else None,
            'max_epilogue_beats': math.floor(.2 * count) if enforced else None}


_PLAN_SYSTEM = '''你是为本地自动视频工作台创作短片的编剧与导演。仅输出符合schema的JSON，不调用工具。
brief是用户目标，style是视觉风格，count与seconds是总段数和每段时长。故事必须恰好count段，具有可辨的目标、阻碍、因果推进、变化和完整结局；按题材自然安排，不强制套用追逐、救援、误会或反转的固定故事。最后一段真正收束，不把结局写成下集预告。
如有opening_observation，只把其实际可见事实当成首图约束。沿用已有角色ID、数量、服装、姿势、道具持有关系、已出现的环境和水态。unknowns保持未知；可创作人物意图与后续事件，但不得把想象的前史或未见细节冒充画面事实。首图可能已经在故事中途，不要倒回去重新起跑或重复首图已经完成的动作。
characters定义后续稳定身份，props只记录有叙事作用的持续道具，invariants记录数量、外观、归属与环境必须延续的规则。身份参考不恢复旧姿势、旧持物或旧摆放。角色离开画面不等于离开地点，道具出画不等于消失。action中的人物数量、指代和协作描述必须与本段cast一致；合作双方可能包含动物，不得默认写成多个人，也不能暗中增加未列出的同伴。
characters.appearance只写身份与稳定外貌，不把当前持物、站位或动作写成永久外貌。可移动道具的当前持有人和位置应随明确交接、移动动作更新；invariants不能一面永久锁死它的位置，一面又安排移动。固定建筑与可移动道具须区别。
每段goal说明观众新知道什么，action是能拍出的因果动作，turn是本段带来的局面变化，emotion须能通过表演看见，ending写最后可见位置、姿势、持物与环境状态。每段通常2～3个有结果的动作，不堆十几个指令，不用反复发光、站看、慢推代替推进；阶段性停顿和情感反应也应有具体信息。每段ending是目标，不是生成后已完成的事实。
每段action须按timing_budget.motion_phases安排完整时长：15秒时为0～5、5～10、10～15秒，分别写清开始行动、持续发展、段尾变化。主要动作、自然表演和关系回应须持续到段尾，不能前5秒完成全部任务、后10秒等着结束；安静片段可用有信息的视线、身体调整、触碰和环境互动推进，不靠强迫奔跑、慢放、重复同一动作或转动镜头凑满时间。最后一段在接近段尾时完成结局并自然收住，不再引出新任务。
长故事要有持续发展的主线与起伏，不要在中段彻底解决核心问题后用许多段重复收尾。解决过程中的选择、线索、代价或关系变化应使后段仍有新进展；尾声长度与用户风格相称，每一段都须增加有效信息。静谧或关系片可用理解、情感和关系变化推进，不强制套用动作片危险或反转；也不能用无变化的重复小动作凑满段数。结局必须由前面看得见的行动和选择造成，不能只靠最后一段宣告成功。
pacing_budget由程序计算。enforced=true时，主线最终目标实际彻底解决的可见动作不得早于main_resolution_not_before段，其后的尾声不得超过max_epilogue_beats段；count<6时不强制此比例。次要微动作、单纯换姿势或环境细节不能冒充尚未解决的主线。用户明确要求长尾声时可以依据其原文合理安排例外，不能仅凭题材或画风自行推定例外。
review_feedback与previous_draft如果出现，表示旧稿已被拒绝，不是要求照搬的范文。新稿须重建主线解决过程、关键选择或代价在各段的分配，纠正实际过早解决的位置；不要保持过早解决不变，只补无关微动作、改段标签或换词填满余段。保留用户目标和已知首图事实，不机械保留被拒旧稿的情节安排。
对白可为空。严格采用timing_budget，8秒最多一小句，不让说话占掉主要动作；不可为了填时长创造旁白。所有逐字台词仅放在各自段的dialogue数组，action/goal/ending只写可见动作和情绪，不使用引号抄台词，不提前执行后段的回应或完整结局。用户明确给定的对白保留原语原字，若无法装入预算，应在故事安排中合理分句，不能加快语速硬塞或改写原话。动作和对白可自然并行，因果相继的动作不能同时完成。
beats的index从1到count连续递增，seconds均采用输入值；倒数阶段必须留足完成结局的动作。不要重写成系列剧框架，不添加用户未要求的任务、续集或制作说明。'''


def plan_request(brief, style, count, seconds, opening_observation=None):
    if not isinstance(brief, str) or not brief.strip():
        raise ValueError('brief不能为空')
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError('count须为正整数')
    seconds = _seconds(seconds)
    budget = _timing_budget(seconds)
    line = _object(speaker=_text(), text=dict(_text(), maxLength=budget['max_dialogue_characters_total']))
    beat = _object(index={'type': 'integer', 'minimum': 1, 'maximum': count},
                   seconds={'type': 'number', 'enum': [seconds]},
                   goal=_text(), action=_text(), turn=_text(), emotion=_text(),
                   ending=_text(), cast=_strings(uniqueItems=True),
                   dialogue={'type': 'array', 'items': line,
                             'maxItems': budget['max_dialogue_lines']})
    schema = _object(title=_text(), logline=_text(), ending=_text(),
        characters={'type': 'array', 'items': _object(
            id=_text(), name=_text(), appearance=_text(), role=_text())},
        props={'type': 'array', 'items': _object(
            id=_text(), name=_text(), appearance=_text(),
            count={'type': 'integer', 'minimum': 1})},
        invariants=_strings(),
        beats={'type': 'array', 'minItems': count, 'maxItems': count, 'items': beat})
    payload = {'brief': brief, 'style': style or '', 'count': count, 'seconds': seconds,
               'timing_budget': budget,
               'pacing_budget': _pacing_budget(count),
               'opening_observation': copy.deepcopy(opening_observation)}
    return _PLAN_SYSTEM, payload, schema


_REVIEW_SYSTEM = '''你是独立的短片剧本审阅者，不是原作者。只审本次plan与用户brief、style、count、seconds及opening_observation的匹配程度，输出规定JSON。不要因编号、字段齐全或作者自称完整而放行，也不要仅为挑错而发明问题。
本阶段验收目标是能够实际生成、主线完整的70～80分预览。issues只列阻断用户目标的重大问题：主线过早结束后大量填充、缺少结局、主要人物数量自相矛盾、关键道具凭空消失或瞬移、核心机制完全不成立、明显无法容纳的连续操作。轻微措辞、同一地形的近义名称、可并行的手指耳朵微动作、合理的仙侠虚构能力或温暖收尾的不同镜头选择，不应单独导致拒绝。不得为了让镜头更符合自己的审美，反复要求作者修改已经可用的内容。
检查pacing：每段是否推进有意义的信息、选择、阻碍、代价或关系；长故事不能在中段彻底达成最终目标，随后多段换姿势重复同一收尾。尾声比例须适合用户目标。静谧关系片可由理解和情感变化推进，不能因没有打斗追逐就判失败，也不要求每段都安排危险或反转。
依据实际动作与结果找出最早已经彻底解决主线的段号，不能相信作者把同一结果换名为“过渡”或把次要微动作标成新主线。pacing_budget.enforced=true时，核对这个实际解决段号不得早于main_resolution_not_before、其后尾声不得超过max_epilogue_beats；数字已由程序算好，不自行另算错。若不合预算，修复方向应重分主线的解决过程、选择或代价，不能只加微动作或要求删段却不说明如何维持用户请求的段数。count<6不强制比例；用户在brief明确要求长尾声时，可由你依据原文合理豁免，并在strengths说明引用依据，不能仅凭题材或画风自行认定例外。
检查causality：手段、施力、物体作用与结果有可见的逻辑链，关键选择确实改变局面；必要机制必须在计划里建立，不能靠旁白或一句宣称补上。题材容许的虚构能力也须前后自洽。
检查continuity：人物、道具数量、持有人、承托位置及环境状态跨段相接，改变有动作依据。characters.appearance只固定身份与稳定外貌，不把持物固定进去；invariants不能锁死可移动道具的位置却又安排移动。画外不等于消失，未知不等于已证实。首图已有事实要承接；首图未见的新内容可以在之后拟展示，不能冒充画面已经证实的前史。
检查ending：主线有真正的解决和相应可见结果，goal、turn、ending彼此一致，不突然改成另一种人物去向或用新任务代替结局。检查capacity：单段动作、表演与对白是否可在seconds内自然拍完，考虑timing_budget，不用加速、瞬移或多项相继动作同时完成来硬塞。
检查整段时间分配：行动或有信息的表演须覆盖motion_phases的开始、中段和最后阶段；若主要内容过早结束，剩余时间仅站看、冻结或重复动作，指出具体空等区间及如何发展原有行动，不建议通过裁短视频或重新生成视频解决。
只列有实际文本依据且影响用户目标的问题，优先关键的3～6项，可少于3项；beat填对应段号，全局问题为0。category只用pacing/causality/continuity/ending/capacity。reason指出计划中可核对的冲突或缺失，repair_direction给作者可操作的局部调整方向；不要写替代情节、替代台词、固定故事套路或整份新剧本。strengths只写实际有依据的优点，可为空。
passed仅在issues为空时为true；有任何需修复问题必须为false。这里只评估剧本，不能说已经看过或验收了尚未生成的视频。'''


def review_request(plan, brief, style, count, seconds, opening_observation=None):
    """Build an independent local-model review; do not manufacture a verdict."""
    if not isinstance(plan, dict):
        raise ValueError('plan须为JSON对象')
    if not isinstance(brief, str) or not brief.strip():
        raise ValueError('brief不能为空')
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError('count须为正整数')
    seconds = _seconds(seconds)
    issue = _object(beat={'type': 'integer', 'minimum': 0, 'maximum': count},
                    category={'type': 'string', 'enum': [
                        'pacing', 'causality', 'continuity', 'ending', 'capacity']},
                    reason=_text(), repair_direction=_text())
    schema = _object(passed={'type': 'boolean'},
                     issues={'type': 'array', 'maxItems': 10, 'items': issue},
                     strengths=_strings(maxItems=6))
    payload = {'stage': 'review', 'plan': copy.deepcopy(plan), 'brief': brief,
               'style': style or '', 'count': count, 'seconds': seconds,
               'opening_observation': copy.deepcopy(opening_observation),
               'timing_budget': _timing_budget(seconds),
               'pacing_budget': _pacing_budget(count)}
    return _REVIEW_SYSTEM, payload, schema


_OBSERVE_SYSTEM = '''你只做当前输入图片的视觉事实记录，不创作剧情。仅输出符合schema的JSON。
opening表示单张开场图；segment表示当前视频的抽样图片。previous_state用于身份匹配和标注最后已知事实，beat只说明预期动作，二者都不是本次画面已经完成的证据。只凭图片本身和明确提供的时间戳作判断。提示词说过不等于画面做过，音画解码成功不等于故事验收成功。
actual_visible总结当前拟采用结束点实际可见的事实。characters记录稳定ID、可见外观、位置、姿势、手中物；props记录关键道具数量、当前持有人或承托位置、外观与可见性。无证据的字段写unknown，不猜。新见角色用新的未解析ID，不猜是画外某人；身份匹配不清楚放入unknowns。previous_state中已知但当前出画的对象，可保留最后已知位置，visibility写offscreen，certainty写last_known；绝不把它描述成本次亲眼确认。
scene只写当前可见地形和建筑，不声称复制了完整地图。water只写可见水态、方向和变化；未见水不等于环境干涸，看到流水不能说已止水。记录姿势或发光，不据此推断封印、开门、交接或修复成功。手部遮挡、道具微小、镜头裁切均须说明。
actions只记录抽样序列中能直接支持的动作，completed只记录在采用结束点之前确有证据的本段目标。opening的actions和completed必须为空，单张静态图不能证明过去动作的完成。missing写预期但未展示的关键动作，不能把未展示一律当作确定失败；unknowns记录无法判断，continuity_risks记录复制、外观漂移、持物变化、空间漂移或不可续接姿势。保留实际偏差，不能用期待的结果替换事实。
keep_end_seconds仅依据图中或随图提供的真实时间戳建议最后一个有用、可续接的结束点。首图或缺少可靠时间信息时填null，不虚构6.5或8秒；不得截在关键动作完成之前。actual_visible及completed必须对应建议保留点，不能把更晚才发生的动作算进去。判断仅覆盖给出的抽样，不宣称全帧或音频验收。
若retention_policy为full_duration，则完整保留0到clip_duration_seconds：keep_end_seconds填该时长，actual_visible以最后实际抽样画面为准，不提前选取另一个结尾；尾部空等、冻结、重复或动作不足只写进continuity_risks，不请求裁切或重做。
beat_status采用achieved、partial、not_observed或unknown，只有关键目标实际可见才用achieved；未知不得自动升级为成功。'''


def observe_request(stage, previous_state=None, beat=None):
    aliases = {'opening': 'opening', 'initial': 'opening', 'image': 'opening',
               'segment': 'segment', 'clip': 'segment', 'video': 'segment'}
    normalized = aliases.get(str(stage).strip().lower())
    if normalized is None:
        raise ValueError('stage须为opening或segment')
    visibility = {'type': 'string', 'enum': ['visible', 'partial', 'offscreen', 'unknown']}
    certainty = {'type': 'string', 'enum': ['observed', 'last_known', 'unknown']}
    action_limit = {'maxItems': 0} if normalized == 'opening' else {}
    schema = _object(actual_visible=_text(), unknowns=_strings(),
        characters={'type': 'array', 'items': _object(
            id=_text(), appearance=_text(), position=_text(), pose=_text(),
            hands=_text(), visibility=copy.deepcopy(visibility), certainty=copy.deepcopy(certainty))},
        props={'type': 'array', 'items': _object(
            id=_text(), appearance=_text(), count={'type': ['integer', 'null'], 'minimum': 0},
            holder=_text(), location=_text(), visibility=copy.deepcopy(visibility),
            certainty=copy.deepcopy(certainty))},
        scene=_text(), water=_text(),
        actions=_strings(**action_limit), completed=_strings(**action_limit),
        missing=_strings(), continuity_risks=_strings(),
        beat_status={'type': 'string', 'enum': ['achieved', 'partial', 'not_observed', 'unknown']},
        keep_end_seconds=({'type': 'null'} if normalized == 'opening' else
                          {'type': ['number', 'null'], 'exclusiveMinimum': 0}),
        keep_end_reason=_text())
    payload = {'stage': normalized, 'previous_state': copy.deepcopy(previous_state),
               'beat': copy.deepcopy(beat),
               'evidence_scope': 'attached image samples only; not a full-video or audio review'}
    return _OBSERVE_SYSTEM, payload, schema


_WRITE_SYSTEM = '''你是电影导演，只写当前段真正能拍摄的镜头、表演、动作和声音。仅输出符合schema的JSON，不输出分析过程。
story_plan给完整故事方向，beat给本段叙事任务，actual_state给当前画面事实。事实优先级：当前实际画面与actual_state > 前段有证据的最后已知状态 > 原计划。上一段没有发生的动作不能写成已完成；未知仍是未知。保留本段goal和完整故事的最终目标，允许针对实际姿势、持物、水态或现有碎石等偏差调整本段执行办法。不要为了兑现原计划凭空生成大石、干渠、失踪物品或新角色。若关键前提未知，先在本段用可见动作揭示，再推进；不能伪造揭示结果。
同一个角色保持身份、发型、服装与体态；同一个道具保持当前实际数量、外形、材质、颜色、持有人或承托位置。实际已有漂移要如实承接并记录continuity_notes，不能突然把道具重置到最初参考图。画外对象保持最后已知状态，不等于被拿走、消失或重新出现。引用首图只约束起始画面，不重演以前追逐、交接或起跑。
分别填写integrated_multimodal_description、overall_soundscape、non_diegetic_music三个独立字符串，字段值只写其实际内容，不抄字段标题。不输出prompt，不写首帧前言、Picture或Subject定义、传输说明、JSON副本。mode=i2va时从实际首帧姿势立即推进；mode=t2va时自然建立开场画面。三个字段由程序机械包装，你只负责电影内容。
visual_style是用户指定且必须兑现的美术与摄影风格，不因题材自行改成动漫。真人写实时，英文正文必须明确live-action, photorealistic, real adult actors，并描述真人表演与实际摄影质感；其他风格同样用英文落实用户的visual_style。
t2va首段必须读取story_plan.characters中当前出场角色的设定，在镜头英文正文直接写清成年身份、关键发色、服装、已设定的面部特征及角色相对位置；不能只写中文名字，不能把风格或外貌只放在continuity_notes，不能自行补造人物卡未给出的外貌。画面首次出现时就要让这些身份特征可被实际生成，而非留到之后才说明。
integrated_multimodal_description从[Shot 1]开始，第一镜不加镜头起始时间戳；按真实秒数安排清楚的动作顺序。camera、composition、acting、interaction和同步物理声音服务于观众理解。最多一次有信息价值的切镜；若有[Shot 2]，格式为[Shot 2] At 00:SS.SSS, the camera cuts to...，切点严格在本段时长内。不要用慢动作、慢推、长准备、重复发光、反复凝视或突然瞬移填时间。8秒通常完成2～3个有因果结果的动作；无需机械凑满动作数。结尾留清楚可接的可见状态，不要求静止摆拍或停住两秒。
完整使用seconds，按timing_budget.motion_phases把开端、中段和末段都写成连续的有效动作或表演。15秒段落在英文镜头正文中自然说明first five seconds、next five seconds和final five seconds各自发生的动作发展，将明确动作安排一直写到接近第15秒，再用该时刻的状态续接；不能只在JSON时间轴写满、镜头正文却只有开头一个动作。短暂呼吸、眼神和情绪反应应带来新信息，后半段不允许无意义站立、定格、循环动作、拉长运镜或慢动作填充。最后一段在段尾完成原有结局。
描述镜头运动时写明类型，必要时写幅度和速度；运动跟随真实动作，不能用运镜速度冒充故事速度。重要交接、选择和结果给观众看清，复杂连续操作不能在同一瞬间全部完成。英文正文通常约140～220词用于6～8秒段落，按实际容量增减，不为凑字数增加事件或装饰。
dialogue_slots是唯一逐字对白来源。正文只写镜头、动作与表演，不写任何对白原话、译文、引号对白、占位符或<d>标签，也不用自己编排对白编号。structured dialogue数组逐项照填slots的speaker和text，原字原标点，不翻译、不加标签；start/end安排真实发声时段，delivery写非空英文说话者身份、语气和表演，例如说明某位成年角色压低声音说话，不抄原话，不放标签或(S1)编号。程序按时间、delivery与稳定speaker_id机械注入对白。没有slots时dialogue=[]，正文不能发明台词或旁白。对白必须遵守timing_budget；复杂操作期间不安排长句。overall_soundscape用英文写现场环境与物理动作声，不重复对白。non_diegetic_music只写英文乐器、速度、节奏；无配乐为N/A，跨段不每次重新介绍同一段音乐。
timeline只在JSON的timeline数组填写，不复制到三个视听字符串里；其他meta如expected_end_state、brief、dialogue、continuity_notes也各填其独立字段，不嵌进镜头正文。timeline列真实开始、结束秒数与可见动作结果，从0秒连续覆盖到seconds，无空档、不重叠，15秒段至少列三个发展阶段；对白可与自然动作并行，句与句不能重叠或超预算。expected_end_state是生成目标而非已验收事实；brief用简短中文说明推进；continuity_notes记录贴合实际画面的局部调整。最后一段必须完成剩余结局，不加新悬念或任务。'''


def _dialogue_language(text, declared=None):
    if declared:
        if not isinstance(declared, str) or not re.fullmatch(r'[A-Za-z][A-Za-z -]*', declared):
            raise ValueError('对白language须为不带标签的语言名称')
        return declared
    if re.search(r'[\u3040-\u30ff]', text):
        return 'Japanese'
    if re.search(r'[\uac00-\ud7af]', text):
        return 'Korean'
    if re.search(r'[\u4e00-\u9fff]', text):
        return 'Chinese'
    if re.search(r'[\u0400-\u04ff]', text):
        return 'Russian'
    if re.search(r'[\u0600-\u06ff]', text):
        return 'Arabic'
    return 'English' if re.search(r'[A-Za-z]', text) else 'Chinese'


def _dialogue_slots(plan, beat):
    lines = beat.get('dialogue') or []
    if not isinstance(lines, list):
        raise ValueError('beat.dialogue须为原话数组')
    aliases = {}
    for character in plan.get('characters') or []:
        if not isinstance(character, dict):
            continue
        canonical = character.get('id') or character.get('name')
        for key in ('id', 'name'):
            if character.get(key):
                aliases[character[key]] = canonical
    # H3 speaker numbering follows first vocal appearance in the complete
    # local plan, not the card order or the first speaker of this clip.
    speaker_ids = {}
    for planned_beat in plan.get('beats') or []:
        for line in planned_beat.get('dialogue') or []:
            if isinstance(line, dict) and isinstance(line.get('speaker'), str):
                identity = aliases.get(line['speaker'], line['speaker'])
                if identity not in speaker_ids:
                    speaker_ids[identity] = 'S%d' % (len(speaker_ids) + 1)
    slots = []
    for number, line in enumerate(lines, 1):
        if not isinstance(line, dict) or not all(isinstance(line.get(k), str) and line[k].strip()
                                                 for k in ('speaker', 'text')):
            raise ValueError('每句原对白须有非空speaker与text')
        if re.search(r'</?d>|\{\{D\d+\}\}', line['text'], re.I):
            raise ValueError('计划对白原文不能包含标签或占位符')
        speaker = line['speaker']
        identity = aliases.get(speaker, speaker)
        if identity not in speaker_ids:
            speaker_ids[identity] = 'S%d' % (len(speaker_ids) + 1)
        slots.append({'index': number,
                      'speaker': speaker, 'speaker_id': speaker_ids[identity],
                      'text': line['text'],
                      'language': _dialogue_language(line['text'], line.get('language'))})
    return slots


def write_request(plan, beat, actual_state, seconds, mode='i2va', visual_style=''):
    seconds = _seconds(seconds)
    mode = str(mode).strip().lower()
    if mode not in ('i2va', 't2va'):
        raise ValueError('mode须为i2va或t2va')
    if not isinstance(plan, dict) or not isinstance(beat, dict):
        raise ValueError('plan与beat须为JSON对象')
    budget = _timing_budget(seconds)
    slots = _dialogue_slots(plan, beat)
    time_field = {'type': 'number', 'minimum': 0, 'maximum': seconds}
    speaker_field = {'type': 'string', 'enum': list(dict.fromkeys(s['speaker'] for s in slots))} if slots else _text()
    text_field = {'type': 'string', 'enum': list(dict.fromkeys(s['text'] for s in slots))} if slots else _text()
    schema = _object(integrated_multimodal_description=_text(),
        overall_soundscape=_text(), non_diegetic_music=_text(),
        expected_end_state=_text(), brief=_text(),
        timeline={'type': 'array', 'minItems': 3 if seconds >= 12 else 1, 'maxItems': 4, 'items': _object(
            start=copy.deepcopy(time_field), end=copy.deepcopy(time_field),
            action=_text(), visible_result=_text())},
        dialogue={'type': 'array', 'minItems': len(slots), 'maxItems': len(slots),
                  'items': _object(speaker=speaker_field, text=text_field,
                      start=copy.deepcopy(time_field), end=copy.deepcopy(time_field), delivery=_text())},
        continuity_notes=_strings())
    payload = {'story_plan': copy.deepcopy(plan), 'beat': copy.deepcopy(beat),
               'actual_state': copy.deepcopy(actual_state), 'seconds': seconds,
               'mode': mode, 'timing_budget': budget,
               'visual_style': visual_style or plan.get('style') or '',
               'dialogue_slots': slots,
               'is_final': bool(plan.get('beats') and beat.get('index') ==
                                plan['beats'][-1].get('index'))}
    return _WRITE_SYSTEM, payload, schema


_AT_TIME = re.compile(r'\bAt\s+(\d{2}):(\d{2}(?:\.\d{1,3})?)\s*,', re.I)
_SHOT = re.compile(r'\[Shot\s+(\d+)\]')


def _time_value(match):
    return int(match[1]) * 60 + float(match[2])


def _normalize_shot_format(description):
    """Canonicalize unambiguous labels/cut spelling, never action timing."""
    description = re.sub(r'\[\s*shot\s*(\d+)\s*\]',
                         lambda match: '[Shot %d]' % int(match[1]),
                         description, flags=re.I)
    # Shot 1 implicitly starts at zero; preserve every nonzero action anchor.
    description = re.sub(r'(\[Shot 1\]\s*)At\s+0{1,2}:0{1,2}(?:\.0{1,3})?\s*,\s*',
                         r'\1', description, count=1, flags=re.I)

    def cut(match):
        return '%sAt %02d:%06.3f,' % (match[1], int(match[2]), float(match[3]))

    return re.sub(r'(\[Shot (?:[2-9]|[1-9]\d+)\]\s*)At\s+'
                  r'(\d{1,2}):(\d{1,2}(?:\.\d{1,3})?)\s*,',
                  cut, description, flags=re.I)


def _insert_dialogue(description, events, seconds):
    shots = list(_SHOT.finditer(description))
    if not shots or description[:shots[0].start()].strip():
        raise ValueError('镜头正文须从[Shot 1]开始')
    if [int(shot[1]) for shot in shots] != list(range(1, len(shots) + 1)):
        raise ValueError('Shot编号须从1连续递增')
    blocks, starts = [], []
    for index, shot in enumerate(shots):
        end = shots[index + 1].start() if index + 1 < len(shots) else len(description)
        block = description[shot.start():end].strip()
        if index:
            cut = _AT_TIME.match(block[len(shot.group(0)):].lstrip())
            if cut is None:
                raise ValueError('后续Shot须在编号后给出At切点时间')
            start = _time_value(cut)
            if not starts[-1] < start < seconds:
                raise ValueError('Shot切点须严格递增且位于本段时长内')
        else:
            start = 0.0
        starts.append(start)
        blocks.append(block)
    for index, block in enumerate(blocks):
        stop = starts[index + 1] if index + 1 < len(starts) else seconds
        anchors = list(_AT_TIME.finditer(block))
        times = [_time_value(anchor) for anchor in anchors]
        if any(not starts[index] <= value < stop for value in times):
            raise ValueError('已有At时间标记不在对应Shot范围内')
        if times != sorted(times):
            raise ValueError('已有At时间标记须按时间递增')
        insertions = {}
        for start, spoken in events:
            if not starts[index] <= start < stop:
                continue
            position = next((anchor.start() for anchor in anchors if _time_value(anchor) > start), len(block))
            insertions.setdefault(position, []).append(spoken)
        for position in sorted(insertions, reverse=True):
            block = block[:position].rstrip() + '\n' + '\n'.join(insertions[position]) + '\n' + block[position:].lstrip()
        blocks[index] = block.strip()
    return '\n\n'.join(blocks)


def compile_written(obj, payload):
    """Bind source-owned words and transport without modifying a model reply."""
    if not isinstance(obj, dict) or not isinstance(payload, dict):
        raise ValueError('写词结果与payload须为JSON对象')
    mode = payload.get('mode', 'i2va')
    if mode not in ('i2va', 't2va'):
        raise ValueError('mode须为i2va或t2va')
    fields = ('integrated_multimodal_description', 'overall_soundscape', 'non_diegetic_music')
    content = {}
    transport = re.compile(r'(?:"?(?:prompt|timeline|expected_end_state|brief|dialogue|continuity_notes|'
                           r'integrated_multimodal_description|overall_soundscape|non_diegetic_music)"?\s*:)', re.I)
    for field in fields:
        value = obj.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(field + '须为非空字符串')
        if transport.search(value) or re.search(r'<(?:Picture|Subject)\s+\d+>|For the target video,', value, re.I):
            raise ValueError(field + '混入传输字段或首帧前言')
        if re.search(r'</?d>', value, re.I):
            raise ValueError('正文不能自行写对白标签，请使用指定占位符')
        content[field] = value.strip()
    slots = payload.get('dialogue_slots') or []
    description = _normalize_shot_format(content[fields[0]])
    if any(re.search(r'\{\{\s*[dD]', content[field]) for field in fields):
        raise ValueError('正文不能手写对白slot或占位符')
    dialogue = obj.get('dialogue')
    if not isinstance(dialogue, list) or len(dialogue) != len(slots):
        raise ValueError('dialogue项目数必须等于固定原话slot数')
    seconds = _seconds(payload.get('seconds'))
    events, previous_end = [], 0.0
    for line, slot in zip(dialogue, slots):
        if not isinstance(line, dict) or any(line.get(k) != slot[k] for k in ('speaker', 'text')):
            raise ValueError('dialogue须按slot顺序保留原说话者和原字，不得翻译或带标签')
        delivery = line.get('delivery')
        if not isinstance(delivery, str) or not delivery.strip() or not re.search(r'[A-Za-z]', delivery):
            raise ValueError('dialogue.delivery须为非空英文身份与语气表演')
        if re.search(r'</?d>|\{\{|\(S\d+\)', delivery, re.I):
            raise ValueError('delivery不能包含对白标签、slot或说话者编号')
        if slot['text'] in '\n'.join([*content.values(), delivery]):
            raise ValueError('对白原字只能由结构化dialogue注入，正文或delivery不能重复抄写')
        start, end = line.get('start'), line.get('end')
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or
               not math.isfinite(value) for value in (start, end)):
            raise ValueError('对白起止时间须为有限数字')
        if not previous_end <= start < end <= seconds:
            raise ValueError('对白须按时间顺序且不重叠、不超出本段')
        previous_end = end
        events.append((start, 'At %02d:%06.3f, %s (%s), <d>[%s] %s</d>.' %
                       (int(start // 60), start % 60, delivery.strip().rstrip('.,'),
                        slot['speaker_id'], slot['language'], slot['text'])))
    description = _insert_dialogue(description, events, seconds)
    content[fields[0]] = description
    result = copy.deepcopy(obj)
    prompt = '\n\n'.join(field + ': ' + content[field] for field in fields)
    result['prompt'] = (I2VA_FIRST_FRAME + '\n\n' if mode == 'i2va' else '') + prompt
    return result
