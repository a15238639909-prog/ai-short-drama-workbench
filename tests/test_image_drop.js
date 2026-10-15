const assert=require('assert'), vm=require('vm'), fs=require('fs');
const handlers={}, doc={readyState:'loading',addEventListener(k,fn){handlers[k]=fn;}};
const context={window:{},document:doc,console,URL,Uint8Array,atob,File,fetch,
 location:{href:'http://localhost:18853/'},DOMParser:class{parseFromString(html){return {querySelector:()=>({getAttribute:()=>/src="([^"]+)"/.exec(html)[1]})};}}};
vm.runInNewContext(fs.readFileSync('web/image_drop.js','utf8'),context);
const d=context.window.ImageDrop;
assert.strictEqual(d.source({files:[{name:'one.png'}]}).file.name,'one.png');
assert.strictEqual(d.source({files:[],getData(t){return t==='text/html'?'<img src="https://example.com/image.png">':'';}}).url,'https://example.com/image.png');
assert.strictEqual(d.source({files:[],getData(t){return t==='text/uri-list'?'# comment\nhttps://example.com/photo':'';}}).url,'https://example.com/photo');
(async()=>{
 let uploaded=0,error='',blocked=true,events={};
 const node={classList:{add(){},remove(){}},addEventListener(k,fn){events[k]=fn;}};
 d.bind(node,()=>uploaded++,{disabled:()=>blocked,error:m=>error=m});
 const event={preventDefault(){},stopPropagation(){},dataTransfer:{files:[{name:'photo.png'}]}};
 await events.drop(event);assert.strictEqual(uploaded,0);
 blocked=false;await events.drop(event);assert.strictEqual(uploaded,1);
 event.dataTransfer={files:[],getData(){return '';}};await events.drop(event);assert(error);
 const file=await d.imageFile('data:image/png;base64,aGVsbG8=');assert.strictEqual(file.type,'image/png');
 console.log('image drop: file / webpage HTML / URL / disabled / error / data URL passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
