/* pages/config.js — ⚙️ 设置：编辑给 Qwen 的指令词 + 全局默认（新项目自动带入）。 */
window.Pages = window.Pages || {};
window.Pages.config = (function () {
  var st = null, main = null;
  var files = [], meta = null, defaults = {}, current = null, dirty = false, gpu = null;
  /* 预设表：选项背后给模型看的原话。全局的，所以放这一页，不在项目设定页。 */
  var presets = null, presetFile = "", presetKey = null, groups = [];
  var presetPh = null;      // 当前展开的是哪个占位符
  var presetMode = null;    // character＝人物预设独立区；workflow＝某份指令词里的预设
  var CHARACTER_PRESET_TABS = [
    ["人设图身体比例·每档的定义", "身体比例"],
    ["身材·每种的定义", "男女身材"],
    ["脸型·每种的定义", "男女五官"],
    ["发型·每种的定义", "男女发型"],
    ["人设图普通姿势·按性别", "普通姿势＋版式"],
    ["人物种族·每种的骨骼特征", "人物种族"],
    ["服装预设·按世界", "服装预设"],
    ["内容尺度（选项和内容）", "尺度人设图"]
  ];
  function esc(s) { return UI.esc(s); }

  function load() {
    var jobs = [
      window.api.get("/api/instructions/list"),
      window.api.get("/api/config/defaults"),
      window.api.get("/api/gpu"),
      window.api.post("/api/presets/get", {})
    ];
    // 选项列表借设定页那个接口拿（要一个 story_id；没有就跳过下拉）
    if (st.storyId) jobs.push(window.api.post("/api/project/settings", { story_id: st.storyId }));
    return Promise.all(jobs.map(function (p) { return p.catch(function () { return null; }); }))
      .then(function (res) {
        files = ((res[0] || {}).files) || [];
        groups = ((res[0] || {}).groups) || [];
        defaults = ((res[1] || {}).defaults) || {};
        gpu = res[2] || null;
        presets = (res[3] || {}).presets || null;
        presetFile = (res[3] || {}).file || "";
        meta = res[4] || null;
        render();
      });
  }

  /* ---- 模型与显存（原来挂在顶栏「设置」按钮里，现在统一收到这一页）----
     注意：老弹窗里那排 /api/models/<名字>/stop|start 按钮后端根本没有这个路由，
     点了只会 404。这里只放真实可用的两个：释放全部显存 / 恢复任务。 */
  function dot(on) {
    return '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;' +
      'vertical-align:middle;background:' + (on ? "#3f8a4a" : "#cfc7b8") + '"></span>';
  }

  function runtimeCard() {
    var g = gpu || {}, ms = g.models || {};
    var used = g.used_gb, total = g.total_gb;
    var pct = (total > 0 && used != null) ? Math.round(used / total * 100) : 0;
    var paused = !!g.game_paused;
    return '<div class="card" id="cfgRuntime"><h3 style="font-size:16px">🖥 模型与显存</h3>' +
      '<p class="note-gray" style="margin:0 0 10px">三个模型共用一张显卡，谁要干活谁加载，用完自动让位。</p>' +
      '<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">' +
      '<div style="flex:1;max-width:420px;height:10px;background:#f0ece4;border-radius:6px;overflow:hidden">' +
      '<div style="height:100%;width:' + pct + '%;background:linear-gradient(90deg,#c98a3f,#8a5a2b)"></div></div>' +
      '<b style="font-size:14px">' + (used == null ? "--" : used) + " / " +
      (total == null ? "--" : total) + " GB</b></div>" +
      '<p style="margin:6px 0 12px;font-size:13.5px">' +
      dot(ms.qwen) + "Qwen 写作　" + dot(ms.comfyui) + "ComfyUI 生图　" + dot(ms.h3) + "MiniMax H3 视频</p>" +
      '<div class="actions" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">' +
      (paused
        ? '<button class="btn primary" id="cfgResume">▶ 恢复任务</button>' +
          '<span class="note-gray" style="margin:0">显存已释放、任务暂停中</span>'
        : '<button class="btn" id="cfgFree">🎮 释放全部显存（暂停任务）</button>' +
          '<span class="note-gray" style="margin:0">和顶栏「游戏模式」是同一个开关，打完游戏回来点恢复接着跑</span>') +
      '<span style="flex:1"></span><button class="btn small" id="cfgGpuRefresh">↻ 刷新</button></div></div>';
  }

  function refreshGpu() {
    // 只换这一张卡片，不整页重画——否则右边正在改的指令词会被冲掉
    return window.api.get("/api/gpu").then(function (g) {
      gpu = g;
      var box = document.getElementById("cfgRuntime");
      if (box) { box.outerHTML = runtimeCard(); wire(); }
    }).catch(function () {});
  }

  function gpuAct(url, okMsg) {
    window.api.post(url, {}).then(function (d) {
      UI.toast(d && d.refired ? okMsg + "，被打断的那段已重新提交" : okMsg);
      refreshGpu();
    }).catch(function (e) { UI.toast("失败：" + e.message, "err"); });
  }

  function sel(id, opts, val) {
    return '<select id="' + id + '" style="width:100%;height:36px;padding:0 8px">' +
      (opts || []).map(function (o) {
        return '<option' + (String(o) === String(val) ? " selected" : "") + '>' + esc(o) + '</option>';
      }).join("") + '</select>';
  }

  function defaultsCard() {
    if (!meta) {
      return '<div class="card"><h3 style="font-size:16px">🌐 全局默认</h3>' +
        '<p class="note-gray">先在左侧选一个项目，这里才能读到世界/画风等选项。' +
        '（全局默认会在你新建项目时自动带入。）</p></div>';
    }
    var styles = (meta.styles || []).map(function (x) { return x.name || x; });
    var tends = meta.content_tendencies || [];
    return '<div class="card"><h3 style="font-size:16px">🌐 全局默认<span style="font-size:12px;color:#8b8375;font-weight:400">（新建项目时自动带入这些值）</span></h3>' +
      '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-top:8px">' +
      '<span>世界类型<br>' + sel("dfWorld", meta.world_types, defaults.world_type) + '</span>' +
      '<span>画风<br>' + sel("dfStyle", styles, defaults.style) + '</span></div>' +
      '<div style="margin-top:10px"><div style="font-size:14px;color:#5a5348;margin-bottom:4px">内容尺度（可多选）</div>' +
      '<div style="display:flex;gap:14px;flex-wrap:wrap">' + tends.map(function (t) {
        return '<label style="font-size:14px"><input type="checkbox" class="dfct" value="' + esc(t) + '"' +
          (((defaults.content_tendencies || []).indexOf(t) >= 0) ? " checked" : "") + '> ' + esc(t) + '</label>';
      }).join("") + '</div></div>' +
      '<div style="margin-top:10px"><label style="font-size:14px;color:#5a5348">自定义尺度（逐字遵守）</label>' +
      '<textarea id="dfScale" style="width:100%;height:64px;padding:8px 10px">' + esc(defaults.custom_content_scale || "") + '</textarea></div>' +
      '<div style="margin-top:8px"><label style="font-size:14px;color:#5a5348">不可更改的故事要求</label>' +
      '<textarea id="dfExtra" style="width:100%;height:56px;padding:8px 10px">' + esc(defaults.extra_requirements || "") + '</textarea></div>' +
      '<div style="text-align:right;margin-top:10px"><button class="btn primary" id="dfSave">💾 保存全局默认</button></div></div>';
  }

  /* 人物预设不再藏在“某份指令词 → 某个占位符”里面。这里直接列出用户日常会改的
     内容；编辑器和保存接口仍复用下面经过验证的通用预设表逻辑。 */
  function characterPresetCard() {
    var tabs = CHARACTER_PRESET_TABS.map(function (x) {
      var on = presetMode === "character" && presetKey === x[0];
      return '<button type="button" class="btn small" data-char-preset="' + esc(x[0]) + '" ' +
        'style="border-radius:12px;padding:6px 12px;' +
        (on ? 'background:#8a5a2b;color:#fff;border-color:#8a5a2b' : '') + '">' +
        esc(x[1]) + '</button>';
    }).join("");
    var body = (presetMode === "character" && presetKey)
      ? '<div id="ppEdit" style="margin-top:12px;border-top:1px solid #ece5da;padding-top:12px">' +
        presetEditor() + '</div>'
      : '<div class="note-gray" style="padding:16px 4px 4px">点上面一类开始修改。</div>';
    return '<div class="card" id="characterPresetCard" style="margin-top:16px">' +
      '<h3 style="font-size:16px">👤 人物预设</h3>' +
      '<p class="note-gray" style="margin:0 0 10px">这里就是人物卡和人物设定图实际读取的内容。' +
      '可以直接添加、删除、改名称和改描述；点“保存”后，下一次生成人物就会使用新内容。</p>' +
      '<div style="display:flex;gap:7px;flex-wrap:wrap">' + tabs + '</div>' + body + '</div>';
  }

  function redrawCharacterPreset() {
    var box = document.getElementById("characterPresetCard");
    if (box) { box.outerHTML = characterPresetCard(); wire(); }
  }

  /* ──────── 预设表：所有选项背后的原话，能看能改能加能删 ────────
     用户 2026-09-01：点哪个选项就显示内容，可以改、可以存、可以加可以删。
     左边选一张表，右边一项一行。存的是 presets/预设表.json，下次生成就生效。
     值有三种形状：列表(选项名)、字典(名→一段话)、字典套字典(男/女 各一套)，
     所以每个输入框带一条 data-pp 路径，保存时按路径塞回去。 */
  function presetNote(k) {
    return ((presets || {})["_每一项影响什么"] || {})[k] || "";
  }

  function ppRow(path, name, val) {
    return '<div class="pp-row" data-pp-row="' + esc(path) + '" style="display:grid;' +
      'grid-template-columns:150px 1fr auto;gap:8px;align-items:start;margin-bottom:8px">' +
      '<input class="pp-name" value="' + esc(name) + '" style="height:34px;padding:0 8px;font-size:14px">' +
      '<textarea class="pp-val" placeholder="留空＝这一项不设置" ' +
      'style="width:100%;height:60px;min-height:60px;' +
      'padding:8px 10px;font-size:13.5px;line-height:1.6">' + esc(val) + '</textarea>' +
      '<button class="btn small pp-del" type="button" style="height:34px">删除</button></div>';
  }

  function presetEditor() {
    if (!presets) {
      return '<div class="note-gray" style="padding:20px">读不到预设表文件。</div>';
    }
    if (!presetKey) {
      return '<div class="note-gray" style="padding:20px">从左边挑一张表，右边就能改。</div>';
    }
    var v = presets[presetKey], html = "";
    if (Object.prototype.toString.call(v) === "[object Array]") {
      /* 选项列表：一行一个名字，加删就是加行删行 */
      html = '<p class="note-gray" style="margin:0 0 8px">一行一个选项名。加了新名字，' +
        '记得到对应的「定义」表里加同名的一条。</p>' +
        '<textarea class="pp-list" style="width:100%;height:46vh;' +
        'padding:12px 14px;font-size:14px;line-height:1.9;font-family:ui-monospace,Consolas,monospace">' +
        esc(v.join("\n")) + '</textarea>';
    } else {
      var keys = Object.keys(v || {});
      var nested = keys.length && typeof v[keys[0]] === "object" && v[keys[0]] !== null;
      var body = "";
      if (nested) {
        /* 分两层的表：上层是选项（影片类型/视点…）或性别，下层是这一项的各个字段。
           所以要有两种「加」：加一整个新选项，和往某个选项里加一个字段。 */
        /* 男女两组是固定结构，只能往男/女里面加预设，不能把“男/女”本身删改成第三组。 */
        var fixedSexGroups = ["身材·每种的定义", "脸型·每种的定义", "发型·每种的定义"];
        if (fixedSexGroups.indexOf(presetKey) < 0) {
          body += '<button class="btn small pp-newopt" type="button" style="margin-bottom:6px">' +
            '＋ 加一个新分组</button>';
        }
        keys.forEach(function (g) {
          body += '<div style="margin:10px 0 4px;font-size:14px;font-weight:600;color:#8a5a2b">' +
            esc(g) + '</div><div class="pp-group" data-pp-group="' + esc(presetKey + "|" + g) + '">' +
            Object.keys(v[g] || {}).map(function (n) {
              return ppRow(presetKey + "|" + g + "|" + n, n, v[g][n]);
            }).join("") +
            '<button class="btn small pp-add" type="button" data-pp-add="' + esc(presetKey + "|" + g) +
            '">＋ 在「' + esc(g) + '」里加一项</button></div>';
        });
      } else {
        body = '<div class="pp-group" data-pp-group="' + esc(presetKey) + '">' +
          keys.map(function (n) { return ppRow(presetKey + "|" + n, n, v[n]); }).join("") +
          '<button class="btn small pp-add" type="button" data-pp-add="' + esc(presetKey) +
          '">＋ 加一项</button></div>';
      }
      html = '<p class="note-gray" style="margin:0 0 10px">左边是选项名，右边是模型真正收到的原话。' +
        '改完点保存，下次生成生效。</p><div style="max-height:52vh;overflow:auto;padding-right:6px">' +
        body + '</div>';
    }
    return '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">' +
      '<b style="font-size:15px">' + esc(presetKey === "人设图普通姿势·按性别" ? "普通人设图·姿势＋版式（按性别）" : presetKey) + '</b>' +
      '<span class="note-gray" style="margin:0;font-size:12.5px">' + esc(presetNote(presetKey)) + '</span>' +
      '<span style="flex:1"></span>' +
      '<button class="btn small" id="ppReload" type="button">↩ 放弃改动</button>' +
      '<button class="btn small primary" id="ppSave" type="button">💾 保存</button></div>' + html;
  }

  /* 把界面上的输入框收回成对象，再整份存回去 */
  function presetCollect() {
    var v = presets[presetKey];
    if (Object.prototype.toString.call(v) === "[object Array]") {
      var t = main.querySelector(".pp-list");
      if (!t) return null;
      var arr = t.value.split("\n").map(function (x) { return x.trim(); })
        .filter(function (x) { return x; });
      if (!arr.length) { UI.toast("选项列表不能全空", "err"); return null; }
      presets[presetKey] = arr;
      return presets;
    }
    var groups = main.querySelectorAll("[data-pp-group]");
    var built = {}, bad = "";
    [].forEach.call(groups, function (g) {
      var path = g.getAttribute("data-pp-group").split("|");
      var obj = {};
      [].forEach.call(g.querySelectorAll("[data-pp-row]"), function (r) {
        var n = (r.querySelector(".pp-name").value || "").trim();
        var val = r.querySelector(".pp-val").value || "";
        if (!n) return;                       /* 名字空了当这行没填，跳过 */
        if (obj[n] !== undefined) bad = n;    /* 同名会互相顶掉，先拦住 */
        obj[n] = val;
      });
      if (path.length === 1) built[path[0]] = obj;
      else { built[path[0]] = built[path[0]] || {}; built[path[0]][path[1]] = obj; }
    });
    if (bad) { UI.toast("有两项都叫「" + bad + "」，改个名再存", "err"); return null; }
    var top = built[presetKey];
    if (!top || !Object.keys(top).length) { UI.toast("这张表不能全空", "err"); return null; }
    presets[presetKey] = top;
    return presets;
  }

  function presetSave() {
    var body = presetCollect();
    if (!body) return;
    window.api.post("/api/presets/save", { presets: body }).then(function () {
      UI.toast("预设表已保存：" + presetKey + "（下次生成生效）");
      return reloadPresets();
    }).catch(function (e) { UI.toast("没存上：" + e.message, "err"); });
  }

  function reloadPresets() {
    return window.api.post("/api/presets/get", {}).then(function (d) {
      presets = (d || {}).presets || null;
      presetFile = (d || {}).file || "";
      var box = document.getElementById("ppEdit");
      if (box) { box.innerHTML = presetEditor(); wire(); }
    }).catch(function (e) { UI.toast(e.message, "err"); });
  }

  /* 左栏按链路分组：一组 = 工作流的一个阶段，组内顺序就是跑的顺序。
     原来只有「在用/其余」两堆，看不出谁先谁后、哪一份管哪一步。 */
  function fileList() {
    var row = function (f) {
      return '<div class="btn" data-file="' + esc(f.name) + '" style="display:block;' +
        'text-align:left;margin-bottom:3px;padding:7px 10px;line-height:1.35;' +
        (current === f.name ? 'background:#8a5a2b;color:#fff' : '') + '">' +
        '<span style="font-size:13.5px">' + esc(f.label || f.name) + '</span>' +
        (f.desc ? '<div style="font-size:11px;opacity:.72">' + esc(f.desc) + '</div>' : '') +
        '</div>';
    };
    var out = "", n = 0;
    (groups || []).forEach(function (g) {
      var items = files.filter(function (f) { return f.group === g; });
      if (!items.length) return;
      n += items.length;
      var off = g.indexOf("没在用") === 0;
      if (off) {
        out += '<details style="margin-top:8px"><summary style="cursor:pointer;' +
          'font-size:12.5px;color:#8b8375">' + esc(g) + '（' + items.length + '）</summary>' +
          '<div style="margin-top:5px">' + items.map(row).join("") + '</div></details>';
      } else {
        out += '<div style="font-size:12px;color:#8b8375;margin:10px 0 4px;' +
          'letter-spacing:.5px">' + esc(g) + '</div>' + items.map(row).join("");
      }
    });
    return out;
  }

  function render() {
    main.innerHTML = '<div style="max-width:1200px;margin:0 auto">' +
      runtimeCard() +
      defaultsCard() +
      characterPresetCard() +
      '<div class="card" style="margin-top:16px"><h3 style="font-size:16px">' +
      '📝 工作流指令词<span style="font-size:12px;color:#8b8375;font-weight:400">' +
      '（每一步发给模型的原话，连同它用到的预设，都在这里改）</span></h3>' +
      '<p class="note-gray" style="margin:0 0 10px">左边按跑的顺序分组，' +
      '点一步进去看它的原文和预设。指令词写前会自动备份到 presets/instructions/_bak/，' +
      '预设存前也会留 .bak。</p>' +
      '<div style="display:grid;grid-template-columns:250px 1fr;gap:16px">' +
      '<div id="cfgList" style="max-height:70vh;overflow:auto">' + fileList() + '</div>' +
      '<div id="cfgEdit">' + editorHtml() + '</div>' +
      '</div></div></div>';
    wire();
  }

  /* ── 编辑区：这一步干什么 → 用到哪些预设 → 指令词原文 ──
     用户 2026-09-01 定的形态：从一份指令词往下钻，就能看到并改掉
     它会用到的每一条预设，不用去另一个页面猜哪条影响哪一步。 */
  function curFile() {
    for (var i = 0; i < files.length; i++) {
      if (files[i].name === current) return files[i];
    }
    return null;
  }

  /* 按钮上写的就是指令词正文里那个占位符（【WORLD】），
     下面小字说它是什么——用户 2026-09-01：看按钮就知道对应正文哪一处。
     一个占位符可能带好几张表（【WORLD】带世界类型+世界补充，
     【CHARACTERS】带人物卡六张），点开之后再列它带的表。 */
  function presetChips() {
    var f = curFile();
    var list = (f && f.presets) || [];
    if (!list.length) {
      return '<div class="note-gray" style="margin:0 0 12px">' +
        '这份指令词是纯模板，正文里没有占位符，不带项目预设。</div>';
    }
    var cur = null;
    for (var i = 0; i < list.length; i++) {
      if (list[i].ph === presetPh) cur = list[i];
    }
    var chips = list.map(function (x) {
      var on = presetPh === x.ph;
      return '<button type="button" class="btn small" data-pp-ph="' + esc(x.ph) + '" ' +
        'style="border-radius:10px;padding:5px 11px;line-height:1.25;text-align:left;' +
        (on ? 'background:#8a5a2b;color:#fff;border-color:#8a5a2b' : '') + '">' +
        '<span style="font-family:ui-monospace,Consolas,monospace;font-size:12.5px">' +
        esc(x.ph) + '</span>' +
        '<div style="font-size:10.5px;opacity:.72">' + esc(x.note) + '</div></button>';
    }).join("");

    var body = "";
    if (cur) {
      /* 带多张表的占位符，先让用户挑一张 */
      if (cur.tables.length > 1) {
        body += '<div style="display:flex;gap:5px;flex-wrap:wrap;margin-bottom:9px">' +
          cur.tables.map(function (t) {
            return '<button type="button" class="btn small" data-pp-tab="' + esc(t) + '" ' +
              'style="border-radius:12px;padding:3px 10px;font-size:12px;' +
              (presetKey === t ? 'background:#6b4a26;color:#fff;border-color:#6b4a26' : '') +
              '">' + esc(t) + '</button>';
          }).join("") + '</div>';
      }
      body = '<div id="ppEdit" style="margin-top:11px;border-top:1px solid #ece5da;' +
        'padding-top:11px">' + body + presetEditor() + '</div>';
    }
    return '<div style="background:#faf7f1;border:1px solid #ece5da;border-radius:10px;' +
      'padding:10px 12px;margin-bottom:12px">' +
      '<div style="font-size:12.5px;color:#8b8375;margin-bottom:8px">' +
      '下面的方括号就是指令词正文里的占位符，生成时换成这些预设的内容——' +
      '点一个就能改，改完存盘，下次生成生效</div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap">' + chips + '</div>' +
      body + '</div>';
  }

  /* 只重画右边这块，并且把正在编辑的指令词原文原样接回去——
     否则用户改了一半的字会被这次重画冲掉（实测栽过）。 */
  function redrawEditor() {
    var box = document.getElementById("cfgEdit");
    if (!box) return;
    var t = document.getElementById("cfgText");
    var keep = t ? t.value : null;
    box.innerHTML = editorHtml();
    var t2 = document.getElementById("cfgText");
    if (t2 && keep !== null) {
      t2.value = keep;
      t2.oninput = function () { dirty = true; };
    }
    wire();
  }

  function editorHtml() {
    if (!current) {
      return '<div class="note-gray" style="padding:20px">' +
        '从左边挑一步开始。每一步都能看到它发给模型的原文，' +
        '以及它会带上哪些预设。</div>';
    }
    var f = curFile() || {};
    return '<div style="display:flex;align-items:baseline;gap:8px;margin-bottom:2px">' +
      '<b style="font-size:17px">' + esc(f.label || current) + '</b>' +
      (f.desc ? '<span style="font-size:13px;color:#6b6357">' + esc(f.desc) + '</span>' : '') +
      '</div>' +
      '<div style="font-size:11.5px;color:#a09786;margin-bottom:10px">' + esc(current) + '</div>' +
      presetChips() +
      '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">' +
      '<b style="font-size:14px">指令词原文</b>' +
      '<span class="note-gray" style="margin:0;font-size:12px">' +
      '发给模型的就是这些字；【WORLD】这类方括号会在生成时换成上面那些预设的内容</span>' +
      (dirty ? '<span style="font-size:12px;color:#a4552b">未保存</span>' : '') +
      '<span style="flex:1"></span>' +
      '<button class="btn small" id="cfgReload">↩ 放弃改动</button>' +
      '<button class="btn small primary" id="cfgSave">💾 保存</button></div>' +
      '<textarea id="cfgText" style="width:100%;height:52vh;padding:12px 14px;font-size:14px;' +
      'line-height:1.7;font-family:ui-monospace,Consolas,monospace">读取中…</textarea>';
  }

  function openFile(name) {
    if (dirty && !confirm("当前指令词有未保存的改动，切换会丢失。继续？")) return;
    current = name; dirty = false;
    presetMode = null; presetKey = null; presetPh = null;   // 换一步就把上一步展开的收起来
    render();
    window.api.get("/api/instructions/read?name=" + encodeURIComponent(name)).then(function (d) {
      var t = document.getElementById("cfgText");
      if (t) { t.value = (d && d.content) || ""; t.oninput = function () { dirty = true; }; }
    }).catch(function (e) { UI.toast(e.message, "err"); });
  }

  function saveFile() {
    var t = document.getElementById("cfgText");
    if (!t || !current) return;
    window.api.post("/api/instructions/save", { name: current, content: t.value })
      .then(function () { dirty = false; UI.toast("已保存：" + current + "（改动立即对下一次生成生效）"); render();
        window.api.get("/api/instructions/read?name=" + encodeURIComponent(current)).then(function (d) {
          var tt = document.getElementById("cfgText");
          if (tt) { tt.value = (d && d.content) || ""; tt.oninput = function () { dirty = true; }; }
        });
      })
      .catch(function (e) { UI.toast(e.message, "err"); });
  }

  function saveDefaults() {
    var val = function (id) { var e = document.getElementById(id); return e ? e.value : undefined; };
    var cts = [].map.call(main.querySelectorAll(".dfct:checked"), function (c) { return c.value; });
    window.api.post("/api/config/defaults/save", {
      world_type: val("dfWorld"), visual_strength: "自动", style: val("dfStyle"),
      content_tendencies: cts, custom_content_scale: val("dfScale"), extra_requirements: val("dfExtra")
    }).then(function (d) { defaults = (d && d.defaults) || {}; UI.toast("全局默认已保存，之后新建的项目会自动带入"); })
      .catch(function (e) { UI.toast(e.message, "err"); });
  }

  function wire() {
    [].forEach.call(main.querySelectorAll("[data-file]"), function (b) {
      b.onclick = function () { openFile(b.getAttribute("data-file")); };
    });
    var s = document.getElementById("cfgSave"); if (s) s.onclick = saveFile;
    var r = document.getElementById("cfgReload"); if (r) r.onclick = function () { dirty = false; openFile(current); };
    var ds = document.getElementById("dfSave"); if (ds) ds.onclick = saveDefaults;
    var fr = document.getElementById("cfgFree");
    if (fr) fr.onclick = function () { gpuAct("/api/gpu/pause", "模型已全部停止，显存已释放"); };
    var rs = document.getElementById("cfgResume");
    if (rs) rs.onclick = function () { gpuAct("/api/gpu/resume", "已恢复"); };
    var gr = document.getElementById("cfgGpuRefresh"); if (gr) gr.onclick = refreshGpu;

    /* ── 预设表 ── 选表 / 加 / 删 / 存 / 放弃。
       整块重画之后事件会掉，所以 wire() 每次都重新绑一遍。 */
    [].forEach.call(main.querySelectorAll("[data-char-preset]"), function (b) {
      b.onclick = function () {
        presetMode = "character";
        presetPh = null;
        presetKey = b.getAttribute("data-char-preset");
        /* 如果右侧指令词刚好展开了预设，先收起，确保页面只有一个编辑器。 */
        redrawEditor();
        redrawCharacterPreset();
      };
    });
    /* 占位符按钮：点开它带的表；带多张时默认展开第一张 */
    [].forEach.call(main.querySelectorAll("[data-pp-ph]"), function (b) {
      b.onclick = function () {
        var ph = b.getAttribute("data-pp-ph");
        if (presetPh === ph) { presetMode = null; presetPh = null; presetKey = null; }
        else {
          presetMode = "workflow";
          presetPh = ph;
          var f = curFile(), tabs = [];
          ((f && f.presets) || []).forEach(function (x) {
            if (x.ph === ph) tabs = x.tables;
          });
          presetKey = tabs[0] || null;
        }
        redrawCharacterPreset();
        redrawEditor();
      };
    });
    [].forEach.call(main.querySelectorAll("[data-pp-tab]"), function (b) {
      b.onclick = function () {
        presetMode = "workflow";
        var k = b.getAttribute("data-pp-tab");
        /* 再点一下同一个按钮＝收起来，别让页面越点越长 */
        presetKey = (presetKey === k) ? null : k;
        redrawEditor();
      };
    });
    [].forEach.call(main.querySelectorAll(".pp-del"), function (b) {
      b.onclick = function () {
        var row = b.closest("[data-pp-row]");
        if (!row) return;
        var nm = (row.querySelector(".pp-name") || {}).value || "这一项";
        /* 删了不马上落盘，点保存才真删——手滑了刷新一下就回来 */
        if (!confirm("删掉「" + nm + "」？点保存后才真正生效。")) return;
        row.parentNode.removeChild(row);
      };
    });
    [].forEach.call(main.querySelectorAll(".pp-add"), function (b) {
      b.onclick = function () {
        var g = b.closest("[data-pp-group]");
        if (!g) return;
        var d = document.createElement("div");
        d.innerHTML = ppRow(b.getAttribute("data-pp-add") + "|", "", "");
        var row = d.firstChild;
        g.insertBefore(row, b);
        wire();
        var nameBox = row.querySelector(".pp-name");
        if (nameBox) nameBox.focus();
      };
    });
    var no = main.querySelector(".pp-newopt");
    if (no) no.onclick = function () {
      var name = (prompt("新选项叫什么？（例：治愈日常、无人机航拍）") || "").trim();
      if (!name) return;
      var v = presets[presetKey] || {};
      var first = Object.keys(v)[0];
      if (v[name]) { UI.toast("已经有一个叫「" + name + "」的了", "err"); return; }
      /* 字段照第一个选项抄一份空的——不然用户不知道该填哪些字段 */
      var blank = {};
      Object.keys(v[first] || {}).forEach(function (f) { blank[f] = ""; });
      v[name] = blank;
      presets[presetKey] = v;
      var box = document.getElementById("ppEdit");
      if (box) { box.innerHTML = presetEditor(); wire(); }
      UI.toast("已加「" + name + "」，把内容填上再点保存");
    };
    var ps = document.getElementById("ppSave"); if (ps) ps.onclick = presetSave;
    var pr = document.getElementById("ppReload");
    if (pr) pr.onclick = function () { reloadPresets(); };
  }

  return {
    render: function (m, state) {
      main = m; st = state; current = null; dirty = false;
      presetMode = "character";
      presetKey = "人设图身体比例·每档的定义";
      presetPh = null;
      main.innerHTML = "读取中…";
      return load();
    }
  };
})();
