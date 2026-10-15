/* Shared image drop: files, browser image URLs, data URLs and local previews. */
(function(){
  'use strict';
  function source(transfer){
    var files=Array.from(transfer.files||[]);
    if(files.length)return {file:files[0]};
    var html=transfer.getData('text/html')||'', url='';
    if(html){var doc=new DOMParser().parseFromString(html,'text/html'), img=doc.querySelector('img');if(img)url=img.getAttribute('src')||'';}
    if(!url)url=(transfer.getData('text/uri-list')||transfer.getData('text/plain')||'').split(/\r?\n/).find(function(line){return line.trim()&&!line.startsWith('#');})||'';
    return {url:url.trim()};
  }
  async function imageFile(url){
    if(url.startsWith('data:image/')||url.startsWith('blob:')){
      var response=await fetch(url), blob=await response.blob();
      if(blob.size>20*1024*1024)throw Error('图片最大20MB');
      var extension={'image/png':'png','image/jpeg':'jpg','image/webp':'webp'}[blob.type];
      if(!extension)throw Error('请拖入PNG、JPG或WebP图片');
      return new File([blob],'拖入图片.'+extension,{type:blob.type});
    }
    var parsed=new URL(url,location.href);
    if(!['http:','https:'].includes(parsed.protocol))throw Error('请拖入图片文件或网页中的图片');
    var data=await window.api.post('/api/image-drop',{url:parsed.href});
    var bytes=Uint8Array.from(atob(data.base64),function(c){return c.charCodeAt(0);});
    return new File([bytes],data.name,{type:data.mime});
  }
  function bind(target, upload, options){
    options=options||{};var loading=false;
    target._imageDropBound=true;
    target.addEventListener('dragover',function(e){e.preventDefault();e.stopPropagation();if(!loading&&!(options.disabled&&options.disabled()))target.classList.add('drag');});
    target.addEventListener('dragleave',function(){target.classList.remove('drag');});
    target.addEventListener('drop',async function(e){
      e.preventDefault();e.stopPropagation();target.classList.remove('drag');
      if(loading||(options.disabled&&options.disabled()))return;
      loading=true;
      try{var item=source(e.dataTransfer);if(!item.file&&!item.url)throw Error('没有读取到图片，请拖入图片本身');await upload(item.file||await imageFile(item.url));}
      catch(error){if(options.error)options.error(error.message);else if(window.UI)UI.toast(error.message,'err');}
      finally{loading=false;}
    });
  }
  // Keep file/URL drops outside a target from navigating away from the workbench.
  document.addEventListener('dragover',function(e){if(Array.from(e.dataTransfer.types||[]).some(function(t){return ['Files','text/uri-list','text/html'].includes(t);}))e.preventDefault();});
  function stopMediaNavigation(e){if((e.dataTransfer.files||[]).length||Array.from(e.dataTransfer.types||[]).includes('text/uri-list')||/<img\b/i.test(e.dataTransfer.getData('text/html')||''))e.preventDefault();}
  document.addEventListener('drop',stopMediaNavigation);
  function installDocument(doc){
    if(!doc||doc._imageDropInstalled)return;doc._imageDropInstalled=true;
    function scan(){
      doc.querySelectorAll('input[type="file"]').forEach(function(input){
        if(input._imageDropInstalled||!(/image|png|jpe?g|webp/i.test(input.accept||''))||input.id==='upOne')return;
        for(var p=input;p;p=p.parentElement)if(p._imageDropBound)return;
        input._imageDropInstalled=true;
        var zone=doc.createElement('div');zone.textContent='可拖入图片文件或网页图片';
        zone.style.cssText='padding:10px;margin:6px 0;border:1px dashed #aab29f;border-radius:6px;font-size:12px';
        input.insertAdjacentElement('afterend',zone);
        bind(zone,function(file){var win=doc.defaultView, transfer=new win.DataTransfer();transfer.items.add(file);input.files=transfer.files;input.dispatchEvent(new win.Event('change',{bubbles:true}));},{disabled:function(){return input.disabled;}});
      });
      doc.querySelectorAll('iframe').forEach(function(frame){
        if(frame._imageDropInstalled)return;frame._imageDropInstalled=true;
        function attach(){try{installDocument(frame.contentDocument);}catch(_){}}
        frame.addEventListener('load',attach);attach();
      });
    }
    scan();new MutationObserver(scan).observe(doc.documentElement,{childList:true,subtree:true});
    if(doc!==document){doc.addEventListener('dragover',function(e){if(Array.from(e.dataTransfer.types||[]).some(function(t){return ['Files','text/uri-list','text/html'].includes(t);}))e.preventDefault();});doc.addEventListener('drop',stopMediaNavigation);}
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',function(){installDocument(document);});else installDocument(document);
  window.ImageDrop={bind:bind,source:source,imageFile:imageFile};
})();
