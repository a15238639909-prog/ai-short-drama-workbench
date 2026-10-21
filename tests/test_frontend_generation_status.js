/* Actual page functions with deferred in-memory HTTP; no server/files/models. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/pages/story_video.js', 'utf8');
const settings = fs.readFileSync('web/pages/settings.js', 'utf8');
const fixtureSource = fs.readFileSync('tests/test_story_video_reference_ui.js', 'utf8');
const fixtures = vm.createContext({require, console});
vm.runInContext(fixtureSource.slice(0, fixtureSource.indexOf('async function main()')), fixtures);
const flush = async () => { for (let index=0;index<8;index++) await Promise.resolve(); };
const deferred = () => { let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject}; };

function story() {
  const h = vm.runInContext('harness("text")', fixtures), c=h.context;
  Object.assign(c,{storyKey:'',previewKey:'',renderObservations:()=>{},renderLogs:()=>{},renderOutput:()=>{},renderStory:()=>{},textOf:value=>typeof value==='string'?value:''});
  vm.runInContext(source.slice(source.indexOf('    function update(payload)'),source.indexOf('    function poll()')),c);
  vm.runInContext(source.slice(source.indexOf('    function poll()'),source.indexOf('    if(el._storyVideoResetListener)')),c);
  return h;
}

function settingsPoll() {
  const timers=[],requests=[],messages=[];
  let stops=0,reloads=0;
  const c=vm.createContext({st:{storyId:'STORY_SAFE_FIXTURE'},main:{},
    setTimeout:(fn,ms)=>{timers.push({fn,ms});return timers.length;},
    UI:{toast:(message,kind)=>messages.push({message,kind})},
    window:{api:{post:()=>{const request=deferred();requests.push(request);return request.promise;}}},
    load:()=>{reloads++;},busy:{setEta:()=>{},stop:()=>{stops++;}}});
  vm.runInContext(settings.slice(settings.indexOf('    function pollGenDone(busy, doneMsg)'),settings.indexOf('    function makeDoneMsg')),c);
  vm.runInContext('pollGenDone(busy,"SAFE_COMPLETION")',c);
  return {c,timers,requests,messages,stops:()=>stops,reloads:()=>reloads};
}

async function main() {
  const progress=story();
  for (const status of ['failed','cancelled','paused','running']) {
    progress.context.update({task:{id:'safe-'+status,status,done:3,total:3}});
    assert.equal(progress.get('bar').style.width,'99%');
  }
  progress.context.update({task:{id:'safe-complete',status:'completed',done:3,total:3}});
  assert.equal(progress.get('bar').style.width,'100%');

  const start=story(), oldStatus=deferred();
  start.context.task={id:'safe-old',status:'failed'};
  start.context.window.api.get=()=>oldStatus.promise;
  start.context.window.api.post=async()=>({task:{id:'safe-new',status:'running'}});
  start.context.poll();
  await start.get('start').onclick();
  assert.equal(start.context.task.id,'safe-new');
  oldStatus.resolve({task:{id:'safe-old',status:'failed'}});await flush();
  assert.equal(start.context.task.id,'safe-new');

  const action=story(), stale=deferred();
  action.context.task={id:'safe-same',status:'running'};
  action.context.window.api.get=()=>stale.promise;
  action.context.window.api.post=async()=>({task:{id:'safe-same',status:'paused'}});
  action.context.poll();await action.context.action('pause');
  stale.resolve({task:{id:'safe-same',status:'running'}});await flush();
  assert.equal(action.context.task.status,'paused');

  const order=story(), first=deferred(), second=deferred();let calls=0;
  order.context.task={id:'safe-order',status:'running'};
  order.context.window.api.get=()=>calls++===0?first.promise:second.promise;
  order.context.poll();order.context.poll();
  second.resolve({task:{id:'safe-order',status:'paused'}});await flush();
  first.resolve({task:{id:'safe-order',status:'running'}});await flush();
  assert.equal(order.context.task.status,'paused');

  const restore=story(), retryTimers=[];let reads=0;
  restore.context.localStorage.getItem=()=> 'safe-restored';
  restore.context.setTimeout=(fn,ms)=>{retryTimers.push({fn,ms});return 1;};
  restore.context.window.api.get=()=>++reads===1?Promise.reject(new Error('temporary offline')):
    Promise.resolve({task:{id:'safe-restored',status:'paused'}});
  restore.context.poll();await flush();
  assert.equal(retryTimers.length,1);assert.equal(retryTimers[0].ms,5000);
  retryTimers.shift().fn();await flush();assert.equal(restore.context.task.id,'safe-restored');

  const double=story(), submit=deferred();
  double.context.window.api.post=(url,data)=>{double.posts.push({url,data});return submit.promise;};
  const pending=double.get('start').onclick();await double.get('start').onclick();
  assert.equal(double.posts.length,1);submit.resolve({task:{id:'safe-once',status:'running'}});await pending;

  for (const status of ['cancelled','failed','partial','paused','blocked','interrupted']) {
    const h=settingsPoll();h.timers.shift().fn();
    h.requests[0].resolve({running:false,status,err:'',step:'尚未完成'});await flush();
    assert.equal(h.messages.some(row=>row.message==='SAFE_COMPLETION'),false,status);
    assert.equal(h.stops(),1);assert.equal(h.timers.length,0);
  }
  for (const status of ['waiting_resource','waiting_task']) {
    const h=settingsPoll();h.timers.shift().fn();
    assert.equal(h.requests.length,1);assert.equal(h.timers.length,0);
    h.requests[0].resolve({running:false,status,err:'等待恢复'});await flush();
    assert.equal(h.messages.length,0);assert.equal(h.stops(),0);assert.equal(h.timers.length,1);
    h.timers.shift().fn();assert.equal(h.requests.length,2);assert.equal(h.timers.length,0);
    h.requests[1].resolve({running:false,status:'completed'});await flush();
    assert.equal(h.messages[0].message,'SAFE_COMPLETION');assert.equal(h.stops(),1);
  }
  for (const result of [{status:'completed'}, {status:'completed',blocked:[]}, {step:'完成'},
                        {done:3,total:3}, {merged:'safe-film.mp4'}]) {
    const h=settingsPoll();h.timers.shift().fn();h.requests[0].resolve({running:false,...result});await flush();
    assert.equal(h.messages[0].message,'SAFE_COMPLETION');assert.equal(h.stops(),1);
  }
  const unknown=settingsPoll();unknown.timers.shift().fn();unknown.requests[0].resolve({running:false});await flush();
  assert.equal(unknown.messages.some(row=>row.message==='SAFE_COMPLETION'),false);
  const network=settingsPoll();network.timers.shift().fn();network.requests[0].reject(new Error('offline'));await flush();
  assert.equal(network.messages.length,0);assert.equal(network.stops(),0);assert.equal(network.timers.length,1);
  console.log('Frontend generation status passed: progress cap, stale/new-task and action races, ordered reads, startup retry, double-click guard, terminal/waiting distinctions, serial polls and legacy completion.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
