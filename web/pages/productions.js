/* pages/productions.js — 🎞 成品栏：视频（单个/拼接/测试/成品）+ 漫画 + 小说，每条七个动作。 */
window.Pages = window.Pages || {};
window.Pages.productions = (function () {
  var kind = "video", vclass = "all";
  function esc(s) { return UI.esc(s); }
  function render(main, st) {
    var url = "/api/productions?story_id=" + st.storyId + (kind === "all" ? "" : "&kind=" + kind);
    /* v2（用户定的）：视频画廊优先。时间轴里已生成的段视频直接列出来能看能放——
       原来这页只认"成品登记"，段视频一条都不显示，整页永远空着。 */
    var tlP = kind === "video"
      ? window.api.post("/api/timeline/" + st.storyId + "/get", {}).catch(function () { return {}; })
      : Promise.resolve({});
    return Promise.all([window.api.get(url), tlP]).then(function (rs) {
      var d = rs[0] || {}, tl = ((rs[1] || {}).timeline || {});
      var segCards = "";
      (tl.scenes || []).forEach(function (sc) {
        (sc.segments || []).forEach(function (g) {
          if (!g.video) return;
          segCards += '<div class="pcard">' +
            '<video src="/files/' + esc(String(g.video).replace(/\\/g, "/")) + '" controls preload="metadata"></video>' +
            '<div class="pcard-h"><b>P' + (g.no < 10 ? "0" + g.no : g.no) + "</b>" +
            '<span>' + Math.round(g.seconds || 0) + "s · " + esc(g.size || "") + "</span>" +
            (g.video_hd ? '<span class="hd">有高清版</span>' : "") +
            '<span style="flex:1"></span>' +
            '<a href="#timeline" style="font-size:12px;color:#8a5a2b">去分镜页 →</a></div></div>';
        });
      });
      var items = d.items || [];
      if (kind === "video" && vclass !== "all") items = items.filter(function (p) { return p.video_class === vclass; });
      main.innerHTML = '<div style="max-width:1680px;margin:0 auto"><h2 style="font-size:20px">🎞 成品</h2>' +
        '<div style="display:flex;gap:8px;margin-bottom:16px">' +
        [["video", "视频"], ["comic", "漫画"], ["novel", "小说"]].map(function (t) {
          return '<button class="btn small ' + (kind === t[0] ? "primary" : "") + '" data-k="' + t[0] + '">' + t[1] + "</button>";
        }).join("") + "</div>" +
        (kind === "video" && segCards
          ? '<h3 class="asec">🎬 本篇的段视频（分镜页生成的）</h3><div class="pgrid">' + segCards + "</div>"
          : "") +
        (items.length ? '<h3 class="asec" style="margin-top:22px">📦 成品记录</h3>' +
          '<div class="pgrid">' + items.map(function (p) {
          var first = (p.files || [])[0] || "";
          var rel = first.replace(/\\/g, "/").split("v41/")[1] || "";
          var media = "";
          if (String(p.kind) === "video" && first) {
            media = '<video src="/files/' + esc(rel) + '" controls preload="metadata"></video>';
          } else if (String(p.kind) === "comic") {
            var thumbs = (p.files || []).filter(function (f) { return /\.png$/i.test(f); }).slice(0, 3);
            media = '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:4px;padding:8px">' + thumbs.map(function (f) {
              return '<img src="/files/' + esc(f.replace(/\\/g, "/").split("v41/")[1]) + '" style="width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:6px">';
            }).join("") + "</div>";
          }
          return '<div class="pcard">' + media +
            '<div class="pcard-h"><b>' + esc(p.production_id) + "</b>" +
            (p.video_class ? '<span>' + esc(p.video_class) + "</span>" : "") +
            (p.output && p.output.duration ? '<span>' + Math.round(p.output.duration) + "s</span>" : "") +
            '<span style="flex:1"></span>' +
            '<details class="amore"><summary>⋯</summary><div class="amore-in">' +
            '<button class="btn small" data-folder="' + esc(p.production_id) + '">打开文件夹</button>' +
            '<button class="btn small" data-prompt="' + esc(p.production_id) + '">查看Prompt</button>' +
            '<button class="btn small" data-ref="' + esc(p.production_id) + '">查看输入参考</button>' +
            '<button class="btn small" data-export="' + esc(p.production_id) + '">导出</button>' +
            "</div></details></div></div>";
        }).join("") + "</div>"
          : (segCards ? "" : UI.empty("当前项目还没有这类成品。视频在 🎬 剧本分镜 页生成后会出现在这里"))) + "</div>";
      main.querySelectorAll("[data-k]").forEach(function (b) { b.onclick = function () { kind = b.getAttribute("data-k"); vclass = "all"; render(main, st); }; });
      main.querySelectorAll("[data-vc]").forEach(function (b) { b.onclick = function () { vclass = b.getAttribute("data-vc"); render(main, st); }; });
      main.querySelectorAll("[data-open]").forEach(function (b) { b.onclick = openAction; });
      main.querySelectorAll("[data-file]").forEach(function (b) {
        b.onclick = function () {
          var f = b.getAttribute("data-file");
          if (f) window.api.get("/api/open-folder?path=" + encodeURIComponent(f));
        };
      });
      main.querySelectorAll("[data-folder]").forEach(function (b) {
        b.onclick = function () { window.api.post("/api/production/" + b.getAttribute("data-folder") + "/open_folder", {}).catch(function (e) { alert(e.message); }); };
      });
      main.querySelectorAll("[data-prompt]").forEach(function (b) { b.onclick = detailAction("prompt"); });
      main.querySelectorAll("[data-ref]").forEach(function (b) { b.onclick = detailAction("ref"); });
      main.querySelectorAll("[data-task]").forEach(function (b) { b.onclick = detailAction("task"); });
      main.querySelectorAll("[data-export]").forEach(function (b) {
        b.onclick = function () {
          window.api.post("/api/production/" + b.getAttribute("data-export") + "/export", {}).then(function (r) { alert("已导出：" + r.files.join("\n")); }).catch(function (e) { alert(e.message); });
        };
      });
    });
  }
  function detailAction(which) {
    return function (e) {
      var key = "data-" + (which === "prompt" ? "prompt" : (which === "ref" ? "ref" : "task"));
      var pid = e.currentTarget.getAttribute(key);
      window.api.get("/api/production/" + pid).then(function (d) {
        var text = which === "prompt" ? ((d.manifest && d.manifest.prompt) || d.production.prompt_ref || "该记录未保存提示词原文")
          : which === "ref" ? JSON.stringify((d.manifest && d.manifest.reference_pack_ids) || d.production.reference_ref || "该记录未保存参考信息", null, 1)
          : (d.production.task_id || "该记录未绑定任务 ID");
        UI.openModal("<h3>" + which + "</h3><pre style='white-space:pre-wrap;font-size:12px'>" + esc(String(text)) + "</pre>");
      });
    };
  }
  function openAction(e) {
    var pid = e.currentTarget.getAttribute("data-open");
    window.api.get("/api/production/" + pid).then(function (d) {
      var p = d.production, f = (p.files || [])[0] || "";
      if (String(p.kind) === "video" && f) {
        UI.openModal("<h3>" + esc(pid) + "</h3><video src='/files/" + esc(f.replace(/\\/g, "/").split("v41/")[1]) + "' controls style='width:100%;max-height:70vh'></video>");
      } else {
        UI.openModal("<h3>" + esc(pid) + "</h3><pre style='white-space:pre-wrap;font-size:12px'>" + esc(JSON.stringify(p, null, 1)) + "</pre>");
      }
    });
  }
  return {
    render: function (main, st) {
      if (!st.storyId) {
        main.innerHTML = UI.empty("先在顶部选择一个故事项目");
        return Promise.resolve();
      }
      return render(main, st);
    }
  };
})();
