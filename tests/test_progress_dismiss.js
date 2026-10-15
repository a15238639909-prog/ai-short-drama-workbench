/* No browser, network or model calls: exercise the actual progress component. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const stored = new Map();
function load() {
  const bar = {className: '', querySelector: () => ({style: {}})};
  const note = {textContent: ''};
  const close = {hidden: true};
  const elements = {gBar: bar, gNote: note, gDismiss: close};
  const context = vm.createContext({window: {UI: {esc: s => s}},
    document: {getElementById: id => elements[id]},
    localStorage: {getItem: key => stored.get(key) || null, setItem: (key, value) => stored.set(key, value)},
    setTimeout: () => 0});
  vm.runInContext(fs.readFileSync('web/ui_actions.js', 'utf8'), context);
  return {p: context.window.UIActions.progress, bar, note, close};
}

let view = load();
view.p.stuck('失败 · 旧任务', 'project-a:run-1:failed');
assert.equal(view.close.hidden, false);
view.close.onclick();
assert.equal(view.note.textContent, '空闲');
assert.equal(view.close.hidden, true);
for (let i = 0; i < 3; i++) view.p.stuck('失败 · 旧任务', 'project-a:run-1:failed');
assert.equal(view.note.textContent, '空闲', 'polling must not redisplay a dismissed result');
view = load();
view.p.stuck('失败 · 旧任务', 'project-a:run-1:failed');
assert.equal(view.note.textContent, '空闲', 'dismissal survives page refresh');
view.p.stuck('失败 · 旧任务', 'project-a:run-2:failed');
assert.equal(view.close.hidden, false, 'the same error in a new run must remain visible');
view.p.stuck('失败 · 旧任务', 'project-b:run-1:failed');
assert.equal(view.close.hidden, false, 'another project is independent');
view.p.run(0.5, '正在生成');
assert.equal(view.note.textContent, '正在生成');
assert.equal(view.close.hidden, true, 'running tasks cannot be dismissed as terminal notices');
assert.equal(view.bar.className, 'pbar run');
console.log('Progress dismissal: polling, refresh, new run, other project and running-state checks passed.');
