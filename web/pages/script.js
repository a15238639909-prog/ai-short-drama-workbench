/* pages/script.js — 🎬 剧本分镜栏
   顶部选集（来自 📖 故事栏的分集）→ 左场次 / 中剧本 / 右分镜。
   一屏之内看得懂：导演的专业字段收进折叠，不占主界面。 */
window.Pages = window.Pages || {};

(function () {
  var esc = function (s) { return window.UI.esc(s); };
  var sel = { ep: null, scene: 0 };

  function badge(text, tone) {
    var c = { done: "#218838", doing: "#b5762e", idle: "#8b8375" }[tone] || "#8b8375";
    return '<span style="font-size:12px;color:' + c + ';border:1px solid ' + c +
      ';border-radius:8px;padding:1px 8px;white-space:nowrap">' + esc(text) + "</span>";
  }

  function thumb(label, w) {
    return '<div style="width:' + (w || "100%") + ';aspect-ratio:16/9;background:#f3f0ea;' +
      'border-radius:8px;display:flex;align-items:center;justify-content:center;' +
      'color:#8b8375;font-size:12px;flex:none">' + esc(label) + "</div>";
  }

  function fold(title, body) {
    return '<details style="margin-top:8px"><summary style="cursor:pointer;color:#8a5a2b;' +
      'font-size:12px">' + esc(title) + '</summary><pre style="white-space:pre-wrap;' +
      'font-size:12px;line-height:1.6;background:#f7f5f0;border-radius:8px;padding:12px;' +
      'margin:8px 0 0;max-height:320px;overflow:auto">' + esc(body || "（这一步还没有生成内容）") +
      "</pre></details>";
  }

  /* 把一个 Shot 渲染成人能读的一行，而不是 JSON dump */
  function shotRow(s, i) {
    var g = function (k) { return s[k] || ""; };
    var detail = [
      ["机位", [g("camera_position"), g("camera_height"), g("camera_angle")].filter(Boolean).join(" · ")],
      ["镜头", [g("optics"), g("camera_distance"), g("depth_of_field")].filter(Boolean).join(" · ")],
      ["运镜", [g("movement_path"), g("movement_speed"), g("movement_end_point")].filter(Boolean).join(" → ")],
      ["画面", [g("foreground"), g("midground"), g("background")].filter(Boolean).join(" / ")],
      ["表演", [g("A_action"), g("A_performance"), g("B_action"), g("B_performance")].filter(Boolean).join("；")],
      ["光线", [g("dominant_light"), g("subject_light_side")].filter(Boolean).join(" · ")],
      ["同期声", g("audio")],
      ["切换理由", g("cut_reason")]
    ].filter(function (x) { return x[1]; })
      .map(function (x) { return x[0] + "：" + x[1]; }).join("\n");

    return '<div class="card" style="padding:12px;display:grid;grid-template-columns:80px 1fr;' +
      'gap:12px;margin-bottom:12px;align-items:start">' +
      thumb(g("shot_id") || ("S" + (i + 1)), "80px") +
      "<div><div style='display:flex;align-items:center;gap:8px;flex-wrap:wrap'>" +
      "<b style='font-size:14px'>" + esc(g("duration") || "?") + "s</b>" +
      badge(g("framing") || "未定景别", "idle") + "</div>" +
      "<div style='color:#8b8375;font-size:13px;line-height:1.6;margin-top:4px'>" +
      esc(g("narrative_purpose") || "（没写这一镜要干什么）") + "</div>" +
      (detail ? fold("展开这一镜的导演信息", detail) : "") +
      "</div></div>";
  }

  function render(main, st) {
    if (!st.storyId) { main.innerHTML = window.UI.empty("先在顶部选择一个故事项目"); return; }

    var m = /ep=(\d+)/.exec(location.hash.split("?")[1] || "");
    if (m) sel.ep = parseInt(m[1], 10);

    Promise.all([
      window.api.post("/api/story/body", { story_id: st.storyId }).catch(function () { return {}; }),
      window.api.get("/api/script?story_id=" + st.storyId).catch(function () { return {}; }),
      window.api.get("/api/state/" + st.storyId).catch(function () { return {}; }),
      window.api.get("/api/chain/view?story_id=" + st.storyId).catch(function () { return {}; })
    ]).then(function (r) {
      var eps = r[0].episodes || [], d = r[1] || {}, sd = r[2] || {}, cv = r[3] || {};
      var units = (d.episode && d.episode.units) || [];
      var previs = d.previs || {};
      var shots = previs.shots || [];
      var scriptTxt = previs.story_script || "";
      var sheets = (cv.production_sheet || {}).sheets || [];
      if (sel.ep == null && eps.length) sel.ep = eps[0].no;
      if (sel.scene >= units.length) sel.scene = 0;

      var tone = shots.length ? "done" : (scriptTxt ? "doing" : "idle");
      var toneText = shots.length ? "分镜完成" : (scriptTxt ? "脚本完成" : "未开始");
      var total = shots.reduce(function (a, s) { return a + (+s.duration || 0); }, 0);

      main.innerHTML =
        '<div style="max-width:1680px;margin:0 auto">' +
        "<h2>🎬 剧本分镜</h2>" +
        "<div class='guide'>这一页只管某一集的剧本和分镜。故事和分集在 📖 故事，项目设定在 ⚙️ 设定，保存的素材在 🗂 资源。</div>" +

        '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:16px">' +
        "<span class='sub'>当前集：</span>" +
        (eps.length ? eps.map(function (e) {
          return '<button class="btn' + (e.no === sel.ep ? " primary" : "") + '" data-ep="' + e.no + '">' +
            esc(e.title || ("第" + e.no + "集")) + "</button>";
        }).join("") : "<span class='note-gray'>📖 故事栏还没有分集，先去分集</span>") +
        "</div>" +

        '<div style="display:grid;grid-template-columns:260px 1fr 340px;gap:16px;align-items:start">' +

        "<div><h3 style='font-size:16px;margin:0 0 12px'>场次</h3>" +
        (units.length ? units.map(function (u, i) {
          var on = i === sel.scene;
          return '<div class="card" data-scene="' + i + '" style="padding:12px;cursor:pointer;margin-bottom:12px;' +
            (on ? "box-shadow:inset 0 0 0 2px #8a5a2b" : "") + '">' +
            thumb("Scene " + (i + 1)) +
            "<div style='margin:8px 0 6px;font-size:14px;line-height:1.5'>" +
            esc((u.purpose || "未命名场次").slice(0, 24)) + "</div>" +
            badge(toneText, tone) + "</div>";
        }).join("") : window.UI.empty("还没有场次")) + "</div>" +

        "<div><h3 style='font-size:16px;margin:0 0 12px'>这一场的剧本</h3>" +
        '<div class="card">' + (scriptTxt
          ? "<pre style='white-space:pre-wrap;font-size:14px;line-height:1.8;margin:0'>" +
            esc(scriptTxt) + "</pre>" + fold("查看将发给模型的提示词", (sheets[0] || {}).prompt)
          : window.UI.empty("还没有剧本。先在 📖 故事 写好这一集讲什么，再回来生成")) +
        window.UIActions.row({ id: "script", hasContent: !!scriptTxt, dirty: false,
          labels: { generate: "✨ 按这一集生成剧本", expand: "✦ 把这场写细",
                    regenerate: "🔄 重写这场", save: "💾 保存剧本" } }) + "</div>" +

        '<div class="card" style="margin-top:16px"><b style="font-size:14px">对白</b>' +
        (cv.dialogue && cv.dialogue.length
          ? "<div style='margin-top:8px'>" + cv.dialogue.map(function (x) {
              return "<div style='display:grid;grid-template-columns:88px 1fr;gap:8px;" +
                "padding:6px 0;border-bottom:1px solid #f3f0ea;font-size:14px;line-height:1.6'>" +
                "<b>" + esc(x.speaker) + "</b><span>" + esc(x.line || "") + "</span></div>";
            }).join("") + "</div>"
          : "<div class='note-gray' style='margin-top:8px'>本场没有对白（纯动作场）</div>") + "</div>" +

        '<div class="card" style="margin-top:16px"><b style="font-size:14px">进场 / 出场状态</b>' +
        "<div style='margin-top:8px;display:flex;flex-wrap:wrap;gap:8px'>" +
        (sd.lines && sd.lines.length
          ? sd.lines.map(function (l) { return '<span class="chip">' + esc(l) + "</span>"; }).join("")
          : "<span class='note-gray'>还没有状态账本</span>") + "</div></div></div>" +

        "<div><h3 style='font-size:16px;margin:0 0 12px'>这一场的分镜" +
        "<span style='font-size:13px;font-weight:400;color:" +
        (total > 15 ? "#c0392b" : "#218838") + "'>　总时长 " + total.toFixed(1) + "s" +
        (total > 15 ? "（超 15 秒，要拆段）" : "") + "</span></h3>" +
        window.UIActions.row({ id: "shots", hasContent: !!shots.length, dirty: false,
          labels: { generate: "🎬 按剧本生成分镜", expand: "➕ 多加一个镜头",
                    regenerate: "🔄 换一种拍法", save: "💾 保存分镜" } }) +
        (shots.length ? shots.map(shotRow).join("") : window.UI.empty("还没有分镜")) +
        "</div></div></div>";

      var H = {
        generate: function (id) {
          var url = id === "shots" ? "/previs" : "/preview";
          return window.api.post("/api/story/" + st.storyId + url, { episode_no: sel.ep })
            .then(function () { render(main, st); })
            .catch(function (e) { alert(e.message); });
        },
        expand: function (id) {
          return window.api.post("/api/create/expand",
            { story_id: st.storyId, target: id, episode_no: sel.ep })
            .then(function (r) { alert(r.note || "已扩写"); render(main, st); })
            .catch(function (e) { alert(e.message); });
        },
        regenerate: function (id) {
          if (!confirm("重新生成会覆盖当前内容，确定？")) return;
          return H.generate(id);
        },
        save: function () {
          alert("剧本与分镜随生成即存；手改后的保存在下一轮接入编辑器时启用");
        }
      };
      window.UIActions.bind(main, H);

      [].forEach.call(main.querySelectorAll("[data-ep]"), function (b) {
        b.onclick = function () { sel.ep = +b.getAttribute("data-ep"); render(main, st); };
      });
      [].forEach.call(main.querySelectorAll("[data-scene]"), function (b) {
        b.onclick = function () { sel.scene = +b.getAttribute("data-scene"); render(main, st); };
      });
    });
  }

  window.Pages.script = { render: render };
})();
