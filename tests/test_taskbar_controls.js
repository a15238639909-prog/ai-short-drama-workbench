/* Actual taskbar handlers with in-memory HTTP/DOM doubles; no models or real cancellations. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/shell.js', 'utf8');
const handlers = source.slice(source.indexOf('  function loadTasks()'), source.indexOf('  function loadLogs()'));

function harness(status, reject = false, kind = 'story_video') {
  const task = {id:'task_displayed',kind,status,title:'本地故事视频',done:1,total:4};
  if(kind==='long_comic')task.project_id='LC_project_target';
  const buttons = new Map(), posts = [], toasts = [], events = [], storage = new Map();
  const bar = {style:{}, querySelectorAll:()=>Array.from(buttons.values())};
  Object.defineProperty(bar, 'innerHTML', {set(html) {
    buttons.clear();
    for (const match of html.matchAll(/id="(tb\w+)"/g)) buttons.set(match[1], {disabled:false});
  }});
  const context = vm.createContext({runtimeTaskCount:0,taskActionPending:'',clearingRuntime:false,runtimeEpoch:0,runtimeCurrent:()=>true,
    UI:{esc:x=>x,badge:x=>x,toast:(...args)=>toasts.push(args)},
    window:{dispatchEvent:event=>events.push(event),api:{
      get:async()=>({active:task.status==='cancelled'?[]:[task]}),
      post:async(url,body)=>{
        posts.push({url,body});
        if(reject)throw new Error('模拟接口失败');
        if(body.action==='cancel')task.status='cancelled';
        return task;
      }}},
    document:{getElementById:id=>id==='taskbar'?bar:buttons.get(id)||null},
    localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)},
    location:{hash:'#story-video'},route:()=>{},updateTaskCount:()=>{},loadLogs:()=>{},
    CustomEvent:class {constructor(type,options){this.type=type;this.detail=options.detail;}}
  });
  vm.runInContext(handlers, context);
  return {context,task,bar,buttons,posts,toasts,events,storage};
}

(async()=>{
  const waiting=harness('waiting_user');
  await waiting.context.loadTasks();
  assert.equal(waiting.buttons.has('tbPause'),false);
  assert.equal(typeof waiting.buttons.get('tbCancel').onclick,'function');
  await waiting.buttons.get('tbCancel').onclick();
  assert.equal(waiting.posts[0].url,'/api/story-video/action');
  assert.equal(waiting.posts[0].body.id,'task_displayed');
  assert.equal(waiting.posts[0].body.action,'cancel');
  assert.equal(waiting.task.status,'cancelled');
  assert.equal(waiting.bar.style.display,'none');
  assert.equal(waiting.events[0].detail.id,'task_displayed');

  const paused=harness('paused');
  await paused.context.loadTasks();
  assert.equal(typeof paused.buttons.get('tbResume').onclick,'function');
  assert.equal(typeof paused.buttons.get('tbCancel').onclick,'function','missing pause control must not break cancellation');

  const failed=harness('waiting_user',true);
  await failed.context.loadTasks();
  await failed.buttons.get('tbCancel').onclick();
  assert.match(failed.toasts[0][0],/任务操作失败/);
  assert.equal(failed.buttons.get('tbCancel').disabled,false);

  const view=harness('waiting_user');
  view.storage.set('storyVideoDraft',JSON.stringify({new_task:true}));
  await view.context.loadTasks();
  view.buttons.get('tbViewStory').onclick();
  assert.equal(view.storage.get('storyVideoTask'),'task_displayed');
  assert.equal(JSON.parse(view.storage.get('storyVideoDraft')).new_task,false);
  const comic=harness('running',false,'long_comic');
  await comic.context.loadTasks();
  await comic.buttons.get('tbPause').onclick();
  assert.equal(comic.posts[0].url,'/api/long-comic/project/LC_project_target/action');
  assert.equal(JSON.stringify(comic.posts[0].body),JSON.stringify({action:'pause'}));
  await comic.buttons.get('tbCancel').onclick();
  assert.equal(comic.posts[1].url,'/api/long-comic/project/LC_project_target/action');
  assert.equal(JSON.stringify(comic.posts[1].body),JSON.stringify({action:'cancel'}));
  assert.equal(comic.bar.style.display,'none');
  console.log('Taskbar checks passed: waiting cancellation, displayed task targeting, paused controls, visible errors, returning to a waiting story, and long-comic pause/cancel routing.');
})().catch(error=>{console.error(error);process.exitCode=1;});
