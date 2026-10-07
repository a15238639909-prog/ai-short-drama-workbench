/* 固定人物写真 V1.0 前端（加载于主脚本之后，覆盖同名函数，仅写真模式生效） */
(function () {
  if (typeof albumStudioMode === "undefined") return;

  var OLD = {
    applyMode: albumApplyMode,
    userInput: albumUserInput,
    makeSample: albumMakeSample,
    renderPlan: albumRenderPlan,
    renderSample: albumRenderSample,
    confirmAndBatch: albumConfirmAndBatch,
    generateBatch: albumGenerateBatch,
    rerender: albumRerender,
    rerenderAll: albumRerenderAll,
    restoreState: albumRestoreState,
    jumpToPhoto: albumJumpToPhoto,
    inputChanged: albumInputChanged
  };

  var PHOTO_SCENES = {
    REAL_WORLD: [
      ["BEDROOM", "居家"], ["HOME_WINDOW", "家里窗边"], ["CAFE", "咖啡馆"],
      ["CITY_STREET", "城市街头"], ["BUS_STOP", "公交站"], ["PARK", "公园"],
      ["RIVERSIDE", "河边/湖边"], ["SEASIDE", "海边"], ["FOREST", "森林"],
      ["GRASSLAND", "草地"], ["OLD_TOWN", "老城区"], ["TRAVEL_ROOM", "旅行住宿"]
    ],
    SPACE_LIFE: [
      ["ORBITAL_BEDROOM", "太空卧室"], ["LIVING_CABIN", "生活舱"],
      ["OBSERVATION_WINDOW", "舷窗"], ["LONG_MODULE", "长舱段"],
      ["OBSERVATION_DECK", "观察甲板"], ["MAINTENANCE_AREA", "维修区"],
      ["EQUIPMENT_PREP", "装备准备区"]
    ]
  };

  var FEEL_MAP = {
    AUTO: { t: "", l: "" },
    NATURAL_LIFE: { t: "NATURAL_LIFE", l: "NATURAL_REAL" },
    QUIET_COOL: { t: "QUIET_COOL", l: "COOL_DOCUMENTARY" },
    RELAXED_SNAPSHOT: { t: "RELAXED_SNAPSHOT", l: "NATURAL_REAL" },
    TRAVEL_DOCUMENTARY: { t: "TRAVEL_DOCUMENTARY", l: "NATURAL_REAL" },
    LIGHT_EDITORIAL: { t: "LIGHT_EDITORIAL", l: "LIGHT_EDITORIAL" }
  };

  function isPhoto() { return albumStudioMode === "character_photography"; }

  function photoDomain() {
    var el = document.querySelector('input[name="albumDomain"]:checked');
    var v = el ? el.value : "AUTO";
    if (v !== "AUTO") return v;
    var theme = $("cmOne") ? $("cmOne").value : "";
    return /太空|空间站|轨道|飞船|舱|宇航|星球|星舰/.test(theme) ? "SPACE_LIFE" : "REAL_WORLD";
  }

  function photoFeelSync() {
    var feel = $("photoFeel") ? $("photoFeel").value : "AUTO";
    var m = FEEL_MAP[feel] || FEEL_MAP.AUTO;
    if ($("photoTendency") && m.t) $("photoTendency").value = m.t;
    if ($("photoLook") && m.l) $("photoLook").value = m.l;
  }

  function photoFeelChanged() {
    if (!isPhoto()) return;
    photoFeelSync();
    albumInputChanged();
  }

  function photoSetCount(n) {
    if ($("photoCount")) $("photoCount").value = String(n);
    var chips = $("photoCountChips");
    if (!chips) return;
    var btns = chips.querySelectorAll("button");
    for (var i = 0; i < btns.length; i++) {
      var on = Number(btns[i].getAttribute("data-count")) === Number(n);
      btns[i].className = "ghost" + (on ? " photoChipOn" : "");
    }
    albumInputChanged();
  }

  function photoDomainHint() {
    var hint = $("photoDomainHint");
    if (!hint) return;
    var el = document.querySelector('input[name="albumDomain"]:checked');
    var v = el ? el.value : "AUTO";
    if (v === "AUTO") hint.textContent = "自动识别：写“太空/空间站/轨道/舱”等会判为太空生活，其余判为真实世界";
    else hint.textContent = "题材：" + (v === "SPACE_LIFE" ? "太空生活" : "真实世界") + "（手动指定）";
  }

  function photoSceneSelected() {
    var mode = document.querySelector('input[name="photoScene"]:checked');
    if (!mode || mode.value !== "MANUAL") return [];
    var dom = photoDomain();
    var box = $("photoSceneChips" + (dom === "REAL_WORLD" ? "Real" : "Space"));
    if (!box) return [];
    return Array.prototype.map.call(box.querySelectorAll("input:checked"), function (x) { return x.value; });
  }

  function renderSceneChips(preserve) {
    var dom = photoDomain();
    var real = $("photoSceneChipsReal"), space = $("photoSceneChipsSpace");
    if (!real || !space) return;
    real.style.display = dom === "REAL_WORLD" ? "" : "none";
    space.style.display = dom === "SPACE_LIFE" ? "" : "none";
    var box = dom === "REAL_WORLD" ? real : space;
    var old = [];
    if (preserve) old = Array.prototype.map.call(box.querySelectorAll("input:checked"), function (x) { return x.value; });
    box.innerHTML = (PHOTO_SCENES[dom] || []).map(function (p) {
      var checked = old.indexOf(p[0]) >= 0 ? " checked" : "";
      return '<label class="chip" style="cursor:pointer"><input type="checkbox" value="' + p[0] + '"' + checked + ' onchange="albumInputChanged()"> ' + p[1] + "</label>";
    }).join("");
  }

  function syncPhotoUi() {
    var sceneMode = document.querySelector('input[name="photoScene"]:checked');
    var sceneBox = $("photoSceneBox");
    if (sceneBox) sceneBox.style.display = sceneMode && sceneMode.value === "MANUAL" ? "" : "none";
    renderSceneChips(true);
    var comp = $("photoComposition");
    var fixedAspect = $("photoFixedAspect");
    if (fixedAspect) fixedAspect.style.display = comp && comp.value === "FIXED" ? "" : "none";
    var wMode = document.querySelector('input[name="photoWardrobe"]:checked');
    var wModeVal = wMode ? wMode.value : "AUTO";
    if ($("photoFixedWardrobe")) $("photoFixedWardrobe").style.display = wModeVal === "FIXED" ? "" : "none";
    if ($("photoCustomWardrobe")) $("photoCustomWardrobe").style.display = wModeVal === "CUSTOM" ? "" : "none";
  }

  function albumPhotoApplyUi() {
    var m = isPhoto();
    var ps = $("albumPhotoSettings"), hint = $("albumCharacterHint"),
        wrap = $("albumStylePresetWrap"), oldDir = $("albumDirectionField"),
        countField = $("albumCountField"), aspectField = $("albumAspectField"),
        moreReq = $("albumMoreReq"), outfit = $("albumOutfitOption"),
        expandRow = $("albumExpandRow"), tendWrap = $("photoTendencyWrap"),
        lookWrap = $("photoLookWrap"), countSel = $("photoCount"),
        countChips = $("photoCountChips");
    if (ps) ps.style.display = m ? "" : "none";
    if (hint) hint.style.display = m ? "" : "none";
    if (wrap) wrap.style.display = m ? "none" : "";
    if (oldDir) oldDir.style.display = "none";
    if (countField) countField.style.display = m ? "none" : "";
    if (aspectField) aspectField.style.display = m ? "none" : "";
    if (moreReq) moreReq.style.display = m ? "" : "";
    if (outfit) outfit.style.display = "none";
    if (expandRow) expandRow.style.display = m ? "none" : "";
    if (tendWrap) tendWrap.style.display = m ? "none" : "";
    if (lookWrap) lookWrap.style.display = m ? "none" : "";
    if (countSel) countSel.style.display = "none";
    if (countChips) countChips.style.display = m ? "" : "none";
    var expandHint = $("albumExpandHint");
    if (expandHint) expandHint.style.display = m ? "none" : "";
    var mustLabel = $("albumMustIncludeLabel"), customLabel = $("albumCustomArtLabel"),
        mustExLabel = $("albumMustExcludeLabel");
    if (m) {
      if (mustLabel) {
        if (!mustLabel.dataset.orig) mustLabel.dataset.orig = mustLabel.textContent;
        mustLabel.textContent = "希望出现（每行一条）";
      }
      if (customLabel) {
        if (!customLabel.dataset.orig) customLabel.dataset.orig = customLabel.textContent;
        customLabel.textContent = "其他补充要求";
      }
      if (mustExLabel) {
        if (!mustExLabel.dataset.orig) mustExLabel.dataset.orig = mustExLabel.textContent;
        mustExLabel.textContent = "高级设置 · 禁止出现（每行一条）";
      }
    } else {
      if (mustLabel && mustLabel.dataset.orig) mustLabel.textContent = mustLabel.dataset.orig;
      if (customLabel && customLabel.dataset.orig) customLabel.textContent = customLabel.dataset.orig;
      if (mustExLabel && mustExLabel.dataset.orig) mustExLabel.textContent = mustExLabel.dataset.orig;
    }
    if (m) {
      if ($("albumBriefLabel")) $("albumBriefLabel").innerHTML = "这套写真想拍什么？ <em>必填</em>";
      if ($("cmOne")) $("cmOne").placeholder = "例：冬日城市生活写真 / 夏天海边旅行写真 / 轨道空间站里的日常生活写真";
      if ($("albumCharacterLabel")) $("albumCharacterLabel").textContent = "人物设定";
      if ($("btnComicGenerate")) $("btnComicGenerate").textContent = "✨ 规划写真并生成人物基准图";
      if ($("albumResultTitle")) $("albumResultTitle").textContent = "人物确认 / 预览 / 成品";
      if ($("albumExpandBtn")) $("albumExpandBtn").textContent = "🪄 AI规划这套写真";
      if ($("albumReExpandBtn")) $("albumReExpandBtn").textContent = "♻ 重新规划";
      if (!document.querySelector('input[name="albumDomain"]:checked')) {
        var r = document.querySelector('input[name="albumDomain"][value="REAL_WORLD"]');
        if (r) r.checked = true;
      }
      photoFeelSync();
      photoSetCount(parseInt($("photoCount") ? $("photoCount").value : "12") || 12);
      photoDomainHint();
      syncPhotoUi();
    }
  }

  function photoFields() {
    return {
      preset: "character_photography",
      originalTheme: $("cmOne").value.trim(),
      expandedBrief: $("albumExpandedBrief").value.trim(),
      fixedCharacter: $("charSheet").value.trim(),
      domain: photoDomain(),
      photoTendency: $("photoTendency") ? $("photoTendency").value : "NATURAL_LIFE",
      photoLook: $("photoLook") ? $("photoLook").value : "NATURAL_REAL",
      count: parseInt($("photoCount") ? $("photoCount").value : "12") || 12,
      compositionMode: $("photoComposition") ? $("photoComposition").value : "AUTO_MIX",
      fixedAspect: $("photoFixedAspect") ? $("photoFixedAspect").value : "4:5",
      wardrobeStrategy: (document.querySelector('input[name="photoWardrobe"]:checked') || {}).value || "AUTO",
      fixedWardrobe: $("photoFixedWardrobe") ? $("photoFixedWardrobe").value.trim() : "",
      customWardrobe: $("photoCustomWardrobe") ? $("photoCustomWardrobe").value : "",
      sceneScope: photoSceneSelected().join(","),
      advancedRequirements: $("albumCustomArt") ? $("albumCustomArt").value.trim() : "",
      mustInclude: $("albumMustInclude") ? $("albumMustInclude").value.split("\n").map(function (s) { return s.trim(); }).filter(Boolean) : [],
      mustExclude: $("albumMustExclude") ? $("albumMustExclude").value.split("\n").map(function (s) { return s.trim(); }).filter(Boolean) : [],
      albumName: $("albumTitle") ? $("albumTitle").value.trim() : "",
      baseSeed: albumJumpSeed || undefined
    };
  }

  // ---- 覆盖：模式切换 ----
  albumApplyMode = function (mode, preserve) {
    OLD.applyMode(mode, preserve);
    albumPhotoApplyUi();
  };

  // ---- 覆盖：输入变更时同步新控件 ----
  albumInputChanged = function () {
    if (isPhoto()) syncPhotoUi();
    OLD.inputChanged();
  };

  // ---- 覆盖：输入组装 ----
  albumUserInput = function () {
    return isPhoto() ? photoFields() : OLD.userInput();
  };

  // ---- AI 规划卡片 ----
  async function albumPhotoPlanCard() {
    if (!$("cmOne").value.trim()) { alert("先写一句这套写真想拍什么"); return; }
    $("comicStatus").textContent = "正在用V1.0内核规划整套写真…";
    try {
      var r = await fetch("/api/album_v3_photo_plan_card", {
        method: "POST", body: JSON.stringify({ userInput: photoFields() })
      });
      var j = await r.json();
      if (!r.ok || !j.ok) throw new Error(j.error || "规划失败");
      var c = j.card, box = $("albumPhotoPlanCard");
      box.style.display = "";
      box.innerHTML = "<b>🪄 AI规划结果</b>" +
        "<p>领域：" + albumEsc(c.domain_cn) + "｜拍摄倾向：" + albumEsc(c.shooting_tendency) + "</p>" +
        "<p>主题：" + albumEsc(c.concept) + "（" + c.count + "张）</p>" +
        "<p>场景：" + albumEsc((c.scene_pool || []).join(" / ")) + "</p>" +
        "<p>服装：" + albumEsc((c.wardrobe_summary || []).join(" / ")) + "</p>" +
        "<p>节奏：" + albumEsc((c.album_rhythm || []).join(" → ")) + "</p>" +
        "<p>镜头：" + albumEsc(c.camera_summary || "") + "</p>";
      $("comicStatus").textContent = "✅ 规划卡已生成。可修改上方设定后再点“规划写真并生成人物基准图”。";
    } catch (e) { $("comicStatus").textContent = "❌ " + e.message; }
  }

  // ---- 覆盖：主入口（规划+人物基准图）----
  albumMakeSample = async function (replan) {
    if (!isPhoto()) return OLD.makeSample(replan);
    if (comicRunning) return;
    if (!(await ensureNoRunningJob())) return;
    if (!$("cmOne").value.trim()) { alert("先写一句这套写真想拍什么"); $("cmOne").focus(); return; }
    albumSetBusy(true); cBar("busy"); setTask("规划固定人物写真…");
    $("comicStatus").textContent = "① 正在用V1.0内核规划整套写真…";
    $("albumActionStep").textContent = "② 规划中…";
    $("albumSample").style.display = "none";
    $("albumSampleActions").style.display = "none";
    $("albumSamplePromptWrap").style.display = "none";
    try {
      var r = await fetch("/api/album_v3_create", { method: "POST", body: JSON.stringify({ userInput: photoFields() }) });
      var text = await r.text(); var j; try { j = JSON.parse(text); } catch (e) { j = { ok: false, error: text }; }
      if (!r.ok || !j.ok) throw new Error(j.error || "画册规划失败");
      albumV3Project = j.project; albumDirty = false; albumSampleStale = false;
      albumRenderPlan();
      var st = await ensureComfy("comicStatus"); if (!st) throw new Error("ComfyUI画图未就绪");
      $("comicStatus").textContent = "② 正在生成人物基准图…";
      setTask("Krea2生成人物基准图…");
      var pj = await albumGenerateOne(0, false);
      albumV3Project = pj.project || albumV3Project;
      renderPhotoIdentity();
      $("comicStatus").textContent = "✅ 人物基准图完成。确认人物满意后继续3张预览。";
      $("albumActionStep").textContent = "人物确认：满意后继续";
    } catch (e) {
      $("comicStatus").textContent = "❌ " + friendlyError(e);
      $("albumActionStep").textContent = "生成未完成，可按提示修改后重试";
    } finally { albumSetBusy(false); cBar("off"); setTask(null); }
  };

  function renderPhotoIdentity() {
    var ref = albumV3Project && albumV3Project.identityReference;
    if (!ref) return;
    $("albumSample").style.display = "none";
    $("albumSampleActions").style.display = "none";
    $("albumSamplePromptWrap").style.display = "none";
    $("albumIdentityBox").style.display = "";
    $("albumPreviewBox").style.display = "none";
    $("albumIdentityImg").innerHTML = ref.archUrl
      ? '<img src="' + albumEsc(ref.archUrl) + '" alt="人物基准图" style="max-width:420px;width:100%;border-radius:8px">'
      : '<div class="albumSampleEmpty">人物基准图尚未生成</div>';
    var btn = $("albumIdentityConfirmBtn");
    if (btn) btn.textContent = ref.confirmed ? "✅ 已确认，继续预览" : "人物满意，继续";
  }

  async function generatePreviews() {
    var idxs = (albumV3Project.sample && albumV3Project.sample.previewIndexes) || [1, 2, 3];
    for (var k = 0; k < idxs.length; k++) {
      var i = idxs[k], img = albumV3Img(i);
      if (img && img.status === "completed" && img.file) continue;
      $("comicStatus").textContent = "正在生成方向预览 " + (k + 1) + "/" + idxs.length + "…";
      var pj = await albumGenerateOne(i, false);
      albumV3Project = pj.project || albumV3Project;
      renderPhotoPreview();
    }
  }

  function renderPhotoPreview() {
    if (!albumV3Project) return;
    $("albumSample").style.display = "none";
    $("albumSampleActions").style.display = "none";
    $("albumIdentityBox").style.display = "none";
    $("albumPreviewBox").style.display = "";
    var idxs = (albumV3Project.sample && albumV3Project.sample.previewIndexes) || [1, 2, 3];
    $("albumPreviewGrid").innerHTML = idxs.map(function (i) {
      var img = albumV3Img(i);
      var url = img && (img.archUrl || img.url);
      return '<div class="albumPlanCard">' +
        (url ? '<img src="' + albumEsc(url) + '" style="width:100%;border-radius:6px">' : '<div class="albumSampleEmpty">等待生成</div>') +
        "<b>" + (img ? albumEsc(img.title) : i) + "</b>" +
        "<p>" + (img ? albumEsc(img.role) : "") + "</p>" +
        "</div>";
    }).join("");
    albumRenderGallery();
  }

  async function albumPhotoConfirmIdentity() {
    if (!albumV3Project) return;
    var r = await fetch("/api/album_v3_confirm", { method: "POST", body: JSON.stringify({ dir: albumV3Project.dir, confirmed: true, phase: "identity" }) });
    var j = await r.json();
    if (!r.ok || !j.ok) { $("comicStatus").textContent = "❌ " + (j.error || "确认失败"); return; }
    albumV3Project = j.project;
    $("comicStatus").textContent = "人物已确认，正在生成3张方向预览…";
    try {
      await generatePreviews();
      renderPhotoPreview();
      $("comicStatus").textContent = "✅ 3张方向预览完成。满意后生成全部，也可跳过预览直接生成。";
      $("albumActionStep").textContent = "方向预览：满意后生成全部";
    } catch (e) { $("comicStatus").textContent = "❌ 预览生成中断：" + friendlyError(e); }
  }

  async function albumPhotoConfirmPreview() {
    if (!albumV3Project) return;
    var r = await fetch("/api/album_v3_confirm", { method: "POST", body: JSON.stringify({ dir: albumV3Project.dir, confirmed: true, phase: "preview" }) });
    var j = await r.json();
    if (!r.ok || !j.ok) { $("comicStatus").textContent = "❌ " + (j.error || "确认失败"); return; }
    albumV3Project = j.project;
    await albumPhotoBatch();
  }

  async function albumPhotoSkipPreview() {
    if (!albumV3Project) return;
    if (albumV3Project.phase === "identity") {
      var r = await fetch("/api/album_v3_confirm", { method: "POST", body: JSON.stringify({ dir: albumV3Project.dir, confirmed: true, phase: "identity" }) });
      var j = await r.json();
      if (!r.ok || !j.ok) { $("comicStatus").textContent = "❌ " + (j.error || "确认失败"); return; }
      albumV3Project = j.project;
    }
    if (albumV3Project.phase === "preview") {
      var r2 = await fetch("/api/album_v3_confirm", { method: "POST", body: JSON.stringify({ dir: albumV3Project.dir, confirmed: true, phase: "preview" }) });
      var j2 = await r2.json();
      if (!r2.ok || !j2.ok) { $("comicStatus").textContent = "❌ " + (j2.error || "确认失败"); return; }
      albumV3Project = j2.project;
    }
    $("comicStatus").textContent = "跳过预览，直接生成整套…";
    await albumPhotoBatch();
  }

  async function albumPhotoBatch() {
    if (!albumV3Project || comicRunning) return;
    if (!(await ensureNoRunningJob())) return;
    albumSetBusy(true); cBar("busy");
    var total = albumV3Project.images.length;
    try {
      var st = await ensureComfy("comicStatus"); if (!st) throw new Error("ComfyUI画图未就绪");
      for (var k = 0; k < albumV3Project.images.length; k++) {
        if (!comicRunning) break;
        var img = albumV3Project.images[k];
        if (img.status === "completed" && img.file) continue;
        var i = Number(img.index);
        $("comicStatus").textContent = "正在生成第" + i + "/" + total + "张：" + img.title;
        setTask("Krea2高质量画册 " + i + "/" + total);
        var pj = await albumGenerateOne(i, false);
        albumV3Project = pj.project || albumV3Project;
        albumRenderGallery();
      }
      if (albumV3Project.images.every(function (x) { return x.status === "completed"; })) {
        $("comicStatus").textContent = "✅ 整套完成，共" + total + "张。可单张重出、整册重出或导出PDF。";
        $("albumActionStep").textContent = "✅ 整套已完成";
        if ($("bookBtn")) $("bookBtn").style.display = "";
        if ($("albumCoverBtn")) $("albumCoverBtn").style.display = "";
        albumRenderGallery();
      }
    } catch (e) {
      $("comicStatus").textContent = "❌ 批量生成中断：" + friendlyError(e) + "；已完成图片均已保存，再点一次可从缺失处继续。";
    } finally { albumSetBusy(false); cBar("off"); setTask(null); albumRenderGallery(); }
  }

  // ---- 覆盖：规划卡渲染 ----
  albumRenderPlan = function () {
    if (!isPhoto()) return OLD.renderPlan();
    if (!albumV3Project) return;
    $("albumPlanWrap").style.display = "";
    var shots = (albumV3Project.albumPlan && albumV3Project.albumPlan.shots) || [];
    $("albumPlanGrid").innerHTML = shots.map(function (s) {
      return '<div class="albumPlanCard"><b>' + (s.index < 10 ? "0" : "") + s.index + " " + albumEsc(s.title) + "</b>" +
        "<p>" + albumEsc(s.scene_name) + "</p>" +
        "<p>" + albumEsc(s.wardrobe_desc) + "</p>" +
        "<p>" + albumEsc(s.action) + "</p>" +
        "<p>" + s.lens + "mm · " + albumEsc(s.frame) + " · " + albumEsc(s.aspectRatio) + "</p>" +
        "<small>" + albumEsc(s.motherType) + "</small></div>";
    }).join("");
    var b = albumV3Project.seriesBible || {};
    var dom = albumV3Project.userInput.domain === "SPACE_LIFE" ? "太空生活" : "真实世界";
    $("albumSeriesBible").style.display = "";
    $("albumSeriesBible").textContent = "本套写真规划：人物：" + (b.subjectLock || "") +
      "｜领域：" + dom + "｜场景：" + (b.worldLock || "") +
      "｜服装：" + (b.wardrobeLock || "") + "｜镜头：" + (b.cameraLock || "") +
      "｜质感：" + (b.styleLock || "");
    $("comicOut").textContent = (albumV3Project.images || []).map(function (x, i) {
      return "【" + (i + 1) + "｜" + x.title + "】\n" + (x.activePrompt || x.autoPrompt || "");
    }).join("\n\n");
  };

  // ---- 覆盖：代表图/预览渲染 ----
  albumRenderSample = function () {
    if (!isPhoto()) return OLD.renderSample();
    if (!albumV3Project) return;
    var phase = albumV3Project.phase || "identity";
    if (phase === "identity" || !(albumV3Project.sample && albumV3Project.sample.previewIndexes)) {
      renderPhotoIdentity();
    } else {
      renderPhotoPreview();
    }
  };

  // ---- 覆盖：确认与批量 ----
  albumConfirmAndBatch = async function () {
    if (!isPhoto()) return OLD.confirmAndBatch();
    if (albumV3Project && albumV3Project.phase === "preview") await albumPhotoConfirmPreview();
    else if (albumV3Project && albumV3Project.phase === "album") await albumPhotoBatch();
  };

  albumGenerateBatch = async function () {
    if (!isPhoto()) return OLD.generateBatch();
    await albumPhotoBatch();
  };

  // ---- 覆盖：单张/整册重出 ----
  albumRerender = async function (index) {
    if (!isPhoto()) return OLD.rerender(index);
    if (!albumV3Project || comicRunning) return;
    if (!(await ensureNoRunningJob())) return;
    albumSetBusy(true); cBar("busy");
    try {
      if (Number(index) === 0) {
        $("comicStatus").textContent = "正在重新生成人物基准图…";
        var pj = await albumGenerateOne(0, true);
        albumV3Project = pj.project || albumV3Project;
        renderPhotoIdentity();
      } else {
        var st = await ensureComfy("comicStatus"); if (!st) throw new Error("ComfyUI画图未就绪");
        var pj2 = await albumGenerateOne(Number(index), true);
        albumV3Project = pj2.project || albumV3Project;
        albumRenderSample(); albumRenderGallery();
      }
      $("comicStatus").textContent = "✅ 第" + index + "张已重新生成，旧版本已保留。";
    } catch (e) { $("comicStatus").textContent = "❌ 重新生成失败：" + friendlyError(e); }
    finally { albumSetBusy(false); cBar("off"); setTask(null); }
  };

  albumRerenderAll = async function () {
    if (!isPhoto()) return OLD.rerenderAll();
    if (!albumV3Project || comicRunning) return;
    if (!confirm("整册重新生成会先重出人物基准图，再为每张换新种子；旧图都会保留到重出历史。继续吗？")) return;
    if (!(await ensureNoRunningJob())) return;
    albumSetBusy(true); cBar("busy"); setTask("整册重新生成…");
    try {
      var st = await ensureComfy("comicStatus"); if (!st) throw new Error("ComfyUI画图未就绪");
      $("comicStatus").textContent = "整册重出：先重出人物基准图…";
      var pj = await albumGenerateOne(0, true);
      albumV3Project = pj.project || albumV3Project;
      var seed = albumV3Project.identityReference.seed;
      for (var k = 0; k < albumV3Project.images.length; k++) {
        var img = albumV3Project.images[k];
        $("comicStatus").textContent = "整册重出 " + (k + 1) + "/" + albumV3Project.images.length + "：" + img.title;
        var pj2 = await albumGenerateOne(Number(img.index), true, seed);
        albumV3Project = pj2.project || albumV3Project;
        albumRenderGallery();
      }
      $("comicStatus").textContent = "✅ 整册已重新生成，所有旧图都保留在重出历史目录。";
    } catch (e) { $("comicStatus").textContent = "❌ 整册重新生成中断：" + friendlyError(e); }
    finally { albumSetBusy(false); cBar("off"); setTask(null); albumRenderSample(); albumRenderGallery(); }
  };

  // ---- 覆盖：恢复项目 ----
  albumRestoreState = async function (showMessage) {
    await OLD.restoreState(showMessage);
    if (!isPhoto() || !albumV3Project) return;
    var ui = albumV3Project.userInput || {};
    var domRadio = document.querySelector('input[name="albumDomain"][value="' + (ui.domain || "REAL_WORLD") + '"]');
    if (domRadio) domRadio.checked = true;
    if ($("photoTendency")) $("photoTendency").value = ui.photoTendency || "NATURAL_LIFE";
    if ($("photoLook")) $("photoLook").value = ui.photoLook || "NATURAL_REAL";
    if ($("photoCount")) $("photoCount").value = String(ui.count || 12);
    if ($("photoComposition")) $("photoComposition").value = ui.compositionMode || "AUTO_MIX";
    if ($("photoFixedAspect")) $("photoFixedAspect").value = ui.fixedAspect || "4:5";
    var ws = ui.wardrobeStrategy || "AUTO";
    var w = document.querySelector('input[name="photoWardrobe"][value="' + ws + '"]');
    if (w) w.checked = true;
    if ($("photoFixedWardrobe")) $("photoFixedWardrobe").value = ui.fixedWardrobe || "";
    if ($("photoCustomWardrobe")) $("photoCustomWardrobe").value = ui.customWardrobe || "";
    var domRadio = document.querySelector('input[name="albumDomain"][value="' + (ui.domain || "") + '"]');
    if (domRadio) domRadio.checked = true;
    var feel = "AUTO";
    for (var k in FEEL_MAP) {
      var fm = FEEL_MAP[k];
      if (fm.t && fm.t === ui.photoTendency && fm.l === ui.photoLook) feel = k;
    }
    if ($("photoFeel")) $("photoFeel").value = feel;
    photoFeelSync();
    photoSetCount(parseInt(ui.count || 12, 10) || 12);
    photoDomainHint();
    syncPhotoUi();
    albumRenderPlan();
    if (albumV3Project.identityReference && albumV3Project.identityReference.status === "completed") renderPhotoIdentity();
    if ((albumV3Project.phase || "identity") !== "identity") renderPhotoPreview();
  };

  // ---- 覆盖：从角色设定图跳转 ----
  albumJumpToPhoto = function () {
    OLD.jumpToPhoto();
    var r = document.querySelector('input[name="albumDomain"][value="REAL_WORLD"]');
    if (r) r.checked = true;
    if ($("photoCount")) $("photoCount").value = "12";
    if ($("photoFeel")) $("photoFeel").value = "AUTO";
    photoFeelSync();
    photoSetCount(12);
    photoDomainHint();
    syncPhotoUi();
  };

  // ---- 历史人物设定：从已有角色设定图项目带入 ----
  async function photoCharPick() {
    var box = $("photoCharList");
    if (!box) return;
    $("photoCharModal").style.display = "flex";
    box.innerHTML = '<div class="hint">正在读取历史人物设定…</div>';
    try {
      var j = await (await fetch("/api/album_v3_projects")).json();
      var list = (j.projects || []).filter(function (x) { return (x.dir || "").indexOf("角色设定图") >= 0; });
      if (!list.length) { box.innerHTML = '<div class="hint">还没有角色设定图项目，先去做一张角色设定图。</div>'; return; }
      var items = [];
      for (var i = 0; i < list.length; i++) {
        try {
          var d = await (await fetch("/api/album_v3_load?dir=" + encodeURIComponent(list[i].dir))).json();
          if (!d.ok) continue;
          var p = d.project, ui = p.userInput || {};
          var thumb = "";
          var im = (p.images || [])[0];
          if (im && (im.archUrl || im.url)) thumb = im.archUrl || im.url;
          items.push({ dir: list[i].dir, name: p.name || list[i].name, char: ui.fixedCharacter || "", seed: (p.generation || {}).baseSeed || 0, thumb: thumb });
        } catch (e) {}
      }
      box.innerHTML = items.map(function (it, idx) {
        return '<div style="display:flex;gap:10px;align-items:center;border:1px solid #e6dcc9;border-radius:10px;padding:8px 10px;margin-bottom:8px;cursor:pointer" onclick="photoCharUse(' + idx + ')">' +
          (it.thumb ? '<img src="' + albumEsc(it.thumb) + '" style="width:56px;height:56px;object-fit:cover;border-radius:8px;background:#eee">' : '<div style="width:56px;height:56px;border-radius:8px;background:#efe9df"></div>') +
          '<div style="flex:1;min-width:0"><b style="font-size:13px">' + albumEsc(it.name) + "</b>" +
          '<div class="hint" style="font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + albumEsc(it.char || "无人物描述") + "</div></div></div>";
      }).join("") || '<div class="hint">没有可用的历史人物。</div>';
      window._photoCharItems = items;
    } catch (e) {
      box.innerHTML = '<div class="hint">读取失败：' + albumEsc(e.message) + "</div>";
    }
  }

  function photoCharClose() {
    $("photoCharModal").style.display = "none";
  }

  function photoCharUse(idx) {
    var items = window._photoCharItems || [];
    var it = items[idx];
    if (!it) return;
    if ($("charSheet")) $("charSheet").value = it.char;
    albumJumpSeed = Number(it.seed) || 0;
    photoCharClose();
    $("comicStatus").textContent = "✅ 已带入人物设定：" + (it.name || "") + "，可直接写主题生成写真。";
    albumInputChanged();
  }

  // 工具函数挂到全局
  window.albumPhotoPlanCard = albumPhotoPlanCard;
  window.photoFeelChanged = photoFeelChanged;
  window.photoSetCount = photoSetCount;
  window.photoCharPick = photoCharPick;
  window.photoCharClose = photoCharClose;
  window.photoCharUse = photoCharUse;
  window.albumPhotoRegenIdentity = function () { albumRerender(0); };
  window.albumPhotoConfirmIdentity = albumPhotoConfirmIdentity;
  window.albumPhotoConfirmPreview = albumPhotoConfirmPreview;
  window.albumPhotoSkipPreview = albumPhotoSkipPreview;
  window.albumPhotoRerenderPreview = function () {
    if (albumV3Project) albumRerender(albumV3SampleIndex());
  };
  window.albumPhotoEditPrompt = function () {
    var img = albumV3Img(albumV3SampleIndex());
    if (!img) return;
    $("albumSamplePrompt").value = img.activePrompt || img.autoPrompt || "";
    $("albumSamplePromptWrap").style.display = "";
    $("tab-album").scrollIntoView({ behavior: "smooth", block: "start" });
  };
  window.albumPhotoReplan = function () { albumMakeSample(true); };

  // 初始化：新控件的事件联动
  document.addEventListener("change", function (ev) {
    if (!isPhoto()) return;
    var t = ev.target;
    if (t && t.name === "albumDomain") { renderSceneChips(true); syncPhotoUi(); photoDomainHint(); }
    if (t && t.name === "photoScene") syncPhotoUi();
  });

  // 挂载“AI规划这套写真”按钮（写真模式替换原扩写行为，其他模式走原逻辑）
  var OLD_EXPAND = window.albumExpandBrief;
  if (OLD_EXPAND) {
    window.albumExpandBrief = function () {
      if (isPhoto()) return albumPhotoPlanCard();
      return OLD_EXPAND.apply(this, arguments);
    };
  }
})();
