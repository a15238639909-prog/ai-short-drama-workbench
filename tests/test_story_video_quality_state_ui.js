'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('web/pages/story_video.js','utf8');
const block=source.slice(source.indexOf('      var quality=payload.quality_summary'),source.indexOf("      get('bar').style.width"));
for(const state of ['running','queued','paused','failed','cancelled','interrupted','waiting_user']){
 const node={};
 const context={payload:{quality_summary:{status:'working_with_warnings',warnings:['evidence'],reasons:['格式提醒']}},task:{status:state},get:()=>node,textOf:String};
 vm.runInNewContext(block,context);
 if(['running','queued'].includes(state))assert.match(node.textContent,/继续生成/);
 else assert.doesNotMatch(node.textContent,/继续生成/);
 assert.match(node.textContent,/1条提醒/);
}
for(const state of ['running','completed']){
 const node={};
 vm.runInNewContext(block,{payload:{quality_summary:{status:'completed_with_warnings',generation_completed:true,reasons:['旧提醒']}},task:{status:state},get:()=>node,textOf:String});
 assert.equal(node.textContent.includes('生成完成'),state==='completed');
}
console.log('9 quality-state UI cases passed; no server or model calls.');
