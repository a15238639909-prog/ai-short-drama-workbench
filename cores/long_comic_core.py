"""Independent, checkpointed long comics; no model or worker starts on import."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
import uuid
import zipfile

from . import runtime_reset, store, long_comic_characters, long_comic_continuity, long_comic_edit

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = store.DATA / 'long_comics'
OUTPUT_ROOT = ROOT / 'outputs' / 'long_comics'
_LOCK = threading.RLock()
_RUN_LOCK = threading.Lock()
_THREADS = {}
ACTIVE = {'queued', 'running', 'pausing'}
MODEL = 'moss'

BOOK_SYSTEM = '''你是长篇漫画编辑，输出一个JSON对象。依据用户故事及人物设定规划完整作品，不改变用户要求。
格式：{"premise":"全书核心事件","ending":"完整结局","characters":[{"id":"c01","name":"姓名","appearance":"稳定身份和脸发特征","clothing":"初始衣着","personality":"性格"}],"chapters":[{"number":1,"title":"章名","summary":"本章明确事件、原因与结果","ending_state":"本章计划结束状态"}]}。
chapters数量严格等于chapter_count，编号从1连续。已有characters的id/name必须原样沿用，不增加同名副本；如尚无人设，可按用户故事建立必要人物。外貌、服装和世界设定一致，允许明确有剧情依据的换装。只用正向具体描述，不写负面提示词。所有自然语言使用简体中文。'''

CHAPTER_SYSTEM = '''你是漫画编剧和分镜师，输出一个JSON对象，只改编指定章，不提前执行以后章节。
格式：{"ending_state":"本章结束时的叙事状态","pages":[{"number":1,"summary":"本页事件","scene":"本页具体场景","character_ids":["c01"],"wardrobe":{"c01":"本页当前衣着"},"opening_state":"本页开始状态","ending_state":"本页结果及衣着、持物、位置","panels":[{"number":1,"description":"可拍的动作、景别和机位","dialogue":[{"speaker":"c01","text":"逐字对白"}],"narration":"旁白或空字符串"}]}]}。
页数严格等于pages_per_chapter，页编号从1连续，每页1至4格，格编号连续。character_ids仅列本页实际出场的既有人物ID；不把全书人物全部塞进每页；没有人物时为空数组。对白speaker必须属于本页人物ID；旁白单列。场景按本章剧情选择。
previous_chapter_state和本页前一页ending_state是已保存的叙事计划，不是实际图像观察。按其已发生的事件接续：已脱下的衣服只有明确重新穿上后才恢复；持物、伤势与事件后果持续。wardrobe只写本页实际有剧情依据的穿戴，不臆造内搭或衣物去向。重要动作按格顺序发生，不把结尾提前到第一格。
画面将由Moss整页原生生成分格、气泡、中文对白与旁白，程序按人物ID绑定说话者。description写画面，对白单列以便准确引用和编辑。采用清楚的正向描述，所有自然语言为简体中文。'''

CHAPTER_SYSTEM_V2 = '''你是漫画编剧和分镜师，只输出指定章的一个JSON对象，所有自然语言用简体中文。
格式：{"pages":[{"number":1,"summary":"本页事件","scene":"具体场景","panels":[{"number":1,"character_ids":["c01"],"description":"本格可见动作、表情、景别及动作完成后的状态","dialogue":[{"speaker":"c01","text":"逐字对白"}],"narration":"旁白或空字符串","state_changes":[{"character_id":"c01","field":"held_items","value":"右手拿着钥匙","evidence":"拿起桌上的钥匙"}]}]}]}。
story是用户原故事，优先于chapter摘要。原故事明确指定的页、格数、人物动作、先后顺序和逐字对白必须保留；不得为摘要删掉或改写。source_requirements是程序从明确格式提取的硬性要求，必须满足；未提取出的原故事要求仍须遵守。仅在原故事未指定时，每页通常2至3格，必要时1至4格。页数等于pages_per_chapter，页和格编号从1连续。一格一个清楚的可见瞬间，不把以后格的事件提前。每格character_ids仅列实际出场人物，不把全书人物塞入每格；description只写本格可见人物，提到的既有人物姓名或ID必须列入本格character_ids。对白speaker必须属于本格character_ids。空镜人物与对白均为空数组。
characters中的身份与已有外貌保持原样，性格只用于理解故事，表情与动作须来自本格事件。衣物、持物、伤势和位置承接previous_chapter_state及前格状态；它们是已保存叙事状态，不是图像观察。
state_changes只记录本格明确发生的变化，未变化为[]。field仅clothing、held_items、injury、location，value写完整结果，clothing为此刻全部实际穿戴。evidence逐字引用本格画面或本页原故事中的动作依据，包含脱下、穿好、拿起、放下、包扎、走到等真实变化动作；description可以只呈现完成态，不必重复动作原文。原故事已标页码时不能引用其他页动作。“穿着旧外套”“双手空着”“握在右手”这类静态描述不能单独作为变化依据。每格每人每字段至多一次变更，人物必须出场。物品交接须在同格同时更新转交双方的held_items：原主人移除物品、接收者增加物品；空手写“双手空空”。同一明确物品不能同时被两人持有；确有多件同类物品时，本格画面或本页原故事须明确各自、另一件或数量依据。状态未知时不猜测；未重新穿回的外套不能在后格恢复。
description描写该动作完成后可见的明确状态，避免同格展示前后两套穿戴。当前状态由程序按state_changes依次计算，不需要输出opening_state、ending_state或wardrobe。
Moss直接生成整页分格、气泡及中文文字；description写画面，对白和旁白单列。'''


class BusyError(RuntimeError):
    pass


class _Paused(RuntimeError):
    pass


def _text(value, name, limit, required=False):
    if not isinstance(value, str):
        raise ValueError(name + '必须为文字')
    value = value.strip()
    if required and not value:
        raise ValueError('请填写' + name)
    if len(value) > limit:
        raise ValueError(name + '过长，最多%d字' % limit)
    return value


def _integer(value, name, minimum, maximum):
    if isinstance(value, bool):
        raise ValueError(name + '必须为整数')
    try:
        result = int(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError(name + '必须为整数') from error
    if not minimum <= result <= maximum:
        raise ValueError('%s须为%d至%d' % (name, minimum, maximum))
    return result


def _characters(value):
    if isinstance(value, str):
        value = [dict(name=line.split('：', 1)[0].strip(), appearance=line.strip())
                 for line in value.splitlines() if line.strip()]
    if not isinstance(value, list) or len(value) > 12:
        raise ValueError('人物须为数组或逐行文字，最多12人')
    rows, identities, names = [], set(), set()
    for index, supplied in enumerate(value, 1):
        if not isinstance(supplied, dict):
            raise ValueError('人物项必须为对象')
        identity = str(supplied.get('id') or 'c%02d' % index)
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', identity) or identity in identities:
            raise ValueError('人物ID无效或重复')
        name = _text(supplied.get('name', ''), '人物姓名', 80, True)
        if name in names:
            raise ValueError('人物姓名重复')
        row = dict(id=identity, name=name)
        for key in ('appearance', 'clothing', 'personality'):
            row[key] = _text(supplied.get(key) or '', '人物设定', 1000)
        rows.append(row)
        identities.add(identity)
        names.add(name)
    return rows


def list_character_sources():
    return long_comic_characters.list_character_sources()


def get_source_characters(story_id):
    return long_comic_characters.get_source_characters(story_id)


def bind_reference(project_id, character_id, image_data):
    return long_comic_edit.bind_reference(project_id, character_id, image_data)


def adopt_version(project_id, chapter, page, version):
    return long_comic_edit.adopt_version(project_id, chapter, page, version)


def _v2(project):
    return project.get('continuity_version') == 2


def _select_source(project, data):
    supplied = {'source_story_id', 'source_character_ids'} & set(data)
    if not supplied:
        return
    if not _v2(project):
        raise ValueError('旧版漫画保留原有规则，请新建漫画后复用项目人设')
    if project.get('outline'):
        raise ValueError('已有规划后不能更换来源人设，请新建漫画')
    story_id = data.get('source_story_id')
    identities = data.get('source_character_ids')
    if story_id in (None, '') and identities in (None, []):
        previous = project.pop('character_source', None)
        if previous and 'characters' not in data:
            project['characters'] = []
        return
    cast, source = long_comic_characters.import_characters(story_id, identities)
    project['characters'] = _characters(cast)
    project['character_source'] = source


def _recalculate_chapter(project, chapter, changed_from):
    previous_ending = copy.deepcopy(chapter['ending_state'])
    rows, ending = long_comic_continuity.recalculate(chapter['pages'], project['characters'],
        chapter['opening_state'], _text, evidence_context=long_comic_continuity.source_evidence_context(
            project['story'], chapter_number=chapter['number']))
    for page in rows:
        if page['number'] < changed_from:
            continue
        page['needs_redo'] = bool(page.get('versions'))
        if not page.get('prompt_edited'):
            page.update(prompt=_compile_prompt(project, page), prompt_dirty=False)
    chapter.update(pages=rows, ending_state=ending,
        wardrobe_state={identity: value['clothing'] for identity, value in ending.items()})
    if any(page.get('needs_redo') or page.get('prompt_dirty') for page in rows):
        chapter['status'] = 'partial'
    if previous_ending != ending:
        _mark_later_chapters(project, chapter['number'])


def _mark_later_chapters(project, number):
    for chapter in project['chapters']:
        if chapter['number'] > number and chapter.get('pages'):
            chapter['continuity_review_required'] = True


def _require_current_chapter(chapter):
    if chapter and chapter.get('continuity_review_required'):
        raise ValueError('第%d章的前章衔接已改变，请先重新规划该章' % chapter['number'])


def _path(project_id):
    if not isinstance(project_id, str) or not re.fullmatch(r'LC_[0-9a-f]{12}', project_id):
        raise ValueError('漫画项目ID无效')
    return DATA_ROOT / project_id / 'project.json'


def _load(project_id):
    path = _path(project_id)
    if not path.is_file():
        raise ValueError('漫画项目不存在')
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('id') != project_id or data.get('schema') != 1:
        raise ValueError('漫画项目格式不兼容，原数据已保留')
    return data


def _save(project):
    project['updated'] = time.time()
    project['revision'] = int(project.get('revision') or 0) + 1
    store.save_json(_path(project['id']), project)


def _url(path):
    return '/files/' + Path(path).resolve().relative_to(ROOT.resolve()).as_posix()


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _recover(project):
    job = project.get('job') or {}
    thread = _THREADS.get(job.get('id'))
    if job.get('status') in ACTIVE and (thread is None or not thread.is_alive()):
        job.update(status='interrupted', step='服务中断，等待手动恢复', updated=time.time())
        _save(project)
    return project


def get_project(project_id):
    with _LOCK:
        return copy.deepcopy(_recover(_load(project_id)))


def list_projects():
    with _LOCK:
        result = []
        if not DATA_ROOT.exists():
            return result
        for path in DATA_ROOT.glob('LC_*/project.json'):
            project = _recover(_load(path.parent.name))
            result.append({key: copy.deepcopy(project.get(key)) for key in (
                'id', 'title', 'style', 'chapter_count', 'pages_per_chapter', 'archived',
                'created', 'updated', 'revision', 'job')})
        return sorted(result, key=lambda row: row['updated'], reverse=True)


def active_tasks():
    with _LOCK:
        result = []
        if not DATA_ROOT.exists():
            return result
        for path in DATA_ROOT.glob('LC_*/project.json'):
            project = _recover(_load(path.parent.name))
            job = project.get('job') or {}
            if job.get('status') not in ACTIVE:
                continue
            result.append(dict(id=project['id'], job_id=job['id'], kind='long_comic',
                project_id=project['id'], title='长篇漫画：' + project['title'], status=job['status'],
                done=job.get('done', 0), total=job.get('total', 0), detail=job.get('step') or '',
                step_label=job.get('step') or '', action=job.get('action'), chapter=job.get('chapter'),
                page=job.get('page'), created=job.get('created'), updated=job.get('updated')))
        return result


def _busy(job):
    if not isinstance(job, dict) or job.get('cleared_at'):
        return False
    status = job.get('status')
    if status in ('paused', 'cancelled', 'completed', 'failed', 'interrupted', 'idle', 'waiting_user'):
        return False
    return bool(job.get('running') or job.get('active') or status in (
        'queued', 'running', 'loading', 'starting', 'retrying', 'waiting_resource', 'waiting_task'))


def _other_tasks_guard():
    from . import runtime_core
    if any(row.get('kind') != 'long_comic' and _busy(row) for row in runtime_core.list_tasks(limit=None)):
        raise BusyError('其他工具已有排队或运行任务，请等完成或暂停后再制作漫画')
    for name in ('api.saga_api', 'api.timeline'):
        module = sys.modules.get(name)
        jobs = getattr(module, '_JOBS', {}) if module else {}
        if isinstance(jobs, dict) and any(_busy(row) for row in list(jobs.values())):
            raise BusyError('主流程正在制作故事或视频，请等完成或暂停后再制作漫画')
    apps = [getattr(sys.modules.get(name), '_APP', None) for name in ('__main__', 'server')]
    apps.append(sys.modules.get('app'))
    for app in apps:
        if app is None:
            continue
        if _busy(getattr(app, 'ACTIVITY', {})) or _busy(getattr(app, 'AUTO_JOB', {})):
            raise BusyError('内嵌工具正在执行任务，请等完成或暂停后再制作漫画')
        for name in ('OPS', 'VC_JOBS'):
            jobs = getattr(app, name, {})
            if isinstance(jobs, dict) and any(_busy(row) for row in list(jobs.values())):
                raise BusyError('内嵌工具已有模型任务，请等完成或暂停后再制作漫画')
        manager = getattr(getattr(app, 'video_gen_core', None), 'MANAGER', None)
        tasks = getattr(manager, 'tasks', {})
        if isinstance(tasks, dict) and any(_busy(row) for row in list(tasks.values())):
            raise BusyError('内嵌视频工具正在执行任务，请等完成或暂停后再制作漫画')


def create_project(data):
    if not isinstance(data, dict):
        raise ValueError('项目设置须为对象')
    from models import moss_client
    width, height = moss_client.dimensions(data.get('base_w', 768), data.get('base_h', 1360))
    raw_characters = data.get('characters') or []
    characters_text = data.get('characters_text') or (raw_characters if isinstance(raw_characters, str) else '')
    project = dict(schema=1, continuity_version=2, id='LC_' + uuid.uuid4().hex[:12],
        title=_text(data.get('title') or '未命名长篇漫画', '标题', 120, True),
        story=_text(data.get('story') or '', '故事', 16000, True),
        style=_text(data.get('style') or '彩色叙事漫画，清晰线稿，分镜节奏明确', '画风', 2000, True),
        characters_text=_text(characters_text, '原始人物设定', 6000),
        characters=_characters(raw_characters if isinstance(raw_characters, list) else []),
        chapter_count=_integer(data.get('chapter_count', 3), '章节数', 1, 20),
        pages_per_chapter=_integer(data.get('pages_per_chapter', 4), '每章页数', 1, 12),
        base_w=width, base_h=height, model=MODEL, archived=False,
        outline=None, chapters=[], job=None, created=time.time(), revision=0)
    _select_source(project, data)
    with _LOCK:
        _save(project)
    return copy.deepcopy(project)


def _chapter(project, number):
    number = _integer(number, '章节', 1, project['chapter_count'])
    return next((row for row in project['chapters'] if row['number'] == number), None)


def _page(chapter, number):
    if not chapter:
        raise ValueError('请先规划本章')
    number = _integer(number, '页码', 1, 12)
    row = next((row for row in chapter.get('pages') or [] if row['number'] == number), None)
    if row is None:
        raise ValueError('本章尚无该页')
    return row


def _panels(value, selected):
    if not isinstance(value, list) or not 1 <= len(value) <= 4:
        raise ValueError('每页须为1至4格')
    result = []
    for number, panel in enumerate(value, 1):
        if not isinstance(panel, dict) or panel.get('number') != number:
            raise ValueError('格编号须从1连续')
        lines = panel.get('dialogue')
        if not isinstance(lines, list) or len(lines) > 6:
            raise ValueError('每格对白须为数组，最多6句')
        dialogue = []
        for line in lines:
            if not isinstance(line, dict) or line.get('speaker') not in selected:
                raise ValueError('对白人物不属于本页出场人物')
            dialogue.append(dict(speaker=line['speaker'], text=_text(line.get('text', ''), '对白', 180, True)))
        result.append(dict(number=number, description=_text(panel.get('description', ''), '分格画面', 1800, True),
            dialogue=dialogue, narration=_text(panel.get('narration', ''), '旁白', 240)))
    return result


def update_project(project_id, data):
    if not isinstance(data, dict):
        raise ValueError('修改内容须为对象')
    with _LOCK:
        project = _recover(_load(project_id))
        if (project.get('job') or {}).get('status') in ACTIVE:
            raise BusyError('请先暂停任务，完成当前页后再修改')
        if {'prompt', 'panels', 'summary'} & set(data):
            current_chapter = _chapter(project, data.get('chapter', 1))
            page = _page(current_chapter, data.get('page'))
            if 'panels' in data:
                page['panels'] = (long_comic_continuity.panels(data['panels'], project['characters'], _text,
                    evidence_context=long_comic_continuity.source_evidence_context(project['story'],
                        page['number'], current_chapter['number']))
                                  if _v2(project) else _panels(data['panels'], page['character_ids']))
            if 'summary' in data:
                page['summary'] = _text(data['summary'], '本页剧情', 1000, True)
            if {'panels', 'summary'} & set(data):
                page.update(prompt='', prompt_dirty=True, prompt_edited=False)
                if _v2(project):
                    _recalculate_chapter(project, current_chapter, page['number'])
                    page = _page(current_chapter, data.get('page'))
            if 'prompt' in data:
                page.update(prompt=_text(data['prompt'], '本页正向提示词', 16000, True),
                            prompt_edited=True, prompt_dirty=False)
            page['needs_redo'] = bool(page.get('versions'))
            if _v2(project) and (page.get('needs_redo') or page.get('prompt_dirty')):
                current_chapter['status'] = 'partial'
        structural = {'story', 'style', 'characters', 'characters_text', 'chapter_count', 'pages_per_chapter',
                      'base_w', 'base_h', 'source_story_id', 'source_character_ids'} & set(data)
        if project.get('outline') and structural:
            raise ValueError('已有规划后不覆盖全书设定；请新建项目，页级提示词可单独编辑')
        if 'title' in data:
            project['title'] = _text(data['title'], '标题', 120, True)
        for key, limit in (('story', 16000), ('style', 2000)):
            if key in data:
                project[key] = _text(data[key], key, limit, True)
        if 'characters' in data:
            if isinstance(data['characters'], str):
                project['characters_text'] = _text(data['characters'], '原始人物设定', 6000)
                project['characters'] = []
            else:
                project['characters'] = _characters(data['characters'])
        if 'characters_text' in data:
            project['characters_text'] = _text(data['characters_text'], '原始人物设定', 6000)
        _select_source(project, data)
        for key, upper in (('chapter_count', 20), ('pages_per_chapter', 12)):
            if key in data:
                project[key] = _integer(data[key], key, 1, upper)
        if {'base_w', 'base_h'} & set(data):
            from models import moss_client
            project['base_w'], project['base_h'] = moss_client.dimensions(
                data.get('base_w', project['base_w']), data.get('base_h', project['base_h']))
        if 'archived' in data:
            if type(data['archived']) is not bool:
                raise ValueError('archived须为布尔值')
            project['archived'] = data['archived']
        _save(project)
        return copy.deepcopy(project)


def _ensure_job(project_id, job_id, *, boundary=False):
    runtime_reset.assert_current()
    project = _load(project_id)
    job = project.get('job') or {}
    if job.get('id') != job_id or job.get('cancel_requested') or job.get('status') == 'cancelled':
        raise runtime_reset.Cancelled('漫画任务已取消，文件与版本保留')
    if boundary and job.get('pause_requested'):
        raise _Paused('已在页边界暂停')
    return project


def _model_call(project_id, job_id, callback):
    from models import anima_client, gpu_manager, moss_client
    _ensure_job(project_id, job_id, boundary=True)
    _other_tasks_guard()
    if not gpu_manager.GPU_LOCK.acquire(blocking=False):
        raise BusyError('其他模型任务正在使用显卡，漫画任务保留，请稍后恢复')
    try:
        moss_client.local_guard()
        moss_client._resource_guard()
        anima_client.idle_guard()
        _ensure_job(project_id, job_id, boundary=True)
        value = callback()
        _ensure_job(project_id, job_id)
        return value
    finally:
        gpu_manager.release('qwen')
        gpu_manager.GPU_LOCK.release()


def _query(project_id, job_id, system, payload, max_tokens):
    from models import qwen_client
    raw = _model_call(project_id, job_id, lambda: qwen_client.chat(system,
        json.dumps(payload, ensure_ascii=False), temperature=.4, max_tokens=max_tokens,
        reasoning=False, response_format={'type': 'json_object'}, check_context=True))
    store.save_json(DATA_ROOT / project_id / 'audit' / (job_id + '_' + uuid.uuid4().hex[:8] + '.json'),
                    dict(system=system, payload=payload, raw=raw, created=time.time()))
    if not isinstance(raw, str):
        raise ValueError('漫画规划未返回文字，原项目保留')
    text = raw.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
    result = json.loads(text)
    if not isinstance(result, dict):
        raise ValueError('漫画规划须为JSON对象')
    return result


def _progress(project_id, job_id, step, done=0, total=0):
    with _LOCK:
        project = _ensure_job(project_id, job_id)
        project['job'].update(step=step, done=done, total=total, updated=time.time())
        _save(project)


def _plan_book(project_id, job_id):
    project = _ensure_job(project_id, job_id, boundary=True)
    if project.get('outline'):
        return
    _progress(project_id, job_id, '规划全书人物与章节')
    system = BOOK_SYSTEM
    if _v2(project):
        system += '\n用户已有characters中的ID、姓名及非空外貌、初始服装、性格均为已确认事实，必须逐字保留；只补充空字段，不重新设计人物。'
    result = _query(project_id, job_id, system, {key: project[key] for key in
        ('title', 'story', 'style', 'characters_text', 'characters', 'chapter_count', 'pages_per_chapter')}, 6000)
    proposed = result.get('characters')
    if _v2(project) and project['characters'] and isinstance(proposed, list):
        fixed = {row['id']: row for row in project['characters']}
        proposed = [dict(row, **{key: fixed[row['id']][key] for key in ('appearance', 'clothing', 'personality')
                                 if fixed[row['id']].get(key)})
                    if isinstance(row, dict) and row.get('id') in fixed else row for row in proposed]
    cast = _characters(proposed)
    if project['characters'] and [(row['id'], row['name']) for row in cast] != [
            (row['id'], row['name']) for row in project['characters']]:
        raise ValueError('全书规划改变了用户已有的人物ID或姓名')
    chapters = result.get('chapters')
    if not isinstance(chapters, list) or len(chapters) != project['chapter_count']:
        raise ValueError('全书规划章节数与设置不符')
    normalized = []
    for number, row in enumerate(chapters, 1):
        if not isinstance(row, dict) or row.get('number') != number:
            raise ValueError('章节编号须从1连续')
        normalized.append(dict(number=number, title=_text(row.get('title', ''), '章名', 120, True),
            summary=_text(row.get('summary', ''), '章节剧情', 2000, True),
            planned_ending_state=_text(row.get('ending_state', ''), '计划末态', 1500, True),
            opening_state='', ending_state='', status='outline', pages=[]))
    with _LOCK:
        project = _ensure_job(project_id, job_id)
        project['characters'] = cast
        project['outline'] = dict(premise=_text(result.get('premise', ''), '全书事件', 3000, True),
            ending=_text(result.get('ending', ''), '全书结局', 2000, True))
        project['chapters'] = normalized
        _save(project)


def _compile_prompt(project, page):
    if _v2(project):
        return long_comic_continuity.compile_prompt(project, page)
    cast = {row['id']: row for row in project['characters']}
    parts = [project['style'], '竖版叙事漫画整页，画面具有清楚的阅读顺序和格间留白。']
    count = len(page['panels'])
    layout = {1: '整页单格。', 2: '上下两格等高布局。', 3: '上方半页一格，下方半页左右两格。',
              4: '两行两列等宽等高四格布局。'}[count]
    parts.append(layout + '漫画气泡、旁白框与人物画面自然融合，清楚的简体中文文字，气泡尾部指向对应说话者。')
    parts.append('本页事件：' + page['summary'])
    parts.append('本页场景：' + page['scene'])
    for identity in page['character_ids']:
        person = cast[identity]
        clothing = page['wardrobe'].get(identity) or person['clothing'] or '沿用既定穿戴；未知细节保持留白'
        parts.append('%s：%s；本页穿戴：%s；神态：%s' %
                     (person['name'], person['appearance'], clothing, person['personality']))
    parts.append('本页开始的叙事状态：' + page['opening_state'])
    for panel in page['panels']:
        content = '第%d格：%s' % (panel['number'], panel['description'])
        for line in panel['dialogue']:
            content += '；%s的气泡逐字写“%s”' % (cast[line['speaker']]['name'], line['text'])
        if panel['narration']:
            content += '；旁白框逐字写“%s”' % panel['narration']
        parts.append(content)
    return '\n'.join(parts)


def _plan_chapter(project_id, job_id, number):
    _plan_book(project_id, job_id)
    project = _ensure_job(project_id, job_id, boundary=True)
    chapter = _chapter(project, number)
    if _v2(project):
        _require_current_chapter(chapter)
    if chapter['pages']:
        return
    previous = _chapter(project, number - 1) if number > 1 else None
    if previous and not previous.get('pages'):
        raise ValueError('请先规划上一章，再承接其已保存的结束状态')
    if _v2(project):
        return _plan_chapter_v2(project_id, job_id, project, chapter, previous)
    opening = previous['ending_state'] if previous else '第一章开场，按全书设定建立人物与环境。'
    _progress(project_id, job_id, '规划第%d章分页、分格与对白' % number)
    result = _query(project_id, job_id, CHAPTER_SYSTEM, dict(title=project['title'], style=project['style'],
        characters=project['characters'], chapter=chapter, pages_per_chapter=project['pages_per_chapter'],
        previous_chapter_state=opening, state_evidence='saved narrative plan, not visual observation'), 9000)
    pages = result.get('pages')
    if not isinstance(pages, list) or len(pages) != project['pages_per_chapter']:
        raise ValueError('本章页数与设置不符')
    identities = {row['id'] for row in project['characters']}
    normalized = []
    current_wardrobe = copy.deepcopy(previous.get('wardrobe_state') if previous else None) or {
        person['id']: person['clothing'] for person in project['characters']}
    for page_number, row in enumerate(pages, 1):
        if not isinstance(row, dict) or row.get('number') != page_number:
            raise ValueError('页编号须从1连续')
        selected = row.get('character_ids')
        if not isinstance(selected, list) or len(set(selected)) != len(selected) or not set(selected) <= identities:
            raise ValueError('本页人物ID必须唯一且属于全书人物')
        clothes = row.get('wardrobe')
        if not isinstance(clothes, dict) or not set(clothes) <= set(selected):
            raise ValueError('本页衣着只能绑定本页出场人物')
        clean_panels = _panels(row.get('panels'), selected)
        updates = {identity: _text(value, '本页衣着', 1000, True) for identity, value in clothes.items()}
        current_wardrobe.update(updates)
        page = dict(number=page_number, summary=_text(row.get('summary', ''), '本页剧情', 1000, True),
            scene=_text(row.get('scene', ''), '本页场景', 1500, True), character_ids=selected,
            wardrobe={identity: current_wardrobe.get(identity) or 'unknown' for identity in selected},
            wardrobe_updates=updates,
            opening_state=_text(row.get('opening_state', ''), '本页开场', 1500, True),
            ending_state=_text(row.get('ending_state', ''), '本页末态', 1500, True), panels=clean_panels,
            seed=uuid.uuid4().int % 2000000000, status='planned', versions=[], current_version=None,
            prompt_edited=False, prompt_dirty=False, needs_redo=False)
        page['prompt'] = _compile_prompt(project, page)
        normalized.append(page)
    ending = _text(result.get('ending_state', ''), '章节结束状态', 2000, True)
    with _LOCK:
        project = _ensure_job(project_id, job_id)
        chapter = _chapter(project, number)
        chapter.update(opening_state=opening, ending_state=ending, pages=normalized, status='planned',
            wardrobe_state=current_wardrobe, state_evidence='narrative_plan_not_visual_verification')
        _save(project)


def _source_requirements(project, chapter_number):
    story = project['story']
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
    counts = re.findall(r'每页\s*(?:严格|固定|均为|都是|为|有)?\s*(' + numeral + r')\s*格', story)
    requirement = dict(dialogue=[])
    if counts and len({number(value) for value in counts}) == 1:
        requirement['panels_per_page'] = number(counts[0])
    chapters = list(re.finditer(r'(?:^|[\n。])\s*第(' + numeral + r')章(?=[：:\s]|$)', story))
    if chapters:
        selected = next((index for index, match in enumerate(chapters)
                         if number(match[1]) == chapter_number), None)
        if selected is None:
            return requirement
        story = story[chapters[selected].end():chapters[selected + 1].start() if selected + 1 < len(chapters) else len(story)]
    elif project['chapter_count'] != 1:
        return requirement  # 未标章节的逐页台词不能猜测属于哪章。
    markers = list(re.finditer(r'第(' + numeral + r')([页格])', story))
    page = None
    names = {person['name']: person['id'] for person in project['characters']}
    name_pattern = '|'.join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    if not name_pattern:
        return requirement
    for index, marker in enumerate(markers):
        if marker[2] == '页':
            page = number(marker[1])
            continue
        if page is None:
            continue
        text = story[marker.end():markers[index + 1].start() if index + 1 < len(markers) else len(story)]
        dialogue, previous_quote = [], 0
        for quote in re.finditer(r'(?:说|问|回答|喊|答|叫)(?:道|着|了)?\s*[:：]?\s*[“"]([^”"\n]+)[”"]', text):
            prefix = re.split(r'[。！？；;\n]', text[previous_quote:quote.start()])[-1]
            prefix = re.sub(r'对\s*(?:' + name_pattern + r')', '', prefix)
            speakers = list(re.finditer(name_pattern, prefix))
            if speakers:
                dialogue.append(dict(speaker=names[speakers[-1][0]], text=quote[1]))
            previous_quote = quote.end()
        if dialogue:
            requirement['dialogue'].append(dict(page=page, panel=number(marker[1]), lines=dialogue))
    return requirement


def _validate_chapter_v2(result, project, opening, requirements, *, evidence_context=None):
    supplied = result.get('pages')
    if not isinstance(supplied, list) or len(supplied) != project['pages_per_chapter']:
        raise ValueError('本章页数与设置不符')
    pages = []
    for number, row in enumerate(supplied, 1):
        if not isinstance(row, dict) or row.get('number') != number:
            raise ValueError('页编号须从1连续')
        pages.append(dict(number=number, summary=_text(row.get('summary', ''), '本页剧情', 1000, True),
            scene=_text(row.get('scene', ''), '本页场景', 1500, True),
            panels=long_comic_continuity.panels(row.get('panels'), project['characters'], _text,
                evidence_context=long_comic_continuity.source_evidence_context(evidence_context, number)),
            seed=uuid.uuid4().int % 2000000000, status='planned', versions=[], current_version=None,
            prompt_edited=False, prompt_dirty=False, needs_redo=False))
    pages, ending = long_comic_continuity.recalculate(pages, project['characters'], opening, _text,
        evidence_context=evidence_context)
    count = requirements.get('panels_per_page')
    if count is not None and any(len(page['panels']) != count for page in pages):
        raise ValueError('原故事要求每页%d格，不能改变格数' % count)
    for required in requirements['dialogue']:
        try:
            if required['page'] < 1 or required['panel'] < 1:
                raise IndexError
            actual = pages[required['page'] - 1]['panels'][required['panel'] - 1]['dialogue']
        except IndexError as error:
            raise ValueError('原故事指定的对白所在页格缺失') from error
        if actual != required['lines']:
            raise ValueError('第%d页第%d格须逐字保留原故事对白、说话者和顺序' % (required['page'], required['panel']))
    for page in pages:
        page['prompt'] = _compile_prompt(project, page)
    return pages, ending


def normalize_chapter_plan(project, chapter_number, result):
    """Validate a saved Qwen JSON object in memory; no model, save or task starts."""
    if not _v2(project) or not isinstance(result, dict):
        raise ValueError('需要新版漫画项目和本章JSON对象')
    chapter = _chapter(project, chapter_number)
    if not chapter:
        raise ValueError('请先规划全书章节')
    previous = _chapter(project, chapter['number'] - 1) if chapter['number'] > 1 else None
    _require_current_chapter(previous)
    if previous and not previous.get('pages'):
        raise ValueError('请先规划上一章，再承接其已保存的结束状态')
    opening = (long_comic_continuity.validate_state(previous['ending_state'], project['characters']) if previous
               else long_comic_continuity.initial_state(project['characters']))
    pages, ending = _validate_chapter_v2(result, project, opening, _source_requirements(project, chapter['number']),
        evidence_context=long_comic_continuity.source_evidence_context(project['story'], chapter_number=chapter['number']))
    return dict(opening_state=opening, ending_state=ending, pages=pages, status='planned',
        wardrobe_state={identity: value['clothing'] for identity, value in ending.items()},
        state_evidence='narrative_plan_not_visual_verification')


def _plan_chapter_v2(project_id, job_id, project, chapter, previous, *, replan=False):
    _require_current_chapter(previous)
    opening = (long_comic_continuity.validate_state(previous['ending_state'], project['characters']) if previous
               else long_comic_continuity.initial_state(project['characters']))
    requirements = _source_requirements(project, chapter['number'])
    _progress(project_id, job_id, '规划第%d章逐格人物与状态' % chapter['number'])
    payload = dict(title=project['title'], story=project['story'], style=project['style'],
        characters=project['characters'], chapter=chapter, pages_per_chapter=project['pages_per_chapter'],
        source_requirements=requirements, previous_chapter_state=opening,
        state_evidence='saved narrative plan, not visual observation')
    result = _query(project_id, job_id, CHAPTER_SYSTEM_V2, payload, 9000)
    evidence_context = long_comic_continuity.source_evidence_context(project['story'], chapter_number=chapter['number'])
    try:
        pages, ending = _validate_chapter_v2(result, project, opening, requirements, evidence_context=evidence_context)
    except ValueError as error:
        _progress(project_id, job_id, '按校验错误修正本章一次')
        repair = dict(payload, previous_result=result, validation_error=str(error),
            repair_instruction='只修正本次校验错误并复核原故事全部要求，返回完整本章JSON；物品交接同格更新双方，逐字保留已给对白。此次是唯一修正机会。')
        result = _query(project_id, job_id, CHAPTER_SYSTEM_V2, repair, 9000)
        pages, ending = _validate_chapter_v2(result, project, opening, requirements, evidence_context=evidence_context)
    with _LOCK:
        current = _ensure_job(project_id, job_id)
        selected = _chapter(current, chapter['number'])
        if replan:
            snapshot = copy.deepcopy(selected)
            snapshot.pop('plan_versions', None)
            versions = selected.setdefault('plan_versions', [])
            versions.append(dict(version=len(versions) + 1, saved_at=time.time(), chapter=snapshot))
        selected.update(opening_state=opening, ending_state=ending, pages=pages, status='planned',
            wardrobe_state={identity: value['clothing'] for identity, value in ending.items()},
            state_evidence='narrative_plan_not_visual_verification')
        selected.pop('continuity_review_required', None)
        if replan:
            _mark_later_chapters(current, chapter['number'])
        _save(current)


def _replan_chapter(project_id, job_id, number):
    project = _ensure_job(project_id, job_id, boundary=True)
    if not _v2(project):
        raise ValueError('仅新版逐格漫画支持重新规划衔接')
    chapter = _chapter(project, number)
    if not chapter or not chapter.get('pages') or not chapter.get('continuity_review_required'):
        raise ValueError('本章没有待重新规划的衔接')
    previous = _chapter(project, number - 1) if number > 1 else None
    _require_current_chapter(previous)
    return _plan_chapter_v2(project_id, job_id, project, chapter, previous, replan=True)


def _current(page):
    version = next((row for row in page.get('versions') or [] if row.get('version') == page.get('current_version')), None)
    if not version or version.get('status') != 'completed':
        return None
    path = Path(version.get('path') or '')
    if not path.is_file() or not path.resolve().is_relative_to(OUTPUT_ROOT.resolve()):
        return None
    if _sha(path) != version.get('sha256'):
        return None
    from PIL import Image
    try:
        with Image.open(path) as image:
            image.verify()
    except (OSError, ValueError):
        return None
    return version


def _render_page(project_id, job_id, chapter_number, page_number, *, redo=False):
    from models import moss_client
    with _LOCK:
        project = _ensure_job(project_id, job_id, boundary=True)
        chapter = _chapter(project, chapter_number)
        page = _page(chapter, page_number)
        if not redo and _current(page):
            return
        if page.get('prompt_dirty') or not page.get('prompt'):
            page.update(prompt=_compile_prompt(project, page), prompt_dirty=False)
        retained = next((row for row in reversed(page['versions'])
            if not redo and row.get('status') != 'completed'
            and row.get('raw_path') and row.get('raw_sha256')
            and row.get('prompt') == page['prompt'] and row.get('model') == MODEL
            and row.get('base_w') == project['base_w'] and row.get('base_h') == project['base_h']), None)
        version_number = max([row['version'] for row in page['versions']] or [0]) + 1
        seed = retained['seed'] if retained else uuid.uuid4().int % 2000000000 if redo else page['seed']
        version = dict(version=version_number, status='generating', prompt=page['prompt'], seed=seed,
            model=MODEL, base_w=project['base_w'], base_h=project['base_h'], created=time.time(),
            content_signature=long_comic_edit.content_signature(project, page))
        if retained:
            version.update(raw_path=retained['raw_path'], raw_sha256=retained['raw_sha256'],
                           prompt_id=retained.get('prompt_id'), recovered_from=retained['version'])
        page['versions'].append(version)
        page['status'] = 'generating'
        _save(project)
    if retained:
        from .runtime_core import _batch_file
        _batch_file(version['raw_path'], version['raw_sha256'])
        result = dict(output_path=version['raw_path'], prompt_id=version.get('prompt_id'))
    else:
        result = _model_call(project_id, job_id, lambda: moss_client.generate(version['prompt'],
            base_w=version['base_w'], base_h=version['base_h'], seed=seed))
    from PIL import Image
    source = Path(result.get('output_path') or '')
    with Image.open(source) as image:
        image.verify()
    # Persist the already generated result before file adoption. A sharing
    # violation during copying must not spend another page generation.
    with _LOCK:
        project = _ensure_job(project_id, job_id)
        page = _page(_chapter(project, chapter_number), page_number)
        version = next(row for row in page['versions'] if row['version'] == version_number)
        version.update(raw_path=str(source), raw_sha256=_sha(source),
                       prompt_id=result.get('prompt_id'), generated_at=time.time())
        _save(project)
    directory = OUTPUT_ROOT / project_id / ('chapter_%03d' % chapter_number) / ('page_%03d' % page_number)
    directory.mkdir(parents=True, exist_ok=True)
    page_path = directory / ('v%03d.png' % version_number)
    with source.open('rb') as reader, page_path.open('xb') as target:
        shutil.copyfileobj(reader, target)
    with _LOCK:
        project = _ensure_job(project_id, job_id)
        page = _page(_chapter(project, chapter_number), page_number)
        version = next(row for row in page['versions'] if row['version'] == version_number)
        version.update(status='completed', path=str(page_path), url=_url(page_path),
            sha256=_sha(page_path), prompt_id=result.get('prompt_id'), finished=time.time(),
            text_review='Moss原生气泡文字，待人工预览，不保证逐字准确或气泡归属')
        page.update(current_version=version_number, status='completed', prompt_edited=False, needs_redo=False)
        _save(project)


def _run_job(project_id, job_id, epoch):
    runtime_reset.bind_thread(epoch)
    try:
        with _LOCK:
            project = _ensure_job(project_id, job_id, boundary=True)
            project['job']['status'] = 'running'
            _save(project)
        job = project['job']
        action, number = job['action'], job['chapter']
        if action == 'plan':
            _plan_book(project_id, job_id)
        elif action == 'replan_chapter':
            _replan_chapter(project_id, job_id, number)
        elif action == 'edit_page':
            long_comic_edit.run_edit(project_id, job_id)
        else:
            _plan_chapter(project_id, job_id, number)
            if action in ('generate', 'redo_page'):
                project = _ensure_job(project_id, job_id, boundary=True)
                pages = _chapter(project, number)['pages']
                selected = [job['page']] if action == 'redo_page' else [row['number'] for row in pages]
                completed = 0
                for offset, page_number in enumerate(selected):
                    _ensure_job(project_id, job_id, boundary=True)
                    _progress(project_id, job_id, '第%d章第%d页：Moss整页漫画生成' % (number, page_number), completed, len(selected))
                    try:
                        _render_page(project_id, job_id, number, page_number, redo=action == 'redo_page')
                    except Exception as error:
                        from .runtime_core import item_failure_requires_stop
                        if item_failure_requires_stop(error):
                            raise
                        with _LOCK:
                            project = _ensure_job(project_id, job_id, boundary=True)
                            page = _page(_chapter(project, number), page_number)
                            for version in page['versions']:
                                if version.get('status') == 'generating':
                                    version.update(status='failed', error=str(error)[:600])
                            page['status'] = 'completed' if _current(page) else 'failed'
                            page['last_error'] = str(error)[:600]
                            project['job'].setdefault('failed_pages', {})[str(page_number)] = str(error)[:600]
                            _save(project)
                        _progress(project_id, job_id, '第%d章第%d页待补，继续后续页' % (number, page_number), completed, len(selected))
                        continue
                    completed += 1
                    _progress(project_id, job_id, '第%d章第%d页已保存' % (number, page_number), completed, len(selected))
                with _LOCK:
                    project = _ensure_job(project_id, job_id)
                    chapter = _chapter(project, number)
                    fresh = not _v2(project) or not any(row.get('needs_redo') or row.get('prompt_dirty') for row in chapter['pages'])
                    chapter['status'] = 'completed' if fresh and all(_current(row) for row in chapter['pages']) else 'partial'
                    _save(project)
        with _LOCK:
            project = _ensure_job(project_id, job_id, boundary=True)
            chapter = _chapter(project, number)
            pending = (_v2(project) and action in ('generate', 'redo_page') and chapter
                       and any(row.get('needs_redo') or row.get('prompt_dirty') for row in chapter['pages']))
            failures = project['job'].get('failed_pages') or {}
            if action in ('generate', 'redo_page') and chapter:
                requested = {job['page']} if action == 'redo_page' else {row['number'] for row in chapter['pages']}
                for row in chapter['pages']:
                    if row['number'] in requested and not _current(row):
                        failures.setdefault(str(row['number']), '该页尚无可用成品，已完成页保留')
                project['job']['failed_pages'] = failures
            project['job'].update(status='partial' if failures else 'completed',
                step=('本轮已处理，%d页待补，已完成页保留' % len(failures)) if failures else
                     '有修改页待单页重做' if pending else
                     '修正候选已保存，请预览后采用' if action == 'edit_page' else '完成',
                error='；'.join('第%s页：%s' % item for item in failures.items())[:600], finished=time.time())
            _save(project)
    except Exception as error:
        with _LOCK:
            project = _load(project_id)
            if (project.get('job') or {}).get('id') == job_id:
                status = ('paused' if isinstance(error, _Paused) else 'cancelled'
                          if isinstance(error, runtime_reset.Cancelled) else 'failed')
                if project['job'].get('cancel_requested'):
                    status = 'cancelled'
                project['job'].update(status=status, error=str(error)[:600], finished=time.time(), step=str(error)[:180])
                if getattr(error, 'prompt_id', None):
                    project['job']['model_prompt_id'] = error.prompt_id
                for chapter in project['chapters']:
                    for page in chapter['pages']:
                        for version in page['versions']:
                            if version.get('status') == 'generating':
                                version.update(status='interrupted' if status in ('paused', 'cancelled') else 'failed',
                                    error=str(error)[:600])
                                page['status'] = 'completed' if _current(page) else 'planned'
                _save(project)
    finally:
        runtime_reset.bind_thread(None)
        with _LOCK:
            _THREADS.pop(job_id, None)
        _RUN_LOCK.release()


def start_job(project_id, action, chapter=1, page=None, edit=None):
    if action not in ('plan', 'plan_chapter', 'generate', 'redo_page', 'replan_chapter', 'edit_page'):
        raise ValueError('不支持的漫画任务')
    with runtime_reset.LOCK, _LOCK:
        runtime_reset.assert_current()
        _other_tasks_guard()
        project = _recover(_load(project_id))
        if project.get('archived'):
            raise ValueError('项目已归档，请先取消归档')
        if (project.get('job') or {}).get('status') in ACTIVE:
            raise BusyError('本项目已有任务，不能重复启动')
        number = _integer(chapter, '章节', 1, project['chapter_count'])
        selected = _chapter(project, number)
        if action == 'replan_chapter':
            if not _v2(project):
                raise ValueError('仅新版逐格漫画支持重新规划衔接')
            if not selected or not selected.get('pages') or not selected.get('continuity_review_required'):
                raise ValueError('本章没有待重新规划的衔接')
            _require_current_chapter(_chapter(project, number - 1) if number > 1 else None)
        elif _v2(project) and action != 'plan':
            _require_current_chapter(selected)
        if action == 'redo_page':
            _page(_chapter(project, number), page)
            page = int(page)
        frozen_edit = None
        if action == 'edit_page':
            selected_page = _page(selected, page)
            page = selected_page['number']
            frozen_edit = long_comic_edit.prepare_edit(project, selected, selected_page, edit)
        if not _RUN_LOCK.acquire(blocking=False):
            raise BusyError('另一漫画任务正在进行，请等完成后再开始')
        job_id = uuid.uuid4().hex
        epoch = runtime_reset.generation()
        project['job'] = dict(id=job_id, action=action, chapter=number, page=page, status='queued',
            step='等待执行', done=0, total=0, error='', pause_requested=False, cancel_requested=False,
            runtime_generation=epoch, created=time.time(), updated=time.time())
        if frozen_edit is not None:
            project['job']['edit'] = frozen_edit
        thread = threading.Thread(target=_run_job, args=(project_id, job_id, epoch), daemon=True,
                                  name='long-comic-' + job_id[:8])
        try:
            _save(project)
            _THREADS[job_id] = thread
            thread.start()
        except Exception:
            _THREADS.pop(job_id, None)
            _RUN_LOCK.release()
            raise
        return copy.deepcopy(project)


def pause_job(project_id):
    with _LOCK:
        project = _recover(_load(project_id))
        job = project.get('job') or {}
        if job.get('status') in ACTIVE:
            job.update(pause_requested=True, status='pausing', step='当前页完成后暂停')
            _save(project)
        return copy.deepcopy(project)


def cancel_job(project_id):
    with _LOCK:
        project = _recover(_load(project_id))
        job = project.get('job') or {}
        if job and job.get('status') not in ('completed', 'cancelled'):
            job.update(cancel_requested=True, status='cancelled', step='已取消，现有页面和版本保留')
            _save(project)
        return copy.deepcopy(project)


def cancel_all_jobs():
    with _LOCK:
        count = 0
        if DATA_ROOT.exists():
            for path in DATA_ROOT.glob('LC_*/project.json'):
                project = _load(path.parent.name)
                job = project.get('job') or {}
                if job.get('status') in ACTIVE:
                    job.update(cancel_requested=True, status='cancelled', step='已取消，现有页面和版本保留')
                    _save(project)
                    count += 1
        return {'cancelled': count}


def export_project(project_id, chapter=None, kind='zip'):
    if kind not in ('zip', 'pdf'):
        raise ValueError('导出格式须为zip或pdf')
    with _LOCK:
        project = _recover(_load(project_id))
        if (project.get('job') or {}).get('status') in ACTIVE:
            raise BusyError('请先暂停生产，再导出稳定版本')
        selected = [_chapter(project, chapter)] if chapter is not None else project['chapters']
        if not selected or any(row is None or not row['pages'] for row in selected):
            raise ValueError('请先规划并完成要导出的章节')
        if _v2(project):
            for current in selected:
                _require_current_chapter(current)
        pages = []
        for current in selected:
            for page in current['pages']:
                if _v2(project) and (page.get('needs_redo') or page.get('prompt_dirty')):
                    raise ValueError('第%d章第%d页有修改，请先单页重做后再导出' % (current['number'], page['number']))
                version = _current(page)
                if version is None:
                    raise ValueError('第%d章第%d页尚未完成或文件校验失败，不导出缺页文件' %
                                     (current['number'], page['number']))
                pages.append((current['number'], page['number'], copy.deepcopy(version)))
        directory = OUTPUT_ROOT / project_id / 'exports'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ('comic_' + time.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8] + '.' + kind)
        if kind == 'zip':
            with zipfile.ZipFile(path, 'x', zipfile.ZIP_DEFLATED) as archive:
                for number, page_number, version in pages:
                    archive.write(version['path'], 'chapter_%03d/page_%03d.png' % (number, page_number))
            with zipfile.ZipFile(path) as archive:
                if archive.testzip():
                    raise RuntimeError('漫画ZIP校验失败，文件已保留')
        else:
            from PIL import Image
            images = []
            try:
                for _, _, version in pages:
                    with Image.open(version['path']) as image:
                        images.append(image.convert('RGB'))
                with path.open('xb') as stream:
                    images[0].save(stream, format='PDF', save_all=True, append_images=images[1:],
                                   title=project['title'], resolution=144)
            finally:
                for image in images:
                    image.close()
        return dict(path=str(path), url=_url(path), kind=kind, pages=len(pages),
                    bytes=path.stat().st_size, sha256=_sha(path))
