'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Reuse the existing complete prompt-panel harness without executing its suite
// or changing that test file's public API.
const existing = fs.readFileSync(path.join(__dirname, 'test_moss_prompt_ui.js'), 'utf8');
const harnessModule = {exports: {}};
vm.runInNewContext(existing.slice(0, existing.indexOf('async function switchModel(')) +
  '\nmodule.exports={harness,flush,deferred};',
  {require, module: harnessModule, __dirname, AbortController, console});
const {harness, flush, deferred} = harnessModule.exports;

const markers = ['【GPT长文：Krea2：自然单图】', '【GPT长文：Krea2：精细人设】'];
const controls = ['btnPrompt', 'btnPromptImage', 'promptModel', 'promptImageRatio', 'promptImageQuality'];
const result = (prompt = '长文人物与环境') => ({ok: true,
  data: {model: 'krea2', format: 'caption', prompt}});

function setup(marker = markers[0]) {
  const target = harness();
  target.context.TextDecoder = TextDecoder;
  target.get('promptIn').value = '庭院里的年轻女性，侧脸自然表情';
  target.get('presetSys').value = marker + '\n按自然动作与服装设计展开';
  target.get('promptOut').textContent = '原来已确认的提示词';
  return target;
}

async function testNewRoutesAndValidation() {
  for (const marker of markers) {
    const {context, get, state} = setup(marker);
    let call;
    const gate = deferred();
    context.fetch = async (url, options) => { call = {url, options}; return gate.promise; };
    const pending = context.promptGen();
    await flush();
    assert.equal(call.url, 'http://127.0.0.1:8853/api/image/krea2/prompt');
    assert.equal(call.options.method, 'POST');
    assert.equal(call.options.headers['Content-Type'], 'application/json');
    assert.deepEqual(JSON.parse(call.options.body), {
      text: '庭院里的年轻女性，侧脸自然表情', system: get('presetSys').value});
    assert.equal(get('promptOut').textContent, '原来已确认的提示词');
    controls.forEach(id => assert.equal(get(id).disabled, true));
    gate.resolve({ok: true, json: async () => result()});
    assert.equal(await pending, '长文人物与环境');
    assert.equal(get('promptOut').textContent, '长文人物与环境');
    controls.forEach(id => assert.equal(get(id).disabled, false));
    assert.equal(state('promptCtrl'), null);
  }
  for (const badData of [
    {model: 'moss', format: 'caption', prompt: '错误模型'},
    {model: 'krea2', format: 'layout', prompt: '错误格式'},
    {model: 'krea2', format: 'caption', prompt: ''},
    {model: 'krea2', format: 'caption', prompt: {caption: '错误数据类型'}}
  ]) {
    const {context, get} = setup();
    context.fetch = async () => ({ok: true, json: async () => ({ok: true, data: badData})});
    assert.equal(await context.promptGen(), '');
    assert.equal(get('promptOut').textContent, '原来已确认的提示词');
    assert.match(get('promptImageNote').textContent, /返回格式不一致/);
    controls.forEach(id => assert.equal(get(id).disabled, false));
  }
}

async function testStaleResponsesAndStop() {
  for (const change of ['input', 'system', 'output', 'model', 'abort', 'stop']) {
    const {context, get, state} = setup();
    const gate = deferred();
    let signal;
    context.fetch = async (_url, options) => { signal = options.signal; return gate.promise; };
    const pending = context.promptGen();
    await flush();
    if (change === 'input') get('promptIn').value = '另一个需求';
    if (change === 'system') get('presetSys').value = markers[1] + '\n另一个预设';
    if (change === 'output') get('promptOut').textContent = '用户手动修改后的提示词';
    if (change === 'model') get('promptModel').value = 'anima';
    if (change === 'abort') vm.runInContext('promptCtrl.abort()', context);
    if (change === 'stop') context.promptStop();
    if (change === 'abort' || change === 'stop') assert.equal(signal.aborted, true);
    gate.resolve({ok: true, json: async () => result('迟到结果')});
    assert.equal(await pending, '');
    assert.equal(get('promptOut').textContent,
      change === 'output' ? '用户手动修改后的提示词' : '原来已确认的提示词');
    assert.equal(state('promptCtrl'), null);
    controls.forEach(id => assert.equal(get(id).disabled, false));
  }
  const {context, get} = setup();
  context.fetch = async () => { const error = new Error('Stopped'); error.name = 'AbortError'; throw error; };
  assert.equal(await context.promptGen(), '');
  assert.match(get('promptImageNote').textContent, /已停止/);
  assert.equal(get('promptOut').textContent, '原来已确认的提示词');
}

async function testLegacyAndModelIsolation() {
  const {context, get} = setup();
  const calls = [];
  context.fetch = async (url, options) => {
    calls.push({url, payload: JSON.parse(options.body)});
    let sent = false;
    return {ok: true, body: {getReader: () => ({read: async () => {
      if (sent) return {done: true};
      sent = true;
      return {done: false, value: new TextEncoder().encode('旧版流式结果')};
    }})}};
  };
  get('presetSys').value = '【GPT优化指令词：Krea2】\n旧预设';
  await context.promptGen();
  assert.equal(calls[0].url, '/api/rawchat');
  assert.equal(get('promptOut').textContent, '旧版流式结果');
  assert.equal(calls[0].payload.messages[0].content, '【GPT优化指令词：Krea2】\n旧预设');
  assert.equal(context.isLongKreaPreset('前言\n' + markers[0]), false);
  assert.equal(context.isLongKreaPreset(markers[0] + '额外首行内容'), false);
  assert.equal(context.isLongKreaPreset(' \n' + markers[0] + '\n规则'), true);
  let animaCalls = 0, mossCalls = 0;
  context.animaPromptGen = async () => { animaCalls++; return 'anima'; };
  context.mossPromptGen = async () => { mossCalls++; return 'moss'; };
  get('presetSys').value = markers[0];
  get('promptModel').value = 'anima'; assert.equal(await context.promptGen(), 'anima');
  get('promptModel').value = 'moss'; assert.equal(await context.promptGen(), 'moss');
  assert.equal(animaCalls, 1); assert.equal(mossCalls, 1); assert.equal(calls.length, 1);
  assert.equal(await context.longKreaPromptGen(), '');
  assert.equal(calls.length, 1);
}

async function main() {
  const app = fs.readFileSync(path.resolve(__dirname, '..', '_legacy_8848', 'app.py'), 'utf8');
  assert.match(app, /id="btnPrompt" onclick="promptGen\(\)"/);
  assert.equal((app.match(/async function promptGen\(\)/g) || []).length, 1);
  await testNewRoutesAndValidation();
  await testStaleResponsesAndStop();
  await testLegacyAndModelIsolation();
  console.log('Krea2 long-preset UI passed: new routes, strict response model/format, stale/stop protection, old streaming and model isolation. No model/service calls.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
