/* Standalone story video: local planning, generation, observation and continuation. */
(function () {
  'use strict';
  var DEFAULT_SEGMENT_SECONDS = 15;
  var MAX_CHARACTERS = 7;
  var VOICES = ['甜美','温柔','清朗','沉稳','活泼','冷静','沙哑'];
  window.Pages = window.Pages || {};
  window.Pages['story-video'] = { render: function (el) {
    var draft = {}, task = null, timer = null, pending = false, uploading = false;
    var displayedTask = '', outputKey = '', token = {}, ratioSupported = false, referenceSupported = false, referenceDetailSupported = false, referenceDetailChecked = false, runtimeEpoch = 0, clearing = false;
    try { draft = JSON.parse(localStorage.getItem('storyVideoDraft') || '{}'); } catch (_) {}
    if (!draft || typeof draft !== 'object' || Array.isArray(draft)) draft = {};
    draft.character_cards = Array.isArray(draft.character_cards) ? draft.character_cards.slice(0, MAX_CHARACTERS) : [];
    el._storyVideoToken = token;
    var esc = window.UI.esc;
    el.innerHTML = '<style>' +
      '.sv-page{max-width:1120px;margin:auto;padding:24px 18px;color:#30342f}.sv-page h2{margin:0 0 8px}.sv-note{color:#73796d;font-size:13px;line-height:1.65;margin:8px 0 14px}' +
      '.sv-grid{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(260px,1fr);gap:18px;margin-top:20px}.sv-card{padding:20px;background:#fffdf8;border:1px solid #dedfd5;border-radius:14px;min-width:0}.sv-card h3{margin:0 0 14px;font-size:16px}' +
      '.sv-page label{display:block;font-size:13px;margin:14px 0 6px}.sv-page textarea,.sv-page input[type=text],.sv-page select{width:100%;box-sizing:border-box}.sv-page textarea{resize:vertical;line-height:1.7}.sv-inline{display:flex;gap:16px;align-items:end}.sv-inline>div{flex:1}' +
      '.sv-image{display:flex;align-items:center;justify-content:center;min-height:170px;max-height:330px;border:1px dashed #c7cfbd;border-radius:10px;background:#f2f4ed;color:#858d7c;text-align:center;padding:12px;overflow:hidden}.sv-image.drag{outline:2px solid #6f8565}.sv-image img{max-width:100%;max-height:300px;object-fit:contain}.sv-page input[type=file]{display:block;width:100%;font-size:12px;margin:12px 0}' +
      '.sv-actions{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:18px}.sv-actions .btn{padding:10px 18px}.sv-status{line-height:1.65;white-space:pre-wrap}.sv-capability{font-size:12px;color:#777d73;margin-top:8px}.sv-progress{height:8px;border-radius:6px;background:#e5e9dd;overflow:hidden;margin:14px 0 8px}.sv-progress i{display:block;height:100%;width:0;background:#758c64;transition:width .2s}' +
      '.sv-stage{font-size:13px;color:#65715a}.sv-observations{list-style:none;padding:0;margin:14px 0 0}.sv-observations li{padding:10px 0;border-top:1px solid #e7e9df;font-size:13px;line-height:1.65;white-space:pre-wrap}.sv-observations strong{color:#526644}.sv-result{margin-top:18px}.sv-result video{width:100%;max-height:620px;background:#111;border-radius:10px}.sv-links{display:flex;gap:18px;flex-wrap:wrap;margin-top:12px}.sv-logs{margin-top:18px}.sv-logs pre{max-height:280px;overflow:auto;white-space:pre-wrap;word-break:break-word;background:#f2f4ed;padding:12px;border-radius:8px;font-size:12px;line-height:1.6}.sv-page [hidden]{display:none!important}' +
      '.sv-mode-tabs{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:12px 0 6px}.sv-mode-tab{appearance:none;text-align:left;padding:16px;border:1px solid #d8dece;border-radius:12px;background:#fffdf8;color:#343c2d;cursor:pointer;font:inherit}.sv-mode-tab strong{display:block;font-size:15px;margin-bottom:6px}.sv-mode-tab span{display:block;font-size:12px;line-height:1.6;color:#73796d}.sv-mode-tab[aria-selected=true]{border-color:#71885f;background:#edf2e5;box-shadow:inset 0 0 0 1px #71885f}.sv-mode-tab:focus-visible{outline:3px solid #97ad83;outline-offset:2px}.sv-mode-tab:disabled{cursor:default;opacity:.65}' +
      '.sv-person{padding:16px 0;border-top:1px solid #e3e7da}.sv-person:first-child{padding-top:0;border-top:0}.sv-person-head{display:flex;align-items:center;justify-content:space-between;gap:10px}.sv-person-head h4{margin:0;font-size:14px}.sv-person .sv-image{min-height:100px;max-height:180px;margin-top:12px}.sv-person .sv-image img{max-height:160px}.sv-person .sv-inline{gap:10px}.sv-person textarea{min-height:72px}.sv-reference-note{padding:10px 12px;background:#f2f5ed;border-radius:8px}.sv-scene{border-top:1px solid #e3e7da;margin-top:20px;padding-top:18px}.sv-scene h4{font-size:14px;margin:0}.sv-scene .sv-image{min-height:120px;margin-top:12px}.sv-scene .sv-image img{max-height:190px}' +
      '@media(max-width:800px){.sv-grid{grid-template-columns:1fr}.sv-page{padding:16px 8px}.sv-inline{align-items:stretch;flex-direction:column;gap:0}.sv-mode-tab{padding:12px 10px}}@media(max-width:460px){.sv-mode-tabs{grid-template-columns:1fr}.sv-mode-tab strong{margin-bottom:3px}}</style>' +
      '<section id="sv-page" class="sv-page"><h2>故事视频</h2><p class="sv-note">可自动完成本地审稿、生成与验收；也可取消自动完成，先查看故事或首段，再决定是否生成成片。</p>' +
      '<div class="sv-actions"><button type="button" id="sv-new" class="btn">新建故事任务</button></div>' +
      '<label>创作方式</label><select id="sv-mode" aria-label="创作方式" hidden><option value="image">图生视频</option><option value="text">文生视频</option><option value="reference">全能参考</option></select>' +
      '<div id="sv-mode-tabs" class="sv-mode-tabs" role="tablist" aria-label="创作方式"><button type="button" class="sv-mode-tab" role="tab" data-mode="text"><strong>文生视频</strong><span>按剧情衔接，注重电影化分镜与动态画面。</span></button><button type="button" class="sv-mode-tab" role="tab" data-mode="image"><strong>图生视频</strong><span>优先延续当前场景与人物，段尾自然保留面部。</span></button><button type="button" class="sv-mode-tab" role="tab" data-mode="reference"><strong>全能参考</strong><span>分别设置人物，参考初始场景讲故事。</span></button></div>' +
      '<div class="sv-grid"><div class="sv-card"><h3>想拍什么故事</h3><div id="sv-story-input-control" hidden><label for="sv-story-input">故事来源</label><select id="sv-story-input"><option value="story">我有完整故事，忠实改编</option><option value="idea">我提供想法，本地 AI 先写故事</option></select><p id="sv-story-input-note" class="sv-note"></p></div><label id="sv-direction-label" for="sv-direction">故事方向（必填）</label>' +
      '<textarea id="sv-direction" rows="7" maxlength="12000" placeholder="例如：女修追赶偷走玉环的白狐，却发现山中泉眼出了问题。要有尝试、意外、转折和完整结局。">' + esc(draft.story_direction || '') + '</textarea>' +
      '<div class="sv-inline"><div><label for="sv-count">故事段数</label><select id="sv-count">' + [2,3,4,5,6,7,8,9,10].map(function (n) { return '<option value="' + n + '"' + (n === Number(draft.segment_count || 10) ? ' selected' : '') + '>' + n + ' 段</option>'; }).join('') + '</select></div>' +
      '<div><label for="sv-style">电影风格</label><input id="sv-style" type="text" maxlength="300" value="' + esc(draft.style || '电影质感，动作利落，光影自然') + '"></div></div>' +
      '<label for="sv-automatic"><input id="sv-automatic" type="checkbox"' + (draft.quality_mode==='guided'?'':' checked') + '> 自动完成整片（本地审稿与验收）</label><p id="sv-quality-note" class="sv-note"></p>' +
      '<label for="sv-audio-review"><input id="sv-audio-review" type="checkbox"' + (draft.audio_review_enabled===true?' checked':'') + '> 语音核对（可选，增加耗时）</label><p id="sv-audio-review-note" class="sv-note"></p>' +
      '<div id="sv-ratio-control"><label for="sv-ratio">视频比例</label><select id="sv-ratio"><option value="16:9">16:9 横屏</option><option value="9:16">9:16 竖屏</option><option value="1:1">1:1 方形</option><option value="4:5">4:5 竖屏</option><option value="3:4">3:4 竖屏</option></select><p id="sv-ratio-note" class="sv-note">正在读取比例支持…</p></div>' +
      '<p id="sv-duration" class="sv-note"></p><div class="sv-actions"><button type="button" id="sv-start" class="btn primary">生成故事</button><button type="button" id="sv-pause" class="btn" hidden>暂停</button><button type="button" id="sv-resume" class="btn" hidden>继续</button><button type="button" id="sv-cancel" class="btn" hidden>取消</button><button type="button" id="sv-retry" class="btn" hidden>恢复失败任务</button></div>' +
      '<div id="sv-capability" class="sv-capability" role="status">正在读取本地服务状态…</div></div>' +
      '<div id="sv-opening-card" class="sv-card"><h3>开场图片</h3><div id="sv-drop" class="sv-image"><span>可上传图片，也可只写故事方向</span></div>' +
      '<input id="sv-upload" type="file" accept=".png,.jpg,.jpeg,.webp" aria-label="上传起始图"><p class="sv-note">PNG、JPG、WebP，最大20MB。支持图片文件和网页图片拖入，视频画幅跟随首图。建议主要人物面部清晰可见；后续以上一段实际末帧续接，不能保证完全锁脸。</p>' +
      '<div id="sv-upload-status" class="sv-note" role="status"></div><button type="button" id="sv-clear" class="btn small" hidden>清除选择</button></div>' +
      '<div id="sv-reference-card" class="sv-card" hidden><h3>人物与初始场景</h3><p class="sv-note sv-reference-note">每个人物分别上传一张人设图或照片，并填写名字、性格与声音。最多 7 位人物。</p><div id="sv-reference-detail-control"><label for="sv-reference-image-size">参考图精度</label><select id="sv-reference-image-size"><option value="match">标准（较快，默认）</option><option id="sv-reference-size-max" value="max" disabled>高清（保留更多参考细节，较慢且占更多显存）</option></select><p id="sv-reference-detail-note" class="sv-note"></p></div><div id="sv-characters"></div><button type="button" id="sv-add-character" class="btn small">＋ 添加人物</button><div id="sv-reference-status" class="sv-note" role="status"></div>' +
      '<div class="sv-scene"><h4>初始场景（可选）</h4><div id="sv-scene-preview" class="sv-image"><span>不上传时，由故事和风格建立初始场景</span></div><input id="sv-scene-upload" type="file" accept=".png,.jpg,.jpeg,.webp" aria-label="上传初始场景"><button type="button" id="sv-scene-clear" class="btn small" hidden>移除场景选择</button><p class="sv-note">场景图只用于第一段。后续使用人物图与上一段实际末帧衔接，地点随故事自然发展。移除选择会保留已上传文件。</p></div></div></div>' +
      '<div id="sv-story-card" class="sv-card" style="margin-top:18px" hidden><h3>查看故事与视频</h3><div id="sv-story"></div><div id="sv-preview"></div><div class="sv-actions"><button id="sv-approve" class="btn primary" hidden>生成成片</button><button id="sv-rewrite" class="btn" hidden>重新写故事</button><label for="sv-redo-segment">重做哪一段<select id="sv-redo-segment"></select></label><button id="sv-redo" class="btn" hidden>重做所选段（后续段随之重接）</button></div></div>' +
      '<div class="sv-card" style="margin-top:18px"><h3>生成进度</h3><div id="sv-status" class="sv-status" role="status">填写故事方向后即可开始。</div><p id="sv-quality-status" class="sv-note" role="status" hidden></p><div class="sv-progress"><i id="sv-bar"></i></div><div id="sv-stage" class="sv-stage"></div>' +
      '<ul id="sv-observations" class="sv-observations"></ul><div id="sv-result" class="sv-result" hidden></div><details class="sv-logs"><summary>运行日志</summary><pre id="sv-logs">暂无日志</pre></details></div></section>';

    function get(name) { return el.querySelector('#sv-' + name); }
    get('mode').value=['image','text','reference'].includes(draft.mode)?draft.mode:(draft.opening_image?'image':'text');
    get('ratio').value=draft.aspect_ratio||'16:9';
    get('story-input').value=draft.story_input==='idea'?'idea':'story';
    get('audio-review').checked=draft.audio_review_enabled===true;
    get('reference-image-size').value=draft.reference_image_size==='max'?'max':'match';
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
      draft.quality_mode=get('automatic').checked?'automatic':'guided';
      draft.story_input=get('story-input').value;
      draft.audio_review_enabled=get('audio-review').checked;
      draft.reference_image_size=get('reference-image-size').value==='max'?'max':'match';
      try { localStorage.setItem('storyVideoDraft', JSON.stringify(draft)); } catch (_) {}
      var data = {story_direction:draft.story_direction.trim(), input_mode:draft.mode, opening_image:draft.mode==='image'?(draft.opening_image || ''):'',
        segment_count:draft.segment_count, segment_seconds:DEFAULT_SEGMENT_SECONDS, style:draft.style.trim(), aspect_ratio:draft.aspect_ratio, quality_mode:draft.quality_mode,
        audio_review_enabled:draft.audio_review_enabled};
      if(draft.mode==='reference'){
        data.character_cards=draft.character_cards.map(function(card){return {id:card.id,image_path:card.image_path||'',name:(card.name||'').trim(),personality:(card.personality||'').trim(),voice:(card.voice||'').trim()};});
        data.initial_scene=draft.initial_scene||'';
        data.story_input=draft.story_input;
        data.reference_image_size=draft.reference_image_size;
      }
      return data;
    }
    function statusName(value) {
      return {queued:'排队中',running:'正在生成',paused:'已暂停',interrupted:'已中断',waiting_user:'等待处理',
        completed:'已完成',failed:'生成失败',cancelled:'已取消',pausing:'正在暂停'}[value] || value || '准备中';
    }
    function invalidatePoll() {
      el._storyVideoPollSequence = (el._storyVideoPollSequence || 0) + 1;
      clearTimeout(timer);
    }
    function locked() { return !!(task && ['queued','running','waiting_user','pausing','paused','interrupted'].indexOf(task.status) >= 0); }
    function taskAutomatic() { return !!(task && (task.quality_mode || (task.inputs || {}).quality_mode)==='automatic'); }
    function sync() {
      var hold = clearing || locked(), status = task && task.status;
      var automatic=locked()?taskAutomatic():get('automatic').checked, mode=get('mode').value, reference=mode==='reference', blocked=hold||pending||uploading;
      get('new').disabled = pending || uploading;
      ['mode','direction','count','style','ratio','upload','automatic','audio-review','story-input','reference-image-size','scene-upload','scene-clear'].forEach(function (name) { get(name).disabled = blocked; });
      get('mode-tabs').querySelectorAll('[data-mode]').forEach(function(button){var selected=button.dataset.mode===mode;button.disabled=blocked;button.setAttribute('aria-selected',selected?'true':'false');button.tabIndex=selected?0:-1;});
      get('characters').querySelectorAll('input,textarea,select,button').forEach(function(control){control.disabled=blocked;});
      get('add-character').disabled=blocked||draft.character_cards.length>=MAX_CHARACTERS;
      get('ratio').disabled = get('ratio').disabled || !ratioSupported;
      get('start').disabled = hold || pending || uploading;
      get('start').textContent = automatic?'自动生成整片':(mode==='text' ? '生成故事和第一段视频' : '先看故事');
      get('quality-note').textContent=reference?(automatic?'新自动任务先有限修复；审稿、观察报告不可用、未通过时，记录提醒后继续。每段生成一次，无自动补拍。提示词、素材、视频或生成服务不可用时仍会停止。':'先查看故事，确认后再生成成片。审阅建议会保留，由你决定继续或重做。'):(automatic?'新自动任务先有限修复；审稿及画面核对结果供参考，有提醒也继续生成，不因质量建议中断或自动补拍。':'有首图先看故事；纯文字先看故事和第一段。审阅建议供参考，由你决定继续或重做。');
      get('audio-review-note').textContent=get('audio-review').checked?'逐段识别语音并核对台词，会增加耗时。核对结果供参考，视频声音按原设置生成。':'跳过语音识别核对，视频声音按原设置生成。';
      get('opening-card').hidden=mode!=='image';
      get('reference-card').hidden=!reference;
      get('reference-detail-control').hidden=!reference;
      get('reference-size-max').disabled=!referenceDetailSupported;
      get('reference-detail-note').textContent=(referenceDetailSupported?'高清会保留更多参考细节。':referenceDetailChecked?'高清参考需重启工作台后使用，标准参考可正常使用。':'正在读取高清参考支持，当前可使用标准参考。')+'应用于本段使用的全部参考图，仍不能保证人物与人设图完全一致。';
      get('story-input-control').hidden=!reference;
      get('direction-label').textContent=reference?(get('story-input').value==='idea'?'故事想法（必填）':'完整故事（必填）'):'故事方向（必填）';
      get('story-input-note').textContent=get('story-input').value==='idea'?'本地 AI 先写完整故事，再按正文安排分镜与生成。':'按你提供的完整故事安排分镜，保留情节、说话人和原台词。';
      get('ratio-control').hidden=mode==='image';
      get('direction').closest('.sv-card').style.gridColumn=mode==='text'?'1 / -1':'';
      get('clear').disabled = hold || pending || uploading;
      get('pause').hidden = !task || ['queued','running'].indexOf(status) < 0;
      get('resume').hidden = !task || ['paused','interrupted'].indexOf(status) < 0;
      get('cancel').hidden = !task || ['queued','running','waiting_user','pausing','paused','interrupted'].indexOf(status) < 0;
      get('retry').hidden = !task || ['failed','cancelled'].indexOf(status) < 0;
      ['pause','resume','cancel','retry'].forEach(function (name) { get(name).disabled = pending || uploading; });
      ['approve','rewrite','redo'].forEach(function(name){get(name).disabled=pending||uploading||!!(task&&['running','queued'].includes(status));});
      var seconds = hold && task && task.inputs ? Number(task.inputs.segment_seconds || DEFAULT_SEGMENT_SECONDS) : DEFAULT_SEGMENT_SECONDS;
      get('duration').textContent = '每段完整' + seconds + '秒，共约' + Number(get('count').value) * seconds + '秒；保留完整时长，用段尾末帧续接。' + (reference?'人物图持续参考，场景图只用于首段。':automatic?'新自动任务每段生成一次，审稿提醒保留后继续。':'不自动裁短或重做。');
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
    function uploadImage(file, statusNode, inputNode, accept) {
      if (!file || clearing || pending || uploading || locked()) return;
      if (!/\.(png|jpe?g|webp)$/i.test(file.name) || !file.size || file.size > 20 * 1024 * 1024) {
        statusNode.textContent = '请选择20MB以内的PNG、JPG或WebP图片。'; return;
      }
      var xhr = new XMLHttpRequest(), epoch=runtimeEpoch; uploading = true; sync();
      statusNode.textContent = '正在上传…';
      xhr.open('POST', '/api/story-video/upload'); xhr.timeout = 180000;
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name));
      xhr.upload.onprogress = function (event) { if (active() && epoch===runtimeEpoch && event.lengthComputable) statusNode.textContent = '上传 ' + Math.round(event.loaded / event.total * 100) + '%'; };
      xhr.onload = function () {
        if (!active() || epoch!==runtimeEpoch || clearing) return;
        try {
          var result = JSON.parse(xhr.responseText);
          if (xhr.status !== 200 || !result.ok || !result.data) throw new Error(result.error || '上传失败');
          accept(result.data);
          saveDraft(); statusNode.textContent = '图片已就绪';
        } catch (error) { statusNode.textContent = error.message; }
      };
      xhr.onerror = xhr.ontimeout = function () { if (active() && epoch===runtimeEpoch) statusNode.textContent = '上传未完成，请重试；原来的选择仍保留。'; };
      xhr.onloadend = function () { uploading = false; if (active()) { if(inputNode)inputNode.value = ''; sync(); } };
      xhr.send(file);
    }
    function upload(file) {
      uploadImage(file,get('upload-status'),get('upload'),function(result){
        draft.opening_image=result.path;draft.opening_url=result.url;draft.opening_name=result.name;
        get('mode').value='image';preview();
      });
    }
    function imageMarkup(path, url, name, empty) {
      var src=mediaUrl(url||path);
      return src?'<img src="'+esc(src)+'" alt="'+esc(name||'已选择的参考图片')+'">':'<span>'+esc(path?('已选择：'+(name||'本地图片')):empty)+'</span>';
    }
    function renderReferences() {
      get('characters').innerHTML=draft.character_cards.map(function(card,index){
        return '<section class="sv-person" data-character="'+esc(card.id)+'"><div class="sv-person-head"><h4>人物 '+(index+1)+'</h4><button type="button" class="btn small" data-remove>移除人物</button></div>'+
          '<div class="sv-image">'+imageMarkup(card.image_path,card.image_url,card.image_name,'上传这位人物的人设图或照片')+'</div>'+
          '<input type="file" data-character-upload accept=".png,.jpg,.jpeg,.webp" aria-label="上传人物 '+(index+1)+' 图片">'+
          '<label>名字<input type="text" data-field="name" maxlength="60" value="'+esc(card.name||'')+'" placeholder="故事里怎么称呼这位人物"></label>'+
          '<label>性格<textarea data-field="personality" rows="2" maxlength="600" placeholder="例如：好奇、有耐心，遇到困难会先观察">'+esc(card.personality||'')+'</textarea></label>'+
          '<label>声音描述<select data-voice-preset aria-label="人物 '+(index+1)+' 声音便选"><option value="">选择声线，或在下面自己描述</option>'+VOICES.map(function(voice){return '<option value="'+voice+'">'+voice+'</option>';}).join('')+'</select></label>'+
          '<input type="text" data-field="voice" maxlength="300" value="'+esc(card.voice||'')+'" aria-label="人物 '+(index+1)+' 自定义声音描述" placeholder="用中文写声线、语速和说话感觉">'+
          '</section>';
      }).join('');
      get('characters').querySelectorAll('[data-character]').forEach(function(row){
        var card=draft.character_cards.find(function(item){return item.id===row.dataset.character;});
        row.querySelectorAll('[data-field]').forEach(function(field){field.addEventListener('input',function(){card[field.dataset.field]=field.value;saveDraft();});});
        row.querySelector('[data-voice-preset]').onchange=function(){
          if(!this.value)return;
          card.voice=this.value+'的声线，咬字清晰，语速自然';
          row.querySelector('[data-field="voice"]').value=card.voice;saveDraft();
        };
        row.querySelector('[data-remove]').onclick=function(){
          if(clearing||locked()||pending||uploading)return;
          draft.character_cards=draft.character_cards.filter(function(item){return item.id!==card.id;});
          saveDraft();renderReferences();get('reference-status').textContent='已取消人物选择，上传文件仍保留。';
        };
        var input=row.querySelector('[data-character-upload]');
        input.onchange=function(){uploadImage(this.files[0],get('reference-status'),input,function(result){
          card.image_path=result.path;card.image_url=result.url;card.image_name=result.name;renderReferences();
        });};
      });
      get('scene-preview').innerHTML=imageMarkup(draft.initial_scene,draft.initial_scene_url,draft.initial_scene_name,'不上传时，由故事和风格建立初始场景');
      get('scene-clear').hidden=!draft.initial_scene;
      sync();
    }
    function addCharacter() {
      if(clearing||locked()||pending||uploading||draft.character_cards.length>=MAX_CHARACTERS)return;
      var highest=draft.character_cards.reduce(function(value,card){var match=/^char_(\d+)$/.exec(card.id||'');return Math.max(value,match?Number(match[1]):0);},Number(draft.next_character_id||0));
      draft.next_character_id=highest+1;
      draft.character_cards.push({id:'char_'+String(draft.next_character_id).padStart(2,'0'),image_path:'',name:'',personality:'',voice:''});
      saveDraft();renderReferences();
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
      var sourceStory=(task.inputs||{}).input_mode==='reference'&&typeof payload.source_story==='string'?payload.source_story:'';
      if(!payload.plan&&!sourceStory)return;
      var p=payload.plan, r=payload.review||{}, automatic=taskAutomatic(), key=JSON.stringify([p,r,automatic,sourceStory]);
      get('story-card').hidden=false;
      if(key!==storyKey){
        storyKey=key;
        get('story').innerHTML=p?'<h3>'+esc(p.title)+'</h3><p>'+esc(p.logline)+'</p>'+(p.beats||[]).map(function(b){return '<details open><summary>第'+b.index+'段 · '+esc(b.goal)+'</summary><p>'+esc(b.action)+'</p><p>'+esc((b.dialogue||[]).map(function(d){return d.speaker+'：'+d.text;}).join('　'))+'</p><p class="sv-note">段落结束：'+esc(b.ending)+'</p></details>';}).join('')+'<p><strong>结局：</strong>'+esc(p.ending)+'</p>'+(r.issues&&r.issues.length?'<details><summary>'+ (automatic?'本地AI审稿记录':'本地AI审阅建议（供参考）') +'</summary>'+r.issues.map(function(i){return '<p>第'+i.beat+'段：'+esc(i.reason)+'<br>'+esc(i.repair_direction)+'</p>';}).join('')+'</details>':''):'';
        if(sourceStory){
          var details=document.createElement('details'), summary=document.createElement('summary'), prose=document.createElement('pre');
          summary.textContent='完整故事正文';
          prose.textContent=sourceStory;
          prose.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;line-height:1.8';
          details.appendChild(summary);details.appendChild(prose);get('story').prepend(details);
        }
      }
      get('approve').hidden=automatic||task.status!=='waiting_user'||!['story','preview'].includes(payload.decision);
      get('approve').textContent=payload.decision==='preview'?'继续延伸，生成成片':'故事确认，生成成片';
      get('rewrite').hidden=['running','queued'].includes(task.status);
      var rows=payload.segments||[], selected=get('redo-segment').value; segmentRows=rows;
      get('redo-segment').innerHTML=rows.map(function(s){return '<option value="'+s.index+'">第'+s.index+'段'+(s.warning?'（有重做建议）':'')+'</option>';}).join('');
      if(rows.some(function(r){return String(r.index)===selected;}))get('redo-segment').value=selected;
      get('redo').hidden=!rows.length||['running','queued'].includes(task.status)||Number((task.inputs||{}).flow_version||1)<2;
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
      if (!next.id) { task = null; get('quality-status').hidden=true; get('quality-status').textContent=''; get('status').textContent = '暂无当前任务，填写故事方向后即可开始。'; sync(); return; }
      task = next;
      if(payload.quality_mode)task.quality_mode=payload.quality_mode;
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
        get('mode').value=['image','text','reference'].includes(input.input_mode)?input.input_mode:(input.opening_image?'image':'text');
        get('automatic').checked=taskAutomatic();
        get('audio-review').checked=input.audio_review_enabled!==false;
        get('reference-image-size').value=input.reference_image_size==='max'?'max':'match';
        if (input.opening_image !== undefined) { draft.opening_image = input.opening_image; draft.opening_url = mediaUrl(input.opening_image); }
        if(Array.isArray(input.character_cards))draft.character_cards=input.character_cards.map(function(card){return Object.assign({},card,{image_url:mediaUrl(card.image_path)});});
        if(input.initial_scene!==undefined){draft.initial_scene=input.initial_scene;draft.initial_scene_url=mediaUrl(input.initial_scene);}
        get('story-input').value=input.story_input==='idea'?'idea':'story';
        saveDraft(); preview();renderReferences();
      }
      var progress = task.progress || {};
      var done = Number(task.done !== undefined ? task.done : progress.done !== undefined ? progress.done : task.completed_segments || (task.checkpoint || {}).completed_segments || 0);
      var total = Number(task.total || progress.total || (task.inputs || {}).segment_count || get('count').value);
      var percent = total > 0 ? Math.min(100,Math.max(0,Math.round(done / total * 100))) : 0;
      if (task.status === 'completed') percent = 100;
      else percent = Math.min(99, percent);
      var label = task.step_label || task.stage || task.phase || '';
      var detail = typeof task.detail === 'string' ? task.detail : textOf(task.detail);
      get('status').textContent = task.error || (statusName(task.status) + (label ? '：' + label : '') + (detail && detail !== label ? '\n' + detail : ''));
      var quality=payload.quality_summary||task.quality_summary||{}, reasons=Array.isArray(quality.reasons)?quality.reasons.map(textOf).join('；'):textOf(quality.reasons||'');
      var completedWithWarnings=quality.status==='completed_with_warnings'&&quality.generation_completed===true, workingWithWarnings=quality.status==='working_with_warnings';
      var warningCount=Array.isArray(quality.warnings)?quality.warnings.length:0;
      get('quality-status').hidden=quality.passed===undefined&&!reasons&&!completedWithWarnings&&!workingWithWarnings;
      get('quality-status').textContent=(quality.status==='failed'?'本地流程已中断':completedWithWarnings?'生成完成（有审稿提醒）':workingWithWarnings?'已保留审稿提醒，继续生成':quality.passed===true?'本地验收通过':quality.passed===false?'本地验收未通过':'本地验收')+((completedWithWarnings||workingWithWarnings)&&warningCount?' · '+warningCount+'条提醒':'')+(reasons?'：'+reasons:'');
      get('bar').style.width = percent + '%'; get('stage').textContent = percent + '% · ' + Math.min(done,total) + ' / ' + total;
      var output = payload.output || task.output || {};
      renderObservations(payload.observations || task.observations || output.observations || task.segments || output.segments);
      renderLogs(payload.logs || task.logs || output.logs); renderOutput(output); renderStory(payload); sync();
      clearTimeout(timer);
      if (['running','queued','pausing'].indexOf(task.status) >= 0) timer = setTimeout(poll,2500);
    }
    function poll() {
      if (!active() || clearing || (draft.new_task && !task)) return;
      var epoch=runtimeEpoch;
      var id = task && task.id;
      if (!id) { try { id = localStorage.getItem('storyVideoTask') || ''; } catch (_) {} }
      var sequence = (el._storyVideoPollSequence || 0) + 1;
      el._storyVideoPollSequence = sequence;
      function current() { return epoch===runtimeEpoch && sequence===el._storyVideoPollSequence && (!task || !id || task.id===id); }
      window.api.get('/api/story-video/status' + (id ? '?id=' + encodeURIComponent(id) : '')).then(function(payload){if(current())update(payload);}).catch(function (error) {
        if (!active()) return;
        if(!current())return;
        get('status').textContent = '读取进度失败：' + error.message;
        if (!task || ['running','queued','waiting_user','pausing'].indexOf(task.status) >= 0) timer=setTimeout(poll,5000);
      });
    }
    async function action(name) {
      if (!task || pending || clearing) return;
      var epoch=runtimeEpoch;
      invalidatePoll();
      pending = true; sync();
      try { var result=await window.api.post('/api/story-video/action',{id:task.id,action:name,segment:name==='redo'?Number(get('redo-segment').value):undefined}); if(epoch!==runtimeEpoch)return; if(['redo','rewrite'].includes(name)){outputKey='';get('result').hidden=true;get('result').replaceChildren();} update(result); if(['approve','redo','rewrite'].includes(name))poll(); }
      catch (error) { if (active()&&epoch===runtimeEpoch) { get('status').textContent = error.message; poll(); } }
      finally { if(epoch===runtimeEpoch){pending=false; if(active())sync();} }
    }
    if(el._storyVideoResetListener)window.removeEventListener('workbench-runtime-reset',el._storyVideoResetListener);
    el._storyVideoResetListener=function(event){
      if(!active())return;
      var phase=event.detail&&event.detail.phase;
      if(phase==='begin'){runtimeEpoch++;clearing=true;pending=true;clearTimeout(timer);}
      else{clearing=false;pending=false;if(phase==='done'){
        task=null;displayedTask='';draft.new_task=true;saveDraft();
        try{localStorage.setItem('storyVideoTask','');}catch(_){}
        get('status').textContent='空闲 · 任务已清理，故事、素材和成品保留。';
        get('quality-status').hidden=true;get('quality-status').textContent='';
        get('bar').style.width='0%';get('stage').textContent='0% · 0 / 0';
        ['pause','resume','cancel','retry','approve','redo','rewrite'].forEach(function(name){get(name).hidden=true;});
      }}
      sync();
      if(phase==='failed')poll();
    };
    window.addEventListener('workbench-runtime-reset',el._storyVideoResetListener);
    if (el._storyVideoTaskListener) window.removeEventListener('workbench-task-updated', el._storyVideoTaskListener);
    el._storyVideoTaskListener = function (event) {
      if (active() && task && event.detail && event.detail.id === task.id) poll();
    };
    window.addEventListener('workbench-task-updated', el._storyVideoTaskListener);
    get('new').onclick = function () {
      if (pending || uploading) return;
      var fresh = {new_task:true, mode:get('mode').value, segment_count:Number(get('count').value),
        style:get('style').value, aspect_ratio:get('ratio').value, quality_mode:'automatic',story_input:get('story-input').value,
        audio_review_enabled:false,reference_image_size:'match'};
      try {
        localStorage.setItem('storyVideoDraft', JSON.stringify(fresh));
        localStorage.setItem('storyVideoTask', '');
      } catch (_) { get('status').textContent='新建任务未能保存，请检查浏览器存储后重试。'; return; }
      clearTimeout(timer);
      window.Pages['story-video'].render(el);
    };
    get('start').onclick = async function () {
      var epoch=runtimeEpoch;if(clearing)return;
      if (pending || uploading || locked()) return;
      var data = saveDraft();
      if (!data.story_direction) { get('status').textContent = '请先写下故事方向。'; get('direction').focus(); return; }
      if(data.input_mode==='image'&&!data.opening_image){get('status').textContent='首图模式请先上传或拖入一张图片。';return;}
      if(data.input_mode==='reference'){
        if(!referenceSupported){get('status').textContent='全能参考服务尚未就绪，请重新加载工作台服务后再试。';return;}
        if(data.reference_image_size==='max'&&!referenceDetailSupported){get('status').textContent=referenceDetailChecked?'高清参考需重启工作台后使用；可选择标准参考。':'尚未确认高清参考支持，请等待服务状态加载，或选择标准参考。';return;}
        if(!data.character_cards.length||data.character_cards.length>MAX_CHARACTERS){get('status').textContent='请添加 1～7 位人物，并为每位人物上传图片。';return;}
        for(var i=0;i<data.character_cards.length;i++){
          var card=data.character_cards[i];
          if(!card.image_path||!card.name||!card.personality||!card.voice){get('status').textContent='请补全人物 '+(i+1)+' 的图片、名字、性格和声音描述。';return;}
          if(!/[\u4e00-\u9fff]/.test(card.voice)){get('status').textContent='请用中文填写人物 '+(i+1)+' 的声音描述。';return;}
        }
        if(new Set(data.character_cards.map(function(card){return card.name;})).size!==data.character_cards.length){get('status').textContent='每位人物请使用不同的名字，方便准确对应台词。';return;}
      }
      if(data.input_mode!=='image'&&data.aspect_ratio!=='16:9'&&!ratioSupported){get('status').textContent='请先重启工作台服务，再使用新的视频比例。';return;}
      invalidatePoll();
      pending=true; sync(); get('status').textContent='正在检查本地模型与游戏状态…';
      try {
        var result=await window.api.post('/api/story-video/start',data);
        if (active()&&epoch===runtimeEpoch) { outputKey=''; get('result').replaceChildren(); get('result').hidden=true; update(result); }
      } catch (error) { if(active()&&epoch===runtimeEpoch){get('status').textContent=error.message;poll();} }
      finally { if(epoch===runtimeEpoch){pending=false; if(active())sync();} }
    };
    ['pause','resume','cancel','retry','approve','rewrite','redo'].forEach(function (name) { get(name).onclick=function(){action(name);}; });
    get('redo-segment').onchange=showSegment;
    ['mode','direction','count','style','ratio','automatic','audio-review','story-input','reference-image-size'].forEach(function (name) { get(name).addEventListener('input',function(){saveDraft();sync();}); });
    var modeButtons=Array.from(get('mode-tabs').querySelectorAll('[data-mode]'));
    function chooseMode(button){if(clearing||locked()||pending||uploading)return;get('mode').value=button.dataset.mode;saveDraft();sync();}
    modeButtons.forEach(function(button,index){
      button.onclick=function(){chooseMode(button);};
      button.addEventListener('keydown',function(event){
        var next=event.key==='ArrowRight'?(index+1)%modeButtons.length:event.key==='ArrowLeft'?(index+modeButtons.length-1)%modeButtons.length:event.key==='Home'?0:event.key==='End'?modeButtons.length-1:-1;
        if(next<0||clearing||locked()||pending||uploading)return;
        event.preventDefault();chooseMode(modeButtons[next]);modeButtons[next].focus();
      });
    });
    get('add-character').onclick=addCharacter;
    get('scene-upload').onchange=function(){uploadImage(this.files[0],get('reference-status'),get('scene-upload'),function(result){
      draft.initial_scene=result.path;draft.initial_scene_url=result.url;draft.initial_scene_name=result.name;renderReferences();
    });};
    get('scene-clear').onclick=function(){if(clearing||locked()||pending||uploading)return;draft.initial_scene='';draft.initial_scene_url='';draft.initial_scene_name='';saveDraft();renderReferences();get('reference-status').textContent='已取消场景选择，上传文件仍保留。';};
    get('upload').onchange=function(){upload(this.files[0]);};
    ImageDrop.bind(get('drop'),upload,{disabled:function(){return locked()||uploading||pending;},error:function(message){get('upload-status').textContent=message;}});
    get('clear').onclick=function(){if(locked()||pending||uploading)return;draft.opening_image='';draft.opening_url='';draft.opening_name='';get('upload-status').textContent='';saveDraft();preview();};
    preview();renderReferences();sync();poll();
    window.api.get('/api/story-video/capabilities').then(function (capabilities) {
      if (!active()) return;
      ratioSupported=Array.isArray(capabilities.aspect_ratios)&&capabilities.aspect_ratios.includes('9:16');
      referenceSupported=capabilities.reference_mode===true||(Array.isArray(capabilities.inputs)&&capabilities.inputs.includes('reference'));
      referenceDetailSupported=Array.isArray(capabilities.reference_image_sizes)&&capabilities.reference_image_sizes.includes('max');
      referenceDetailChecked=true;
      get('ratio-note').textContent=ratioSupported?'第一段和后续成片使用同一比例。':'新比例尚未加载，请重启工作台服务后使用。';
      sync();
      var message = capabilities.message || capabilities.summary || capabilities.detail;
      get('capability').textContent = typeof message === 'string' ? message : '本地流程已连接；开始前会检查所需模型与运行状态。';
    }).catch(function(error){if(active())get('capability').textContent=error.message;});
  }};
})();
