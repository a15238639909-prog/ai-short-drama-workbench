"""Chinese director policies for new image/text tasks; reference stays unchanged."""
import copy

from . import story_video_prompts as base
from . import story_video_reference_prompts as chinese


PROFILE = 'story-mode-director-zh-v1'
I2VA_FIRST_FRAME = base.I2VA_FIRST_FRAME

_IMAGE_POLICY = '''
本任务是图生故事，story_input_mode=image在后续段保持不变。当前实际画面优先；承接当前可见场景、地标、人物外形、姿势与持物，不能凭原图或旧状态假称当前仍露脸或仍在原处。尽可能延续当前场景及空间关系，剧情确实需要换场才通过可见行动或合理转场建立新地点，不把场景不变当成硬要求。
人物自然互动时优先采用清晰正面或三分之四面部视角，适度运镜，让关键动作与反应看得清。有后续段时，尽量在段尾的正常行动中看清续接所需主要人物面部；不强制转正、直视镜头、冻结或摆拍，不阻碍剧情，不凭提示词承诺锁脸。无人物或无可辨面部主体不强加人脸任务。露脸、场景延续、运镜和镜头数量均为可选导演建议，不增加验收阻断条件。
'''

_TEXT_POLICY = '''
本任务是文生故事，story_input_mode=text在后续使用i2va真实尾帧续接时仍保持文生导演策略。主体有真实动态并与环境互动，利用前后景纵深交代空间；因果动作、关键选择和反应须可见，运动、景别与多个镜头服务具体剧情和新信息，换场按故事自然发生并交代空间衔接。
避免无意义慢推、静站、空泛光影、机械切镜或绕主体旋转来填时间。尊重安静题材，细微表演与环境互动同样可以推进故事；不强制动作片节奏，不规定镜头数，不要求每段展示全套景别，也不新增逐人露脸任务。这些摄影偏好只提供建议，不增加验收阻断条件。
'''

_IMAGE_PLAN_POLICY = '''
图生故事优先承接开场图已观察到的场景、地标与人物外形，尽可能在当前空间发展故事；剧情需要换场时给出合理过渡。镜头、露脸及段尾角度由后续导演结合实际画面安排，不在计划中锁死姿势、机位或场景。
'''
_TEXT_PLAN_POLICY = '''
文生故事通过主体行动、环境互动、因果与关键反应推进，交代前后空间及自然换场；安静题材可由细微表演推进，不强加追逐、固定镜头数或逐人露脸。具体摄影由后续导演依据实际状态安排。
'''

_REVIEW_POLICY = '''
本模式的露脸、场景不变、镜头数量、运镜和动静偏好只作导演建议，不能成为新增阻断标准，不能仅因此判passed=false或要求重拍。blocking_only=true时可将有依据的建议列为advisory；没有severity字段时，仅审美偏好不加入要求修复的issues。仍按原规则核对用户明确的核心剧情、原对白、因果、结尾与实际时长，不把建议伪装成已证实的契约错误。
按timing_contract和duration_contract_text解释时长：segment_count是全片段数，segment_seconds及seconds均为每段时长，total_seconds才是全片总时长，seconds_scope=per_segment。timing_budget只约束单段，不是全片总容量；不得把多段时长相加后与单段seconds比较，也不得为满足误读的全片seconds而缩短每段。
参数依据只能使用parameter_sources提供的单条连续原文字符串，本请求仅提供duration_contract_text；如有参数冲突，source_quote应从该句逐字摘录，不引用JSON键、数字或自行拼接的字段表达式，不把计划内提到的秒数替代本请求的时长约定。对白、剧情和计划事实仍只引用brief或plan内连续原文，不把参数依据当成剧情错误证据。任何建议若没有可靠且可逐字引用的依据就不列issues，不设最低条数；没有可证实问题时issues=[]。对确有明确依据的核心错误仍按原blocking规则处理，不能因为本次参数说明而忽略真实超时或剧情冲突。
'''

_REVIEW_SOURCE_RULE = 'source_quote必须是用户brief或plan内任一字符串中的连续原文片段，逐字引用，不改写、不拼接不同位置；'
_REVIEW_SOURCE_RULE_V2 = 'source_quote必须是用户brief或plan内任一字符串、或parameter_sources中单条字符串的连续原文片段，逐字引用，不改写、不拼接不同位置；'

_REFERENCE_FACE_NOTE = '\n如画面具有可辨面部的角色，逐一在appearance、pose或continuity_risks'
_IMAGE_OBSERVE = '''
face_view与face_clarity只记录本次当前拟采用结束点实际可见的脸，不复制previous_state中的脸部结论。face_view只用front、three_quarter、profile、back、occluded、offscreen或unknown；face_clarity只用clear、blurred、occluded、too_small、not_visible或unknown。当前出画写offscreen/not_visible；背影不写正面，遮挡、太小、模糊或无法判断如实记录，不凭人物身份或旧图推断当前脸清晰。无可辨面部的主体可用unknown/not_visible，不增加人物。
scene_anchors为当前画面实际可见的稳定地标、陈设及相对方位的简短字符串数组；没有可辨地标时为空数组。不能复制旧场景的不可见地标，也不把计划中的新场景当成已发生事实。以上字段用于事实记录，脸未露清或场景变化仅记录风险，不自动拒绝视频或要求重拍。
'''
_TEXT_OBSERVE = '''
本次只记录当前画面可见主体、真实动作、环境互动、前后景空间及可核对的状态变化；actual_visible可概括实际视觉类型。previous_state仅用于身份匹配和标注最后已知事实，不是当前证据。不额外逐人检查露脸角度或清晰度，不因静谧题材、单镜头或没有人脸判为失败。
'''


class _ModePrompts:
    I2VA_FIRST_FRAME = I2VA_FIRST_FRAME

    def __init__(self, input_mode):
        self.input_mode = input_mode

    def _request(self, request, policy):
        system, payload, schema = copy.deepcopy(request)
        payload.update(director_profile=PROFILE, story_input_mode=self.input_mode)
        return system + policy, payload, schema

    @property
    def _policy(self):
        return _IMAGE_POLICY if self.input_mode == 'image' else _TEXT_POLICY

    def plan_request(self, brief, style, count, seconds, opening_observation=None):
        return self._request(chinese.plan_request(brief, style, count, seconds,
            copy.deepcopy(opening_observation)),
            _IMAGE_PLAN_POLICY if self.input_mode == 'image' else _TEXT_PLAN_POLICY)

    def review_request(self, plan, brief, style, count, seconds,
                       opening_observation=None, blocking_only=False):
        system, payload, schema = self._request(chinese.review_request(copy.deepcopy(plan), brief, style,
            count, seconds, copy.deepcopy(opening_observation), blocking_only=blocking_only),
            _REVIEW_POLICY)
        if blocking_only:
            if system.count(_REVIEW_SOURCE_RULE) != 1:
                raise RuntimeError('中文审稿依据规则已变化，无法安全扩展参数依据')
            system = system.replace(_REVIEW_SOURCE_RULE, _REVIEW_SOURCE_RULE_V2, 1)
        count, seconds = payload['count'], payload['seconds']
        total_seconds = count * seconds
        duration_contract_text = (f'共{count}段，每段{seconds:g}秒，全片{total_seconds:g}秒。'
                                  'seconds与timing_budget仅约束单段。')
        payload.update(review_evidence_policy='story-review-evidence-v2',
            timing_contract=dict(segment_count=count, segment_seconds=seconds,
                total_seconds=total_seconds, seconds_scope='per_segment'),
            duration_contract_text=duration_contract_text,
            parameter_sources=[duration_contract_text])
        return system, payload, schema

    def write_request(self, plan, beat, actual_state, seconds,
                      mode='i2va', visual_style=''):
        if mode not in ('i2va', 't2va'):
            raise ValueError('图生/文生导演仅支持i2va或t2va')
        return self._request(chinese.write_request(copy.deepcopy(plan), copy.deepcopy(beat),
            copy.deepcopy(actual_state), seconds, mode=mode, visual_style=visual_style), self._policy)

    def observe_request(self, stage, previous_state=None, beat=None):
        system, payload, schema = self._request(chinese.observe_request(stage,
            copy.deepcopy(previous_state), copy.deepcopy(beat)), '')
        if self.input_mode == 'image':
            character = schema['properties']['characters']['items']
            character['properties'].update(
                face_view=dict(type='string', enum=['front', 'three_quarter', 'profile',
                    'back', 'occluded', 'offscreen', 'unknown']),
                face_clarity=dict(type='string', enum=['clear', 'blurred', 'occluded',
                    'too_small', 'not_visible', 'unknown']))
            character['required'].extend(['face_view', 'face_clarity'])
            schema['properties']['scene_anchors'] = dict(type='array', items=dict(type='string', minLength=1))
            schema['required'].append('scene_anchors')
            system += _IMAGE_OBSERVE
        else:
            # The shared Chinese observer appends this one face-specific
            # paragraph. Replace it locally so text tasks gain no face burden.
            start = system.find(_REFERENCE_FACE_NOTE)
            if start < 0:
                raise RuntimeError('中文观察规则已变化，无法隔离文生观察策略')
            system = system[:start] + _TEXT_OBSERVE
        return system, payload, schema

    def compile_written(self, obj, payload):
        payload = copy.deepcopy(payload)
        if payload.get('mode', 'i2va') not in ('i2va', 't2va'):
            raise ValueError('图生/文生导演仅支持i2va或t2va')
        result = chinese.compile_written(copy.deepcopy(obj), payload)
        if payload.get('mode', 'i2va') == 'i2va':
            prefix = chinese.I2VA_FIRST_FRAME + '\n\n'
            if not result['prompt'].startswith(prefix):
                raise RuntimeError('中文图生编译首帧声明已变化，未静默替换')
            result['prompt'] = I2VA_FIRST_FRAME + '\n\n' + result['prompt'][len(prefix):]
        return result


def for_mode(input_mode):
    if input_mode not in ('image', 'text'):
        raise ValueError('导演策略须按任务input_mode选择image或text')
    return _ModePrompts(input_mode)
