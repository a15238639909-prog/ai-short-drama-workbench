/* pages/story.js — 📖 剧本（P463 重做；P464 第二话简化）

   第一话沿用原流程；第二话起可在本页直接写梗概并生成文字，生成视频仍走后续流程。
   左：分话列表。中：这一话讲什么 → 剧本（正文＝剧本，一个框）→ 视频分段（每段可改）→ 人物 → 场景。
   所有框改了就自动保存（停手 1.2 秒），没有保存按钮；生成图片/视频都按改过的来。 */
window.Pages = window.Pages || {};

(function () {
  var SAGA = null, cur = 1, poll = null;

  function esc(t) { return UI.esc(t); }
  function api(p, b) { return window.api.post("/api/saga/" + (window.AppState || {}).storyId + p, b || {}); }
  function clean(t) { return String(t == null ? "" : t).replace(/^\s*(集名|标题|题目)\s*[：:]\s*/, ""); }
  function ep(no) { return ((SAGA || {}).episodes || []).filter(function (e) { return +e.no === +no; })[0]; }

  function autoGrow(ta, minH) {
    if (!ta) return;
    if (ta.clientWidth < 200) { ta.style.height = ""; return; }   /* 面板还没排好版时量出来是几万像素，等 resize 再量 */
    ta.style.height = "0px";
    ta.style.height = Math.min(6000, Math.max(minH || 120, ta.scrollHeight + 8)) + "px";
  }

  function statusChip(e) {
    var s = e.status && e.status !== "EMPTY" ? e.status : ((e.prose || e.body) ? "DRAFT" : "EMPTY");
    var m = { LOCKED: ["🔒 已锁定", "#218838"], DRAFT: ["✏️ 草稿", "#b5762e"], EMPTY: ["⬜ 未生成", "#8b8375"] }[s] || ["", "#8b8375"];
    return '<span style="font-size:11px;color:' + m[1] + ';border:1px solid ' + m[1] + ';border-radius:8px;padding:1px 7px;white-space:nowrap">' + m[0] + "</span>";
  }

  /* 自动保存：停手 1.2 秒存一次；状态显示在 #sgSaveState */
  var saveTimers = {};
  function setState(t, warn) {
    var el = document.getElementById("sgSaveState");
    if (el) { el.textContent = t; el.style.color = warn ? "#c47d00" : ""; }
  }
  function debounce(key, fn, ms) {
    clearTimeout(saveTimers[key]);
    saveTimers[key] = setTimeout(fn, ms || 1200);
  }

  function render(main, st) {
    if (!st.storyId) { main.innerHTML = UI.empty("先在顶部选一个项目，或点「+ 新建」"); return Promise.resolve(); }
    return api("/get").then(function (r) {
      SAGA = r.saga || {};
      var eps = (SAGA.episodes || []).slice().sort(function (a, b) { return +a.no - +b.no; });
      if (!eps.length) {
        main.innerHTML = '<div class="wrap"><h2>📖 剧本</h2>' +
          UI.empty("还没有内容。去 ⚙️ 设定 写这一话讲什么，点「① 生成文字」，回这里看剧本和分段。", "去设定", function () { location.hash = "#settings"; }) + "</div>";
        return;
      }
      var requestedEp = (location.hash || "").match(/[?&]ep=(\d+)/);
      if (requestedEp && ep(+requestedEp[1])) cur = +requestedEp[1];
      if (!ep(cur)) cur = +eps[0].no;
      var e = ep(cur) || {};
      var locked = e.status === "LOCKED";
      var script = e.pictures || e.body || e.prose || "";
      var nextNo = Math.max.apply(null, eps.map(function (x) { return +x.no || 0; })) + 1;

      main.innerHTML = '<div class="wrap"><div class="sgbody">' +
        '<div class="sgeps"><div class="col-h sub"><b>分话</b><span>' + eps.length + " 话</span>" +
        '<span class="sp"></span><button class="btn small" id="sgAddEp">＋ 新建第 ' + nextNo + ' 话</button></div>' +
        '<div class="eplist">' + eps.map(function (x) {
          return '<div class="epitem' + (+x.no === +cur ? " on" : "") + '" data-no="' + x.no + '">' +
            '<div class="epno">第' + x.no + "话</div>" +
            '<div class="eptitle">' + esc(clean(x.title || x.logline || "")) + "</div>" + statusChip(x) + "</div>";
        }).join("") + "</div></div>" +

        '<div class="sgmain">' +
        '<div class="col-h"><b>第 ' + cur + " 话</b>" + statusChip(e) +
        '<span class="sub" id="sgSaveState" style="margin-left:10px">改了会自动保存</span><span class="sp"></span>' +
        (locked ? '<button class="btn small" id="sgUnlock">🔓 解锁重做</button>' : "") + "</div>" +

        '<div class="col-h sub"><b>这一话讲什么</b><span class="sub">' + (cur > 1 ? '一句话、一段口述或详细情节都可以；人物和场景默认接着上一话' : '一件一件说清楚发生了什么；改了它，下次「生成文字」会重写剧本') + '</span></div>' +
        '<textarea class="prompt" id="sgBrief" style="min-height:100px;overflow:hidden" placeholder="' + (cur > 1 ? '说说第 ' + cur + ' 话要发生什么……' : '') + '"' + (locked ? " readonly" : "") + '>' + esc(e.brief || "") + "</textarea>" +
        (cur > 1 ? '<div style="display:flex;align-items:center;gap:10px;justify-content:flex-end;padding:8px 18px 2px">' +
          '<span class="sub" id="sgGenHint">只生成剧本和视频分段，不出图、不出视频</span>' +
          '<button class="btn primary" id="sgGenText" style="height:38px;border-radius:8px"' + (locked ? ' disabled' : '') + '>生成本话文字</button></div>' : '') +

        '<div class="col-h sub"><b>剧本</b><span class="sub" id="sgScriptCount"></span><span class="sub">分段、提示词、视频都按这份剧本来；改了就自动保存</span></div>' +
        '<textarea class="prompt" id="sgScript" style="min-height:260px;overflow:hidden"' + (locked ? " readonly" : "") + ' placeholder="' + (cur > 1 ? '还没有剧本。写好上面的内容后，点「生成本话文字」' : '还没有剧本。去 ⚙️ 设定 点「① 生成文字」') + '">' + esc(script) + "</textarea>" +

        '<div class="col-h sub"><b>视频分段</b><span class="sub" id="sgSlHint">每段一个连续镜头；时间块一行一块「起-止秒=动作」，紧跟的「名字：话」是这块末尾的台词</span></div>' +
        '<div id="sgSlices" style="padding:0 18px 12px"><span class="sub">读取中…</span></div>' +

        '<div class="col-h sub"><b>这一话的人物</b><span id="sgChrN"></span></div>' +
        '<div class="agrid" id="sgChrBox" style="padding:12px 18px"></div>' +
        '<div class="col-h sub"><b>这一话的场景</b><span id="sgScnN"></span></div>' +
        '<div class="agrid" id="sgScnBox" style="padding:12px 18px"></div>' +
        "</div></div></div>";

      wire(main, st);
    }).catch(function (err) {
      main.innerHTML = UI.card("打不开", "<p>" + esc(err.message) + "</p>");
    });
  }

  /* ───── 视频分段 ───── */
  function slRowHtml(r, i) {
    return '<div class="card sl-row" data-src="' + (r.no || 0) + '" style="padding:10px 12px;margin:8px 0">' +
      '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">' +
      '<b class="sl-no" style="min-width:52px">第 ' + (i + 1) + ' 段</b>' +
      '<label class="sub">地点</label><input class="sl-place" value="' + esc(r.place || "") + '" style="width:120px;height:30px;border-radius:6px">' +
      '<label class="sub">在场</label><input class="sl-cast" value="' + esc(r.cast || "") + '" title="画面里的人，顿号分开" style="width:150px;height:30px;border-radius:6px">' +
      '<label class="sub">秒</label><input class="sl-sec" type="number" min="3" max="30" step="1" value="' + esc(String(Math.round(+r.seconds || 0))) + '" style="width:56px;height:30px;border-radius:6px">' +
      '<span class="sp"></span>' +
      '<button class="btn small sl-add" title="在这段后面加一段">＋</button>' +
      '<button class="btn small sl-del" title="删掉这段">✕</button></div>' +
      '<div style="display:flex;gap:8px;align-items:center;margin-top:6px"><label class="sub" style="min-width:52px">机位</label>' +
      '<input class="sl-cam" value="' + esc(r.camera || "") + '" style="flex:1;height:30px;border-radius:6px"></div>' +
      '<textarea class="sl-blocks prompt" style="min-height:60px;margin-top:6px;font-size:13px;line-height:1.6" ' +
      'placeholder="0-4秒=谁做什么\n    名字：这一块末尾说的话\n4-9秒=…">' + esc(r.blocks_text || "") + '</textarea></div>';
  }
  function loadSlices(main, st) {
    var box = document.getElementById("sgSlices");
    if (!box) return;
    api("/slices", { ep: cur }).then(function (r) {
      var d = r.data || r;
      var rows = d.rows || [];
      if (!rows.length) {
        var where = cur > 1 ? '点上面的「生成本话文字」' : '去 ⚙️ 设定 点「① 生成文字」';
        box.innerHTML = '<span class="sub">' + (d.has_script ? '还没有分段：' + where : '还没有剧本和分段：' + where) + '</span>';
        return;
      }
      box.innerHTML = (d.stale ? '<div class="sub" style="color:#c47d00;margin:6px 0">⚠ 剧本改过了，这份分段是按旧剧本分的：下次「生成文字/图片/视频」会先按新剧本重分（你手改的分段会被替换）。' +
          '<a href="#" id="sgReslice" style="margin-left:8px">现在就重分</a></div>' : "") +
        rows.map(slRowHtml).join("") +
        '<div class="sub" style="margin-top:6px">共 ' + rows.length + ' 段 · ' + Math.round(d.total_seconds || 0) + ' 秒</div>';
      wireSlices(main, st);
    }).catch(function (e) { box.innerHTML = '<span class="sub">分段读不出来：' + esc(e.message) + '</span>'; });
  }
  function slCollect() {
    return [].map.call(document.querySelectorAll("#sgSlices .sl-row"), function (row) {
      var q = function (c) { return row.querySelector(c); };
      return { src_no: +row.getAttribute("data-src") || 0, place: q(".sl-place").value.trim(), cast: q(".sl-cast").value.trim(),
               seconds: +q(".sl-sec").value || 0, camera: q(".sl-cam").value.trim(), blocks_text: q(".sl-blocks").value };
    });
  }
  function slRenumber() {
    [].forEach.call(document.querySelectorAll("#sgSlices .sl-row .sl-no"), function (b, i) { b.textContent = "第 " + (i + 1) + " 段"; });
  }
  function saveSlices(main, st) {
    setState("分段保存中…");
    return api("/slices-save", { ep: cur, rows: slCollect() })
      .then(function (r) {
        var d = r.data || r;
        setState("分段已保存" + (d.changed_from ? "（第 " + d.changed_from + " 段起的提示词会按新分段重写）" : ""));
        /* 新加的段保存后才有段号：把 data-src 对上，不重画（免得打断编辑） */
        [].forEach.call(document.querySelectorAll("#sgSlices .sl-row"), function (row, i) { row.setAttribute("data-src", i + 1); });
      })
      .catch(function (e) { setState("分段保存失败：" + e.message, true); });
  }
  function wireSlices(main, st) {
    var box = document.getElementById("sgSlices");
    if (!box) return;
    box.oninput = function () { setState("分段有改动…"); debounce("slices", function () { saveSlices(main, st); }); };
    [].forEach.call(box.querySelectorAll(".sl-blocks"), function (ta) { autoGrow(ta, 60); ta.addEventListener("input", function () { autoGrow(ta, 60); }); });
    [].forEach.call(box.querySelectorAll(".sl-del"), function (b) {
      b.onclick = function () {
        if (box.querySelectorAll(".sl-row").length <= 1) { UI.toast("至少留一段", "err"); return; }
        b.closest(".sl-row").remove(); slRenumber(); saveSlices(main, st);
      };
    });
    [].forEach.call(box.querySelectorAll(".sl-add"), function (b) {
      b.onclick = function () {
        var row = b.closest(".sl-row");
        var tmp = document.createElement("div");
        tmp.innerHTML = slRowHtml({ no: 0, place: row.querySelector(".sl-place").value, cast: row.querySelector(".sl-cast").value,
                                    seconds: 12, camera: row.querySelector(".sl-cam").value, blocks_text: "0-4秒=\n4-8秒=\n8-12秒=" }, 0);
        row.parentNode.insertBefore(tmp.firstChild, row.nextSibling);
        slRenumber(); wireSlices(main, st); setState("加了一段，填好会自动保存");
      };
    });
    var rs = document.getElementById("sgReslice");
    if (rs) rs.onclick = function (ev) {
      ev.preventDefault();
      if (!confirm("按现在的剧本重新分段？你手改的分段会没掉。")) return;
      api("/reslice", { ep: cur })
        .then(function () { watchJob(main, st, "重新分好了"); })
        .catch(function (e) { UI.toast(e.message, "err"); });
    };
  }

  /* 后台任务：轮询到结束再刷新 */
  function watchJob(main, st, okMsg) {
    if (poll) clearInterval(poll);
    poll = setInterval(function () {
      api("/progress").then(function (j) {
        setState(j.err ? ("❌ " + j.err) : ("⏳ " + (j.step || "")));
        if (j.running) return;
        clearInterval(poll); poll = null;
        if (j.err) { UI.toast("失败：" + j.err, "err"); return; }
        if (okMsg) UI.toast(okMsg);
        render(main, st);
      }).catch(function () {});
    }, 2500);
  }

  function wire(main, st) {
    var id = function (x) { return document.getElementById(x); };
    [].forEach.call(document.querySelectorAll(".epitem"), function (x) {
      x.onclick = function () { cur = +x.getAttribute("data-no"); render(main, st); };
    });
    var addEpBtn = id("sgAddEp");
    if (addEpBtn) addEpBtn.onclick = function () {
      if (addEpBtn.disabled) return;
      var originalLabel = addEpBtn.textContent;
      addEpBtn.disabled = true;
      addEpBtn.textContent = "正在新建…";
      return api("/add-episode", {})
        .then(function (r) {
          cur = (r && r.no) || cur;
          location.hash = "#story?ep=" + cur;
          UI.toast("第 " + cur + " 话已建好，在右侧写这一话讲什么");
          return render(main, st).then(function () {
            var brief = id("sgBrief");
            if (brief) brief.focus();
          });
        })
        .catch(function (e) {
          addEpBtn.disabled = false;
          addEpBtn.textContent = originalLabel;
          UI.toast("新建失败：" + e.message, "err");
        });
    };
    var unlockBtn = id("sgUnlock");
    if (unlockBtn) unlockBtn.onclick = function () {
      if (!confirm("解锁第 " + cur + " 话？\n后面已经写好的话可能要重新规划。")) return;
      api("/unlock", { no: cur }).then(function () { render(main, st); });
    };

    /* 剧本框：自动保存 → 正文＝剧本，改了分段会标过期 */
    var sc = id("sgScript"), cnt = id("sgScriptCount");
    if (sc) {
      var countIt = function () { if (cnt) cnt.textContent = String(sc.value || "").replace(/\s/g, "").length + " 字"; };
      countIt(); autoGrow(sc, 260);
      sc.dataset.saved = sc.value;
      sc.addEventListener("input", function () {
        autoGrow(sc, 260); countIt();
        if (sc.readOnly) return;
        setState("剧本有改动…");
        debounce("script", function () {
          if (sc.dataset.saved === sc.value) { setState("剧本没变"); return; }
          var v = sc.value;
          api("/script-save", { ep: cur, text: v }).then(function (r) {
            var d = r.data || r;
            sc.dataset.saved = v;
            setState(d.slices_stale ? "剧本已保存；分段是按旧剧本分的，下次生成前会重分" : "剧本已保存");
            if (d.slices_stale) loadSlices(main, st);
          }).catch(function (e) { setState("剧本保存失败：" + e.message, true); });
        });
      });
      window.addEventListener("resize", function () { autoGrow(sc, 260); });
      setTimeout(function () { autoGrow(sc, 260); }, 300);
    }
    /* 这一话讲什么：自动保存（口述改了，下次生成文字会重写剧本） */
    var br = id("sgBrief");
    if (br) {
      autoGrow(br, 100); br.dataset.saved = br.value;
      var saveBriefNow = function () {
        var v = br.value.trim(), e0 = ep(cur) || {};
        if (!v) return Promise.reject(new Error("先写这一话讲什么"));
        if (br.dataset.saved === br.value) return Promise.resolve();
        setState("正在保存本话内容…");
        return api("/episode-input", { ep: cur, brief: v, pace: e0.story_pace || "适中", notes: e0.episode_notes || "", shooting_notes: e0.shooting_notes || "" })
          .then(function () { br.dataset.saved = br.value; setState("本话内容已保存"); });
      };
      br.addEventListener("input", function () {
        autoGrow(br, 100);
        if (br.readOnly) return;
        setState("口述有改动…");
        debounce("brief", function () {
          if (br.dataset.saved === br.value) return;
          saveBriefNow()
            .then(function () { setState(cur > 1 ? "本话内容已保存；可以生成文字" : "口述已保存；下次「生成文字」会按它重写剧本"); })
            .catch(function (e) { setState("口述保存失败：" + e.message, true); });
        });
      });
      var gen = id("sgGenText");
      if (gen) gen.onclick = function () {
        if (!br.value.trim()) { UI.toast("先写第 " + cur + " 话讲什么", "err"); br.focus(); return; }
        gen.disabled = true;
        saveBriefNow()
          .then(function () { return api("/make", { ep: cur, stage: "text" }); })
          .then(function () {
            setState("正在生成第 " + cur + " 话的剧本和分段…");
            UI.toast("开始生成第 " + cur + " 话文字；只做剧本和分段，不出视频");
            watchJob(main, st, "第 " + cur + " 话文字已生成，可以检查和修改");
          })
          .catch(function (e) { gen.disabled = false; setState("生成失败：" + e.message, true); UI.toast(e.message, "err"); });
      };
    }
    loadSlices(main, st);
    loadScenes(st);
  }

  /* 人物卡 / 场景卡（只看；图由设定页「② 生成图片」出） */
  function loadScenes(st) {
    var box = document.getElementById("sgScnBox");
    var cbox = document.getElementById("sgChrBox");
    if (!box && !cbox) return;
    window.api.get("/api/assets?story_id=" + st.storyId).then(function (d) {
      var g = d.groups || {};
      var adopted = {};
      (g.visuals || []).forEach(function (v) { if (v.status === "adopted") adopted[v.owner_id] = v.path; });
      function card(name, id, isNew, hint, sub) {
        var p = adopted[id];
        return '<div class="acard"><' + (p
          ? 'img class="acard-im" src="/files/' + esc(String(p).replace(/\\/g, "/")) + '">'
          : 'div class="acard-im acard-none">' + esc(hint || "还没有图") + '</div>') +
          '<div class="acard-h"><b>' + esc(name) + "</b>" +
          (isNew ? '<span class="stat-green" style="margin-left:6px;font-size:12px">新</span>' : "") + "</div>" +
          (sub ? '<div class="sub" style="font-size:12px;line-height:1.5;margin-top:4px">' + esc(sub) + "</div>" : "") + "</div>";
      }
      if (cbox) {
        var cs = (g.characters || []).filter(function (c) { return !c.auto_incidental && +(c.first_episode || 1) <= +cur; });
        var cn = document.getElementById("sgChrN");
        if (cn) cn.textContent = cs.length + " 人";
        cbox.innerHTML = cs.length ? cs.map(function (c) {
          var sub = [c.sex, c.age ? c.age + "岁" : "", c.clothing].filter(Boolean).join(" · ");
          return card(c.name || c.character_id, c.character_id, +(c.first_episode || 1) === +cur, "还没有设定图（设定页「② 生成图片」）", sub.slice(0, 60));
        }).join("") : '<div class="sub">还没有人物卡。设定页「① 生成文字」会从剧本里整理人物。</div>';
      }
      if (box) {
        var mine = (g.scenes || []).filter(function (s) { return +s.episode === +cur; });
        var n = document.getElementById("sgScnN");
        if (n) n.textContent = mine.length + " 个";
        box.innerHTML = mine.length ? mine.map(function (s) {
          return card(s.name, s.scene_id, false, "还没有场景图（设定页「② 生成图片」）", String(s.space || "").slice(0, 60));
        }).join("") : '<div class="sub">还没有场景卡。设定页「① 生成文字」会从剧本里整理场景。</div>';
      }
    }).catch(function () {});
  }

  window.Pages.story = { render: render };
})();
