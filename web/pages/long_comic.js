(function () {
  'use strict';
  var PREFIX = '/api/long-comic';
  var ACTIVE = ['queued', 'running', 'pausing'];
  var SELECTED = 'longComicSelected';
  var DRAFTS = 'longComicDrafts';
  window.Pages = window.Pages || {};
  window.Pages['long-comic'] = { render: async function (host) {
    if (host._longComicDispose) host._longComicDispose();
    var token = {}, project = null, projects = [], chapterNumber = 1, pageNumber = 1;
    var timer = null, epoch = 0, sequence = 0, pending = false, disposed = false, clearing = false, stale = false;
    var projectDirty = false, planDirty = false, promptDirty = false, editorKey = '', galleryKey = '', drafts = {};
    var sources = [], sourceCharacters = [], sourceStoryId = '', sourceCharacterIds = [], sourceSequence = 0, sourceLoading = false, sourceError = '', editingPanels = [];
    var referenceKey = '', repairKey = '', repairVersionsKey = '', repairPeopleKey = '', repairRegion = null, repairDrag = null;
    var repairSize = {width: 0, height: 0}, repairPeople = [], repairPreview = null, repairView = 'current';
    var repairAwaiting = null;
    var selected = localStorage.getItem(SELECTED) || '';
    var esc = window.UI.esc;
    try { drafts = JSON.parse(localStorage.getItem(DRAFTS) || '{}'); } catch (error) {}
    if (!drafts || typeof drafts !== 'object' || Array.isArray(drafts)) drafts = {};
    host._longComicToken = token;
    host.innerHTML = '<style>' +
      '.lc-page{max-width:1450px;margin:auto;color:#423b31}.lc-page h2{margin:0 0 8px}.lc-page h3{margin:0 0 12px}.lc-note{font-size:13px;line-height:1.7;color:#837762;margin:8px 0 14px}.lc-toolbar,.lc-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:12px 0}.lc-toolbar select{flex:1;min-width:160px}.lc-grid{display:grid;grid-template-columns:minmax(280px,.8fr) minmax(420px,1.7fr);gap:18px;align-items:start}.lc-card{background:#fffdf8;border:1px solid #e3d9ca;border-radius:12px;padding:18px;min-width:0}.lc-page label{display:block;font-size:13px;margin:10px 0 5px}.lc-page input,.lc-page select,.lc-page textarea{box-sizing:border-box;max-width:100%;font:inherit}.lc-page textarea,.lc-page input[type=text]{width:100%}.lc-page textarea{resize:vertical;line-height:1.65;min-height:65px}.lc-inline{display:flex;gap:12px;flex-wrap:wrap}.lc-inline>div{flex:1;min-width:110px}.lc-inline input,.lc-inline select{width:100%}.lc-state{white-space:pre-wrap;line-height:1.7;padding:12px;background:#f4efe5;border-radius:8px;margin-top:12px}.lc-error{color:#a14531;white-space:pre-wrap;overflow-wrap:anywhere}.lc-progress{height:7px;background:#eee5d6;border-radius:5px;margin-top:10px;overflow:hidden}.lc-progress i{display:block;height:100%;background:#a97c45;width:0}.lc-outline{white-space:pre-wrap;line-height:1.7;font-size:13px;max-height:260px;overflow:auto}.lc-panel{border-top:1px solid #e5ddcf;margin-top:14px;padding-top:8px}.lc-gallery{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px;margin-top:16px}.lc-sheet{padding:12px;border:1px solid #e1d8c8;background:#fffdf9;border-radius:10px;min-width:0}.lc-sheet img{display:block;width:100%;height:auto;max-height:600px;object-fit:contain;margin:10px auto}.lc-sheet details{font-size:12px;line-height:1.7}.lc-sheet a{overflow-wrap:anywhere}.lc-empty{padding:28px 12px;text-align:center;color:#887d69;background:#faf5eb;border-radius:8px}.lc-page [hidden]{display:none!important}.lc-page button:disabled{opacity:.55}.lc-dirty{color:#9e6b20;font-size:12px}.lc-export-links{display:flex;gap:14px;flex-wrap:wrap;margin:12px 0}.lc-export-links a{overflow-wrap:anywhere}@media(max-width:1050px){.lc-grid{grid-template-columns:1fr}.lc-page{padding:0 2px}}' +
      '.lc-source-card{display:block;padding:10px;border:1px solid #e3d9ca;border-radius:8px;margin:7px 0}.lc-source-card small{display:block;line-height:1.6;margin-top:4px}.lc-people{display:flex;gap:12px;flex-wrap:wrap}.lc-people label{display:inline-flex;gap:5px;align-items:center}.lc-change{padding:9px;background:#f8f3e9;border-radius:8px;margin:9px 0}.lc-change textarea{min-height:40px}.lc-state-view{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;max-height:220px;overflow:auto}' +
      '.lc-reference-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px}.lc-reference{border:1px solid #e3d9ca;border-radius:8px;padding:10px}.lc-reference img{display:block;width:100%;height:120px;object-fit:contain;margin:8px 0}.lc-reference input{width:100%;font-size:12px}.lc-repair{border-top:1px solid #e3d9ca;margin-top:22px;padding-top:18px}.lc-repair-previews{display:flex;gap:12px;align-items:start}.lc-repair-stage,.lc-candidate{position:relative;flex:1;min-width:0}.lc-repair-stage img,.lc-candidate img{display:block;width:100%;height:auto}.lc-repair-stage canvas{position:absolute;inset:0;width:100%;height:100%;touch-action:none;cursor:crosshair}.lc-region{display:grid;grid-template-columns:repeat(4,minmax(50px,1fr));gap:8px}.lc-region input{width:100%}.lc-version{margin:8px 0;padding:8px;background:#f8f3e9;border-radius:6px}.lc-repair button[aria-pressed=true]{background:#ede0cd;border-color:#a97c45}' +
      '</style><section id="lc-page" class="lc-page"><h2>长篇漫画</h2>' +
      '<p class="lc-note">漫画独立保存，可复制主项目的人物设定。先规划全书，再预览本章分镜，确认后生成；已有页面保留，可单页重做。</p>' +
      '<div class="lc-toolbar"><label for="lc-projects">漫画项目</label><select id="lc-projects" aria-label="漫画项目"><option value="">新漫画</option></select><button id="lc-new" class="btn">＋ 新建漫画</button><button id="lc-refresh" class="btn">刷新状态</button></div>' +
      '<div class="lc-grid"><section class="lc-card"><h3>作品设定</h3><label for="lc-title">标题</label><input id="lc-title" type="text" maxlength="120" placeholder="给漫画起个名字">' +
      '<label for="lc-style">画风</label><textarea id="lc-style" rows="2" maxlength="2000" placeholder="例如：彩色手绘奇幻漫画，线条清晰"></textarea>' +
      '<label for="lc-source">人物来自哪里</label><select id="lc-source" style="width:100%"><option value="">手动填写人物</option></select><p id="lc-source-note" class="lc-note"></p><div id="lc-source-characters"></div>' +
      '<label for="lc-characters">手动人物设定</label><textarea id="lc-characters" rows="5" maxlength="6000" placeholder="未选择主项目时，在这里填写人物姓名、外貌、穿着及性格"></textarea>' +
      '<label for="lc-story">完整故事或故事方向</label><textarea id="lc-story" rows="7" maxlength="16000" placeholder="写清主线、重要人物关系和希望的结局"></textarea>' +
      '<div class="lc-inline"><div><label for="lc-chapters">章节数</label><input id="lc-chapters" type="number" min="1" max="20" value="3"></div><div><label for="lc-pages">每章页数</label><input id="lc-pages" type="number" min="1" max="12" value="4"></div></div>' +
      '<label for="lc-model">绘图模型</label><select id="lc-model" disabled><option value="moss">Moss</option></select>' +
      '<p class="lc-note">Qwen 用中文规划故事、分镜和出图词，Moss 生成整页漫画与对白。人物和文字仍可能有偏差，请逐页预览；不承诺自动锁脸或零错字。</p>' +
      '<p id="lc-setting-note" class="lc-note"></p><div class="lc-actions"><button id="lc-save-project" class="btn primary">保存作品</button><span id="lc-project-dirty" class="lc-dirty"></span></div>' +
      '<details open><summary>人物参考图</summary><p class="lc-note">为每个人绑定一张清楚的参考图，修图时再勾选需要的人物。可更换，原图保留。支持 PNG、JPEG、WEBP，每张最多 8 MB。</p><div id="lc-references" class="lc-reference-grid"></div></details>' +
      '<button id="lc-plan" class="btn">① 规划全书</button><details style="margin-top:16px"><summary>全书规划</summary><div id="lc-outline" class="lc-outline">尚未规划</div></details></section>' +
      '<section class="lc-card"><h3>逐章制作</h3><div class="lc-inline"><div><label for="lc-chapter">当前章节</label><select id="lc-chapter"></select></div><div><label for="lc-page-select">编辑哪一页</label><select id="lc-page-select"></select></div></div>' +
      '<p id="lc-chapter-summary" class="lc-note"></p><p id="lc-continuity-note" class="lc-error" role="alert" hidden></p><div class="lc-actions"><button id="lc-plan-chapter" class="btn">② 规划本章</button><button id="lc-replan-chapter" class="btn" hidden>重新规划本章（保留旧版本）</button><button id="lc-generate" class="btn primary">③ 生成本章</button><button id="lc-pause" class="btn" hidden>暂停后续页</button><button id="lc-resume" class="btn" hidden>继续本章</button></div>' +
      '<div id="lc-status" class="lc-state" role="status">填写作品设定后，保存并规划全书。</div><div class="lc-progress"><i id="lc-bar"></i></div><div id="lc-error" class="lc-error" role="alert"></div>' +
      '<div id="lc-editor" hidden><h3 style="margin-top:22px">本页分镜与对白</h3><p id="lc-page-people" class="lc-note"></p><label for="lc-page-summary">本页剧情</label><textarea id="lc-page-summary" rows="2"></textarea><div id="lc-panels"></div>' +
      '<label for="lc-prompt">本页出图词</label><textarea id="lc-prompt" rows="7" maxlength="16000"></textarea><p id="lc-page-note" class="lc-note"></p><div class="lc-actions"><button id="lc-save-page" class="btn">保存本页修改</button><button id="lc-redo" class="btn">重做本页（保留旧图）</button><span id="lc-page-dirty" class="lc-dirty"></span></div>' +
      '<section id="lc-repair" class="lc-repair"><h3>局部修图</h3><p id="lc-repair-note" class="lc-note"></p><div class="lc-actions"><button id="lc-show-current" class="btn" aria-pressed="true">当前采用图 · 框选</button><button id="lc-show-selected" class="btn">预览所选版本</button><button id="lc-show-compare" class="btn">并排对照</button></div>' +
      '<div class="lc-repair-previews"><div id="lc-repair-stage" class="lc-repair-stage"><img id="lc-repair-image" alt="当前采用的漫画页"><canvas id="lc-repair-canvas" aria-label="拖动框选需要修正的区域"></canvas></div><div id="lc-candidate" class="lc-candidate" hidden><img id="lc-candidate-image" alt="所选历史版本"></div></div>' +
      '<p id="lc-region-note" class="lc-note">在当前采用图上拖动框选，也可以填写原图坐标。</p><div class="lc-region">' + ['x1', 'y1', 'x2', 'y2'].map(function (name) { return '<div><label for="lc-region-' + name + '">' + {x1:'左侧 X',y1:'顶部 Y',x2:'右侧 X',y2:'底部 Y'}[name] + '</label><input id="lc-region-' + name + '" type="number" min="0" step="1"></div>'; }).join('') + '</div>' +
      '<label>本次参考人物（最多 3 人）</label><div id="lc-repair-people" class="lc-people"></div><label for="lc-repair-prompt">这一区域要改什么</label><textarea id="lc-repair-prompt" rows="3" maxlength="16000" placeholder="例如：按参考图修正人物的发型与五官，保持视角、表情和衣服不变"></textarea><button id="lc-repair-submit" class="btn primary">生成修图候选</button>' +
      '<label for="lc-repair-version">预览历史版本</label><select id="lc-repair-version"></select><button id="lc-repair-adopt" class="btn">采用这个版本</button><p class="lc-note">候选不会自动替换当前采用图；检查满意后再采用。每次修图都从当前采用图出发，框选以外保持原样。</p></section></div>' +
      '<p id="lc-no-plan" class="lc-note">先点“规划本章”，预览每页分镜后再生成。通常每页 2—3 格更容易看清动作与对白；原有 1—4 格页面仍可继续编辑。</p></section></div>' +
      '<section class="lc-card" style="margin-top:18px"><h3>漫画页预览与导出</h3><div class="lc-actions"><button id="lc-export-chapter-zip" class="btn">本章 ZIP</button><button id="lc-export-chapter-pdf" class="btn">本章 PDF</button><button id="lc-export-book-zip" class="btn">全书 ZIP</button><button id="lc-export-book-pdf" class="btn">全书 PDF</button></div><div id="lc-export-links" class="lc-export-links" role="status"></div><div id="lc-gallery" class="lc-gallery"></div></section></section>';

    function get(name) { return host.querySelector('#lc-' + name); }
    function active() { return !disposed && host._longComicToken === token && host.isConnected && location.hash.split('?')[0] === '#long-comic'; }
    function current(mark) { return active() && mark === epoch; }
    function busy() { return !!(project && ACTIVE.includes((project.job || {}).status)); }
    function chapter() { return project && (project.chapters || []).find(function (item) { return item.number === chapterNumber; }); }
    function page() { var value = chapter(); return value && (value.pages || []).find(function (item) { return item.number === pageNumber; }); }
    function chapterOutdated() { return v2() && !!(chapter() && chapter().continuity_review_required); }
    function bookOutdated() { return v2() && (project.chapters || []).some(function (item) { return item.continuity_review_required; }); }
    function stopPoll() { sequence++; clearTimeout(timer); timer = null; }
    function draftKey() { return selected || 'new'; }
    function saveDrafts() { try { localStorage.setItem(DRAFTS, JSON.stringify(drafts)); } catch (error) {} }
    function capture() {
      if (!get('title')) return;
      var value = drafts[draftKey()] || {};
      value.form = form(); value.project_dirty = projectDirty; value.chapter = chapterNumber; value.page = pageNumber;
      value.page_drafts = value.page_drafts || {};
      var pageKey = chapterNumber + ':' + pageNumber;
      if (get('editor').hidden === false && (planDirty || promptDirty)) {
        value.page_drafts[pageKey] = {chapter: chapterNumber, page: pageNumber, summary: get('page-summary').value,
          panels: panelValues(false), prompt: get('prompt').value, plan_dirty: planDirty, prompt_dirty: promptDirty};
      } else if (!(planDirty || promptDirty)) { delete value.page_drafts[pageKey]; }
      drafts[draftKey()] = value; saveDrafts();
    }
    function form() { return {title: get('title').value, style: get('style').value, characters_text: get('characters').value,
      story: get('story').value, chapter_count: Number(get('chapters').value), pages_per_chapter: Number(get('pages').value), model: 'moss',
      source_story_id: sourceStoryId, source_character_ids: sourceCharacterIds.slice()}; }
    function assign(name, value) { var text = String(value == null ? '' : value); if (get(name).value !== text) get(name).value = text; }
    function message(text, error) { get('error').textContent = error ? text : ''; if (!error) get('status').textContent = text; }
    function localUrl(value) { return typeof value === 'string' && value.startsWith('/files/') && !/[\r\n]/.test(value) ? value : ''; }
    function adopted(value) { return value && (value.versions || []).find(function (item) { return item.version === value.current_version && item.status === 'completed'; }); }
    function pageOutdated(value) { return chapterOutdated() || !!(value && (value.needs_redo || value.continuity_review_required || value.status === 'outdated')); }
    function repairBlocked() { return !project || !page() || pending || clearing || stale || busy() || projectDirty || planDirty || promptDirty || pageOutdated(page()) || !localUrl((adopted(page()) || {}).url); }
    function repairScope() { return {mark: epoch, id: selected, chapter: chapterNumber, page: pageNumber, version: page() && page().current_version}; }
    function sameRepair(scope) { return current(scope.mark) && selected === scope.id && chapterNumber === scope.chapter && pageNumber === scope.page && page() && page().current_version === scope.version; }
    function readRegion(validate) {
      var coordinates = ['x1','y1','x2','y2'].map(function (name) { var text = get('region-' + name).value; return String(text).trim() ? Number(text) : NaN; });
      var valid = repairSize.width > 0 && repairSize.height > 0 && coordinates.every(Number.isInteger) && coordinates[0] >= 0 && coordinates[1] >= 0 && coordinates[2] > coordinates[0] && coordinates[3] > coordinates[1] && coordinates[2] <= repairSize.width && coordinates[3] <= repairSize.height;
      if (!valid && validate) throw new Error('请在当前采用图上框选有效区域，坐标须在原图范围内。');
      return valid ? coordinates : null;
    }
    function drawRegion() {
      var canvas = get('repair-canvas'), context = canvas.getContext('2d');
      if (!context) return;
      context.clearRect(0, 0, canvas.width, canvas.height);
      if (!repairRegion) return;
      context.fillStyle = 'rgba(169,124,69,.12)'; context.strokeStyle = '#ad603a'; context.lineWidth = Math.max(2, repairSize.width / 250);
      context.fillRect(repairRegion[0], repairRegion[1], repairRegion[2] - repairRegion[0], repairRegion[3] - repairRegion[1]);
      context.strokeRect(repairRegion[0], repairRegion[1], repairRegion[2] - repairRegion[0], repairRegion[3] - repairRegion[1]);
    }
    function setRegion(coordinates) {
      repairRegion = coordinates;
      ['x1','y1','x2','y2'].forEach(function (name, index) { assign('region-' + name, coordinates ? coordinates[index] : ''); });
      drawRegion(); sync();
    }
    function pointerPoint(event) {
      var bounds = get('repair-canvas').getBoundingClientRect();
      if (!bounds.width || !bounds.height || !repairSize.width || !repairSize.height) return null;
      return [Math.max(0, Math.min(repairSize.width, (event.clientX - bounds.left) * repairSize.width / bounds.width)), Math.max(0, Math.min(repairSize.height, (event.clientY - bounds.top) * repairSize.height / bounds.height))];
    }
    function dragRegion(event) {
      var point = pointerPoint(event); if (!repairDrag || !point) return;
      setRegion([Math.floor(Math.min(point[0], repairDrag[0])), Math.floor(Math.min(point[1], repairDrag[1])), Math.ceil(Math.max(point[0], repairDrag[0])), Math.ceil(Math.max(point[1], repairDrag[1]))]);
    }
    function renderReferences() {
      var people = project && project.characters || [], references = project && project.character_references || {};
      var key = JSON.stringify([selected, people, references]); if (key === referenceKey) return;
      referenceKey = key;
      get('references').innerHTML = people.map(function (person) {
        var reference = references[person.id], url = localUrl(reference && reference.url);
        return '<div class="lc-reference"><strong>' + esc(person.name || person.id) + '</strong>' + (url ? '<img src="' + esc(url) + '" alt="' + esc(person.name || person.id) + '的参考图">' : '<p class="lc-note">尚未绑定参考图</p>') +
          '<label>' + (reference ? '更换参考图' : '上传参考图') + '<input type="file" accept="image/png,image/jpeg,image/webp" data-reference-person="' + esc(person.id) + '"></label></div>';
      }).join('') || '<p class="lc-note">保存作品并完成人物规划后，可在这里绑定参考图。</p>';
      get('references').querySelectorAll('[data-reference-person]').forEach(function (control) { control.onchange = function () { return uploadReference(control); }; });
    }
    async function uploadReference(control) {
      if (!project || pending || clearing || stale || busy()) return;
      var file = (control.files || [])[0]; if (!file) return;
      var identity = control.getAttribute('data-reference-person'), projectId = selected, mark = epoch;
      return action(async function () {
        if (!['image/png','image/jpeg','image/webp'].includes(file.type) || file.size > 8 * 1024 * 1024 || !file.size) throw new Error('请上传不超过 8 MB 的 PNG、JPEG 或 WEBP 图片。');
        var data = await new Promise(function (resolve, reject) { var reader = new FileReader(); reader.onload = function () { resolve(reader.result); }; reader.onerror = function () { reject(new Error('参考图读取失败，请重新选择。')); }; reader.onabort = reader.onerror; reader.readAsDataURL(file); });
        if (!current(mark) || selected !== projectId) return;
        var result = await window.api.post(PREFIX + '/project/' + encodeURIComponent(projectId) + '/reference', {character_id: identity, image_data: data});
        if (!current(mark) || selected !== projectId) return;
        if (!result.project || result.project.id !== projectId) throw new Error('参考图保存结果不属于当前漫画，请刷新核对。');
        project = result.project;
      });
    }
    function renderRepair() {
      var value = page(), base = adopted(value), baseUrl = localUrl(base && base.url);
      var key = JSON.stringify([selected, chapterNumber, pageNumber, value && value.current_version, baseUrl]);
      if (key !== repairKey) {
        repairKey = key; repairVersionsKey = ''; repairPeopleKey = ''; repairRegion = null; repairDrag = null; repairPeople = []; repairPreview = null; repairView = 'current'; repairAwaiting = null;
        repairSize = {width: Number(base && base.width || 0), height: Number(base && base.height || 0)};
        ['x1','y1','x2','y2'].forEach(function (name) { assign('region-' + name, ''); }); assign('repair-prompt', '');
      }
      get('repair').hidden = !value;
      var versions = (value && value.versions || []).filter(function (item) { return item.status === 'completed' && localUrl(item.url); });
      if (repairAwaiting) {
        var job = project && project.job || {}, waiting = repairAwaiting;
        if (!sameRepair(waiting.scope) || job.id !== waiting.jobId || job.action !== 'edit_page' || job.chapter !== chapterNumber || job.page !== pageNumber) repairAwaiting = null;
        else if (job.status === 'completed') {
          var candidate = versions.find(function (item) { return item.version === job.candidate_version && item.kind === 'edit' && item.candidate && !waiting.knownVersions.includes(item.version); });
          if (candidate) { repairPreview = candidate.version; repairView = 'compare'; repairAwaiting = null; }
        } else if (!ACTIVE.includes(job.status)) repairAwaiting = null;
      }
      if (!versions.some(function (item) { return item.version === repairPreview; })) repairPreview = versions.length ? versions[versions.length - 1].version : null;
      var versionsKey = JSON.stringify(versions);
      if (repairVersionsKey !== versionsKey) {
        repairVersionsKey = versionsKey;
        get('repair-version').innerHTML = versions.map(function (item) { return '<option value="' + Number(item.version) + '">版本 ' + Number(item.version) + (item.version === value.current_version ? ' · 当前采用' : item.kind === 'edit' ? (item.candidate ? ' · 修图候选' : ' · 修图版本') : ' · 历史图片') + '</option>'; }).join('') || '<option value="">尚无图片</option>';
      }
      get('repair-version').value = String(repairPreview == null ? '' : repairPreview);
      var preview = versions.find(function (item) { return item.version === repairPreview; }), previewUrl = localUrl(preview && preview.url);
      var ids = value && value.character_ids || [];
      if (!ids.length && value) (value.panels || []).forEach(function (panel) { ids = ids.concat(panel.character_ids || []); });
      var references = project && project.character_references || {}, people = (project && project.characters || []).filter(function (person) { return ids.includes(person.id); });
      var peopleKey = JSON.stringify(people.map(function (person) { return [person.id, person.name, references[person.id]]; }));
      if (peopleKey !== repairPeopleKey) {
        var first = !repairPeopleKey; repairPeopleKey = peopleKey;
        repairPeople = first ? people.filter(function (person) { return references[person.id]; }).slice(0,3).map(function (person) { return person.id; }) : repairPeople.filter(function (id) { return people.some(function (person) { return person.id === id && references[id]; }); });
        get('repair-people').innerHTML = people.map(function (person) { return '<label><input type="checkbox" data-repair-person="' + esc(person.id) + '"' + (repairPeople.includes(person.id) ? ' checked' : '') + '> ' + esc(person.name || person.id) + (references[person.id] ? '' : '（先绑定参考图）') + '</label>'; }).join('') || '<span class="lc-note">本页尚未填写出场人物。</span>';
        get('repair-people').querySelectorAll('[data-repair-person]').forEach(function (control) { control.onchange = function () {
          var id = control.getAttribute('data-repair-person');
          if (control.checked && !repairPeople.includes(id) && repairPeople.length >= 3) { control.checked = false; message('每次修图最多选择 3 位参考人物。', true); return; }
          repairPeople = control.checked ? Array.from(new Set(repairPeople.concat([id]))) : repairPeople.filter(function (item) { return item !== id; }); sync();
        }; });
      }
      var scope = repairScope(), image = get('repair-image'), imageUrl = repairView === 'selected' ? previewUrl : baseUrl;
      get('repair-stage').hidden = !imageUrl; get('candidate').hidden = repairView !== 'compare' || !previewUrl;
      image.onload = function () {
        if (!sameRepair(scope) || repairView === 'selected') return;
        repairSize = {width: image.naturalWidth, height: image.naturalHeight};
        get('repair-canvas').width = repairSize.width; get('repair-canvas').height = repairSize.height;
        get('region-note').textContent = '原图 ' + repairSize.width + ' × ' + repairSize.height + ' 像素。拖动框选，或填写左上角与右下角坐标。'; drawRegion(); sync();
      };
      if (image.getAttribute('src') !== imageUrl) image.setAttribute('src', imageUrl);
      if (image.complete && image.naturalWidth && repairView !== 'selected') image.onload();
      get('candidate-image').setAttribute('src', repairView === 'compare' ? previewUrl : '');
      get('repair-canvas').width = repairSize.width; get('repair-canvas').height = repairSize.height; get('repair-canvas').hidden = repairView === 'selected'; drawRegion();
      ['current','selected','compare'].forEach(function (mode) { get('show-' + mode).setAttribute('aria-pressed', String(repairView === mode)); });
      get('repair-note').textContent = !baseUrl ? '本页生成图片后才能局部修图。' : pageOutdated(value) ? '本页或本章已有修改，请先重做或重新规划，旧图仍保留。' : planDirty || promptDirty || projectDirty ? '请先保存修改，再框选修图或采用历史版本。' : '先在当前采用图上框选人物头部等小区域，再生成候选。请预览核对身份、画风和接缝。';
    }
    async function generateRepair() {
      if (repairBlocked()) return;
      var scope = repairScope(), knownVersions = (page().versions || []).map(function (item) { return item.version; });
      return action(async function () {
        var region = readRegion(true), prompt = get('repair-prompt').value.trim();
        if (!prompt) throw new Error('请填写这一区域要改什么。');
        if (!repairPeople.length || repairPeople.length > 3) throw new Error('请勾选 1—3 位已绑定参考图的本页人物。');
        var body = {chapter: scope.chapter, page: scope.page, base_version: scope.version, region: region, character_ids: repairPeople.slice(), prompt: prompt};
        var result = await window.api.post(PREFIX + '/project/' + encodeURIComponent(scope.id) + '/edit', body);
        if (!sameRepair(scope)) return;
        if (!result.project || result.project.id !== scope.id) throw new Error('修图结果不属于当前漫画，请刷新核对。');
        project = result.project; repairPreview = null;
        repairAwaiting = {scope: scope, jobId: (project.job || {}).id, knownVersions: knownVersions};
      });
    }
    async function adoptVersion(number, version) {
      if (pending || clearing || stale || busy() || projectDirty || planDirty || promptDirty) return;
      var value = chapter() && (chapter().pages || []).find(function (item) { return item.number === number; });
      var candidate = value && (value.versions || []).find(function (item) { return item.version === version && item.status === 'completed' && localUrl(item.url); });
      if (!candidate || pageOutdated(value) || value.current_version === version) return;
      if (pageNumber !== number) choosePage(number);
      var scope = repairScope();
      return action(async function () {
        var result = await window.api.post(PREFIX + '/project/' + encodeURIComponent(scope.id) + '/adopt', {chapter: scope.chapter, page: scope.page, version: version});
        if (!sameRepair(scope)) return;
        if (!result.project || result.project.id !== scope.id) throw new Error('采用结果不属于当前漫画，请刷新核对。');
        project = result.project; repairKey = ''; galleryKey = '';
      });
    }
    function statusName(value) { return {queued: '排队中', running: '正在制作', pausing: '当前页完成后暂停', paused: '已暂停', interrupted: '已中断', partial: '本轮已处理，部分待补', failed: '制作失败', completed: '已完成', cancelled: '已取消'}[value] || '尚未开始'; }
    function fillForm(value) {
      assign('title', value.title || ''); assign('style', value.style || ''); assign('characters', value.characters_text || '');
      assign('story', value.story || ''); assign('chapters', value.chapter_count || 3); assign('pages', value.pages_per_chapter || 4);
      var snapshot = value.character_source || {}, nextSource = String(value.source_story_id || snapshot.story_id || '');
      if (sourceStoryId !== nextSource) { sourceCharacters = []; sourceError = ''; sourceSequence++; sourceLoading = false; }
      sourceStoryId = nextSource;
      sourceCharacterIds = Array.isArray(value.source_character_ids) ? value.source_character_ids.slice() : (snapshot.characters || []).map(function (item) { return item.source_character_id; });
    }
    function sync() {
      var hold = pending || clearing || stale, working = busy(), planned = !!(project && project.outline);
      ['title', 'style', 'characters', 'story', 'chapters', 'pages'].forEach(function (name) { get(name).disabled = hold || working || (planned && name !== 'title'); });
      get('characters').disabled = get('characters').disabled || !!sourceStoryId;
      get('source').disabled = hold || working || planned || !!(project && !v2());
      get('source-characters').querySelectorAll('input').forEach(function (control) { control.disabled = hold || working || planned || sourceLoading || !!(project && !v2()); });
      get('setting-note').textContent = planned ? '全书已有规划，故事、人物和画风已固定；可编辑单页。要换一套设定，请新建漫画。' : '默认 3 章，每章 4 页；最多 20 章、每章 12 页。';
      get('save-project').disabled = hold || working || sourceLoading;
      get('projects').disabled = pending || clearing; get('new').disabled = pending || clearing;
      get('refresh').disabled = pending || clearing; get('chapter').disabled = pending || clearing; get('page-select').disabled = pending || clearing;
      ['plan', 'plan-chapter', 'generate'].forEach(function (name) { get(name).disabled = hold || working || sourceLoading; });
      get('generate').disabled = get('generate').disabled || !(chapter() && (chapter().pages || []).length) || chapterOutdated();
      get('plan-chapter').disabled = get('plan-chapter').disabled || chapterOutdated();
      get('replan-chapter').hidden = !chapterOutdated();
      get('replan-chapter').disabled = hold || working || sourceLoading || !chapterOutdated();
      var editJob = project && (project.job || {}).action === 'edit_page';
      get('pause').hidden = !working || editJob; get('pause').disabled = pending || clearing || stale || editJob || (project && (project.job || {}).status === 'pausing');
      get('resume').hidden = !project || editJob || !['paused', 'interrupted', 'failed', 'partial', 'cancelled'].includes((project.job || {}).status);
      get('resume').disabled = hold || working || chapterOutdated();
      ['save-page', 'redo'].forEach(function (name) { get(name).disabled = hold || working || !page(); });
      get('redo').disabled = get('redo').disabled || chapterOutdated();
      get('editor').querySelectorAll('input,textarea,select').forEach(function (control) { control.disabled = hold || working; });
      get('panels').querySelectorAll('button').forEach(function (control) { control.disabled = hold || working; });
      ['export-chapter-zip', 'export-chapter-pdf', 'export-book-zip', 'export-book-pdf'].forEach(function (name) { get(name).disabled = hold || working || !project; });
      ['zip', 'pdf'].forEach(function (kind) {
        get('export-chapter-' + kind).disabled = get('export-chapter-' + kind).disabled || chapterOutdated();
        get('export-book-' + kind).disabled = get('export-book-' + kind).disabled || bookOutdated();
      });
      get('project-dirty').textContent = projectDirty ? '设定尚未保存' : '';
      get('page-dirty').textContent = planDirty || promptDirty ? '本页尚未保存' : '';
      get('references').querySelectorAll('input').forEach(function (control) { control.disabled = hold || working || !project; });
      var blocked = repairBlocked(), references = project && project.character_references || {};
      get('repair').querySelectorAll('input,textarea').forEach(function (control) { control.disabled = blocked; });
      get('repair-people').querySelectorAll('[data-repair-person]').forEach(function (control) { control.disabled = blocked || !references[control.getAttribute('data-repair-person')]; });
      get('repair-canvas').style.pointerEvents = blocked || repairView === 'selected' ? 'none' : 'auto';
      get('repair-submit').disabled = blocked || !readRegion(false) || !repairPeople.length || repairPeople.length > 3 || !get('repair-prompt').value.trim();
      get('repair-adopt').disabled = blocked || repairPreview == null || repairPreview === (page() && page().current_version);
      get('repair-version').disabled = hold || !page();
      get('gallery').querySelectorAll('[data-adopt-page]').forEach(function (control) {
        var value = chapter() && (chapter().pages || []).find(function (item) { return item.number === Number(control.getAttribute('data-adopt-page')); });
        control.disabled = hold || working || projectDirty || planDirty || promptDirty || pageOutdated(value);
      });
    }
    function renderList() {
      var markup = '<option value="">新漫画</option>' + projects.map(function (item) { return '<option value="' + esc(item.id) + '">' + esc(item.title || item.id) + '</option>'; }).join('');
      if (get('projects').innerHTML !== markup) get('projects').innerHTML = markup;
      get('projects').value = selected;
    }
    function short(value) { var text = String(value || ''); return text.length > 180 ? text.slice(0, 180) + '…' : text; }
    function renderSource() {
      var snapshot = project && project.character_source, planned = !!(project && project.outline);
      var options = '<option value="">手动填写人物</option>' + sources.map(function (item) { return '<option value="' + esc(item.story_id) + '">' + esc(item.title || item.story_id) + '（' + Number(item.character_count || 0) + ' 人）</option>'; }).join('');
      if (sourceStoryId && !sources.some(function (item) { return item.story_id === sourceStoryId; })) options += '<option value="' + esc(sourceStoryId) + '">' + esc(sourceStoryId) + '（已选来源）</option>';
      if (get('source').innerHTML !== options) get('source').innerHTML = options;
      get('source').value = sourceStoryId;
      var people = sourceCharacters;
      if (snapshot && snapshot.story_id === sourceStoryId && (planned || !people.length)) people = (snapshot.characters || []).map(function (item) {
        var person = (project.characters || []).find(function (row) { return row.id === item.comic_character_id; }) || item.snapshot || {};
        return {character_id: item.source_character_id, name: person.name, appearance: person.appearance, clothing: person.clothing};
      });
      get('source-note').textContent = project && !v2() ? '旧版漫画沿用原人物设定；新建漫画时可选择主项目人物。' : sourceLoading ? '正在读取人物…' : sourceError || (sourceStoryId ?
        (snapshot && snapshot.story_id === sourceStoryId ? '人物已复制为本漫画的独立快照，主项目修改不会改动这份设定。' : '勾选本漫画需要的人物；保存时复制设定，原项目保持原样。') : '也可以选择一个主项目，复制其中的人物；不会调用图像参考。');
      get('source-characters').innerHTML = sourceStoryId ? people.map(function (person) {
        return '<label class="lc-source-card"><input type="checkbox" data-source-person="' + esc(person.character_id) + '"' + (sourceCharacterIds.includes(person.character_id) ? ' checked' : '') + '> ' + esc(person.name || person.character_id) +
          '<small>外貌：' + esc(short(person.appearance) || '未填写') + '</small><small>衣着：' + esc(short(person.clothing) || '未填写') + '</small></label>';
      }).join('') || (sourceLoading ? '' : '<p class="lc-note">这个来源暂无可选人物，可改选其他项目或手动填写。</p>') : '';
      get('source-characters').querySelectorAll('[data-source-person]').forEach(function (control) { control.onchange = function () {
        var identity = control.getAttribute('data-source-person');
        sourceCharacterIds = control.checked ? Array.from(new Set(sourceCharacterIds.concat([identity]))) : sourceCharacterIds.filter(function (item) { return item !== identity; });
        changedProject();
      }; });
    }
    async function loadSourceCharacters(identity, mark) {
      var request = ++sourceSequence;
      sourceCharacters = []; sourceError = ''; sourceLoading = !!identity; renderSource(); sync();
      if (!identity) return;
      try {
        var result = await window.api.get(PREFIX + '/sources/' + encodeURIComponent(identity) + '/characters');
        if (!current(mark) || request !== sourceSequence || identity !== sourceStoryId) return;
        sourceCharacters = result.characters || [];
      } catch (error) { if (current(mark) && request === sourceSequence && identity === sourceStoryId) sourceError = '读取人物失败：' + error.message + '。可重新选择来源。'; }
      finally { if (current(mark) && request === sourceSequence && identity === sourceStoryId) { sourceLoading = false; renderSource(); sync(); } }
    }
    async function loadSources(mark) {
      try {
        var result = await window.api.get(PREFIX + '/sources');
        if (current(mark)) sources = result.sources || [];
      } catch (error) { if (current(mark)) sourceError = '主项目列表暂不可用，可先手动填写人物。'; }
      if (current(mark)) renderSource();
    }
    function dialogueText(panel) {
      return (panel.dialogue || []).map(function (line) {
        var person = (project.characters || []).find(function (item) { return item.id === line.speaker; });
        return (person ? person.name : line.speaker) + '：' + line.text;
      }).join('\n');
    }
    function v2() { return !!project && Number(project.continuity_version) === 2; }
    function stateText(value) { return value && typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value || ''); }
    function panelIds(panel) { return Array.isArray(panel.character_ids) ? panel.character_ids : (page().character_ids || []); }
    function changeValues(container, validate, identities, description) {
      return Array.from(container.querySelectorAll('[data-change]')).map(function (element, index) {
        var result = Object.assign({}, (container._lcChanges || [])[index] || {});
        ['character_id', 'field', 'value', 'evidence'].forEach(function (field) { result[field] = element.querySelector('[data-change-field="' + field + '"]').value; });
        if (!result.value.trim() && !result.evidence.trim()) return validate ? null : result;
        if (validate && (!identities.includes(result.character_id) || !['clothing', 'held_items', 'injury', 'location'].includes(result.field))) throw new Error('状态变化须选择本格已出场的人物与有效类型。');
        if (validate && (!result.value.trim() || !result.evidence.trim() || !description.includes(result.evidence.trim()))) throw new Error('状态变化请填写结果，并从本格“画面与动作”原文复制依据。');
        return result;
      }).filter(function (value) { return value !== null; });
    }
    function renderChanges(container, changes) {
      container._lcChanges = changes.map(function (item) { return Object.assign({}, item); });
      container.innerHTML = changes.map(function (change) {
        return '<div class="lc-change" data-change><label>发生变化的人物</label><select data-change-field="character_id">' +
          (project.characters || []).map(function (person) { return '<option value="' + esc(person.id) + '"' + (person.id === change.character_id ? ' selected' : '') + '>' + esc(person.name) + '</option>'; }).join('') + '</select>' +
          '<label>变化类型</label><select data-change-field="field">' + [['clothing', '衣着'], ['held_items', '手中物品'], ['injury', '伤势'], ['location', '位置']].map(function (item) {
            return '<option value="' + item[0] + '"' + (item[0] === change.field ? ' selected' : '') + '>' + item[1] + '</option>';
          }).join('') + '</select><label>变化后的状态</label><textarea data-change-field="value" rows="1">' + esc(change.value || '') + '</textarea>' +
          '<label>依据（复制本格动作原文）</label><textarea data-change-field="evidence" rows="1">' + esc(change.evidence || '') + '</textarea><button class="btn small" data-remove-change>移除这条变化</button></div>';
      }).join('');
      container.querySelectorAll('input,textarea,select').forEach(function (control) { control.oninput = changedPlan; control.onchange = changedPlan; });
      container.querySelectorAll('[data-remove-change]').forEach(function (button, index) { button.onclick = function () {
        var values = changeValues(container, false, [], ''); values.splice(index, 1); renderChanges(container, values); changedPlan(); sync();
      }; });
    }
    function panelValues(validate) {
      var currentPage = page();
      return Array.from(get('panels').querySelectorAll('[data-lc-panel]')).map(function (element) {
        var number = Number(element.getAttribute('data-lc-panel')), base = editingPanels.find(function (item) { return item.number === number; }) || {};
        var identities = v2() ? Array.from(element.querySelectorAll('[data-person]')).filter(function (control) { return control.checked; }).map(function (control) { return control.getAttribute('data-person'); }) : (currentPage.character_ids || []);
        var people = (project.characters || []).filter(function (person) { return identities.includes(person.id); });
        var dialogue = element.querySelector('[data-field="dialogue"]').value;
        var lines = validate ? dialogue.split('\n').filter(function (line) { return line.trim(); }).map(function (line) {
          var split = line.search(/[:：]/);
          if (split < 1) throw new Error('对白请按“人物姓名：台词”填写，每行一句。');
          var speaker = line.slice(0, split).trim(), matches = people.filter(function (person) { return person.name === speaker || person.id === speaker; });
          if (matches.length !== 1) throw new Error('对白人物“' + speaker + '”须属于本格勾选的出场人物。');
          return {speaker: matches[0].id, text: line.slice(split + 1).trim()};
        }) : dialogue;
        var result = Object.assign({}, base, {number: number, description: element.querySelector('[data-field="description"]').value,
          dialogue: lines, narration: element.querySelector('[data-field="narration"]').value});
        if (v2()) {
          result.character_ids = identities;
          result.state_changes = changeValues(element.querySelector('[data-state-changes]'), validate, identities, result.description);
        }
        return result;
      });
    }
    function renderEditor(force) {
      var value = page(), key = JSON.stringify([selected, chapterNumber, pageNumber, value]);
      get('editor').hidden = !value; get('no-plan').hidden = !!value;
      if (!value || (!force && (planDirty || promptDirty || key === editorKey))) return;
      editorKey = key;
      var saved = ((drafts[draftKey()] || {}).page_drafts || {})[chapterNumber + ':' + pageNumber];
      var restore = saved && saved.chapter === chapterNumber && saved.page === pageNumber;
      var panels = restore ? saved.panels : value.panels || [];
      editingPanels = panels.map(function (item) { return Object.assign({}, item); });
      assign('page-summary', restore ? saved.summary : value.summary);
      assign('prompt', restore ? saved.prompt : value.prompt);
      get('page-people').textContent = v2() ? '逐格勾选实际出场人物，随后填写“姓名：台词”；本格未出场的人不能说话。通常每页 2—3 格更清楚。' : '旧版分镜，沿用本页人物：' + (project.characters || []).filter(function (person) { return (value.character_ids || []).includes(person.id); }).map(function (person) { return person.name; }).join('、') + '。对白每行格式：姓名：台词。';
      get('panels').innerHTML = panels.map(function (panel) {
        var identities = panelIds(panel);
        return '<section class="lc-panel" data-lc-panel="' + panel.number + '"><h4>第 ' + panel.number + ' 格</h4>' +
          (v2() ? '<label>本格出场人物</label><div class="lc-people">' + (project.characters || []).map(function (person) {
            return '<label><input type="checkbox" data-person="' + esc(person.id) + '"' + (identities.includes(person.id) ? ' checked' : '') + '> ' + esc(person.name) + '</label>';
          }).join('') + '</div>' : '') + '<label>画面与动作</label><textarea data-field="description" rows="3">' + esc(panel.description || '') + '</textarea>' +
          '<label>对白（每行一句：姓名：台词）</label><textarea data-field="dialogue" rows="2">' + esc(typeof panel.dialogue === 'string' ? panel.dialogue : dialogueText(panel)) + '</textarea>' +
          '<label>旁白（可留空）</label><textarea data-field="narration" rows="1">' + esc(panel.narration || '') + '</textarea>' +
          (v2() ? '<details><summary>状态变化（可选）</summary><p class="lc-note">仅记录本格明确发生的换装、持物、伤势或位置变化。没有变化就留空。</p><div data-state-changes></div><button class="btn small" data-add-change>＋ 记录变化</button></details>' : '') +
          '<details><summary>承接状态（只读）</summary><pre class="lc-state-view">' + esc(stateText(panel.opening_state)) + '\n→\n' + esc(stateText(panel.ending_state)) + '</pre></details></section>';
      }).join('');
      get('panels').querySelectorAll('textarea').forEach(function (control) { control.oninput = changedPlan; });
      get('panels').querySelectorAll('[data-person]').forEach(function (control) { control.onchange = changedPlan; });
      if (v2()) get('panels').querySelectorAll('[data-lc-panel]').forEach(function (element) {
        var panel = editingPanels.find(function (item) { return item.number === Number(element.getAttribute('data-lc-panel')); }) || {};
        var container = element.querySelector('[data-state-changes]'); renderChanges(container, panel.state_changes || []);
        element.querySelector('[data-add-change]').onclick = function () {
          var values = changeValues(container, false, [], '');
          values.push({character_id: '', field: 'clothing', value: '', evidence: ''}); renderChanges(container, values); changedPlan(); sync();
        };
      });
      planDirty = !!(restore && saved.plan_dirty); promptDirty = !!(restore && saved.prompt_dirty);
      get('page-note').textContent = value.needs_redo ? '本页已修改，旧图仍保留；点击“重做本页”才替换当前展示版本。' : '修改分镜后保存会重新编写本页出图词；已有图不会自动覆盖。可单独编辑出图词再重做。';
    }
    function renderGallery() {
      var value = chapter(), pages = value && value.pages || [], key = JSON.stringify([selected, chapterNumber, pages.map(function (item) { return [item.number, item.status, item.current_version, item.needs_redo, item.versions]; })]);
      if (key === galleryKey) return;
      galleryKey = key;
      get('gallery').innerHTML = pages.map(function (item) {
        var versions = item.versions || [], currentVersion = versions.find(function (version) { return version.version === item.current_version; });
        var url = localUrl(currentVersion && currentVersion.url);
        return '<article class="lc-sheet"><strong>第 ' + chapterNumber + ' 章 · 第 ' + item.number + ' 页</strong>' +
          (url ? '<a href="' + esc(url) + '" target="_blank" rel="noopener"><img loading="lazy" src="' + esc(url) + '" alt="第' + item.number + '页漫画"></a>' : '<div class="lc-empty">' + (item.status === 'generating' ? '正在绘制本页…' : '尚未生成') + '</div>') +
          (item.needs_redo ? '<p class="lc-note">有新修改，当前仍显示旧图。</p>' : '') + '<button class="btn small" data-edit-page="' + item.number + '">查看/编辑此页</button>' +
          (versions.length ? '<details><summary>历史版本（' + versions.length + '）</summary>' + versions.map(function (version) { var oldUrl = localUrl(version.url); return '<div class="lc-version">版本 ' + version.version + (version.kind === 'edit' ? ' · 修图' : '') + ' · ' + esc(statusName(version.status)) + (version.version === item.current_version ? ' · 当前采用' : version.candidate ? ' · 待确认候选' : '') + (oldUrl ? ' · <a href="' + esc(oldUrl) + '" target="_blank" rel="noopener">打开图片</a>' : '') + (oldUrl && version.status === 'completed' && version.version !== item.current_version ? '<button class="btn small" data-adopt-page="' + item.number + '" data-adopt-version="' + version.version + '">采用这个版本</button>' : '') + '</div>'; }).join('') + '</details>' : '') + '</article>';
      }).join('') || '<div class="lc-empty">本章规划完成后，这里显示各页与历史版本。</div>';
      get('gallery').querySelectorAll('[data-edit-page]').forEach(function (button) { button.onclick = function () { choosePage(Number(button.getAttribute('data-edit-page'))); }; });
      get('gallery').querySelectorAll('[data-adopt-page]').forEach(function (button) { button.onclick = function () { return adoptVersion(Number(button.getAttribute('data-adopt-page')), Number(button.getAttribute('data-adopt-version'))); }; });
    }
    function renderProject() {
      if (!projectDirty && project) fillForm(project);
      renderSource(); renderReferences();
      var count = project ? project.chapter_count : Math.max(1, Math.min(20, Math.floor(Number(get('chapters').value) || 3)));
      var chapterMarkup = Array.from({length: count}, function (unused, index) {
        var number = index + 1, item = project && (project.chapters || []).find(function (value) { return value.number === number; });
        return '<option value="' + number + '">第 ' + number + ' 章' + (item && item.title ? ' · ' + esc(item.title) : '（未规划）') + '</option>';
      }).join('');
      if (get('chapter').innerHTML !== chapterMarkup) get('chapter').innerHTML = chapterMarkup;
      get('chapter').value = String(chapterNumber);
      var currentChapter = chapter(), pages = currentChapter && currentChapter.pages || [];
      var pageMarkup = pages.map(function (item) { return '<option value="' + item.number + '">第 ' + item.number + ' 页</option>'; }).join('') || '<option value="1">尚未规划页面</option>';
      if (get('page-select').innerHTML !== pageMarkup) get('page-select').innerHTML = pageMarkup;
      get('page-select').value = String(pageNumber);
      get('chapter-summary').textContent = currentChapter ? (currentChapter.summary || '') : '尚未规划本章；第二章起需先有上一章规划。';
      get('continuity-note').hidden = !chapterOutdated();
      get('continuity-note').textContent = chapterOutdated() ? '前一章状态已修改，本章分镜需要重新规划。旧规划和图片会保留；重新规划完成前不能继续制作或导出。' : '';
      get('outline').textContent = project && project.outline ? [project.outline.premise || '', (project.chapters || []).map(function (item) { return '第 ' + item.number + ' 章 ' + item.title + '\n' + (item.summary || ''); }).join('\n\n'), '结局：' + (project.outline.ending || '')].join('\n\n') : '尚未规划';
      if (project && !stale && !clearing) {
        var job = project.job || {}, step = job.step || '', done = Number(job.done || 0), total = Number(job.total || 0);
        get('status').textContent = statusName(job.status) + (step ? ' · ' + step : '') + (total ? '\n已完成 ' + done + ' / ' + total : '') + (job.status === 'pausing' ? '\n当前页完成后暂停，已出页面保留。' : '');
        get('bar').style.width = (total ? Math.max(0, Math.min(100, done / total * 100)) : 0) + '%';
        get('error').textContent = job.error || '';
      }
      renderEditor(false); renderRepair(); renderGallery(); sync();
    }
    function changedProject() { projectDirty = true; capture(); sync(); }
    function changedPlan() { planDirty = true; promptDirty = false; get('prompt').value = ''; capture(); sync(); }
    function choosePage(number) { capture(); pageNumber = number; planDirty = false; promptDirty = false; editorKey = ''; renderEditor(true); renderRepair(); capture(); sync(); }
    function chooseChapter(number) { capture(); chapterNumber = number; pageNumber = 1; planDirty = false; promptDirty = false; editorKey = ''; galleryKey = ''; renderProject(); capture(); }
    function schedule() { clearTimeout(timer); if (active() && project && !clearing && !stale) timer = setTimeout(poll, busy() ? 1800 : 7000); }
    async function poll() {
      if (!active() || !selected || pending || clearing || stale) return;
      var mark = epoch, request = ++sequence, projectId = selected;
      try {
        var result = await window.api.get(PREFIX + '/project/' + encodeURIComponent(projectId));
        if (!current(mark) || request !== sequence || selected !== projectId) return;
        project = result.project; renderProject();
      } catch (error) { if (current(mark) && request === sequence) message('读取进度失败：' + error.message + '；可点击刷新状态。', true); }
      if (current(mark) && request === sequence) schedule();
    }
    async function loadList(mark) {
      var result = await window.api.get(PREFIX + '/projects');
      if (!current(mark)) return;
      projects = result.projects || []; renderList();
    }
    async function loadProject(id) {
      stopPoll(); epoch++; var mark = epoch; selected = id; project = null; projectDirty = false; planDirty = false; promptDirty = false; editorKey = ''; galleryKey = ''; repairKey = ''; referenceKey = ''; stale = false;
      localStorage.setItem(SELECTED, id); get('projects').value = id;
      var draft = drafts[draftKey()] || {}; chapterNumber = Number(draft.chapter || 1); pageNumber = Number(draft.page || 1);
      if (!id) {
        fillForm(draft.form || {});
        var currentStory = String((window.AppState || {}).storyId || '');
        if (!draft.form && sources.some(function (item) { return item.story_id === currentStory; })) sourceStoryId = currentStory;
        projectDirty = !!draft.project_dirty; renderProject(); message('填写作品设定后，保存并规划全书。');
        await loadSourceCharacters(sourceStoryId, mark); return;
      }
      pending = true; sync();
      try {
        var result = await window.api.get(PREFIX + '/project/' + encodeURIComponent(id));
        if (!current(mark)) return;
        project = result.project;
        if (draft.project_dirty && draft.form) { fillForm(draft.form); projectDirty = true; }
        chapterNumber = Math.max(1, Math.min(chapterNumber, project.chapter_count));
        renderProject();
        if (!project.outline) await loadSourceCharacters(sourceStoryId, mark);
      } catch (error) { if (current(mark)) message('加载漫画失败：' + error.message, true); }
      finally { if (current(mark)) { pending = false; sync(); schedule(); } }
    }
    function validateForm(value) {
      if (!value.title.trim() || !value.style.trim() || !value.story.trim() || (!value.source_story_id && !value.characters_text.trim())) throw new Error('请填写标题、画风、人物和故事。');
      if (sourceLoading) throw new Error('人物仍在读取，请稍候保存。');
      if (value.source_story_id && !value.source_character_ids.length) throw new Error('请勾选至少一位来源人物，或改为手动填写人物。');
      if (value.source_story_id && !(project && project.outline) && (sourceError || value.source_character_ids.some(function (identity) { return !sourceCharacters.some(function (person) { return person.character_id === identity; }); }))) throw new Error('来源人物未能完整读取，请重新选择来源后保存。');
      if (!Number.isInteger(value.chapter_count) || value.chapter_count < 1 || value.chapter_count > 20 || !Number.isInteger(value.pages_per_chapter) || value.pages_per_chapter < 1 || value.pages_per_chapter > 12) throw new Error('章节数须为 1—20，每章页数须为 1—12。');
    }
    async function saveProject(mark) {
      if (project && !projectDirty) return;
      var value = form(); validateForm(value);
      if (project && !v2()) { delete value.source_story_id; delete value.source_character_ids; }
      var result = await window.api.post(project ? PREFIX + '/project/' + encodeURIComponent(selected) + '/save' : PREFIX + '/project', project && project.outline ? {title: value.title} : value);
      if (!current(mark)) return;
      var priorKey = draftKey(), priorDraft = drafts[priorKey] || {};
      project = result.project; selected = project.id; projectDirty = false;
      localStorage.setItem(SELECTED, selected); if (priorKey === 'new') delete drafts.new;
      drafts[selected] = priorDraft; drafts[selected].form = value; drafts[selected].project_dirty = false; saveDrafts();
      if (!projects.some(function (item) { return item.id === selected; })) projects.unshift({id: selected, title: project.title});
      projects.forEach(function (item) { if (item.id === selected) item.title = project.title; }); renderList();
    }
    async function savePage(mark) {
      if (!page() || !(planDirty || promptDirty)) return;
      var patch = {chapter: chapterNumber, page: pageNumber};
      if (planDirty) { patch.summary = get('page-summary').value; patch.panels = panelValues(true); }
      if (promptDirty) { if (!get('prompt').value.trim()) throw new Error('出图词不能为空；修改分镜会在生成时自动重编。'); patch.prompt = get('prompt').value; }
      var result = await window.api.post(PREFIX + '/project/' + encodeURIComponent(selected) + '/save', patch);
      if (!current(mark)) return;
      project = result.project; planDirty = false; promptDirty = false; editorKey = '';
      if (drafts[draftKey()] && drafts[draftKey()].page_drafts) delete drafts[draftKey()].page_drafts[chapterNumber + ':' + pageNumber]; saveDrafts();
    }
    async function action(callback) {
      if (!active() || pending || clearing || stale) return;
      stopPoll(); var mark = epoch; pending = true; get('error').textContent = ''; sync();
      try { await callback(mark); if (current(mark)) { renderProject(); capture(); } }
      catch (error) { if (current(mark)) message(error.message, true); }
      finally { if (current(mark)) { pending = false; sync(); schedule(); } }
    }
    async function run(actionName) {
      if (busy()) return;
      if (chapterOutdated() && actionName !== 'replan_chapter') { message('本章状态已过期，请先点击“重新规划本章”。', true); return; }
      if (actionName === 'replan_chapter' && !chapterOutdated()) return;
      if (actionName === 'generate' && !(chapter() && (chapter().pages || []).length)) { message('请先规划本章，预览分镜后再生成。', true); return; }
      await action(async function (mark) {
        await saveProject(mark); if (!current(mark)) return;
        await savePage(mark); if (!current(mark)) return;
        var body = {action: actionName, chapter: chapterNumber};
        if (actionName === 'redo_page') body.page = pageNumber;
        var result = await window.api.post(PREFIX + '/project/' + encodeURIComponent(selected) + '/run', body);
        if (current(mark)) project = result.project;
      });
    }
    function dispose() {
      if (disposed) return;
      capture(); disposed = true; epoch++; sourceSequence++; stopPoll();
      window.removeEventListener('hashchange', onHash); window.removeEventListener('workbench-runtime-reset', onReset);
    }
    function onHash() { if (location.hash.split('?')[0] !== '#long-comic') dispose(); }
    function onReset(event) {
      var phase = event.detail && event.detail.phase;
      if (phase === 'begin') { capture(); epoch++; sourceSequence++; sourceLoading = false; stopPoll(); pending = false; clearing = true; stale = true; message('正在清理运行任务，项目、漫画页与未保存输入保留。'); }
      else { clearing = false; message(phase === 'failed' ? '清理未完成，请点击“刷新状态”核对项目；不会自动重新生成。' : '运行状态已清理，请点击“刷新状态”读取项目；不会自动重新生成。'); }
      sync();
    }
    host._longComicDispose = dispose;
    window.addEventListener('hashchange', onHash); window.addEventListener('workbench-runtime-reset', onReset);
    ['title', 'style', 'characters', 'story', 'chapters', 'pages'].forEach(function (name) { get(name).oninput = changedProject; });
    get('source').onchange = async function () {
      if (pending || clearing || stale || busy() || (project && (project.outline || !v2()))) return;
      sourceStoryId = get('source').value; sourceCharacterIds = []; sourceCharacters = []; sourceError = '';
      changedProject(); await loadSourceCharacters(sourceStoryId, epoch);
    };
    get('page-summary').oninput = changedPlan;
    get('prompt').oninput = function () { promptDirty = true; capture(); sync(); };
    get('chapter').onchange = function () { chooseChapter(Number(get('chapter').value)); };
    get('page-select').onchange = function () { choosePage(Number(get('page-select').value)); };
    get('projects').onchange = function () { capture(); loadProject(get('projects').value); };
    get('new').onclick = function () { capture(); loadProject(''); };
    get('refresh').onclick = async function () {
      if (pending || clearing) return;
      stale = false;
      if (project) { await poll(); return; }
      try { var mark = epoch; await loadList(mark); if (current(mark)) await loadSources(mark); if (current(mark)) await loadProject(selected); }
      catch (error) { if (active()) message('刷新失败：' + error.message, true); }
    };
    get('save-project').onclick = function () { if (!busy()) return action(saveProject); };
    get('save-page').onclick = function () { if (!busy()) return action(savePage); };
    get('plan').onclick = function () { return run('plan'); };
    get('plan-chapter').onclick = function () { return run('plan_chapter'); };
    get('replan-chapter').onclick = function () { return run('replan_chapter'); };
    get('generate').onclick = function () { return run('generate'); };
    get('redo').onclick = function () { return run('redo_page'); };
    get('resume').onclick = function () { if (project && (project.job || {}).action === 'edit_page') return; if (project && project.job && project.job.chapter) chooseChapter(project.job.chapter); return run('generate'); };
    get('pause').onclick = function () { return action(async function (mark) {
      if (project && (project.job || {}).action === 'edit_page') return;
      var result = await window.api.post(PREFIX + '/project/' + encodeURIComponent(selected) + '/pause', {});
      if (current(mark)) project = result.project;
    }); };
    ['x1','y1','x2','y2'].forEach(function (name) { get('region-' + name).oninput = function () { repairRegion = readRegion(false); drawRegion(); sync(); }; });
    get('repair-prompt').oninput = sync;
    get('repair-submit').onclick = generateRepair;
    get('repair-adopt').onclick = function () { return adoptVersion(pageNumber, repairPreview); };
    get('repair-version').onchange = function () { repairAwaiting = null; repairPreview = Number(get('repair-version').value); repairView = 'selected'; renderRepair(); sync(); };
    ['current','selected','compare'].forEach(function (mode) { get('show-' + mode).onclick = function () { repairAwaiting = null; repairView = mode; repairDrag = null; renderRepair(); sync(); }; });
    get('repair-canvas').onpointerdown = function (event) {
      if (repairBlocked() || repairView === 'selected' || (event.button != null && event.button !== 0)) return;
      repairDrag = pointerPoint(event); if (!repairDrag) return;
      if (get('repair-canvas').setPointerCapture) get('repair-canvas').setPointerCapture(event.pointerId);
      if (event.preventDefault) event.preventDefault(); dragRegion(event);
    };
    get('repair-canvas').onpointermove = function (event) { if (repairDrag && !repairBlocked()) dragRegion(event); };
    get('repair-canvas').onpointerup = function (event) { if (repairDrag && !repairBlocked()) dragRegion(event); repairDrag = null; };
    get('repair-canvas').onpointercancel = function () { repairDrag = null; };
    ['chapter', 'book'].forEach(function (scope) { ['zip', 'pdf'].forEach(function (kind) {
      get('export-' + scope + '-' + kind).onclick = function () { return action(async function (mark) {
        if (scope === 'book' ? bookOutdated() : chapterOutdated()) throw new Error('存在状态过期的章节，请先重新规划后再导出。');
        var body = {kind: kind}; if (scope === 'chapter') body.chapter = chapterNumber;
        var result = await window.api.post(PREFIX + '/project/' + encodeURIComponent(selected) + '/export', body);
        if (!current(mark)) return;
        var url = localUrl(result.url); if (!url) throw new Error('导出未返回有效的本地文件地址。');
        get('export-links').innerHTML = '<a href="' + esc(url) + '" download>下载' + (scope === 'book' ? '全书' : '本章') + ' ' + kind.toUpperCase() + '</a>';
      }); };
    }); });
    fillForm((drafts.new || {}).form || {}); renderProject();
    try { var initial = epoch; await loadList(initial); if (current(initial)) await loadSources(initial); if (current(initial)) await loadProject(selected); }
    catch (error) { if (active()) message('读取项目列表失败：' + error.message + '。可点击刷新状态。', true); }
  }};
})();
