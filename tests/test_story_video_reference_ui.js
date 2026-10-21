/* Real page handlers in a small DOM harness; no server, upload or model calls. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/pages/story_video.js', 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));

class Element {
  constructor(value='') { this.value=value;this.checked=true;this.style={};this.hidden=false;this.disabled=false;this.textContent='';this.dataset={};this.attributes={};this.children=[];this.parentElement={hidden:false};this.listeners={}; }
  setAttribute(key,value){this.attributes[key]=value;}
  querySelectorAll(){return this.children;}
  closest(){return this.card||(this.card={hidden:false,style:{}});}
  replaceChildren(){this.children=[];}
  appendChild(child){this.children.push(child);}
  prepend(child){this.children.unshift(child);}
  focus(){this.focused=true;}
  addEventListener(name,callback){this.listeners[name]=callback;}
}

function harness(mode='reference', referenceDetailSupported=true) {
  const nodes={},saved=new Map(),posts=[],xhrs=[],listeners={};
  const get=name=>(nodes[name]||(nodes[name]=new Element()));
  Object.assign(get('mode'),{value:mode});
  get('direction').value='完整故事与逐句台词';get('count').value='3';
  get('style').value='电影写实';get('ratio').value='16:9';get('story-input').value='story';
  get('audio-review').checked=false;
  get('mode-tabs').children=['text','image','reference'].map(value=>{const node=new Element();node.dataset.mode=value;return node;});
  class XHR {
    constructor(){this.upload={};this.headers={};xhrs.push(this);}
    open(method,url){this.method=method;this.url=url;}
    setRequestHeader(key,value){this.headers[key]=value;}
    send(file){this.file=file;}
    finish(status,result){this.status=status;this.responseText=JSON.stringify(result);this.onload();this.onloadend();}
  }
  const context=vm.createContext({get,el:{},draft:{character_cards:[]},task:null,pending:false,uploading:false,
    clearing:false,runtimeEpoch:0,referenceSupported:true,ratioSupported:true,referenceDetailSupported,referenceDetailChecked:referenceDetailSupported,
    DEFAULT_SEGMENT_SECONDS:15,MAX_CHARACTERS:7,VOICES:['甜美','温柔','清朗','沉稳','活泼','冷静','沙哑'],
    localStorage:{setItem:(key,value)=>saved.set(key,value)},XMLHttpRequest:XHR,
    document:{createElement:()=>new Element()},window:{api:{post:async(url,data)=>{posts.push({url,data:plain(data)});return {task:{id:'new'}};}},
      addEventListener:(name,callback)=>{listeners[name]=callback;},removeEventListener:()=>{}},
    active:()=>true,esc:value=>String(value??''),clearTimeout:()=>{},setTimeout:()=>1,timer:null,
    update:()=>{},outputKey:'old',displayedTask:'old',poll:()=>{}});
  vm.runInContext(source.slice(source.indexOf('    function mediaUrl'),source.indexOf('    function textOf')),context);
  context.renderReferences=()=>{context.referencesRendered=(context.referencesRendered||0)+1;};
  vm.runInContext(source.slice(source.indexOf("    get('start').onclick"),source.indexOf("    ['pause','resume','cancel','retry','approve','rewrite','redo'].forEach")),context);
  return {context,get,nodes,saved,posts,xhrs,listeners};
}

function card(id='char_01',name='阿洛'){return {id,image_path:'D:/AI视频工作台/outputs/people/'+id+'.png',name,personality:'好奇，愿意帮助同伴',voice:'清朗自然的声线'};}

function testSourceStory(){
  const h=harness(),c=h.context;
  c.task={id:'story-ready',status:'waiting_user',inputs:{input_mode:'reference',quality_mode:'guided',flow_version:3}};
  Object.assign(c,{storyKey:'',segmentRows:[],showSegment:()=>{}});
  c.document.createElement=tag=>{const node=new Element();node.tagName=tag;return node;};
  const literal='<img src=x onerror="globalThis.__storyExecuted=true">\n第二段正文';
  Object.defineProperty(h.get('story'),'innerHTML',{get(){return this.markup||'';},set(value){assert(!value.includes(literal));this.markup=value;this.children=[];}});
  vm.runInContext(source.slice(source.indexOf('    function renderStory(payload)'),source.indexOf('    function renderObservations(value)')),c);
  c.renderStory({source_story:literal,decision:'story'});
  assert.equal(h.get('story-card').hidden,false);assert.equal(h.get('approve').hidden,false);
  const details=h.get('story').children[0];assert.equal(details.tagName,'details');
  assert.equal(details.children[0].textContent,'完整故事正文');assert.equal(details.children[1].tagName,'pre');
  assert.equal(details.children[1].textContent,literal);assert.match(details.children[1].style.cssText,/white-space:pre-wrap/);
  assert.equal(c.__storyExecuted,undefined);
  const firstKey=c.storyKey;c.renderStory({source_story:'更新后的完整故事',decision:'story'});
  assert.notEqual(c.storyKey,firstKey);assert.equal(h.get('story').children[0].children[1].textContent,'更新后的完整故事');
  c.task.inputs.input_mode='image';c.renderStory({source_story:literal,plan:{title:'旧任务',logline:'旧故事',beats:[],ending:'结束'}});
  assert.equal(h.get('story').children.length,0);assert.match(h.get('story').innerHTML,/旧任务/);
  console.log('Story source display passed: reference prose visible before plan, textContent literal HTML, wrapping, cache refresh and old-mode compatibility.');
}

async function main(){
  testSourceStory();
  const loading=harness('reference',false),lc=loading.context;
  lc.draft.character_cards=[card()];loading.get('reference-image-size').value='max';
  let receiveCapabilities;
  lc.window.api.get=()=>new Promise(resolve=>{receiveCapabilities=resolve;});
  const capabilitiesStart=source.indexOf("    window.api.get('/api/story-video/capabilities')");
  vm.runInContext(source.slice(capabilitiesStart,source.indexOf('\n  }};',capabilitiesStart)),lc);
  lc.sync();assert.equal(loading.get('reference-size-max').disabled,true);
  assert.equal(loading.get('reference-image-size').disabled,false);
  assert.match(loading.get('reference-detail-note').textContent,/正在读取高清参考支持/);
  await loading.get('start').onclick();assert.equal(loading.posts.length,0);
  assert.equal(loading.get('reference-image-size').value,'max');assert.match(loading.get('status').textContent,/尚未确认高清参考支持/);
  receiveCapabilities({reference_mode:true,aspect_ratios:['16:9']});await Promise.resolve();
  assert.equal(loading.get('reference-size-max').disabled,true);
  assert.match(loading.get('reference-detail-note').textContent,/高清参考需重启工作台后使用/);
  await loading.get('start').onclick();assert.equal(loading.posts.length,0);
  assert.equal(loading.get('reference-image-size').value,'max');assert.match(loading.get('status').textContent,/高清参考需重启工作台后使用/);
  loading.get('reference-image-size').value='match';
  await loading.get('start').onclick();assert.equal(loading.posts.length,1);assert.equal(loading.posts[0].data.reference_image_size,'match');

  const supported=harness('reference',false),sc=supported.context;
  sc.draft.character_cards=[card()];supported.get('reference-image-size').value='max';
  sc.window.api.get=()=>Promise.resolve({reference_mode:true,reference_image_sizes:['match','max']});
  vm.runInContext(source.slice(capabilitiesStart,source.indexOf('\n  }};',capabilitiesStart)),sc);await Promise.resolve();
  assert.equal(supported.get('reference-size-max').disabled,false);
  assert.doesNotMatch(supported.get('reference-detail-note').textContent,/需重启/);
  assert.match(supported.get('reference-detail-note').textContent,/不能保证/);
  await supported.get('start').onclick();assert.equal(supported.posts.length,1);assert.equal(supported.posts[0].data.reference_image_size,'max');
  const h=harness(),c=h.context;
  c.draft.character_cards=[card(),card('char_03','米娅')];c.draft.initial_scene='D:/AI视频工作台/outputs/scene.png';
  const payload=plain(c.saveDraft());
  assert.deepEqual(payload.character_cards,[card(),card('char_03','米娅')]);
  assert.equal(payload.input_mode,'reference');assert.equal(payload.opening_image,'');assert.equal(payload.story_input,'story');
  assert.equal(payload.initial_scene,c.draft.initial_scene);assert.equal(payload.segment_seconds,15);
  assert.equal(payload.reference_image_size,'match');
  h.get('reference-image-size').value='max';
  assert.equal(c.saveDraft().reference_image_size,'max');
  assert.equal(JSON.parse(h.saved.get('storyVideoDraft')).reference_image_size,'max');
  h.get('reference-image-size').value='unsupported';assert.equal(c.saveDraft().reference_image_size,'match');
  assert.equal(payload.audio_review_enabled,false);assert.equal(c.draft.audio_review_enabled,false);
  h.get('audio-review').checked=true;
  assert.equal(c.saveDraft().audio_review_enabled,true);
  assert.equal(JSON.parse(h.saved.get('storyVideoDraft')).audio_review_enabled,true);
  h.get('story-input').value='idea';assert.equal(c.saveDraft().story_input,'idea');
  c.sync();assert.equal(h.get('reference-card').hidden,false);assert.equal(h.get('opening-card').hidden,true);
  assert.equal(h.get('reference-detail-control').hidden,false);
  assert.match(h.get('quality-note').textContent,/每段生成一次，无自动补拍/);assert.equal(h.get('ratio-control').hidden,false);
  assert.match(h.get('quality-note').textContent,/先有限修复/);assert.match(h.get('quality-note').textContent,/审稿、观察报告不可用、未通过时，记录提醒后继续/);
  assert.match(h.get('audio-review-note').textContent,/增加耗时/);assert.match(h.get('audio-review-note').textContent,/声音按原设置生成/);
  assert.match(h.get('quality-note').textContent,/生成服务不可用时仍会停止/);
  assert.equal(h.get('mode-tabs').children[2].attributes['aria-selected'],'true');

  h.get('mode').value='image';c.draft.opening_image='D:/AI视频工作台/outputs/opening.png';
  const image=plain(c.saveDraft());assert.equal(image.opening_image,c.draft.opening_image);assert(!('character_cards' in image));
  assert(!('reference_image_size' in image));
  assert.equal(image.audio_review_enabled,true);
  c.sync();assert.equal(h.get('ratio-control').hidden,true);assert.equal(h.get('reference-card').hidden,true);
  assert.equal(h.get('reference-detail-control').hidden,true);
  assert.match(h.get('duration').textContent,/每段生成一次/);assert.doesNotMatch(h.get('duration').textContent,/有限补拍/);
  assert.match(h.get('quality-note').textContent,/先有限修复/);assert.match(h.get('quality-note').textContent,/不因质量建议中断或自动补拍/);
  assert(!h.get('quality-note').textContent.includes('未通过则停止'));
  h.get('mode').value='text';const text=plain(c.saveDraft());assert.equal(text.opening_image,'');assert(!('initial_scene' in text));
  assert(!('reference_image_size' in text));
  assert.equal(text.audio_review_enabled,true);
  h.get('audio-review').checked=false;c.saveDraft();
  c.sync();assert.equal(h.get('opening-card').hidden,true);
  assert.equal(h.get('reference-detail-control').hidden,true);
  assert.match(h.get('audio-review-note').textContent,/跳过语音识别核对/);assert.doesNotMatch(h.get('audio-review-note').textContent,/未通过|失败/);
  assert.match(h.get('quality-note').textContent,/有提醒也继续生成/);

  const adding=harness();adding.context.draft.character_cards=[card('char_05')];
  adding.context.addCharacter();assert.equal(adding.context.draft.character_cards[1].id,'char_06');
  adding.context.draft.character_cards.splice(0,1);adding.context.addCharacter();assert.equal(adding.context.draft.character_cards[1].id,'char_07');
  for(let i=0;i<12;i++)adding.context.addCharacter();assert.equal(adding.context.draft.character_cards.length,7);
  adding.context.sync();assert.equal(adding.get('add-character').disabled,true);

  const validation=harness();
  await validation.get('start').onclick();assert.equal(validation.posts.length,0);assert.match(validation.get('status').textContent,/添加/);
  validation.context.draft.character_cards=[card()];validation.context.draft.character_cards[0].voice='clear';
  await validation.get('start').onclick();assert.equal(validation.posts.length,0);assert.match(validation.get('status').textContent,/中文/);
  validation.context.draft.character_cards=[card(),card('char_02')];
  await validation.get('start').onclick();assert.equal(validation.posts.length,0);assert.match(validation.get('status').textContent,/不同的名字/);
  validation.context.draft.character_cards=[card(),card('char_02','露娜')];validation.get('story-input').value='idea';
  validation.get('reference-image-size').value='max';
  await validation.get('start').onclick();assert.equal(validation.posts.length,1);
  assert.equal(validation.posts[0].url,'/api/story-video/start');assert.equal(validation.posts[0].data.initial_scene,'');
  assert.equal(validation.posts[0].data.character_cards[1].name,'露娜');assert.equal(validation.posts[0].data.story_input,'idea');
  assert.equal(validation.posts[0].data.audio_review_enabled,false);
  assert.equal(validation.posts[0].data.reference_image_size,'max');
  validation.context.task={status:'running'};validation.context.sync();assert.equal(validation.get('audio-review').disabled,true);
  assert.equal(validation.get('reference-image-size').disabled,true);
  await validation.get('start').onclick();assert.equal(validation.posts.length,1);

  const uploading=harness(),uc=uploading.context;let chosen='old.png';
  const upload=()=>uc.uploadImage({name:'人物.png',size:100},uploading.get('reference-status'),new Element(),result=>{chosen=result.path;});
  upload();upload();assert.equal(uploading.xhrs.length,1);assert.equal(uc.uploading,true);
  assert.equal(uploading.xhrs[0].url,'/api/story-video/upload');assert.equal(uploading.xhrs[0].headers['X-Filename'],encodeURIComponent('人物.png'));
  uploading.xhrs[0].finish(400,{ok:false,error:'格式错误'});assert.equal(chosen,'old.png');assert.equal(uc.uploading,false);
  upload();uploading.xhrs[1].finish(200,{ok:true,data:{path:'new.png'}});assert.equal(chosen,'new.png');
  upload();uc.runtimeEpoch++;uploading.xhrs[2].finish(200,{ok:true,data:{path:'stale.png'}});assert.equal(chosen,'new.png');

  const restore=harness('text'),rc=restore.context;
  Object.assign(rc,{storyKey:'',previewKey:'',renderObservations:()=>{},renderLogs:()=>{},renderOutput:()=>{},renderStory:()=>{},textOf:value=>typeof value==='string'?value:''});
  vm.runInContext(source.slice(source.indexOf('    function update(payload)'),source.indexOf('    function poll()')),rc);
  rc.update({task:{id:'restored',status:'running',inputs:{input_mode:'reference',story_direction:'测试正文',segment_count:2,style:'写实',quality_mode:'automatic',story_input:'story',reference_image_size:'max',character_cards:[card('char_04')],initial_scene:'scene.png'}}});
  assert.equal(restore.get('mode').value,'reference');assert.equal(rc.draft.character_cards[0].id,'char_04');
  assert.equal(rc.draft.character_cards[0].voice,'清朗自然的声线');assert.equal(rc.draft.initial_scene,'scene.png');
  assert.equal(restore.get('audio-review').checked,true);assert.equal(restore.get('audio-review').disabled,true);
  assert.equal(restore.get('reference-image-size').value,'max');assert.equal(rc.draft.reference_image_size,'max');
  rc.update({task:{id:'restored-review-disabled',status:'paused',inputs:{input_mode:'text',story_direction:'新任务',segment_count:2,audio_review_enabled:false}}});
  assert.equal(restore.get('audio-review').checked,false);assert.equal(rc.draft.audio_review_enabled,false);
  rc.update({task:{id:'old',status:'paused',inputs:{opening_image:'old.png',story_direction:'旧故事',segment_count:2}}});
  assert.equal(restore.get('mode').value,'image');
  assert.equal(restore.get('audio-review').checked,true);assert.equal(rc.draft.audio_review_enabled,true);
  assert.equal(restore.get('reference-image-size').value,'match');assert.equal(rc.draft.reference_image_size,'match');

  const resuming=harness('reference',false),resume=resuming.context;
  const oldInputs={input_mode:'reference',character_cards:[card()],segment_count:3};
  resume.task={id:'old-reference',status:'paused',inputs:oldInputs};
  resuming.get('reference-image-size').value='max';
  vm.runInContext(source.slice(source.indexOf('    async function action(name)'),source.indexOf('    if(el._storyVideoResetListener)')),resume);
  await resume.action('resume');
  assert.deepEqual(resuming.posts,[{url:'/api/story-video/action',data:{id:'old-reference',action:'resume'}}]);
  assert.deepEqual(plain(resume.task.inputs),oldInputs);assert(!('reference_image_size' in oldInputs));
  rc.update({task:{id:'interrupted',status:'failed'},quality_summary:{status:'failed',passed:false,reasons:['审稿格式错误']}});
  assert.equal(restore.get('quality-status').textContent,'本地流程已中断：审稿格式错误');
  assert.equal(restore.get('quality-status').hidden,false);
  rc.update({task:{id:'accepted',status:'completed'},quality_summary:{status:'passed',passed:true,reasons:[]}});
  assert.equal(restore.get('quality-status').textContent,'本地验收通过');
  rc.update({task:{id:'completed-with-warnings',status:'completed'},quality_summary:{status:'completed_with_warnings',passed:false,generation_completed:true,warnings:[{stage:'review'},{stage:'segment'}],reasons:['审稿提醒已保留']}});
  assert.equal(restore.get('quality-status').textContent,'生成完成（有审稿提醒） · 2条提醒：审稿提醒已保留');
  assert.equal(restore.get('quality-status').hidden,false);assert(!restore.get('quality-status').textContent.includes('验收'));
  rc.update({task:{id:'working-with-warnings',status:'running'},quality_summary:{status:'working_with_warnings',passed:false,generation_completed:false,warnings:[{stage:'review'}],reasons:['审稿暂不可用']}});
  assert.equal(restore.get('quality-status').textContent,'已保留审稿提醒，继续生成 · 1条提醒：审稿暂不可用');
  assert.equal(restore.get('quality-status').hidden,false);
  rc.update({task:{id:'working-without-reasons',status:'running'},quality_summary:{status:'working_with_warnings',warnings:[]}});
  assert.equal(restore.get('quality-status').textContent,'已保留审稿提醒，继续生成');assert.equal(restore.get('quality-status').hidden,false);
  rc.update({task:{id:'quality-rejected',status:'completed'},quality_summary:{status:'rejected',passed:false,reasons:[]}});
  assert.equal(restore.get('quality-status').textContent,'本地验收未通过');
  const referenceInputs={input_mode:'reference',quality_mode:'automatic',segment_count:2};
  rc.update({task:{id:'reference-working',status:'running',inputs:referenceInputs},quality_summary:{status:'working_with_warnings',quality_gate:false,generation_completed:false,warnings:[{stage:'observation'}],reasons:['观察报告暂不可用']}});
  assert.equal(restore.get('quality-status').textContent,'已保留审稿提醒，继续生成 · 1条提醒：观察报告暂不可用');
  assert.equal(restore.get('quality-status').hidden,false);
  rc.update({task:{id:'reference-complete',status:'completed',inputs:referenceInputs},quality_summary:{status:'completed_with_warnings',quality_gate:false,generation_completed:true,warnings:[{stage:'review'}],reasons:[]}});
  assert.equal(restore.get('quality-status').textContent,'生成完成（有审稿提醒） · 1条提醒');
  assert.equal(restore.get('quality-status').hidden,false);assert(!restore.get('quality-status').textContent.includes('验收通过'));
  rc.update({task:{id:'reference-legacy-complete',status:'completed',inputs:referenceInputs},quality_summary:{status:'recorded',quality_gate:false,accepted_segments:2,reasons:[]}});
  assert.equal(restore.get('quality-status').hidden,true);assert.notEqual(restore.get('quality-status').textContent,'本地验收通过');
  rc.update({task:{id:'reference-incomplete',status:'running',inputs:referenceInputs},quality_summary:{status:'completed_with_warnings',quality_gate:false,generation_completed:false,warnings:[{stage:'review'}],reasons:[]}});
  assert.equal(restore.get('quality-status').hidden,true);assert(!restore.get('quality-status').textContent.includes('生成完成'));
  rc.update({task:{id:'reference-service-failed',status:'failed',inputs:referenceInputs},quality_summary:{status:'failed',quality_gate:false,reasons:['生成服务不可用']}});
  assert.equal(restore.get('quality-status').textContent,'本地流程已中断：生成服务不可用');

  const reset=harness(),rr=reset.context;rr.draft.character_cards=[card()];rr.draft.initial_scene='keep.png';
  reset.get('audio-review').checked=true;
  vm.runInContext(source.slice(source.indexOf('    if(el._storyVideoResetListener)'),source.indexOf('    if (el._storyVideoTaskListener)')),rr);
  reset.listeners['workbench-runtime-reset']({detail:{phase:'begin'}});assert.equal(rr.clearing,true);
  reset.listeners['workbench-runtime-reset']({detail:{phase:'done'}});assert.equal(rr.draft.character_cards[0].name,'阿洛');
  assert.equal(rr.draft.initial_scene,'keep.png');assert.equal(reset.saved.get('storyVideoTask'),'');
  assert.equal(rr.draft.audio_review_enabled,true);assert.equal(reset.get('audio-review').disabled,false);
  const fresh=harness(),fc=fresh.context;let rendered=0;
  fresh.get('audio-review').checked=true;fc.saveDraft();
  fc.window.Pages={'story-video':{render:()=>{rendered++;}}};
  vm.runInContext(source.slice(source.indexOf("    get('new').onclick"),source.indexOf("    get('start').onclick")),fc);
  fresh.get('new').onclick();
  assert.equal(JSON.parse(fresh.saved.get('storyVideoDraft')).audio_review_enabled,false);assert.equal(rendered,1);
  assert.equal(JSON.parse(fresh.saved.get('storyVideoDraft')).reference_image_size,'match');
  assert(source.includes("get('audio-review').checked=draft.audio_review_enabled===true"));
  assert(source.includes("get('reference-image-size').value=draft.reference_image_size==='max'?'max':'match'"));
  assert.match(source,/仍不能保证人物与人设图完全一致/);
  assert(source.includes("'automatic','audio-review','story-input'"));
  assert(source.includes('移除选择会保留已上传文件'));assert(!/\/api\/[^'"\s]*delete/.test(source));
  console.log('Story reference UI passed: three modes, exact payload, standard/high-detail reference selection, pending/old/new service capability handshake, default/restore/lock/legacy-resume compatibility, stable IDs/7 limit, validation, upload lock/failure/stale response, old/new task restore and reset preservation; no models or HTTP.');
}
if(process.argv.includes('--source-story'))testSourceStory();
else main().catch(error=>{console.error(error);process.exitCode=1;});
