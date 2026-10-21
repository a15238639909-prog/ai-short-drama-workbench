/* Exercise actual reset, HTTP response barriers and page listeners; no real server/models. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=path=>fs.readFileSync(path,'utf8');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
const eventClass=class{constructor(type,options){this.type=type;this.detail=options.detail;}};
function eventBus(){const listeners={};return{addEventListener:(name,fn)=>(listeners[name]??=[]).push(fn),removeEventListener:()=>{},dispatchEvent:e=>(listeners[e.type]||[]).forEach(fn=>fn(e))};}

async function shellReset(){
 const pending=deferred(),posts=[],events=[],toasts=[],saved=new Map([['storyVideoDraft','keep'],['motionDraft','keep'],['v43_story','STORY_034']]);
 const elements={};for(const id of ['btnGame','cfgFree','btnTasks','taskbar','gCancel'])elements[id]={style:{},classList:{toggle:()=>{}},setAttribute:()=>{},textContent:'old',innerHTML:'old'};
 let resets=0;
 const win={dispatchEvent:e=>events.push(e),__sagaBusy:true,__srvBusy:true,api:{post:(url,body)=>{posts.push({url,body});return pending.promise;}},UIActions:{progress:{reset:()=>resets++}}};
 const context=vm.createContext({window:win,UIActions:win.UIActions,UI:{toast:(...args)=>toasts.push(args)},
  document:{getElementById:id=>elements[id]},CustomEvent:eventClass,
  localStorage:{getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v)},
  sagaRunningId:'old',sagaSweep:4,updateTaskCount:()=>{},loadGpu:()=>{},loadTasks:()=>{},loadSagaProgress:()=>{}});
 const text=source('web/shell.js');vm.runInContext(text.slice(text.indexOf('  var logFilter'),text.indexOf('  function route()')),context);
 const first=context.clearCache(),second=context.clearCache();assert.equal(first,second);
 await Promise.resolve();assert.equal(posts.length,1);assert.equal(posts[0].url,'/api/gpu/clear');assert.equal(elements.btnGame.disabled,true);
 assert.equal(events[0].detail.phase,'begin');pending.resolve({paused:false,cancelled:3});await first;
 assert.equal(context.runtimeTaskCount,0);assert.equal(context.sagaProgress,null);assert.equal(win.__sagaBusy,false);
 assert.equal(elements.taskbar.style.display,'none');assert.equal(elements.gCancel.hidden,true);assert.equal(elements.btnGame.disabled,false);assert.equal(resets,1);
 assert.equal(events.at(-1).detail.phase,'done');assert.equal(saved.get('storyVideoDraft'),'keep');assert.equal(saved.get('motionDraft'),'keep');assert.equal(saved.get('v43_story'),'STORY_034');
 assert.equal(context.runtimeCurrent(0),false,'old poll epoch is invalid');
 const oldResets=resets;win.api.post=async()=>{throw Error('无法移入回收站');};await context.clearCache();
 assert.equal(resets,oldResets,'failed cleanup must not claim success');assert.equal(events.at(-1).detail.phase,'failed');assert.match(toasts.at(-1)[0],/清理未完成/);
}

async function responseBarrier(){
 const bus=eventBus(),pending=deferred(),timeouts=[];let calls=0;
 const win={...bus};
 const context=vm.createContext({window:win,setTimeout:fn=>timeouts.push(fn),fetch:()=>{calls++;return pending.promise;}});
 vm.runInContext(source('web/api.js'),context);
 let downstream=false;const old=win.api.post('/api/produce',{}).then(()=>{downstream=true;});
 bus.dispatchEvent(new eventClass('workbench-runtime-reset',{detail:{phase:'begin'}}));
 pending.resolve({json:async()=>({ok:true,data:{started:true}})});
 await assert.rejects(old,error=>error.code==='RUNTIME_CLEARED');assert.equal(downstream,false);assert.equal(calls,1);
 bus.dispatchEvent(new eventClass('workbench-runtime-reset',{detail:{phase:'done'}}));
 context.fetch=async()=>({json:async()=>({ok:true,data:{running:true}})});
 assert.equal((await win.api.post('/api/motion/start',{})).running,true);
 context.fetch=async()=>({json:async()=>({ok:true,data:{job:{cleared_at:123,running:true,percent:100}}})});
 await assert.rejects(win.api.post('/api/timeline/STORY_034/progress',{}),error=>error.code==='RUNTIME_CLEARED');
}

async function lateProgress(){
 const bus=eventBus(),pending=deferred(),progress={style:{}},elements={gBar:{querySelector:()=>progress},gNote:{},gDismiss:{},logpanel:{style:{}}};
 const win={...bus,UI:{esc:value=>value}};
 const context=vm.createContext({window:win,document:{getElementById:id=>elements[id]},
  localStorage:{getItem:()=>null},setInterval:()=>17,clearInterval:()=>{},setTimeout:()=>18});
 vm.runInContext(source('web/ui_actions.js'),context);
 const p=win.UIActions.progress,button={innerHTML:'生成',disabled:false};
 const operation=p.wrap('旧任务',pending.promise);const busy=win.UIActions.busy(button,'生成',pending.promise);
 assert.equal(button.disabled,true);
 bus.dispatchEvent(new eventClass('workbench-runtime-reset',{detail:{phase:'begin'}}));p.reset();
 assert.equal(button.disabled,false);assert.equal(button.innerHTML,'生成');
 pending.resolve('late');await operation;await busy;
 assert.equal(elements.gNote.textContent,'空闲','late promise must not restore completion status');
 assert.equal(progress.style.width,'0%');
}

function pageReset(path,prefix,end){
 const code=source(path),bus=eventBus(),items={},saved=new Map([['storyVideoTask','old']]);
 const get=name=>(items[name]??={value:'keep '+name,style:{},textContent:'keep',hidden:false});
 const context=vm.createContext({window:bus,el:{},active:()=>true,get,clearTimeout:()=>{},timer:123,sync:()=>{},
  runtimeEpoch:0,clearing:false,pending:false,busy:true,task:{id:'old'},displayedTask:'old',
  featureQueue:[{key:'scene'}],featureRunning:true,featureVersions:{character:1,character2:2,scene:3},
  draft:{story_direction:'保留故事',opening_image:'keep.png'},saveDraft:()=>{},
  localStorage:{setItem:(k,v)=>saved.set(k,v)}});
 const begin=code.indexOf(prefix),finish=code.indexOf(end,begin);assert(begin>=0&&finish>begin);
 vm.runInContext(code.slice(begin,finish),context);
 bus.dispatchEvent(new eventClass('workbench-runtime-reset',{detail:{phase:'begin'}}));
 assert.equal(context.runtimeEpoch,1);assert.equal(context.clearing,true);
 if(path.includes('motion')){assert.equal(context.featureQueue.length,0);assert.equal(context.featureRunning,false);assert.equal(context.featureVersions.scene,4);}
 bus.dispatchEvent(new eventClass('workbench-runtime-reset',{detail:{phase:'done'}}));
 assert.equal(context.clearing,false);assert.equal(context.task,null);assert.match(get('status').textContent,/空闲/);
 if(path.includes('story_video')){assert.equal(saved.get('storyVideoTask'),'');assert.equal(context.draft.opening_image,'keep.png');assert.equal(context.draft.story_direction,'保留故事');assert.equal(get('bar').style.width,'0%');}
}

(async()=>{
 await shellReset();await responseBarrier();await lateProgress();
 pageReset('web/pages/motion.js','    if(el._motionResetListener)','    sync();poll();');
 pageReset('web/pages/story_video.js','    if(el._storyVideoResetListener)','    if (el._storyVideoTaskListener)');
 console.log('Runtime reset UI passed: one request, idle state, failure visibility, stale HTTP rejection, no follow-up generation, motion queue invalidation and story draft preservation.');
})().catch(error=>{console.error(error);process.exitCode=1;});
