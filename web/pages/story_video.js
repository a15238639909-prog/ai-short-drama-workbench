/* Standalone story video: local planning, generation, observation and continuation. */
(function () {
  'use strict';
  var DEFAULT_SEGMENT_SECONDS = 15;
  window.Pages = window.Pages || {};
  window.Pages['story-video'] = { render: function (el) {
    var draft = {}, task = null, timer = null, pending = false, uploading = false;
    var displayedTask = '', outputKey = '', token = {}, ratioSupported = false;
    try { draft = JSON.parse(localStorage.getItem('storyVideoDraft') || '{}'); } catch (_) {}
    el._storyVideoToken = token;
    var esc = window.UI.esc;
    el.innerHTML = '<style>' +
      '.sv-page{max-width:1120px;margin:auto;padding:24px 18px;color:#30342f}.sv-page h2{margin:0 0 8px}.sv-note{color:#73796d;font-size:13px;line-height:1.65;margin:8px 0 14px}' +
      '.sv-grid{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(260px,1fr);gap:18px;margin-top:20px}.sv-card{padding:20px;background:#fffdf8;border:1px solid #dedfd5;border-radius:14px;min-width:0}.sv-card h3{margin:0 0 14px;font-size:16px}' +
      '.sv-page label{display:block;font-size:13px;margin:14px 0 6px}.sv-page textarea,.sv-page input[type=text],.sv-page select{width:100%;box-sizing:border-box}.sv-page textarea{resize:vertical;line-height:1.7}.sv-inline{display:flex;gap:16px;align-items:end}.sv-inline>div{flex:1}' +
      '.sv-image{display:flex;align-items:center;justify-content:center;min-height:170px;max-height:330px;border:1px dashed #c7cfbd;border-radius:10px;background:#f2f4ed;color:#858d7c;text-align:center;padding:12px;overflow:hidden}.sv-image.drag{outline:2px solid #6f8565}.sv-image img{max-width:100%;max-height:300px;object-fit:contain}.sv-page input[type=file]{display:block;width:100%;font-size:12px;margin:12px 0}' +
      '.sv-actions{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:18px}.sv-actions .btn{padding:10px 18px}.sv-status{line-height:1.65;white-space:pre-wrap}.sv-capability{font-size:12px;color:#777d73;margin-top:8px}.sv-progress{height:8px;border-radius:6px;background:#e5e9dd;overflow:hidden;margin:14px 0 8px}.sv-progress i{display:block;height:100%;width:0;background:#758c64;transition:width .2s}' +
      '.sv-stage{font-size:13px;color:#65715a}.sv-observations{list-style:none;padding:0;margin:14px 0 0}.sv-observations li{padding:10px 0;border-top:1px solid #e7e9df;font-size:13px;line-height:1.65;white-space:pre-wrap}.sv-observations strong{color:#526644}.sv-result{margin-top:18px}.sv-result video{width:100%;max-height:620px;background:#111;border-radius:10px}.sv-links{display:flex;gap:18px;flex-wrap:wrap;margin-top:12px}.sv-logs{margin-top:18px}.sv-logs pre{max-height:280px;overflow:auto;white-space:pre-wrap;word-break:break-word;background:#f2f4ed;padding:12px;border-radius:8px;font-size:12px;line-height:1.6}.sv-page [hidden]{display:none!important}' +
      '@media(max-width:800px){.sv-grid{grid-template-columns:1fr}.sv-page{padding:16px 8px}.sv-inline{align-items:stretch;flex-direction:column;gap:0}}</style>' +
      '<section id="sv-page" class="sv-page"><h2>故事视频</h2><p class="sv-note">分两次完成：有首图先看故事，再生成成片；纯文字先看故事和第一段视频，再决定是否延伸。审阅意见供你参考，重做由你选择。</p>' +
      '<div class="sv-actions"><button type="button" id="sv-new" class="btn">新建故事任务</button></div>' +
      '<label for="sv-mode">创作方式</label><select id="sv-mode"><option value="image">首图延伸：先看故事，再生成成片</option><option value="text">纯文字：先看故事和第一段，再延伸成片</option></select>' +
      '<div class="sv-grid"><div class="sv-card"><h3>想拍什么故事</h3><label for="sv-direction">故事方向（必填）</label>' +
      '<textarea id="sv-direction" rows="7" maxlength="12000" placeholder="例如：女修追赶偷走玉环的白狐，却发现山中泉眼出了问题。要有尝试、意外、转折和完整结局。">' + esc(draft.story_direction || '') + '</textarea>' +
      '<div class="sv-inline"><div><label for="sv-count">故事段数</label><select id="sv-count">' + [2,3,4,5,6,7,8,9,10].map(function (n) { return '<option value="' + n + '"' + (n === Number(draft.segment_count || 10) ? ' selected' : '') + '>' + n + ' 段</option>'; }).join('') + '</select></div>' +
      '<div><label for="sv-style">电影风格</label><input id="sv-style" type="text" maxlength="300" value="' + esc(draft.style || '电影质感，动作利落，光影自然') + '"></div></div>' +
      '<div id="sv-ratio-control"><label for="sv-ratio">视频比例</label><select id="sv-ratio"><option value="16:9">16:9 横屏</option><option value="9:16">9:16 竖屏</option><option value="1:1">1:1 方形</option><option value="4:5">4:5 竖屏</option><option value="3:4">3:4 竖屏</option></select><p id="sv-ratio-note" class="sv-note">正在读取比例支持…</p></div>' +
      '<p id="sv-duration" class="sv-note"></p><div class="sv-actions"><button type="button" id="sv-start" class="btn primary">生成故事</button><button type="button" id="sv-pause" class="btn" hidden>暂停</button><button type="button" id="sv-resume" class="btn" hidden>继续</button><button type="button" id="sv-cancel" class="btn" hidden>取消</button><button type="button" id="sv-retry" class="btn" hidden>恢复失败任务</button></div>' +
      '<div id="sv-capability" class="sv-capability" role="status">正在读取本地服务状态…</div></div>' +
      '<div class="sv-card"><h3>起始图（可选）</h3><div id="sv-drop" class="sv-image"><span>可上传图片，也可只写故事方向</span></div>' +
      '<input id="sv-upload" type="file" accept=".png,.jpg,.jpeg,.webp" aria-label="上传起始图"><p class="sv-note">PNG、JPG、WebP，最大20MB。支持图片文件和网页图片拖入，视频画幅跟随首图。</p>' +
      '<div id="sv-upload-status" class="sv-note" role="status"></div><button type="button" id="sv-clear" class="btn small" hidden>清除选择</button></div></div>' +
      '<div id="sv-story-card" class="sv-card" style="margin-top:18px" hidden><h3>查看故事与首段</h3><div id="sv-story"></div><div id="sv-preview"></div><div class="sv-actions"><button id="sv-approve" class="btn primary" hidden>生成成片</button><button id="sv-rewrite" class="btn" hidden>重新写故事</button><label for="sv-redo-segment">重做哪一段<select id="sv-redo-segment"></select></label><button id="sv-redo" class="btn" hidden>重做所选段（后续段随之重接）</button></div></div>' +
      '<div class="sv-card" style="margin-top:18px"><h3>生成进度</h3><div id="sv-status" class="sv-status" role="status">填写故事方向后即可开始。</div><div class="sv-progress"><i id="sv-bar"></i></div><div id="sv-stage" class="sv-stage"></div>' +
      '<ul id="sv-observations" class="sv-observations"></ul><div id="sv-result" class="sv-result" hidden></div><details class="sv-logs"><summary>运行日志</summary><pre id="sv-logs">暂无日志</pre></details></div></section>';

    function get(name) { return el.querySelector('#sv-' + name); }
    get('mode').value=draft.mode||(draft.opening_image?'image':'text');
    get('ratio').value=draft.aspect_ratio||'16:9';
    function active() { return el._storyVideoToken === token && !!get('page') && el.isConnected; }
    function mediaUrl(value) {
      if (typeof value !== 'string' || !value) return '';
      if (value.indexOf('/files/outputs/') === 0) return value;
      var normalized = value.replace(/\\/g, '/');
      var at = normalized.indexOf('/outputs/');
      return at >= 0 ? '/files' + normalized.slice(at) : '';
    }
    function saveDraft() {
      draft.story_direction = get('direction').value;
      draft.segment_count = Number(get('count').value);
      draft.style = get('style').value;
      draft.mode=get('mode').value;
      draft.aspect_ratio=get('ratio').value;
      try { localStorage.setItem('storyVideoDraft', JSON.stringify(draft)); } catch (_) {}
      return {story_direction:draft.story_direction.trim(), input_mode:draft.mode, opening_image:draft.mode==='image'?(draft.opening_image || ''):'',
        segment_count:draft.segment_count, segment_seconds:DEFAULT_SEGMENT_SECONDS, style:draft.style.trim(), aspect_ratio:draft.aspect_ratio};
    }
    function statusName(value) {
      return {queued:'排队中',running:'正在生成',paused:'已暂停',interrupted:'已中断',waiting_user:'等待处理',
        completed:'已完成',failed:'生成失败',cancelled:'已取消',pausing:'正在暂停'}[value] || value || '准备中';
    }
    function locked() { return !!(task && ['queued','running','waiting_user','pausing','paused','interrupted'].indexOf(task.status) >= 0); }
    function sync() {
      var hold = locked(), status = task && task.status;
      get('new').disabled = pending || uploading;
      ['mode','direction','count','style','ratio','upload'].forEach(function (name) { get(name).disabled = hold || pending || uploading; });
      get('ratio').disabled = get('ratio').disabled || !ratioSupported;
      get('start').disabled = hold || pending || uploading;
      get('start').textContent = get('mode').value==='image' ? '生成故事' : '生成故事和第一段视频';
      get('upload').closest('.sv-card').hidden=get('mode').value==='text';
      get('ratio-control').hidden=get('mode').value!=='text';
      get('direction').closest('.sv-card').style.gridColumn=get('mode').value==='text'?'1 / -1':'';
      get('clear').disabled = hold || pending || uploading;
      get('pause').hidden = !task || ['queued','running'].indexOf(status) < 0;
      get('resume').hidden = !task || ['paused','interrupted'].indexOf(status) < 0;
      get('cancel').hidden = !task || ['queued','running','waiting_user','pausing','paused','interrupted'].indexOf(status) < 0;
      get('retry').hidden = !task || ['failed','cancelled'].indexOf(status) < 0;
      ['pause','resume','cancel','retry'].forEach(function (name) { get(name).disabled = pending || uploading; });
      ['approve','rewrite','redo'].forEach(function(name){get(name).disabled=pending||uploading||!!(task&&['running','queued'].includes(status));});
      var seconds = hold && task.inputs ? Number(task.inputs.segment_seconds || DEFAULT_SEGMENT_SECONDS) : DEFAULT_SEGMENT_SECONDS;
      get('duration').textContent = '每段完整' + seconds + '秒，共约' + Number(get('count').value) * seconds + '秒；保留完整时长，用段尾末帧续接，不自动裁短或重做。';
    }
    function preview() {
      var area = get('drop'); area.replaceChildren();
      if (draft.opening_image) {
        var url = mediaUrl(draft.opening_url || draft.opening_image);
        if (url) { var image = document.createElement('img'); image.src = url; image.alt = '已选择的故事起始图'; area.appendChild(image); }
        else { var chosen = document.createElement('span'); chosen.textContent = '已选择：' + (draft.opening_name || '本地图片'); area.appendChild(chosen); }
      } else { var empty = document.createElement('span'); empty.textContent = '可上传图片，也可只写故事方向'; area.appendChild(empty); }
      get('clear').hidden = !draft.opening_image;
      sync();
    }
    function upload(file) {
      if (!file || pending || uploading || locked()) return;
      if (!/\.(png|jpe?g|webp)$/i.test(file.name) || !file.size || file.size > 20 * 1024 * 1024) {
        get('upload-status').textContent = '请选择20MB以内的PNG、JPG或WebP图片。'; return;
      }
      var xhr = new XMLHttpRequest(); uploading = true; sync();
      get('upload-status').textContent = '正在上传…';
      xhr.open('POST', '/api/story-video/upload'); xhr.timeout = 180000;
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name));
      xhr.upload.onprogress = function (event) { if (active() && event.lengthComputable) get('upload-status').textContent = '上传 ' + Math.round(event.loaded / event.total * 100) + '%'; };
      xhr.onload = function () {
        if (!active()) return;
        try {
          var result = JSON.parse(xhr.responseText);
          if (xhr.status !== 200 || !result.ok || !result.data) throw new Error(result.error || '上传失败');
          draft.opening_image = result.data.path; draft.opening_url = result.data.url; draft.opening_name = result.data.name;
          get('mode').value='image';
          saveDraft(); preview(); get('upload-status').textContent = '起始图已就绪';
        } catch (error) { get('upload-status').textContent = error.message; }
      };
      xhr.onerror = xhr.ontimeout = function () { if (active()) get('upload-status').textContent = '上传未完成，请重试；原来的选择仍保留。'; };
      xhr.onloadend = function () { uploading = false; if (active()) { get('upload').value = ''; sync(); } };
      xhr.send(file);
    }
    function textOf(value) {
      if (typeof value === 'string') return value;
      if (!value) return '';
      return value.observed || value.summary || value.message || value.detail || '';
    }
    var storyKey='', previewKey='', segmentRows=[];
    function showSegment(){
      var row=segmentRows.find(function(s){return String(s.index)===get('redo-segment').value;})||segmentRows[0];
      var preview=row&&row.url||'';
      if(preview!==previewKey){previewKey=preview;get('preview').replaceChildren();if(preview){var label=document.createElement('p');label.textContent='第'+row.index+'段视频'+(row.warning?' · '+row.warning:'');get('preview').appendChild(label);var v=document.createElement('video');v.src=preview;v.controls=true;v.preload='metadata';v.style.cssText='width:100%;max-height:480px;background:#111';get('preview').appendChild(v);}}
    }
    function renderStory(payload){
      if(!payload.plan)return;
      var p=payload.plan, r=payload.review||{}, key=JSON.stringify([p,r]);
      get('story-card').hidden=false;
      if(key!==storyKey){
        storyKey=key;
        get('story').innerHTML='<h3>'+esc(p.title)+'</h3><p>'+esc(p.logline)+'</p>'+(p.beats||[]).map(function(b){return '<details open><summary>第'+b.index+'段 · '+esc(b.goal)+'</summary><p>'+esc(b.action)+'</p><p>'+esc((b.dialogue||[]).map(function(d){return d.speaker+'：'+d.text;}).join('　'))+'</p><p class="sv-note">段落结束：'+esc(b.ending)+'</p></details>';}).join('')+'<p><strong>结局：</strong>'+esc(p.ending)+'</p>'+(r.issues&&r.issues.length?'<details><summary>本地AI审阅建议（不阻止生成）</summary>'+r.issues.map(function(i){return '<p>第'+i.beat+'段：'+esc(i.reason)+'<br>'+esc(i.repair_direction)+'</p>';}).join('')+'</details>':'');
      }
      get('approve').hidden=task.status!=='waiting_user'||!['story','preview'].includes(payload.decision);
      get('approve').textContent=payload.decision==='preview'?'继续延伸，生成成片':'故事确认，生成成片';
      get('rewrite').hidden=['running','queued'].includes(task.status);
      var rows=payload.segments||[], selected=get('redo-segment').value; segmentRows=rows;
      get('redo-segment').innerHTML=rows.map(function(s){return '<option value="'+s.index+'">第'+s.index+'段'+(s.warning?'（有重做建议）':'')+'</option>';}).join('');
      if(rows.some(function(r){return String(r.index)===selected;}))get('redo-segment').value=selected;
      get('redo').hidden=!rows.length||['running','queued'].includes(task.status)||(task.inputs||{}).flow_version!==2;
      get('redo-segment').parentElement.hidden=get('redo').hidden;
      showSegment();
    }
    function renderObservations(value) {
      var rows = Array.isArray(value) ? value : value && typeof value === 'object' ? Object.keys(value).sort(function (a,b) { return Number(a)-Number(b); }).map(function (key) { var row=value[key]; return typeof row==='string'?{segment:key,observed:row}:Object.assign({segment:key},row); }) : [];
      var list = get('observations'); list.replaceChildren();
      rows.forEach(function (row, index) {
        var observed = textOf(row.actual_state || row.observation || row.actual || row.review || row);
        if (!observed) return;
        var item = document.createElement('li'), title = document.createElement('strong');
        title.textContent = '第' + (row.segment || row.index || row.n || index + 1) + '段观察：';
        item.appendChild(title); item.appendChild(document.createTextNode(' ' + observed)); list.appendChild(item);
      });
    }
    function renderLogs(logs) {
      if (!Array.isArray(logs)) return;
      get('logs').textContent = logs.slice(-120).map(function (row) {
        if (typeof row === 'string') return row;
        var stamp = row.time || row.at || row.timestamp || '';
        if (typeof stamp === 'number') stamp = new Date(stamp * 1000).toLocaleTimeString();
        return (stamp ? stamp + ' ' : '') + textOf(row);
      }).join('\n') || '暂无日志';
    }
    function renderOutput(output) {
      output = output || {};
      var finalUrl = mediaUrl(output.url || output.compact_url || output.final_url || output.output_path || output.compact_path);
      var rawUrl = mediaUrl(output.raw_url || output.raw_path || output.raw_output_path);
      var manifest = mediaUrl(output.manifest_url || output.manifest_path);
      var key = [finalUrl,rawUrl,manifest].join('|');
      if (!finalUrl || key === outputKey) return;
      outputKey = key;
      var result = get('result'); result.replaceChildren(); result.hidden = false;
      var title = document.createElement('h3'); title.textContent = '故事成片'; result.appendChild(title);
      var video = document.createElement('video'); video.controls = true; video.preload = 'metadata'; video.src = finalUrl; result.appendChild(video);
      var links = document.createElement('div'); links.className = 'sv-links';
      [['打开成片',finalUrl],['完整原片',rawUrl],['生成记录',manifest]].forEach(function (pair) {
        if (!pair[1]) return;
        var link = document.createElement('a'); link.href = pair[1]; link.textContent = pair[0]; link.target = '_blank'; link.rel = 'noopener'; links.appendChild(link);
      });
      result.appendChild(links);
      if (output.review || output.warning) { var note=document.createElement('p'); note.className='sv-note'; note.textContent=output.review || output.warning; result.appendChild(note); }
    }
    function update(payload) {
      if (!active()) return;
      payload = payload || {};
      var next = payload.task || payload;
      if (!next.id && next.task_id) next.id = next.task_id;
      if (!next.id) { task = null; get('status').textContent = '暂无当前任务，填写故事方向后即可开始。'; sync(); return; }
      task = next;
      draft.new_task = false;
      if(displayedTask && displayedTask!==task.id){storyKey='';previewKey='';get('story-card').hidden=true;get('story').replaceChildren();get('preview').replaceChildren();}
      try { localStorage.setItem('storyVideoTask', task.id); } catch (_) {}
      if (displayedTask !== task.id && task.inputs && locked()) {
        displayedTask = task.id;
        var input = task.inputs;
        if (input.story_direction !== undefined) get('direction').value = input.story_direction;
        if (input.segment_count !== undefined) get('count').value = input.segment_count;
        if (input.style !== undefined) get('style').value = input.style;
        get('ratio').value=input.aspect_ratio||'16:9';
        get('mode').value=input.opening_image?'image':'text';
        if (input.opening_image !== undefined) { draft.opening_image = input.opening_image; draft.opening_url = mediaUrl(input.opening_image); }
        saveDraft(); preview();
      }
      var progress = task.progress || {};
      var done = Number(task.done !== undefined ? task.done : progress.done !== undefined ? progress.done : task.completed_segments || (task.checkpoint || {}).completed_segments || 0);
      var total = Number(task.total || progress.total || (task.inputs || {}).segment_count || get('count').value);
      var percent = total > 0 ? Math.min(100,Math.max(0,Math.round(done / total * 100))) : 0;
      if (task.status === 'completed') percent = 100;
      var label = task.step_label || task.stage || task.phase || '';
      var detail = typeof task.detail === 'string' ? task.detail : textOf(task.detail);
      get('status').textContent = task.error || (statusName(task.status) + (label ? '：' + label : '') + (detail && detail !== label ? '\n' + detail : ''));
      get('bar').style.width = percent + '%'; get('stage').textContent = percent + '% · ' + Math.min(done,total) + ' / ' + total;
      var output = payload.output || task.output || {};
      renderObservations(payload.observations || task.observations || output.observations || task.segments || output.segments);
      renderLogs(payload.logs || task.logs || output.logs); renderOutput(output); renderStory(payload); sync();
      clearTimeout(timer);
      if (['running','queued','pausing'].indexOf(task.status) >= 0) timer = setTimeout(poll,2500);
    }
    function poll() {
      if (!active() || (draft.new_task && !task)) return;
      var id = task && task.id;
      if (!id) { try { id = localStorage.getItem('storyVideoTask') || ''; } catch (_) {} }
      window.api.get('/api/story-video/status' + (id ? '?id=' + encodeURIComponent(id) : '')).then(update).catch(function (error) {
        if (!active()) return;
        get('status').textContent = '读取进度失败：' + error.message;
        if (task && ['running','queued','waiting_user','pausing'].indexOf(task.status) >= 0) timer=setTimeout(poll,5000);
      });
    }
    async function action(name) {
      if (!task || pending) return;
      pending = true; sync();
      try { var result=await window.api.post('/api/story-video/action',{id:task.id,action:name,segment:name==='redo'?Number(get('redo-segment').value):undefined}); if(['redo','rewrite'].includes(name)){outputKey='';get('result').hidden=true;get('result').replaceChildren();} update(result); if(['approve','redo','rewrite'].includes(name))poll(); }
      catch (error) { if (active()) get('status').textContent = error.message; }
      finally { pending=false; if(active())sync(); }
    }
    if (el._storyVideoTaskListener) window.removeEventListener('workbench-task-updated', el._storyVideoTaskListener);
    el._storyVideoTaskListener = function (event) {
      if (active() && task && event.detail && event.detail.id === task.id) poll();
    };
    window.addEventListener('workbench-task-updated', el._storyVideoTaskListener);
    get('new').onclick = function () {
      if (pending || uploading) return;
      var fresh = {new_task:true, mode:get('mode').value, segment_count:Number(get('count').value),
        style:get('style').value, aspect_ratio:get('ratio').value};
      try {
        localStorage.setItem('storyVideoDraft', JSON.stringify(fresh));
        localStorage.setItem('storyVideoTask', '');
      } catch (_) { get('status').textContent='新建任务未能保存，请检查浏览器存储后重试。'; return; }
      clearTimeout(timer);
      window.Pages['story-video'].render(el);
    };
    get('start').onclick = async function () {
      if (pending || uploading || locked()) return;
      var data = saveDraft();
      if (!data.story_direction) { get('status').textContent = '请先写下故事方向。'; get('direction').focus(); return; }
      if(data.input_mode==='image'&&!data.opening_image){get('status').textContent='首图模式请先上传或拖入一张图片。';return;}
      if(data.input_mode==='text'&&data.aspect_ratio!=='16:9'&&!ratioSupported){get('status').textContent='请先重启工作台服务，再使用新的视频比例。';return;}
      pending=true; sync(); get('status').textContent='正在检查本地模型与游戏状态…';
      try {
        var result=await window.api.post('/api/story-video/start',data);
        if (active()) { outputKey=''; get('result').replaceChildren(); get('result').hidden=true; update(result); }
      } catch (error) { if(active())get('status').textContent=error.message; }
      finally { pending=false; if(active())sync(); }
    };
    ['pause','resume','cancel','retry','approve','rewrite','redo'].forEach(function (name) { get(name).onclick=function(){action(name);}; });
    get('redo-segment').onchange=showSegment;
    ['mode','direction','count','style','ratio'].forEach(function (name) { get(name).addEventListener('input',function(){saveDraft();sync();}); });
    get('upload').onchange=function(){upload(this.files[0]);};
    ImageDrop.bind(get('drop'),upload,{disabled:function(){return locked()||uploading||pending;},error:function(message){get('upload-status').textContent=message;}});
    get('clear').onclick=function(){if(locked()||pending||uploading)return;draft.opening_image='';draft.opening_url='';draft.opening_name='';get('upload-status').textContent='';saveDraft();preview();};
    preview(); sync(); poll();
    window.api.get('/api/story-video/capabilities').then(function (capabilities) {
      if (!active()) return;
      ratioSupported=Array.isArray(capabilities.aspect_ratios)&&capabilities.aspect_ratios.includes('9:16');
      get('ratio-note').textContent=ratioSupported?'第一段和后续成片使用同一比例。':'新比例尚未加载，请重启工作台服务后使用。';
      sync();
      var message = capabilities.message || capabilities.summary || capabilities.detail;
      get('capability').textContent = typeof message === 'string' ? message : '本地流程已连接；开始前会检查所需模型与运行状态。';
    }).catch(function(error){if(active())get('capability').textContent=error.message;});
  }};
})();
