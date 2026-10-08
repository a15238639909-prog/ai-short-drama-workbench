(function () {
  window.Pages = window.Pages || {};
  window.Pages.motion = { render: function (el) {
    var draft = {};
    try { draft = JSON.parse(localStorage.getItem('motionDraft') || '{}'); } catch (_) {}
    var esc = UI.esc, media = draft.media || {}, requests = {}, task = null, timer = null, lastOutput = '';
    var busy = false, displayedTask = '';
    var featureQueue = [], featureRunning = false, featureVersions = {character:0,character2:0,scene:0}, featureSources = draft.featureSources || {};
    var card = function (key, number, title, note) {
      return '<section class="mt-card" data-drop="' + key + '"><div class="mt-card-title"><span class="mt-number">' + number + '</span><h3>' + title + '</h3></div>' +
        '<p class="mt-note">' + note + '</p><div class="mt-preview" id="mt-preview-' + key + '"><span>点击下方选择，或将文件拖到这里</span></div>' +
        '<input id="mt-upload-' + key + '" type="file" aria-label="上传' + title + '" accept="' + (key === 'video' ? '.mp4,.mov,.mkv,.webm' : '.png,.jpg,.jpeg,.webp') + '">' +
        '<div class="mt-file" id="mt-name-' + key + '">尚未选择</div><div class="mt-upload-status" id="mt-upload-status-' + key + '" role="status"></div>' +
        '<button type="button" class="btn small" data-clear="' + key + '" hidden>清除选择</button>' +
        (key.indexOf('character') === 0 ? '<label>人物图类型<select id="mt-' + key + '_layout"><option value="single">单人照片 / 单人全身图</option><option value="four_panel">四格设定图（取最左侧人物）</option></select></label>' : '') +
        (key !== 'video' ? '<label>' + (key.indexOf('character') === 0 ? '人物特征（一句话）' : '场景特征（换场景时填写）') + '<textarea id="mt-' + key + '_details" rows="2" placeholder="上传后本地AI自动填写，也可以自己写">' + esc(draft[key + '_details'] || '') + '</textarea></label><button type="button" class="btn small" data-describe="' + key + '">识别特征</button><div class="mt-upload-status" id="mt-feature-status-' + key + '" role="status"></div>' : '') + (key.indexOf('character') === 0 ? '<label>替换原视频中的谁<input class="mt-path" id="mt-source_person' + (key === 'character2' ? '2' : '') + '" placeholder="如：金发的人、灰衣服的人；单人视频可留空" value="' + esc(draft[key === 'character2' ? 'source_person2' : 'source_person'] || '') + '"></label>' : '') + '</section>';
    };
    el.innerHTML = '<style>' +
      '.mt-page{max-width:1120px;margin:auto;padding:24px 18px;color:#30342f}.mt-page h2{margin:0 0 8px}.mt-note{color:#777d73;font-size:13px;line-height:1.6;margin:8px 0 14px}' +
      '.mt-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin:22px 0}.mt-card{padding:18px;background:#fffdf8;border:1px solid #dedfd5;border-radius:14px;min-width:0}.mt-card.drag{outline:2px solid #6f8565}' +
      '.mt-card-title{display:flex;align-items:center;gap:9px}.mt-card h3{font-size:17px;margin:0}.mt-number{background:#e6ecdf;color:#47623b;border-radius:50%;width:26px;height:26px;text-align:center;line-height:26px;font-size:13px}' +
      '.mt-preview{height:190px;background:#f0f1ea;border:1px dashed #ccd2c1;border-radius:9px;display:flex;align-items:center;justify-content:center;overflow:hidden;text-align:center;color:#8a9083;font-size:13px;margin-bottom:12px}.mt-preview img,.mt-preview video{width:100%;height:100%;object-fit:contain}' +
      '.mt-page input[type=file]{width:100%;font-size:0}.mt-page input[type=file]::file-selector-button{font-size:13px;border:1px solid #d6dccf;background:#eef2e7;color:#435739;padding:8px 15px;border-radius:7px;cursor:pointer}.mt-page label{display:block;font-size:13px;margin-top:12px}.mt-page textarea,.mt-page select,.mt-path{display:block;width:100%;box-sizing:border-box;margin-top:6px}.mt-file{font-size:12px;overflow-wrap:anywhere;margin-top:9px;color:#5a6651}.mt-upload-status{font-size:12px;color:#b2673a;min-height:18px;margin:5px 0}' +
      '.mt-trim{padding:16px 20px;background:#f0f1e9;border-radius:12px;display:flex;align-items:center;gap:20px;flex-wrap:wrap}.mt-trim label{margin:0}.mt-trim input{width:74px;margin:0 5px}.mt-advanced{margin:18px 0;color:#616957}.mt-advanced summary{cursor:pointer;font-size:13px}.mt-actions{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.mt-actions .btn{padding:11px 22px}.mt-result video{width:100%;border-radius:12px;max-height:620px;background:#111}.mt-result a{display:inline-block;margin:12px 18px 0 0}.mt-status{min-height:24px;line-height:1.6}' +
      '@media(max-width:800px){.mt-grid{grid-template-columns:1fr}.mt-preview{height:220px}.mt-page{padding:16px 8px}}</style>' +
      '<div class="mt-page"><h2>动作迁移</h2><p class="mt-note">换一个人物演原视频的动作，也可以一起换场景。先选素材，再生成左右对比。</p><label><input type="checkbox" id="mt-auto-describe"' + (draft.auto_describe === false ? '' : ' checked') + '> 上传图片后自动识别特征（本地AI）</label><p class="mt-note">识别结果是可修改的草稿；首次加载模型会稍慢。手写内容优先保留，也可关闭自动识别。</p>' +
      '<div class="mt-grid">' + card('video', '1', '原视频', '提供人物动作和运镜 · 最多250MB') + card('character', '2', '替换人物', '必选 · 建议单人全身照，最多20MB') + card('scene', '3', '替换场景（可选）', '不上传就保留原场景 · 最多20MB') + '</div><details class="mt-advanced"' + (draft.character2 ? ' open' : '') + '><summary>＋ 替换第二个人（可选）</summary><p class="mt-note">上传第二张人物图，并分别写明原视频中对应哪个人。人物交叉、遮挡和翻滚仍可能跟随不准。</p>' + card('character2', '2B', '第二个人物', '不上传就只替换一个人') + '</details>' +
      '<div class="mt-trim"><label>处理范围<select id="mt-range_mode"><option value="full">整段视频（自动分段拼合）</option><option value="clip">指定片段</option></select></label><label id="mt-start-label">从第 <input id="mt-start" aria-label="开始秒数" type="number" min="0" step="0.1" value="' + esc(String(draft.start || 0)) + '"> 秒开始</label><label id="mt-seconds-label">生成 <input id="mt-seconds" aria-label="生成时长" type="number" min="0.1" step="0.1" value="' + esc(String(draft.seconds || 8)) + '"> 秒</label><span id="mt-duration" class="mt-note">按切镜及约5秒分段，完整尾段，自动拼合并保留原声</span></div>' +
      '<details class="mt-advanced"><summary>高级设置（通常不用改）</summary><p class="mt-note">当前使用V1参考流程、0.4档、10步。仅给照片的替换效果尚不稳定，请保留一句人物/场景特征。留空时由本地千问逐段看原片并写词，缺少特征会先本地识图。手写完整提示词逐段原样复用；双人时图片顺序为人物1、人物2、可选场景。</p><label>完整提示词（可选，填写后优先使用）<textarea id="mt-prompt" rows="6">' + esc(draft.prompt || '') + '</textarea></label>' +
      ['video','character','character2','scene'].map(function(k){return '<label>' + ({video:'原视频',character:'人物照片',character2:'第二人物照片',scene:'场景照片'})[k] + '本地路径（兼容以前的选择）<input class="mt-path" id="mt-' + k + '" value="' + esc(draft[k] || '') + '"></label>';}).join('') + '</details>' +
      '<div class="mt-actions"><button class="btn" id="mt-go">开始生成对比视频</button><button class="btn" id="mt-stop" hidden>停止生成</button><span class="mt-note">近似跟随动作与运镜；分段接缝可能跳变，偶有背景替换不完整。</span></div>' +
      '<p id="mt-status" class="mt-status" role="status">请上传原视频和人物照片，检查自动填写的特征后开始生成。</p><div id="mt-result" class="mt-result"></div></div>';
    var marker = el.lastElementChild;
    var active = function () { return marker.isConnected && el.lastElementChild === marker; };
    var get = function (id) { return el.querySelector('#mt-' + id); };
    get('character_layout').value = draft.character_layout || 'single';
    get('character2_layout').value = draft.character2_layout || 'single';
    get('range_mode').value = draft.range_mode || 'full';
    var fields = ['video','character','character2','scene','character_layout','character2_layout','character_details','character2_details','scene_details','source_person','source_person2','range_mode','start','seconds','prompt'];
    function saveDraft() {
      var data = {}; fields.forEach(function(k){ data[k] = get(k).value; }); data.media = media;
      data.auto_describe = get('auto-describe').checked; data.featureSources = featureSources;
      try { localStorage.setItem('motionDraft', JSON.stringify(data)); } catch (_) {}
      return data;
    }
    function sync() {
      var uploading = Object.keys(requests).length > 0;
      get('go').disabled = busy || uploading || featureRunning || featureQueue.length > 0;
      get('stop').hidden = !busy;
      fields.forEach(function(k){ get(k).disabled = busy; });
      ['start','seconds'].forEach(function(k){get(k+'-label').style.display=get('range_mode').value==='full'?'none':'';});
      ['video','character','character2','scene'].forEach(function(k){get('upload-' + k).disabled = busy || !!requests[k];});
      el.querySelectorAll('[data-clear]').forEach(function(b){b.disabled = busy;});
      get('auto-describe').disabled = busy;
      el.querySelectorAll('[data-describe]').forEach(function(b){var k=b.dataset.describe;b.disabled=busy||featureRunning||!!requests[k]||!get(k).value;});
    }
    function invalidateFeature(key) {
      if (key === 'video') return;
      featureVersions[key]++;
      featureQueue = featureQueue.filter(function(j){return j.key !== key;});
      if (featureSources[key] && get(key+'_details').value === featureSources[key].text) get(key+'_details').value='';
      delete featureSources[key];
      get('feature-status-'+key).textContent='';
    }
    function queueFeature(key, automatic) {
      if (busy || !get(key).value || (automatic && !get('auto-describe').checked)) return;
      var field=get(key+'_details'), status=get('feature-status-'+key);
      if (automatic && field.value.trim()) { status.textContent='已保留原来的特征；点“识别特征”可按新图重写。'; return; }
      featureVersions[key]++;
      featureQueue=featureQueue.filter(function(j){return j.key!==key;});
      featureQueue.push({key:key,path:get(key).value,layout:key==='scene'?'single':get(key+'_layout').value,text:field.value,version:featureVersions[key],automatic:automatic});
      status.textContent='等待本地识别…';sync();runFeatureQueue();
    }
    async function runFeatureQueue() {
      if (featureRunning || !active()) return;
      var job=featureQueue.shift(); if(!job){sync();return;}
      var key=job.key, field=get(key+'_details'), status=get('feature-status-'+key);
      if (job.version!==featureVersions[key] || get(key).value!==job.path || field.value!==job.text || (job.automatic&&!get('auto-describe').checked)) {
        if(job.version===featureVersions[key])status.textContent=field.value!==job.text?'已保留你修改的文字。':'已取消等待识别。';
        sync();runFeatureQueue();return;
      }
      featureRunning=true;sync();status.textContent='本地AI识别中，首次加载模型可能需要1～2分钟…';
      try {
        var result=await window.api.post('/api/motion/describe',{kind:key==='character2'?'character':key,path:job.path,character_layout:job.layout});
        if(!active() || job.version!==featureVersions[key] || get(key).value!==job.path) return;
        if(field.value!==job.text){status.textContent='识别已完成；保留你刚刚修改的文字。';return;}
        field.value=result.details;
        featureSources[key]={path:job.path,layout:job.layout,text:result.details};
        status.textContent='已自动填写，请检查后使用；可以直接修改。';saveDraft();
      } catch(e) {
        if(active()&&job.version===featureVersions[key]) status.textContent='识别未完成：'+e.message+'。可手写特征后继续。';
      } finally {
        featureRunning=false;if(active()){sync();runFeatureQueue();}
      }
    }
    get('auto-describe').onchange=function(){saveDraft();runFeatureQueue();};
    el.querySelectorAll('[data-describe]').forEach(function(b){b.onclick=function(){queueFeature(b.dataset.describe,false);};});
    function preview(key) {
      var area = get('preview-' + key), m = media[key]; area.replaceChildren();
      if (m && m.path === get(key).value && m.url) {
        var item = document.createElement(key === 'video' ? 'video' : 'img');
        item.src = m.url;
        if (key === 'video') { item.controls = true; item.preload = 'metadata'; }
        else item.alt = key === 'character' ? '待替换人物照片预览' : '新场景照片预览';
        area.appendChild(item);
      } else { var empty = document.createElement('span'); empty.textContent = get(key).value ? '沿用已选本地文件，可重新上传查看预览' : '点击下方选择，或将文件拖到这里'; area.appendChild(empty); }
      get('name-' + key).textContent = get(key).value ? (m && m.path === get(key).value ? m.name : get(key).value.split(/[\\/]/).pop()) : '尚未选择';
      el.querySelector('[data-clear="' + key + '"]').hidden = !get(key).value && !requests[key];
      if (key === 'video') get('duration').textContent = (m && m.path === get(key).value ? '原片 ' + Number(m.duration).toFixed(1) + ' 秒 · ' : '') + '按切镜及约5秒分段，完整尾段，自动拼合并保留原声';
    }
    function upload(key, file) {
      if (!file || busy || requests[key]) return;
      var limit = (key === 'video' ? 250 : 20) * 1024 * 1024;
      var valid = key === 'video' ? /\.(mp4|mov|mkv|webm)$/i : /\.(png|jpe?g|webp)$/i;
      if (!valid.test(file.name) || !file.size || file.size > limit) { get('upload-status-' + key).textContent = '格式不支持或文件太大，请按上方说明重新选择。'; return; }
      var xhr = new XMLHttpRequest(); requests[key] = xhr; sync();
      el.querySelector('[data-clear="' + key + '"]').hidden = false;
      get('upload-status-' + key).textContent = '正在上传…';
      xhr.open('POST', '/api/motion/upload'); xhr.timeout = 180000;
      xhr.setRequestHeader('Content-Type', 'application/octet-stream'); xhr.setRequestHeader('X-Media-Kind', key==='character2'?'character':key); xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name));
      xhr.upload.onprogress = function(e){if(active() && e.lengthComputable)get('upload-status-' + key).textContent = '上传 ' + Math.round(e.loaded/e.total*100) + '%，完成后检查文件…';};
      xhr.onload = function(){
        if (!active()) return;
        try {
          var j = JSON.parse(xhr.responseText); if (xhr.status !== 200 || !j.ok) throw new Error(j.error || '上传失败');
          invalidateFeature(key); media[key] = j.data; get(key).value = j.data.path;
          if (key === 'video') { get('start').value = 0; get('seconds').value = Math.min(8, Math.floor(j.data.duration * 10) / 10); }
          get('upload-status-' + key).textContent = '已就绪'; preview(key); saveDraft();
          if(key!=='video')queueFeature(key,true);
        } catch(e){get('upload-status-' + key).textContent = e.message;}
      };
      xhr.onerror = xhr.ontimeout = function(){if(active())get('upload-status-' + key).textContent = '上传失败，请重试；原来的选择仍保留。';};
      xhr.onabort = function(){if(active())get('upload-status-' + key).textContent = '已取消上传';};
      xhr.onloadend = function(){delete requests[key];if(active()){get('upload-' + key).value='';sync();}};
      xhr.send(file);
    }
    ['video','character','character2','scene'].forEach(function(key){
      preview(key);
      get('upload-' + key).onchange = function(){upload(key,this.files[0]);};
      var drop = el.querySelector('[data-drop="' + key + '"]');
      drop.ondragover = function(e){e.preventDefault();if(!busy)drop.classList.add('drag');};
      drop.ondragleave = function(){drop.classList.remove('drag');};
      drop.ondrop = function(e){e.preventDefault();drop.classList.remove('drag');upload(key,e.dataTransfer.files[0]);};
      el.querySelector('[data-clear="' + key + '"]').onclick = function(){if(requests[key])requests[key].abort();invalidateFeature(key);get(key).value='';delete media[key];get('upload-status-' + key).textContent='';preview(key);saveDraft();sync();};
      if(key!=='video')get(key).addEventListener('change',function(){invalidateFeature(key);saveDraft();queueFeature(key,true);});
    });
    ['character','character2'].forEach(function(key){get(key+'_layout').addEventListener('change',function(){invalidateFeature(key);saveDraft();queueFeature(key,true);});});
    fields.forEach(function(k){get(k).addEventListener('input',function(){if(['video','character','character2','scene'].includes(k))preview(k);saveDraft();sync();});});
    function update(t) {
      if (!active()) return;
      task = t; busy = !!(t && ['running','queued'].includes(t.status)); sync();
      if(busy && t.inputs && displayedTask !== t.id){
        displayedTask=t.id;fields.forEach(function(k){if(t.inputs[k]!==undefined)get(k).value=t.inputs[k];});
        ['video','character','character2','scene'].forEach(preview);saveDraft();sync();
      }
      var names = {completed:'已完成',failed:'生成失败',cancelled:'已取消',paused:'已暂停',interrupted:'已中断',running:'正在生成',queued:'排队中'};
      if(t)get('status').textContent = t.error || ((names[t.status] || t.status) + '：' + (t.step_label || '') + (busy && t.detail ? ' · ' + t.detail : ''));
      if(t && t.output && t.output.comparison_url !== lastOutput){
        lastOutput=t.output.comparison_url;var result=get('result');result.replaceChildren();
        var title=document.createElement('h3');title.textContent='生成结果 · 左边原视频，右边替换后';result.appendChild(title);
        if(t.output.segments){var note=document.createElement('p');note.className='mt-note';note.textContent='共'+t.output.segments.length+'段，已自动拼合 · '+Number(t.output.seconds).toFixed(2)+'秒';result.appendChild(note);}
        var video=document.createElement('video');video.controls=true;video.preload='metadata';video.src=t.output.comparison_url;result.appendChild(video);
        var audioLabel=t.output.audio_mode==='source'?'原声':t.output.audio_mode==='silent'?'原片无声':null;
        [['打开新视频（'+(audioLabel||'含生成音频')+'）',t.output.url],['下载左右对比（'+(audioLabel||'静音')+'）',t.output.comparison_url]].forEach(function(pair,i){var a=document.createElement('a');a.textContent=pair[0];a.href=pair[1];a.target='_blank';if(i)a.download='动作迁移对比.mp4';result.appendChild(a);});
      }
      clearTimeout(timer);if(busy)timer=setTimeout(poll,2500);
    }
    function poll(){if(active())window.api.get('/api/motion/status').then(function(d){update(d.task);}).catch(function(e){if(active()){get('status').textContent='读取进度失败：'+e.message;if(busy)timer=setTimeout(poll,5000);}});}
    get('go').onclick=async function(){
      var data=saveDraft();delete data.media;delete data.featureSources;delete data.auto_describe;
      if(!data.video || !data.character){get('status').textContent='请先上传原视频和替换人物照片。';return;}
      if(!data.prompt.trim() && data.character2 && (!data.source_person.trim() || !data.source_person2.trim())){get('status').textContent='替换两个人时，请分别填写原片中的哪个人。';return;}
      busy=true;sync();get('status').textContent='检查素材与游戏状态…';
      try {var d=await window.api.post('/api/motion/start',data);update(d.task);}
      catch(e){if(active()){busy=false;sync();get('status').textContent=e.message;}}
    };
    get('stop').onclick=async function(){if(!task)return;try{await window.api.post('/api/motion/cancel',{id:task.id});poll();}catch(e){if(active())get('status').textContent=e.message;}};
    sync();poll();
  }};
})();
