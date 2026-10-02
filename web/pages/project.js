/* pages/project.js — 项目页节点图（Phase 2）。
节点全部来自 /api/project/graph（真实数据）；拖动位置与备注经 /api/node/<id> 持久化。 */
window.Pages = window.Pages || {};
window.Pages.project = (function () {
  var W = 250, H = 168;
  var state = { tx: 20, ty: 20, zoom: 0.9, mode: "pan", selected: null, edgesOn: true, nodes: [], edges: [] };

  function nodeCard(n) {
    var b = UI.badge(n.status);
    var ver = n.version ? '<span class="ver">' + UI.esc(n.version) + "</span>" : "";
    var html = "";
    if (n.kind === "project") {
      html = "<h3>" + UI.esc(n.title) + "</h3>" + b +
        "<p>" + UI.esc(n.summary) + "</p><p class='sub'>" + UI.esc(n.story_id) + "</p>";
    } else if (n.kind === "character" || n.kind === "scene_asset") {
      html = (n.cover
        ? '<img src="/files/' + UI.esc(n.cover) + '" style="width:100%;height:86px;object-fit:cover;border-radius:8px">'
        : '<div style="width:100%;height:86px;background:#f7f5f0;border-radius:8px;display:flex;align-items:center;justify-content:center;color:#aaa;font-size:12px">暂无采用图</div>') +
        "<h3 style='margin-top:6px'>" + UI.esc(n.title) + "</h3>" + b + ver +
        "<p>" + UI.esc(n.summary) + "</p>";
    } else if (n.kind === "scene") {
      html = "<h3>" + UI.esc(n.title) + "</h3>" + b +
        "<p>" + UI.esc(n.summary) + "</p><p class='sub'>" + UI.esc(n.extra.unit_id || "") + "</p>";
    } else {
      var media = "";
      if (n.kind === "video" && n.extra.path) {
        media = '<video src="/files/' + UI.esc(n.extra.path.replace(/\\/g, "/")) +
          '" controls style="width:100%;height:76px;border-radius:8px;background:#000"></video>';
      } else if (n.thumbs.length) {
        media = '<div style="display:flex;gap:4px;overflow:hidden">' + n.thumbs.slice(0, 4).map(function (t) {
          return '<img src="/files/' + UI.esc(t) + '" style="height:60px;flex:1;object-fit:cover;border-radius:6px">';
        }).join("") + "</div>";
      } else {
        media = '<div style="height:40px;color:#aaa;font-size:12px">无预览文件</div>';
      }
      html = "<h3>" + UI.badge(n.kind) + UI.esc(n.title) + "</h3>" + b + ver + media +
        "<p class='sub'>" + UI.esc(n.summary) + "</p>";
    }
    return '<div class="gnode" data-id="' + n.id + '" style="left:' + n.x + "px;top:" + n.y +
      "px;width:" + W + "px;min-height:" + H + 'px">' + html +
      (n.note ? '<div class="gnote">' + UI.esc(n.note) + "</div>" : "") + "</div>";
  }

  function renderInspector(n, storyId) {
    var box = document.getElementById("inspector");
    box.classList.remove("hidden");
    var childKinds = { character: 0, scene_asset: 0, scene: 0, comic: 0, video: 0, novel: 0, album: 0 };
    state.nodes.forEach(function (x) { if (x.parent_id === n.id) childKinds[x.kind]++; });
    var relCount = Object.keys(childKinds).reduce(function (a, k) { return a + childKinds[k]; }, 0);
    box.innerHTML =
      "<h4>选中节点</h4>" +
      '<div class="kv"><b>' + UI.esc(n.title) + "</b></div>" +
      '<div class="kv">类型：' + UI.esc(n.kind) + " ｜ " + UI.badge(n.status) + UI.esc(n.version || "") + "</div>" +
      '<div class="kv">关联节点数：' + relCount + "</div>" +
      '<div class="kv" id="nodeNote">备注：' + UI.esc(n.note || "（无）") + "</div>" +
      "<h4>快捷操作</h4>" +
      '<button class="btn small" id="qOpen">打开</button> ' +
      '<button class="btn small" id="qLink">复制链接</button> ' +
      '<button class="btn small" id="qMove">移动</button> ' +
      '<button class="btn small" id="qNote">标注</button> ' +
      '<button class="btn small" id="qChild">新建下级</button> ' +
      '<button class="btn small" disabled title="待补">复制</button> ' +
      '<button class="btn small" disabled title="待补">删除</button>';
    document.getElementById("qOpen").onclick = function () { location.hash = "#" + n.page; };
    document.getElementById("qLink").onclick = function () {
      var url = location.origin + "/#" + n.page + "?node=" + n.id;
      (navigator.clipboard ? navigator.clipboard.writeText(url) : Promise.resolve()).then(function () {
        alert("已复制节点链接");
      });
    };
    document.getElementById("qMove").onclick = function () { state.mode = "move"; };
    document.getElementById("qNote").onclick = function () {
      UI.openModal("<h3>节点备注</h3><textarea id='noteBox' style='width:100%;height:90px'>" +
        UI.esc(n.note || "") + "</textarea><p><button class='btn primary' id='noteSave'>保存</button></p>");
      document.getElementById("noteSave").onclick = function () {
        var note = document.getElementById("noteBox").value;
        window.api.post("/api/node/" + n.id, { story_id: storyId, note: note }).then(function () {
          n.note = note;
          UI.closeModal();
          renderGraph();
          renderInspector(n, storyId);
        }).catch(function (e) { alert(e.message); });
      };
    };
    document.getElementById("qChild").onclick = function () {
      if (n.kind === "project") { location.hash = "#create"; return; }
      alert("该类型的下级节点由对应模块管理（人物/场景→资产页，Scene→剧本页，成品→创作页）");
    };
  }

  function renderGraph() {
    var stage = document.getElementById("cstage");
    stage.innerHTML = state.nodes.map(nodeCard).join("");
    var svg = document.getElementById("edgeSvg");
    var maxX = 40, maxY = 40;
    state.nodes.forEach(function (n) {
      maxX = Math.max(maxX, n.x + W + 40);
      maxY = Math.max(maxY, n.y + H + 40);
    });
    svg.setAttribute("viewBox", "0 0 " + maxX + " " + maxY);
    svg.setAttribute("width", maxX);
    svg.setAttribute("height", maxY);
    var byId = {};
    state.nodes.forEach(function (n) { byId[n.id] = n; });
    svg.innerHTML = state.edges.map(function (e) {
      var a = byId[e.from], b = byId[e.to];
      if (!a || !b) return "";
      var x1 = a.x + W / 2, y1 = a.y + H, x2 = b.x + W / 2, y2 = b.y;
      return '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 +
        '" stroke="#cfc7b8" stroke-width="2"></line>' +
        (state.edgesOn ? '<text x="' + ((x1 + x2) / 2) + '" y="' + ((y1 + y2) / 2 - 4) +
          '" font-size="10" fill="#999">↓</text>' : "");
    }).join("");
    var wrap = document.getElementById("cwrap");
    wrap.style.transform = "translate(" + state.tx + "px," + state.ty + "px) scale(" + state.zoom + ")";
    wrap.style.transformOrigin = "0 0";
    document.getElementById("zoomLabel").textContent = Math.round(state.zoom * 100) + "%";
  }

  function wire(main, storyId) {
    var stage = document.getElementById("cstage");
    var wrap = document.getElementById("cwrap");
    var drag = null;
    function moveNode(id, x, y) {
      var n = state.nodes.find(function (z) { return z.id === id; });
      if (n) { n.x = Math.max(0, x); n.y = Math.max(0, y); }
    }
    stage.addEventListener("pointerdown", function (e) {
      var node = e.target.closest(".gnode");
      if (node) {
        if (state.mode !== "pan") {
          var n = state.nodes.find(function (z) { return z.id === node.getAttribute("data-id"); });
          if (n) {
            state.selected = n;
            renderInspector(n, storyId);
            stage.querySelectorAll(".gnode").forEach(function (x) { x.classList.remove("sel"); });
            node.classList.add("sel");
            drag = { id: n.id, sx: e.clientX, sy: e.clientY, ox: n.x, oy: n.y, moved: false };
          }
        }
        return;
      }
      if (state.mode === "pan" || state.mode === "box") {
        drag = { pan: true, sx: e.clientX, sy: e.clientY, tx: state.tx, ty: state.ty };
      }
    });
    window.addEventListener("pointermove", function (e) {
      if (!drag) return;
      if (drag.pan) {
        state.tx = drag.tx + (e.clientX - drag.sx);
        state.ty = drag.ty + (e.clientY - drag.sy);
        wrap.style.transform = "translate(" + state.tx + "px," + state.ty + "px) scale(" + state.zoom + ")";
      } else if (drag.id) {
        var dx = (e.clientX - drag.sx) / state.zoom, dy = (e.clientY - drag.sy) / state.zoom;
        moveNode(drag.id, drag.ox + dx, drag.oy + dy);
        drag.moved = true;
        renderGraph();
      }
    });
    window.addEventListener("pointerup", function () {
      if (drag && drag.id) {
        var n = state.nodes.find(function (z) { return z.id === drag.id; });
        window.api.post("/api/node/" + drag.id, { story_id: storyId, x: Math.round(n.x), y: Math.round(n.y) })
          .catch(function (e) { console.warn(e); });
      }
      drag = null;
    });
    document.getElementById("cwrap").addEventListener("wheel", function (e) {
      e.preventDefault();
      var z = state.zoom * (e.deltaY < 0 ? 1.12 : 0.89);
      state.zoom = Math.max(0.25, Math.min(2.5, z));
      renderGraph();
    }, { passive: false });
    var tools = {
      tPan: function () { state.mode = "pan"; },
      tSel: function () { state.mode = "select"; },
      tEdge: function () { state.edgesOn = !state.edgesOn; renderGraph(); },
      tNote: function () {
        if (!state.selected) { alert("先点选一个节点"); return; }
        renderInspector(state.selected, storyId);
        document.getElementById("qNote").click();
      },
      tBox: function () { state.mode = "box"; },
      tIn: function () { state.zoom = Math.min(2.5, state.zoom * 1.15); renderGraph(); },
      tOut: function () { state.zoom = Math.max(0.25, state.zoom / 1.15); renderGraph(); },
      tFit: function () {
        if (!state.nodes.length) return;
        var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        state.nodes.forEach(function (n) {
          minX = Math.min(minX, n.x); minY = Math.min(minY, n.y);
          maxX = Math.max(maxX, n.x + W); maxY = Math.max(maxY, n.y + H);
        });
        var availW = main.clientWidth - 60, availH = main.clientHeight - 90;
        state.zoom = Math.max(0.25, Math.min(1.2, Math.min(availW / (maxX - minX + 60), availH / (maxY - minY + 60))));
        state.tx = 20 - minX * state.zoom;
        state.ty = 20 - minY * state.zoom;
        renderGraph();
      }
    };
    Object.keys(tools).forEach(function (id) {
      var b = document.getElementById(id);
      if (b) b.onclick = tools[id];
    });
  }

  return {
    render: function (main, st) {
      if (!st.storyId) {
        main.innerHTML = UI.empty("先在顶部选择一个故事项目");
        return Promise.resolve();
      }
      return window.api.get("/api/project/graph?story_id=" + st.storyId).then(function (g) {
        state.nodes = g.nodes || [];
        state.edges = g.edges || [];
        state.tx = 20; state.ty = 20; state.zoom = 0.9; state.selected = null;
        main.innerHTML =
          "<h2>项目</h2>" +
          '<div id="canvasWrap" style="position:relative;height:' + (main.clientHeight - 40) +
          'px;border:1px solid #e6e0d6;border-radius:12px;overflow:hidden;background:#fff">' +
          '<div id="toolbar" style="position:absolute;left:10px;top:10px;z-index:5;display:flex;gap:4px;background:#fff;border:1px solid #e6e0d6;border-radius:10px;padding:6px">' +
          '<button class="btn small" id="tPan">抓手</button><button class="btn small" id="tSel">选择</button>' +
          '<button class="btn small" id="tEdge">连线</button><button class="btn small" id="tNote">便签</button>' +
          '<button class="btn small" id="tBox">框选</button><button class="btn small" id="tIn">放大</button>' +
          '<button class="btn small" id="tOut">缩小</button><button class="btn small" id="tFit">适配</button></div>' +
          '<div id="zoomLabel" style="position:absolute;right:12px;bottom:10px;z-index:5;font-size:12px;color:#999;background:#fff;border:1px solid #e6e0d6;border-radius:6px;padding:2px 8px">100%</div>' +
          '<div id="cwrap" style="position:absolute;left:0;top:0;z-index:1;transform-origin:0 0">' +
          '<svg id="edgeSvg" style="position:absolute;left:0;top:0;pointer-events:none"></svg>' +
          '<div id="cstage" style="position:absolute;left:0;top:0"></div></div></div>';
        wire(main, st.storyId);
        renderGraph();
        var proj = state.nodes.find(function (n) { return n.kind === "project"; });
        if (proj) renderInspector(proj, st.storyId);
      });
    }
  };
})();
