/* shell.js — 外壳与路由：顶栏/左导航/右检查器/底部任务条/日志面板/设置页。 */
(function () {
  var state = { storyId: localStorage.getItem("v43_story") || "" };
  /* 左栏分三段，用户定的信息架构：
     ① 跟着项目走——换个项目这五页的内容全变；
     ② 历史 / 设置——全局的，不跟项目；
     ③ 四个工具——不跟项目，随时点开就能玩。
     段与段之间画分界线，中段浮在中间、下段沉到底部上方（样式见 index.html #nav）。 */
  var NAV_GROUPS = [
    [
      { h: "#settings", t: "⚙️ 设定", d: "一句话 → 故事框架 + 第一话故事 + 人物设定图" },
      { h: "#story", t: "📖 剧本", d: "确认设定后，生成第一话剧本和场景图" },
      { h: "#timeline", t: "🎬 分镜视频", d: "镜头表 → 视频生成提示词 → 视频" },
      { h: "#assets", t: "📦 成品", d: "人物设定图、场景图、每话的分镜段视频（可选段合并）、合成的成片" },
      /* P321：「成品」并入「历史」（历史页的「已保存」筛选就是成品）；页面保留，只是不占左栏 */
    ],
    [
      { h: "#history", t: "🕘 历史", d: "所有工具出的图和视频，带提示词原文，可存进资源" },
      { h: "#config", t: "🛠 设置", d: "模型与显存 + 指令词 + 全局默认" }
    ],
    [
      // 并入的「本地模型工作台」四大模块（前后端原封不动嵌入，见 pages/legacy.js）
      { h: "#chat", t: "💬 问AI", d: "本地 Qwen 对话，可传图无审核识别" },
      { h: "#vchar", t: "🎭 虚拟人物", d: "长期虚拟人物聊天 + 人设图 / 头像生成" },
      { h: "#prompt", t: "🎨 出图词", d: "提示词生成 / 优化 / 反推，直接出图" },
      { h: "#director", t: "🎥 视频导演台", d: "MiniMax H3 单视频：文/图/首尾帧/全能参考" }
    ]
  ];
  var logFilter = "";
  var sagaProgress = null;
  var sagaLogs = [];
  var sagaStepKey = "";
  var runtimeTaskCount = 0;

  function route() {
    var h = location.hash || "#settings";
    var q = h.split("?")[1] || "";
    if (q.indexOf("story=") === 0) {
      state.storyId = q.split("story=")[1];
      localStorage.setItem("v43_story", state.storyId);
      var sel = document.getElementById("projectSelect");
      if (sel) sel.value = state.storyId;
    }
    var name = h.replace("#", "").split("?")[0] || "settings";
    if (name === "script") {
      // 旧“剧本”入口已经合并到时间线。保留 script.js 加载只为兼容旧代码，
      // 但用户打开旧书签时直接落到唯一的新写入口，避免两套数据继续分叉。
      location.replace("#timeline" + (q ? "?" + q : ""));
      return;
    }
    var REDIRECT = { project: "settings", "project-settings": "settings", 
                     create: "settings", video: "productions", review: "productions" };
    if (REDIRECT[name]) name = REDIRECT[name];
    if (name === "logs") {
      var lp = document.getElementById("logpanel");
      lp.style.display = lp.style.display === "block" ? "none" : "block";
      if (lp.style.display === "block") loadLogs();
      name = "settings";
    }
    var page = window.Pages && window.Pages[name];
    var main = document.getElementById("main");
    var links = document.querySelectorAll("#nav a[data-nav]");
    for (var i = 0; i < links.length; i++) {
      links[i].classList.toggle("on", links[i].getAttribute("data-nav") === name);
    }
    // 离开嵌入页时恢复 #main 的默认内边距/滚动（legacy 页会再自行加回）
    main.classList.remove("legacyframe");
    if (!page) {
      main.innerHTML = UI.empty("页面不存在：" + name);
      return;
    }
    /* 有的页面 render 忘了 return Promise——路由器不能因此崩掉，
       崩了整个左侧导航就是"点了没反应"。 */
    var pr = page.render(main, state);
    if (pr && typeof pr.catch === "function") {
      pr.catch(function (e) {
        main.innerHTML = UI.empty("加载失败：" + e.message, "重试", function () { route(); });
      });
    }
  }

  function renderNav() {
    var el = document.getElementById("nav");
    el.innerHTML = NAV_GROUPS.map(function (items) {
      return '<div class="navgroup">' + items.map(function (n) {
        return '<a href="' + n.h + '" data-nav="' + n.h.replace("#", "") +
          '" title="' + UI.esc(n.d || "") + '">' + n.t + "</a>";
      }).join("") + "</div>";
    }).join('<div class="navsep"></div>');
  }

  function loadStories(skipRoute) {
    /* skipRoute=true：只刷新顶部项目下拉，不重渲染当前页面。
       设定页保存设定时用——原来这里的 route() 会把整页重画，
       正在置灰计时的「全部生成」按钮被换成新按钮，用户以为点了没反应。 */
    return window.api.get("/api/stories").then(function (d) {
      var stories = d.stories || [];
      var sel = document.getElementById("projectSelect");
      if (!stories.length) {
        sel.innerHTML = '<option value="">（还没有故事项目）</option>';
        state.storyId = "";
        renderInspector();
        if (!skipRoute) route();
        return;
      }
      if (!state.storyId || !stories.some(function (s) { return s.story_id === state.storyId; })) {
        state.storyId = stories[0].story_id;
        localStorage.setItem("v43_story", state.storyId);
      }
      sel.innerHTML = stories.map(function (s) {
        return '<option value="' + s.story_id + '">' + UI.esc(s.title || s.story_id) + "</option>";
      }).join("");
      sel.value = state.storyId;
      renderInspector();
      if (!skipRoute) route();
    });
  }

  /* 右侧检查器整块删了（用户："这一栏没啥用"）。运行状态挪进顶栏进度条右边 */
  function renderInspector() {
    var box = document.getElementById("inspector");
    if (box) box.classList.add("hidden");
  }

  function loadGpu() {
    window.api.get("/api/gpu").then(function (d) {
      var el = document.getElementById("statusCapsule");
      if (el) {
        var used = d.used_gb, total = d.total_gb;
        el.textContent = "显存 " + (used == null ? "--" : used) + "/" +
          (total == null ? "--" : total) + "G ｜ Qwen" + (d.models.qwen ? "开" : "关") +
          " Comfy" + (d.models.comfyui ? "开" : "关") + " H3" + (d.models.h3 ? "开" : "关");
        el.className = "tbadge";
        if (total > 0) {
          var r = used / total;
          el.classList.add(r > 0.88 ? "hot" : r > 0.4 ? "busy" : "ok");
        }
      }
      // 游戏模式按钮的状态跟着后端旗子走
      var gb = document.getElementById("btnGame");
      if (gb) {
        gb.textContent = d.game_paused ? "▶ 恢复任务" : "🎮 游戏模式";
        gb.classList.toggle("hot", !!d.game_paused);
      }
      // 进度条吃**服务器**状态：不管是谁的窗口点的生成，所有打开的页面都能看到
      var t = d.current_task;
      /* 一个进度条只能有一个主人。saga 任务在跑的时候它已经在写
         「45%　3/8　剩余约 4 分」，这里再写「生成中：场1段2」就会和它交替覆盖，
         用户看到的是百分比和步骤名来回跳。saga 在跑就让位。 */
      if (t && window.UIActions && !window.__sagaBusy) {
        window.UIActions.progress.busy("生成中：场" + t.scene_no + "段" + t.seg_no);
        window.__srvBusy = true;
      } else if (window.__srvBusy && !window.__sagaBusy && window.UIActions) {
        window.UIActions.progress.done("");
        window.__srvBusy = false;
      }
    }).catch(function () {});
  }

  /* 游戏模式：一键杀掉三个模型、清干净显存、暂停任务；再点一下恢复并续跑 */
  function toggleGame() {
    var gb = document.getElementById("btnGame");
    var paused = gb && gb.textContent.indexOf("恢复") >= 0;
    var url = paused ? "/api/gpu/resume" : "/api/gpu/pause";
    gb.textContent = paused ? "恢复中…" : "释放显存中…";
    window.api.post(url, {}).then(function (d) {
      UI.toast(paused
        ? (d.refired ? "已恢复，被打断的那段重新提交了" : "已恢复")
        : "模型已全部停止，显存已释放。打完游戏点「恢复任务」");
      loadGpu();
    }).catch(function (e) { UI.toast("失败：" + e.message, "err"); loadGpu(); });
  }

  function fmtTime(ts) {
    var d = new Date(ts * 1000);
    function p(n) { return n < 10 ? "0" + n : "" + n; }
    return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  }

  function fmtRemain(seconds) {
    if (seconds == null || !isFinite(+seconds)) return "计算中";
    var s = Math.max(0, Math.round(+seconds));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    function p(n) { return n < 10 ? "0" + n : "" + n; }
    return h ? h + ":" + p(m) + ":" + p(sec) : p(m) + ":" + p(sec);
  }

  function updateTaskCount() {
    var n = runtimeTaskCount + (sagaProgress && sagaProgress.running ? 1 : 0);
    var el = document.getElementById("btnTasks");
    if (el) el.textContent = "任务 " + n;
  }

  function rememberSagaStep(j) {
    if (!j || !j.step) return;
    var key = [j.running, j.step, j.done, j.total, j.err].join("|");
    if (key === sagaStepKey) return;
    sagaStepKey = key;
    sagaLogs.push({
      ts: +j.updated_at || +j.server_time || Date.now() / 1000,
      level: j.err ? "error" : "info",
      msg: (j.running ? "当前步骤：" : "任务状态：") + j.step +
        (j.total > 0 ? "（" + (+j.done || 0) + "/" + j.total + "）" : "") +
        (j.err ? "；" + j.err : "")
    });
    if (sagaLogs.length > 100) sagaLogs.splice(0, sagaLogs.length - 100);
  }

  var sagaSweep = 0, sagaRunningId = "";
  function loadSagaProgress() {
    // 任务可能在另一个项目里启动，切到别的故事后顶部也必须继续显示。
    // P297：原来每 2.5 秒把**全部**项目都问一遍（180 个项目 ≈ 每秒 70 个请求，把服务压得页面自己的请求都排队）。
    // 改成：当前项目 + 上次看到在跑的项目每次都问；全部项目每 12 次（30 秒）扫一遍。
    var all = [].map.call(document.querySelectorAll("#projectSelect option"),
                          function (o) { return o.value; }).filter(Boolean);
    var ids = (sagaSweep++ % 12 === 0) ? all.slice() : [];
    [state.storyId, sagaRunningId].forEach(function (sid) { if (sid && ids.indexOf(sid) < 0) ids.unshift(sid); });
    if (!ids.length) return Promise.resolve();
    return Promise.all(ids.map(function (sid) {
      return window.api.post("/api/saga/" + sid + "/progress", {}).then(function (j) {
        j = j || {}; j._storyId = sid; return j;
      }).catch(function () { return {_storyId: sid}; });
    })).then(function (jobs) {
      sagaProgress = jobs.filter(function (j) { return j.running; })[0] ||
        jobs.filter(function (j) { return j._storyId === state.storyId; })[0] || {};
      sagaRunningId = sagaProgress.running ? (sagaProgress._storyId || "") : "";
      rememberSagaStep(sagaProgress);
      updateTaskCount();
      if (!window.UIActions) return;
      /* 取消按钮：有任务在跑才露出来。点了发 /cancel，这个项目的设定页任务、
         分镜页任务、正在生的 H3 一起停；已生成的部分保留（用户 2026-09-02 定）。 */
      var gc = document.getElementById("gCancel");
      if (gc) {
        gc.hidden = !sagaProgress.running;
        gc.onclick = function () {
          var sid = sagaProgress._storyId || state.storyId;
          if (!sid) return;
          if (!window.confirm("取消这个项目正在跑的生成？已经出来的部分会保留。")) return;
          gc.disabled = true; gc.textContent = "取消中…";
          window.api.post("/api/saga/" + sid + "/cancel", {}).then(function () {
            if (window.UI && window.UI.toast) window.UI.toast("已发出取消，当前这一步停下后任务结束");
          }).catch(function (e) {
            if (window.UI && window.UI.toast) window.UI.toast("取消失败：" + e.message, "err");
          }).then(function () { gc.disabled = false; gc.textContent = "⏹ 取消"; });
        };
      }
      if (sagaProgress.running) {
        window.__sagaBusy = true;
        var done = +sagaProgress.done || 0, total = +sagaProgress.total || 0;
        if (total > 0) {
          var pct = Math.max(0, Math.min(100, Math.round(done / total * 100)));
          /* 百分比、剩余时间和当前在干什么，必须**同时**出现在一行。
             以前步骤名由设定页另写一次，两个轮询交替覆盖，用户看到的是
             「45%　3/8　剩余约4分」和「画人物：张凡」来回跳。现在只有这一处写，
             一次把三样都写全。 */
          var note = pct + "%　" + done + "/" + total + "　剩余约 " +
            fmtRemain(sagaProgress.eta_seconds) +
            (sagaProgress.step ? "　·　" + sagaProgress.step : "");
          window.UIActions.progress.run(done / total, note);
        } else {
          // 兼容升级前已经启动的任务：没有计数就如实显示步骤，不伪造百分比。
          window.UIActions.progress.busy("生成中 · " + (sagaProgress.step || "处理中"));
        }
      } else if (window.__sagaBusy) {
        window.__sagaBusy = false;
        if (sagaProgress.err) window.UIActions.progress.stuck("失败 · " + sagaProgress.err);
        else window.UIActions.progress.done("100%　已完成");
      }
    }).catch(function () {});
  }

  function loadTasks() {
    window.api.get("/api/tasks/active").then(function (d) {
      var act = d.active || [];
      runtimeTaskCount = act.length;
      updateTaskCount();
      var bar = document.getElementById("taskbar");
      if (!act.length) { bar.style.display = "none"; return; }
      var t = act[0];
      var done = t.done || 0, total = t.total || 0;
      var pct = total > 0 ? Math.round(done / total * 100) : 0;
      bar.style.display = "flex";
      bar.innerHTML =
        '<span><b>' + UI.esc(t.title || t.id) + "</b></span>" +
        UI.badge(t.status) +
        '<span>' + UI.esc(t.step_label || t.detail || "") + "</span>" +
        '<div class="bar"><div class="fill" style="width:' + pct + '%"></div></div>' +
        "<span>" + pct + "%</span>" +
        (t.status === "paused"
          ? '<button class="btn small" id="tbResume">继续</button>'
          : '<button class="btn small" id="tbPause">暂停</button>') +
        '<button class="btn small" id="tbCancel">取消</button>';
      document.getElementById("tbPause").onclick = function () { actTask("pause"); };
      var rs = document.getElementById("tbResume");
      if (rs) rs.onclick = function () { actTask("resume"); };
      document.getElementById("tbCancel").onclick = function () { actTask("cancel"); };
    }).catch(function () {});
  }

  function actTask(act) {
    window.api.get("/api/tasks/active").then(function (d) {
      var t = (d.active || [])[0];
      if (!t) return;
      return window.api.post("/api/tasks/" + t.id + "/" + act);
    }).then(function () { loadTasks(); loadLogs(); });
  }

  function loadLogs() {
    if (document.getElementById("logpanel").style.display !== "block") return;
    var logStoryId = (sagaProgress || {})._storyId || state.storyId;
    var sagaReq = logStoryId
      ? window.api.post("/api/saga/" + logStoryId + "/progress", {}).catch(function () { return {}; })
      : Promise.resolve({});
    Promise.all([
      window.api.get("/api/logs?filter=" + encodeURIComponent(logFilter)), sagaReq
    ]).then(function (rows) {
      var d = rows[0] || {}, sj = rows[1] || {};
      var merged = (d.logs || []).concat(sj.logs || [], sagaLogs);
      var seen = {};
      merged = merged.filter(function (e) {
        var k = String(e.ts) + "|" + e.level + "|" + e.msg;
        if (seen[k]) return false;
        seen[k] = true;
        if (logFilter === "error") return e.level === "error";
        if (logFilter === "image") return /画人物|画场景|生图|krea|comfy/i.test(e.msg || "");
        return true;
      }).sort(function (a, b) { return (+a.ts || 0) - (+b.ts || 0); });
      var list = document.getElementById("loglist");
      list.innerHTML = merged.slice(-120).map(function (e) {
        var cls = e.level === "error" ? " log-error" : "";
        return '<div class="log-row' + cls + '"><span>' + fmtTime(e.ts) + "</span> [" +
          UI.esc(e.level || "info") + "] " + UI.esc(e.msg) + "</div>";
      }).join("<br>") || "（暂无日志）";
      list.scrollTop = list.scrollHeight;
    }).catch(function () {});
  }

  /* 顶栏那颗「设置」按钮和它的弹窗已删：内容统一收进左栏「🛠 设置」页（pages/config.js）。
     顺带修掉一个老毛病——弹窗里那排 /api/models/<名字>/stop|start 后端没有对应路由，
     点了一直是 404，新页面只保留真实可用的释放/恢复。 */

  function doSearch(q) {
    if (!q) return;
    window.api.get("/api/search?q=" + encodeURIComponent(q)).then(function (d) {
      var rows = d.rows || [];
      UI.openModal("<h3>搜索结果：" + UI.esc(d.q) + "（" + rows.length + "）</h3>" +
        (rows.length
          ? rows.map(function (r) {
              return '<div class="card"><b>' + UI.badge(r.kind) + UI.esc(r.title) +
                "</b><p>" + UI.esc(r.sub || "") + "</p><p class='sub'>" + UI.esc(r.id) + "</p></div>";
            }).join("")
          : UI.empty("没有匹配结果")));
    }).catch(function (e) { alert(e.message); });
  }

  function boot() {
    renderNav();
    document.getElementById("projectSelect").onchange = function () {
      state.storyId = this.value;
      localStorage.setItem("v43_story", state.storyId);
      renderInspector(); route();
    };
    /* 「+ 新建」直接建空项目并跳到设定页（用户定的）：
       不弹框问一句话——一句话就写在设定页第一个框里，那才是它该在的地方。 */
    document.getElementById("btnNew").onclick = function () {
      var b = this;
      b.disabled = true;
      window.api.post("/api/stories", { one_line: "" }).then(function (d) {
        state.storyId = d.story.story_id;
        localStorage.setItem("v43_story", state.storyId);
        return loadStories();
      }).then(function () {
        location.hash = "#settings";
        route();
        b.disabled = false;
        UI.toast("新项目已建。写下这一话想发生什么，再按需要填写「你想怎么拍」。");
        // 光标直接落进一句话框，省一次点击
        setTimeout(function () {
          var one = document.getElementById("plIdea");
          if (one) { one.focus(); one.scrollIntoView({ block: "center" }); }
        }, 600);
      }).catch(function (e) {
        b.disabled = false;
        UI.toast("建项目失败：" + e.message, "err");
      });
    };
    document.getElementById("btnTasks").onclick = function () { toggleLog(); };
    document.getElementById("btnLogs").onclick = function () { toggleLog(); };
    document.getElementById("btnGame").onclick = function () { toggleGame(); };
    document.getElementById("searchBox").addEventListener("keydown", function (e) {
      if (e.key === "Enter") doSearch(this.value);
    });
    document.addEventListener("keydown", function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        document.getElementById("searchBox").focus();
      }
    });
    document.getElementById("logpanel").querySelectorAll(".filt button").forEach(function (b) {
      b.onclick = function () {
        document.querySelectorAll("#logpanel .filt button").forEach(function (x) { x.classList.remove("on"); });
        b.classList.add("on");
        logFilter = b.getAttribute("data-f");
        loadLogs();
      };
    });
    window.addEventListener("hashchange", function () {
      renderInspector(); route(); loadSagaProgress();
    });
    window.AppState = state;
    loadStories();
    loadGpu();
    loadTasks();
    loadSagaProgress();
    setInterval(loadGpu, 5000);
    setInterval(loadTasks, 3000);
    setInterval(loadSagaProgress, 2500);
    setInterval(loadLogs, 3000);
  }

  function toggleLog() {
    var p = document.getElementById("logpanel");
    p.style.display = p.style.display === "block" ? "none" : "block";
    if (p.style.display === "block") loadLogs();
  }

  document.addEventListener("DOMContentLoaded", boot);

  /* 给别的页面用：改了项目名后刷新顶部下拉。 */
  window.Shell = window.Shell || {};
  window.Shell.reloadStories = loadStories;
})();


/* 体检红绿灯（2026-09-03）：正文/剧本/提示词三层机械检查，绿了往下走，红了看 detail。 */
window.UIHealth = {
  fetch: function (sid, ep) {
    return window.api.post("/api/saga/" + sid + "/health", { ep: ep || 1 });
  },
  html: function (data) {
    var secs = [["正文", "story"], ["剧本", "pictures"], ["提示词", "prompts"]];
    var h = '<div style="font-size:13px;line-height:1.7">';
    secs.forEach(function (sc) {
      var rows = (data || {})[sc[1]] || [];
      var bad = rows.filter(function (r) { return !r.ok; }).length;
      h += '<div style="margin:6px 0 2px;font-weight:600">' + sc[0] + (bad ? ' <span style="color:#c0392b">' + bad + ' 项红</span>' : ' <span style="color:#27ae60">全绿</span>') + '</div>';
      rows.forEach(function (r) {
        h += '<div>' + (r.ok ? '🟢' : '🔴') + ' ' + r.name + '：<span style="color:#666">' + (r.detail || '') + '</span></div>';
      });
    });
    return h + '</div>';
  },
  show: function (sid, ep) {
    var box = document.getElementById("healthBox");
    if (!box) {
      box = document.createElement("div");
      box.id = "healthBox";
      box.style.cssText = "position:fixed;right:16px;bottom:16px;width:360px;max-height:70vh;overflow:auto;background:#fff;border:1px solid #ddd;border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.15);padding:10px 12px;z-index:9999";
      document.body.appendChild(box);
    }
    box.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center"><b>🩺 体检</b><button class="btn small" onclick="document.getElementById(\'healthBox\').remove()">关</button></div><div style="color:#888">检查中…</div>';
    return window.UIHealth.fetch(sid, ep).then(function (r) {
      box.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center"><b>🩺 体检</b><button class="btn small" onclick="document.getElementById(\'healthBox\').remove()">关</button></div>' + window.UIHealth.html(r);
    }).catch(function (e) { box.innerHTML += '<div style="color:#c0392b">' + (e.message || e) + '</div>'; });
  }
};
