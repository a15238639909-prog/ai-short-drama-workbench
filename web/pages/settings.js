/* pages/settings.js — ⚙️ 设定栏（8848 做漫画页版式照搬 + 场景卡 + 保存候选）。 */
window.Pages = window.Pages || {};
window.Pages.settings = (function () {
  /* 五维可选值（/api/kits/options）。取不到就退回空表，界面只显示「自动」。 */
  var KD = {};
  var D = null, draft = null;
  function esc(s) { return UI.esc(s); }
  function ph(label, id, val, ph, note) {
    return '<p style="margin:0 0 8px"><label style="display:block;font-size:14px;color:#5a5348;margin-bottom:4px">' + label + "</label>" +
      '<input id="' + id + '" value="' + esc(val) + '" placeholder="' + esc(ph) + '" style="width:100%;height:36px;padding:0 12px">' +
      (note ? '<span class="note-gray">' + note + "</span>" : "") + "</p>";
  }
  /* 文本框是写故事的地方，不是填表单——字号和高度都按"能读进去"来给。
     实测原来一句话框只有 90px 高、14px 字，比一条评论框还小。 */
  function ta(id, val, ph, h) {
    return '<textarea id="' + id + '" placeholder="' + esc(ph) +
      '" style="width:100%;height:' + Math.max(h, 120) + 'px;padding:14px 16px;' +
      'font-size:15px;line-height:1.85;border-radius:10px">' + esc(val || "") + "</textarea>";
  }
  function sel(id, list, val) {
    /* P354：卡上的值不在选项里（旧卡的旧标签、别的世界的款式）也保留成一项，别被顶成「自动」 */
    if (val && (list || []).indexOf(val) < 0) list = (list || []).concat([val]);
    return '<select id="' + id + '" style="width:100%;height:36px">' + list.map(function (x) {
      return '<option' + (x === val ? " selected" : "") + ">" + esc(x) + "</option>";
    }).join("") + "</select>";
  }
  function concreteCharacterOptions(list) {
    return (list || []).filter(function (x) {
      return x !== "自动" && x !== "自动判断" && x !== "" && x != null;
    });
  }
  function charSel(id, list, val) {
    var opts = concreteCharacterOptions(list);
    var chosen = (val && val !== "自动" && val !== "自动判断") ? val : (opts[0] || "");
    return sel(id, opts, chosen);
  }
  function dataList(id, list) {
    return '<datalist id="' + id + '">' + (list || []).map(function (x) {
      return '<option value="' + esc(x) + '"></option>';
    }).join("") + '</datalist>';
  }
  /* 出视频的三个参数（用户 2026-09-09 定）。纯枚举，不进 style_presets——
     那是带语义解释表的预设，塞进去会污染预设表编辑器。 */
  var SIZE_TIER_OPT = ["0.4", "0.5", "0.6", "0.7", "0.8", "0.9", "1.0"];
  var STEPS_OPT = ["10", "15", "20", "25"];
  var RATIO_OPT = ["16:9", "3:4", "1:1"];

  function optLabel(label, kind, sourceId, panelId, sexId) {
    return '<span class="option-label"><span>' + esc(label) + '</span>' +
      '<button type="button" class="option-detail-btn" data-option-detail="' + esc(kind) +
      '" data-option-source="' + esc(sourceId || "") + '" data-option-panel="' + esc(panelId) + '"' +
      (sexId ? ' data-option-sex="' + esc(sexId) + '"' : '') + '>详细</button></span>';
  }
  function optionPanel(id) {
    return '<div class="option-detail-panel" id="' + esc(id) + '" hidden></div>';
  }
  function outfitOptionsFor(world) {
    var byWorld = (D && D.outfit_presets_by_world) || {};
    return byWorld[world] || (D && D.outfit_presets) || ["自动", "自定义"];
  }
  function joinCharacterDetails(c) {
    return "声线：" + (c.voice || "") + "\n" +
      "性格描述：" + (c.personality || "") + "\n" +
      "服装描述：" + (c.clothing || "") + "\n" +
      "外貌与识别特征：" + (c.appearance_details || c.other || c.look || "");
  }
  function splitCharacterDetails(text, current) {
    var out = { hair: "", voice: "", personality: "", clothing: "", appearance_details: "" };
    var active = "", matched = false;
    String(text || "").split(/\r?\n/).forEach(function (line) {
      var m = line.match(/^\s*(发型|声线|性格描述|服装(?:描述)?|外貌与识别特征|其他(?:要求)?)\s*[：:]\s*(.*)$/);
      if (m) {
        matched = true;
        active = m[1].indexOf("发型") === 0 ? "hair" :
          (m[1].indexOf("声线") === 0 ? "voice" :
          (m[1].indexOf("性格描述") === 0 ? "personality" :
          (m[1].indexOf("服装") === 0 ? "clothing" : "appearance_details")));
        out[active] = m[2].trim();
      } else if (line.trim()) {
        var key = active || "appearance_details";
        out[key] += (out[key] ? "\n" : "") + line.trim();
      }
    });
    if (!matched) {
      out.hair = (current || {}).hair || "";
      out.voice = (current || {}).voice || "";
      out.personality = (current || {}).personality || "";
      out.clothing = (current || {}).clothing || "";
      out.appearance_details = String(text || "").trim();
    }
    return out;
  }
  function imgPlaceholder(t1, t2) {
    return '<div style="aspect-ratio:16/9;min-height:200px;width:100%;height:100%;object-fit:cover;background:#f3f0ea;border-radius:8px;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#8b8375;font-size:14px">' +
      "<div>" + esc(t1) + "</div><div style='font-size:12px'>" + esc(t2) + "</div></div>";
  }
  function adoptedMap() {
    var m = {};
    (D.visuals || []).forEach(function (v) { if (v.status === "adopted") m[v.owner_id] = v; });
    return m;
  }
  function candidatesOf(owner) {
    return (D.visuals || []).filter(function (v) {
      return v.owner_id === owner && (v.status === "candidate" || v.status === "draft");
    });
  }
  /* 「生成候选」整块删掉（用户定的）：所有生成过的设定图都在历史里，
     在 🗂 资源 的「未保存的候选」里挑，不用在设定页挤一排缩略图。 */
  function candRow() { return ""; }

  function loadKitDims() {
    /* 不缓存：预设表里新加的影片类型/角色审美/视点，进设定页就要看到
       （2026-09-02 用户实测：加了选项前端不显示，就是这里缓存住了）。请求很小。 */
    return window.api.post("/api/kits/options", {})
      .then(function (d) { KD = (d && d.dims) || {}; KD._kits = (d && d.kits) || []; return KD; })
      .catch(function () { return KD; });
  }
  function load(main, st) {
    return loadKitDims().then(function () {
      return window.api.post("/api/project/settings", { story_id: st.storyId });
    }).then(function (d) {
      D = d;
      render(main, st);
    });
  }
  /* 页面内提示条：比 alert 好在不挡操作、能一直看着 */
  function banner(main, text, warn) {
    var old = document.getElementById("psBanner");
    if (old) old.remove();
    var el = document.createElement("div");
    el.id = "psBanner";
    el.style.cssText = "background:" + (warn ? "#fdf0e8" : "#eaf6ec") +
      ";border:1px solid " + (warn ? "#e0b9a0" : "#bfe0c6") +
      ";border-radius:12px;padding:12px 16px;margin:0 0 16px;font-size:14px;color:#2a2620";
    el.textContent = text;
    main.insertBefore(el, main.firstChild);
    setTimeout(function () { if (el.parentNode) el.remove(); }, 12000);
  }

  function renderDraft(main, st) {
    if (!draft) return;
    var d = draft;
    main.innerHTML += '<div class="card" style="margin-top:16px;border:2px solid #8a5a2b"><h3 style="font-size:16px">✨ 一句话生成的全部设定（草稿，点「💾 保存这张」才落盘）</h3>' +
      '<p style="font-size:14px">世界类型：' + esc(d.world_type || "") + " ｜ 画风：" + esc(d.style || "") +
      ' <button class="btn success" id="saveWS" style="height:36px;border-radius:8px">💾 保存这张</button></p>' +
      '<p class="note-gray">' + esc(d.story || "") + "</p>" +
      '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px">' +
      (d.characters || []).map(function (c, i) {
        return '<div class="card" style="margin:0"><b style="font-size:14px">人物草稿 ' + esc(c.name || "") + "</b>" +
          '<div style="font-size:13px;line-height:1.6;margin:6px 0">' +
          "年龄：" + esc(c.age || "") + "<br>外貌：" + esc(c.look || "") + "<br>身材：" + esc(c.build || "") +
          "<br>性格与神态：" + esc(c.behavior_anchor || c.personality || "") + "<br>服装：" + esc(c.clothing || "") +
          "<br>外貌与识别特征：" + esc(c.appearance_details || c.features || c.other || "") + "</div>" +
          '<button class="btn success" data-savec="' + i + '" style="height:36px;border-radius:8px">💾 保存这张</button></div>';
      }).join("") +
      (d.scenes || []).map(function (s, i) {
        return '<div class="card" style="margin:0"><b style="font-size:14px">场景草稿 ' + esc(s.name || "") + "</b>" +
          '<div style="font-size:13px;line-height:1.6;margin:6px 0">' +
          "空间：" + esc(s.space || "") + "<br>尺度：" + esc(s.scale || "") + "<br>纵深：" + esc(s.depth || "") +
          "<br>光线：" + esc(s.light || "") + "</div>" +
          '<button class="btn success" data-saves="' + i + '" style="height:36px;border-radius:8px">💾 保存这张</button></div>';
      }).join("") +
      "</div></div>";
    document.getElementById("saveWS").onclick = function () {
      window.api.post("/api/project/settings", {
        story_id: st.storyId,
        settings: {
          world_type: d.world_type, style: d.style,
          one_line: d.one_line || ((document.getElementById("psOne") || {}).value || (D.settings || {}).one_line || "")
        }
      }).then(function () { alert("世界类型 / 画风已保存（可再改）"); return load(main, st); })
        .catch(function (e) { alert(e.message); });
    };
    document.querySelectorAll("[data-savec]").forEach(function (b) {
      b.onclick = function () {
        var c = d.characters[+b.getAttribute("data-savec")];
        window.api.post("/api/story/" + st.storyId + "/characters/characters", {
          name: c.name, age: c.age, appearance_details: c.appearance_details || c.look || c.features,
          build: c.build, behavior_anchor: c.behavior_anchor || c.personality,
          clothing: c.clothing
        }).then(function () { alert("人物草稿已保存（可再改）"); return load(main, st); })
          .catch(function (e) { alert(e.message); });
      };
    });
    document.querySelectorAll("[data-saves]").forEach(function (b) {
      b.onclick = function () {
        var s = d.scenes[+b.getAttribute("data-saves")];
        window.api.post("/api/story/" + st.storyId + "/scenes/scenes", {
          name: s.name, contract_text: s.space, scale: s.scale, depth: s.depth, main_light: s.light
        }).then(function () { alert("场景草稿已保存（缺尺度会自动补默认）"); return load(main, st); })
          .catch(function (e) { alert(e.message); });
      };
    });
  }
  /* 【冻结线】用户定的流程：一句话 → 框架 → 设定 → 确定 → 才写正文。
     冻结之前一个字正文都不写，所以随便改都不心疼；冻结之后不再回头猜设定。
     解冻不删任何东西，只把"哪几话可能对不上"列出来，改不改由人决定。 */
  function lockBar(lock, impact) {
    lock = lock || {}; impact = impact || {};
    if (!lock.locked) {
      return '<button class="btn" id="lockSet" style="height:38px;border-radius:8px">🔒 确定设定并进入剧本页</button>';
    }
    var warn = "";
    if ((impact.labels || []).length) {
      warn = '<span class="note-gray" style="color:#a4552b">冻结后改过：' +
        esc((impact.labels || []).join("、")) +
        ((impact.episodes || []).length
          ? '；可能对不上的话次：第 ' + (impact.episodes || []).join("、") + " 话" : "") +
        (impact.need_repaint ? "；建议重画设定图" : "") + "</span>";
    }
    return '<span class="stat-green" style="font-size:14px">🔒 设定已冻结</span>' +
      '<button class="btn" id="unlockSet" style="height:38px;border-radius:8px">解冻修改</button>' + warn;
  }


  /* ---------- 序号徽章：几个大块共用 ---------- */
  function stepNo(n) {
    return '<span style="display:inline-flex;width:24px;height:24px;border-radius:50%;' +
      'background:#8a5a2b;color:#fff;align-items:center;justify-content:center;' +
      'font-size:14px;margin-right:8px">' + n + '</span>';
  }
  function slotOf(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    return { get innerHTML() { return el.innerHTML; },
             set innerHTML(v) { el.innerHTML = v; } };
  }

  /* ---------- 5 第一话故事（步骤1展示，可改）---------- */
  /* 用户全程手动：第一话故事简介他自己看、自己改，改完再往下生成剧本。 */
  function saveEp1Brief(st, quiet) {
    var ta2 = document.getElementById("ep1Brief");
    if (!ta2 || ta2.dataset.episodeExists !== "1") return Promise.resolve({ skipped: true });
    var v = ta2.value || "";
    if (ta2.dataset.savedValue === v) return Promise.resolve({ skipped: true });
    return window.api.post("/api/saga/" + st.storyId + "/save-episode", { no: 1, brief: v })
      .then(function (j) {
        ta2.dataset.savedValue = v;
        if (!quiet) UI.toast("第一话故事已自动保存");
        return j;
      });
  }
  function saveEp1Prose(st, quiet) {
    var ta = document.getElementById("ep1Prose");
    if (!ta || ta.dataset.episodeExists !== "1") return Promise.resolve({ skipped: true });
    var v = ta.value || "";
    if (ta.dataset.savedValue === v) return Promise.resolve({ skipped: true });
    return window.api.post("/api/saga/" + st.storyId + "/save-episode", { no: 1, prose: v })
      .then(function (j) {
        ta.dataset.savedValue = v;
        if (!quiet) UI.toast("第一话原文已自动保存");
        return j;
      });
  }
  function renderEp1(main, st) {
    var slot = slotOf("slotEp1"); if (!slot) return;
    slot.innerHTML =
      '<div class="card" style="margin-top:16px"><h3 style="font-size:16px">' + stepNo(7) +
      '第一话故事（生成结果，可改）</h3>' +
       /* ── 故事简介（可改 + AI扩写）── */
       '<p class="note-gray" style="margin:0 0 6px"><b>故事简介</b>：这一话大致讲什么，你自己看、自己改。' +
       '简介是下一步「按设定重新生成故事」写原文的依据。</p>' +
       '<textarea id="ep1Brief" style="width:100%;height:120px;padding:12px 14px;' +
       'font-size:15px;line-height:1.8;border-radius:10px" placeholder="还没有简介，点上面「全部生成」">读取中…</textarea>' +
       '<div style="text-align:right;margin:6px 0 16px"><button class="btn" id="expandBrief">✦ AI扩写简介</button></div>' +
       /* ── 第一话原文（可改 + AI扩写 + 按原文生成剧本）── */
       '<p class="note-gray" style="margin:0 0 6px"><b>第一话原文</b>（小说体）：真正的故事正文，可直接改。' +
       '满意了点「按原文生成剧本」，剧本页就按这份原文的节奏出分镜。</p>' +
       '<textarea id="ep1Prose" style="width:100%;height:300px;padding:14px 16px;' +
       'font-size:15px;line-height:1.9;border-radius:10px" placeholder="还没有原文，点上面「全部生成」">读取中…</textarea>' +
       /* P309：重新生成/生剧本/体检/生视频收进折叠里，首次打开不用面对一排按钮 */
       '<details style="margin-top:8px"><summary style="cursor:pointer;font-size:13px;color:#5a5348">更多操作（只改第一话：重新生成正文 / 按原文生成剧本 / 体检 / 生视频）</summary>' +
       '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:8px;flex-wrap:wrap">' +
       '<button class="btn" id="regenStory">🔁 按设定重新生成故事</button>' +
       '<button class="btn" id="makeScript">🎬 按原文生成剧本</button><button class="btn small" id="healthBtn" title="正文/剧本/提示词三层红绿灯">🩺 体检</button>' +
       '<button class="btn" id="genVideo" title="生成场景图＋前五段分镜提示词＋前五段视频（需要已有剧本）">🎥 生视频</button>' +
       '</div>' +
       '<p class="note-gray" style="margin:8px 0 0;font-size:13px">改完上面的人设和故事简介后，' +
       '点「按设定重新生成故事」，会按你现在的<b>人设＋简介</b>重写一版原文；对味了再「按原文生成剧本」。第二话起去上面「选一话来做」。</p></details>' +
       '</div>';
    window.api.post("/api/saga/" + st.storyId + "/framework", {}).then(function (j) {
      var eps = (j && j.episodes) || [];
      var e0 = eps.length ? eps[0] : null;
      var tb = document.getElementById("ep1Brief");
      var tp = document.getElementById("ep1Prose");
      if (tb) {
        tb.value = e0 ? (e0.brief || e0.logline || "") : "";
        tb.dataset.episodeExists = e0 ? "1" : "0";
        tb.dataset.savedValue = tb.value;
        tb.onblur = function () {
          saveEp1Brief(st, false).catch(function (e) { UI.toast(e.message, "err"); });
        };
      }
      if (tp) {
        tp.value = e0 ? (e0.prose || "") : "";
        tp.dataset.episodeExists = e0 ? "1" : "0";
        tp.dataset.savedValue = tp.value;
        tp.onblur = function () {
          saveEp1Prose(st, false).catch(function (e) { UI.toast(e.message, "err"); });
        };
      }
    });
  }

  /* ---------- 6 冻结线（挪到最后） ---------- */
  /* 原来这个按钮和「全部生成」并排在页面中段，也就是在人物卡、场景卡、
     场次表的**上面**——你要冻结的东西一半在按钮下面、一半在另一个页面。
     顺序是反的。现在放到所有要确认的内容之后。 */
  function renderLock(main, st) {
    var slot = slotOf("slotLock"); if (!slot) return;
    slot.innerHTML =
      '<div class="card" style="margin-top:16px;border:2px solid #8a5a2b">' +
      '<h3 style="font-size:16px">' + stepNo(8) + '按设定重新生成故事</h3>' +
      '<p class="note-gray" style="margin:0 0 10px">改完了人设和故事简介？点这里——' +
      '按当前<b>故事要求</b>和<b>本话安排</b>重写第一话正文，并核对事实与情节。' +
      '不满意可以反复改设定、反复重生成，直到原文对味，再去「按原文生成剧本」。</p>' +
      '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">' +
      '<button class="btn primary" id="regenStory" style="height:38px;border-radius:8px">🔁 按设定重新生成故事</button>' +
      '</div></div>';
  }

  /* ---------- 0 先说想法 → 一张安排（第一次确认） ---------- */
  /* 用户定的产品目标：一个输入框、两次确认，完成一话。这张卡是第一次确认：
     想法 → 安排卡 → 像聊天一样改 → 「就按这个做」落盘。落盘由后端 confirm
     写进写作链读的那两个字段（plan_rows / story_design），前端一个字都不碰，
     只画后端给回来的 settings.arrangement——画的永远是盘上那份，不会和写作链两张皮。 */
  var AR_DRAFT_LABEL = "给我一张安排";
  var AR_IDEA_PH = "精灵老板和年轻学徒修一盏旧灯，日常聊天多一点。学徒开始想换新的，后来理解老板为什么要修。最后灯亮起来，温暖一点。";
  var AR_MOODS = ["日常聊天", "紧张刺激", "温暖感人", "安静氛围"];
  var AR_DURS = [["30", "30 秒"], ["60", "1 分钟"], ["120", "2 分钟"], ["custom", "自定义"]];
  var AR_ITEMS = [["what", "第一话讲什么"], ["shoot", "主要拍什么"], ["how", "怎么拍"],
                  ["pace", "节奏快还是慢"], ["who", "主角是谁"], ["where", "用哪些场景"]];
  var AR_CELL = 'style="padding:8px;vertical-align:top;font-size:14px;line-height:1.7"';

  /* 标签由后端代码判、不让模型自报；前端只认「你的要求」四个字，其余一律画成「建议」，
     这样模型哪怕在 source 里瞎填也只会被降级成灰标，不会冒充用户的话。 */
  function arTag(source) {
    var mine = String(source || "") === "你的要求";
    return '<span style="display:inline-block;font-size:11px;line-height:1;padding:3px 6px;border-radius:6px;' +
      'white-space:nowrap;vertical-align:middle;flex:none;' +
      (mine ? "background:#8a5a2b;color:#fff" : "background:#e6e1d8;color:#5a5348") + '">' +
      (mine ? "你的要求" : "建议") + "</span>";
  }
  /* 「你的要求」的条目里，系统补的那几个分句单独点出来——用户的例子：
     "老板为什么珍惜这盏灯"是系统补的，就要让你看见。added 由后端按原话对照算，前端只画。 */
  function arAdded(list) {
    var xs = (list || []).map(function (x) { return String(x || "").trim(); }).filter(Boolean);
    if (!xs.length) return "";
    return '<div class="note-gray" style="margin:2px 0 0;font-size:12px">其中系统补的：' + esc(xs.join("；")) + '</div>';
  }
  /* 人物一行：名字（身份，关系）。没起名的角色拿身份当名字，别显示成空括号。 */
  function arPeopleLine(people) {
    return (people || []).map(function (p) {
      var name = String(p.name || p.role || "").trim();
      var extra = [p.role, p.relation].map(function (x) { return String(x || "").trim(); })
        .filter(function (x) { return x && x !== name; });
      return name ? esc(name) + (extra.length ? "（" + esc(extra.join("，")) + "）" : "") : "";
    }).filter(Boolean).join("；");
  }
  function arCardHtml(a) {
    if (!a || !a.items) return "";
    var items = a.items || {}, beats = a.beats || [], budget = a.budget || [];
    var who = items.who || {}, where = items.where || {}, how = items.how || {}, pace = items.pace || {};
    var ver = "v" + (+a.version || 1);
    var confirmed = a.status === "confirmed";
    var dur = +a.duration_sec || 0;
    var total = budget.reduce(function (n, b) { return n + (+b.seconds || 0); }, 0);
    var h = '<div style="margin-top:14px;border-top:1px dashed #d9d2c5;padding-top:12px">' +
      '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px">' +
      '<b style="font-size:15px">本话安排</b>' +
      (confirmed
        ? '<span class="stat-green" style="font-size:14px">✅ 已确认 ' + esc(ver) + '</span>'
        : '<span class="stat-orange" style="font-size:14px">草稿 ' + esc(ver) + '，看一遍，不对就在下面说</span>') +
      '</div>';
    if (a.whole_story) {
      h += '<div style="background:#fdf0e8;border:1px solid #e0b9a0;border-radius:10px;padding:10px 14px;' +
        'margin-bottom:10px;font-size:14px;line-height:1.7">你写的是整部故事的构想。' +
        (a.first_episode_ends_at
          ? '第一话准备讲到这里：<b>' + esc(a.first_episode_ends_at) + '</b>'
          : '第一话只讲开头一段，讲到哪里可以在下面告诉它。') + '</div>';
    }
    /* 六项表：每格右侧一个标签，一眼看出哪些是系统补的 */
    h += '<table style="margin-bottom:12px"><thead><tr><th style="width:120px">项目</th><th>方案</th></tr></thead><tbody>';
    AR_ITEMS.forEach(function (pair) {
      var it = items[pair[0]] || {}, sub = "";
      if (pair[0] === "how" && how.geometry) sub = esc("画面关系：" + how.geometry);
      if (pair[0] === "pace" && pace.value) sub = esc(pace.value);
      if (pair[0] === "who") sub = arPeopleLine(who.people);
      if (pair[0] === "where" && (where.places || []).length) sub = esc((where.places || []).join("、"));
      h += '<tr><td ' + AR_CELL + '><b>' + esc(pair[1]) + '</b></td><td ' + AR_CELL + '>' +
        '<div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start">' +
        '<span>' + esc(it.text || "") + '</span>' + arTag(it.source) + '</div>' +
        arAdded(it.added) +
        (sub ? '<div class="note-gray" style="margin:2px 0 0">' + sub + '</div>' : "") + '</td></tr>';
    });
    h += '</tbody></table>';
    if (beats.length) {
      h += '<p class="setup-label"><label>情节顺序</label></p><table style="margin-bottom:12px"><tbody>' +
        beats.map(function (b) {
          return '<tr><td style="width:120px;padding:8px;vertical-align:top;font-size:14px"><b>' + esc(b.stage || "") + '</b></td>' +
            '<td ' + AR_CELL + '><div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start">' +
            '<span>' + esc(b.text || "") + '</span>' + arTag(b.source) + '</div>' + arAdded(b.added) + '</td></tr>';
        }).join("") + '</tbody></table>';
    }
    if (budget.length) {
      /* 各段之和和目标时长差过 15% 就提醒——这是后端的校验口径，前端只是让用户看得见 */
      var off = dur && Math.abs(total - dur) > dur * 0.15;
      h += '<p class="setup-label"><label>时间预算</label></p>' +
        '<table style="margin-bottom:12px"><thead><tr><th>内容</th><th style="width:90px">建议时间</th><th>拍摄重点</th></tr></thead><tbody>' +
        budget.map(function (b) {
          return '<tr><td>' + esc(b.beat || "") + '</td><td>' + (+b.seconds || 0) + ' 秒</td><td>' + esc(b.focus || "") + '</td></tr>';
        }).join("") +
        '<tr><td><b>合计</b></td><td><b>' + total + ' 秒</b></td><td class="note-gray">目标 ' + dur + ' 秒' +
        (off ? '，差得有点多，可以在下面让它重新分' : '') + '</td></tr></tbody></table>';
    }
    h += '<p style="font-size:14px;margin:0 0 12px"><b>人物：</b>' + (arPeopleLine(who.people) || "（还没有人物）") + '</p>';
    /* 聊天式修改 + 确认。已确认的按钮置灰：再点一次会把安排重新落盘，
       盖掉用户在结构表里手改过的东西；想改就在上面说一句，状态自然回到草稿。 */
    h += '<p class="setup-label"><label>哪里不对，说一句就行</label></p>' +
      '<textarea id="arChat" placeholder="像聊天一样说。例：别安排悲伤的身世。灯是老板自己做坏的，结尾轻松好笑一点。" ' +
      'style="width:100%;height:80px;min-height:80px;padding:10px 12px;font-size:14px;line-height:1.7;border-radius:10px"></textarea>' +
      '<div style="display:flex;gap:8px;align-items:center;margin-top:8px;flex-wrap:wrap">' +
      '<button class="btn" id="arRevise" style="height:38px;border-radius:8px">改一下</button>' +
      '<button class="btn primary" id="arConfirm" style="height:38px;border-radius:8px"' + (confirmed ? " disabled" : "") + '>' +
      (confirmed ? "✅ 已按这个做" : "就按这个做") + '</button>' +
      '<span class="note-gray">' + (confirmed ? "还想改就在上面说一句，改完再确认一次" : "确认后，故事、剧本、视频都按这张安排来") + '</span>' +
      '</div></div>';
    return h;
  }
  function arDuration() {
    var v = (document.getElementById("arDur") || {}).value || "60";
    if (v !== "custom") return parseInt(v, 10) || 60;
    var n = parseInt((document.getElementById("arDurCustom") || {}).value || "", 10);
    return (n >= 10 && n <= 600) ? n : 0;
  }
  /* 三个动作共用的收尾：后端给回来的安排画进 #arCard，并替换 D.settings 里那份，
     免得别处触发整页重画又退回旧版本。后端没在返回里带安排时整页重读设定兜底。 */
  function arApply(main, st, r) {
    var a = (r && r.arrangement) ? r.arrangement : ((r && r.items) ? r : null);
    if (!a) return load(main, st).then(function () { return (D.settings || {}).arrangement || null; });
    D.settings = D.settings || {};
    D.settings.arrangement = a;
    var card = document.getElementById("arCard");
    if (card) card.innerHTML = arCardHtml(a);
    arWireCard(main, st);
    return Promise.resolve(a);
  }
  function arWireCard(main, st) {
    on("arRevise", function () {
      var ins = ((document.getElementById("arChat") || {}).value || "").trim();
      if (!ins) { UI.toast("先说一句想怎么改", "err"); return; }
      var b = document.getElementById("arRevise");
      if (b) { b.disabled = true; b.textContent = "正在改…（约半分钟）"; }
      window.api.post("/api/saga/" + st.storyId + "/arrange/revise", { instruction: ins })
        .then(function (r) {
          var why = String((r && r.why) || "").trim();
          return arApply(main, st, r).then(function () {
            UI.toast(why ? "改好了：" + why : "改好了，看看新的这版");
          });
        })
        .catch(function (e) {
          UI.toast(e.message, "err");
          var b2 = document.getElementById("arRevise");
          if (b2) { b2.disabled = false; b2.textContent = "改一下"; }
        });
    });
    on("arConfirm", function () {
      var b = document.getElementById("arConfirm");
      if (b) { b.disabled = true; b.textContent = "正在确认…"; }
      window.api.post("/api/saga/" + st.storyId + "/arrange/confirm", {})
        .then(function (r) { return arApply(main, st, r); })
        .then(function (a) {
          var a2 = a || (D.settings || {}).arrangement || {};
          /* 一句话故事空着就填想法：写作链把它当"故事要求"，后端 confirm 也同样补了盘上那份 */
          var one = document.getElementById("psOne");
          if (one && !one.value.trim() && a2.idea) { one.value = a2.idea; D.settings.one_line = a2.idea; }
          UI.toast("已确认。下面点「✨ 生成故事」，正文会按这张安排写");
          var g = document.getElementById("genStory");
          if (g && g.scrollIntoView) g.scrollIntoView({ behavior: "smooth", block: "center" });
        })
        .catch(function (e) {
          UI.toast(e.message, "err");
          var b2 = document.getElementById("arConfirm");
          if (b2) { b2.disabled = false; b2.textContent = "就按这个做"; }
        });
    });
  }

  /* ═══════════ 整部规划卡（P287）：想法 → 完整剧情 → 自动分话/时长 → 看/改/确认 → 逐话生成 ═══════════ */
  var PL_IDEA_PH = "写下整个故事的想法：主要人物、想讲什么、大概怎么收尾。名字、地点可以不写，系统会补。";
  var PL_STYLES = ["由 AI 建议", "电影级写实", "平面动漫", "水彩", "黑白"];
  var PL_PACES = ["由 AI 建议", "舒缓日常", "稳步推进", "紧张快节奏"];
  var SAVE_PROJECT = null;  /* P316：wire() 里的 saveCurrentProject 挂到模块级——规划按钮原来 typeof 查不到它，「会先保存再规划」从没保存过（项目183 规划时没有世界/画风/尺度） */
  var plOpen = {};          /* 展开了哪几话（uid → true） */
  var plEditing = {};       /* 正在编辑哪几话 */
  function plEpisodeHtml(e, confirmed, sagaEp) {
    var open = !!plOpen[e.uid], editing = !!plEditing[e.uid];
    var st = "";
    if (sagaEp) {
      if (sagaEp.plan_stale) st = '<span class="stale" style="margin-left:6px">⚠ 规划改了，正文需要更新</span>';
      else if (sagaEp.has_prose) st = '<span class="stat-green" style="margin-left:6px;font-size:12px">✅ 有正文' + (sagaEp.has_pictures ? "＋剧本" : "") + '</span>';
    }
    /* P301：默认只看四件事——讲什么 / 主要展示 / 节奏 / 讲到哪里结束；时长不再显示（规划阶段不设） */
    var head = '<div style="display:flex;align-items:baseline;gap:8px;flex-wrap:wrap">' +
      '<b style="font-size:15px;color:#5a3b1e">第 ' + e.no + ' 话 · ' + esc(e.title || "") + '</b>' +
      (e.source === "user" ? '<span class="tag" style="font-size:11px">你改过</span>' : "") + st +
      '<span style="margin-left:auto"></span>' +
      '<button class="btn small" data-pl="edit" data-uid="' + esc(e.uid) + '">' + (editing ? "取消编辑" : "编辑这一话") + '</button>' +
      '<button class="btn small" data-pl="toggle" data-uid="' + esc(e.uid) + '">' + (open ? "收起" : "展开详细过程") + '</button>' +
      '</div>';
    var body;
    var PACE3 = ["舒缓", "适中", "紧凑"];
    if (editing) {
      body = '<div style="margin-top:8px;font-size:13px;color:#5a5348">' +
        '<label>标题</label><input data-pl-field="title" data-uid="' + esc(e.uid) + '" value="' + esc(e.title || "") + '" style="width:100%;height:32px;padding:0 8px;margin:2px 0 8px">' +
        '<label>这一话讲什么</label><textarea data-pl-field="one_line" data-uid="' + esc(e.uid) + '" style="width:100%;height:90px;padding:10px 12px;font-size:14px;line-height:1.7;border-radius:8px;margin:2px 0 8px">' + esc(e.one_line || "") + '</textarea>' +
        '<label>主要展示（一行一条）</label><textarea data-pl-field="focus" data-uid="' + esc(e.uid) + '" style="width:100%;height:64px;padding:8px 12px;font-size:13px;line-height:1.6;border-radius:8px;margin:2px 0 8px">' + esc((e.focus || []).join("\n")) + '</textarea>' +
        '<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">' +
        '<label>节奏</label><select data-pl-field="pace" data-uid="' + esc(e.uid) + '" style="height:32px;border-radius:8px">' +
        PACE3.map(function (p) { return '<option' + (p === e.pace ? " selected" : "") + '>' + p + '</option>'; }).join("") + '</select>' +
        '<label>讲到哪里结束</label>' +
        '<input data-pl-field="landing" data-uid="' + esc(e.uid) + '" value="' + esc(e.landing || "") + '" style="flex:1;min-width:220px;height:32px;padding:0 8px">' +
        '</div>' +
        /* P319：详细过程那几栏也在这里改（改了「讲什么」而没动这里，主要过程/在场/本话作用会按新文字重算） */
        '<details style="margin-top:8px"><summary class="note-gray" style="cursor:pointer;margin:0">详细过程（本话作用 / 开始状态 / 场景顺序 / 主要过程 / 在场 / 承接）——不改就按「讲什么」自动重算</summary>' +
        '<div style="margin-top:6px">' +
        '<label>本话作用</label><input data-pl-field="purpose" data-uid="' + esc(e.uid) + '" value="' + esc(e.purpose || "") + '" style="width:100%;height:32px;padding:0 8px;margin:2px 0 6px">' +
        '<label>从什么情况接着讲（开始状态）</label><input data-pl-field="start" data-uid="' + esc(e.uid) + '" value="' + esc(e.start || "") + '" style="width:100%;height:32px;padding:0 8px;margin:2px 0 6px">' +
        '<label>场景顺序（顿号分隔）</label><input data-pl-field="scenes" data-uid="' + esc(e.uid) + '" value="' + esc((e.scenes || []).join("、")) + '" style="width:100%;height:32px;padding:0 8px;margin:2px 0 6px">' +
        '<label>主要过程（一行一条，按顺序）</label><textarea data-pl-field="events" data-uid="' + esc(e.uid) + '" style="width:100%;height:110px;padding:8px 12px;font-size:13px;line-height:1.6;border-radius:8px;margin:2px 0 6px">' + esc((e.events || []).join("\n")) + '</textarea>' +
        '<label>在场（顿号分隔）</label><input data-pl-field="cast" data-uid="' + esc(e.uid) + '" value="' + esc((e.cast || []).join("、")) + '" style="width:100%;height:32px;padding:0 8px;margin:2px 0 6px">' +
        '<label>怎么进入本话</label><input data-pl-field="link_in" data-uid="' + esc(e.uid) + '" value="' + esc(e.link_in || "") + '" style="width:100%;height:32px;padding:0 8px;margin:2px 0 6px">' +
        '<label>下一话为什么接着发生</label><input data-pl-field="link_out" data-uid="' + esc(e.uid) + '" value="' + esc(e.link_out || "") + '" style="width:100%;height:32px;padding:0 8px;margin:2px 0 6px">' +
        '</div></details>' +
        '<div style="margin-top:8px"><button class="btn small primary" data-pl="save" data-uid="' + esc(e.uid) + '" data-no="' + e.no + '">保存这一话</button></div></div>';
    } else {
      body = '<div style="margin-top:4px;font-size:14px;line-height:1.75;color:#2f2921">' +
        '<div><b>这一话讲什么</b>：' + esc(e.one_line || "") + '</div>' +
        ((e.focus || []).length ? '<div><b>主要展示</b>：' + esc(e.focus.join("；")) + '</div>' : "") +
        '<div><b>节奏</b>：' + esc(e.pace || "") + (e.pace_meaning ? '<span class="note-gray" style="margin:0 0 0 6px">' + esc(e.pace_meaning) + '</span>' : "") +
        (e.pace_why ? '<span class="note-gray" style="margin:0 0 0 6px">——' + esc(e.pace_why) + '</span>' : "") + '</div>' +
        (e.landing ? '<div><b>讲到哪里结束</b>：' + esc(e.landing) + '</div>' : "") +
        '</div>' +
        /* 单话一句话修改（P301）：只影响这一话；结尾变了影响后话时，影响面板会说明 */
        '<div style="display:flex;gap:6px;margin-top:6px;align-items:center">' +
        '<input data-pl-ins="' + esc(e.uid) + '" placeholder="想调整这一话？直接说。例：别急着让两人熟悉，多写一次有分歧的交谈" style="flex:1;height:32px;padding:0 10px;font-size:13px;border-radius:8px">' +
        '<button class="btn small" data-pl="revise1" data-uid="' + esc(e.uid) + '" data-no="' + e.no + '">改这一话</button></div>';
    }
    var detail = "";
    if (open) {
      var beats = (e.beats || []).map(function (b) {
        var w = b.weight === "重点" ? '<b style="color:#8a5a2b">重点</b>' : '<span class="note-gray" style="margin:0">带过</span>';
        var sec = (b.seconds && (b.seconds[0] || b.seconds[1])) ? (b.seconds[0] === b.seconds[1] ? b.seconds[0] + " 秒" : b.seconds[0] + "～" + b.seconds[1] + " 秒") : "";
        return '<li>' + w + '　' + esc(b.text || "") + (sec ? '　<span class="note-gray" style="margin:0">' + sec + '</span>' : "") + '</li>';
      }).join("");
      var evs = (e.events || []).map(function (x) { return '<li>' + esc(x) + '</li>'; }).join("");
      detail = '<div style="margin-top:8px;padding:8px 12px;background:#fbf8f2;border:1px solid #efe9de;border-radius:8px;font-size:13px;line-height:1.75">' +
        (e.purpose ? '<div><b>本话作用</b>：' + esc(e.purpose) + '</div>' : "") +
        (e.start ? '<div><b>从什么情况接着讲</b>：' + esc(e.start) + '</div>' : "") +
        ((e.scenes || []).length ? '<div><b>场景顺序</b>：' + esc(e.scenes.join(" → ")) + '</div>' : "") +
        (evs ? '<div><b>主要过程</b><ol style="margin:2px 0 4px 20px;padding:0">' + evs + '</ol></div>'
             : (beats ? '<div><b>主要过程</b><ol style="margin:2px 0 4px 20px;padding:0">' + beats + '</ol></div>' : "")) +
        (e.where ? '<div><b>时间主要花在</b>：' + esc(e.where) + '</div>' : "") +
        (e.link_in ? '<div><b>怎么进入</b>：' + esc(e.link_in) + '</div>' : "") +
        (e.link_out ? '<div><b>下一话为什么接着发生</b>：' + esc(e.link_out) + '</div>' : "") +
        ((e.cast || []).length ? '<div><b>在场</b>：' + esc(e.cast.join("、")) + '</div>' : "") +
        '</div>';
    }
    var rv = (plReview[e.no] || []).map(function (x) {
      var col = x.status === "有问题" ? "var(--red)" : "var(--orange)";
      return '<div style="color:' + col + '">[' + esc(x.status) + '] ' + esc(x.check) + '：' + esc(x.detail) + '</div>';
    }).join("");
    if (rv) rv = '<div style="margin-top:6px;font-size:12px;line-height:1.7">' + rv + '</div>';
    return '<div class="card" style="margin:8px 0;padding:12px 14px" data-pl-ep="' + esc(e.uid) + '">' + head + body + rv + detail + '</div>';
  }
  var plReview = {};
  var plView = null;                 /* 最近一次 plan/get 的结果（P298：生成按钮要看当前规划，不能看页面刚打开时那份） */
  function plImpactHtml(imp, note, readOnly) {
    if (!imp) return "";
    var rows = [];
    if (note) rows.push('<div><b>改动说明</b>：' + esc(note) + '</div>');
    if ((imp.changed || []).length) rows.push('<div>改了：' + imp.changed.map(function (n) {
      var f = (imp.details || {})[String(n)] || [];
      return "第 " + n + " 话" + (f.length ? "（" + f.join("、") + "）" : "");
    }).join("；") + '</div>');
    if ((imp.added || []).length) rows.push('<div>新增：第 ' + imp.added.join("、") + ' 话</div>');
    if ((imp.removed || []).length) rows.push('<div>合并/删除：原第 ' + imp.removed.join("、") + ' 话</div>');
    if ((imp.moved || []).length) rows.push('<div>顺序变了：' + imp.moved.map(function (m) { return "原第 " + m[0] + " 话 → 第 " + m[1] + " 话"; }).join("；") + '</div>');
    (imp.reasons || []).forEach(function (r) { rows.push('<div class="note-gray" style="margin:0">' + esc(r) + '</div>'); });
    if ((imp.stale || []).length) rows.push('<div style="color:var(--orange)">已有正文/剧本要更新：第 ' + imp.stale.join("、") + ' 话（旧稿保留）</div>');
    if ((imp.retire || []).length) rows.push('<div style="color:var(--orange)">旧稿退休保留、不再挂在任何一话上：原第 ' + imp.retire.join("、") + ' 话</div>');
    if ((imp.keep || []).length) rows.push('<div class="stat-green" style="font-size:13px">不受影响：第 ' + imp.keep.join("、") + ' 话</div>');
    if (!rows.length) rows.push('<div class="note-gray" style="margin:0">没有实质改动</div>');
    if (readOnly) {
      /* P293 草稿状态：改动已经生效，只说明改了哪几话，不用再按一次 */
      return '<div id="plLastChange" style="margin:10px 0;padding:10px 12px;border:1px solid #d8c9b0;border-radius:8px;background:#fbf6ee;font-size:13px;line-height:1.75">' +
        '<b>上次修改改了什么</b>' + rows.join("") + '</div>';
    }
    return '<div id="plImpact" style="margin:10px 0;padding:10px 12px;border:1px solid var(--orange);border-radius:8px;background:#fff8ee;font-size:13px;line-height:1.75">' +
      '<b>这次修改的影响</b>' + rows.join("") +
      '<div style="margin-top:8px;display:flex;gap:8px"><button class="btn primary" id="plApply">确认这次修改</button>' +
      '<button class="btn" id="plDiscard">不要这次修改</button></div></div>';
  }
  function plCardHtml(view, sagaEps) {
    var sum = view.pending_summary || view.summary;
    if (!sum) return "";
    var confirmed = (view.plan || {}).status === "confirmed" && !view.pending;
    var byNo = {};
    (sagaEps || []).forEach(function (e) { byNo[e.no] = e; });
    var head = '<div style="display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;margin:14px 0 6px">' +
      '<b style="font-size:16px;color:#5a3b1e">整部方案</b>' +
      (view.pending ? '<span class="stat-orange" style="font-size:13px">改动待确认</span>'
        : (confirmed ? '<span class="stat-green" style="font-size:13px">已确认 v' + esc((view.plan || {}).version) + '</span>'
        : '<span class="note-gray" style="margin:0">草稿，看过再确认</span>')) +
      '<span class="note-gray" style="margin:0 0 0 auto">建议分 ' + sum.n + ' 话' + (sum.reason ? '：' + esc(sum.reason) : '') + '</span></div>';
    /* P316：整部级两栏由用户编辑——整个故事怎么发展 / 整体怎么展示（写作、改这一话都照它） */
    var top = '<div style="font-size:14px;line-height:1.8">' +
      '<div><b>主要人物与关系</b>：' + esc((sum.people || []).join("；")) + '</div>' +
      '</div>';
    var probs = (sum.problems || []).length ? '<div style="margin:6px 0;font-size:13px;color:var(--orange)">' +
      sum.problems.map(function (x) {
        /* P318：最后一话只有收尾动作 → 一键并入上一话（零模型） */
        var mm = /^第(\d+)话只有收尾动作/.exec(String(x || ""));
        return '<div>⚠ ' + esc(x) + (mm ? ' <button class="btn small" data-pl="merge" data-no="' + mm[1] + '">并入上一话</button>' : '') + '</div>';
      }).join("") + '</div>' : "";
    /* P292 想法核对：想法里写明的事规划有没有照样写——只报待审核，附最接近的原句，用户看一眼定 */
    probs += (sum.facts || []).length ? '<div style="margin:6px 0;font-size:13px;color:var(--orange)">' +
      sum.facts.map(function (x) { return '<div>待审核 · ' + esc(x.detail) + '</div>'; }).join("") + '</div>' : "";
    var eps = (sum.episodes || []).map(function (e) { return plEpisodeHtml(e, confirmed, byNo[e.no]); }).join("");
    var chat = '<div style="display:flex;gap:8px;margin-top:10px;align-items:center;flex-wrap:wrap">' +
      '<input id="plIns" placeholder="用一句话改：前两话合并 / 第二话聊天多一点慢下来 / 这个真相晚一话揭晓 / 结尾改成两人分别" ' +
      'style="flex:1;min-width:260px;height:38px;padding:0 12px;font-size:14px;border-radius:8px">' +
      '<button class="btn" id="plRevise" style="height:38px;border-radius:8px">改一下</button>' +
      (view.pending ? "" : '<button class="btn primary" id="plConfirm" style="height:38px;border-radius:8px"' + (confirmed ? " disabled" : "") + '>' +
        (confirmed ? "已确认" : "就按这个做") + '</button>') + '</div>';
    var lastChange = (!view.pending && !confirmed && view.last_change) ? plImpactHtml(view.last_change.impact, view.last_change.note, true) : "";
    /* P321：整部两栏常开、放在卡片上面（用户：展开/编辑按钮多余） */
    var story2 = '<div id="plSyn" style="margin:8px 0;padding:10px 12px;background:#fbf8f2;border-radius:8px;font-size:13px;line-height:1.8">' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">' +
      '<div><label>整个故事怎么发展（可改）</label>' +
      '<textarea id="plSynEdit" style="width:100%;height:150px;padding:10px 12px;font-size:14px;line-height:1.7;border-radius:8px;margin:2px 0 0">' + esc(((view.pending || view.plan) || {}).synopsis || "") + '</textarea></div>' +
      '<div><label>整体怎么展示（看点、氛围、镜头感、什么要多给）</label>' +
      '<textarea id="plPresEdit" placeholder="例：多给两人相处的安静时刻；打斗少、对视多" style="width:100%;height:150px;padding:10px 12px;font-size:14px;line-height:1.7;border-radius:8px;margin:2px 0 0">' + esc(((view.pending || view.plan) || {}).presentation || "") + '</textarea></div>' +
      '</div><div style="margin-top:6px"><button class="btn small primary" id="plStorySave">保存这两栏</button></div></div>';
    return head + top + probs + story2 + plImpactHtml(view.impact, view.note) + lastChange + '<div id="plEps">' + eps + '</div>' + chat +
      '';
  }
  function plSagaEps(st) {
    return window.api.post("/api/saga/" + st.storyId + "/get", {}).then(function (r) {
      var sg = (r && (r.data || r)) || {};
      var eps = sg.saga ? sg.saga.episodes : (sg.episodes || []);
      return (eps || []).map(function (e) {
        return { no: e.no, brief: e.brief || "", story_pace: e.story_pace || "适中", episode_notes: e.episode_notes || "",
          shooting_notes: e.shooting_notes || "", prose: e.prose || "", body: e.body || "", pictures: e.pictures || "",
          plan_stale: !!e.plan_stale, has_prose: !!String(e.prose || "").trim(), has_pictures: !!String(e.pictures || "").trim() };
      });
    }).catch(function () { return []; });
  }
  function plLoad(main, st) {
    return window.api.post("/api/saga/" + st.storyId + "/plan/get", {}).then(function (r) {
      var view = (r && (r.data || r)) || {};
      return window.api.post("/api/saga/" + st.storyId + "/plan/review", {}).then(function (rv) {
        plReview = ((rv && (rv.data || rv)) || {}).review || {};
      }).catch(function () { plReview = {}; }).then(function () {
        return plSagaEps(st).then(function (eps) { plLastEps = eps; plDraw(main, st, view, eps); return view; });
      });
    });
  }
  function plDraw(main, st, view, sagaEps) {
    var box = document.getElementById("plCard"); if (!box) return;
    plView = view;
    box.innerHTML = plCardHtml(view, sagaEps);
    /* P301：⑤ 的话次下拉跟着规划走；默认选第一个还没正文的话；状态一行说明已有成果 */
    (function () {
      var p = view.plan || null, has = !!(p && (p.episodes || []).length);
      var sel = document.getElementById("mkEp"), stat = document.getElementById("mkStatus");
      if (!sel) return;
      var eps = has ? p.episodes : [];
      var byNo = {}; (sagaEps || []).forEach(function (x) { byNo[x.no] = x; });
      var firstTodo = 0;
      var opts = eps.map(function (e) {
        var se = byNo[e.no] || {};
        var flag = se.plan_stale ? "（需要更新）" : (se.has_prose ? (se.has_pictures ? "（已有正文＋剧本）" : "（已有正文）") : "");
        if (!firstTodo && !(se.has_prose && !se.plan_stale)) firstTodo = e.no;
        return '<option value="' + e.no + '">第 ' + e.no + ' 话 · ' + esc(e.title || "") + flag + '</option>';
      }).join("");
      var keep = sel.dataset.user === "1" ? sel.value : "";     /* 用户自己选过才保留，否则默认跳到第一个还没做的话 */
      sel.innerHTML = opts || '<option value="1">第 1 话</option>';
      var want = (keep && eps.some(function (e) { return String(e.no) === String(keep); })) ? keep : String(firstTodo || 1);
      sel.value = want;
      sel.onchange = function () { sel.dataset.user = "1"; };
      var confirmedNow = !!(p && p.status === "confirmed" && !view.pending);
      if (stat) stat.textContent = !has ? "还没有规划：先在上面「规划整个故事」" :
        (!confirmedNow ? "规划是草稿也可以直接做：先做第 1 话，第 1 话定了再改第 2 话的卡、再做第 2 话（做的时候自动落盘）" :
         "已确认 v" + (p.version || "") + "，共 " + eps.length + " 话。生成后去 📖 剧本 页读正文和剧本；下一话回到这里选");
    })();
    plWire(main, st, view);
  }
  function plPoll(main, st, doneMsg) {
    var t = setInterval(function () {
      window.api.post("/api/saga/" + st.storyId + "/progress", {}).then(function (j2) {
        if (j2.running) return;
        clearInterval(t);
        if (j2.err) { UI.toast("失败：" + j2.err, "err"); }
        else UI.toast(doneMsg || "好了");
        plLoad(main, st).catch(function () {});
        var b = document.getElementById("plDraft"); if (b) { b.disabled = false; b.textContent = "📐 规划整个故事"; }
        var b2 = document.getElementById("plRevise"); if (b2) { b2.disabled = false; b2.textContent = "改一下"; }
      }).catch(function () {});
    }, 2500);
  }
  function plWire(main, st, view) {
    on("plStorySave", function () {
      var b = document.getElementById("plStorySave"); if (b) b.disabled = true;
      var oldSyn = String(((view.pending || view.plan) || {}).synopsis || "");
      var newSyn = (document.getElementById("plSynEdit") || {}).value || "";
      window.api.post("/api/saga/" + st.storyId + "/plan/edit-story", {
        synopsis: newSyn,
        presentation: (document.getElementById("plPresEdit") || {}).value || "" })
        .then(function () {
          UI.toast("整部两栏已保存；写正文时会原样收到");
          /* P318：梗概改了 → 各话的安排还是旧的；问一句要不要让模型把受影响的话同步（新增/改动的句子作为修改要求） */
          if (newSyn.trim() && newSyn.trim() !== oldSyn.trim()) {
            var cut = function (t) { return String(t || "").split(/(?<=[。！？；\n])/).map(function (x) { return x.trim(); }).filter(Boolean); };
            var oldS = cut(oldSyn), added = cut(newSyn).filter(function (x) { return oldS.indexOf(x) < 0; });
            if (added.length && confirm("梗概改了。要让模型把受影响的话同步到新梗概吗？\n改动的句子：" + added.slice(0, 4).join("") + "\n（不同步也可以：写正文时会收到新梗概，但各话的安排还是旧的）")) {
              return window.api.post("/api/saga/" + st.storyId + "/plan/revise", { instruction: "整部梗概改了（用户改的），改动的句子是：" + added.join("") + "——把受影响的话的讲什么/主要过程/结束状态按新梗概改，没受影响的话保持原样。" })
                .then(function () { plPoll(main, st, "各话已按新梗概同步，看一下影响再确认"); });
            }
          }
          return plLoad(main, st);
        })
        .catch(function (e) { UI.toast(e.message, "err"); if (b) b.disabled = false; });
    });
    on("plConfirm", function () {
      var b = document.getElementById("plConfirm"); if (b) b.disabled = true;
      window.api.post("/api/saga/" + st.storyId + "/plan/confirm", {})
        .then(function () { UI.toast("整部规划已确认。每一话都可以点「写第 N 话（正文＋剧本）」了；下面「✨ 生成第一话全部文字」也是同一条路"); return plLoad(main, st); })
        .catch(function (e) { UI.toast(e.message, "err"); if (b) b.disabled = false; });
    });
    on("plApply", function () {
      var b = document.getElementById("plApply"); if (b) b.disabled = true;
      window.api.post("/api/saga/" + st.storyId + "/plan/confirm", {})
        .then(function () { UI.toast("修改已确认；受影响的话标了「需要更新」，旧稿都留着"); return plLoad(main, st); })
        .catch(function (e) { UI.toast(e.message, "err"); if (b) b.disabled = false; });
    });
    on("plDiscard", function () {
      window.api.post("/api/saga/" + st.storyId + "/plan/discard", {})
        .then(function () { UI.toast("这次修改已放弃"); return plLoad(main, st); })
        .catch(function (e) { UI.toast(e.message, "err"); });
    });
    on("plRevise", function () {
      var ins = ((document.getElementById("plIns") || {}).value || "").trim();
      if (!ins) { UI.toast("先说要改什么，一句话就行", "err"); return; }
      var b = document.getElementById("plRevise"); if (b) { b.disabled = true; b.textContent = "改着…（约半分钟）"; }
      window.api.post("/api/saga/" + st.storyId + "/plan/revise", { instruction: ins })
        .then(function () { plPoll(main, st, "改好了，看一下影响再确认"); })
        .catch(function (e) { UI.toast(e.message, "err"); if (b) { b.disabled = false; b.textContent = "改一下"; } });
    });
    [].forEach.call(document.querySelectorAll("#plCard [data-pl]"), function (b) {
      b.onclick = function () {
        var act = b.getAttribute("data-pl"), uid = b.getAttribute("data-uid"), no = +b.getAttribute("data-no");
        if (act === "toggle") { plOpen[uid] = !plOpen[uid]; plDraw(main, st, view, plLastEps); }
        else if (act === "edit") { plEditing[uid] = !plEditing[uid]; if (plEditing[uid]) plOpen[uid] = true; plDraw(main, st, view, plLastEps); }
        else if (act === "save") {
          var f = {};
          [].forEach.call(document.querySelectorAll('#plCard [data-pl-field][data-uid="' + uid + '"]'), function (inp) { f[inp.getAttribute("data-pl-field")] = inp.value; });
          b.disabled = true;
          window.api.post("/api/saga/" + st.storyId + "/plan/edit", { no: no, fields: f })
            .then(function () { plEditing[uid] = false; UI.toast("第 " + no + " 话已改"); return plLoad(main, st); })
            .catch(function (e) { UI.toast(e.message, "err"); b.disabled = false; });
        }
        else if (act === "merge") {
          if (!confirm("把第 " + no + " 话并入第 " + (no - 1) + " 话（过程接在后面、结束状态取后一话的）？")) return;
          b.disabled = true;
          window.api.post("/api/saga/" + st.storyId + "/plan/merge", { no: no })
            .then(function () { UI.toast("已并入第 " + (no - 1) + " 话"); return plLoad(main, st); })
            .catch(function (e) { UI.toast(e.message, "err"); b.disabled = false; });
        }
        else if (act === "revise1") {
          var inp = document.querySelector('#plCard [data-pl-ins="' + uid + '"]');
          var ins = ((inp || {}).value || "").trim();
          if (!ins) { UI.toast("先说这一话要怎么改", "err"); return; }
          b.disabled = true; b.textContent = "改中…";
          window.api.post("/api/saga/" + st.storyId + "/plan/revise", { instruction: ins, no: no })
            .then(function () { UI.toast("开始改第 " + no + " 话（约 1 分钟）"); plPoll(main, st, "第 " + no + " 话改好了，看一下改了什么"); })
            .catch(function (e) { UI.toast(e.message, "err"); b.disabled = false; b.textContent = "改这一话"; });
        }
        else if (act === "write") {
          if (!confirm("按整部规划写第 " + no + " 话正文并出剧本（正文约 1 分钟，剧本约 2～5 分钟；不出图不出视频）。开始？")) return;
          b.disabled = true;
          window.api.post("/api/saga/" + st.storyId + "/gen-story", { ep: no })
            .then(function () { UI.toast("开始写第 " + no + " 话"); plPoll(main, st, "第 " + no + " 话正文和剧本写好了，去 📖 剧本 页看"); })
            .catch(function (e) { UI.toast(e.message, "err"); b.disabled = false; });
        }
      };
    });
  }
  var plLastEps = [];
  function renderSplitPlan(main, st) {
    var slot = slotOf("slotIdea"), slot2 = slotOf("slotPlan"); if (!slot || !slot2) return;
    var s0 = (D.settings || {});
    var sp = s0.story_plan || {};
    slot.innerHTML =
      '<div class="card" id="planCard"><h3 style="font-size:16px">' + stepNo(1) + '你想讲一个怎样的故事？</h3>' +
      '<p class="note-gray" style="margin:0 0 8px">写一句话也可以，系统会补充完整发展，并建议分几话。人物、地点、事情之间的因果由系统补。</p>' +
      '<textarea id="plIdea" placeholder="' + esc(PL_IDEA_PH) + '" style="width:100%;height:120px;min-height:120px;' +
      'padding:14px 16px;font-size:15px;line-height:1.85;border-radius:10px">' + esc(sp.idea || s0.one_line || "") + '</textarea>' +
      '<p class="setup-label" style="margin-top:10px"><label>补充要求（可不填：故事上必须出现什么、结局、画面观感、人物长相要求……一路带到规划、写作、人设、场景和视频）</label></p>' +
      '<textarea id="plNotes" placeholder="例：必须出现一次大雨；结局两人分别；整体暗调、雨天多；人物要极瘦高挑" style="width:100%;height:64px;min-height:64px;padding:10px 12px;font-size:14px;line-height:1.7;border-radius:8px">' + esc(s0.extra_requirements || sp.notes || (sp.prefs || {}).notes || "") + '</textarea>' +
      '</div>';
    slot2.innerHTML =
      '<div class="card" id="planCard2"><h3 style="font-size:16px">' + stepNo(3) + '规划整个故事</h3>' +
      '<p class="note-gray" style="margin:0 0 8px">补充故事发展并安排各话——这一步不生成正文、图片和视频。规划阶段不定时长；每话的节奏由系统先选，你可以改。</p>' +
      '<div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">' +
      '<button class="btn primary" id="plDraft" style="height:38px;border-radius:8px;font-size:15px">📐 规划整个故事</button>' +
      '<span class="note-gray" style="margin:0">画风等偏好取自上面「作品偏好」，会先保存再规划</span>' +
      '</div><div id="plCard"></div></div>';
    on("plDraft", function () {
      var idea = ((document.getElementById("plIdea") || {}).value || "").trim();
      if (!idea) { UI.toast("先写一段故事想法", "err"); return; }
      var notes = ((document.getElementById("plNotes") || {}).value || "").trim();
      var style = (document.getElementById("psStyle") || {}).value || "";
      var b = document.getElementById("plDraft"); if (b) { b.disabled = true; b.textContent = "规划中…（约 1～2 分钟）"; }
      /* 先把偏好存进项目，规划和后面的写作用的就是页面上这份 */
      (typeof SAVE_PROJECT === "function" ? SAVE_PROJECT() : Promise.resolve())
        .catch(function (e) { UI.toast("偏好没保存成：" + (e && e.message || e), "err"); throw e; })
        .then(function () {
          return window.api.post("/api/saga/" + st.storyId + "/plan/draft", { idea: idea, style: style === "自动" ? "" : style, notes: notes });
        })
        .then(function () { plPoll(main, st, "整部方案出来了，往下看、改，满意就「就按这个做」"); })
        .catch(function (e) { UI.toast(e.message, "err"); if (b) { b.disabled = false; b.textContent = "📐 规划整个故事"; } });
    });
    plLoad(main, st).then(function (view) { plLastEps = []; }).catch(function () {});
    /* 老项目只有单话安排卡的，仍能在下面看到（不再是默认入口） */
  }

  var manualEp = 1, manualEpisodes = [];
  var manualInputKind = "idea";
  var MANUAL_INPUT_GUIDES = {
    idea: {
      title: "这一话想讲什么？",
      note: "推荐给大多数人。用自己的话说清楚谁遇到什么、经过什么、最后到哪里；人物对白和过程由本地模型补成正文。",
      placeholder: "例如：少年救下受伤老者，照顾他养伤；老者提出给钱或带他入门修炼，少年选择修炼并准备出发。",
      template: "主角是谁：\n这一话开始发生什么：\n中间经过哪些主要事情：\n最后停在哪里：",
      example: "一个贫穷少年在山里救下一名受伤老者，并把他藏在家里照顾。老者伤好后告诉少年，自己是修仙门派的长老，愿意报答他。老人给少年两个选择：拿一笔钱回家过安稳生活，或者跟他进入门派修炼。少年想到即使有钱以后仍可能受欺负，最后选择修炼。这一话结尾，少年告别家人，跟着老者踏上前往门派的路。",
      primary: "📝 生成当前正文",
      secondary: "🎬 认可正文并制作视频"
    },
    story: {
      title: "粘贴完整故事",
      note: "适合已经有小说正文或完整故事稿的人。系统直接采用，不会再让千问重新扩写。",
      placeholder: "粘贴有开头、发展和结果的完整故事正文……",
      template: "开头：人物在什么地方，发生什么。\n发展：人物采取什么行动，遇到什么阻碍。\n结果：事情如何解决，本话停在哪里。",
      example: "清晨，少年赵安在山脚采药时，发现溪边躺着一名浑身是血的老人。他担心追杀者还在附近，便把老人藏进柴车运回家。接下来的七天，赵安每天偷偷送药送饭。老人伤愈后表明自己来自青云宗，为报答救命之恩，让赵安在一百两银子和入门修炼之间选择。赵安想到自己即使有钱也无法保护家人，最终选择修炼。当天傍晚，他收拾好行李，跟着老人离开村庄。",
      primary: "💾 保存为本话正文",
      secondary: "🎬 保存正文并生成剧本"
    },
    script: {
      title: "粘贴现成剧本",
      note: "适合已经写好场景、动作和台词的人。系统原样保存，直接进入视频拆段。",
      placeholder: "按“场景＋人物动作＋台词”粘贴现成剧本……",
      template: "【场景1：地点，时间】\n人物动作。\n人物名：台词。\n场景最后发生的动作。",
      example: "【场景1：山脚溪边，清晨】\n赵安背着竹篓沿溪边采药，忽然发现草丛里露出一只沾血的手。\n赵安放下竹篓，小心靠近。\n赵安：老人家，你能听见吗？\n老人睁开眼，抓住赵安的手腕。\n老人：别出声……他们还在附近。\n远处传来马蹄声。赵安把老人扶进柴车，用药草盖住他的身体。",
      primary: "💾 保存为本话剧本",
      secondary: "🎞 保存剧本并去分镜"
    }
  };
  function renderPlan(main, st) {
    if ((D.settings || {}).story_mode === "split") return renderSplitPlan(main, st);
    var slot = slotOf("slotIdea"), slot2 = slotOf("slotPlan"); if (!slot || !slot2) return;
    var s0 = (D.settings || {});
    /* 默认入口只收当前这一话的完整剧情。保留 plIdea/plNotes 的 id，旧项目的
       保存逻辑和文字链可以继续复用，但这里不再显示或调用整部规划。 */
    var slotAct = slotOf("slotActions");
    slot.innerHTML =
      '<div id="planCard">' +
      '<h4 style="font-size:15px;margin:0 0 5px">第一话讲什么？</h4>' +
      '<p class="note-gray" style="margin:0 0 8px">把这一话发生的事一件一件说清楚，系统照你说的写、不改意思；第二话起在 📖 剧本 页加。</p>' +
      '<textarea id="plIdea" data-kind="idea" placeholder="例如：少年救下受伤老者，照顾他养伤；老者提出给钱或带他入门修炼，少年选择修炼并准备出发。" ' +
      'style="width:100%;height:200px;min-height:160px;padding:14px 16px;font-size:15px;line-height:1.85;border-radius:10px">' + esc(s0.one_line || "") + '</textarea>' +
      '<div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:10px">' +
      '<label class="setup-label" style="margin:0">这一话节奏：<select id="plPace" style="height:34px;border-radius:7px;padding:0 8px">' +
      '<option value="紧凑"' + (s0.story_pace !== "舒缓" ? " selected" : "") + '>紧凑（动作为主）</option>' +
      '<option value="舒缓"' + (s0.story_pace === "舒缓" ? " selected" : "") + '>舒缓（文戏为主）</option></select></label>' +
      '<span class="note-gray">只影响写正文：舒缓＝多对白多反应、每件事约 380 字；紧凑＝动作句连着写、台词短、每件事约 180 字。分段和每段秒数由导演分段决定（一段一个连续镜头 11～15 秒），和这里无关</span></div>' +
      '<p class="setup-label" style="margin-top:10px"><label>补充要求（可不填：对白多少、必须保留的事实或结尾）</label></p>' +
      '<textarea id="plNotes" placeholder="例如：对白多一些；不要加新的人物。" style="width:100%;height:64px;min-height:64px;padding:10px 12px;font-size:14px;line-height:1.65;border-radius:8px"></textarea>' +
      '</div>';
    var actionsHtml =
      '<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:14px;padding-top:12px;border-top:1px solid #eee4d6">' +
      '<button class="btn primary" id="genText" style="height:40px;border-radius:8px;font-size:15px" title="正文=剧本 → 人物卡 → 场景卡 → 导演分段。约 10 分钟，不出图不出片">① 生成文字</button>' +
      '<button class="btn primary" id="genImages" style="height:40px;border-radius:8px;font-size:15px" title="文字没做会先做文字；再出人设图和场景图。不写提示词、不出视频">② 生成图片</button>' +
      '<button class="btn primary" id="genAll" style="height:40px;border-radius:8px;font-size:15px" title="文字、图片没做的先补；再按剧本和分段写提示词、出全部视频、合成成片">③ 生成视频</button>' +
      '<span class="note-gray">按顺序：① 看剧本和分段（📖 剧本 页，改了自动保存）→ ② 看人设图场景图 → ③ 出片；直接点 ③ 就全自动到成片。</span></div>' +
      '<div id="mkStatus" class="note-gray" style="margin:8px 0 0"></div><div id="mkAsks"></div>';
    if (slotAct) { slotAct.innerHTML = actionsHtml; } else { slot.innerHTML += actionsHtml; }   // P452：按钮放合并卡最下面
    slot2.innerHTML = '';
    plView = { plan: null, pending: null };
    plLastEps = [];
    manualEp = 1;
    manualInputKind = localStorage.getItem("v43_input_kind_" + st.storyId) || "idea";
    if (!MANUAL_INPUT_GUIDES[manualInputKind]) manualInputKind = "idea";
    var manualDrafts = {};
    var currentEpisodeData = {};
    function episodeText(e, kind) {
      var key = manualEp + "|" + kind;
      if (Object.prototype.hasOwnProperty.call(manualDrafts, key)) return manualDrafts[key];
      if (kind === "story") return e.prose || "";
      if (kind === "script") return e.body || e.pictures || "";
      return e.brief || (manualEp === 1 ? s0.one_line || "" : "");
    }
    function showInputKind(kind, keepCurrent) {
      var ta = document.getElementById("plIdea");
      if (ta && keepCurrent) manualDrafts[manualEp + "|" + manualInputKind] = ta.value || "";
      manualInputKind = MANUAL_INPUT_GUIDES[kind] ? kind : "idea";
      localStorage.setItem("v43_input_kind_" + st.storyId, manualInputKind);
      var g = MANUAL_INPUT_GUIDES[manualInputKind];
      if (ta) { ta.dataset.kind = manualInputKind; ta.placeholder = g.placeholder; ta.value = episodeText(currentEpisodeData, manualInputKind); }
      var title = document.getElementById("manualKindTitle"), note = document.getElementById("manualKindNote");
      var tpl = document.getElementById("manualTemplate"), ex = document.getElementById("manualExample");
      if (title) title.textContent = g.title;
      if (note) note.textContent = g.note;
      if (tpl) tpl.textContent = g.template;
      if (ex) ex.textContent = g.example;
      var p = document.getElementById("genStory"), sbtn = document.getElementById("genAll");
      if (p) p.textContent = g.primary;
      /* P398：主按钮文案固定，不随输入方式变 */
      [].forEach.call(document.querySelectorAll("[data-input-kind]"), function (b) {
        b.classList.toggle("primary", b.getAttribute("data-input-kind") === manualInputKind);
      });
    }
    function drawEpisode() {
      var e = manualEpisodes.find(function(x) { return +x.no === manualEp; }) || {};
      currentEpisodeData = e;
      var pick = document.getElementById("mkEp");
      if (pick) {
        pick.innerHTML = manualEpisodes.map(function(x) { return '<option value="'+x.no+'">第 '+x.no+' 话</option>'; }).join('');
        pick.value = String(manualEp);
      }
      document.getElementById("plPace").value = e.story_pace === "舒缓" ? "舒缓" : "紧凑";
      document.getElementById("plNotes").value = e.episode_notes || "";
      if (document.getElementById("plShoot")) document.getElementById("plShoot").value = e.shooting_notes || "";
      if (document.getElementById("manualProse")) document.getElementById("manualProse").textContent = e.prose || "还没有正文。";
      var make = document.getElementById("genAll");
      if (make) make.title = e.prose ? "点击即确认当前正文并继续制作" : "请先生成并阅读正文";
      var write = document.getElementById("genStory");
      if (write && e.prose) write.textContent = "📝 重新生成当前正文";
      showInputKind(manualInputKind, false);
    }
    function reloadEpisode() {
      return plSagaEps(st).then(function(eps) {
        manualEpisodes = eps.length ? eps : [{no:1}];
        drawEpisode();
      });
    }
    reloadEpisode();
    [].forEach.call(document.querySelectorAll("[data-input-kind]"), function (b) {
      b.onclick = function () { showInputKind(b.getAttribute("data-input-kind"), true); };
    });
    var _ume = document.getElementById("useManualExample");
    if (_ume) _ume.onclick = function () {
      var ta = document.getElementById("plIdea");
      if (ta) { ta.value = MANUAL_INPUT_GUIDES[manualInputKind].example; ta.focus(); }
    };
    var _mkEp = document.getElementById("mkEp");
    if (_mkEp) _mkEp.onchange = function() {
      manualEp = +this.value || 1;
      reloadEpisode().catch(function(e) { UI.toast(e.message,"err"); });
    };
    var _mkNext = document.getElementById("manualNext");
    if (_mkNext) _mkNext.onclick = function() {
      saveManualInput(manualEp).then(function() { return plSagaEps(st); }).then(function(eps) {
        manualEp = Math.max.apply(null, eps.map(function(e) { return +e.no; }).concat([0])) + 1;
        manualEpisodes = eps.concat([{no:manualEp}]); drawEpisode();
      }).catch(function(e) { UI.toast(e.message,"err"); });
    };
    function saveManualInput(no) {
      var input = {ep:no, brief:(document.getElementById("plIdea") || {}).value || "",
        pace:(document.getElementById("plPace") || {}).value || "紧凑", notes:(document.getElementById("plNotes") || {}).value || "",
        shooting_notes:(document.getElementById("plShoot") || {}).value || ""};
      if (manualInputKind === "idea") {
        return Promise.resolve(typeof SAVE_PROJECT === "function" ? SAVE_PROJECT() : null).then(function () {
          return window.api.post("/api/saga/" + st.storyId + "/episode-input", input);
        });
      }
      var content = input.brief;
      var payload = {no:no, create:1, shooting_notes:input.shooting_notes};
      payload[manualInputKind === "story" ? "prose" : "body"] = content;
      return Promise.resolve(typeof SAVE_PROJECT === "function" ? SAVE_PROJECT() : null).then(function () {
        return window.api.post("/api/saga/" + st.storyId + "/save-episode", payload);
      });
    }
  }

  function renderArrange(main, st) {
    var slot = slotOf("slotArrange"); if (!slot) return;
    var a = (D.settings || {}).arrangement || null;
    var dur = a ? (+a.duration_sec || 60) : 60;
    var durKey = AR_DURS.some(function (p) { return p[0] === String(dur); }) ? String(dur) : "custom";
    var moods = AR_MOODS.slice();
    if (a && a.mood && moods.indexOf(a.mood) < 0) moods.push(a.mood);   /* 老安排里的自定义感觉别丢 */
    slot.innerHTML =
      '<div class="card" id="arrangeCard"><h3 style="font-size:16px">💡 先说想法，系统给你一张安排</h3>' +
      '<p class="note-gray" style="margin:0 0 8px">人物名字、地点、结尾动作都可以不写，想到什么写什么，系统负责补齐。</p>' +
      '<textarea id="arIdea" placeholder="' + esc(AR_IDEA_PH) + '" style="width:100%;height:120px;min-height:120px;' +
      'padding:14px 16px;font-size:15px;line-height:1.85;border-radius:10px">' + esc((a && a.idea) || "") + '</textarea>' +
      '<div style="display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;margin-top:10px">' +
      '<span><label style="display:block;font-size:14px;color:#5a5348;margin-bottom:4px">时长</label>' +
      '<select id="arDur" style="height:36px;min-width:110px">' + AR_DURS.map(function (p) {
        return '<option value="' + p[0] + '"' + (p[0] === durKey ? " selected" : "") + '>' + p[1] + '</option>';
      }).join("") + '</select></span>' +
      '<span id="arDurCustomWrap" style="' + (durKey === "custom" ? "" : "display:none") + '">' +
      '<label style="display:block;font-size:14px;color:#5a5348;margin-bottom:4px">秒数</label>' +
      '<input id="arDurCustom" type="number" min="10" max="600" value="' + dur + '" style="width:90px;height:36px;padding:0 10px"></span>' +
      '<span><label style="display:block;font-size:14px;color:#5a5348;margin-bottom:4px">感觉</label>' +
      sel("arMood", moods, (a && a.mood) || "日常聊天") + '</span>' +
      '<button class="btn primary" id="arDraft" style="height:38px;border-radius:8px;font-size:15px">' + AR_DRAFT_LABEL + '</button>' +
      '</div>' +
      '<div id="arCard">' + arCardHtml(a) + '</div></div>';
    var durSel = document.getElementById("arDur"), wrap = document.getElementById("arDurCustomWrap");
    if (durSel) durSel.onchange = function () {
      if (wrap) wrap.style.display = durSel.value === "custom" ? "" : "none";
      var c = document.getElementById("arDurCustom");
      if (c && durSel.value === "custom") c.focus();
    };
    on("arDraft", function () {
      var idea = ((document.getElementById("arIdea") || {}).value || "").trim();
      if (!idea) { UI.toast("先写一段想法，一两句就行", "err"); return; }
      var secs = arDuration();
      if (!secs) { UI.toast("时长填个秒数，10 到 600 之间", "err"); return; }
      var cur = (D.settings || {}).arrangement;
      if (cur && cur.status === "confirmed" && !confirm("重新安排会换掉已确认的这张，继续？")) return;
      var b = document.getElementById("arDraft");
      if (b) { b.disabled = true; b.textContent = "正在安排…（约半分钟）"; }
      var mood = (document.getElementById("arMood") || {}).value || "日常聊天";
      window.api.post("/api/saga/" + st.storyId + "/arrange", { idea: idea, duration_sec: secs, mood: mood })
        .then(function (r) { return arApply(main, st, r); })
        .then(function () {
          UI.toast("安排好了。看一遍，不对就在下面说一句");
          var c = document.getElementById("arCard");
          if (c && c.scrollIntoView) c.scrollIntoView({ behavior: "smooth", block: "start" });
        })
        .catch(function (e) { UI.toast(e.message, "err"); })
        .then(function () {
          /* 每次重新取按钮：兜底整页重画后旧按钮已不在，拿新的恢复 */
          var b2 = document.getElementById("arDraft");
          if (b2) { b2.disabled = false; b2.textContent = AR_DRAFT_LABEL; }
        });
    });
    arWireCard(main, st);
    /* /api/project/settings 只回设定表白名单里的字段，arrangement 不在其中——
       刷新后 D.settings.arrangement 是空的，已确认的卡就不见了。这里单独取一次。 */
    if (!a && st && st.storyId) {
      window.api.post("/api/saga/" + st.storyId + "/arrange/get", {})
        .then(function (r) {
          var got = r && (r.arrangement || ((r.data || {}).arrangement));
          if (got && got.items) return arApply(main, st, { arrangement: got });
        })
        .catch(function () {});
    }
  }

  function render(main, st) {
    if (MK_POLL) { clearInterval(MK_POLL); MK_POLL = null; BUSY_CONT = false; }    /* P328g③：重画页面就停掉上一轮续出片的轮询 */
    var s = D.settings, gated = D.gated ? " disabled title='生成轮启用后可用'" : "";
    /* P298：有整部规划（草稿或已确认）时，一句话故事由规划里的想法接管 */
    var planIdea = ((s.story_plan || {}).idea || "").trim();
    var hasPlan = !!(s.story_plan && (s.story_plan.episodes || []).length);
    var adopted = adoptedMap();
    // 老项目曾把同一份视觉要求拆在两个字段里。界面只展示一个总框；保存时再
    // 同步回两条旧链，既避免用户重复填写，也不破坏历史项目的兼容读取。
    var visualText = String((s.look_panel || {}).look || "").trim();
    var legacyGuide = String(s.custom_guidance || "").trim();
    if (legacyGuide && visualText.indexOf(legacyGuide) < 0) {
      visualText = [visualText, legacyGuide].filter(Boolean).join("；");
    }
    var _split = ((s.story_mode || ((s.story_plan && (s.story_plan.episodes || []).length) ? "split" : "single")) === "split");
    main.innerHTML = '<div class="setwrap">' +
      /* P452：单话模式一张卡——项目名 → 第一话讲什么（slotIdea）→ 选项 → 尺度 → 按钮（slotActions）；split 模式保持旧的两张卡 */
      (_split ? '<div id="slotIdea"></div><div class="card project-setup-card"><h3 style="font-size:16px">偏好与生成参数</h3>'
              : '<div class="card project-setup-card"><h3 style="font-size:16px">① 故事设定</h3>') +
      '<div id="blk_one" class="setup-story">' +
      '<p class="setup-label"><label>项目名（可不填，默认按编号）</label></p>' +
      '<input id="psTitle" value="' + esc(D.title || "") + '" placeholder="给这个项目起个名字" ' +
      'style="width:100%;height:38px;padding:0 12px;font-size:15px;border-radius:8px;margin-bottom:12px">' +
      "</div>" +
      (_split ? '' : '<div id="slotIdea" style="margin-bottom:14px"></div>') +
      '<div class="setup-label setup-options-title">故事类型和画面选择</div>' +
      '<div class="settings-choice-grid">' +
      '<span>' + optLabel("世界类型", "world", "psWorld", "psOptionDetail") + sel("psWorld", D.world_types, s.world_type) + "</span>" +
      '<span>' + optLabel("画风", "style", "psStyle", "psOptionDetail") + sel("psStyle", D.styles.map(function (x) { return x.name; }), s.style) + "</span>" +
      /* 【五维改造 2026-08-29】影片类型＝叙事结构、角色审美＝长相体型、视点＝摄影机身份。
         选项由 /api/kits/options 提供，没取到时退回只有「自动」的单项，不影响老项目。 */
      /* 默认节奏（影片类型）下拉已删（用户 2026-09-06 定）：节奏由结构表每话自带 */
      '<span><span class="option-label"><span>视点（摄影机）</span></span>' +
      sel("psPov", ["自动"].concat(KD.pov || []), s.pov || "电影第三人称") + "</span>" +
      /* 出视频参数：分辨率档越大越慢，步数越多越细（P242）。
         这三个下拉没有「自动」项，所以提交时不能用 pick()——它会把「自动」转成空串。 */
      '<span class="settings-extra"><span class="option-label" title="视频分辨率档（兆像素，越大越慢）"><span>分辨率档</span></span>' +
      sel("psSizeTier", SIZE_TIER_OPT, String(s.video_size_tier || "0.4")) + "</span>" +
      '<span class="settings-extra"><span class="option-label" title="视频采样步数（越多越细，越慢）"><span>采样步数</span></span>' +
      sel("psSteps", STEPS_OPT, String(s.video_steps || "10")) + "</span>" +
      '<span class="settings-extra"><span class="option-label" title="视频画幅比"><span>画幅比</span></span>' +
      sel("psRatio", RATIO_OPT, String(s.video_ratio || "16:9")) + "</span>" +
      /* P398：视频导演方式已删（深度导演实测无用，固定标准） */
      /* 题材引擎、参考图用法两个选项已删（用户定：每一话故事简介自己看自己改，
         不需要系统自动判类型；参考图统一走 loose）。备份在 trash/。 */
      /* P321：「画面观感」并入 ①「补充要求」（同一个框，规划和画面两边都送） */
      "</div>" + optionPanel("psOptionDetail") +
      /* 「整部作品长什么样」框已删（用户定）：世界类型+画风+视觉强度已覆盖，
         这个自由框实测一直空着。后端 look_panel 字段保留兼容旧项目。 */
      /* 内容尺度属于「设定」，放在人设图上面（用户定的） */
      '<div class="tendency-head">' + optLabel("内容尺度 / 夸张度（可多选）", "content_tendency", "", "tendencyDetail") + '</div>' +
      '<div class="tendency-row">' + D.content_tendencies.map(function (t) {
        return '<label style="font-size:14px;line-height:1.6"><input type="checkbox" class="ct" value="' + esc(t) + '"' +
          (s.content_tendencies.indexOf(t) >= 0 ? " checked" : "") + "> " + esc(t) + "</label>";
      }).join("") + "</div>" + optionPanel("tendencyDetail") +
      /* P398：自定义尺度框已删（尺度按勾选项走） */
      /* 预设表（选项背后的原话）是全局的，统一放在 ⚙️ 设置页；
         这里只留一行指路，免得用户在设定页翻半天。 */
      '<p class="note-gray" style="margin:10px 0 0">想改这些选项本身（加一个世界类型、改画风的定义、改人设卡字段）？去左栏 ⚙️ 设置 → 🎛 预设表。</p>' +
      (_split ? '' : '<div id="slotActions"></div>') +
      '</div>' +
      /* ③ 当前内容的输入与制作按钮由 renderPlan 画进同一张卡片 */
      '<div id="slotPlan"></div>' +
      /* 【三步走 · 步骤1 的确认顺序】故事框架 → 第一话故事 → 人物设定图 → 冻结。
         场景和场次表都归步骤2（剧本页），设定页不再显示。 */
      '<div id="slotChars"></div>' +
      /* P320：⑦「第一话故事」块（简介/原文/按设定重新生成/按原文生成剧本/体检/生视频）删掉——
         正文和剧本在 📖 剧本 页改，重写走 ⑤「生成本话全部文字」，视频走「制作本话」/🎬 分镜页；同一件事不放两处 */
      '</div>';
    renderChars(main, st, gated, adopted);
    wire(main, st, s);
  }
  function renderChars(main, st, gated, adopted) {
    var slot = document.getElementById("slotChars");
    if (slot) main = { get innerHTML() { return slot.innerHTML; },
                       set innerHTML(v) { slot.innerHTML = v; } };
    var cf = D.char_fields;
    /* 长相和身材的选项分男女两套。原来写死用 D.char_fields（女性那套），
       男性角色的"修长青年"在女性列表里找不到，下拉永远显示"自动"。 */
    function fieldsFor(sex) {
      return (D.char_fields_by_sex || {})[sex === "男" ? "男" : "女"] || cf;
    }
    main.innerHTML += '<div class="card" style="margin-top:16px"><h3 style="font-size:16px">② 人物设定</h3>' +
      '<p class="note-gray" style="margin:0 0 8px">只显示第一话登场的人物。后面话次才出场的新人物，' +
      '写到那一话时在剧本页添加。</p>' +
      '<div style="text-align:right;margin-bottom:8px">' +
      '<button class="btn" id="fixCharImgs" title="只给还没有设定图的人物出图（偶发失败后补图用）">🎭 补出人设图</button>' +
      '<button class="btn" id="addChar" style="margin-left:8px">＋ 添加人物</button></div>' +
      '<div id="charList">' + D.characters.map(function (c, i) {
        /* 只显示第一话在场的（first_episode<=1）。龙套/后面话次的角色不占位，
           但用 return "" 跳过而不是过滤数组——下面接线按 index 遍历全部，
           过滤会让 index 错位。 */
        if (+(c.first_episode || 1) > 1) return "";
        var av = adopted[c.character_id];
        return '<div class="card character-card" id="blk_char' + i + '" style="display:grid">' +
          '<div>' + (av && av.path
            ? '<img src="/files/' + esc(av.path.replace(/\\/g, "/")) + '" style="width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:8px">'
            : imgPlaceholder("人物" + (i + 1) + "（主要角色）", "还没有三视图")) +
          candRow(c.character_id, "人物" + (i + 1) + "（主要角色）") +
          /* 图下面那行「身材：… · 性格：…」已删（用户 2026-08-26）：
             下拉里本来就看得见，重复占地方。 */
          "</div>" +
          '<div class="character-editor">' +
          /* 【字段名必须和数据模型一致】原来这里读的键全是错的：
               长相读 c.look（数据里是 c.face_type，永远显示"自动"）
               服装轮廓读 c.clothing（那是自由文本描述，不在选项里，永远"自动"）
               服装描述读 c.clothing_requirement（那是轮廓枚举，显示成"修身利落"）
             而且保存端也按同一套错的键写回去，点一次保存就把服装描述冲掉。
             姓名和发型这两个最关键的字段界面上压根没有输入框。
             下拉选项还写死了女性那一套，男性角色的身材永远匹配不上。 */
          '<div class="character-options character-basics">' +
          '<span>姓名<br><input id="cName' + i + '" value="' + esc(c.name || "") + '" placeholder="必填，后面出图和视频都用这个名字" style="width:100%;height:36px;padding:0 12px"></span>' +
          /* P316：年龄可以是词（少年/成年，人物表现在这么写）；原来 type=number 把「少年」洗成空，一保存卡上年龄就没了 */
          '<span>年龄<br><input id="cAge' + i + '" value="' + esc(String(c.age || "")) + '" placeholder="例：24 / 少年 / 中年" style="width:100%;height:36px;padding:0 12px"></span>' +
          '<span>' + optLabel("性别", "sex", "cSex" + i, "charOptionDetail" + i) + charSel("cSex" + i, ["女", "男"], c.sex) + "</span>" +
          /* P316：卡上的类型是身份词（魔法师/猫娘奴隶）时不在物种选项里，原来一保存就被顶成「人类」——不在选项里的值补成一项 */
          /* P352（用户 9-14 定）：人物类型改成「身份」自由文字，故事自动填（勇者/修女/武术家…），种族按文字自动判 */
          '<span>身份<br><input id="cType' + i + '" value="' + esc(c.char_type || "") + '" placeholder="例：勇者 / 修女 / 女战士（故事自动填）" style="width:100%;height:36px;padding:0 12px"></span></div>' +
          '<div class="note-gray" style="margin:8px 0 4px">头身比例和身材默认按故事人物生成；手动选择后以你的选择为准。两项只控制人物设定图。</div>' +
          '<div class="character-options character-visuals">' +
          '<span>' + optLabel("人设图身体比例", "sheet_ratio", "cSheetRatio" + i, "charOptionDetail" + i) +
          charSel("cSheetRatio" + i, fieldsFor(c.sex)["身体比例"], c.sheet_body_ratio) + "</span>" +
          '<span>' + optLabel("身材", "body", "cBuild" + i, "charOptionDetail" + i, "cSex" + i) + charSel("cBuild" + i, fieldsFor(c.sex)["身材"], c.build) + "</span>" +
          '<span>' + optLabel("五官骨相", "face", "cLook" + i, "charOptionDetail" + i, "cSex" + i) + charSel("cLook" + i, fieldsFor(c.sex)["长相"], c.face_type) + "</span>" +
          '<span>' + optLabel("发型预设", "hair", "cHairPreset" + i, "charOptionDetail" + i, "cSex" + i) + charSel("cHairPreset" + i, fieldsFor(c.sex)["发型"], c.hair_preset) + "</span>" +
          '<span><span class="option-label"><span>最终发型和发色</span></span><input id="cHair' + i + '" value="' + esc(c.hair || "") + '" placeholder="选择预设会自动填；也可以直接手改" style="width:100%;height:36px;padding:0 12px"></span>' +
          '<span>' + optLabel("性格与神态", "pose", "cChar" + i, "charOptionDetail" + i) + charSel("cChar" + i, cf["性格"], c.behavior_anchor || c.personality) + "</span></div>" +
          '<div class="character-options character-outfit"><span>' +
          optLabel("具体服装预设", "outfit", "cOutfit" + i, "charOptionDetail" + i) + charSel("cOutfit" + i, outfitOptionsFor((D.settings || {}).world_type), c.outfit_preset) +
          '</span><span>' +
          optLabel("服装款式", "cloth", "cCloth" + i, "charOptionDetail" + i) + charSel("cCloth" + i, cf["服装轮廓"], c.clothing_requirement) +
          '</span></div>' + optionPanel("charOptionDetail" + i) +
          '<label style="display:block;font-size:14px;color:#5a5348;margin-top:8px">人物详细设定</label>' +
          '<textarea class="character-details" id="cDetails' + i + '" style="min-height:200px" placeholder="声线、性格描述、服装描述、外貌与识别特征——这个人的其他详细设定写在这里">' +
          esc(joinCharacterDetails(c)) + "</textarea>" +
          /* 人物卡只留一个主动作：保存当前卡片并按当前设定生成设计图。
             单独保存和单独重出图已隐藏，提示词面板仍可按当前文本直接出图。 */
          '<div class="char-actions">' + UIActions.row({ id: "char:" + i, kind: "character_image", gated: gated,
                          hasContent: true, redesign: true, hideState: true,
                          hideSave: true, hideRegenSheet: true,
                          labels: { generate: "🎨 按当前人设生成设计图" } }) +
          '<button class="btn small" data-prompt="' + esc(c.character_id || "") + '">📝 提示词</button>' +
          '<button class="btn del small" data-delc="' + esc(c.character_id || "") + '">－ 删除人物</button></div>' +
          '<div class="prompt-box" id="pbox_' + esc(c.character_id || "") + '" hidden></div></div></div>';
      }).join("") + "</div></div>";
  }
  /* renderScenes 已整体删除（用户定的）：场景不在设定页维护，
     它跟着故事走——在 📖 故事 里按话生成、按需补图。 */

  /* 防御：删了某块界面却漏删它的事件绑定，会让整页白屏（实测栽过两次）。
     统一走这个 on()，元素不在就静默跳过。 */
  function on(id, fn) {
    var el = document.getElementById(id);
    if (el) el.onclick = fn;
  }

  /* ───── P327③：出片前门停下来问的问题，在设定页就能答（和 🎬 分镜页 同一套路由）─────
     kind=scene  → /shotlist-scene；speaker → /shotlist-speaker；length → /arrange/length；
     其他（problem/event/beat…）→「就按现在的出片」(force) / 「我去改剧本」。
     全部答完 → /api/timeline/<sid>/gen-next {n:5, ep, force}，进度就在 mkStatus 里滚，出完提示去分镜页看。 */
  var ASK_MEMO = {};
  function askKeyS(st, ep, a) { return st.storyId + "#" + ep + "|" + (a.kind || "") + "|" + (a.ref || a.question || ""); }
  function askDoneS(st, ep, a) { return a.answered || ASK_MEMO[askKeyS(st, ep, a)] || ""; }
  function askOptions(a) {
    var opts = a.options || [];
    if (["scene", "length", "speaker"].indexOf(a.kind) < 0) opts = ["就按现在的出片", "我去改剧本"];
    return opts;
  }
  function renderAsks(st, ep, asks) {
    var box = document.getElementById("mkAsks");
    if (!box) return;
    asks = Array.isArray(asks) ? asks : [];
    if (!asks.length) { box.innerHTML = ""; return; }
    if (window.GateChat) {                                    /* P328②：对话框（和分镜页同一个） */
      window.GateChat.mount(box, { sid: st.storyId, ep: ep, asks: asks, n: 5, onClear: function (force) { startAfterChat(st, ep, !!force); } });
      return;
    }
    var left = asks.filter(function (a) { return !askDoneS(st, ep, a); }).length;
    box.innerHTML = '<div style="margin-top:10px;font-size:13px;line-height:1.7">' +
      '<div style="color:var(--red);font-weight:600">第 ' + ep + ' 话先不出片——' + asks.length + ' 个问题要你定' + (left ? '（还剩 ' + left + ' 个，答完自动接着出片）' : '（都答了，正在接着出片）') + '：</div>' +
      asks.map(function (a, i) {
        var opts = askOptions(a), done = askDoneS(st, ep, a);
        var circ = "①②③④⑤⑥⑦⑧⑨⑩"[i] || (i + 1) + ".";
        return '<div style="margin:6px 0;padding:6px 8px;background:#fbf8f2;border-radius:8px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">' +
          "<span><b>" + circ + "</b> " + esc(a.question || "") + "</span>" +
          (done
            ? '<span class="stat-green">✓ ' + esc(done) + "</span>"
            : opts.map(function (o, k) { return '<button class="btn small" data-mkask="' + i + '" data-opt="' + k + '">' + esc(o) + "</button>"; }).join("") +
              (a.kind === "speaker" ? '<input data-mkask-free="' + i + '" placeholder="或填名字后回车" style="height:28px;padding:0 8px;border-radius:6px;width:130px">' : "")) +
          "</div>";
      }).join("") + "</div>";
    [].forEach.call(box.querySelectorAll("[data-mkask]"), function (b) {
      b.onclick = function () {
        var i = parseInt(b.getAttribute("data-mkask"), 10), k = parseInt(b.getAttribute("data-opt"), 10);
        var a = asks[i]; if (!a) return;
        answerAskS(st, ep, asks, a, String(askOptions(a)[k] || ""), b);
      };
    });
    [].forEach.call(box.querySelectorAll("[data-mkask-free]"), function (inp) {
      inp.onkeydown = function (e) {
        if (e.key !== "Enter") return;
        var i = parseInt(inp.getAttribute("data-mkask-free"), 10), a = asks[i], v = (inp.value || "").trim();
        if (a && v) answerAskS(st, ep, asks, a, v, inp);
      };
    });
  }
  function answerAskS(st, ep, asks, a, opt, btn) {
    var sid = st.storyId, req;
    opt = String(opt || "");
    if (a.kind === "scene") {
      var mNew = /^新建场景卡「(.+)」$/.exec(opt);
      req = window.api.post("/api/timeline/" + sid + "/shotlist-scene", mNew ? { ep: ep, scene_id: a.ref, card: mNew[1], create: true } : { ep: ep, scene_id: a.ref, card: opt });
    } else if (a.kind === "speaker") {
      req = window.api.post("/api/timeline/" + sid + "/shotlist-speaker", { ep: ep, line_n: a.ref, name: opt });
    } else if (a.kind === "length") {
      req = window.api.post("/api/saga/" + sid + "/arrange/length", { choice: /延长/.test(opt) ? "延长本话" : "留到下一话" });
    } else {
      if (!/^就按|^已经拍了|^继续/.test(opt)) { UI.toast("好，去 📖 剧本 页改剧本；改完再点「制作本话」"); return Promise.resolve(); }
      req = Promise.resolve();
    }
    if (btn) btn.disabled = true;
    return req.then(function () {
      a.answered = opt; ASK_MEMO[askKeyS(st, ep, a)] = opt;
      UI.toast("已记下「" + opt + "」");
      renderAsks(st, ep, asks);
      return continueAsksS(st, ep, asks);
    }).catch(function (e) { if (btn) btn.disabled = false; UI.toast("没记上：" + (e.message || ""), "err"); });
  }
  var BUSY_CONT = false, MK_POLL = null;
  function continueAsksS(st, ep, asks) {
    if (!asks.length || asks.some(function (a) { return !askDoneS(st, ep, a); }) || BUSY_CONT) return Promise.resolve();
    var force = asks.some(function (a) { return /^就按|^已经拍了|^继续/.test(String(askDoneS(st, ep, a) || "")); });
    return startAfterChat(st, ep, force);
  }
  /* P328②/P342：对话框里都答完 → 接着出这一话全部视频并合成（走 auto-all），进度在 mkStatus 里滚 */
  function startAfterChat(st, ep, force) {
    if (BUSY_CONT) return Promise.resolve();
    BUSY_CONT = true;
    var stat = document.getElementById("mkStatus");
    if (stat) stat.textContent = "问题都答了，接着出第 " + ep + " 话剩下的视频…";
    if (window.UIActions && window.UIActions.progress) window.UIActions.progress.busy("接着出片…");
    return window.api.post("/api/saga/" + st.storyId + "/auto-all", { ep: ep, force: force })
      .then(function () {
        UI.toast("接着出第 " + ep + " 话全部视频，出完自动合成一条");
        var box = document.getElementById("mkAsks"); if (box) box.innerHTML = "";
        if (MK_POLL) clearInterval(MK_POLL);
        var t = MK_POLL = setInterval(function () {
          window.api.post("/api/saga/" + st.storyId + "/progress", {}).then(function (j) {
            j = j || {};
            var st2 = document.getElementById("mkStatus");
            if (j.running) { if (st2) st2.textContent = "出片中：" + (j.step || "") + (j.total ? "（" + (j.done || 0) + "/" + j.total + "）" : ""); return; }
            clearInterval(t); MK_POLL = null; BUSY_CONT = false;
            if (window.UIActions && window.UIActions.progress) window.UIActions.progress.done(j.err ? "失败" : "完成");
            if (j.err) { if (st2) st2.textContent = "出片失败：" + j.err; UI.toast("出片失败：" + j.err, "err"); return; }
            if (Array.isArray(j.blocked) && j.blocked.length) { if (st2) st2.textContent = "又有要你定的："; renderAsks(st, ep, j.blocked); return; }
            var msg = j.merged ? ("第 " + ep + " 话全部视频出完并合成了一条：" + j.merged) : ("第 " + ep + " 话出片结束，但没合成——已出的段都在，看任务日志里的 error，修好再点一次会接着补");
            if (st2) st2.textContent = msg;
            UI.toast(msg, j.merged ? undefined : "err");
          }).catch(function () {});
        }, 3000);
      })
      .catch(function (err) { BUSY_CONT = false; UI.toast("接着出片没启动：" + (err.message || ""), "err"); });
  }
  function checkBlocked(st) {
    window.api.post("/api/saga/" + st.storyId + "/progress", {}).then(function (j) {
      if (!j || j.running || !Array.isArray(j.blocked) || !j.blocked.length) {
        /* P328g②：续出片走的是分镜那边的任务，停下的问题落在那边——也看一眼 */
        var ep0 = parseInt((document.getElementById("mkEp") || {}).value || "1", 10) || 1;
        return window.api.post("/api/timeline/" + st.storyId + "/progress", { ep: ep0 }).then(function (d0) {
          var j2 = (d0 && d0.job) || {};
          if (!j2 || j2.running || !Array.isArray(j2.blocked) || !j2.blocked.length) return;
          renderAsks(st, parseInt(j2._ep || ep0, 10) || ep0, j2.blocked);
        });
      }
      var ep = parseInt(j.ep || (document.getElementById("mkEp") || {}).value || "1", 10) || 1;
      renderAsks(st, ep, j.blocked);
    }).catch(function () {});
  }

  function wire(main, st, s) {
    renderPlan(main, st);              /* 整部规划卡（P287）：想法 → 整部方案 → 改/确认 → 逐话生成 */
    /* P353：门不再问，设定页不画问题清单 */
    /* 一句话失焦自动保存。实测踩过：用户写完直接切项目，回来框是空的——
       这一栏是整个项目的根，不能靠人记得点保存按钮。 */
    (function () {
      var one = document.getElementById("psOne");
      if (!one) return;
      if (s.story_plan && (s.story_plan.episodes || []).length) { one.readOnly = true; one.style.background = "#f6f2ea"; return; }
      var last = one.value;
      one.onblur = function () {
        var v = one.value.trim();
        if (v === last.trim()) return;
        last = v;
        window.api.post("/api/project/settings",
                        { story_id: st.storyId, settings: { one_line: v } })
          .then(function () { window.UI.toast("一句话已自动保存"); })
          .catch(function () {});
      };
    })();

    /* 所有下拉/多选的解释都从后端的真实提示词定义读取。页面不再常驻堆注释，
       需要时点小「详细」查看当前选项到底会给模型什么。 */
    (function () {
      var defs = D.option_defs || {};
      function defOf(kind, value, sex) {
        if (value === "自动" || value === "自动判断") {
          return "系统会结合一句话故事、世界类型和人物身份自动选择，并把最终结果写入生成提示词。";
        }
        if (value === "自定义") return "不追加内置预设，直接使用你在下方填写的文字。";
        if (kind === "sex") return value === "男" ? "使用男性脸型与身材预设表。" : "使用女性脸型与身材预设表。";
        if (kind === "outfit") {
          var world = (document.getElementById("psWorld") || {}).value || (D.settings || {}).world_type;
          return (((D.outfit_preset_defs_by_world || {})[world] || {})[value]) ||
            ((D.outfit_preset_defs || {})[value]) || "当前服装方向由人物文字与世界观共同补全。";
        }
        var table = defs[kind] || {};
        if ((kind === "face" || kind === "body" || kind === "hair") && table[sex]) table = table[sex];
        return table[value] || "当前选项会按名称作为明确约束写入生成提示词。";
      }
      [].forEach.call(document.querySelectorAll("[data-option-detail]"), function (btn) {
        btn.onclick = function () {
          var panel = document.getElementById(btn.getAttribute("data-option-panel"));
          if (!panel) return;
          var kind = btn.getAttribute("data-option-detail");
          var source = document.getElementById(btn.getAttribute("data-option-source"));
          var sex = (document.getElementById(btn.getAttribute("data-option-sex")) || {}).value || "女";
          var key = kind + ":" + (source ? source.value : "all") + ":" + sex;
          if (!panel.hidden && panel.getAttribute("data-open-key") === key) {
            panel.hidden = true; return;
          }
          if (kind === "content_tendency") {
            /* 每个尺度选项摊开写清对三层的实际影响——这三句就是模型
               真正收到的原话（用户 2026-09-01 要求说清对什么有影响）。
               文案在 presets/content_scale.json，改完不用重启。 */
            var EFF = D.content_scale_effects || {};
            var LAYER = [["story", "故事", "决定故事里能发生什么、写到什么程度"],
                         ["figure", "人设图", "决定人物长什么样、穿多少、身材如何"],
                         ["shot", "视频画面", "决定镜头怎么拍、贴多近"]];
            panel.innerHTML =
              '<div style="opacity:.75;margin-bottom:8px">' +
              '勾上之后，下面这些话会**原样进到提示词里**。可多选，叠加生效。' +
              '不勾任何一项，这一行整个不出现。</div>' +
              (D.content_tendencies || []).map(function (name) {
                var e = EFF[name] || {};
                var rows = LAYER.map(function (L) {
                  var v = e[L[0]] || "";
                  if (!v) { return ""; }
                  return '<div style="margin:4px 0 4px 12px">' +
                    '<span style="display:inline-block;min-width:64px;opacity:.7">' +
                    esc(L[1]) + '</span><span>' + esc(v) + '</span></div>';
                }).join("");
                return '<div style="margin-bottom:12px"><b>' + esc(name) +
                  '</b>' + (rows || '<div style="margin-left:12px;opacity:.6">' +
                  esc(defOf(kind, name, sex)) + '</div>') + '</div>';
              }).join("") +
              '<div style="opacity:.6;margin-top:10px;border-top:1px solid ' +
              'rgba(128,128,128,.3);padding-top:8px">' +
              '故事＝' + esc(LAYER[0][2]) + '｜人设图＝' + esc(LAYER[1][2]) +
              '｜视频画面＝' + esc(LAYER[2][2]) + '<br>' +
              '想改这些话：编辑 presets/content_scale.json，改完下次生成就生效。' +
              '想给单个项目临时加要求：用下面那个「自定义尺度」框，' +
              '它优先级最高、三层都会带上。</div>';
          } else if (kind === "face" || kind === "body" || kind === "hair") {
            var value = source ? source.value : "";
            var table = (defs[kind] || {})[sex] || {};
            var custom = value && value !== "自动" && !table[value]
              ? '<div style="margin-bottom:8px"><b>当前自定义：' + esc(value) + '</b><span>' + esc(defOf(kind, value, sex)) + '</span></div>' : '';
            panel.innerHTML = custom + '<div style="opacity:.7;margin-bottom:7px">下面是' + esc(sex) + '性可选内容；当前值会作为硬约束进入人设图提示词。</div>' +
              Object.keys(table).map(function (name) {
                return '<div style="margin:5px 0"><b' + (name === value ? ' style="color:#9a5b22"' : '') + '>' +
                  esc(name) + (name === value ? '（当前）' : '') + '</b><span>' + esc(table[name]) + '</span></div>';
              }).join("");
          } else {
            var value = source ? source.value : "";
            panel.innerHTML = '<b>' + esc(value || "当前选项") + '</b><span>' + esc(defOf(kind, value, sex)) + '</span>';
          }
          panel.setAttribute("data-open-key", key);
          panel.hidden = false;
        };
      });
    })();

    /* 【每一块内容的标准动作】用户定的规矩：所有设定单页都要有
       AI 扩写 / 重新扩写 / 重新生图 / 保存。做成一个函数挂在每块上，
       而不是每块手写一遍——漏一个就全站都漏，补一个就全站都有。 */
    function attachActions(id, getText, setText, opts) {
      var o = opts || {};
      window.UIActions.bind(document.getElementById("blk_" + id) || document, {
        generate: function (_, btn) { return doExpand(id, getText, setText, btn); },
        expand: function (_, btn) { return doExpand(id, getText, setText, btn); },
        regenerate: function (_, btn) { return doExpand(id, getText, setText, btn); },
        save: function () { return saveBlock(id, o); },
        regen_sheet: o.regenSheet,
        change_outfit: o.changeOutfit
      });
    }

    /* 扩写：调通用接口，回来用对照视图给用户看清"哪些是新加的"，
       并且留一个「只要原文」的退路——AI 改坏了要能一键回去。 */
    function doExpand(id, getText, setText, btn) {
      var text = (getText() || "").trim();
      if (!text) { window.UI.toast("这一块还是空的，先写点东西再让 AI 扩写"); return; }
      return window.api.post("/api/create/expand",
                             { story_id: st.storyId, text: text, purpose: PURPOSE[id] || "内容" })
        .then(function (r) {
          var d = (r && r.data) || {};
          if (!d.expanded) { window.UI.toast("扩写没成功：" + ((r && r.error) || "未知原因")); return; }
          setText(d.expanded);
          var host = document.getElementById("blk_" + id);
          if (host) {
            var old = host.querySelector(".diffbox");
            if (old) old.remove();
            var box = window.UIActions.diffView(d.original, d.expanded, function (o) { setText(o); });
            box.classList.add("diffbox");
            host.appendChild(box);
          }
          window.UI.toast("扩写完成：" + (d.note || ""));
        });
    }

    var PURPOSE = { one: "一句话故事", look: "整部作品长什么样", plan: "故事简介" };

    function currentProjectPayload() {
      /* 当前话输入就是事实来源；兼容旧项目仍读取 psOne/旧 one_line。 */
      var ideaEl = document.getElementById("plIdea");
      var one = (ideaEl || document.getElementById("psOne") || {}).value || ((D.settings || {}).one_line || "");
      /* 粘贴完整故事/剧本时不能拿几千字覆盖项目最初想法。 */
      if (ideaEl && ideaEl.dataset.kind && ideaEl.dataset.kind !== "idea") {
        one = s.initial_story_input || s.one_line || "";
      }
      var title = ((document.getElementById("psTitle") || {}).value || "").trim();
      /* 项目名没填 → 按数字编号走（用户定），不再截一句话故事当名字 */
      if (!title || title === "未命名故事") {
        var pnum = String((st.storyId || "")).replace(/\D/g, "").replace(/^0+/, "");
        title = "项目" + (pnum || "1");
      }
      var tends = [].map.call(document.querySelectorAll(".ct:checked"), function (x) { return x.value; });
      var pick = function (id) {
        var v = (document.getElementById(id) || {}).value || "";
        return v === "自动" ? "" : v;
      };
      var settings = {
        genre: pick("psGenre"),
        look: "",   /* 角色审美已撤：由画风推出真实组/夸张组（2026-09-04） */
        pov: pick("psPov"),
        kit: s.kit || "",
        world_type: (document.getElementById("psWorld") || {}).value || s.world_type,
        visual_strength: "自动",   /* 视觉强度已撤：由画风推出（2026-09-04） */
        style: (document.getElementById("psStyle") || {}).value || s.style,
        ref_mode: "loose",
        /* 【必须写在这里】这个对象是写死的字段清单，不加就永远发不上去，
           后端 save() 是深合并、不报错也不丢旧值，只是静默存不进新值（P242）。 */
        video_size_tier: (document.getElementById("psSizeTier") || {}).value || "0.4",
        video_steps: (document.getElementById("psSteps") || {}).value || "10",
        video_ratio: (document.getElementById("psRatio") || {}).value || "16:9",
        video_director_mode: "standard",           /* P398：深度导演已废 */
        story_mode: s.story_mode || ((s.story_plan && (s.story_plan.episodes || []).length) ? "split" : "single"),
        story_pace: s.story_pace || "适中",
        prose_words: s.prose_words || "3000～4000",
        extra_requirements: (document.getElementById("psExtra") || {}).value || s.extra_requirements || "",
        one_line: (s.story_mode === "split" ? one : (s.initial_story_input || s.one_line || "")),
        content_tendencies: tends,
        custom_content_scale: s.custom_content_scale || ""
      };
      var plan = document.getElementById("psPlan");
      /* 原来这里把 psLook（角色审美下拉）的值当成已删的「整部作品长什么样」文本框写进
         custom_guidance 和 look_panel.look，"网红精致"四个字被灌进人物图/场景图/镜头三层（2026-09-04 体检查出）。
         现在这两个字段只保留旧值，不再由界面写。 */
      settings.custom_guidance = "";
      settings.look_panel = { look: "", hero_style: "" };
      if (plan) settings.plan = plan.value || s.plan || "";
      return { story_id: st.storyId, title: title, settings: settings };
    }

    function saveCurrentProject() {
      var input = {ep:+((document.getElementById("mkEp") || {}).value || manualEp), brief:(document.getElementById("plIdea") || {}).value || "",
        pace:(document.getElementById("plPace") || {}).value || "紧凑", notes:(document.getElementById("plNotes") || {}).value || "",
        shooting_notes:(document.getElementById("plShoot") || {}).value || ""};
      return window.api.post("/api/project/settings", currentProjectPayload()).then(function (j) {
        /* skipRoute=true：只刷新顶部项目名下拉，不重渲染本页（重渲染会把
           置灰计时中的「全部生成」按钮换成新按钮，看着像点了没反应）。 */
        if (window.Shell && window.Shell.reloadStories) window.Shell.reloadStories(true);
        var k = ((document.getElementById("plIdea") || {}).dataset || {}).kind || "idea";
        if (s.story_mode !== "split" && k === "idea") return window.api.post("/api/saga/"+st.storyId+"/episode-input", input);
        return j;
      });
    }
    SAVE_PROJECT = saveCurrentProject;

    function saveBlock(id, o) {
      saveCurrentProject()
        .then(function () { window.UI.toast("已保存"); })
        .catch(function (e) { window.UI.toast(e.message, "err"); });
      return true;
    }

    attachActions("look",
      function () { return (document.getElementById("psLook") || {}).value; },
      function (v) { var e = document.getElementById("psLook"); if (e) e.value = v; });
    attachActions("one",
      function () { return (document.getElementById("psOne") || {}).value; },
      function (v) { var e = document.getElementById("psOne"); if (e) e.value = v; });
    attachActions("plan",
      function () { return (document.getElementById("psPlan") || {}).value; },
      function (v) { var e = document.getElementById("psPlan"); if (e) e.value = v; });

    function characterPayload(c, i) {
      var v = function (id) { return (document.getElementById(id + i) || {}).value || ""; };
      var detail = splitCharacterDetails(v("cDetails"), c);
      return {
        name: v("cName") || c.name, age: v("cAge"), sex: v("cSex"),
        sheet_body_ratio: v("cSheetRatio"),                                                           /* 只用于人物设定图 */
        char_type: v("cType"),
        face_type: v("cLook"),
        build: v("cBuild"),
        behavior_anchor: v("cChar"),
        hair_preset: v("cHairPreset"),
        hair: v("cHair") || detail.hair,
        voice: detail.voice,
        personality: detail.personality,
        clothing_requirement: v("cCloth"),
        outfit_preset: v("cOutfit"),
        clothing: detail.clothing,
        appearance_details: detail.appearance_details
      };
    }

    function saveCharacter(c, i) {
      return window.api.post("/api/story/" + st.storyId + "/characters/" +
        encodeURIComponent(c.character_id) + "/update", characterPayload(c, i))
        .then(function () { return load(main, st); });
    }

    /* 批量保存所有【已渲染】的人物卡（全部生成前调用）。没渲染出来的卡跳过，
       别用空值把它覆盖了。 */
    function saveAllCharacters() {
      var jobs = [];
      (D.characters || []).forEach(function (c, i) {
        if (!document.getElementById("cName" + i)) return;
        jobs.push(window.api.post("/api/story/" + st.storyId + "/characters/" +
          encodeURIComponent(c.character_id) + "/update", characterPayload(c, i))
          .catch(function () {}));
      });
      return Promise.all(jobs);
    }

    /* 每张人物卡自己保存当前人物，不再依赖页面顶部的批量保存。 */
    (D.characters || []).forEach(function (c, i) {
      attachActionsCard("blk_char" + i, "cDetails" + i, "人物外观", function () {
        return saveCharacter(c, i);
      }, c.character_id, "character");
    });

    function attachActionsCard(hostId, fieldId, purpose, doSave, ownerId, kind) {
      var host = document.getElementById(hostId);
      if (!host) return;
      var get = function () { return (document.getElementById(fieldId) || {}).value || ""; };
      var set = function (v) { var e = document.getElementById(fieldId); if (e) e.value = v; };
      var redesign = function (_, b) {
        /* 人物卡唯一主按钮：当前选项和文本就是事实，不再让角色设计师重选一遍。 */
        if (kind !== "character" || !ownerId) return cardExpand(host, get, set, purpose);
        if (!window.confirm("保存当前人物卡，并严格按照现在的选项和文字生成一张新设计图？")) return;
        window.UIActions.progress.busy("正在保存当前人设…");
        if (b) b.disabled = true;
        return Promise.resolve(doSave())
          /* 手改提示词优先级最高；这里必须先清掉，否则旧提示词会覆盖刚保存的人物卡。 */
          .then(function () {
            return window.api.post("/api/asset/prompt", {
              story_id: st.storyId, owner_id: ownerId, kind: "character_master", reset: true
            });
          })
          .then(function () {
            window.UIActions.progress.busy("正在按当前人设生成设计图…");
            return window.api.post("/api/produce", {
              story_id: st.storyId, kind: "character_master", owner_id: ownerId
            });
          })
          .then(function () {
            return new Promise(function (res, rej) {
              var t = setInterval(function () {
                window.api.post("/api/produce/progress", { story_id: st.storyId, owner_id: ownerId })
                  .then(function (j) {
                    window.UIActions.progress.busy(j.step || "生成中…");
                    if (!j.running) {
                      clearInterval(t);
                      if (j.err) { rej(new Error(j.err)); return; }
                      res(j);
                    }
                  }).catch(function () {});
              }, 3000);
            });
          })
          .then(function () {
            if (b) b.disabled = false;
            window.UIActions.progress.done("新设计图已生成并采用");
            window.UI.toast("已按当前人物卡生成并采用新设计图");
            return load(main, st);
          })
          .catch(function (e) {
            if (b) b.disabled = false;
            window.UIActions.progress.stuck("失败");
            window.UI.toast(e.message, "err");
          });
      };
      window.UIActions.bind(host, {
        generate: redesign,
        expand: redesign,
        regenerate: redesign,
        save: function () {
          return Promise.resolve(doSave()).then(function () {
            window.UI.toast("人物已保存");
            return true;
          });
        },
        regen_sheet: function (_, b) {
          /* 出图 2~4 分钟。必须有进度，否则用户以为按钮坏了 */
          window.UIActions.progress.busy("正在生成设定图…");
          if (b) { b.disabled = true; }
          return window.api.post("/api/produce",
            { story_id: st.storyId, kind: kind === "scene" ? "scene_master" : "character_master",
              owner_id: ownerId })
            .then(function () {
              return new Promise(function (res, rej) {
                var t = setInterval(function () {
                  window.api.post("/api/produce/progress",
                                  { story_id: st.storyId, owner_id: ownerId })
                    .then(function (j) {
                      window.UIActions.progress.busy(j.step || "生成中…");
                      if (!j.running) {
                        clearInterval(t);
                        if (b) b.disabled = false;
                        if (j.err) { window.UIActions.progress.stuck("失败");
                                     window.UI.toast("失败：" + j.err, "err"); rej(new Error(j.err)); return; }
                        window.UIActions.progress.done("设定图已生成");
                        window.UI.toast("设定图已生成并采用");
                        res(load(main, st));
                      }
                    }).catch(function () {});
                }, 3000);
              });
            })
            .catch(function (e) {
              if (b) b.disabled = false;
              window.UIActions.progress.stuck("失败");
              window.UI.toast(e.message, "err");
            });
        },
        change_outfit: function () {
          window.UI.toast("先在「服装描述」里改成新的一套，再点重新生成设定图");
        }
      });
    }

    function cardExpand(host, get, set, purpose) {
      var text = (get() || "").trim();
      if (!text) { window.UI.toast("这一栏还是空的，先写点东西再让 AI 扩写"); return; }
      return window.api.post("/api/create/expand",
                             { story_id: st.storyId, text: text, purpose: purpose })
        .then(function (r) {
          var d = (r && r.data) || {};
          if (!d.expanded) { window.UI.toast("扩写没成功：" + ((r && r.error) || "未知原因")); return; }
          set(d.expanded);
          var old = host.querySelector(".diffbox");
          if (old) old.remove();
          var box = window.UIActions.diffView(d.original, d.expanded, function (o) { set(o); });
          box.classList.add("diffbox");
          host.appendChild(box);
          window.UI.toast("扩写完成：" + (d.note || ""));
        });
    }

    on("genText", function () { gateMake("text"); });
    on("genImages", function () { gateMake("images"); });
    on("genAll", function () { gateMake("video"); });
    on("genStory", function () { gateGen(true); });

    /* ───── 📐 故事结构 ───── */
    var STRUCT = (s.plan_rows && s.plan_rows.length) ? s.plan_rows.map(function (r) { return Object.assign({}, r); }) : [];
    var STRUCT_DESIGN = s.story_design || null;
    var PACES = ["日常", "推进", "高潮"];
    function structRender(qs) {
      var box = document.getElementById("structBox"), host = document.getElementById("structRows");
      if (!box || !host) return;
      box.style.display = STRUCT.length ? "block" : "none";
      host.innerHTML = STRUCT.map(function (r, i) {
        var evs = (r.events || []).map(function (e, k) { return '<span style="display:inline-block;background:#f3f0ea;border-radius:6px;padding:1px 8px;margin:2px 4px 2px 0;font-size:12px">' + (k + 1) + ' ' + esc(e) + '</span>'; }).join("");
        return '<div class="struct-row" data-i="' + i + '" style="display:grid;grid-template-columns:52px 1fr 96px 60px 28px;gap:8px;align-items:start;padding:8px 0;border-top:1px solid #eee7dc">' +
          '<b style="padding-top:8px">第' + (i + 1) + '话</b>' +
          '<div><textarea class="struct-one" style="width:100%;min-height:54px;padding:8px 10px;font-size:14px;line-height:1.6;border-radius:8px" placeholder="这一话讲什么——写事件就行">' + esc(r.one_line || "") + '</textarea>' +
          '<div style="margin-top:4px">' + (evs || '<span style="font-size:12px;color:#8b8375">（点「↻ 重新排」拆事件）</span>') +
          (r.no_strike ? '<span style="font-size:12px;color:#8a5a20;margin-left:6px">对峙而不打</span>' : "") +
          ((r.cast || r.resistance || r.landing) ? '<div style="font-size:12px;color:#8b8375;margin-top:2px">' +
            (r.cast ? "在场：" + esc(r.cast) + "　" : "") + (r.resistance ? "阻力：" + esc(r.resistance) + "　" : "") + (r.change ? "变化点：" + esc(r.change) + "　" : "") + (r.landing ? "落点：" + esc(r.landing) : "") +
            (r.note ? '　<b style="color:#8a5a20">' + esc(r.note) + "</b>" : "") + "</div>" : "") + '</div></div>' +
          '<select class="struct-pace" style="height:34px;border-radius:8px">' + PACES.map(function (p) { return '<option' + (p === r.pace ? " selected" : "") + '>' + p + '</option>'; }).join("") + '</select>' +
          '<span style="font-size:12px;color:#8b8375;padding-top:9px">' + esc(r.geometry || "") + '</span>' +
          '<button class="btn small struct-del" title="删掉这一话" style="padding:4px 8px">✕</button>' +
          '</div>';
      }).join("");
      var q = document.getElementById("structQs");
      if (q) q.innerHTML = (qs && qs.length) ? '❓ 还缺这几件事，答在对应那一话的框里再点「↻ 重新排」：<br>' + qs.map(function (x) { return "· " + esc(x); }).join("<br>") : "";
      host.querySelectorAll(".struct-del").forEach(function (b) {
        b.onclick = function () { STRUCT.splice(+b.closest(".struct-row").getAttribute("data-i"), 1); structRender([]); };
      });
      host.querySelectorAll(".struct-pace").forEach(function (se) {
        se.onchange = function () { STRUCT[+se.closest(".struct-row").getAttribute("data-i")].pace = se.value; };
      });
      host.querySelectorAll(".struct-one").forEach(function (t) {
        t.onblur = function () { STRUCT[+t.closest(".struct-row").getAttribute("data-i")].one_line = t.value; };
      });
    }
    function structCollect() {
      document.querySelectorAll("#structRows .struct-row").forEach(function (row) {
        var i = +row.getAttribute("data-i");
        if (STRUCT[i]) { STRUCT[i].one_line = row.querySelector(".struct-one").value; STRUCT[i].pace = row.querySelector(".struct-pace").value; }
      });
      return STRUCT.filter(function (r) { return (r.one_line || "").trim(); });
    }
    function structPreview(btn) {
      var rows = structCollect();
      if (!rows.length) {
        var one = (document.getElementById("psOne") || {}).value || "";
        if (!one.trim()) { UI.toast("先写一句话故事", "err"); return; }
        rows = [{ one_line: one }];
      }
      if (btn) { btn.disabled = true; btn.textContent = "⏳ 排结构中…"; }
      window.api.post("/api/structure/preview", { story_id: st.storyId, rows: rows })
        .then(function (j) {
          var d = (j && j.data) || j || {};   /* api.post 已经把 data 拆出来了（P76） */
          STRUCT = (d.rows || []).map(function (r) { return Object.assign({}, r); });
          STRUCT_DESIGN = d.design || null;
          structRender(d.questions || []);
          if (d.online === false) UI.toast("文字模型没起来，现有分话已保留；启动模型后可重新规划", "err");
        })
        .catch(function (e) { UI.toast(e.message, "err"); })
        .then(function () { if (btn) { btn.disabled = false; btn.textContent = btn.id === "structPlan" ? "📐 排结构" : "↻ 重新排"; } });
    }
    var sp1 = document.getElementById("structPlan");
    if (sp1) sp1.onclick = function () { structPreview(sp1); };
    /* 🧭 从一句话推框架（P96）：1 次铺 + ≤1 次修，代码核对的问题列在下面，用户改完再确认 */
    var spf = document.getElementById("structFrame");
    if (spf) spf.onclick = function () {
      var one = (document.getElementById("psOne") || {}).value || "";
      if (!one.trim()) { UI.toast("先写一句话故事", "err"); return; }
      var n = parseInt((document.getElementById("structN") || {}).value || "0", 10) || 0;     // 0 ＝自动（P114）
      spf.disabled = true; spf.textContent = "⏳ 推框架中（约 1 分钟）…";
      window.api.post("/api/structure/framework", { story_id: st.storyId, one_line: one, n: n })
        .then(function (j) {
          var d = (j && j.data) || j || {};
          STRUCT = (d.rows || []).map(function (r) { return Object.assign({}, r); });
          STRUCT_DESIGN = d.design || null;
          var qs = (d.problems || []).map(function (p) { return "框架核对：" + p; }).concat((d.warnings || []).map(function (p) { return "提醒：" + p; }));
          structRender(qs);
          var nb = document.getElementById("structN"); if (nb && d.n) nb.value = d.n;
          UI.toast("推出 " + STRUCT.length + " 话（调用 " + (d.calls || 1) + " 次）" + (qs.length ? "，有 " + qs.length + " 处要你看" : "，核对全过"));
        })
        .catch(function (e) { UI.toast(e.message, "err"); })
        .then(function () { spf.disabled = false; spf.textContent = "🧭 从一句话推框架"; });
    };
    var sp2 = document.getElementById("structRefresh");
    if (sp2) sp2.onclick = function () { structPreview(sp2); };
    var sp3 = document.getElementById("structAdd");
    if (sp3) sp3.onclick = function () { structCollect(); STRUCT.push({ one_line: "", pace: "推进", events: [] }); structRender([]); };
    var sp4 = document.getElementById("structSave");
    if (sp4) sp4.onclick = function () {
      var rows = structCollect();
      if (!rows.length) { UI.toast("至少写一话", "err"); return; }
      window.api.post("/api/structure/save", { story_id: st.storyId, rows: rows, design: STRUCT_DESIGN })
        .then(function () {
          UI.toast("结构已确认：" + rows.length + " 话。生成第 N 话只认第 N 行；上面的一句话保持不动");   // P120：不再用第 1 行覆盖一句话
        })
        .catch(function (e) { UI.toast(e.message, "err"); });
    };
    if (STRUCT.length) structRender([]);

    /* ✨ 智能配套：一句话 → 五维。用户明说过的优先，没说的由套装或常识补。
       只填界面不落库——用户看过、改过，点生成时才随设定一起存。 */
    on("kitDetect", function () {
      var one = ((document.getElementById("psOne") || {}).value || "").trim();
      if (!one) { UI.toast("先写一句话，说你想看什么", "err"); return; }
      var b = document.getElementById("kitDetect");
      var why = document.getElementById("kitWhy");
      if (b) { b.disabled = true; b.textContent = "⏳ 正在读懂你这句话…"; }
      if (why) why.textContent = "";
      window.api.post("/api/kits/detect", { one_line: one })
        .then(function (r) {
          if (b) { b.disabled = false; b.textContent = "✨ 智能配套"; }
          if (!r || !r.world) { UI.toast("没配出来，手动选也行", "err"); return; }
          var setv = function (id, v) {
            var el = document.getElementById(id);
            if (el && v) {
              var hit = [].some.call(el.options, function (o) { return o.value === v; });
              if (hit) el.value = v;
            }
          };
          setv("psWorld", r.world);
          setv("psStyle", r.art);
          setv("psGenre", r.genre);
          setv("psPov", r.pov);
          /* 内容尺度：类型自带的自动勾上（恐怖带黑暗血腥），用户仍可改 */
          var want = r.scale || [];
          [].forEach.call(document.querySelectorAll(".ct"), function (c) {
            if (want.indexOf(c.value) >= 0) c.checked = true;
          });
          var t2 = document.getElementById("psTitle");
          if (t2 && !t2.value.trim() && r.title) t2.value = r.title;
          if (why) {
            why.textContent = (r.kit ? "套装：" + r.kit + "　" : "自由组合　") + (r.why || "");
          }
          UI.toast("配好了，下面每一项都能改");
        })
        .catch(function (e) {
          if (b) { b.disabled = false; b.textContent = "✨ 智能配套"; }
          UI.toast(e.message, "err");
        });
    });
    /* 页面加载/刷新时任务还在跑 → 按钮接上置灰计时（用后端 started_at 算已用秒），
       不然刷新一下按钮就"复活"了，看着像没在干活。 */
    window.api.post("/api/saga/" + st.storyId + "/progress", {})
      .then(function (j) {
        if (!(j && j.running)) return;
        var ts = j.started_at ? j.started_at * 1000 : Date.now();
        if (j.title === "生成文字" || j.title === "生成图片" || j.title === "生成视频") {
          var _bid = { "生成文字": "genText", "生成图片": "genImages", "生成视频": "genAll" }[j.title];
          var _lab = { "生成文字": "① 生成文字", "生成图片": "② 生成图片", "生成视频": "③ 生成视频" }[j.title];
          pollGenDone(beginGenBusy(ts, _bid, _lab, { "生成文字": 600, "生成图片": 1500, "生成视频": 3600 }[j.title]), function (j2) { return makeDoneMsg(j.title, j2); });
        } else if (j.title === "全部生成" || j.title === "按设定重新生成故事") {
          pollGenDone(beginGenBusy(ts));
        } else if (j.title === "生成故事") {
          pollGenDone(beginGenBusy(ts, "genStory",
            "\u2728 生成故事（完整梗概 → 分话安排 → 第一话正文）", 420),
            "完整故事结构和第一话正文已生成；请在故事页阅读和继续下一话");
        } else if (j.title === "生视频") {
          pollGenDone(beginGenBusy(ts, "genVideo", "🎥 生视频", 1500),
            "场景图和前五段视频都生成好了，去 🎬 分镜视频 页看");
        }
      }).catch(function () {});
    /* 🎥 生视频：场景图 → 第1段分镜 → 提示词 → 视频（要求剧本已生成） */
    on("genVideo", function () {
      var busy = beginGenBusy(Date.now(), "genVideo", "🎥 生视频", 1500);
      UI.toast("开始：场景图 → 逐段分镜 → 前五段视频，全程约 20-25 分钟…");
      window.api.post("/api/saga/" + st.storyId + "/gen-first-video", {})
        .then(function () {
          pollGenDone(busy, "场景图和前五段视频都生成好了，去 🎬 分镜视频 页看");
        })
        .catch(function (e) { busy.stop(); UI.toast(e.message, "err"); });
    });

    /* ── 新流水线的三个按钮 ── */
    function aiExpand(kind, taId, btnId) {
      var ta = document.getElementById(taId);
      if (!ta) return;
      var text = (ta.value || "").trim();
      if (!text) { UI.toast("这一栏还是空的，先写点东西再让 AI 扩写"); return; }
      var b = document.getElementById(btnId); if (b) b.disabled = true;
      UI.toast(kind + "扩写中，约 1 分钟…");
      window.api.post("/api/saga/" + st.storyId + "/ai-expand", { kind: kind, text: text })
        .then(function (r) {
          if (b) b.disabled = false;
          var d = (r && r.data) || {};
          if (!d.expanded) { UI.toast("扩写没成功：" + ((r && r.error) || "未知原因"), "err"); return; }
          ta.value = d.expanded;
          ta.dataset.savedValue = d.expanded;   /* 后端已存回，别再触发 onblur 重存 */
          UI.toast(kind + "已扩写");
        })
        .catch(function (e) { if (b) b.disabled = false; UI.toast(e.message, "err"); });
    }
    on("expandBrief", function () { aiExpand("简介", "ep1Brief", "expandBrief"); });

    on("healthBtn", function () { window.UIHealth.show(st.storyId, 1); });
    on("makeScript", function () {
      var b = document.getElementById("makeScript"); if (b) b.disabled = true;
      UI.toast("编剧把原文改成剧本，约 1 分钟…");
      saveEp1Prose(st, true)
        .then(function () { return window.api.post("/api/saga/" + st.storyId + "/make-screenplay", {}); })
        .then(function (r) {
          if (b) b.disabled = false;
          if (r && r.data && r.data.body_len) {
            UI.toast("剧本已生成，进入剧本页");
            window.location.hash = "#story";
          } else {
            UI.toast("剧本没生成：" + ((r && r.error) || "未知原因"), "err");
          }
        })
        .catch(function (e) { if (b) b.disabled = false; UI.toast(e.message, "err"); });
    });

    on("regenStory", function () {
      var b = document.getElementById("regenStory"); if (b) b.disabled = true;
      UI.toast("按人设+简介重写原文中…");
      /* 人物卡和简介都自动先存（2026-08-29 用户定：不用手点保存，
         重写用的就是页面上这份最新的人设+简介） */
      saveAllCharacters()
        .then(function () { return saveEp1Brief(st, true); })
        .then(function () { return window.api.post("/api/saga/" + st.storyId + "/regenerate-story", {}); })
        .then(function () {
          var t = setInterval(function () {
            window.api.post("/api/saga/" + st.storyId + "/progress", {}).then(function (j2) {
              if (!j2.running) {
                clearInterval(t);
                if (b) b.disabled = false;
                if (j2.err) { UI.toast("失败：" + j2.err, "err"); return; }
                UI.toast("已按设定重写第一话原文");
                load(main, st);
              }
            }).catch(function () {});
          }, 2500);
        })
        .catch(function (e) { if (b) b.disabled = false; UI.toast(e.message, "err"); });
    });

    on("lockSet", function () {
      var btn = document.getElementById("lockSet");
      if (btn) btn.disabled = true;
      saveCurrentProject()
        .then(function () { return saveEp1Brief(st, true); })
        .then(function () {
          return window.api.post("/api/project/settings/lock", { story_id: st.storyId, locked: true });
        })
        .then(function () {
          UI.toast("设定已确定，正在进入剧本页");
          window.location.hash = "#story";
        })
        .catch(function (e) {
          if (btn) btn.disabled = false;
          UI.toast(e.message, "err");
        });
    });
    on("unlockSet", function () {
      window.api.post("/api/project/settings/lock", { story_id: st.storyId, locked: false })
        .then(function (j) {
          var a = (j || {}).affected || {};
          var eps = a.episodes || [];
          UI.toast(eps.length
            ? "已解冻。第 " + eps.join("、") + " 话可能和新设定对不上，要不要重写由你决定"
            : "已解冻，可以改设定了");
          return load(main, st);
        })
        .catch(function (e) { alert(e.message); });
    });
    /* 「全部生成」= 从一句话跑到框架和设定就绪（不写正文）。
       中间的圣经/集纲/故事核/骨架/医生全是内部工序，用户不需要知道。
       跑完他在 📖 故事 看到的是：整部框架 + 每话集纲 + 人物卡 + 第一话的设定图。
       正文要等他点「确定设定」之后再单独写——设定没定死之前写正文是浪费。
       已经填过的东西是事实：定了人物就围绕这些人写，定了世界就在那个世界里。 */
    /* 生成中的按钮状态：置灰 + 倒计时（用户定）。总时长先按经验估 9 分钟，
       后端一旦算出真实剩余时间(eta_seconds)就自动校准；倒到 0 还没完显示"快好了"。
       每秒重新 getElementById——页面被重渲染换了按钮也能接管，不再"看着像没反应"。 */
    function beginGenBusy(t0, btnId, label0, estSec) {
      var btn = btnId || "genAll";
      var label = label0 || "🎬 生成第一话（正文 → 人物 → 场景 → 视频 → 成片）";
      var doneAt = t0 + (estSec || 1920) * 1000;
      var tick = setInterval(function () {
        var b = document.getElementById(btn);
        if (!b) return;
        b.disabled = true;
        var left = Math.round((doneAt - Date.now()) / 1000);
        if (left > 0) {
          var m = Math.floor(left / 60), s = left % 60;
          b.textContent = "⏳ 生成中 剩余约 " + m + ":" + (s < 10 ? "0" : "") + s;
        } else {
          b.textContent = "⏳ 生成中 快好了…";
        }
      }, 1000);
      var b0 = document.getElementById(btn);
      if (b0) { b0.disabled = true; b0.textContent = "⏳ 生成中…"; }
      return {
        setEta: function (seconds) {
          if (seconds != null && isFinite(seconds) && seconds > 0) {
            doneAt = Date.now() + seconds * 1000;
          }
        },
        stop: function () {
          clearInterval(tick);
          var b = document.getElementById(btn);
          if (b) { b.disabled = false; b.textContent = label; }
        }
      };
    }
    function pollGenDone(busy, doneMsg) {
      /* 进度百分比交给 shell.js 的全局轮询显示，这里管按钮倒计时校准和收尾 */
      var t = setInterval(function () {
        window.api.post("/api/saga/" + st.storyId + "/progress", {}).then(function (j2) {
          if (j2.running) { busy.setEta(j2.eta_seconds); return; }
          clearInterval(t);
          busy.stop();
          if (j2.err) { UI.toast("失败：" + j2.err, "err"); return; }
          /* 出片前检查判不了的地方：任务停在「需要你确认」，不是完成也不是失败（P248）。
             把问题和候选原样显示；用户去分镜页改好再点「生视频」接着出。 */
          if (j2.blocked && j2.blocked.length) {
            UI.toast("出片停了：" + (j2.step || "看任务日志"), "err");     /* P354：门不再问；这里只可能是自修没跑完 */
            load(main, st);
            return;
          }
          UI.toast(typeof doneMsg === "function" ? doneMsg(j2) : (doneMsg || "简介、原文、人设图都生成好了，往下看、改，满意就「按原文生成剧本」"));
          load(main, st);
        }).catch(function () {});
      }, 2500);
    }
    function makeDoneMsg(title, j2) {
      if (title === "生成文字") return "文字部分好了：去 📖 剧本 页看剧本和分段，改了会自动保存；满意就回来点 ②/③";
      if (title === "生成图片") return "图片部分好了：去 📖 剧本 页看人设图和场景图；满意就回来点 ③";
      return j2 && j2.merged ? ("视频出完并合成了一条：" + j2.merged + "，去 📦 成品 页看")
        : "出片没出完或没合成——已出的段都保留，看任务日志里的 error，修好再点一次 ③ 会接着补";
    }
    /* P463：三个按钮（文字 → 图片 → 视频），后一个自动补前面的；都按剧本页改过的剧本/分段来 */
    function gateMake(stage) {
      var planNow = (plView && plView.plan) || s.story_plan || null;
      var hasPlanNow = !!(planNow && (planNow.episodes || []).length);
      var singleMode = String(s.story_mode || (hasPlanNow ? "split" : "single")).toLowerCase() !== "split";
      if (!singleMode) { gateGen(false); return; }                       /* 旧分话项目还走原来的一键 */
      var one = ((document.getElementById("plIdea") || {}).value || "").trim();
      var epNo = parseInt((document.getElementById("mkEp") || {}).value || "1", 10) || 1;
      var hasProse = !!(((document.getElementById("manualProse") || {}).textContent || "").trim());   /* 设定页正文预览有字＝这一话已有正文 */
      if (!one.trim() && !(hasProse && (document.getElementById("manualProse") || {}).textContent !== "还没有正文。")) { UI.toast("先写这一话想发生的事情", "err"); return; }
      var ids = { text: ["genText", "① 生成文字", 600], images: ["genImages", "② 生成图片", 1500], video: ["genAll", "③ 生成视频", 3600] }[stage];
      var busy = beginGenBusy(Date.now(), ids[0], ids[1], ids[2]);
      var titleOf = { text: "生成文字", images: "生成图片", video: "生成视频" }[stage];
      saveAllCharacters()
        .then(saveCurrentProject)
        .then(function () { return window.api.post("/api/saga/" + st.storyId + "/make", { ep: epNo, stage: stage }); })
        .then(function () {
          pollGenDone(busy, function (j2) {
            var m = makeDoneMsg(titleOf, j2);
            if (stage !== "video") setTimeout(function () { location.hash = "#story?ep=" + epNo; }, 800);
            return m;
          });
        })
        .catch(function (e) { busy.stop(); window.UIActions.progress.stuck("失败"); UI.toast(e.message, "err"); });
    }
    function gateGen(storyOnly) {
      var planNow = (plView && plView.plan) || s.story_plan || null;
      var hasPlanNow = !!(planNow && (planNow.episodes || []).length);
      var singleMode = String(s.story_mode || (hasPlanNow ? "split" : "single")).toLowerCase() !== "split";
      var one = (document.getElementById(singleMode ? "plIdea" : "psOne") || {}).value || "";
      var inputKind = singleMode ? ((((document.getElementById("plIdea") || {}).dataset || {}).kind) || "idea") : "idea";
      if (!singleMode && hasPlanNow) one = (planNow.idea || one);
      if (!one.trim()) { UI.toast(singleMode ? "先写这一话想发生的事情" : (hasPlanNow ? "最上面的故事想法是空的" : "先在最上面写故事想法并生成整部规划，或写一句话故事"), "err"); return; }
      /* 旧分话项目继续保留确认门槛；新单话项目不经过整部规划。 */
      if (!singleMode && hasPlanNow && plView && plView.pending) { UI.toast("整部规划有改动待确认：先在最上面点「确认这次修改」或「不要这次修改」", "err"); return; }
      var epNo = parseInt((document.getElementById("mkEp") || {}).value || "1", 10) || 1;
      if (!singleMode && !hasPlanNow) { UI.toast("先在上面「规划整个故事」并确认，再选一话来做", "err"); return; }
      /* 完整故事和现成剧本是用户的定稿，原样保存，不能再当“想法”扩写一遍。 */
      if (singleMode && inputKind !== "idea") {
        var direct = {no:epNo, create:1,
          shooting_notes:(document.getElementById("plShoot") || {}).value || ""};
        direct[inputKind === "story" ? "prose" : "body"] = one.trim();
        var bDirect = document.getElementById(storyOnly ? "genStory" : "genAll");
        if (bDirect) bDirect.disabled = true;
        saveAllCharacters().then(saveCurrentProject)
          .then(function () { return window.api.post("/api/saga/" + st.storyId + "/save-episode", direct); })
          .then(function () {
            if (inputKind === "story" && !storyOnly) {
              UI.toast("完整故事已保存，正在按原文生成剧本…");
              return window.api.post("/api/saga/" + st.storyId + "/make-screenplay", {ep:epNo});
            }
          })
          .then(function () {
            if (bDirect) bDirect.disabled = false;
            if (inputKind === "script") {
              UI.toast("现成剧本已原样保存，进入分镜视频页");
              window.location.hash = "#timeline?ep=" + epNo;
            } else if (storyOnly) {
              UI.toast("完整故事已原样保存为本话正文");
              window.location.hash = "#story?ep=" + epNo;
            } else {
              UI.toast("正文和剧本已准备好，先通读再制作视频");
              window.location.hash = "#story?ep=" + epNo;
            }
          })
          .catch(function (e) { if (bDirect) bDirect.disabled = false; UI.toast(e.message, "err"); });
        return;
      }
      var busy = storyOnly
        ? beginGenBusy(Date.now(), "genStory", "📝 生成第 " + epNo + " 话全部内容（不出视频）", 1500)
        : beginGenBusy(Date.now());
      // 先把界面上填/选的人物卡存进去，再保存项目设定，让生成用的就是页面上这份事实。
      saveAllCharacters()
        .then(saveCurrentProject)
        .then(function () {
          if (storyOnly) return window.api.post("/api/saga/" + st.storyId + "/gen-story",
            { ep: epNo, one_line: one.trim() });
          /* “认可正文并制作”本身就是确认动作，不再让用户先点一个单独的确认按钮。 */
          return window.api.post("/api/saga/" + st.storyId + "/auto-all",
            { ep: epNo, one_line: one.trim(), auto: 1,
              text_only: (document.getElementById("genTextOnly") || {}).checked ? 1 : 0 });   /* P398：一键到底；P412：可只写到提示词 */
        })
        .then(function () {
          pollGenDone(busy, storyOnly
            ? ("第 " + epNo + " 话正文已生成（未出图、未出视频）；先去 📖 剧本 页阅读，满意后再制作")
            : function (j2) {
              return j2.merged ? ("第 " + epNo + " 话全部视频出完并合成了一条：" + j2.merged + "，去 🎬 分镜视频 页看")
                : ("第 " + epNo + " 话出片没出完或没合成——已出的段都保留，看任务日志里的 error，修好再点一次会接着补");
            });
        })
        .catch(function (e) {
          busy.stop();
          window.UIActions.progress.stuck("失败");
          UI.toast(e.message, "err");
        });
    }
    /* 补出人设图：只给缺图的人物出（全部生成时偶发失败后用它补，不用重跑整个故事） */
    on("fixCharImgs", function () {
      var b = document.getElementById("fixCharImgs");
      if (b) b.disabled = true;
      UI.toast("开始补出缺的人设图，每张约 2-4 分钟…");
      saveAllCharacters()
        .then(function () {
          return window.api.post("/api/saga/" + st.storyId + "/gen-char-images", {});
        })
        .then(function () { pollGenDone(beginGenBusy(Date.now())); })
        .catch(function (e) { if (b) b.disabled = false; UI.toast(e.message, "err"); });
    });
    document.getElementById("addChar").onclick = function () {
      window.api.post("/api/story/" + st.storyId + "/characters/characters", { name: "新人物" })
        .then(function () { return load(main, st); }).catch(function (e) { alert(e.message); });
    };
    /* 【提示词面板】只保留“按这份提示词出图”：无论文本是否修改，每点一次都保存当前文本并生成新图。 */
    document.querySelectorAll("[data-prompt]").forEach(function (b) {
      b.onclick = function () {
        var oid = b.getAttribute("data-prompt");
        var box = document.getElementById("pbox_" + oid);
        if (!box) return;
        if (!box.hidden) { box.hidden = true; return; }
        box.hidden = false;
        var pkind = b.getAttribute("data-pkind") || "character_master";
        box.innerHTML = '<div class="sub">' + (pkind === "character_master"
          ? '读取中…尚无提示词时由本地千问整理文字，此处只写词、不出图。' : '读取中…') + '</div>';
        window.api.post("/api/asset/prompt",
                        { story_id: st.storyId, owner_id: oid, kind: pkind,
                          generate: pkind === "character_master" })
          .then(function (j) {
            /* api.post 已经把 {ok,data} 拆成 data 了，这里不要再拆一层 */
            var d0 = j || {};
            box.innerHTML =
              '<label style="display:block;font-size:14px;color:#5a5348;margin:8px 0 4px">' +
              '出图提示词' + (d0.edited ? '（已改过）' : '') + '</label>' +
              '<textarea id="pt_' + esc(oid) + '" style="width:100%;height:220px;font-size:13px;' +
              'line-height:1.7;padding:10px 12px">' + esc(d0.prompt || "") + '</textarea>' +
              '<div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">' +
              '<button class="btn primary small" data-pgen="' + esc(oid) + '">🎨 按这份提示词出图</button></div>';
            bindPromptBox(oid, box, pkind);
          })
          .catch(function (e) { box.innerHTML = '<div class="sub">读取失败：' + esc(e.message) + '</div>'; });
      };
    });
    /* kind 从调用方传进来：人物是 character_master，场景是 scene_master。
       原来三处 API 调用都写死了 character_master，场景卡点「提示词」会去
       人物表里找这张卡，必然 404——所以设定页从来只能改人物提示词。 */
    function bindPromptBox(oid, box, kind) {
      kind = kind || "character_master";
      var ta = document.getElementById("pt_" + oid);
      var q = function (sel) { return box.querySelector(sel); };
      var save = function () {
        return window.api.post("/api/asset/prompt/save",
          { story_id: st.storyId, owner_id: oid, kind: kind, prompt: ta.value });
      };
      q("[data-pgen]").onclick = function (ev) {
        var btn = ev.target; btn.disabled = true; btn.textContent = "出图中…";
        save().then(function () {
          return window.api.post("/api/produce", { story_id: st.storyId,
            kind: kind, owner_id: oid, prompt: ta.value });
        }).then(function () {
          var t = setInterval(function () {
            window.api.post("/api/produce/progress", { story_id: st.storyId, owner_id: oid })
              .then(function (j) {
                if (j && !j.running) {
                  clearInterval(t); btn.disabled = false; btn.textContent = "🎨 按这份提示词出图";
                  if (j.err) { UI.toast("失败：" + j.err, "err"); return; }
                  UI.toast("出图完成"); load(main, st);
                }
              }).catch(function () {});
          }, 3000);
        }).catch(function (e) {
          btn.disabled = false; btn.textContent = "🎨 按这份提示词出图";
          UI.toast(e.message, "err");
        });
      };
    }

    /* ---------- 阶段一新增块的接线：基调图 / 场景卡 / 场次表 ---------- */
    wireStageOne(main, st);

    function wireStageOne(main, st) {
      /* --- 场次表 --- */
      var slGen = document.getElementById("slGen");
      if (slGen) slGen.onclick = function () {
        var label = slGen.textContent;
        slGen.disabled = true; slGen.textContent = "生成中…（约 2 分钟）";
        var note = document.getElementById("slNote");
        window.api.post("/api/saga/" + st.storyId + "/build-shotlist", { no: 1 })
          .then(function () {
            var t = setInterval(function () {
              window.api.post("/api/saga/" + st.storyId + "/progress", {}).then(function (j) {
                if (note && j && j.step) note.textContent = j.step;
                if (j && !j.running) {
                  clearInterval(t);
                  slGen.disabled = false; slGen.textContent = label;
                  if (j.err) { UI.toast("场次表失败：" + j.err, "err"); return; }
                  if (note) note.textContent = j.note || "";
                  UI.toast("场次表好了");
                  load(main, st);
                }
              }).catch(function () {});
            }, 3000);
          })
          .catch(function (e) {
            slGen.disabled = false; slGen.textContent = label;
            UI.toast(e.message, "err");
          });
      };

      /* --- 删场景 --- */
      document.querySelectorAll("[data-dels]").forEach(function (b) {
        b.onclick = function () {
          var id = b.getAttribute("data-dels");
          if (!id || !confirm("删除这个场景？文件移到回收区可恢复。")) return;
          window.api.post("/api/story/" + st.storyId + "/scenes/" +
                          encodeURIComponent(id) + "/delete", {})
            .then(function () { return load(main, st); })
            .catch(function (e) { alert(e.message); });
        };
      });
    }

    document.querySelectorAll("[data-delc]").forEach(function (b) {
      b.onclick = function () {
        var cid = b.getAttribute("data-delc");
        if (!cid || !confirm("删除人物？文件移到回收区可恢复。")) return;
        window.api.post("/api/story/" + st.storyId + "/characters/" + encodeURIComponent(cid) + "/delete", {})
          .then(function () { return load(main, st); }).catch(function (e) { alert(e.message); });
      };
    });
    /* 性别一改，长相、身材和发型的选项跟着换（男女是不同的列表）。 */
    (D.characters || []).forEach(function (c, i) {
      var sx = document.getElementById("cSex" + i);
      if (!sx) return;
      sx.onchange = function () {
        var f = fieldsFor(sx.value);
        [["cLook" + i, f["长相"]], ["cBuild" + i, f["身材"]], ["cHairPreset" + i, f["发型"]]].forEach(function (pair) {
          var e = document.getElementById(pair[0]);
          if (!e) return;
          var keep = e.value;
          var concrete = concreteCharacterOptions(pair[1]);
          e.innerHTML = concrete.map(function (o) {
            return '<option' + (o === keep ? " selected" : "") + ">" + o + "</option>";
          }).join("");
        });
      };
      var hairPreset = document.getElementById("cHairPreset" + i);
      if (hairPreset) hairPreset.onchange = function () {
        var table = ((D.option_defs || {}).hair || {})[sx.value === "男" ? "男" : "女"] || {};
        var target = document.getElementById("cHair" + i);
        if (target && table[hairPreset.value]) target.value = table[hairPreset.value];
      };
      var outfitPreset = document.getElementById("cOutfit" + i);
      if (outfitPreset) outfitPreset.onchange = function () {
        var world = (document.getElementById("psWorld") || {}).value || (D.settings || {}).world_type;
        var text = ((((D.outfit_preset_defs_by_world || {})[world] || {})[outfitPreset.value]) ||
                    ((D.outfit_preset_defs || {})[outfitPreset.value]) || "");
        var target = document.getElementById("cDetails" + i);
        if (target && text) {
          target.value = /^服装描述：.*$/m.test(target.value)
            ? target.value.replace(/^服装描述：.*$/m, "服装描述：" + text)
            : target.value + "\n服装描述：" + text;
        }
      };
    });
    var worldSelect = document.getElementById("psWorld");
    if (worldSelect) {
      var previousWorldChange = worldSelect.onchange;
      worldSelect.onchange = function () {
        if (previousWorldChange) previousWorldChange.call(this);
        var opts = concreteCharacterOptions(outfitOptionsFor(this.value));
        (D.characters || []).forEach(function (c, i) {
          var e = document.getElementById("cOutfit" + i);
          if (!e) return;
          var keep = e.value;
          e.innerHTML = opts.map(function (o) {
            return '<option' + (o === keep ? ' selected' : '') + '>' + esc(o) + '</option>';
          }).join('');
          if (opts.indexOf(keep) < 0) e.value = opts.indexOf("自定义") >= 0 ? "自定义" : (opts[0] || "");
        });
      };
    }

    document.querySelectorAll("[data-dels]").forEach(function (b) {
      b.onclick = function () {
        var scid = b.getAttribute("data-dels");
        if (!scid || !confirm("删除场景？文件移到回收区可恢复。")) return;
        window.api.post("/api/story/" + st.storyId + "/scenes/" + encodeURIComponent(scid) + "/scenes/delete", {})
          .then(function () { return load(main, st); }).catch(function (e) { alert(e.message); });
      };
    });
    document.querySelectorAll("[data-save]").forEach(function (b) {
      b.onclick = function () {
        window.api.post("/api/asset/adopt", { story_id: st.storyId, asset_id: b.getAttribute("data-save") })
          .then(function () {
            alert("已保存到 🗂 资源");
            return load(main, st);
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
