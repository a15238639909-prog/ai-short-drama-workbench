/* pages/assets.js — 🗂 资源栏：保存下来的人设/场景图，视频用图 + 漫画用人设文字。 */
window.Pages = window.Pages || {};
window.Pages.assets = (function () {
  var tab = "all", D = null;
  var EP = 1, EPS = [], TL = null, FILMS = null, MPOLL = null;     /* P399：分镜 / 成片 标签的数据 */
  function esc(s) { return UI.esc(s); }
  function load(main, st) {
    return window.api.get("/api/assets?story_id=" + st.storyId).then(function (d) {
      D = d;
      render(main, st);
    });
  }
  function fileUrl(p) { return "/files/" + esc(String(p || "").replace(/\\/g, "/")); }
  /* ── 分镜标签：这一话每段视频 + 勾选合并 ── */
  function loadSegments(main, st) {
    return window.api.post("/api/saga/" + st.storyId + "/get", {}).then(function (r) {
      EPS = ((r && r.saga && r.saga.episodes) || []).map(function (e) { return +e.no; }).filter(Boolean).sort(function (a, b) { return a - b; });
      if (!EPS.length) EPS = [1];
      if (EPS.indexOf(EP) < 0) EP = EPS[0];
      return window.api.post("/api/timeline/" + st.storyId + "/get", { ep: EP });
    }).then(function (r) { TL = (r && r.timeline) || { scenes: [] }; });
  }
  function segmentsHtml() {
    var segs = [];
    ((TL && TL.scenes) || []).forEach(function (sc) { (sc.segments || []).forEach(function (g) { segs.push(g); }); });
    var epSel = '<label style="font-size:14px">第 <select id="pdEp" style="height:32px;border-radius:7px;padding:0 6px">' +
      EPS.map(function (n) { return '<option value="' + n + '"' + (n === EP ? " selected" : "") + '>' + n + '</option>'; }).join("") + '</select> 话</label>';
    var head = '<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px">' + epSel +
      '<button class="btn small" id="pdAll">全选</button><button class="btn small" id="pdNone">全不选</button>' +
      '<span style="flex:1"></span>' +
      '<button class="btn small primary" id="pdMergeSel">🎞 合并所选</button>' +
      '<button class="btn small" id="pdMergeAll">🎞 合并整话</button>' +
      '<span id="pdMergeNote" class="note-gray" style="width:100%;margin:0"></span></div>';
    if (!segs.length) return head + UI.empty("这一话还没有分镜视频。去 🎬 分镜视频 页出片。");
    return head + '<div class="pgrid">' + segs.map(function (g) {
      var beat = String(g.source_text || "").replace(/^── .*? ──\n?/, "").replace(/\n/g, " / ");
      return '<div class="pcard">' + (g.video
          ? '<video src="' + fileUrl(g.video) + '" controls preload="metadata"></video>'
          : '<div style="aspect-ratio:16/9;background:#f3f0ea;display:flex;align-items:center;justify-content:center;color:#8b8375">还没出片</div>') +
        '<div class="pcard-h"><label style="display:flex;align-items:center;gap:6px"><input type="checkbox" class="pdSeg" value="' + g.no + '"' + (g.video ? "" : " disabled") + '> <b>第 ' + g.no + ' 段</b></label>' +
        '<span>' + Math.round(g.seconds || 0) + 's · ' + esc(g.scene || "") + '</span><span style="flex:1"></span>' +
        '<a href="#timeline?ep=' + EP + '" style="font-size:12px;color:#8a5a2b">去分镜页改 →</a></div>' +
        '<div class="sub" style="padding:0 14px 10px;font-size:12px;color:#8b8375">' + esc(beat.slice(0, 80)) + '</div></div>';
    }).join("") + "</div>";
  }
  function filmsHtml() {
    var items = FILMS || [];
    if (!items.length) return UI.empty("还没有成片。分镜页出完片会自动合成；或在「分镜」标签里勾几段合并。");
    return '<div class="pgrid">' + items.map(function (f) {
      return '<div class="pcard"><video src="' + fileUrl(f.path) + '" controls preload="metadata"></video>' +
        '<div class="pcard-h"><b style="font-size:13px">' + esc(f.name) + '</b><span style="flex:1"></span>' +
        '<span>' + esc(f.when) + ' · ' + Math.round((f.size || 0) / 1048576) + 'MB</span>' +
        '<a href="' + fileUrl(f.path) + '" download style="font-size:12px;color:#8a5a2b;margin-left:8px">⬇ 下载</a></div></div>';
    }).join("") + "</div>";
  }
  function startMerge(main, st, segs) {
    var note = document.getElementById("pdMergeNote");
    window.api.post("/api/timeline/" + st.storyId + "/merge", { ep: EP, segs: segs || [] }).then(function () {
      if (note) note.textContent = "合成中…";
      if (MPOLL) clearInterval(MPOLL);
      MPOLL = setInterval(function () {
        window.api.post("/api/timeline/" + st.storyId + "/progress", { ep: EP }).then(function (r) {
          var j = (r && r.job) || {};
          if (j.running) { if (note) note.textContent = j.step || "合成中…"; return; }
          clearInterval(MPOLL); MPOLL = null;
          if (j.err) { UI.toast("合成失败：" + j.err, "err"); if (note) note.textContent = "❌ " + j.err; return; }
          UI.toast("合成完成，去「成片」标签看");
          tab = "films"; FILMS = null; render(main, st);
        }).catch(function () {});
      }, 2000);
    }).catch(function (e) { UI.toast(e.message, "err"); });
  }
  function missingCards(list) {
    return (list || []).map(function (m) {
      return '<div class="card" style="padding:16px">' +
        '<div style="width:100%;aspect-ratio:16/9;background:#f3f0ea;border-radius:8px;display:flex;align-items:center;justify-content:center;color:#8b8375;font-size:12px">还没有设定图</div>' +
        '<div style="margin:8px 0;font-size:16px">' + esc(m.name || m.id) + '</div>' +
        '<div style="font-size:12px;color:#8b8375;margin-bottom:8px">' + m.kind + ' ｜ 缺设定图，视频没法用它做参考</div>' +
        '<button class="btn small primary" data-firstgen="' + esc(m.id) + '" data-genapi="' + m.api + '">🎭 生成设定图</button> ' +
        '<button class="btn small" data-upload-kind="' + (m.api === "characters" ? "character" : "scene") + '" data-upload-owner="' + esc(m.id) + '">⬆ 上传图</button></div>';
    }).join("");
  }
  function render(main, st) {
    var g = D.groups || {};
    var adopted = (g.visuals || []).filter(function (v) { return v.status === "adopted"; });
    /* 人物采用第一张身份锚点后不再换版；旧候选保留在数据中，但不再进入界面。 */
    var candidates = (g.visuals || []).filter(function (v) {
      return (v.status === "candidate" || v.status === "draft") && v.kind === "scene_master";
    });
    var charByName = {};
    (g.characters || []).forEach(function (c) { charByName[c.character_id] = c; });
    var sceneByName = {};
    (g.scenes || []).forEach(function (s) { sceneByName[s.scene_id] = s; });
    /* 重新生成设定图/换服装走的是 /characters/<id>/master，要的是人物 ID，
       不是 visual_id——原来直接把 visual_id 塞进去，后端必报「人物不存在」。 */
    var ownerMap = {};
    (g.visuals || []).forEach(function (v) { ownerMap[v.visual_id] = v.owner_id; });
    /* 还没有任何设定图的人物/场景也要有卡片，否则第一张图永远没有生成入口
       ——李哲就是这样在资源页里消失的。 */
    var hasAdopted = {};
    adopted.forEach(function (v) { hasAdopted[v.owner_id] = true; });
    var missing = [];
    if (tab === "all" || tab === "characters")
      (g.characters || []).forEach(function (c) {
        if (!hasAdopted[c.character_id]) missing.push({ id: c.character_id, name: c.name, kind: "人物", api: "characters" });
      });
    if (tab === "all" || tab === "scenes")
      (g.scenes || []).forEach(function (sc) {
        if (!hasAdopted[sc.scene_id]) missing.push({ id: sc.scene_id, name: sc.name, kind: "场景", api: "scenes" });
      });
    var items = tab === "candidate" ? candidates
      : (tab === "characters" ? adopted.filter(function (v) { return (v.kind || "").indexOf("character") >= 0; })
        : (tab === "scenes" ? adopted.filter(function (v) { return v.kind === "scene_master"; }) : adopted));
    /* 卡片重设计（用户定的）：图是主角，常用动作只留一排，其余收进「更多」。
       已采用的卡不再显示「保存为采用」这种废按钮；人物和场景分区展示。 */
    var card = function (v) {
      var owner = v.kind === "scene_master" ? sceneByName[v.owner_id] : charByName[v.owner_id];
      var title = owner ? (owner.name || owner.character_id || owner.scene_id) : v.owner_id;
      var text = v.kind === "scene_master"
        ? (owner ? owner.contract_text : "")
        : [owner && owner.look, owner && owner.clothing, owner && owner.identity_anchor].filter(Boolean).join("；");
      var isChar = (v.kind || "").indexOf("character") >= 0;
      var isCand = v.status !== "adopted";
      return '<div class="acard">' +
        (v.path ? '<img class="acard-im" data-big="' + esc(v.path || "") +
                  '" src="/files/' + esc(v.path.replace(/\\/g, "/")) + '">'
          : '<div class="acard-im acard-none">还没有图</div>') +
        '<div class="acard-h"><b>' + esc(title) + "</b>" +
        '<span class="ver">V' + esc(v.visual_version) + "</span>" +
        (isCand ? '<span class="acard-cand">候选</span>' : '<span class="acard-ok">已采用</span>') +
        "</div>" +
        '<div class="acard-act">' +
        (isCand && !isChar ? '<button class="btn small primary" data-act="save" data-id="' + esc(v.visual_id) + '">💾 采用这张</button>' : "") +
        '<button class="btn small" data-vers="' + esc(v.visual_id) + '">换一版</button>' +
        '<button class="btn small" data-act="regenerate" data-id="' + esc(v.visual_id) + '">🔄 重新生成</button>' +
        '<button class="btn small" data-upload-kind="' + (isChar ? "character" : "scene") + '" data-upload-owner="' + esc(v.owner_id) + '" title="选一张本地图替掉这张，上传后直接采用">⬆ 上传替换</button>' +
        '<details class="amore"><summary>⋯</summary><div class="amore-in">' +
        '<button class="btn small" data-copy="' + esc(v.visual_id) + '">📖 复制人设文字</button>' +
        '<button class="btn small" data-exp="' + esc(v.visual_id) + '">⬇ 导出</button>' +
        '<button class="btn small" data-act="expand" data-id="' + esc(v.visual_id) + '">✦ 补充设定细节</button>' +
        "</div></details></div>" +
        '<div id="txt' + esc(v.visual_id) + '" style="display:none">' + esc(text || "") + "</div></div>";
    };
    var grid = function (list) {
      return '<div class="agrid">' + list.map(card).join("") + "</div>";
    };
    var body;
    if (tab === "segments") {
      if (!TL) { loadSegments(main, st).then(function () { render(main, st); }); body = '<div class="card">加载中…</div>'; }
      else body = segmentsHtml();
    } else if (tab === "films") {
      if (!FILMS) { window.api.post("/api/timeline/" + st.storyId + "/films", {}).then(function (r) { FILMS = (r && r.items) || []; render(main, st); }); body = '<div class="card">加载中…</div>'; }
      else body = filmsHtml();
    } else if (tab === "all") {
      var cs = items.filter(function (v) { return (v.kind || "").indexOf("character") >= 0; });
      var ss = items.filter(function (v) { return v.kind === "scene_master"; });
      body = (cs.length ? '<h3 class="asec">👤 人物</h3>' + grid(cs) : "") +
             (ss.length ? '<h3 class="asec">🏛 场景</h3>' + grid(ss) : "") +
             (missing.length ? '<h3 class="asec">⚠ 还缺设定图</h3><div class="agrid">' + missingCards(missing) + "</div>" : "");
      if (!cs.length && !ss.length && !missing.length)
        body = UI.empty("还没有保存的资源。去 📖 故事 点「一键到设定」，或去 ⚙️ 设定 手动生成。", "去故事", function () { location.hash = "#story"; });
    } else {
      body = items.length ? grid(items)
        : (missing.length ? '<div class="agrid">' + missingCards(missing) + "</div>"
           : UI.empty("这个分类还没有内容"));
    }
    main.innerHTML = '<div style="max-width:1680px;margin:0 auto"><h2 style="font-size:20px">📦 成品</h2>' +
      '<div style="display:flex;gap:8px;margin-bottom:16px;align-items:center">' +
      [["all", "全部设定图"], ["characters", "人物"], ["scenes", "场景"], ["segments", "分镜"], ["films", "成片"], ["candidate", "候选"]].map(function (t) {
        return '<button class="btn small ' + (tab === t[0] ? "primary" : "") + '" data-tab="' + t[0] + '">' + t[1] + "</button>";
      }).join("") +
      '<span style="flex:1"></span><button class="btn small" id="btnUpload">⬆ 上传设定图</button></div>' +
      body + "</div>";
    main.querySelectorAll("[data-tab]").forEach(function (b) {
      b.onclick = function () { tab = b.getAttribute("data-tab"); if (tab === "segments") TL = null; if (tab === "films") FILMS = null; render(main, st); };
    });
    /* P399：分镜标签的接线 */
    var pdEp = document.getElementById("pdEp");
    if (pdEp) pdEp.onchange = function () { EP = +pdEp.value || 1; TL = null; render(main, st); };
    var checks = function () { return [].slice.call(main.querySelectorAll(".pdSeg")); };
    var b1 = document.getElementById("pdAll"); if (b1) b1.onclick = function () { checks().forEach(function (c) { if (!c.disabled) c.checked = true; }); };
    var b2 = document.getElementById("pdNone"); if (b2) b2.onclick = function () { checks().forEach(function (c) { c.checked = false; }); };
    var b3 = document.getElementById("pdMergeSel"); if (b3) b3.onclick = function () {
      var segs = checks().filter(function (c) { return c.checked; }).map(function (c) { return +c.value; });
      if (!segs.length) { UI.toast("先勾选要合并的段", "err"); return; }
      startMerge(main, st, segs);
    };
    var b4 = document.getElementById("pdMergeAll"); if (b4) b4.onclick = function () { startMerge(main, st, []); };
    /* 上传设定图：必须先选清楚归属（人物还是场景、哪一个），
       图挂在谁名下就只会出现在谁的参考卡里——人物图和场景图互相选不到 */
    var up = document.getElementById("btnUpload");
    if (up) up.onclick = function () {
      var chars = (D.groups || {}).characters || [];
      var scenes = (D.groups || {}).scenes || [];
      var opts = function (list, key) {
        return list.map(function (x) {
          return '<option value="' + esc(x[key]) + '">' + esc(x.name || x[key]) + "</option>";
        }).join("");
      };
      UI.openModal(
        '<h3 style="margin:0 0 12px">⬆ 上传设定图</h3>' +
        '<p style="margin:0 0 8px"><label><input type="radio" name="upKind" value="character" checked> 👤 人物图</label>　' +
        '<label><input type="radio" name="upKind" value="scene"> 🏛 场景图</label></p>' +
        '<p style="margin:0 0 8px">挂在谁名下：<select id="upOwnerC">' + opts(chars, "character_id") + "</select>" +
        '<select id="upOwnerS" style="display:none">' + opts(scenes, "scene_id") + "</select></p>" +
        '<p style="margin:0 0 12px"><input type="file" id="upFile" accept=".png,.jpg,.jpeg,.webp"></p>' +
        '<button class="btn primary" id="upGo">上传</button>');
      document.querySelectorAll('input[name="upKind"]').forEach(function (r) {
        r.onchange = function () {
          var isChar = document.querySelector('input[name="upKind"]:checked').value === "character";
          document.getElementById("upOwnerC").style.display = isChar ? "" : "none";
          document.getElementById("upOwnerS").style.display = isChar ? "none" : "";
        };
      });
      document.getElementById("upGo").onclick = function () {
        var kind = document.querySelector('input[name="upKind"]:checked').value;
        var owner = document.getElementById(kind === "character" ? "upOwnerC" : "upOwnerS").value;
        var f = document.getElementById("upFile").files[0];
        if (!f) { UI.toast("先选一张图", "err"); return; }
        var rd = new FileReader();
        rd.onload = function () {
          window.api.post("/api/asset/upload", {
            story_id: st.storyId, owner_kind: kind, owner_id: owner,
            filename: f.name, data_b64: rd.result, adopt: 1
          }).then(function () {
            UI.closeModal(); UI.toast("已上传并采用（旧图在「换一版」里）");
            return load(main, st);
          }).catch(function (e) { UI.toast("上传失败：" + e.message, "err"); });
        };
        rd.readAsDataURL(f);
      };
    };
    /* P406：每张卡的「上传替换」——一个隐藏文件框，点哪张卡就记下归属，选完图上传并直接采用 */
    var upOne = document.getElementById("upOne");
    if (!upOne) {
      upOne = document.createElement("input");
      upOne.type = "file"; upOne.id = "upOne"; upOne.accept = ".png,.jpg,.jpeg,.webp"; upOne.style.display = "none";
      document.body.appendChild(upOne);
    }
    var upTarget = null;
    upOne.onchange = function () {
      var f = upOne.files[0]; upOne.value = "";
      if (!f || !upTarget) return;
      var t = upTarget; upTarget = null;
      var rd = new FileReader();
      rd.onload = function () {
        UI.toast("上传中…");
        window.api.post("/api/asset/upload", { story_id: st.storyId, owner_kind: t.kind, owner_id: t.owner, filename: f.name, data_b64: rd.result, adopt: 1 })
          .then(function () { UI.toast("已替换并采用（旧图在「换一版」里）"); return load(main, st); })
          .catch(function (e) { UI.toast("上传失败：" + e.message, "err"); });
      };
      rd.readAsDataURL(f);
    };
    main.querySelectorAll("[data-upload-owner]").forEach(function (b) {
      b.onclick = function () {
        upTarget = { kind: b.getAttribute("data-upload-kind"), owner: b.getAttribute("data-upload-owner") };
        upOne.click();
      };
    });
    main.querySelectorAll("[data-big]").forEach(function (b) {
      b.onclick = function () {
        var p = b.getAttribute("data-big");
        UI.openModal(p ? '<img src="/files/' + esc(p.replace(/\\/g, "/")) + '" style="max-width:100%">' : "<p>这张还没有图</p>");
      };
    });
    /* 标准动作组：人物设定图的重新生成 / 换服装，走 8848 同一套后端能力 */
    window.UIActions.bind(main, {
      regenerate: function (vid) {
        /* P405：人物图 / 场景图都能重出；新图直接采用，旧图留在「换一版」里随时换回 */
        if (!confirm("重新生成这张？新图会直接采用，旧图留在「换一版」里可以换回。约 2～3 分钟。")) return;
        var owner = ownerMap[vid] || vid;
        var api = /^SCENE_/.test(owner) ? "scenes" : "characters";
        UI.toast("已开始重新生成，约 2～3 分钟");
        window.api.post("/api/story/" + st.storyId + "/" + api + "/" + owner + "/master", { redo: true })
          .then(function () { UI.toast("新图已采用"); load(main, st); })
          .catch(function (e) { alert(e.message); });
      },
      regen_sheet: function (vid) {
        window.api.post("/api/story/" + st.storyId + "/characters/" + (ownerMap[vid] || vid) + "/master",
                        { redo: true, sheet: true })
          .then(function () { render(main, st); })
          .catch(function (e) { alert(e.message); });
      },
      change_outfit: function (vid) {
        window.api.post("/api/story/" + st.storyId + "/characters/" + (ownerMap[vid] || vid) + "/update",
                        { change_outfit: true })
          .then(function () { alert("已按新服装重新设计，去看候选"); render(main, st); })
          .catch(function (e) { alert(e.message); });
      },
      expand: function (vid) {
        window.api.post("/api/create/expand", { story_id: st.storyId, target: vid })
          .then(function (r) { alert(r.note || "已补充设定细节"); render(main, st); })
          .catch(function (e) { alert(e.message); });
      },
      save: function (vid) {
        window.api.post("/api/asset/adopt", { story_id: st.storyId, visual_id: vid })
          .then(function () { render(main, st); })
          .catch(function (e) { alert(e.message); });
      }
    });

    main.querySelectorAll("[data-firstgen]").forEach(function (b) {
      b.onclick = function () {
        var id = b.getAttribute("data-firstgen"), api = b.getAttribute("data-genapi");
        b.disabled = true; b.textContent = "生成中…（首次要先启动绘图模型，约 2~3 分钟）";
        UI.toast("已开始生成设定图");
        window.api.post("/api/story/" + st.storyId + "/" + api + "/" + id + "/master",
                        (api === "characters" ? { sheet: true } : {}))
          .then(function () { UI.toast("设定图生成完成"); load(main, st); })
          .catch(function (e) { b.disabled = false; b.textContent = "🎭 生成设定图"; alert(e.message); });
      };
    });
    main.querySelectorAll("[data-copy]").forEach(function (b) {
      b.onclick = function () {
        var txt = document.getElementById("txt" + b.getAttribute("data-copy")).textContent.trim();
        (navigator.clipboard ? navigator.clipboard.writeText(txt) : Promise.reject()).then(function () {
          alert("人设文字已复制");
        }).catch(function () {
          UI.openModal("<pre style='white-space:pre-wrap'>" + esc(txt || "（暂无文字）") + "</pre>");
        });
      };
    });
    main.querySelectorAll("[data-exp]").forEach(function (b) {
      b.onclick = function () {
        window.api.post("/api/asset/" + b.getAttribute("data-exp") + "/export", { story_id: st.storyId })
          .then(function (r) { alert("已导出：" + r.path); })
          .catch(function (e) { alert(e.message); });
      };
    });
    main.querySelectorAll("[data-vers]").forEach(function (b) {
      b.onclick = function () {
        var vid = b.getAttribute("data-vers");
        window.api.get("/api/asset/" + vid + "/versions").then(function (d) {
          UI.openModal("<h3>换一版（候选对比）</h3><div style='display:flex;gap:10px;flex-wrap:wrap'>" +
            d.versions.map(function (v) {
              return '<div style="width:180px;border:2px solid ' + (v.status === "adopted" ? "#8a5a2b" : "#e3ddd2") + ';border-radius:8px;padding:8px">' +
                (v.path ? '<img src="/files/' + esc(v.path.replace(/\\/g, "/")) + '" style="width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:8px">'
                  : '<div style="width:100%;aspect-ratio:16/9;background:#f3f0ea;border-radius:8px"></div>') +
                "<p style='font-size:13px'>" + UI.badge(v.status === "adopted" ? "adopted" : (v.status === "superseded" ? "superseded" : "candidate")) +
                "<span class='ver'>V" + esc(v.visual_version) + "</span></p>" +
                (v.status === "adopted" ? "" : '<button class="btn small primary" data-cv="' + esc(v.visual_id) + '">采用</button>') + "</div>";
            }).join("") + "</div>");
          document.querySelectorAll("[data-cv]").forEach(function (x) {
            x.onclick = function () {
              window.api.post("/api/asset/adopt", { story_id: st.storyId, asset_id: x.getAttribute("data-cv") })
                .then(function () { UI.closeModal(); return load(main, st); })
                .catch(function (e) { alert(e.message); });
            };
          });
        }).catch(function (e) { alert(e.message); });
      };
    });
  }
  return {
    render: function (main, st) {
      if (!st.storyId) {
        main.innerHTML = UI.empty("先在顶部选择一个故事项目");
        return Promise.resolve();
      }
      return load(main, st);
    }
  };
})();
