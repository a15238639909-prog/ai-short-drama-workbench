/* Whole settings page in an in-memory DOM: no server, files changed or models. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/pages/settings.js', 'utf8');
const copy = value => JSON.parse(JSON.stringify(value));
const flush = async () => { for (let n=0;n<30;n++) await Promise.resolve(); };
const deferred = () => { let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve}; };
const decode = text => String(text || '').replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');

class Element {
  constructor(tag='div', doc) {
    this.tagName=tag;this.doc=doc;this.children=[];this.attributes={};this.dataset={};this.style={};this.value='';this.hidden=false;
    this.disabled=false;this.checked=false;this.textContent='';this.classList={add:()=>{},toggle:()=>{}};
  }
  get id(){return this.attributes.id || '';}
  get options(){return this.children.filter(child=>child.tagName==='option');}
  setAttribute(name,value){this.attributes[name]=decode(value);if(name==='value')this.value=decode(value);if(name==='hidden')this.hidden=true;
    if(name==='disabled')this.disabled=true;if(name==='checked')this.checked=true;
    if(name.startsWith('data-'))this.dataset[name.slice(5).replace(/-([a-z])/g,(_,s)=>s.toUpperCase())]=decode(value);}
  getAttribute(name){return this.attributes[name] ?? null;}
  appendChild(child){child.parentNode=this;this.children.push(child);return child;}
  get innerHTML(){return this._html || '';}
  set innerHTML(markup){
    this._html=markup;this.children=[];
    const stack=[this], voidTags=['input','img','br','hr','meta','link'];
    for(const match of String(markup).matchAll(/<!--[\s\S]*?-->|<\/?([a-z][\w-]*)\b([^>]*)>|([^<]+)/gi)) {
      if(match[0].startsWith('<!--'))continue;
      if(match[3]){stack[stack.length-1].textContent+=decode(match[3]);continue;}
      const tag=match[1].toLowerCase();
      if(match[0][1]==='/'){while(stack.length>1){if(stack.pop().tagName===tag)break;}continue;}
      const node=new Element(tag,this.doc);
      for(const attr of match[2].matchAll(/([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s]+)))?/g))node.setAttribute(attr[1],attr[2]??attr[3]??attr[4]??'');
      stack[stack.length-1].appendChild(node);
      if(!voidTags.includes(tag))stack.push(node);
    }
    function values(node){node.children.forEach(values);if(node.tagName==='textarea')node.value=node.textContent;
      if(node.tagName==='option'&&!('value' in node.attributes))node.value=node.textContent;
      if(node.tagName==='select')node.value=(node.options.find(item=>'selected' in item.attributes)||node.options[0]||{}).value||'';}
    values(this);
  }
  matches(selector){
    if(selector.includes(' '))return false;
    const attr=selector.match(/^\[([^=\]]+)(?:=["']?([^"'\]]*)["']?)?\]$/);
    if(attr)return attr[1] in this.attributes&&(attr[2]===undefined||this.attributes[attr[1]]===attr[2]);
    if(selector[0]==='#')return this.id===selector.slice(1);
    if(selector[0]==='.')return String(this.attributes.class||'').split(/\s+/).includes(selector.slice(1).replace(':checked',''))&&(!selector.endsWith(':checked')||this.checked);
    return this.tagName===selector;
  }
  querySelectorAll(selector){return this.children.flatMap(child=>[...(child.matches(selector)?[child]:[]),...child.querySelectorAll(selector)]);}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  focus(){} remove(){} scrollIntoView(){}
}

function project(owner, pov='第一视角POV', names=['安宁']){
  return {title:'安全测试项目',settings:{pov,...(owner?{camera_owner:owner}:{}),world_type:'现代',style:'自然摄影',story_mode:'single',one_line:'成年朋友在书店寻找一本书。',content_tendencies:[]},
    characters:names.map((name,n)=>({name,age:'24',sex:'女',character_id:'C'+n})),styles:[{name:'自然摄影'}],world_types:['现代'],visuals:[],content_tendencies:[],char_fields:{},sheet_body_overrides_supported:true};
}

function harness(projects){
  const posts=[],bindings=[],messages=[],saved=new Map(),timers=[];
  const document={createElement:tag=>new Element(tag,document)};
  const main=new Element('main',document);
  document.getElementById=id=>main.id===id?main:main.querySelector('#'+id);
  document.querySelectorAll=selector=>main.querySelectorAll(selector);
  let readSettings=(sid)=>Promise.resolve(copy(projects[sid]));
  const api={post:async(url,payload)=>{
    posts.push({url,payload:copy(payload)});
    if(url==='/api/kits/options')return {dims:{pov:['电影第三人称','第一视角POV','自拍手持']}};
    if(url==='/api/project/settings'){
      if(payload.settings){projects[payload.story_id].settings={...projects[payload.story_id].settings,...copy(payload.settings)};return {};}
      return readSettings(payload.story_id);
    }
    if(url.endsWith('/get')&&!url.endsWith('/plan/get'))return {episodes:[{no:1,brief:'成年朋友在书店寻找一本书。'}]};
    if(url.endsWith('/progress'))return {running:false,status:'completed'};
    return {};
  }};
  const UI={esc:text=>String(text??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'),toast:(...args)=>messages.push(args),empty:()=>''};
  const UIActions={row:()=>'',bind:(host,handlers)=>bindings.push({host,handlers}),progress:{stuck:()=>{},busy:()=>{},done:()=>{}}};
  const location={hash:'#settings'},window={api,UI,UIActions,location};
  const context=vm.createContext({window,document,UI,UIActions,location,localStorage:{getItem:key=>saved.get(key)||null,setItem:(key,value)=>saved.set(key,value)},
    history:{replaceState:()=>{}},setTimeout:(fn,ms)=>{timers.push({fn,ms});return timers.length;},clearTimeout:()=>{},setInterval:()=>1,clearInterval:()=>{},
    alert:message=>messages.push([message]),confirm:()=>true,console});
  vm.runInContext(source,context,{filename:'settings.js'});
  const render=async sid=>{bindings.length=0;await window.Pages.settings.render(main,{storyId:sid});await flush();};
  const save=async()=>{const binding=bindings.find(row=>row.host===document&&row.handlers.save);assert(binding,'real normal-save binding exists');binding.handlers.save();await flush();};
  const click=async id=>{const element=document.getElementById(id);assert(element&&element.onclick,'real click handler '+id);await element.onclick();await flush();};
  const settingPosts=()=>posts.filter(row=>row.url==='/api/project/settings'&&row.payload.settings);
  return {render,save,click,posts,settingPosts,document,main,messages,readWith:callback=>{readSettings=callback;}};
}

async function main(){
  const projects={SAFE_46:project('白川','第一视角POV',['白川','安宁']),SAFE_47:project(),SAFE_SELFIE:project('白川','自拍手持',['白川','安宁'])};
  const h=harness(projects),get=id=>h.document.getElementById(id);
  await h.render('SAFE_47');
  assert.equal(get('psCameraOwnerControl').hidden,false);assert.equal(get('psCameraOwner').value,'我');
  assert.match(get('psCameraOwner').options[0].textContent,/画面里的人物需要露脸/);
  assert(get('psCameraOwner').options.some(option=>option.value==='安宁'));
  await h.save();assert.equal(h.settingPosts().at(-1).payload.settings.camera_owner,'我');
  get('psCameraOwner').value='安宁';await h.save();assert.equal(h.settingPosts().at(-1).payload.settings.camera_owner,'安宁');
  get('psCameraOwner').value='我';await h.click('genImages');
  let make=h.posts.findIndex(row=>row.url==='/api/saga/SAFE_47/make');assert(make>=0);
  assert.equal(h.posts[make].payload.stage,'images');
  assert.equal(h.posts.slice(0,make).filter(row=>row.url==='/api/project/settings'&&row.payload.settings).at(-1).payload.settings.camera_owner,'我');
  get('psCameraOwner').value='安宁';await h.click('fixCharImgs');
  const fix=h.posts.findIndex(row=>row.url==='/api/saga/SAFE_47/gen-char-images');assert(fix>=0);
  assert.equal(h.posts.slice(0,fix).filter(row=>row.url==='/api/project/settings'&&row.payload.settings).at(-1).payload.settings.camera_owner,'安宁');
  await h.render('SAFE_46');assert.equal(get('psCameraOwner').value,'白川');await h.save();
  projects.SAFE_47=project();await h.render('SAFE_47');await h.save();
  assert.equal(h.settingPosts().at(-1).payload.story_id,'SAFE_47');assert.equal(h.settingPosts().at(-1).payload.settings.camera_owner,'我');
  await h.render('SAFE_SELFIE');assert.equal(get('psCameraOwnerControl').hidden,true);
  await h.save();assert(!('camera_owner' in h.settingPosts().at(-1).payload.settings));assert.equal(projects.SAFE_SELFIE.settings.camera_owner,'白川');
  get('psPov').value='第一视角POV';get('psPov').onchange();assert.equal(get('psCameraOwnerControl').hidden,false);assert.equal(get('psCameraOwner').value,'白川');
  get('psPov').value='电影第三人称';get('psPov').onchange();assert.equal(get('psCameraOwnerControl').hidden,true);await h.save();
  assert(!('camera_owner' in h.settingPosts().at(-1).payload.settings));
  projects.SAFE_GONE=project('已命名的拍摄者');await h.render('SAFE_GONE');assert.equal(get('psCameraOwner').value,'已命名的拍摄者');await h.save();
  assert.equal(h.settingPosts().at(-1).payload.settings.camera_owner,'已命名的拍摄者');
  const delayed=deferred();h.readWith(sid=>sid==='SAFE_46'?delayed.promise:Promise.resolve(copy(projects[sid])));
  const old=h.render('SAFE_46');await flush();await h.render('SAFE_47');delayed.resolve(copy(projects.SAFE_46));await old;
  assert.equal(get('psCameraOwner').value,'我');await h.save();assert.equal(h.settingPosts().at(-1).payload.story_id,'SAFE_47');
  assert.equal(h.messages.filter(row=>row[1]==='err').length,0,JSON.stringify(h.messages));
  console.log('Settings whole-page POV UI passed: default viewer, explicit character, normal save, make-images, fix-images, project switch and stale reply, non-POV/selfie preservation. No models or server calls.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
