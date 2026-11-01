/* Actual timeline functions in VM. No server, user projects, models or media. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/pages/timeline.js', 'utf8');
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject}; };
const flush = async () => { for (let n=0;n<12;n++) await Promise.resolve(); };

async function saveWhileTyping() {
  const row={no:4,prompt:'原自动稿'}, sc={no:2,segments:[row]}, posts=[];
  const editor={value:'第一次编辑',getAttribute:()=> '4'}, status={innerHTML:''};
  const nodes={pText:editor,pSave:status};
  const c=vm.createContext({ST:{storyId:'SAFE_NORMAL_UI'},EP:2,seg:0,
    promptDirty:true,lastSave:{ok:true,at:0,err:''},savingNow:null,
    id:key=>nodes[key]||null,scene:()=>sc,segsOf:value=>value.segments,
    fmtClock:()=> 'safe-clock',esc:String,Date,
    window:{UI:{toast:()=>{}},api:{post:(url,body)=>{const pending=deferred();posts.push({url,body,pending});return pending.promise;}}}});
  vm.runInContext(source.slice(source.indexOf('  function savePrompt('),source.indexOf('  var _gen1Fired')),c);
  const first=c.savePrompt(sc,0,editor,status);
  editor.value='第二次编辑：最新的正常文字'; c.promptDirty=true;
  const beforeGeneration=c.flushPromptSave();
  posts[0].pending.resolve({}); await flush();
  assert.equal(posts.length,2,'Generation flush must persist newer editor text typed during a prior save');
  assert.equal(posts[1].body.prompt,editor.value);
  editor.value='第三次编辑：生成前的最终稿'; c.promptDirty=true;
  posts[1].pending.resolve({}); await flush();
  assert.equal(posts.length,3,'Flush must wait for edits that arrive while the newer save is pending too');
  assert.equal(posts[2].body.prompt,editor.value);
  posts[2].pending.resolve({}); await first; await beforeGeneration;
  assert.equal(row.prompt,editor.value);
  assert.equal(c.promptDirty,false);
}

async function queuedSaveKeepsOriginalProjectAndEpisode() {
  const row={no:4,prompt:'原自动稿'}, sc={no:2,segments:[row]}, posts=[];
  const editor={value:'第一次编辑',getAttribute:()=> '4'}, status={innerHTML:''};
  let currentScene=sc;
  const nodes={pText:editor,pSave:status};
  const c=vm.createContext({ST:{storyId:'SAFE_NORMAL_UI'},EP:2,seg:0,
    promptDirty:true,lastSave:{ok:true,at:0,err:''},savingNow:null,
    id:key=>nodes[key]||null,scene:()=>currentScene,segsOf:value=>value.segments,
    fmtClock:()=> 'safe-clock',esc:String,Date,
    window:{UI:{toast:()=>{}},api:{post:(url,body)=>{const pending=deferred();posts.push({url,body,pending});return pending.promise;}}}});
  vm.runInContext(source.slice(source.indexOf('  function savePrompt('),source.indexOf('  var _gen1Fired')),c);
  const first=c.savePrompt(sc,0,editor,status);
  editor.value='排队中的第二次编辑'; c.promptDirty=true;
  const second=c.savePrompt(sc,0,editor,status);
  assert.equal(posts.length,1,'Prompt writes must be serialized');
  c.ST={storyId:'SAFE_OTHER_UI'};c.EP=3;
  currentScene={no:1,segments:[{no:1,prompt:'另一个项目原文'}]};
  nodes.pText={value:'另一个项目未保存的文字'};c.promptDirty=true;
  posts[0].pending.resolve({}); await flush();
  assert.equal(posts.length,2);
  assert.equal(posts[1].url,'/api/timeline/SAFE_NORMAL_UI/segment-save');
  assert.equal(posts[1].body.ep,2);
  assert.equal(posts[1].body.no,4);
  posts[1].pending.resolve({});await first;await second;
  assert.equal(c.promptDirty,true,'An old project save must not mark a new project editor clean');
  assert.equal(nodes.pText.value,'另一个项目未保存的文字');
}

async function actualSegmentNumberAndEpisode() {
  const posts=[], row={no:4,prompt:'成年修表师放下工具。'};
  const sc={no:2,segments:[row]}, editor={value:'用户新写的第4段正常提示词',getAttribute:()=> '4'};
  const c=vm.createContext({ST:{storyId:'SAFE_NORMAL_UI'},EP:2,cur:2,seg:0,
    D:{timeline:{scenes:[sc]}},scene:()=>sc,segsOf:value=>value.segments,
    savingNow:null,flushPromptSave:()=>Promise.resolve(true),
    document:{getElementById:()=>editor},reload:()=>Promise.resolve(),
    window:{api:{post:async(url,body)=>{posts.push({url,body});return {}; }},
      UIActions:{progress:{wrap:(label,promise)=>promise}}}});
  vm.runInContext(source.slice(source.indexOf('  function genSeg('),source.indexOf('  /* ---------- 出片前预览')),c);
  await c.genSeg({},false);
  assert.equal(posts.length,1);
  assert.equal(posts[0].body.seg_no,4,'Scene-local array index must not replace saved global segment no');
  assert.equal(posts[0].body.ep,2,'Selected episode must be explicit on the single-segment request');
  assert.equal(posts[0].body.prompt,editor.value,'The editor belongs to segment 4 and must be sent');
}

function combinedGenerationHarness() {
  const rows=[{no:3,prompt:'第3段原词'},{no:4,prompt:'第4段原词'}];
  const sc={no:2,segments:rows}, posts=[];
  const editor={value:'第4段初次编辑',segment:'4',getAttribute(){return this.segment;}}, status={innerHTML:''};
  const nodes={pText:editor,pSave:status};
  const c=vm.createContext({ST:{storyId:'SAFE_NORMAL_UI'},EP:2,cur:2,seg:1,
    promptDirty:true,lastSave:{ok:true,at:0,err:''},savingNow:null,
    id:key=>nodes[key]||null,scene:()=>sc,segsOf:value=>value.segments,
    document:{getElementById:key=>nodes[key]||null},
    fmtClock:()=> 'safe-clock',esc:String,Date,reload:()=>Promise.resolve(),
    window:{UI:{toast:()=>{}},UIActions:{progress:{wrap:(label,promise)=>promise}},api:{post:(url,body)=>{
      const pending=deferred();posts.push({url,body,pending});
      if(url.endsWith('/generate-segment'))pending.resolve({});
      return pending.promise;
    }}}});
  vm.runInContext(source.slice(source.indexOf('  function savePrompt('),source.indexOf('  var _gen1Fired')),c);
  vm.runInContext(source.slice(source.indexOf('  function genSeg('),source.indexOf('  /* ---------- 出片前预览')),c);
  return {c,sc,rows,posts,editor,status};
}

async function singleGenerationWaitsForLatestSavedText() {
  const h=combinedGenerationHarness(), {c,sc,posts,editor,status}=h;
  const first=c.savePrompt(sc,1,editor,status);
  editor.value='第4段最终编辑稿';c.promptDirty=true;
  const generation=c.genSeg({},false);
  await flush();
  assert.equal(posts.length,1,'No generation request may overtake the blur save');
  posts[0].pending.resolve({});await flush();
  assert.equal(posts.length,2);
  assert(posts[1].url.endsWith('/segment-save'));
  assert.equal(posts[1].body.prompt,editor.value);
  assert.equal(posts.some(p=>p.url.endsWith('/generate-segment')),false);
  posts[1].pending.resolve({});await first;await generation;
  assert.equal(posts.length,3);
  assert(posts[2].url.endsWith('/generate-segment'));
  assert.equal(posts[2].body.prompt,editor.value);
  assert.equal(posts[2].body.seg_no,4);
  assert.equal(h.rows[1].prompt,editor.value);
}

async function cardGenerationWaitsWithoutSavingTheOldEditorIntoNewTarget() {
  const h=combinedGenerationHarness(), {c,sc,posts,editor,status}=h;
  c.seg=0;editor.segment='3';editor.value='第3段第一次编辑';
  const first=c.savePrompt(sc,0,editor,status);
  editor.value='第3段排队的第二次编辑';c.promptDirty=true;
  const second=c.savePrompt(sc,0,editor,status);
  c.seg=1;
  const generation=c.genSeg({},false);await flush();
  assert.equal(posts.length,1);
  posts[0].pending.resolve({});await flush();
  assert.equal(posts.length,2);
  assert(posts[1].url.endsWith('/segment-save'));
  assert.equal(posts[1].body.no,3,'Queued old-editor writes retain their original segment');
  assert.equal(posts.some(p=>p.url.endsWith('/generate-segment')),false);
  posts[1].pending.resolve({});await first;await second;await generation;
  const submits=posts.filter(p=>p.url.endsWith('/generate-segment'));
  assert.equal(submits.length,1);
  assert.equal(submits[0].body.seg_no,4);
  assert.equal(submits[0].body.prompt,'','The old segment 3 editor must not be sent for segment 4');
  assert.equal(h.rows[1].prompt,'第4段原词');
}

async function targetSwitchDuringSaveCancelsTheOldGeneration() {
  for(const kind of ['project','episode','scene','segment']) {
    const h=combinedGenerationHarness(), {c,sc,posts,editor,status}=h;
    const saving=c.savePrompt(sc,1,editor,status);
    const outcome=c.genSeg({},false).then(()=>({ok:true}),error=>({error}));
    await flush();
    if(kind==='project')c.ST={storyId:'SAFE_OTHER_UI'};
    else if(kind==='episode')c.EP=3;
    else if(kind==='scene')c.cur=3;
    else c.seg=0;
    posts[0].pending.resolve({});await saving;
    const result=await outcome;
    assert(result.error,'Changing '+kind+' during save must cancel the old generation');
    assert.equal(posts.some(p=>p.url.endsWith('/generate-segment')),false,kind);
  }
}

async function main() {
  const failures=[];
  for (const [name,test] of [['saveWhileTyping',saveWhileTyping],
    ['queuedSaveKeepsOriginalProjectAndEpisode',queuedSaveKeepsOriginalProjectAndEpisode],
    ['actualSegmentNumberAndEpisode',actualSegmentNumberAndEpisode],
    ['singleGenerationWaitsForLatestSavedText',singleGenerationWaitsForLatestSavedText],
    ['cardGenerationWaitsWithoutSavingTheOldEditorIntoNewTarget',cardGenerationWaitsWithoutSavingTheOldEditorIntoNewTarget],
    ['targetSwitchDuringSaveCancelsTheOldGeneration',targetSwitchDuringSaveCancelsTheOldGeneration]]) {
    try { await test(); console.log('PASS',name); }
    catch(error){ failures.push({name,error:error.message});console.error('FAIL',name,error.message); }
  }
  if(failures.length) process.exitCode=1;
  else console.log('Timeline UI save/segment contracts passed without models or media.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
