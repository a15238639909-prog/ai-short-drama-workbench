'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.resolve(__dirname, '..', '_legacy_8848', 'app.py'), 'utf8').replace(/\r\n/g, '\n');
const plain = value => JSON.parse(JSON.stringify(value));
const flush = async () => { for (let count = 0; count < 30; count++) await Promise.resolve(); };
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; };
const animaArtists = [
  {value: '@chigusa_minori', label: '@chigusa_minori · 原图画师'},
  {value: '@as109', label: '@as109 · 女孩系列'},
  {value: '@yoneyama mai', label: '@yoneyama mai · 米山舞'},
  {value: '@billyhhyb', label: '@billyhhyb · 3D模型'},
  {value: '@ohako', label: '@ohako · 仿手绘'},
  {value: '@yogisya', label: '@yogisya · 唯美国漫插画'},
  {value: '@tianliang duohe fangdongye', label: '@tianliang duohe fangdongye · 可爱画风'},
  {value: '@fajyobore', label: '@fajyobore · 现代线条细节丰富动漫'},
  {value: '@kim hyung tae', label: '@kim hyung tae · 干净的游戏插画'}
];

class Element {
  constructor() { this.value = ''; this.textContent = ''; this.disabled = false; this.children = []; this.style = {}; }
  set innerHTML(markup) { this.markup = markup; this.children = []; }
  get innerHTML() { return this.markup || ''; }
  appendChild(child) { this.children.push(child); }
  focus() {}
}

function harness() {
  const nodes = {}, messages = [];
  const get = name => nodes[name] || (nodes[name] = new Element());
  get('promptModel').value = 'krea2';
  get('promptImageRatio').value = '16:9';
  get('promptImageQuality').value = '2k';
  const context = vm.createContext({
    $: get, location: {origin: 'http://127.0.0.1:8853'}, document: {createElement: () => new Element()},
    setStatus: message => messages.push(message), setTask: () => {}, alert: message => messages.push(message),
    AbortController, console, fetch: async url => {
      if (url === 'http://127.0.0.1:8853/api/image/anima/config') return {ok: true, json: async () => ({ok: true,
        data: {artists: animaArtists, default_artist: '@chigusa_minori', quality_prefix: ['best quality'], fixed_prefix: '@chigusa_minori, best quality'}})};
      throw new Error('Unexpected request');
    }
  });
  const presetStart = source.indexOf('let presets=[], promptCtrl=null, promptImageCtrl=null;');
  vm.runInContext(source.slice(presetStart, source.indexOf('let promptUploadData=null;', presetStart)), context);
  const modelStart = source.indexOf('let promptActiveModel="krea2", promptModelDrafts={};');
  vm.runInContext(source.slice(modelStart, source.indexOf('async function promptImageGenTwoStage(', modelStart)), context);
  const imageStart = source.indexOf('async function promptImageGen(){');
  vm.runInContext(source.slice(imageStart, source.indexOf('async function promptImageGenHires(', imageStart)), context);
  const queueStart = source.indexOf('let pqItems=[],');
  const queueEnd = source.indexOf('\n}\n', source.indexOf('async function pqStart(){', queueStart)) + 3;
  vm.runInContext(source.slice(queueStart, queueEnd), context);
  for (const functionName of ['promptStop', 'stopAll']) {
    const start = source.indexOf('function ' + functionName + '(){');
    vm.runInContext(source.slice(start, source.indexOf('\n}\n', start) + 3), context);
  }
  vm.runInContext('presets = ' + JSON.stringify([
    {name: '通用提示词优化', model: 'krea2', system: '通用中文'},
    {name: 'Krea2·作品角色人设四视图', model: 'krea2', system: '人物中文'},
    {name: 'Moss·场景图', model: 'moss', system: 'Moss场景中文', builtin: true},
    {name: 'Moss·人物人设图', model: 'moss', system: 'Moss人物布局', builtin: true},
    {name: 'Anima·常规单图', model: 'anima', system: 'English tags', builtin: true},
    {name: 'Anima·人设图', model: 'anima', system: 'English sheet tags', builtin: true},
    {name: 'GPT优化指令词', model: 'krea2', system: 'GPT Krea2策略'},
    {name: 'GPT优化指令词', model: 'moss', system: 'GPT Moss策略'}
  ]), context);
  return {context, get, messages, state: expression => plain(vm.runInContext(expression, context))};
}

async function switchModel(target, model) {
  target.get('promptModel').value = model;
  await target.context.promptModelChanged();
}

async function testDraftsAndSizes() {
  const target = harness(), {context, get} = target;
  get('promptIn').value = '作品：七龙珠，成年布玛';
  get('promptOut').textContent = '蓝绿短发人物四视图';
  get('presetName').value = get('presetSel').value = 'Krea2·作品角色人设四视图';
  get('presetSys').value = '人物中文';
  await switchModel(target, 'moss');
  assert.equal(get('promptIn').value, '');
  assert.equal(get('promptOut').textContent, '');
  assert.equal(get('presetSys').value, 'GPT Moss策略');
  assert.equal(get('presetSel').value, 'GPT优化指令词');
  assert.equal(get('promptImageQuality').value, '2x');
  assert.match(get('promptImageQuality').title, /Moss.*1\.2.*约1MP.*4K/);
  assert(get('promptImageQuality').innerHTML.includes('workflow'));
  assert(get('promptImageQuality').innerHTML.includes('4k'));
  get('promptOut').textContent = 'Moss编辑后的中文人设';
  await switchModel(target, 'anima');
  assert.equal(get('presetSys').value, 'English tags');
  assert.match(get('promptImageQuality').title, /Anima.*直出/);
  assert.match(get('promptModelHint').textContent, /英文词组.*混合排队/);
  get('promptOut').textContent = 'adult character, blue hair';
  await switchModel(target, 'moss');
  assert.equal(get('promptOut').textContent, 'Moss编辑后的中文人设');
  await switchModel(target, 'krea2');
  assert.equal(get('promptOut').textContent, '蓝绿短发人物四视图');
  assert.equal(get('promptIn').value, '作品：七龙珠，成年布玛');
  assert.equal(get('promptImageQuality').value, '2k');
  assert.match(get('promptImageQuality').title, /Krea2.*1\.5/);
  await switchModel(target, 'anima');
  assert.equal(get('promptOut').textContent, 'adult character, blue hair');
  assert.equal(context.promptSizeSpec('16:9', '2k', 'krea2').w, 1920);
  assert.equal(context.promptSizeSpec('16:9', '2k', 'anima').final_w, context.promptSizeSpec('16:9', '2k', 'anima').w);
  const original = plain(context.promptSizeSpec('9:16', 'workflow', 'moss'));
  assert.deepEqual([original.w, original.h, original.final_w, original.final_h], [768, 1360, 928, 1632]);
  for (const ratio of ['1:1', '4:3', '3:2', '16:9', '2.35:1', '4:5', '2:3', '9:16']) {
    const size = context.promptSizeSpec(ratio, '4k', 'moss');
    assert.equal(size.quality, '4k');
    assert.equal(size.w % 16, 0); assert.equal(size.h % 16, 0);
    assert.equal(size.upscale, 2);
    assert.equal(Math.max(size.final_w, size.final_h), 3840);
    assert(size.w * size.h <= 1920 * 1920);
  }
}

async function testMixedQueue() {
  const target = harness(), {context, get} = target;
  await switchModel(target, 'moss');
  get('promptImageRatio').value = '9:16'; get('promptOut').textContent = 'Moss第一张'; context.pqAdd();
  await switchModel(target, 'krea2');
  get('promptImageQuality').value = '1k'; get('promptImageRatio').value = '1:1';
  get('promptOut').textContent = 'Krea第二张'; context.pqAdd();
  await switchModel(target, 'moss');
  get('promptImageRatio').value = '16:9'; get('promptOut').textContent = 'Moss第三张'; context.pqAdd();
  const frozen = target.state('pqItems.map(item=>item.request)');
  assert(vm.runInContext('pqItems.every(item=>Object.isFrozen(item.request))', context));
  const calls = [], pending = []; let active = 0, maximumActive = 0;
  context.fetch = async (url, options) => {
    active++; maximumActive = Math.max(maximumActive, active);
    calls.push({url, payload: JSON.parse(options.body)});
    const gate = deferred(); pending.push(gate);
    const result = await gate.promise; active--;
    return {ok: result.ok !== false, text: async () => JSON.stringify(result)};
  };
  const running = context.pqStart(); await flush();
  assert.equal(calls.length, 1);
  get('promptOut').textContent = '运行时改词'; get('promptImageRatio').value = '2:3';
  get('promptImageQuality').value = '4k'; await switchModel(target, 'anima');
  assert.equal(get('promptModel').value, 'moss');
  assert.equal(calls.length, 1);
  pending[0].resolve({ok: true, data: {url: '/media/moss-first.png'}}); await flush();
  assert.equal(calls.length, 2);
  pending[1].resolve({ok: true, archurl: '/legacy/media/krea-second.png'}); await flush();
  assert.equal(calls.length, 3);
  pending[2].resolve({ok: true, data: {url: '/media/moss-third.png'}}); await running;
  assert.equal(maximumActive, 1);
  assert.deepEqual(calls.map(call => call.payload.model || 'krea2'), ['moss', 'krea2', 'moss']);
  assert.deepEqual(calls.map(call => call.payload.text || call.payload.prompt), ['Moss第一张', 'Krea第二张', 'Moss第三张']);
  calls.forEach((call, index) => {
    assert.equal(call.payload.seed, frozen[index].seed);
    assert.equal(call.payload.base_w || call.payload.w, frozen[index].base_w);
    assert.equal(call.payload.base_h || call.payload.h, frozen[index].base_h);
  });
  assert(calls[0].url.endsWith('/api/image/generate'));
  assert.equal(calls[1].url, '/api/genimage');
  assert.deepEqual(target.state('pqItems.map(item=>item.status)'), ['finished', 'finished', 'finished']);
  assert.equal(target.state('pqRunning'), false);
  assert.equal(get('btnPqStart').disabled, false);
}

async function testModelSpecificGptDrafts() {
  const target = harness(), {context, get} = target;
  get('presetSel').value = 'GPT优化指令词'; context.presetPick();
  get('presetSys').value = 'Krea2 GPT自定义指令';
  get('promptIn').value = '同一个中文画面需求';
  get('promptOut').textContent = '已经整理的正向词';
  get('promptImageQuality').value = '4k';
  get('promptImageRatio').value = '9:16';
  await switchModel(target, 'moss');
  assert.equal(get('presetSel').value, 'GPT优化指令词');
  assert.equal(get('presetSys').value, 'GPT Moss策略');
  assert.equal(get('promptIn').value, '');
  assert.equal(get('promptOut').textContent, '');
  assert.equal(get('promptImageQuality').value, '2x');
  assert.equal(get('promptImageRatio').value, '16:9');
  get('presetSys').value = 'Moss GPT自定义指令';
  get('promptOut').textContent = 'Moss编辑后的正向词';
  await switchModel(target, 'krea2');
  assert.equal(get('presetSys').value, 'Krea2 GPT自定义指令');
  assert.equal(get('promptOut').textContent, '已经整理的正向词');
  assert.equal(get('promptImageQuality').value, '4k');
  assert.equal(get('promptImageRatio').value, '9:16');
  get('presetSys').value = 'Krea2 GPT二次编辑';
  await switchModel(target, 'moss');
  assert.equal(get('presetSys').value, 'Moss GPT自定义指令');
  get('presetSel').value = 'Moss·场景图'; context.presetPick();
  get('presetSys').value = 'Moss场景自定义';
  await switchModel(target, 'krea2');
  assert.equal(get('presetSel').value, 'GPT优化指令词');
  assert.equal(get('presetSys').value, 'Krea2 GPT二次编辑');
  get('presetSel').value = 'GPT优化指令词'; context.presetPick();
  get('presetSys').value = 'Krea2 GPT第三次编辑';
  await switchModel(target, 'moss');
  assert.equal(get('presetSys').value, 'Moss场景自定义');
  await switchModel(target, 'anima');
  assert.equal(get('presetSys').value, 'English tags');
  get('presetSel').value = 'Anima·人设图'; context.presetPick();
  get('presetSys').value = 'Anima sheet custom tags';
  get('promptIn').value = 'Anima独立需求';
  get('promptOut').textContent = 'anime, independent tags';
  await switchModel(target, 'krea2');
  assert.equal(get('presetSys').value, 'Krea2 GPT第三次编辑');
  assert.equal(get('promptIn').value, '同一个中文画面需求');
  await switchModel(target, 'anima');
  assert.equal(get('presetSys').value, 'Anima sheet custom tags');
  assert.equal(get('presetSel').value, 'Anima·人设图');
  assert.equal(get('promptIn').value, 'Anima独立需求');
  assert.equal(get('promptOut').textContent, 'anime, independent tags');

  const reverse = harness();
  await switchModel(reverse, 'moss');
  reverse.get('presetSel').value = 'GPT优化指令词'; reverse.context.presetPick();
  reverse.get('presetSys').value = 'Moss先选GPT后的编辑';
  await switchModel(reverse, 'krea2');
  assert.equal(reverse.get('presetSys').value, '');
  await switchModel(reverse, 'moss');
  assert.equal(reverse.get('presetSys').value, 'Moss先选GPT后的编辑');
}

async function testLegacyAnimaAndRetry() {
  const target = harness(), {context, get} = target, calls = [];
  await switchModel(target, 'moss');
  get('promptImageRatio').value = '9:16'; get('promptImageQuality').value = 'workflow';
  vm.runInContext('pqItems=[{id:"old",prompt:"旧任务",size:"1K",checked:true,status:"ready"},{id:"anima",model:"anima",prompt:"forest, sunlight",ratio:"1:1",quality:"1k",checked:true,status:"ready"},{id:"large",model:"krea2",prompt:"Krea二次采样",ratio:"16:9",quality:"2k",checked:true,status:"ready"}]', context);
  context.fetch = async (url, options) => {
    calls.push({url, payload: JSON.parse(options.body)});
    return {ok: true, text: async () => JSON.stringify(url === '/api/genimage' ? {ok: true, url: '/old.png'} : {ok: true, data: {url: '/new.png'}})};
  };
  await context.pqStart();
  assert.equal(calls[0].url, '/api/genimage');
  assert.deepEqual([calls[0].payload.w, calls[0].payload.h], [1536, 864]);
  assert.equal(calls[1].payload.model, 'anima');
  assert(!Object.hasOwn(calls[1].payload, 'artist'));
  assert.equal(calls[2].payload.model, 'krea2');
  assert.equal(calls[2].payload.base_w, 1920);
  const seed = calls[0].payload.seed;
  context.pqRetry('old'); await context.pqStart();
  assert.equal(calls.at(-1).payload.seed, seed);
  vm.runInContext('pqItems=[{id:"bad",model:"moss",prompt:"缺图测试",checked:true,status:"ready"},{id:"next",prompt:"后续任务",checked:true,status:"ready"}]', context);
  context.fetch = async url => ({ok: true, text: async () => JSON.stringify(url === '/api/genimage' ? {ok: true, url: '/next.png'} : {ok: true, data: {}})});
  await context.pqStart();
  assert.deepEqual(target.state('pqItems.map(item=>item.status)'), ['failed', 'finished']);
  assert.match(target.state('pqItems[0].error'), /没有返回图片/);
  assert.match(get('pqList').innerHTML, /原因：生图接口没有返回图片/);
  vm.runInContext('pqItems[0].error="<img src=x onerror=alert(1)>\\n端口超时 & 服务未就绪"; pqRender();', context);
  assert.match(get('pqList').innerHTML, /原因：&lt;img src=x onerror=alert\(1\)&gt;\n端口超时 &amp; 服务未就绪/);
  assert(!get('pqList').innerHTML.includes('<img src=x'));
}

async function testAnimaTwoPresetsAndLegacyDraftMigration() {
  const aliases = {
    'Anima·英文词组': ['Anima·常规单图', 'English tags'],
    'anima动漫模型提示词': ['Anima·常规单图', 'English tags'],
    'GPT优化指令词': ['Anima·常规单图', 'English tags'],
    'Anima·人设三视图＋面部特写': ['Anima·人设图', 'English sheet tags']
  };
  for (const [oldName, [newName, system]] of Object.entries(aliases)) {
    const target = harness(), {context, get} = target;
    vm.runInContext('promptModelDrafts.anima=' + JSON.stringify({input: '保留需求', out: 'saved completed prompt',
      system: 'old archived instruction', name: oldName, sel: oldName, quality: '2k', ratio: '9:16', artist: '@ohako'}), context);
    await switchModel(target, 'anima');
    assert.deepEqual(get('presetSel').children.map(row => row.value), ['Anima·常规单图', 'Anima·人设图']);
    assert.equal(get('presetSel').value, newName); assert.equal(get('presetName').value, newName);
    assert.equal(get('presetSys').value, system); assert.equal(get('promptIn').value, '保留需求');
    assert.equal(get('promptOut').textContent, 'saved completed prompt');
    assert.equal(get('promptImageQuality').value, '2k'); assert.equal(get('promptImageRatio').value, '9:16');
    assert.equal(get('animaArtist').value, '@ohako');
    context.fillPresetSelect(oldName);
    assert.equal(get('presetSel').value, newName); assert.equal(get('presetSys').value, system);
    const priorMessages = target.messages.length;
    await context.presetDel();
    assert.equal(target.messages.length, priorMessages + 1);
    assert.match(target.messages.at(-1), /内置默认预设/);
  }
  const target = harness(), {context, get} = target;
  await switchModel(target, 'anima');
  assert.equal(get('presetSel').value, 'Anima·常规单图');
  get('presetSys').value = 'edited single-image instruction';
  await switchModel(target, 'moss'); await switchModel(target, 'anima');
  assert.equal(get('presetSys').value, 'edited single-image instruction');
  vm.runInContext('presets.push({name:"我的Anima预设",model:"anima",system:"custom source"})', context);
  await switchModel(target, 'krea2');
  vm.runInContext('promptModelDrafts.anima={input:"自定义需求",out:"custom output",system:"custom edited instruction",name:"我的Anima预设",sel:"我的Anima预设",quality:"1k",ratio:"1:1",artist:"@as109"}', context);
  await switchModel(target, 'anima');
  assert.equal(get('presetSel').value, '我的Anima预设');
  assert.equal(get('presetSys').value, 'custom edited instruction');
  assert.equal(get('promptIn').value, '自定义需求'); assert.equal(get('promptOut').textContent, 'custom output');
  assert.equal(get('animaArtist').value, '@as109');
}

async function testManualMoss() {
  const target = harness(), {context, get} = target, calls = [];
  await switchModel(target, 'moss');
  get('promptImageRatio').value = '9:16'; get('promptOut').textContent = '手动中文人设';
  context.fetch = async (url, options) => {
    calls.push({url, payload: JSON.parse(options.body)});
    return {ok: true, json: async () => ({ok: true, data: {url: '/moss.png', width: 928, height: 1632, seed: 7}})};
  };
  await context.promptImageGen();
  assert.equal(calls.length, 1); assert.equal(calls[0].payload.model, 'moss');
  assert.equal(calls[0].payload.kind, 'free'); assert.equal(calls[0].payload.text, '手动中文人设');
  assert(!Object.hasOwn(calls[0].payload, 'artist'));
  assert.deepEqual([calls[0].payload.base_w, calls[0].payload.base_h], [768, 1360]);
  assert.equal(calls[0].payload.upscale, 2);
  assert.match(get('promptImagePreview').innerHTML, /928×1632/);
  assert.equal(get('promptModel').disabled, false);
}

async function testMossFrozenUpscaleAndLegacyDraft() {
  const target=harness(), {context,get}=target, calls=[];
  await switchModel(target,'moss');
  for(const quality of ['2x','4k','workflow']){
    get('promptImageQuality').value=quality;get('promptOut').textContent=quality;context.pqAdd();
  }
  const frozen=target.state('pqItems.map(row=>row.request)');
  assert.deepEqual(frozen.map(row=>row.upscale),[2,2,1.2]);
  assert.deepEqual([frozen[1].base_w,frozen[1].base_h,frozen[1].final_w,frozen[1].final_h],[1920,1088,3840,2176]);
  await switchModel(target,'krea2');await switchModel(target,'moss');
  assert.equal(get('promptImageQuality').value,'workflow');
  get('promptImageQuality').value='4k';get('promptImageRatio').value='1:1';
  context.fetch=async(url,options)=>{
    calls.push(JSON.parse(options.body));
    return {ok:true,text:async()=>JSON.stringify({ok:true,data:{url:'/done.png'}})};
  };
  await context.pqStart();
  assert.deepEqual(calls.map(row=>row.upscale),[2,2,1.2]);
  assert.deepEqual(calls.map(row=>[row.base_w,row.base_h]),frozen.map(row=>[row.base_w,row.base_h]));
}

async function testQueueStop() {
  for (const stopName of ['promptStop', 'stopAll']) {
    const target = harness(), {context, get} = target, calls = [];
    await switchModel(target, 'moss');
    get('promptOut').textContent = '停止前第一项'; context.pqAdd();
    await switchModel(target, 'krea2');
    get('promptOut').textContent = '保留第二项'; context.pqAdd();
    context.fetch = (url, options) => {
      calls.push({url, payload: options.body ? JSON.parse(options.body) : null});
      if (url === '/api/stopall') return Promise.resolve({ok: true});
      return new Promise((resolve, reject) => options.signal.addEventListener('abort', () => {
        const error = new Error('Aborted'); error.name = 'AbortError'; reject(error);
      }, {once: true}));
    };
    const running = context.pqStart(); await flush();
    assert.equal(calls.length, 1);
    context[stopName](); await running; await flush();
    assert.deepEqual(calls.map(call => call.url), ['http://127.0.0.1:8853/api/image/generate', '/api/stopall']);
    assert.deepEqual(target.state('pqItems.map(item=>item.status)'), ['ready', 'ready']);
    assert.equal(target.state('pqRunning'), false);
    assert.equal(target.state('pqCtrl'), null);
    assert.equal(get('btnPqStart').disabled, false);
  }
}

async function testAnimaArtistSelectionAndComposition() {
  const target = harness(), {context, get} = target;
  await switchModel(target, 'anima');
  assert.equal(get('animaArtistRow').style.display, 'flex');
  assert.equal(get('animaArtist').value, '@chigusa_minori');
  assert.deepEqual(get('animaArtist').children.map(option => option.value), animaArtists.map(row => row.value));
  get('promptIn').value = '山间村落';
  get('promptOut').textContent = '@chigusa_minori, best quality, mountain village, sunlight';
  const calls = [];
  context.fetch = async (url, options) => {
    calls.push({url, payload: JSON.parse(options.body)});
    return {ok: true, json: async () => ({ok: true, data: {artist: '@ohako', body: 'mountain village, sunlight',
      prompt: '@ohako, best quality, mountain village, sunlight'}})};
  };
  get('animaArtist').value = '@ohako'; await context.animaArtistChanged();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'http://127.0.0.1:8853/api/image/anima/compose');
  assert.deepEqual(calls[0].payload, {text: '@chigusa_minori, best quality, mountain village, sunlight', artist: '@ohako'});
  assert.equal(get('promptOut').textContent, '@ohako, best quality, mountain village, sunlight');
  await switchModel(target, 'krea2');
  assert.equal(get('animaArtistRow').style.display, 'none');
  await switchModel(target, 'moss');
  assert.equal(get('animaArtistRow').style.display, 'none');
  await switchModel(target, 'anima');
  assert.equal(get('animaArtist').value, '@ohako');
  assert.equal(get('promptOut').textContent, '@ohako, best quality, mountain village, sunlight');
  assert.equal(calls.length, 1);
}

async function testAnimaStaleCompositionResponses() {
  for (const change of ['artist', 'output', 'input', 'model']) {
    const target = harness(), {context, get} = target;
    await switchModel(target, 'anima');
    get('promptIn').value = '原始需求'; get('promptOut').textContent = 'original body';
    get('animaArtist').value = '@as109';
    const gate = deferred();
    context.fetch = async () => gate.promise;
    const composing = context.animaArtistChanged(); await flush();
    if (change === 'artist') get('animaArtist').value = '@ohako';
    if (change === 'output') get('promptOut').textContent = 'user edited body';
    if (change === 'input') get('promptIn').value = '后续新需求';
    if (change === 'model') { await switchModel(target, 'krea2'); await switchModel(target, 'anima'); }
    const expected = get('promptOut').textContent;
    gate.resolve({ok: true, json: async () => ({ok: true, data: {artist: '@as109', body: 'original body', prompt: 'stale prefix, original body'}})});
    await composing;
    assert.equal(get('promptOut').textContent, expected, change);
  }
  const target = harness(), {context, get} = target, gates = [];
  await switchModel(target, 'anima'); get('promptOut').textContent = 'same body';
  context.fetch = async () => { const gate = deferred(); gates.push(gate); return gate.promise; };
  get('animaArtist').value = '@as109'; const first = context.animaArtistChanged();
  get('animaArtist').value = '@as109'; const second = context.animaArtistChanged();
  gates[1].resolve({ok: true, json: async () => ({ok: true, data: {artist: '@as109', body: 'same body', prompt: 'latest prefix, same body'}})});
  await second;
  gates[0].resolve({ok: true, json: async () => ({ok: true, data: {artist: '@as109', body: 'same body', prompt: 'older prefix, same body'}})});
  await first;
  assert.equal(get('promptOut').textContent, 'latest prefix, same body');
}

async function testAnimaImmediateRequestsCaptureArtist() {
  const target = harness(), {context, get} = target, calls = [];
  await switchModel(target, 'anima');
  get('promptIn').value = '绘制山间小屋'; get('animaArtist').value = '@yogisya';
  context.fetch = async (url, options) => {
    assert.equal(get('animaArtist').disabled, true);
    calls.push({url, payload: JSON.parse(options.body)});
    return {ok: true, json: async () => ({ok: true, data: {artist: '@yogisya', body: 'cabin, forest',
      prompt: '@yogisya, best quality, cabin, forest', url: '/anima.png', width: 1368, height: 768, seed: 8}})};
  };
  await context.animaPromptGen();
  assert.equal(calls[0].payload.artist, '@yogisya');
  assert.equal(calls[0].payload.text, '绘制山间小屋');
  assert.equal(get('promptOut').textContent, '@yogisya, best quality, cabin, forest');
  assert.equal(get('animaArtist').disabled, false);
  await context.promptImageGen();
  assert.equal(calls[1].payload.artist, '@yogisya');
  assert.equal(calls[1].payload.text, '@yogisya, best quality, cabin, forest');
  assert.equal(calls[1].payload.model, 'anima');
  assert.equal(get('animaArtist').disabled, false);
}

async function testAnimaQueueFreezesArtistAndKeepsLegacyInference() {
  const target = harness(), {context, get} = target, calls = [];
  await switchModel(target, 'anima');
  get('animaArtist').value = '@ohako'; get('promptOut').textContent = '@ohako, cabin, forest'; context.pqAdd();
  get('animaArtist').value = '@as109'; get('promptOut').textContent = '@as109, city, sunlight'; context.pqAdd();
  vm.runInContext('pqItems.push({id:"old-anima",model:"anima",prompt:"@kim hyung tae, old body",checked:true,status:"ready",ratio:"1:1",quality:"1k"})', context);
  await switchModel(target, 'moss'); get('promptOut').textContent = 'Moss中文主题'; context.pqAdd();
  await switchModel(target, 'krea2'); get('promptOut').textContent = 'Krea中文主题'; context.pqAdd();
  const frozen = target.state('pqItems.map(item=>pqRequest(item))');
  assert.equal(frozen[0].artist, '@ohako'); assert.equal(frozen[1].artist, '@as109');
  assert(!Object.hasOwn(frozen[2], 'artist')); assert(!Object.hasOwn(frozen[3], 'artist')); assert(!Object.hasOwn(frozen[4], 'artist'));
  get('animaArtist').value = '@fajyobore';
  context.fetch = async (url, options) => {
    assert.equal(get('animaArtist').disabled, true);
    calls.push({url, payload: JSON.parse(options.body)});
    return {ok: true, text: async () => JSON.stringify({ok: true, data: {url: '/queue.png'}})};
  };
  await context.pqStart();
  assert.deepEqual(calls.slice(0, 2).map(row => row.payload.artist), ['@ohako', '@as109']);
  for (const row of calls.slice(2)) assert(!Object.hasOwn(row.payload, 'artist'));
  assert.equal(get('animaArtist').disabled, false);
  const firstId = target.state('pqItems[0].id');
  context.pqRetry(firstId); await context.pqStart();
  assert.equal(calls.at(-1).payload.artist, '@ohako');
  assert.equal(calls.at(-1).payload.seed, frozen[0].seed);
}

async function testMossPlannerAndLayoutDimensions() {
  const target=harness(), {context,get}=target;
  await switchModel(target,'moss');
  get('promptIn').value='画一张标题“回家”的海报';
  const layout=JSON.stringify({summary:{caption:'回家海报'},items:[{label:'text',text:'回家',caption:'顶部标题',bbox:[-350,-100,-250,100]}],bbox_coord_space:'abs_centralized',bbox_format:'yxyx'});
  const calls=[];
  context.fetch=async(url,options)=>{
    const payload=JSON.parse(options.body);calls.push({url,payload});
    return {ok:true,json:async()=>({ok:true,data:{model:'moss',prompt:layout,format:'layout',base_w:payload.base_w,base_h:payload.base_h}})};
  };
  await context.promptGen();
  assert.equal(calls[0].url,'http://127.0.0.1:8853/api/image/moss/prompt');
  assert.equal(calls[0].payload.system,'GPT Moss策略');
  const spec=context.promptSizeSpec();
  assert.deepEqual([calls[0].payload.base_w,calls[0].payload.base_h],[spec.w,spec.h]);
  assert.equal(get('promptOut').textContent,layout);
  context.pqAdd();assert.equal(target.state('pqItems.length'),1);
  const frozen=target.state('pqItems[0].request');
  await switchModel(target,'krea2');await switchModel(target,'moss');
  get('promptImageRatio').value='1:1';context.pqAdd();
  assert.equal(target.state('pqItems.length'),1);
  await context.mossImageGen();assert.equal(calls.length,1);
  assert(target.messages.some(row=>row.includes('重新生成提示词')));
  assert.deepEqual(target.state('pqItems[0].request'),frozen);
  get('promptOut').textContent='普通中文场景';context.pqAdd();
  assert.equal(target.state('pqItems.length'),2);
  const gate=deferred();context.fetch=async()=>gate.promise;
  get('promptIn').value='新需求';
  const pending=context.mossPromptGen();get('promptIn').value='用户又修改了需求';
  gate.resolve({ok:true,json:async()=>({ok:true,data:{model:'moss',prompt:'迟到结果',format:'caption',base_w:1024,base_h:1024}})});
  await pending;assert.equal(get('promptOut').textContent,'普通中文场景');
  assert.equal(target.state('promptCtrl'),null);
  assert.equal(get('promptModel').disabled,false);
}

async function main() {
  assert.match(source, /<option value="moss">Moss<\/option>/);
  assert.match(source, /Krea2、Moss、Anima各有独立指令词与草稿/);
  await testDraftsAndSizes(); await testModelSpecificGptDrafts(); await testMixedQueue(); await testLegacyAnimaAndRetry(); await testManualMoss(); await testQueueStop();
  await testAnimaTwoPresetsAndLegacyDraftMigration();
  await testAnimaArtistSelectionAndComposition(); await testAnimaStaleCompositionResponses(); await testAnimaImmediateRequestsCaptureArtist(); await testAnimaQueueFreezesArtistAndKeepsLegacyInference();
  await testMossPlannerAndLayoutDimensions();
  await testMossFrozenUpscaleAndLegacyDraft();
  console.log('Prompt UI passed: 13 groups covering Moss 2x/4K frozen sizing and legacy drafts, independent model drafts, planner/layout dimensions/stale responses, Anima names/artists, mixed queues and cancellation. No model or service calls.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
