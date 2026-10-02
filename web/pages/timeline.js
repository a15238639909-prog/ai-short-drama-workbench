/* pages/timeline.js — 🎬 剧本分镜（方案 A：暖白三栏 + 预览位）

   用户定的工作方式：**这个平台是用来检查和修改的**——
   做一次设定，剩下的 Qwen 全部生成完，人来逐个检查，不合格的单独重做。

   所以不是四个手点的步骤，而是：
     一个「全部生成」按钮跑完链  →  逐段检查  →  哪一级不行就重做哪一级

   布局：
     工具条            场次 · 全部生成 · 统计 · 缩放
     大时间轴          单位是**段**（≤15秒），宽度=时长，不是单个分镜
     ├ 左  提示词      这一段完整的六段式提示词，长文可滚
     ├ 中  预览 + 参考 上面 16:9 看片，下面这一段带哪些参考图
     └ 右  分镜        这一段里的镜头，每个能单独重做
*/
window.Pages = window.Pages || {};

(function () {
  var esc = function (s) { return window.UI.esc(s); };
  var D = { timeline: { scenes: [] }, characters: [], scenes: [] };
  var ST = null;        // shell 传进来的 state（它不挂 window）
  var cur = 1;          // 当前场
  var seg = 0;          // 当前段
  var EP = 1;           // 当前第几话（第一公民，从 hash 读）
  var genN = 5;         // 「生成下面 N 段」选的 N，默认 5（用户定：一次给够五段）
  /* P312：提示词保存状态。lastSave = {ok, at, err}；promptDirty = 输入框改了还没存 */
  var promptDirty = false, lastSave = { ok: true, at: 0, err: "" }, savingNow = null;
  function id(x) { return document.getElementById(x); }
  function fmtClock(ts) { var d = new Date(ts); function p(n) { return (n < 10 ? "0" : "") + n; } return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds()); }
  function savePrompt(sc, segIdx, pt, ps) {
    var g = segsOf(sc)[segIdx];
    if (!g || g.no == null) return Promise.resolve(true);
    if (pt.value === (g.prompt || "")) { promptDirty = false; return Promise.resolve(true); }
    var val = pt.value;
    var box = function () { return id("pSave") || ps; };      /* 页面可能已重画，状态框每次现取 */
    if (box()) box().innerHTML = '<span class="note-gray" style="margin:0">保存中…</span>';
    savingNow = window.api.post("/api/timeline/" + ((ST || {}).storyId || "") + "/segment-save", { no: g.no, prompt: val, ep: EP }).then(function (r) {
      g.prompt = val; g.prompt_edited = true; promptDirty = false;
      lastSave = { ok: true, at: Date.now(), err: "" };
      if (box()) box().innerHTML = '<span class="stat-green">已保存 ' + fmtClock(lastSave.at) + '（你改过这一段，剧本重写时不会覆盖）</span>';
      return true;
    }).catch(function (e) {
      lastSave = { ok: false, at: Date.now(), err: e.message || "保存失败" };
      if (box()) box().innerHTML = '<span style="color:var(--red)">保存失败：' + esc(lastSave.err) + ' <button class="btn small" id="pRetry">重试</button></span>';
      var rb = id("pRetry"); if (rb) rb.onclick = function () { savePrompt(sc, segIdx, pt, ps); };
      return false;
    }).then(function (okv) { savingNow = null; return okv; });
    return savingNow;
  }
  /* 生成前：把还没存的改动存好；存失败就拦下。返回 Promise<bool>；失败时 reject 让调用方停 */
  function flushPromptSave() {
    var pt = id("pText"), ps = id("pSave");
    var p = savingNow || Promise.resolve(true);
    return p.then(function () {
      if (pt && promptDirty) { var sc = scene(); if (sc) return savePrompt(sc, seg, pt, ps); }
      return lastSave.ok;
    }).then(function (okv) {
      if (!okv || !lastSave.ok) { window.UI.toast("有一段提示词没保存成功，先点「重试」保存，再生成", "err"); throw new Error("提示词没保存成功：" + (lastSave.err || "")); }
      return true;
    });
  }
  function flushPromptSaveSync() {
    if (promptDirty || !lastSave.ok) { window.UI.toast("这一段提示词还没保存成功，先点到输入框外让它保存（或点重试）", "err"); return false; }
    return true;
  }

  var _gen1Fired = false;  // #timeline?gen1=1 只自动出一次第一段
  function readEp() {
    var m = (location.hash || "").match(/[?&]ep=(\d+)/);
    if (m) EP = parseInt(m[1], 10) || 1;
    return EP;
  }
  var PX = 50;          // 默认 800px 宽；16:9 大卡一屏约两张
  var poll = null;      // 进度轮询
  var tlX = 0;          // 时间轴横向滚动位置——整页重绘后要还原，
                        // 不然拖到右边点第10段，一重绘就弹回第1段
  /* 出片前预览（第二次确认）用的状态 */
  var MAIN = null;      // 页面根节点：预览区的确认按钮要靠它起轮询
  var PV = null;        // /preview 的返回；null = 还没读到
  var pvOpen = false;   // 检查明细展开没有
  var pvSeq = 0;        // 预览请求序号：切话/连点时只认最后一次的返回
  var busy = false;     // 有任务在跑：确认按钮要灰掉
  var curBlocked = [];  // 后端停下来问的问题（j.blocked），候选按钮从这里取

  function num(v, d) { var x = parseFloat(v); return x > 0 ? x : d; }
  function scene() {
    return (D.timeline.scenes || []).filter(function (s) {
      return parseInt(s.no, 10) === cur;
    })[0];
  }
  function segsOf(sc) { return (sc || {}).segments || []; }
  function segCountAll() {
    return (D.timeline.scenes || []).reduce(function (a, s) {
      return a + ((s.segments || []).length);
    }, 0);
  }
  function lastSegNo() {
    var mx = 0;
    (D.timeline.scenes || []).forEach(function (s) {
      (s.segments || []).forEach(function (g) { mx = Math.max(mx, +(g.no || 0)); });
    });
    return mx;
  }
  function shotsIn(sc, g) {
    var ids = (g || {}).shot_ids || [];
    return (sc.shots || []).filter(function (s) { return ids.indexOf(s.shot_id) >= 0; });
  }
  function fmt(t) {
    var m = Math.floor(t / 60), s = Math.floor(t % 60);
    return (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
  }

  /* 文件地址挂版本号（P241）：单段重做会写回同一个文件名，
     不挂版本号的话浏览器会拿缓存里的旧片，用户以为重做没生效；
     顺带绕开加 Range 支持之前缓存下来的那份旧响应（那份拖不动进度）。 */
  function fileUrl(p, tag) {
    var s = "/files/" + String(p || "").replace(/\\/g, "/");
    var v = String(tag == null ? "" : tag).replace(/[^0-9a-zA-Z_.-]/g, "");
    return v ? (s + (s.indexOf("?") >= 0 ? "&" : "?") + "v=" + v) : s;
  }
  function segVer(g) {
    g = g || {};
    return String(g.updated_at || g.seed || "") + "_" +
           String(g.seconds || "") + "_" + String((g.size || "")).replace("x", "");
  }

  /* 最终送给 H3 的提示词镜头才是实际成片结构。上游 S01/S02 只是素材来源，
     不能再拿它的数量冒充最终镜头数。 */
  function promptShots(prompt) {
    var text = String(prompt || "");
    var out = [], m;
    /* 老格式：镜头1（1.0–3.0秒，中景）：… */
    var re = /镜头(\d+)[（(]([\d.]+)\s*[–—-]\s*([\d.]+)秒\s*[，,]\s*([^）)]+)[）)]\s*[：:]?\s*([\s\S]*?)(?=\n镜头\d+[（(]|\n\n【|$)/g;
    while ((m = re.exec(text))) {
      out.push({ no: +m[1], start: +m[2], end: +m[3], framing: m[4].trim(), body: m[5].trim() });
    }
    if (out.length) return out;
    /* 当前格式：时间块「0—4秒：…」，一块就是一个镜头（P238）。
       老格式解析不出来才走这里，两种都支持。 */
    var re2 = /(?:^|\n)[ \t]*([\d.]+)[ \t]*[–—~-][ \t]*([\d.]+)[ \t]*秒[ \t]*[：:][ \t]*([\s\S]*?)(?=\n[ \t]*[\d.]+[ \t]*[–—~-][ \t]*[\d.]+[ \t]*秒[ \t]*[：:]|\n[ \t]*这一段结束时|\n[ \t]*【|$)/g;
    var i = 0;
    while ((m = re2.exec(text))) {
      i += 1;
      var b = String(m[3] || "").trim();
      var head = b.split("\n")[0] || "";
      var fm = head.match(/^([^，。；]{2,14})[，。；]/);
      out.push({ no: i, start: +m[1], end: +m[2],
                 framing: fm ? fm[1].trim() : "", body: b });
    }
    return out;
  }
  function promptShotHtml(prompt, fallback) {
    var shots = promptShots(prompt);
    if (!shots.length) {
      /* 有提示词却解析不出镜头，不能说"还没有编"——那是假报警（P238） */
      if (String(prompt || "").trim()) {
        return '<div class="prompt-shots empty"><b>提示词镜头</b>' +
          '<span>提示词已生成，镜头列表未识别（不影响出片）</span></div>';
      }
      return '<div class="prompt-shots empty"><b>提示词镜头</b><span>还没有编最终提示词；当前仅有 ' +
        (fallback || 0) + ' 个上游素材镜头</span></div>';
    }
    return '<section class="prompt-shots"><div class="prompt-shots-head"><b>最终提示词镜头</b>' +
      '<span>视频会按下面 ' + shots.length + ' 镜生成</span></div><div class="prompt-shot-grid">' +
      shots.map(function (s) {
        var steps = s.body.split(/\s*→\s*/).filter(Boolean);
        return '<article class="prompt-shot"><header><strong>镜头 ' + s.no + '</strong>' +
          '<em>' + s.start + '–' + s.end + ' 秒</em><span>' + esc(s.framing) + '</span></header>' +
          '<div>' + steps.map(function (x, i) { return '<p><i>' + (i + 1) + '</i>' + esc(x) + '</p>'; }).join("") +
          '</div></article>';
      }).join("") + '</div></section>';
  }

  /* ---------- 工具条 ---------- */
  function bar(sc) {
    var list = D.timeline.scenes || [];
    var gs = segsOf(sc);
    var total = gs.reduce(function (a, g) { return a + num(g.seconds, 0); }, 0);
    var finalShotCount = gs.reduce(function (a, g) {
      return a + (promptShots(g.prompt).length || (g.shot_ids || []).length);
    }, 0);
    return '<div class="tbar"><div class="tbar-main">' +
      '<div class="tbar-title"><span class="tbar-t">分镜视频</span>' +
      /* 话次下拉（用户定）：点第几话出第几话，各话分镜互不牵扯 */
      '<select id="epPick" title="切换话次" style="height:34px;padding:0 8px;border-radius:8px;' +
      'border:1px solid #e3ddd2;font-size:14px;max-width:230px">' +
      ((D.episodes && D.episodes.length ? D.episodes : [{ no: EP, title: "", segments: 0 }])
        .map(function (x) {
          return '<option value="' + x.no + '"' + (+x.no === +EP ? " selected" : "") + '>第 ' + x.no + ' 话' +
            (x.title ? "《" + esc(x.title) + "》" : "") +
            (x.segments ? "（" + x.segments + " 段）" : (x.has_body ? "（有剧本）" : "（空）")) +
            "</option>";
        }).join("")) + "</select>" +
      (D.ep_locked ? '<span class="epbadge">🔒</span>' : "") + "</div>" +
      /* P353：不再提示"改过了"——剧本改了程序自动从改动处重写，不用用户管 */
      (sc ? '<span class="tbar-i"><b>' + esc(sc.location || "") + "</b>　" +
            total.toFixed(0) + " 秒 · " + gs.length + " 段 · " +
            finalShotCount + " 个提示词镜头 · " + (sc.beats || []).length + " 拍</span>" : "") +
      '</div><div class="tbar-actions">' +
      (list.length ? '<span class="tabs">' + list.map(function (x) {
        return '<button class="tab' + (parseInt(x.no, 10) === cur ? " on" : "") +
          '" data-no="' + x.no + '">场景' + x.no + ' · ' + esc(x.location || x.title || "未命名") + "</button>";
      }).join("") + "</span>" : "") +
      '<span class="tbar-work">' +
      /* P320：「① 生成第一段 / ⏭ 生成下一段」（旧导演链 direct-next）删掉——和「🎬 生成（提示词＋视频）」是同一件事，选 1 段就是它 */
      /* 「全部提示词」+「全部视频」合成一个（用户 2026-09-02 定）：
         选几段，接着已出视频的往后写这几段的提示词，写完连着出这几段的视频。 */
      '<span class="gen-n" style="display:inline-flex;align-items:center;gap:6px">' +
      '<select id="genN" title="往后生成几段" style="height:34px;padding:0 8px;border-radius:8px;' +
      'border:1px solid #e3ddd2;font-size:14px">' +
      [1,2,3,4,5,6,7,8,9,10].map(function (k) {
        return '<option value="' + k + '"' + (k === genN ? " selected" : "") + '>下面 ' + k + ' 段</option>';
      }).join("") + '</select>' +
      /* P323⑤：体检 / 语音核对 / 重渲不合格段 三个按钮删了（用户：都不要了；自动语音核对也关了） */
      '<button class="btn primary" id="bGenN" title="从第一个没视频的段起往后出这几段（提示词已有就直接出，没有先写）">' +
      '🎬 生成</button></span>' +
      '</span>' +
      '<span class="tbar-z"><button class="btn small" id="zOut">－</button>' +
      '<button class="btn small" id="zIn">＋</button></span></div></div>';
  }

  /* ---------- 大时间轴：单位是段 ----------
     用户定的样式：段块统一 16:9 大卡片，不按秒数拉伸宽度；太多就往右拖。 */
  function timeline(sc) {
    var gs = segsOf(sc);
    if (!gs.length) {
      return '<div class="tl-empty">还没有内容。点上面的「🎬 生成」</div>';
    }
    var w = PX * 16;                      // 缩放按钮调 PX，卡片跟着变
    var clips = gs.map(function (g, i) {
      var finalShots = promptShots(g.prompt).length || (g.shot_ids || []).length;
      return '<div class="clip' + (i === seg ? " on" : "") + '" data-i="' + i +
        '" style="width:' + w + 'px">' +
        '<div class="clip-th">' + (g.video
          ? '<video src="' + esc(fileUrl(g.video, segVer(g))) +
            '" controls preload="metadata"></video>'
          : '<span>' + (i ? "首帧接续" : "起场") + "</span>") + "</div>" +
        '<div class="clip-m"><b>第' + g.no + "段</b><em>" + num(g.seconds, 0) + "s</em>" +
        '<span>提示词 ' + finalShots + " 镜 · " + esc(g.location || "") + "</span></div>" +
        '<div class="clip-act">' + (g.video
          ? '<button class="btn" data-card-gen="' + i + '">重新生成</button>' +
            '<button class="btn ' + (g.adopted ? 'success' : 'primary') + '" data-card-adopt="' + i + '"' +
            (g.adopted ? ' disabled' : '') + '>' + (g.adopted ? '✓ 已采用' : '采用') + '</button>'
          : '<button class="btn primary" data-card-gen="' + i + '">生成这一段</button>') +
        // 只有末段能删（321，不能删中间、不能跳）
        (+g.no === lastSegNo()
          ? '<button class="btn del small" data-seg-del="' + g.no + '" title="只能从最后一段往回删">✕ 删末段</button>'
          : "") +
        '</div></div>';
    }).join("");
    /* P316：开场段（场景板全景 + 人物亮相）画在第 1 段前面；不占段号 */
    var ops = (D.timeline.opening || []).map(function (g) {
      var kind = g.kind === "scene" ? "场景" : "人物";
      return '<div class="clip open" data-open="' + g.k + '" style="width:' + w + 'px;border-style:dashed">' +
        '<div class="clip-th">' + (g.video
          ? '<video src="' + esc(fileUrl(g.video, segVer(g))) + '" controls preload="metadata"></video>'
          : '<span>开场·' + kind + '</span>') + "</div>" +
        '<div class="clip-m"><b>开场' + g.k + " · " + kind + "</b><em>" + num(g.seconds, 0) + "s</em>" +
        '<span>' + esc(g.name || "") + "</span></div>" +
        '<div class="clip-act">' +
        '<button class="btn' + (g.video ? "" : " primary") + '" data-open-gen="' + g.k + '">' + (g.video ? "重出" : "出这段") + '</button>' +
        '</div></div>';
    }).join("");
    return '<div class="tl"><div class="tl-scroll" id="tlScroll">' +
      '<div class="tl-row">' + ops + clips + "</div></div></div>";
  }

  /* ---------- 三栏详情 ---------- */
  function panel(sc) {
    var gs = segsOf(sc);
    var g = gs[seg];
    if (!g) return "";
    var list = shotsIn(sc, g);
    var refs = g.refs || [];
    /* 参考图规则（用户定的）：
       · 只显示**这一段相关**的候选：本段镜头里出现的人物 + 本段地点的场景图。
         不相关的（别的场景、不在场的人）不显示——显示了就是引导人勾错。
       · 没勾过时**默认全选**——每一段视频都必须有参考图，默认就是要用的那套。 */
    /* 出场名单和后端同一规则：分镜的 chars 字段（导演写明的"画面里的人"）是权威。
       拿全部文字匹配会把画外人拉进来——起始状态写"骑车经过李哲"，
       他的参考卡就冒出来了，可这段画面里根本没有他。 */
    var onset = {};
    var hasCharsField = false;
    list.forEach(function (s) {
      if (Object.prototype.toString.call(s.chars) === "[object Array]") {
        hasCharsField = true;
        s.chars.forEach(function (n) { onset[n] = 1; });
      }
    });
    if (!hasCharsField) {
      var blob = list.map(function (s) {
        return ["subject", "action", "foreground", "midground", "background"].map(function (k) {
          return String(s[k] || "");
        }).join(" ");
      }).join(" ");
      (D.characters || []).forEach(function (c) {
        if (c.name && blob.indexOf(c.name) >= 0) onset[c.name] = 1;
      });
    }
    /* 导演段（一段一个连续镜头）没有 shot 结构、也没有 location 字段——人物/场景
       都写在段落文字(g.prompt)里。这时扫段文字里出现的人名/场景名来匹配参考图。
       归一化掉引号/空格，好让『"断刃"酒馆』这种带引号的卡名也能对上段里的『断刃酒馆』。 */
    var isDirector = (list.length === 0);
    var norm = function (x) { return String(x || "").replace(/[「」“”"'‘’·、，,。\s]/g, ""); };
    var promptN = norm(g.prompt);
    var myChars = (D.characters || []).filter(function (c) {
      if (onset[c.name]) return true;
      return isDirector && c.name && promptN.indexOf(norm(c.name)) >= 0;
    });
    var myScenes = (D.scenes || []).filter(function (s) {
      var loc = String(g.location || "");
      if (s.name && loc && (s.name === loc || loc.indexOf(s.name) >= 0 || s.name.indexOf(loc) >= 0)) return true;
      if (!(isDirector && s.name)) return false;
      var core = norm(s.name);
      // 段里可能只写地点前半段（『断刃酒馆角落』对『"断刃"酒馆角落座位』）——用 5 字前缀兜底
      return promptN.indexOf(core) >= 0 || (core.length >= 5 && promptN.indexOf(core.slice(0, 5)) >= 0);
    });
    /* 用户手动删掉的自动匹配项（g.ref_hide）不再显示。 */
    var _hide = g.ref_hide || [];
    if (_hide.length) {
      myChars = myChars.filter(function (c) { return _hide.indexOf(c.name) < 0; });
      myScenes = myScenes.filter(function (s) { return _hide.indexOf(s.name) < 0; });
    }
    var hasSaved = refs.length > 0;
    var chip = function (kind, name, icon) {
      var saved = refs.filter(function (r) { return r.n === name; })[0];
      var on = hasSaved ? !!saved : true;     /* 没勾过 = 默认全选 */
      /* 人设图是身份锚点，只显示资源页的已采用版，不再允许段级切换候选。 */
      var src = (D.ref_images || {})[name];
      var tag = kind === "character"
        ? '<span class="rtag rtag-c">👤 人物</span>'
        : '<span class="rtag rtag-s">🏛 场景</span>';
      return '<div class="refcard-wrap" style="position:relative;display:inline-block">' +
        '<span class="refdel" data-ref-del="' + esc(name) + '" title="从这一段删除这张参考图" ' +
        'style="position:absolute;top:3px;right:3px;z-index:3;cursor:pointer;background:#c0392b;color:#fff;' +
        'border-radius:50%;width:18px;height:18px;line-height:18px;text-align:center;font-size:12px">✕</span>' +
        '<label class="refcard' + (on ? " on" : "") + '">' +
        '<input type="checkbox" data-ref="' + esc(name) + '" data-kind="' + kind + '"' +
        (on ? " checked" : "") + ">" + tag +
        '<span class="refthumb">' + (src
          ? '<img src="/files/' + esc(String(src).replace(/\\/g, "/")) + '" alt="">'
          : '<em>没有设定图</em>') + "</span>" +
        '<span class="refname">' + esc(name) + "</span></label></div>";
    };
    /* 手动加进来的参考图（在 g.refs 里、但不是自动匹配到的那些）也要显示成 chip。 */
    var matchedNames = {};
    myChars.forEach(function (c) { matchedNames[c.name] = 1; });
    myScenes.forEach(function (s) { matchedNames[s.name] = 1; });
    var sceneNameSet = {}; (D.scenes || []).forEach(function (s) { if (s.name) sceneNameSet[s.name] = 1; });
    var extras = (refs || []).filter(function (r) { return r.n && !matchedNames[r.n]; });
    var shownNames = {}; Object.keys(matchedNames).forEach(function (n) { shownNames[n] = 1; });
    extras.forEach(function (r) { shownNames[r.n] = 1; });
    /* ＋从资源添加：列出资源栏里已采用、且这一段还没有的人设/场景图。 */
    var addable = [];
    (D.characters || []).forEach(function (c) {
      if (c.name && (D.ref_images || {})[c.name] && !shownNames[c.name]) addable.push({ k: "character", n: c.name });
    });
    (D.scenes || []).forEach(function (s) {
      if (s.name && (D.ref_images || {})[s.name] && !shownNames[s.name]) addable.push({ k: "scene", n: s.name });
    });
    var addSelect = '<select id="refAdd" style="margin-top:8px;max-width:100%">' +
      '<option value="">＋ 从资源添加参考图…</option>' +
      addable.map(function (a) {
        return '<option value="' + a.k + '|' + esc(a.n) + '">' +
          (a.k === "character" ? "👤 " : "🏛 ") + esc(a.n) + "</option>";
      }).join("") + "</select>";
    var extrasHtml = extras.map(function (r) {
      return chip(r.k || (sceneNameSet[r.n] ? "scene" : "character"), r.n, "");
    }).join("");

    var refCount = hasSaved ? refs.length : (myChars.length + myScenes.length);

    var finalShots = promptShots(g.prompt);

    return '<div class="cols">' +

      /* 左：分镜信息 + 可编辑提示词 */
      '<div class="col col-p">' +
      '<div class="col-h"><b>第 ' + g.no + " 段</b>" +
      '<span>' + num(g.seconds, 0) + " 秒 · 提示词 " + (finalShots.length || list.length) + " 镜 · " +
      esc(g.location || "") + "</span>" +
      '<span class="sp"></span>' +
      '<button class="btn small primary" id="bDirect" title="口述框留空＝按这一拍自动写">按口述写这一段的提示词</button></div>' +
      '<div class="sub" style="margin:6px 0 2px">这一拍：' + esc(String(g.source_text || "").replace(/\n/g, " / ")) + '</div>' +
      '<textarea id="pDirect" spellcheck="false" style="width:100%;min-height:64px;padding:8px 10px;font-size:13px;line-height:1.7;border:1px solid #e6e0d6;border-radius:8px;margin-bottom:6px" ' +
      'placeholder="口述这一段怎么拍：景别（全景 / 中景 / 特写）、角度（仰拍 / 俯拍 / 平视）、固定还是推拉跟、谁在画面哪边、做什么、台词在第几秒说……留空就按这一拍自动写">' +
      esc(g.direction || "") + '</textarea>' +
      promptShotHtml(g.prompt, list.length) +
      '<div class="sub" style="margin:2px 0">提示词可以直接改，改完自动保存；然后点上面卡片的「重新生成 / 生成这一段」出片。</div>' +
      '<textarea class="prompt" id="pText" data-seg="' + g.no + '" spellcheck="false" placeholder="这一段还没有提示词。点「按口述写这一段的提示词」，或自己写">' +
      esc(g.prompt || "") + "</textarea>" +
      (g.h3_prompt_sent
        ? '<details style="margin-top:6px"><summary class="sub" style="cursor:pointer">出片时实际发给 H3 的提示词（含上一段末帧的接力句、参考图编号）</summary><pre style="white-space:pre-wrap;font-size:12px;line-height:1.6;background:#faf8f3;padding:8px;border-radius:6px">' + esc(g.h3_prompt_sent) + '</pre></details>'
        : '<div class="sub" style="margin-top:4px">出片时会自动在上面这份后面加上「上一段最后一帧」的接力句和参考图编号（第 1 段没有上一段）；出过片后这里会显示实际发送的那份。</div>') +
      '<div id="pSave" class="sub" style="margin-top:4px;min-height:18px">' + (g.prompt_edited ? '<span class="stat-green">你改过这一段（已保存）' + (g.prompt_auto ? '；剧本重写时没覆盖' : '') + '</span>' : '') + '</div></div>' +

      /* 右：只保留参考；视频和操作已经回到上方每一段的大卡片 */
      '<div class="col col-r">' +
      '<div class="col-h sub"><b>参考图</b><span>' + refCount + " 张</span></div>" +
      '<div class="refs">' +
      myChars.map(function (c) { return chip("character", c.name, "👤"); }).join("") +
      myScenes.map(function (s) { return chip("scene", s.name, "🏛"); }).join("") +
      extrasHtml +
      ((myChars.length + myScenes.length + extras.length) === 0
        ? '<div class="note">这一段没有参考图——从下面「＋ 从资源添加」挑人设/场景图</div>' : "") +
      addSelect +
      '<div class="note">✕ 从这一段删除；＋ 从资源添加（来源是资源栏已采用的人设/场景图）。人设图只用已采用版。</div>' +
      "</div></div>" +

      "</div>";
  }

  /* ---------- 整页 ---------- */
  function render(main) {
    MAIN = main;
    var sc = scene();
    /* #tlPreview 在工具条之下、视频条之上：出片前先看一遍每段讲什么，再点确认 */
    /* P353（用户 9-14 定）：出片前预览、确认按钮、对话框、问题清单全部去掉——只留段卡片和提示词框 */
    main.innerHTML = '<div class="tlpage">' + bar(sc) +
      (sc ? timeline(sc) + panel(sc)
          : '<div class="tl-empty">还没有内容。点「🎬 生成」</div>') +
      '<div class="job" id="jobBox" style="display:none"></div></div>';
    wire(main);
    drawPreview();   // 用缓存的预览画；真正重新读在 reload / 任务结束时
  }

  function wire(main) {
    var st = ST || {};
    var id = function (x) { return document.getElementById(x); };
    var sc = scene();
    /* 「当前是第几话」是系统的第一公民：从 #timeline?ep=N 读，所有请求都带上，
       分镜页不许自己猜——猜错就会覆盖别的话的分镜。 */
    var api = function (p, b) {
      var body = b || {};
      if (body.ep == null) body.ep = EP;
      return window.api.post("/api/timeline/" + st.storyId + p, body);
    };

    [].forEach.call(document.querySelectorAll(".tab"), function (x) {
      x.onclick = function () {
        cur = parseInt(x.getAttribute("data-no"), 10); seg = 0; render(main);
      };
    });
    /* 话次下拉：切到第 N 话，各话数据本来就分开存，直接重载即可 */
    var epSel = id("epPick");
    if (epSel) epSel.onchange = function () {
      var n = parseInt(epSel.value, 10) || 1;
      if (n === EP) return;
      EP = n; cur = 1; seg = 0; tlX = 0;
      PV = null; pvOpen = false;        // 别把上一话的预览挂在这一话上
      if (poll) { clearInterval(poll); poll = null; }
      /* 同步 hash（刷新后还留在这一话）。hash 变了路由会自己重渲染，
         这里就别再 reload 一次，否则同一页画两遍。 */
      var h = "#timeline?ep=" + n;
      if (location.hash !== h) { location.hash = h; } else { reload(main); }
    };

    var bh = id("bHealth");
    if (bh) bh.onclick = function () { window.UIHealth.show(st.storyId, (ST || {}).ep || 1); };
    var bv = id("bVoice");
    if (bv) bv.onclick = function (e) {
      e.currentTarget.disabled = true;
      showJob({ running: true, step: "加载语音模型…", done: 0, total: 0 });
      api("/voice-check", {}).then(function () { startPoll(main); })
        .catch(function (err) { e.currentTarget.disabled = false; showJob({ err: err.message || "启动失败" }); });
    };
    var br = id("bRerender");
    if (br) br.onclick = function (e) {
      var bad = window.__voiceBad || [];
      if (!bad.length) { window.UI.toast("没有不合格的段"); return; }
      if (!window.confirm("重渲第 " + bad.join("、") + " 段？")) return;
      e.currentTarget.disabled = true;
      api("/rerender", { segs: bad }).then(function () { startPoll(main); })
        .catch(function (err) { e.currentTarget.disabled = false; showJob({ err: err.message || "启动失败" }); });
    };
    var b = id("bGenN");
    var nSel = id("genN");
    if (nSel) nSel.onchange = function () { genN = parseInt(nSel.value, 10) || 5; };
    if (b) b.onclick = function (e) {
      var btn = e.currentTarget;
      var n = nSel ? (parseInt(nSel.value, 10) || 5) : genN;
      genN = n;
      if (!window.confirm("从第一个没视频的段起往后出 " + n + " 段（每段约 3-8 分钟）。开始？")) return;
      btn.disabled = true;
      showJob({ running: true, step: "正在启动…", done: 0, total: n * 2 });
      return flushPromptSave().then(function () { return api("/gen-next", { n: n }); }).then(function () {
        window.UI.toast("开始生成下面 " + n + " 段，下面有进度");
        startPoll(main);
      }).catch(function (err) {
        btn.disabled = false;
        showJob({ err: err.message || "启动失败" });
      });
    };

    /* 逐段生成：只往后加一段（123，不能跳），接住已确认（含你改过）的前段末尾状态。
       第一段和下一段是同一个动作，只是按当前段数分别启用。 */
    var genNext = function (btn) {
      if (!flushPromptSaveSync()) return;
      btn.disabled = true;
      window.UI.toast("导演生成下一段…约 20 秒");
      api("/direct-next", {}).then(function (r) {
        if (r && r.done) { window.UI.toast("剧本已全部分完 ✓"); btn.disabled = false; return; }
        var no = (r && r.no) || 0;
        window.UI.toast("第 " + no + " 段已生成，可改文字，满意再点「生成下一段」");
        if (no) seg = no - 1;            // 选中刚出的这一段
        return reload(main);
      }).catch(function (e) { btn.disabled = false; window.UI.toast(e.message, "err"); });
    };
    b = id("bFirst"); if (b) b.onclick = function () { genNext(this); };
    b = id("bNext"); if (b) b.onclick = function () { genNext(this); };

    b = id("bScene");
    if (b) b.onclick = function () {
      return window.UIActions.progress.wrap("重做第" + cur + "场",
        api("/beats", { scene_no: cur }).then(function () {
          return api("/shots", { scene_no: cur });
        }).then(function () { return reload(main); }));
    };

    b = id("zIn"); if (b) b.onclick = function () { PX = Math.min(60, PX + 4); render(main); };
    b = id("zOut"); if (b) b.onclick = function () { PX = Math.max(32, PX - 4); render(main); };

    [].forEach.call(document.querySelectorAll(".clip"), function (x) {
      x.onclick = function (e) {
        if (e.target.closest && e.target.closest("button,video")) return;
        e.stopPropagation();
        var sr0 = id("tlScroll");
        if (sr0) tlX = sr0.scrollLeft;      // 记住拖到哪了，重绘后还原
        seg = parseInt(x.getAttribute("data-i"), 10); render(main);
      };
    });
    [].forEach.call(document.querySelectorAll(".clip video"), function (v) {
      v.onclick = function (e) { e.stopPropagation(); };
      v.onplay = function () {
        [].forEach.call(document.querySelectorAll(".clip video"), function (other) {
          if (other !== v) other.pause();
        });
      };
    });
    [].forEach.call(document.querySelectorAll("[data-seg-del]"), function (btn) {
      btn.onclick = function (e) {
        e.stopPropagation();
        var no = parseInt(btn.getAttribute("data-seg-del"), 10) || 0;
        if (!window.confirm("删掉第 " + no + " 段（末段）？删除只能从最后一段往回删。")) return;
        api("/segment-delete", { no: no }).then(function () {
          window.UI.toast("已删第 " + no + " 段");
          seg = Math.max(0, no - 2);   // 选到上一段
          return reload(main);
        }).catch(function (err) { window.UI.toast(err.message, "err"); });
      };
    });
    [].forEach.call(document.querySelectorAll("[data-card-gen]"), function (btn) {
      btn.onclick = function (e) {
        e.stopPropagation();
        var sr0 = id("tlScroll"); if (sr0) tlX = sr0.scrollLeft;
        seg = parseInt(btn.getAttribute("data-card-gen"), 10) || 0;
        genSeg(main, !!segsOf(sc)[seg].video);
      };
    });
    /* P316：开场段单独出/重出 */
    [].forEach.call(document.querySelectorAll("[data-open-gen]"), function (btn) {
      btn.onclick = function (e) {
        e.stopPropagation();
        var k = parseInt(btn.getAttribute("data-open-gen"), 10) || 0;
        btn.disabled = true;
        api("/opening-render", { k: k, ep: EP })
          .then(function () { UI.toast("开场段 " + k + " 开始出片"); startPoll(main); })
          .catch(function (err) { btn.disabled = false; UI.toast(err.message, "err"); });
      };
    });
    [].forEach.call(document.querySelectorAll("[data-card-adopt]"), function (btn) {
      btn.onclick = function (e) {
        e.stopPropagation();
        var i = parseInt(btn.getAttribute("data-card-adopt"), 10) || 0;
        var g = segsOf(sc)[i]; if (!g || !g.video) return;
        g.adopted = true;
        btn.disabled = true; btn.textContent = "✓ 已采用";
        api("/save-scene", { scene_no: cur, segments: sc.segments })
          .then(function () { UI.toast("第 " + g.no + " 段视频已采用"); render(main); })
          .catch(function (err) { g.adopted = false; btn.disabled = false; btn.textContent = "采用"; UI.toast(err.message, "err"); });
      };
    });
    var srr = id("tlScroll");
    if (srr && tlX) srr.scrollLeft = tlX;

    /* 按住空白处拖时间轴 */
    var sr = id("tlScroll");
    if (sr) {
      var down = false, sx = 0, sl = 0;
      sr.onmousedown = function (e) {
        if (e.target.closest && e.target.closest(".clip")) return;
        down = true; sx = e.pageX; sl = sr.scrollLeft; sr.classList.add("drag");
      };
      document.addEventListener("mouseup", function () {
        down = false;
        var el = document.getElementById("tlScroll");
        if (el) el.classList.remove("drag");
      });
      sr.onmousemove = function (e) {
        if (!down) return; e.preventDefault(); sr.scrollLeft = sl - (e.pageX - sx);
      };
      sr.onwheel = function (e) {
        if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) { e.preventDefault(); sr.scrollLeft += e.deltaY; }
      };
    }

    /* P397：口述这一段怎么拍 → 只重写这一段的提示词（留空＝按这一拍自动写） */
    b = id("bDirect");
    if (b) b.onclick = function () {
      var t = id("pDirect");
      return window.UIActions.progress.wrap("按口述写提示词",
        api("/segment-prompt", { scene_no: cur, seg_no: seg + 1, ep: EP, direction: t ? t.value : "" })
          .then(function () { return reload(main); }));
    };

    /* 参考图只记录人物/场景名字，具体路径恒取全局已采用版。 */
    var collectRefs = function () {
      var g = segsOf(sc)[seg];
      if (!g) return;
      g.refs = [];
      [].forEach.call(document.querySelectorAll("[data-ref]"), function (y) {
        if (!y.checked) return;
        var nm = y.getAttribute("data-ref");
        g.refs.push({ k: y.getAttribute("data-kind"), n: nm, p: "" });
      });
      api("/save-scene", { scene_no: cur, segments: sc.segments });
    };
    [].forEach.call(document.querySelectorAll("[data-ref]"), function (c) {
      c.onchange = function () {
        var lab = c.closest ? c.closest(".refcard") : c.parentNode;
        if (lab) lab.classList.toggle("on", c.checked);
        collectRefs();
      };
    });
    /* ✕ 从这一段删除某张参考图（自动匹配到的记进 ref_hide 不再显示；手动加的从 refs 去掉）。 */
    [].forEach.call(document.querySelectorAll("[data-ref-del]"), function (x) {
      x.onclick = function (e) {
        e.preventDefault(); e.stopPropagation();
        var g = segsOf(sc)[seg]; if (!g) return;
        var nm = x.getAttribute("data-ref-del");
        g.ref_hide = (g.ref_hide || []).filter(function (n) { return n !== nm; });
        g.ref_hide.push(nm);
        g.refs = (g.refs || []).filter(function (r) { return r.n !== nm; });
        api("/save-scene", { scene_no: cur, segments: sc.segments });
        render(main);
      };
    });
    /* ＋ 从资源添加参考图（来源是资源栏已采用的人设/场景图）。 */
    var addSel = id("refAdd");
    if (addSel) addSel.onchange = function () {
      var v = addSel.value; if (!v) return;
      var i0 = v.indexOf("|"); var k = v.slice(0, i0), nm = v.slice(i0 + 1);
      var g = segsOf(sc)[seg]; if (!g) return;
      g.ref_hide = (g.ref_hide || []).filter(function (n) { return n !== nm; });
      g.refs = g.refs || [];
      if (!g.refs.some(function (r) { return r.n === nm; })) g.refs.push({ k: k, n: nm, p: "" });
      api("/save-scene", { scene_no: cur, segments: sc.segments });
      render(main);
    };

    /* 整页滚动布局下，提示词框随内容自动长高，不出现内滚动条 */
    var pt = id("pText");
    if (pt) {
      var grow = function () { pt.style.height = "auto"; pt.style.height = (pt.scrollHeight + 10) + "px"; };
      grow();
      pt.oninput = grow;
      /* 改动自动存回这一段——逐段生成时下一段就按你改后的接住。
         P312：显示 保存中／已保存／保存失败，失败不再静默；生成前会先 flushPromptSave() */
      var ps = id("pSave");
      pt.onblur = function () { savePrompt(sc, seg, pt, ps); };
      pt.addEventListener("input", function () { promptDirty = true; if (ps) ps.innerHTML = '<span class="note-gray" style="margin:0">有改动，离开输入框自动保存</span>'; });
    }
  }

  function genSeg(main, force) {
    var st = ST || {};
    /* 提示词框是可编辑的：生成时把框里现在的文本带上，
       用户改过什么就按什么生成，不用先点保存 */
    var t = document.getElementById("pText");
    /* P353 bug 修：卡片上的「生成这一段」切段后没重画，框里还是上一段的词——只有框属于这一段时才带 */
    var mine = t && String(t.getAttribute("data-seg") || "") === String(seg + 1);
    return window.UIActions.progress.wrap("生成第" + (seg + 1) + "段视频",
      window.api.post("/api/timeline/" + st.storyId + "/generate-segment",
                      { scene_no: cur, seg_no: seg + 1, force: !!force,
                        prompt: mine ? t.value : "" })
        .then(function () { return reload(main); }));
  }

  /* ---------- 出片前预览（第二次确认） ----------
     用户定的：出视频之前，先把每一段"发生什么 / 谁出场 / 在哪 / 关键台词 / 几秒"摆出来看一遍，
     再点一次「确认，生成本话视频」才真的出片。数据来自 /preview：
     切片按 beat 归属 + 拍摄清单的检查结果 + 安排里的时长和剪点。 */
  function segByNo(no) {
    var hit = null;
    (D.timeline.scenes || []).forEach(function (s) {
      (s.segments || []).forEach(function (g) { if (+g.no === +no) hit = g; });
    });
    return hit;
  }
  function beatIndexOf(name) {
    var bs = ((PV || {}).arrangement || {}).beats || [];
    for (var i = 0; i < bs.length; i++) if (bs[i] && bs[i].stage === name) return i;
    return -1;
  }
  function cutBeat() {
    var c = parseInt(((PV || {}).arrangement || {}).cut_after_beat, 10);
    return isNaN(c) ? -1 : c;
  }
  /* 有没有视频：预览卡自己说的 + 时间轴上现在的段（边出边刷，卡上的 ✅ 不等重新读预览） */
  function cardHasVideo(c) {
    /* 按旧剧本出的那条不算"已经有视频"——它要按新剧本重出（P282） */
    if (c && c.stale_video) return false;
    if (c.has_video) return true;
    var g = segByNo(c.no);
    return !!(g && g.video);
  }
  /* 还没出视频的段数：确认按钮一次出这么多（1..10）。剪到下一话的段不算。 */
  function pendingCount() {
    var cards = (PV || {}).cards || [];
    var cut = cutBeat(), n = 0;
    if (cards.length) {
      cards.forEach(function (c) {
        if (cardHasVideo(c)) return;
        if (cut >= 0 && beatIndexOf(c.beat) > cut) return;
        n += 1;
      });
    } else {
      (D.timeline.scenes || []).forEach(function (s) {
        (s.segments || []).forEach(function (g) { if (!g.video) n += 1; });
      });
    }
    return Math.max(1, Math.min(10, n));
  }
  /* 缩略图地址：后端给的可能已经带 /files/，也可能是相对路径，两种都接 */
  function thumbUrl(p) {
    var s = String(p || "").replace(/\\/g, "/");
    if (!s) return "";
    if (/^(\/files\/|https?:\/\/|data:)/.test(s)) return s;
    return fileUrl(s.replace(/^\/+/, ""));
  }
  function loadPreview() {
    return Promise.resolve();                                   /* P353：出片前预览已去掉 */
    var sid = (ST || {}).storyId;
    if (!sid) return Promise.resolve();
    var my = ++pvSeq;
    return window.api.post("/api/timeline/" + sid + "/preview", { ep: EP })
      .then(function (r) {
        if (my !== pvSeq) return;        // 后面又发过一次，这次的作废
        PV = r || {};
        PV.error = "";
        drawPreview();
      })
      .catch(function (e) {
        if (my !== pvSeq) return;
        PV = { error: e.message || "读不到预览" };
        drawPreview();
      });
  }
  /* 估时文案（P267）：秒 → 「N 分钟」；≥1 小时 → 「X 小时 Y 分」；0/空/负数返回 ""（没估到就不写，别编数字）。
     不足一分钟按 1 分钟——确认框里写「约 0 分钟」用户会以为是瞬间完成。 */
  function etaText(sec) {
    var s = parseFloat(sec);
    if (!(s > 0)) return "";
    var m = Math.max(1, Math.round(s / 60));
    if (s >= 3600) {
      var h = Math.floor(m / 60), r = m % 60;
      return h + " 小时" + (r ? " " + r + " 分" : "");
    }
    return m + " 分钟";
  }
  /* 还没设定图的名字（P267）：预览返回 images_missing {characters:[名字], scenes:[名字]}，人物在前场景在后 */
  function pvMissingNames() {
    var im = (PV || {}).images_missing || {};
    return [].concat(im.characters || [], im.scenes || [])
      .map(function (x) { return String(x || "").trim(); }).filter(Boolean);
  }
  /* 缺图提示（P267）：确认后后端会先把缺的设定图出完再出视频，之前不提示，
     用户点了确认看进度条半天停在"出图"以为卡死。橙色小字挂在检查结论后面。 */
  function pvMissingHtml() {
    var names = pvMissingNames();
    if (!names.length) return "";
    var mins = Math.max(1, Math.round(num((PV || {}).images_eta_sec, 0) / 60));
    return ' <span class="stat-orange" style="font-size:12px">还没有设定图：' +
      names.map(function (x) { return esc(x); }).join("、") +
      "，确认后会先出图（约 " + mins + " 分钟）</span>";
  }
  /* 答过的问题记一下（P263）：预览每次重读都是新对象，靠 kind+ref 认出同一条，
     不然答完刷新一次「已选」就没了；键带项目和话，换话不串 */
  var askMemo = {};
  function askKey(a) {
    return (ST || {}).storyId + "#" + EP + "|" + (a.kind || "") + "|" + (a.ref || a.question || "");
  }
  function askDone(a) { return a.answered || askMemo[askKey(a)] || ""; }
  /* 顶部一行的检查结论：没清单=灰字"未检查"；有报告按 status 三色；末尾挂缺图提示（P267） */
  function pvStatusHtml() {
    if (!PV) return '<span class="note-gray" style="margin:0">正在读取预览…</span>';
    if (PV.error) return '<span class="note-gray" style="margin:0">预览读不到：' + esc(PV.error) + "</span>";
    var rep = PV.report;
    if (!PV.checked || !rep) {
      /* 没清单：清单六项未检查，但安排对照（结尾/情节/时长）后端仍会给——放不下要问的话不能藏起来 */
      var ar = PV.arrangement || {}, aAsks = ar.asks || [], aItems = ar.items || [];
      var aBad = aItems.filter(function (x) { return x.status === "有问题"; }).length;
      var head = '<span class="note-gray" style="margin:0">这一话还没有拍摄清单，清单项未检查</span>';
      if (aAsks.length) head += ' <span class="stat-orange" style="font-size:13px">❓ 需要确认（' + aAsks.length + " 处）</span>";
      else if (aBad) head += ' <span style="font-size:13px;color:var(--red)">❓ 安排对照 ' + aBad + " 处要你定</span>";
      if (aAsks.length || aItems.length) head += ' <button class="btn small" id="pvToggle">' + (pvOpen ? "收起" : "看明细") + "</button>";
      return head + pvMissingHtml();
    }
    var items = rep.items || [], asks = rep.asks || [];
    var bad = items.filter(function (x) { return x.status === "有问题"; }).length;
    var unk = items.filter(function (x) { return x.status === "无法判断"; }).length + asks.length;
    var s;
    if (rep.status === "通过") s = '<span class="stat-green" style="font-size:13px">✅ 通过</span>';
    else if (rep.status === "有问题") s = '<span style="font-size:13px;color:var(--red)">❓ ' + bad + " 处要你定</span>";      /* P328③：不是程序出错，是要你拿主意 */
    else s = '<span class="stat-orange" style="font-size:13px">❓ 需要确认' + (unk ? "（" + unk + " 处）" : "") + "</span>";
    return s + ' <button class="btn small" id="pvToggle">' + (pvOpen ? "收起" : "看明细") + "</button>" + pvMissingHtml();
  }
  /* 预览明细里列出的问题：候选按钮点了从这里取对象（和进度条下的 curBlocked 是两份） */
  var curPvAsks = [];
  /* 明细：和后端 summarize 同一格式——[状态] 检查：详情；再把要问的列在后面。
     要问的每条带候选按钮（P263）：用户在预览里就能答，不必先点确认、等门停下来再答；
     答过的显示「已选：xx」，和进度条下的样子一致。 */
  function pvDetailHtml() {
    if (!pvOpen || !PV) return "";
    /* 没清单时明细画安排对照那几条（后端放在 arrangement.items/asks 里） */
    var rep = PV.report || { items: (PV.arrangement || {}).items || [], asks: (PV.arrangement || {}).asks || [] };
    var rows = [];
    (rep.items || []).forEach(function (x) {
      var style = x.status === "通过" ? "color:var(--green)"
                : (x.status === "有问题" ? "color:var(--red)" : "color:var(--orange)");
      rows.push('<div style="' + style + '">[' + esc(x.status === "有问题" ? "要你定" : (x.status === "无法判断" ? "要你定" : (x.status || ""))) + "] " + esc(x.check || "") +
                "：" + esc(x.detail || "") + "</div>");
    });
    curPvAsks = rep.asks || [];
    /* P328g①：任务停在对话框时，这里同一批问题不再画第二套按钮（两套各记一套状态互不知道），只列文字 */
    var chatOn = !!(window.GateChat && Array.isArray(curBlocked) && curBlocked.length && document.querySelector("#gateChat [data-gc-input]"));
    curPvAsks.forEach(function (a, i) {
      var opts = a.options || [], done = askDone(a);
      rows.push('<div style="color:var(--orange);display:flex;align-items:center;gap:6px;flex-wrap:wrap">' +
                "<span>❓ " + esc(a.question || "") + "</span>" +
                (done
                  ? '<span class="stat-green">✓ 已选：' + esc(done) + "</span>"
                  : (chatOn ? '<span class="note-gray" style="margin:0">（在下面的对话框里答）</span>'
                            : opts.map(function (o, k) {
                                return '<button class="btn small" data-i="' + i + '" data-k="' + k + '">' + esc(o) + "</button>";
                              }).join(""))) +
                "</div>");
    });
    if (!rows.length) rows.push('<div class="note-gray" style="margin:0">清单检查没有记录</div>');
    return '<div id="pvDetail" style="font-size:13px;line-height:1.7;margin:0 0 10px;padding:8px 12px;' +
      'background:#fbf8f2;border:1px solid #efe9de;border-radius:8px">' + rows.join("") + "</div>";
  }
  function pvCardHtml(c) {
    var vid = cardHasVideo(c);
    var who = [].concat(c.who || []).filter(Boolean).map(function (w) { return esc(w); }).join("、");
    var th = thumbUrl(c.thumb);
    return '<div class="card pvcard" style="flex:none;width:330px;margin:0;padding:12px;display:flex;gap:10px;' +
      'position:relative;scroll-snap-align:start">' +
      (c.stale_video ? '<span title="这条片子是按旧剧本出的，确认后会重出" style="position:absolute;top:8px;right:10px;font-size:12px;color:var(--orange)">⚠ 旧剧本</span>'
        : (vid ? '<span title="这一段已经有视频" style="position:absolute;top:8px;right:10px;font-size:14px">✅</span>' : "")) +
      '<div style="flex:none;width:96px;height:54px;border-radius:6px;overflow:hidden;background:#efeae1;' +
      'display:flex;align-items:center;justify-content:center">' +
      (th ? '<img src="' + esc(th) + '" alt="" style="width:100%;height:100%;object-fit:cover;display:block">'
          : '<span style="font-size:11px;color:#a49a89">没有图</span>') + "</div>" +
      '<div style="min-width:0;flex:1;font-size:13px;line-height:1.6;color:#4a4238">' +
      '<div style="font-size:14px;font-weight:600;color:#5a3b1e;padding-right:20px">第 ' + esc(c.no) + " 段" +
      (c.beat ? " · " + esc(c.beat) : "") + "</div>" +
      '<div style="color:#2f2921">' + (c.what ? esc(c.what) : '<span class="note-gray" style="margin:0">（没写发生什么）</span>') + "</div>" +
      "<div>谁：" + (who || "—") + "</div>" +
      "<div>在哪：" + (c.where ? esc(c.where) : "—") + "</div>" +
      (c.line ? '<div style="color:#8a5a2b">台词：' + esc(c.line) + "</div>" : "") +
      '<div class="note-gray" style="margin:2px 0 0">约 ' + Math.round(num(c.seconds, 0)) + " 秒</div>" +
      "</div></div>";
  }
  function drawPreview() {
    var box = document.getElementById("tlPreview");
    if (!box) return;
    var cards = (PV || {}).cards || [];
    var cut = cutBeat();
    var cutLine = "";
    if (cut >= 0) {
      var bs = ((PV || {}).arrangement || {}).beats || [];
      var stage = (bs[cut] || {}).stage || ("第 " + (cut + 1) + " 段");
      cutLine = '<div style="margin:0 0 10px"><span class="stale">✂ 后半部分留到下一话（本话出到「' +
        esc(stage) + "」为止）</span></div>";
    }
    var body;
    if (!PV) body = '<div class="note-gray">正在读取预览…</div>';
    else if (!cards.length) body = PV.error ? "" :
      '<div class="note-gray">这一话还没有切出画面段，先在剧本页把画面稿生成好</div>';
    else body = '<div class="tl-scroll" id="pvScroll"><div class="tl-row" style="gap:12px;padding:4px 2px 8px">' +
      cards.map(pvCardHtml).join("") + "</div></div>";
    box.innerHTML = '<div class="tl">' +
      '<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:8px">' +
      '<b style="font-size:15px;color:#5a3b1e">出片前预览</b>' + pvStatusHtml() +
      (cards.length ? '<span class="note-gray" style="margin:0">' + cards.length + " 段</span>" : "") +
      '<span style="margin-left:auto"></span>' +
      '<button class="btn primary" id="pvConfirm"' + (busy ? " disabled" : "") +
      ' title="按下面的预览，把还没出的段一次出完（先写提示词，再出视频）">✅ 确认，生成本话视频</button>' +
      "</div>" + cutLine + pvDetailHtml() + body + "</div>";
    var tg = document.getElementById("pvToggle");
    if (tg) tg.onclick = function () { pvOpen = !pvOpen; drawPreview(); };
    var cb = document.getElementById("pvConfirm");
    if (cb) cb.onclick = function () { confirmAll(); };
    var ps = document.getElementById("pvScroll");
    if (ps) ps.onwheel = function (e) {
      if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) { e.preventDefault(); ps.scrollLeft += e.deltaY; }
    };
    /* 明细里的候选按钮（P263）：只认 #pvDetail 里的，时间轴的 .clip 也用 data-i，别串 */
    [].forEach.call(document.querySelectorAll("#pvDetail button[data-i]"), function (b) {
      b.onclick = function () {
        var i = parseInt(b.getAttribute("data-i"), 10), k = parseInt(b.getAttribute("data-k"), 10);
        var a = curPvAsks[i]; if (!a) return;
        answerAsk(a, String((a.options || [])[k] || ""), b);
      };
    });
  }
  /* 确认框文案（P267）：段数 + 缺图先出图 + 视频估时 + 合计。
     之前写死「n×1.5 分钟」，实测 864×480 一段 15 秒要五六分钟，用户以为卡死了；
     现在按后端估的 eta_video_sec / images_eta_sec / eta_sec 写，并交代是按哪档几步、每秒视频耗时多少、
     实测还是默认估的——数字对不上时用户能看出是哪一项错。PV 没读到或后端没给：那几段留空，只剩段数。 */
  function confirmText(n) {
    var pv = PV || {};
    var parts = [];
    var names = pvMissingNames();
    if (names.length) {
      parts.push("会先出 " + names.length + " 张设定图（" + names.join("、") + "），约 " +
                 (etaText(pv.images_eta_sec) || "1 分钟"));
    }
    var vid = etaText(pv.eta_video_sec);
    if (vid) {
      var rate = pv.rate || {}, how = [];
      if (rate.tier) how.push(String(rate.tier) + " 档" + (num(rate.steps, 0) ? " " + Math.round(rate.steps) + " 步" : ""));
      if (num(rate.sec_per_sec, 0)) how.push("每 1 秒视频约 " + (Math.round(rate.sec_per_sec * 10) / 10) + " 秒");
      how.push(rate.source === "measured" ? "按最近实测估" : "按默认估");
      var total = num(pv.eta_sec, 0) || (num(pv.images_eta_sec, 0) + num(pv.eta_video_sec, 0));
      parts.push("视频约 " + vid + "（" + how.join("，") + "）；合计约 " + etaText(total));
    }
    return "按预览生成本话全部视频（还有 " + n + " 段没出）。" +
      (parts.length ? parts.join("；") + "。" : "") + "开始？";
  }
  /* 第二次确认：和「🎬 生成（提示词＋视频）」走同一条 /gen-next，n = 还没视频的段数。
     后端在写完提示词、出视频之前会再跑一次出片前门，判不了的会停下来问（见 showJob 的 blocked）。 */
  function confirmAll() {
    var st = ST || {};
    if (!st.storyId || !MAIN) return;
    var n = pendingCount();
    if (!window.confirm(confirmText(n))) return;
    genN = n;
    showJob({ running: true, step: "正在启动…", done: 0, total: n * 2 });
    flushPromptSave().then(function () { return window.api.post("/api/timeline/" + st.storyId + "/gen-next", { n: n, ep: EP }); })
      .then(function () { window.UI.toast("开始生成 " + n + " 段，下面有进度"); startPoll(MAIN); })
      .catch(function (err) { showJob({ err: err.message || "启动失败" }); });
  }
  /* 两个出片按钮一起灰/一起亮：跑着的时候都不许再点 */
  function setGenEnabled(on) {
    ["bGenN", "pvConfirm"].forEach(function (x) {
      var b = document.getElementById(x);
      if (b) b.disabled = !on;
    });
  }
  /* 后端停下来问的问题：每条 question + 候选按钮 */
  function blockedHtml(j) {
    return "";                                                  /* P353：门不再问，进度条下面不画问题 */
    var asks = Array.isArray(j.blocked) ? j.blocked : [];
    curBlocked = asks;
    if (!asks.length) return "";
    if (window.GateChat) return "";                          /* P328②：问题在对话框里答，进度条下不再画清单 */
    var left = asks.filter(function (a) { return !askDone(a); }).length;
    return '<div id="jobBlocked" style="margin-top:10px;font-size:13px;line-height:1.7">' +
      '<div style="color:var(--red);font-weight:600">先不出片——' + asks.length + ' 个问题要你定' + (left ? '（还剩 ' + left + ' 个，答完自动接着出片）' : '（都答了，正在接着出片）') + '：</div>' +
      asks.map(function (a, i) {
        var opts = a.options || [], done = askDone(a);
        if (["scene", "length", "speaker", "problem", "event", "beat"].indexOf(a.kind) < 0) opts = ["就按现在的出片", "我去改剧本"];   /* P326a：页面答不了的 kind 也有出口 */
        var circ = "①②③④⑤⑥⑦⑧⑨⑩"[i] || (i + 1) + ".";
        return '<div style="margin:6px 0;padding:6px 8px;background:#fbf8f2;border-radius:8px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">' +
          "<span><b>" + circ + "</b> " + esc(a.question || "") + "</span>" +
          (done
            ? '<span class="stat-green">✓ ' + esc(done) + "</span>"
            : opts.map(function (o, k) {
                return '<button class="btn small" data-ask="' + i + '" data-opt="' + k + '">' + esc(o) + "</button>";
              }).join("") +
              /* P323d：说话人问题可以自己填一个名字（候选里没有的） */
              (a.kind === "speaker" ? '<input data-ask-free="' + i + '" placeholder="或填名字后回车" style="height:28px;padding:0 8px;border-radius:6px;width:130px">' : "")) +
          "</div>";
      }).join("") + "</div>";
  }
  /* 回答一条「要你定」（P263）：预览明细和进度条下的候选按钮都走这里，按 kind 分路——
       scene ：清单上的场景对不上场景卡，选一张卡 → /shotlist-scene {ep, scene_id=ref, card}
       length：本话时长不够/超了 → /arrange/length {choice}（候选文案「延长本话」/「把后半部分留到下一话」，路由收「延长本话」/「留到下一话」）
       其他  ：说话人/在场这类要在分镜里改，这里只提示不提供入口
     记上之后：标已选、亮确认按钮、重画进度条下的问题、重读预览——场景 ask 之前只能等门停下来再答，绕一圈 */
  function answerAsk(a, opt, btn) {
    if (!a) return Promise.resolve();
    var sid = (ST || {}).storyId;
    opt = String(opt || "");
    var req;
    if (a.kind === "scene") {
      var mNew = /^新建场景卡「(.+)」$/.exec(opt);   // 候选最后一项：照清单那场戏建一张卡再绑（P269）
      req = window.api.post("/api/timeline/" + sid + "/shotlist-scene",
        mNew ? { ep: EP, scene_id: a.ref, card: mNew[1], create: true } : { ep: EP, scene_id: a.ref, card: opt });
    } else if (a.kind === "speaker") {
      /* P323d：这一句谁说的 → 写回清单和画面稿 */
      req = window.api.post("/api/timeline/" + sid + "/shotlist-speaker", { ep: EP, line_n: a.ref, name: opt });
    } else if (a.kind === "length") {
      var choice = /延长/.test(opt) ? "延长本话" : "留到下一话";
      req = window.api.post("/api/saga/" + sid + "/arrange/length", { choice: choice });
    } else if (a.kind === "problem" || a.kind === "event" || a.kind === "beat" || ["scene", "length", "speaker"].indexOf(a.kind) < 0) {
      /* 检查停下来了（P280）：要么照现在的出，要么去改剧本。已出的段两种选择都保留。 */
      if (/^就按|^已经拍了|^继续/.test(opt)) {
        if (btn) btn.disabled = true;
        a.answered = opt;
        askMemo[askKey(a)] = opt;
        var bb0 = document.getElementById("jobBlocked");
        if (bb0) { bb0.outerHTML = blockedHtml({ blocked: curBlocked }); wireBlocked(); }
        return continueIfAllAnswered();            /* P323e：全部答完才自动接着出（原来答一条就出片，别的问题被跳过） */
      }
      window.UI.toast("好，去 ⚙️设定 页改剧本；已出的段都留着，改完再点「确认，生成本话视频」");
      return Promise.resolve();
    } else {
      window.UI.toast("这个要在分镜里改好，再点「确认，生成本话视频」");
      return Promise.resolve();
    }
    if (btn) btn.disabled = true;
    return req
      .then(function () {
        a.answered = opt;
        askMemo[askKey(a)] = opt;
        window.UI.toast("已记下「" + opt + "」");
        setGenEnabled(true);
        var bb = document.getElementById("jobBlocked");
        if (bb) { bb.outerHTML = blockedHtml({ blocked: curBlocked }); wireBlocked(); }
        drawPreview();          // 预览里的这条先标「已选」，不等重读回来
        return loadPreview().then(continueIfAllAnswered);     /* P323e：答完全部自动接着出片 */
      })
      .catch(function (e) {
        if (btn) btn.disabled = false;
        window.UI.toast("没记上：" + (e.message || ""), "err");
      });
  }
  /* P323e：问题都答了 → 自动接着出片（有「就按现在的出」这种答案就 force，别的门再查一遍） */
  function continueIfAllAnswered() {
    var asks = curBlocked || [];
    if (!asks.length || asks.some(function (a) { return !askDone(a); })) return Promise.resolve();
    var force = asks.some(function (a) { return /^就按|^已经拍了|^继续/.test(String(askDone(a) || "")); });
    var nF = Math.max(1, Math.min(10, genN || 5));
    showJob({ running: true, step: "问题都答了，接着出片…", done: 0, total: nF * 2 });
    return window.api.post("/api/timeline/" + (ST || {}).storyId + "/gen-next", { n: nF, ep: EP, force: force })
      .then(function () { window.UI.toast("接着出片，共 " + nF + " 段"); startPoll(MAIN); })
      .catch(function (err) { showJob({ err: err.message || "启动失败" }); });
  }
  function wireBlocked() {
    [].forEach.call(document.querySelectorAll("[data-ask]"), function (b) {
      b.onclick = function () {
        var i = parseInt(b.getAttribute("data-ask"), 10), k = parseInt(b.getAttribute("data-opt"), 10);
        var a = curBlocked[i]; if (!a) return;
        answerAsk(a, String((a.options || [])[k] || ""), b);
      };
    });
    [].forEach.call(document.querySelectorAll("[data-ask-free]"), function (inp) {
      inp.onkeydown = function (e) {
        if (e.key !== "Enter") return;
        var i = parseInt(inp.getAttribute("data-ask-free"), 10), a = curBlocked[i];
        var v = (inp.value || "").trim();
        if (a && v) answerAsk(a, v, inp);
      };
    });
  }

  /* 进度条：点下去立刻显示，不等轮询 */
  function showJob(j) {
    var box = document.getElementById("jobBox");
    if (!box) return;
    busy = !!j.running;
    box.style.display = "block";
    var pct = j.total ? Math.round(j.done / j.total * 100) : 0;
    box.innerHTML = '<div class="job-in"><span class="job-s">' +
      (j.err ? "❌ " + esc(j.err) : esc(j.step || "准备中")) + "</span>" +
      (j.total ? '<span class="job-n">' + j.done + " / " + j.total + "</span>" : "") +
      (j.running ? '<button class="btn small" id="jobCancel" style="margin-left:8px" ' +
        'title="停掉这个项目正在跑的生成，已出来的段保留">⏹ 取消</button>' : "") +
      '<div class="job-b"><i style="width:' + pct + '%"></i></div>' +
      (j.note ? '<span class="job-o">' + esc(j.note) + "</span>" : "") + "</div>" +
      blockedHtml(j);   // 停下来问的问题画在进度条下面（不只 err 时）
    var jc = document.getElementById("jobCancel");
    if (jc) jc.onclick = function () {
      if (!window.confirm("取消这个项目正在跑的生成？已经出来的段会保留。")) return;
      jc.disabled = true; jc.textContent = "取消中…";
      window.api.post("/api/timeline/" + (ST || {}).storyId + "/cancel", {})
        .then(function () { window.UI.toast("已发出取消，当前这一步停下后任务结束"); })
        .catch(function (e) { window.UI.toast("取消失败：" + (e.message || ""), "err"); jc.disabled = false; jc.textContent = "⏹ 取消"; });
    };
    wireBlocked();
    setGenEnabled(!j.running);
    mountChat(j);                                             /* P328② */
    if (!j.running && Array.isArray(j.blocked) && j.blocked.length && !j.blocked.some(function (a) { return !askDone(a); }) && !busyContinue) {
      busyContinue = true;                                    /* P326a：门停下时问题其实都答过了（预览里答的）→ 自动接着出，只发一次 */
      continueIfAllAnswered().then(function () { busyContinue = false; }, function () { busyContinue = false; });
    }
  }
  var busyContinue = false;
  /* P328②：门停下来 → 对话框（分镜页、设定页共用 gate_chat.js）；答完 onClear(force) → 接着出片 */
  function mountChat(j) {
    return;                                                     /* P353：对话框修复已去掉 */
    var box = document.getElementById("gateChat");
    if (!box || !window.GateChat) return;
    if (j && !j.running && Array.isArray(j.blocked) && j.blocked.length) {
      window.GateChat.mount(box, { sid: (ST || {}).storyId, ep: EP, asks: j.blocked, n: Math.max(1, Math.min(10, genN || 5)),
        onClear: function (force) {
          var nF = Math.max(1, Math.min(10, genN || 5));
          showJob({ running: true, step: "问题都答了，接着出片…", done: 0, total: nF * 2 });
          window.api.post("/api/timeline/" + (ST || {}).storyId + "/gen-next", { n: nF, ep: EP, force: !!force })
            .then(function () { window.UI.toast("接着出片，共 " + nF + " 段"); startPoll(MAIN); })
            .catch(function (err) { showJob({ err: err.message || "启动失败" }); });
        } });
    } else if (j && j.running) {
      window.GateChat.clear(box);
    }
  }

  /* 进度轮询：全部生成是后台跑的，这里显示到第几场、哪一步。
     轮询间隔短一点——2.5 秒里用户会以为按钮坏了。 */
  function startPoll(main) {
    if (poll) clearInterval(poll);
    misses = 0;
    tick(main);                     // 立刻拉一次，不等第一个间隔
    poll = setInterval(function () { tick(main); }, 1500);
  }

  var misses = 0;

  function tick(main) {
    return window.api.post("/api/timeline/" + (ST || {}).storyId + "/progress", { ep: EP })
      .then(function (r) {
        misses = 0;
        var j = r.job || {};
        showJob(j);
        try {
          if (!j.running && j.note && j.note.indexOf('"voice"') >= 0) {
            var vc = JSON.parse(j.note);
            window.__voiceBad = vc.bad || [];
            var box = document.getElementById("jobBox");
            if (box) {
              var h = '<div style="font-size:12px;line-height:1.6;margin-top:6px">';
              (vc.voice || []).forEach(function (rw) {
                h += '<div>' + (rw.ok ? '🟢' : '🔴') + ' 第 ' + rw.seg + ' 段 期望：' + ((rw.expect || []).join(' / ') || '无') +
                     ' ｜ 听到：' + ((rw.heard || []).map(function (x) { return x[2]; }).join(' / ') || '无语音') +
                     (rw.note ? ' <span style="color:#c0392b">' + rw.note + '</span>' : '') + '</div>';
              });
              box.insertAdjacentHTML("beforeend", h + '</div>');
              var br2 = document.getElementById("bRerender");
              if (br2) { br2.style.display = window.__voiceBad.length ? "" : "none"; br2.disabled = false; }
              var bv2 = document.getElementById("bVoice"); if (bv2) bv2.disabled = false;
            }
          }
        } catch (_e) {}
        /* 边跑边刷新时间轴：拆完一场就能看到那一场的段，不用等全部结束 */
        var before = JSON.stringify((D.timeline || {}).scenes || []).length;
        D.timeline = r.timeline || D.timeline;
        var after = JSON.stringify((D.timeline || {}).scenes || []).length;
        if (!j.running || after !== before) {
          if (!j.running && poll) { clearInterval(poll); poll = null; }
          render(main);
          showJob(j);   // render 会把 jobBox 重建成隐藏的，跑着的时候也要补回来；停在 blocked 也在这里画出来
          if (!j.running) {
            setGenEnabled(true);
            loadPreview();   // 任务停了：缩略图/✅/检查结论都可能变了，重新读一遍预览
          }
        }
      })
      .catch(function (e) {
        /* 单次请求失败不杀轮询：后台还在跑，网络抖一下就断掉进度显示，
           用户会以为任务停了。连续失败 5 次才放弃。 */
        misses += 1;
        if (misses >= 5 && poll) {
          clearInterval(poll); poll = null;
          showJob({ err: "读不到进度了：" + (e.message || "") });
        }
      });
  }

  function reload(main) {
    return window.api.post("/api/timeline/" + (ST || {}).storyId + "/get", { ep: EP })   /* P314：带话号，别靠服务端记的"当前话"（切话后编辑框显示的是上一话的提示词，实测） */
      .then(function (r) {
        D = r || {};
        D.timeline = D.timeline || { scenes: [] };
        D.characters = D.characters || [];
        D.scenes = D.scenes || [];
        render(main);
        loadPreview();   // 每次 reload 都重新读出片前预览
        /* 刷新过页面也要能接上后台任务：进来先问一次还在不在跑。
           上次停在"需要你确认"的，刷新后也要看得到那几个问题。 */
        window.api.post("/api/timeline/" + (ST || {}).storyId + "/progress", { ep: EP })
          .then(function (r) {
            var j = r.job || {};
            if (j.running) startPoll(main);
          })
          .catch(function () {});
      });
  }

  window.Pages.timeline = {
    render: function (main, st) {
      ST = st;
      readEp();                 // 当前是第几话，从 #timeline?ep=N 读
      PV = null; pvOpen = false;   // 换项目/换话进来，预览从头读，别拿旧的顶着
      if (!st.storyId) {
        main.innerHTML = window.UI.empty("先在顶部选择一个故事项目");
        return Promise.resolve();
      }
      // 从剧本页「生成分镜」跳进来时带 gen1=1：分镜还空就自动出第一段
      var _gen1 = /[?&]gen1=1/.test(location.hash || "") && !_gen1Fired;
      if (_gen1) _gen1Fired = true;
      return reload(main).then(function () {
        if (_gen1 && segCountAll() === 0) {
          var b = document.getElementById("bFirst");
          if (b) b.click();
        }
      });
    }
  };
})();
