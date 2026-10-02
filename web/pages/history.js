/* pages/history.js — 🕘 历史：各工具生成的图片/视频汇到一处，看提示词、存进项目。P402：服务端翻页，一页 48 条。 */
window.Pages = window.Pages || {};
window.Pages.history = (function () {
  var st = null, items = [], filter = "all", srcFilter = "", includeRaw = false, main = null;
  var PAGE = 48, page = 0, total = 0, sources = [];
  function esc(s) { return UI.esc(s); }

  function load() {
    var url = "/api/history/list?limit=" + PAGE + "&offset=" + (page * PAGE) +
      (includeRaw ? "&include_raw=1" : "") +
      (filter === "image" || filter === "video" ? "&kind=" + filter : "") +
      (filter === "saved" ? "&saved=1" : "") +
      (srcFilter ? "&source=" + encodeURIComponent(srcFilter) : "") +
      (st.storyId ? "&story_id=" + encodeURIComponent(st.storyId) : "");
    main.innerHTML = '<div class="card">读取中…</div>';
    return window.api.get(url).then(function (d) {
      items = (d && d.items) || [];
      total = (d && d.total) || items.length;
      sources = (d && d.sources) || [];
      render();
    }).catch(function (e) {
      main.innerHTML = UI.empty("历史加载失败：" + e.message, "重试", function () { load(); });
    });
  }

  function fmtTime(ts) {
    var d = new Date((ts || 0) * 1000);
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return (d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }

  function media(it) {
    if (it.kind === "video") {
      return '<video src="' + esc(it.url) + '" controls preload="none" style="width:100%;aspect-ratio:16/9;background:#000;border-radius:8px"></video>';
    }
    return '<a href="' + esc(it.url) + '" target="_blank" rel="noopener">' +
      '<img src="' + esc(it.url) + '" loading="lazy" style="width:100%;aspect-ratio:16/9;object-fit:contain;border-radius:8px;background:#f3f0ea"></a>';
  }

  function card(it, i) {
    var ex = it.extra || {};
    var bits = [ex.mode, ex.size, ex.seconds ? ex.seconds + "秒" : ""].filter(Boolean).join(" · ");
    var srcTag = '<span style="font-size:12px;color:#8b8375">' + esc(it.source || "") + ' · ' + fmtTime(it.ts) +
      (bits ? " · " + esc(bits) : "") + (it.saved ? ' · <b style="color:#3a7d3a">已保存</b>' : '') + '</span>';
    var peek = String(it.prompt || "").replace(/\s+/g, " ").slice(0, 22);
    var promptBox = it.prompt
      ? '<details style="margin-top:6px"><summary style="cursor:pointer;font-size:13px;color:#5a5348">提示词 <span style="color:#a49a89;font-size:12px">' +
        esc(peek) + (it.prompt.length > 22 ? "…" : "") + '</span></summary>' +
        '<div style="white-space:pre-wrap;font-size:12px;line-height:1.6;color:#5a5348;margin-top:4px;max-height:160px;overflow:auto">' + esc(it.prompt) + '</div></details>'
      : '<div style="font-size:12px;color:#b3ab9c;margin-top:6px">（无提示词记录）</div>';
    return '<div class="card" id="hist' + i + '" style="padding:10px">' + media(it) +
      '<div style="margin-top:6px">' + srcTag + '</div>' + promptBox +
      '<div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap">' +
      (it.prompt ? '<button class="btn small" data-copy="' + i + '">📋 复制提示词</button>' : '') +
      '<button class="btn small primary" data-save="' + i + '"' + (it.saved ? ' disabled' : '') + '>' +
      (it.saved ? '✓ 已存到项目' : '💾 保存到项目') + '</button></div></div>';
  }

  function srcChips() {
    if (sources.length < 2) return "";
    var btn = function (val, label) {
      return '<button class="btn small" data-src="' + esc(val) + '"' + (srcFilter === val ? ' style="background:#8a5a2b;color:#fff"' : "") + ">" + esc(label) + "</button>";
    };
    return '<span style="width:100%;display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:6px">' +
      '<span style="font-size:12px;color:#8b8375">来源</span>' + btn("", "全部") + sources.map(function (s) { return btn(s, s); }).join("") + '</span>';
  }

  function pager() {
    var pages = Math.max(1, Math.ceil(total / PAGE));
    if (pages <= 1) return "";
    return '<div style="display:flex;gap:8px;align-items:center;justify-content:center;margin:14px 0">' +
      '<button class="btn small" id="hprev"' + (page <= 0 ? " disabled" : "") + '>← 上一页</button>' +
      '<span style="font-size:13px;color:#5a5348">第 ' + (page + 1) + ' / ' + pages + ' 页</span>' +
      '<button class="btn small" id="hnext"' + (page >= pages - 1 ? " disabled" : "") + '>下一页 →</button></div>';
  }

  function render() {
    var chip = function (key, label) {
      return '<button class="btn small" data-filter="' + key + '"' + (filter === key ? ' style="background:#8a5a2b;color:#fff"' : '') + '>' + label + '</button>';
    };
    main.innerHTML = '<div style="max-width:1200px;margin:0 auto">' +
      '<div class="card" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">' +
      '<h3 style="font-size:16px;margin:0">🕘 生成历史</h3><span style="flex:1"></span>' +
      chip("all", "全部") + chip("image", "图片") + chip("video", "视频") + chip("saved", "已保存") +
      '<label style="font-size:13px;color:#5a5348;margin-left:8px"><input type="checkbox" id="hraw"' + (includeRaw ? " checked" : "") + '> 包含所有原始出图</label>' +
      '<button class="btn small" id="hrefresh">🔄 刷新</button>' +
      '<span style="font-size:12px;color:#8b8375;width:100%">共 ' + total + ' 项，每页 ' + PAGE + ' 项（人设·场景·分镜·出图词·虚拟人物·视频导演台 出的图和视频，带提示词原文；视频点播放才加载）</span>' +
      srcChips() + '</div>' +
      (items.length
        ? '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px;margin-top:12px">' + items.map(card).join("") + '</div>' + pager()
        : UI.empty("这一页没有内容。去出图/出视频，或勾选「包含所有原始出图」")) +
      '</div>';
    wire();
  }

  function wire() {
    [].forEach.call(main.querySelectorAll("[data-filter]"), function (b) { b.onclick = function () { filter = b.getAttribute("data-filter"); page = 0; load(); }; });
    [].forEach.call(main.querySelectorAll("[data-src]"), function (b) { b.onclick = function () { srcFilter = b.getAttribute("data-src"); page = 0; load(); }; });
    var raw = document.getElementById("hraw"); if (raw) raw.onchange = function () { includeRaw = raw.checked; page = 0; load(); };
    var rf = document.getElementById("hrefresh"); if (rf) rf.onclick = function () { load(); };
    var pv = document.getElementById("hprev"); if (pv) pv.onclick = function () { if (page > 0) { page--; load(); window.scrollTo(0, 0); } };
    var nx = document.getElementById("hnext"); if (nx) nx.onclick = function () { page++; load(); window.scrollTo(0, 0); };
    [].forEach.call(main.querySelectorAll("[data-copy]"), function (b) {
      b.onclick = function () {
        var it = items[+b.getAttribute("data-copy")];
        try { navigator.clipboard.writeText(it.prompt); UI.toast("提示词已复制"); } catch (e) { UI.toast("复制失败，手动选中吧"); }
      };
    });
    [].forEach.call(main.querySelectorAll("[data-save]"), function (b) {
      b.onclick = function () {
        var it = items[+b.getAttribute("data-save")];
        if (!st.storyId) { UI.toast("先选一个项目再保存", "err"); return; }
        b.disabled = true;
        var ex = it.extra || {};
        window.api.post("/api/history/save", { story_id: st.storyId, path: it.path, owner_kind: ex.owner_kind || "", owner_id: ex.owner_id || "" })
          .then(function () { it.saved = true; UI.toast("已保存到项目" + (ex.owner_id ? "（已挂到 " + (ex.name || ex.owner_id) + "）" : "")); render(); })
          .catch(function (e) { b.disabled = false; UI.toast(e.message, "err"); });
      };
    });
  }

  return {
    render: function (m, state) { main = m; st = state; page = 0; return load(); }
  };
})();
