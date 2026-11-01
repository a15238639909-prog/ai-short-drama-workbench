'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'web/pages/long_comic.js'), 'utf8');
const copy = value => JSON.parse(JSON.stringify(value));
const flush = async () => { for (let count = 0; count < 30; count++) await Promise.resolve(); };
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; };
const decode = text => String(text || '').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
const escape = text => String(text == null ? '' : text).replace(/[&<>"']/g, value => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[value]));

class Element {
  constructor(tag = 'div') { this.tagName = tag; this.children = []; this.attributes = {}; this.style = {}; this.value = ''; this.textContent = ''; this.disabled = false; this.checked = false; this.hidden = false; this.isConnected = true; this.scrollTop = 0; this.writes = 0; }
  appendChild(child) { this.children.push(child); child.parentNode = this; return child; }
  setAttribute(name, value) { this.attributes[name] = decode(value); if (name === 'value') this.value = decode(value); if (name === 'hidden') this.hidden = true; if (name === 'disabled') this.disabled = true; if (name === 'checked') this.checked = true; }
  getAttribute(name) { return this.attributes[name] ?? null; }
  getBoundingClientRect() { return this.bounds || {left: 10, top: 20, width: 500, height: 800}; }
  getContext() { return this.context || (this.context = {clearRect() {}, fillRect() {}, strokeRect() {}}); }
  setPointerCapture() {}
  get innerHTML() { return this.markup || ''; }
  set innerHTML(markup) {
    this.markup = markup; this.children = []; this.writes++;
    const stack = [this], voidTags = ['input', 'img', 'br', 'hr', 'meta', 'link'];
    for (const match of String(markup).replace(/<style>[\s\S]*?<\/style>/g, '').matchAll(/<\/?([a-z][\w-]*)\b([^>]*)>|([^<]+)/gi)) {
      if (match[3]) { stack[stack.length - 1].textContent += decode(match[3]); continue; }
      const tag = match[1].toLowerCase();
      if (match[0][1] === '/') { while (stack.length > 1) { if (stack.pop().tagName === tag) break; } continue; }
      const element = new Element(tag);
      for (const attribute of match[2].matchAll(/([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s]+)))?/g)) element.setAttribute(attribute[1], attribute[2] ?? attribute[3] ?? attribute[4] ?? '');
      stack[stack.length - 1].appendChild(element);
      if (!voidTags.includes(tag)) stack.push(element);
    }
    function values(element) {
      element.children.forEach(values);
      if (element.tagName === 'textarea') element.value = element.textContent;
      if (element.tagName === 'select') element.value = (element.children.find(child => 'selected' in child.attributes) || element.children[0] || {}).value || '';
    }
    values(this);
  }
  matches(selector) {
    if (selector.startsWith('#')) return this.attributes.id === selector.slice(1);
    const attribute = selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);
    if (attribute) return attribute[1] in this.attributes && (attribute[2] === undefined || this.attributes[attribute[1]] === attribute[2]);
    return this.tagName === selector;
  }
  querySelectorAll(selector) { return this.children.flatMap(child => [...(selector.split(',').some(option => child.matches(option)) ? [child] : []), ...child.querySelectorAll(selector)]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

function page(number = 1) {
  return {number, summary: '成年兄妹一起走出森林', character_ids: ['char_a', 'char_b'], prompt: '纯中文漫画页提示词', status: 'completed', current_version: 1, needs_redo: false,
    panels: [{number: 1, description: '两人在林间并肩前行', dialogue: [{speaker: 'char_a', text: '前面有光'}], narration: ''}],
    versions: [{version: 1, status: 'completed', width: 1000, height: 1600, url: '/files/outputs/long_comic/test/page_' + number + '.png'}]};
}

function project(id = 'comic_a', planned = true) {
  return {id, title: '森林归途', style: '彩色手绘', characters_text: '甲：成年哥哥\n乙：成年妹妹', story: '兄妹走出森林回家', chapter_count: 3, pages_per_chapter: 4, model: 'moss',
    characters: [{id: 'char_a', name: '甲'}, {id: 'char_b', name: '乙'}], outline: planned ? {premise: '归家的冒险', ending: '平安到家'} : null,
    chapters: planned ? [{number: 1, title: '林间', summary: '同行出发', pages: [page(1), page(2)]}] : [], job: {status: 'completed', done: 2, total: 2, step: '已完成本章'}};
}

function harness(initial = {}, stored = {}, options = {}) {
  const projects = copy(initial), storage = new Map(Object.entries(stored)), calls = [], timers = new Map(), listeners = new Map();
  const host = new Element('main');
  let timerId = 0, overrideGet = null, overridePost = null;
  const readers = [];
  class FileReader {
    constructor() { readers.push(this); }
    readAsDataURL(file) { this.result = file.data || 'data:image/png;base64,dGVzdA=='; if (!options.deferRead) Promise.resolve().then(() => this.onload()); }
  }
  const api = {
    get: async url => {
      calls.push({method: 'GET', url});
      if (overrideGet) { const result = overrideGet(url); if (result !== undefined) return result; }
      if (url.endsWith('/projects')) return {projects: Object.values(projects).map(value => ({id: value.id, title: value.title}))};
      if (url === '/api/long-comic/sources') return {sources: copy(options.sources || [])};
      const sourceMatch = url.match(/\/sources\/([^/]+)\/characters$/);
      if (sourceMatch) return {characters: copy((options.characters || {})[decodeURIComponent(sourceMatch[1])] || [])};
      return {project: copy(projects[decodeURIComponent(url.split('/').at(-1))])};
    },
    post: async (url, body) => {
      calls.push({method: 'POST', url, body: copy(body)});
      if (overridePost) { const result = overridePost(url, body); if (result !== undefined) return result; }
      if (url === '/api/long-comic/project') {
        const value = Object.assign(project('comic_new', false), copy(body), {job: {}, continuity_version: 2});
        if (body.source_story_id) {
          const people = (options.characters || {})[body.source_story_id] || [];
          value.characters = people.filter(person => body.source_character_ids.includes(person.character_id)).map((person, index) => ({id: 'import_' + index, name: person.name, appearance: person.appearance, clothing: person.clothing}));
          value.character_source = {story_id: body.source_story_id, characters: value.characters.map((person, index) => ({source_character_id: body.source_character_ids[index], comic_character_id: person.id, snapshot: copy(person), hash: 'saved-hash', version: 1}))};
        }
        projects[value.id] = value;
        return {project: copy(value)};
      }
      const id = url.split('/')[4], value = projects[id];
      if (url.endsWith('/save')) {
        if ('page' in body) {
          const item = value.chapters.find(chapter => chapter.number === body.chapter).pages.find(item => item.number === body.page);
          for (const key of ['summary', 'panels', 'prompt']) if (key in body) item[key] = copy(body[key]);
          if ('panels' in body) item.prompt = '';
          item.needs_redo = true;
        } else Object.assign(value, copy(body));
      }
      if (url.endsWith('/run')) {
        if (body.action === 'plan') { value.outline = {premise: '归途', ending: '到家'}; value.chapters = [{number: 1, title: '林间', pages: []}]; }
        if (body.action === 'plan_chapter') value.chapters[0].pages = [page(1), page(2)];
        value.job = {status: 'queued', chapter: body.chapter, action: body.action, done: 0, total: 4, step: '等待执行'};
      }
      if (url.endsWith('/pause')) value.job.status = 'pausing';
      if (url.endsWith('/reference')) {
        value.character_references = value.character_references || {};
        value.character_references[body.character_id] = {id:'ref_' + body.character_id, url:'/files/outputs/long_comic/' + id + '/reference.png', width:500, height:500};
      }
      if (url.endsWith('/edit')) value.job = {id:'edit_' + calls.length,status:'queued',action:'edit_page',chapter:body.chapter,page:body.page,step:'等待修图'};
      if (url.endsWith('/adopt')) value.chapters.find(chapter => chapter.number === body.chapter).pages.find(item => item.number === body.page).current_version = body.version;
      if (url.endsWith('/export')) return {url: '/files/outputs/long_comic/test/book.' + body.kind, pages: 2};
      return {project: copy(value)};
    }
  };
  const location = {hash: '#long-comic'}, window = {api, UI: {esc: escape},
    AppState: options.storyId ? {storyId: options.storyId} : {},
    addEventListener: (name, callback) => { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(callback); },
    removeEventListener: (name, callback) => { if (listeners.has(name)) listeners.get(name).delete(callback); }};
  const context = vm.createContext({window, location, localStorage: {getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value)},
    setTimeout: callback => { timers.set(++timerId, callback); return timerId; }, clearTimeout: id => timers.delete(id), FileReader, console});
  vm.runInContext(source, context);
  const get = name => host.querySelector('#lc-' + name);
  const render = () => window.Pages['long-comic'].render(host);
  const input = (name, value) => { get(name).value = value; get(name).oninput(); };
  const click = async name => { assert(get(name) && get(name).onclick, name); await get(name).onclick(); await flush(); };
  const tick = async () => { const entry = timers.entries().next().value; assert(entry, 'Expected scheduled read-only poll'); timers.delete(entry[0]); await entry[1](); await flush(); };
  const emit = (name, detail) => { [...(listeners.get(name) || [])].forEach(callback => callback({detail})); };
  return {host, context, window, location, storage, calls, timers, projects, readers, get, input, click, render, tick, emit,
    getWith: callback => { overrideGet = callback; }, postWith: callback => { overridePost = callback; }, posts: () => calls.filter(call => call.method === 'POST')};
}

async function testCreateAndRun() {
  const target = harness(); await target.render();
  assert.equal(target.get('chapters').value, '3'); assert.equal(target.get('pages').value, '4');
  for (const [name, value] of Object.entries({title: '新冒险', style: '手绘', characters: '成年朋友甲乙', story: '朋友回家'})) target.input(name, value);
  await target.click('generate');
  assert.equal(target.posts().length, 0); assert.match(target.get('error').textContent, /先规划本章/);
  await target.click('plan');
  assert.equal(target.posts()[1].body.action, 'plan');
  target.projects.comic_new.job.status = 'completed'; await target.tick();
  assert(target.get('generate').disabled); await target.click('plan-chapter');
  target.projects.comic_new.job.status = 'completed'; await target.tick();
  assert.equal(target.get('generate').disabled, false); await target.click('generate');
  assert.equal(target.posts()[0].body.model, 'moss'); assert.equal(target.posts().at(-1).body.action, 'generate');
  assert.equal(target.posts()[1].body.chapter, 1);
  assert.match(target.get('status').textContent, /排队中.*等待执行/); assert(target.get('generate').disabled);
  const runningCount = target.posts().length; await target.click('generate'); assert.equal(target.posts().length, runningCount);
  await target.click('pause'); assert.match(target.get('status').textContent, /当前页完成后暂停/);
  target.projects.comic_new.job.status = 'paused'; await target.tick();
  assert.equal(target.get('resume').hidden, false); await target.click('resume');
  assert.equal(target.posts().at(-1).body.action, 'generate');
  assert.equal(target.storage.get('longComicSelected'), 'comic_new');
}

async function testPartialBatchKeepsSavedPagesAndOffersExplicitResume() {
  const value = project();
  value.job = {status:'partial',chapter:1,action:'generate',done:1,total:2,step:'第2页待补，已完成页保留'};
  const target = harness({comic_a:value},{longComicSelected:'comic_a'}); await target.render();
  assert.match(target.get('status').textContent,/部分待补/);
  assert.equal(target.get('resume').hidden,false);
  assert.equal(target.get('bar').style.width,'50%');
  assert.equal(target.posts().length,0);
  await target.click('resume');
  assert.equal(target.posts().at(-1).body.action,'generate');
  assert.equal(target.projects.comic_a.chapters[0].pages[0].current_version,1);
}

async function testDirtyEditingAndExport() {
  const target = harness({comic_a: project()}, {longComicSelected: 'comic_a'}); await target.render();
  assert(target.get('story').disabled); assert.equal(target.get('title').disabled, false);
  assert.match(target.get('gallery').innerHTML, /page_1\.png/);
  const writes = target.host.writes, editorWrites = target.get('panels').writes;
  target.host.scrollTop = 333; target.input('title', '尚未保存的标题');
  const description = target.get('panels').querySelector('[data-field="description"]');
  description.value = '两人在桥边停步'; description.oninput();
  const dialogue = target.get('panels').querySelector('[data-field="dialogue"]');
  dialogue.value = '乙：一起回家'; dialogue.oninput();
  target.projects.comic_a.title = '后台旧标题'; target.projects.comic_a.chapters[0].pages[0].prompt = '后台新词';
  await target.tick();
  assert.equal(target.get('title').value, '尚未保存的标题');
  assert.equal(target.get('panels').querySelector('[data-field="description"]').value, '两人在桥边停步');
  assert.equal(target.get('prompt').value, ''); assert.equal(target.get('panels').writes, editorWrites);
  assert.equal(target.host.writes, writes); assert.equal(target.host.scrollTop, 333);
  target.get('page-select').value = '2'; target.get('page-select').onchange();
  target.get('page-select').value = '1'; target.get('page-select').onchange();
  assert.equal(target.get('panels').querySelector('[data-field="dialogue"]').value, '乙：一起回家');
  await target.click('save-page');
  const saved = target.posts().at(-1).body;
  assert.equal(saved.chapter, 1); assert.equal(saved.page, 1);
  assert.deepEqual(saved.panels[0].dialogue, [{speaker: 'char_b', text: '一起回家'}]);
  assert(!('prompt' in saved)); assert.match(target.get('gallery').innerHTML, /当前仍显示旧图/);
  target.input('prompt', '我编辑的整页中文词'); await target.click('redo');
  assert.equal(target.posts().at(-2).body.prompt, '我编辑的整页中文词');
  assert.equal(target.posts().at(-1).body.action, 'redo_page'); assert.equal(target.posts().at(-1).body.page, 1);
  target.projects.comic_a.job = {status: 'completed'}; await target.tick();
  await target.click('export-chapter-zip'); assert.deepEqual(target.posts().at(-1).body, {kind: 'zip', chapter: 1});
  assert.match(target.get('export-links').innerHTML, /下载本章 ZIP/);
  await target.click('export-book-pdf'); assert.deepEqual(target.posts().at(-1).body, {kind: 'pdf'});
  assert.match(target.get('export-links').innerHTML, /下载全书 PDF/);
}

async function testLateRepliesAndReset() {
  const target = harness({comic_a: project(), comic_b: project('comic_b')}, {longComicSelected: 'comic_a'});
  target.projects.comic_b.title = '另一本漫画'; await target.render();
  const old = deferred(); target.getWith(url => url.endsWith('/comic_a') ? old.promise : undefined);
  const polling = target.tick(); await flush();
  target.get('projects').value = 'comic_b'; target.get('projects').onchange(); await flush();
  old.resolve({project: Object.assign(project(), {title: '迟到旧项目'})}); await polling;
  assert.equal(target.get('title').value, '另一本漫画'); assert.equal(target.posts().length, 0);
  target.getWith(() => undefined);
  const late = deferred(); target.postWith(url => url.endsWith('/run') ? late.promise : undefined);
  const starting = target.click('generate'); await flush();
  target.emit('workbench-runtime-reset', {phase: 'begin'}); target.emit('workbench-runtime-reset', {phase: 'done'});
  assert.equal(target.timers.size, 0); assert.match(target.get('status').textContent, /不会自动重新生成/);
  late.resolve({project: Object.assign(project('comic_b'), {title: '迟到生成响应', job: {status: 'running'}})}); await starting;
  assert.equal(target.get('title').value, '另一本漫画'); assert.equal(target.timers.size, 0);
  target.projects.comic_b.job = {status: 'cancelled'}; await target.click('refresh');
  assert.match(target.get('status').textContent, /已取消/);
  const leaving = deferred(); target.getWith(url => url.endsWith('/comic_b') ? leaving.promise : undefined);
  const during = target.tick(); await flush();
  const status = target.get('status').textContent, callCount = target.calls.length;
  target.location.hash = '#story-video'; target.emit('hashchange');
  leaving.resolve({project: Object.assign(project('comic_b'), {job: {status: 'running'}})}); await during;
  assert.equal(target.get('status').textContent, status); assert.equal(target.timers.size, 0);
  assert.equal(target.calls.length, callCount);
}

async function testRefreshRecoveryAndErrors() {
  const target = harness({comic_a: project()}, {longComicSelected: 'comic_a'});
  let failList = true;
  target.getWith(url => url.endsWith('/projects') && failList ? Promise.reject(new Error('测试网络离线')) : undefined);
  await target.render(); assert.match(target.get('error').textContent, /测试网络离线/);
  failList = false; await target.click('refresh'); assert.equal(target.get('title').value, '森林归途');
  target.input('title', '失败后仍保留');
  target.postWith(() => Promise.reject(new Error('<script>保存失败</script>')));
  await target.click('save-project'); assert.equal(target.get('title').value, '失败后仍保留');
  assert.equal(target.get('error').textContent, '<script>保存失败</script>');
  assert.equal(target.get('save-project').disabled, false);
  target.postWith(() => undefined); await target.click('save-project');
  assert.deepEqual(target.posts().at(-1).body, {title: '失败后仍保留'});
  const count = target.posts().length, sameStorage = Object.fromEntries(target.storage);
  const reloaded = harness(target.projects, sameStorage); await reloaded.render();
  assert.equal(reloaded.get('title').value, '失败后仍保留'); assert.equal(reloaded.posts().length, 0);
  await reloaded.tick(); assert.equal(reloaded.posts().length, 0); assert.equal(target.posts().length, count);
  await reloaded.click('new');
  for (const [name, value] of Object.entries({title: '测试', style: '手绘', characters: '甲', story: '回家', chapters: '21'})) reloaded.input(name, value);
  await reloaded.click('plan'); assert.equal(reloaded.posts().length, 0); assert.match(reloaded.get('error').textContent, /1—20/);
  const invalidDraft = harness({}, Object.fromEntries(reloaded.storage)); await invalidDraft.render();
  assert.equal(invalidDraft.get('chapter').children.length, 20);
}

const sourceFixture = {
  sources: [{story_id: 'STORY_A', title: '第一个项目', character_count: 1}, {story_id: 'STORY_B', title: '当前项目', character_count: 2}],
  characters: {
    STORY_A: [{character_id: 'CHAR_A', name: '旧来源人物', appearance: '短发', clothing: '蓝外套'}],
    STORY_B: [{character_id: 'CHAR_B1', name: '人物甲', appearance: '黑色短发', clothing: '白衬衫'}, {character_id: 'CHAR_B2', name: '人物乙', appearance: '戴眼镜', clothing: '灰外套'}]
  }
};

async function testCharacterSourceSelectionSnapshotAndRealCurrentDefault() {
  const original = copy(sourceFixture);
  const target = harness({}, {}, Object.assign(copy(sourceFixture), {storyId: 'STORY_B'})); await target.render();
  assert.equal(target.get('source').value, 'STORY_B');
  assert.match(target.get('source-characters').innerHTML, /人物甲.*黑色短发/);
  for (const [name, value] of Object.entries({title: '复用人物漫画', style: '手绘', story: '两位朋友一起回家'})) target.input(name, value);
  await target.click('save-project'); assert.equal(target.posts().length, 0);
  assert.match(target.get('error').textContent, /勾选至少一位/);
  const selected = target.get('source-characters').querySelector('[data-source-person="CHAR_B2"]');
  selected.checked = true; selected.onchange();
  await target.click('save-project');
  assert.equal(target.posts()[0].body.source_story_id, 'STORY_B');
  assert.deepEqual(target.posts()[0].body.source_character_ids, ['CHAR_B2']);
  assert.deepEqual(target.projects.comic_new.character_source.characters.map(item => item.source_character_id), ['CHAR_B2']);
  assert.match(target.get('source-note').textContent, /独立快照/);
  assert.equal(target.get('characters').disabled, true);
  target.projects.comic_new.outline = {premise: '已确认'}; await target.tick();
  assert.equal(target.get('source').disabled, true);
  assert(target.get('source-characters').querySelectorAll('input').every(control => control.disabled));
  assert.deepEqual(sourceFixture, original);
  const manual = harness({}, {}, copy(sourceFixture)); await manual.render();
  assert.equal(manual.get('source').value, '');
  assert.equal(manual.calls.some(call => /\/sources\/STORY_A\/characters$/.test(call.url)), false);
}

async function testSourceLateReplyAndResetCannotReplaceNewSelection() {
  const target = harness({}, {}, copy(sourceFixture)); await target.render();
  const first = deferred(), second = deferred();
  target.getWith(url => url.endsWith('/STORY_A/characters') ? first.promise : url.endsWith('/STORY_B/characters') ? second.promise : undefined);
  target.get('source').value = 'STORY_A'; const loadingFirst = target.get('source').onchange(); await flush();
  target.get('source').value = 'STORY_B'; const loadingSecond = target.get('source').onchange(); await flush();
  second.resolve({characters: sourceFixture.characters.STORY_B}); await loadingSecond;
  assert.match(target.get('source-characters').innerHTML, /人物甲/);
  first.resolve({characters: sourceFixture.characters.STORY_A}); await loadingFirst;
  assert.equal(target.get('source').value, 'STORY_B');
  assert(!target.get('source-characters').innerHTML.includes('旧来源人物'));
  const late = deferred(); target.getWith(url => url.endsWith('/STORY_A/characters') ? late.promise : undefined);
  target.get('source').value = 'STORY_A'; const loading = target.get('source').onchange(); await flush();
  target.emit('workbench-runtime-reset', {phase: 'begin'}); target.emit('workbench-runtime-reset', {phase: 'done'});
  const markup = target.get('source-characters').innerHTML;
  late.resolve({characters: sourceFixture.characters.STORY_A}); await loading;
  assert.equal(target.get('source-characters').innerHTML, markup);
  assert.match(target.get('status').textContent, /不会自动重新生成/); assert.equal(target.posts().length, 0);
}

async function testV2PanelCastDialogueStateAndUnknownFieldsSurviveEditing() {
  const value = project(); value.continuity_version = 2;
  const originalPanel = {number: 1, description: '甲脱下外套，把外套放在椅背上。', character_ids: ['char_a'], dialogue: [{speaker: 'char_a', text: '我放在这里'}], narration: '',
    state_changes: [{character_id: 'char_a', field: 'clothing', value: '白衬衫', evidence: '脱下外套'}],
    opening_state: {char_a: {clothing: '蓝外套', held_items: '空手'}}, ending_state: {char_a: {clothing: '白衬衫', held_items: '空手'}}, camera: '中景', extra_contract: {retained: true}};
  value.chapters[0].pages[0].panels = [copy(originalPanel), {number: 2, description: '乙在门边等待。', character_ids: ['char_b'], dialogue: [], narration: '', state_changes: []}];
  const target = harness({comic_a: value}, {longComicSelected: 'comic_a'}); await target.render();
  assert(!target.get('panels').innerHTML.includes('[object Object]'));
  const first = target.get('panels').querySelectorAll('[data-lc-panel]')[0];
  const dialogue = first.querySelector('[data-field="dialogue"]'); dialogue.value = '乙：我在这里'; dialogue.oninput();
  await target.click('save-page'); assert.equal(target.posts().length, 0); assert.match(target.get('error').textContent, /本格勾选/);
  const add = first.querySelector('[data-add-change]'); add.onclick();
  const changes = first.querySelector('[data-state-changes]').querySelectorAll('[data-change]');
  changes[1].querySelector('[data-change-field="field"]').value = 'held_items';
  const result = changes[1].querySelector('[data-change-field="value"]'); result.value = '空手'; result.oninput();
  const evidence = changes[1].querySelector('[data-change-field="evidence"]'); evidence.value = '把外套放在椅背上'; evidence.oninput();
  const other = first.querySelector('[data-person="char_b"]'); other.checked = true; other.onchange();
  await target.click('save-page');
  const saved = target.posts().at(-1).body.panels[0];
  assert.deepEqual(saved.character_ids, ['char_a', 'char_b']);
  assert.deepEqual(saved.dialogue, [{speaker: 'char_b', text: '我在这里'}]);
  assert.deepEqual(saved.state_changes, [originalPanel.state_changes[0], {character_id: 'char_a', field: 'held_items', value: '空手', evidence: '把外套放在椅背上'}]);
  assert.deepEqual(saved.opening_state, originalPanel.opening_state); assert.deepEqual(saved.extra_contract, {retained: true}); assert.equal(saved.camera, '中景');
  assert.deepEqual(target.posts().at(-1).body.panels[1].character_ids, ['char_b']);
}

async function testLegacyUnplannedProjectDoesNotAcquireSourceOrV2Fields() {
  const value = project('comic_old', false);
  const target = harness({comic_old: value}, {longComicSelected: 'comic_old'}, Object.assign(copy(sourceFixture), {storyId: 'STORY_B'})); await target.render();
  assert.equal(target.get('source').disabled, true); assert.equal(target.get('source').value, '');
  target.input('title', '旧漫画保留原规则'); await target.click('save-project');
  const body = target.posts().at(-1).body;
  assert(!Object.hasOwn(body, 'source_story_id')); assert(!Object.hasOwn(body, 'source_character_ids'));
  assert(!Object.hasOwn(body, 'continuity_version'));
}

async function testOutdatedChapterRequiresExplicitReplanningAndBlocksExports() {
  const value = project(); value.continuity_version = 2;
  value.chapters.push({number: 2, title: '桥边', summary: '承接前章状态', continuity_review_required: true, pages: [page(1)]});
  value.job = {status: 'paused', chapter: 2, action: 'generate'};
  const target = harness({comic_a: value}, {longComicSelected: 'comic_a'}); await target.render();
  assert.equal(target.get('export-chapter-zip').disabled, false);
  assert.equal(target.get('export-book-zip').disabled, true);
  target.get('chapter').value = '2'; target.get('chapter').onchange();
  assert.equal(target.get('replan-chapter').hidden, false); assert.equal(target.get('replan-chapter').disabled, false);
  assert.equal(target.get('continuity-note').hidden, false); assert.match(target.get('continuity-note').textContent, /旧规划和图片会保留/);
  for (const name of ['generate', 'redo', 'resume', 'plan-chapter', 'export-chapter-zip', 'export-chapter-pdf', 'export-book-zip', 'export-book-pdf']) assert.equal(target.get(name).disabled, true, name);
  await target.click('generate'); await target.click('resume'); await target.click('export-chapter-zip'); await target.click('export-book-pdf');
  assert.equal(target.posts().length, 0);
  await target.click('replan-chapter');
  assert.deepEqual(target.posts().map(call => call.body), [{action: 'replan_chapter', chapter: 2}]);
  target.projects.comic_a.chapters[1].continuity_review_required = false;
  target.projects.comic_a.chapters[1].plan_versions = [{version: 1, pages: [page(1)]}];
  target.projects.comic_a.job = {status: 'completed'}; await target.tick();
  assert.equal(target.get('replan-chapter').hidden, true); assert.equal(target.get('continuity-note').hidden, true);
  assert.equal(target.get('generate').disabled, false); assert.equal(target.get('export-book-pdf').disabled, false);
  assert.equal(target.projects.comic_a.chapters[1].plan_versions.length, 1);
  const legacy = project(); legacy.chapters[0].continuity_review_required = true;
  const old = harness({comic_a: legacy}, {longComicSelected: 'comic_a'}); await old.render();
  assert.equal(old.get('replan-chapter').hidden, true); assert.equal(old.get('generate').disabled, false);
}

function repairProject(id = 'comic_a') {
  const value = project(id);
  value.character_references = {
    char_a: {id:'ref_a',url:'/files/outputs/long_comic/' + id + '/a.png'},
    char_b: {id:'ref_b',url:'/files/outputs/long_comic/' + id + '/b.png'}
  };
  return value;
}
function selectRegion(target) {
  const canvas = target.get('repair-canvas');
  canvas.onpointerdown({clientX:410,clientY:660,pointerId:1,button:0,preventDefault() {}});
  canvas.onpointermove({clientX:60,clientY:100,pointerId:1});
  canvas.onpointerup({clientX:60,clientY:100,pointerId:1});
  target.input('repair-prompt','按参考图修正五官，保持表情和服装');
}
async function testRepairCoordinatesCandidatePreviewAndExplicitAdoption() {
  const target = harness({comic_a:repairProject()},{longComicSelected:'comic_a'}); await target.render();
  assert.match(target.get('references').innerHTML,/a\.png/);
  selectRegion(target);
  assert.deepEqual(['x1','y1','x2','y2'].map(name => Number(target.get('region-' + name).value)),[100,160,800,1280]);
  assert.equal(target.get('repair-submit').disabled,false);
  await target.click('repair-submit');
  assert.deepEqual(target.posts().at(-1).body,{chapter:1,page:1,base_version:1,region:[100,160,800,1280],character_ids:['char_a','char_b'],prompt:'按参考图修正五官，保持表情和服装'});
  assert.equal(target.projects.comic_a.chapters[0].pages[0].current_version,1);
  assert.equal(target.get('repair-submit').disabled,true);
  const candidate = {version:2,kind:'edit',candidate:true,status:'completed',url:'/files/outputs/long_comic/comic_a/candidate.png'};
  target.projects.comic_a.chapters[0].pages[0].versions.push(candidate);
  target.projects.comic_a.job = {status:'completed',action:'edit_page'}; await target.tick();
  assert.equal(target.projects.comic_a.chapters[0].pages[0].current_version,1);
  assert.equal(target.get('repair-image').getAttribute('src'),'/files/outputs/long_comic/test/page_1.png');
  assert.match(target.get('gallery').innerHTML,/待确认候选/);
  target.get('repair-version').value = '2'; target.get('repair-version').onchange();
  assert.equal(target.get('repair-image').getAttribute('src'),candidate.url);
  assert.equal(target.get('repair-canvas').hidden,true);
  await target.click('show-compare'); assert.equal(target.get('candidate').hidden,false);
  assert.equal(target.get('candidate-image').getAttribute('src'),candidate.url);
  assert.equal(target.get('repair-adopt').disabled,false);
  await target.click('repair-adopt');
  assert.deepEqual(target.posts().at(-1).body,{chapter:1,page:1,version:2});
  assert.equal(target.projects.comic_a.chapters[0].pages[0].current_version,2);
  assert.equal(target.get('repair-adopt').disabled,true);
}
async function testRepairBlockingAndEditJobCannotResumeChapter() {
  for (const mode of ['busy','page-old','chapter-old','dirty']) {
    const value = repairProject(); value.continuity_version = 2;
    value.chapters[0].pages[0].versions.push({version:2,kind:'edit',candidate:true,status:'completed',url:'/files/outputs/candidate.png'});
    if (mode === 'busy') value.job = {status:'running',action:'edit_page'};
    if (mode === 'page-old') value.chapters[0].pages[0].needs_redo = true;
    if (mode === 'chapter-old') value.chapters[0].continuity_review_required = true;
    const target = harness({comic_a:value},{longComicSelected:'comic_a'}); await target.render();
    if (mode === 'dirty') target.input('page-summary','新的分镜草稿');
    assert.equal(target.get('repair-submit').disabled,true,mode);
    assert.equal(target.get('repair-adopt').disabled,true,mode);
    const button = target.get('gallery').querySelector('[data-adopt-version="2"]');
    assert.equal(button.disabled,true,mode);
    await target.click('repair-submit'); await target.click('repair-adopt'); await button.onclick();
    assert.equal(target.posts().length,0,mode);
  }
  const value = repairProject(); value.job = {status:'failed',action:'edit_page',chapter:1};
  const target = harness({comic_a:value},{longComicSelected:'comic_a'}); await target.render();
  assert.equal(target.get('resume').hidden,true); await target.click('resume'); assert.equal(target.posts().length,0);
  target.input('region-x1','0'); target.input('region-y1','0'); target.input('region-x2','1001'); target.input('region-y2','100');
  target.input('repair-prompt','修正五官'); assert.equal(target.get('repair-submit').disabled,true);
  await target.click('repair-submit'); assert.equal(target.posts().length,0); assert.match(target.get('error').textContent,/坐标/);
}
async function testFreshCandidateIsShownOnceWithoutAdoptionOrOverridingManualPreview() {
  const target = harness({comic_a:repairProject()},{longComicSelected:'comic_a'}); await target.render();
  selectRegion(target); await target.click('repair-submit');
  const job = copy(target.projects.comic_a.job);
  const candidate = {version:2,kind:'edit',candidate:true,status:'completed',url:'/files/outputs/long_comic/comic_a/fresh_candidate.png'};
  target.projects.comic_a.chapters[0].pages[0].versions.push(candidate);
  target.projects.comic_a.job = Object.assign(job,{status:'completed',candidate_version:2}); await target.tick();
  assert.equal(target.get('repair-version').value,'2');
  assert.equal(target.get('candidate').hidden,false);
  assert.equal(target.get('candidate-image').getAttribute('src'),candidate.url);
  assert.equal(target.get('show-compare').getAttribute('aria-pressed'),'true');
  assert.equal(target.projects.comic_a.chapters[0].pages[0].current_version,1);
  assert.equal(target.posts().some(call => call.url.endsWith('/adopt')),false);
  target.get('repair-version').value = '1'; target.get('repair-version').onchange();
  await target.tick();
  assert.equal(target.get('repair-version').value,'1');
  assert.equal(target.get('candidate').hidden,true);
  assert.equal(target.get('repair-image').getAttribute('src'),'/files/outputs/long_comic/test/page_1.png');
  assert.equal(target.get('show-selected').getAttribute('aria-pressed'),'true');
  const existing = copy(target.projects.comic_a);
  existing.chapters[0].pages[0].versions[1].candidate = false;
  const opened = harness({comic_a:existing},{longComicSelected:'comic_a'}); await opened.render();
  assert.equal(opened.get('candidate').hidden,true);
  assert.match(opened.get('repair-version').innerHTML,/修图版本/);
  assert.equal(opened.get('show-current').getAttribute('aria-pressed'),'true');
}
async function testRepairLateResponseCannotReplaceNewPage() {
  const target = harness({comic_a:repairProject()},{longComicSelected:'comic_a'}); await target.render();
  selectRegion(target);
  const late = deferred(); target.postWith(url => url.endsWith('/edit') ? late.promise : undefined);
  const editing = target.click('repair-submit'); await flush();
  target.get('page-select').value = '2'; target.get('page-select').onchange();
  const staleProject = repairProject(); staleProject.title = '迟到的修图状态'; staleProject.chapters[0].pages[1].summary = '不该覆盖新页';
  late.resolve({project:staleProject}); await editing;
  assert.equal(target.get('page-select').value,'2'); assert.equal(target.get('title').value,'森林归途');
  assert.notEqual(target.get('page-summary').value,'不该覆盖新页'); assert.equal(target.get('region-x1').value,'');
  assert.equal(target.get('repair-image').getAttribute('src'),'/files/outputs/long_comic/test/page_2.png');
}
async function testReferenceUploadStaysBoundToProjectAndRejectsOldReplies() {
  const target = harness({comic_a:repairProject(),comic_b:repairProject('comic_b')},{longComicSelected:'comic_a'}); await target.render();
  let control = target.get('references').querySelector('[data-reference-person="char_a"]');
  control.files = [{type:'image/png',size:200,data:'data:image/png;base64,cGljdHVyZQ=='}]; await control.onchange(); await flush();
  assert.equal(target.posts().at(-1).url,'/api/long-comic/project/comic_a/reference');
  assert.deepEqual(target.posts().at(-1).body,{character_id:'char_a',image_data:'data:image/png;base64,cGljdHVyZQ=='});
  control = target.get('references').querySelector('[data-reference-person="char_a"]');
  control.files = [{type:'image/png',size:9*1024*1024}]; await control.onchange();
  assert.equal(target.posts().length,1); assert.match(target.get('error').textContent,/8 MB/);
  const late = deferred(); target.postWith(url => url.endsWith('/reference') ? late.promise : undefined);
  control.files = [{type:'image/webp',size:123}]; const saving = control.onchange(); await flush();
  target.get('projects').value = 'comic_b'; await target.get('projects').onchange(); await flush();
  const staleProject = repairProject(); staleProject.title = '旧上传返回';
  late.resolve({project:staleProject}); await saving;
  assert.equal(target.get('title').value,'森林归途'); assert.equal(target.get('projects').value,'comic_b');
  assert.match(target.get('references').innerHTML,/comic_b\/a\.png/);
  const reading = harness({comic_a:repairProject(),comic_b:repairProject('comic_b')},{longComicSelected:'comic_a'},{deferRead:true}); await reading.render();
  const upload = reading.get('references').querySelector('[data-reference-person="char_b"]'); upload.files = [{type:'image/png',size:100}];
  const waiting = upload.onchange(); await flush();
  reading.get('projects').value = 'comic_b'; reading.get('projects').onchange(); await flush();
  reading.readers[0].onload(); await waiting;
  assert.equal(reading.posts().length,0);
}

async function main() {
  if (process.argv.includes('--continuity-review-only')) {
    await testOutdatedChapterRequiresExplicitReplanningAndBlocksExports();
    console.log('Long comic UI: targeted outdated-chapter check passed for replan dispatch, generation/resume/export blocking, version retention and legacy isolation. No server or model calls.');
    return;
  }
  assert.match(fs.readFileSync(path.join(root, 'web/shell.js'), 'utf8'), /h: "#long-comic"/);
  assert.match(fs.readFileSync(path.join(root, 'web/index.html'), 'utf8'), /src="\/pages\/long_comic\.js"/);
  await testCreateAndRun(); await testPartialBatchKeepsSavedPagesAndOffersExplicitResume();
  await testDirtyEditingAndExport(); await testLateRepliesAndReset(); await testRefreshRecoveryAndErrors();
  await testCharacterSourceSelectionSnapshotAndRealCurrentDefault(); await testSourceLateReplyAndResetCannotReplaceNewSelection();
  await testV2PanelCastDialogueStateAndUnknownFieldsSurviveEditing(); await testLegacyUnplannedProjectDoesNotAcquireSourceOrV2Fields();
  await testOutdatedChapterRequiresExplicitReplanningAndBlocksExports();
  await testRepairCoordinatesCandidatePreviewAndExplicitAdoption(); await testRepairBlockingAndEditJobCannotResumeChapter();
  await testFreshCandidateIsShownOnceWithoutAdoptionOrOverridingManualPreview();
  await testRepairLateResponseCannotReplaceNewPage(); await testReferenceUploadStaysBoundToProjectAndRejectsOldReplies();
  console.log('Long comic UI: 15 groups passed, including original-coordinate crop selection, fresh candidate comparison without adoption or repeated preview resets, explicit target-version adoption, busy/stale/dirty blocking, edit-job resume isolation and project-bound uploads/stale replies. No server or model calls.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
