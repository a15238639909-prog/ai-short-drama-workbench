"""Compile visible panel content and exact text using MOSS's layout schema."""
import json
import re


LAYOUTS = {1: '整页单格', 2: '上下两格等高', 3: '上方半页一格，下方左右两格', 4: '两行两列四格'}


def panel_boxes(count, width, height):
    margin, gap = 10, 14
    left, right, top, bottom = margin, width-margin, margin, height-margin
    mx, my = width/2, height/2
    if count == 1:
        return [(left, top, right, bottom)]
    if count == 2:
        return [(left, top, right, my-gap/2), (left, my+gap/2, right, bottom)]
    if count == 3:
        return [(left, top, right, my-gap/2), (left, my+gap/2, mx-gap/2, bottom),
                (mx+gap/2, my+gap/2, right, bottom)]
    if count == 4:
        return [(left, top, mx-gap/2, my-gap/2), (mx+gap/2, top, right, my-gap/2),
                (left, my+gap/2, mx-gap/2, bottom), (mx+gap/2, my+gap/2, right, bottom)]
    raise ValueError('漫画版式支持1至4格')


def _appearance(text):
    text = re.sub(r'年龄：(\d+)(?!\d|岁)', r'\1岁', str(text))
    text = text.replace('性别：男', '男性').replace('性别：女', '女性')
    for label in ('身高：', '人物类型：', '脸型：', '身材：', '发型：', '外貌与识别特征：'):
        text = text.replace(label, '')
    return text.replace('；', '，')


def compile_prompt(project, page):
    width, height = project['base_w'], project['base_h']
    count = len(page['panels'])
    cast = {person['id']: person for person in project['characters']}
    items = []

    def bbox(box):
        x0, y0, x1, y1 = box
        return [round(y0-height/2), round(x0-width/2), round(y1-height/2), round(x1-width/2)]

    text_count = sum(len(panel['dialogue']) + bool(panel['narration']) for panel in page['panels'])
    for panel, box in zip(page['panels'], panel_boxes(count, width, height)):
        x0, y0, x1, y1 = box
        description = panel['description']
        # Keep the actor performing the main visible action close to their design.
        ordered = sorted(panel['character_ids'], key=lambda identity:
            description.find(cast[identity]['name']) if cast[identity]['name'] in description else len(description))
        people = []
        for identity in ordered:
            person = cast[identity]
            state = panel['ending_state'][identity]
            visible = '，'.join(value for value in state.values() if value not in ('未知', 'unknown', ''))
            people.append('，'.join(value for value in (person['name'], _appearance(person['appearance']), visible) if value))
        caption = '第%d格漫画画面，%s。%s。%s。' % (panel['number'], page['scene'].rstrip('。'), description.rstrip('。'), '；'.join(people))
        if not panel['dialogue'] and not panel['narration']:
            caption += '这一格以动作画面叙事。'
        items.append(dict(label='object', caption=caption, bbox=bbox(box)))
        lines = [dict(speaker=cast[line['speaker']]['name'], text=line['text']) for line in panel['dialogue']]
        if panel['narration']:
            lines.append(dict(speaker='', text=panel['narration']))
        for number, line in enumerate(lines):
            span = (x1-x0-24)/max(1, len(lines))
            text_box = (x0+12+span*number, y0+10, x0+12+span*(number+1), y0+max(70, (y1-y0)*.17))
            caption = ('第%d格中%s说话的白色气泡，尾部指向%s，黑色中文排版。' %
                       (panel['number'], line['speaker'], line['speaker']) if line['speaker'] else
                       '第%d格的简短旁白框，黑色中文排版。' % panel['number'])
            items.append(dict(label='text', caption=caption, text=line['text'], bbox=bbox(text_box)))
    layout = dict(summary=dict(meta=['comic', 'colored illustration'], canvas='single',
        caption=project['style'] + ' 一张竖版叙事漫画页，%s，共%d格，格间清楚留白。全页文字共%d处，内容逐字采用text字段。' %
        (LAYOUTS[count], count, text_count)), items=items,
        bbox_coord_space='abs_centralized', bbox_format='yxyx')
    return json.dumps(layout, ensure_ascii=False, indent=2)
