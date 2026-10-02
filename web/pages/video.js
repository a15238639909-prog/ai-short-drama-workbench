/* pages/video.js — 视频页（Phase 5）：镜头列表 / 播放与 Take / 参考与连贯性检查 / 拼接轨道。 */
window.Pages = window.Pages || {};
window.Pages.video = {
  render: function (main, st) {
    if (!st.storyId) {
      main.innerHTML = UI.empty("先在顶部选择一个故事项目");
      return Promise.resolve();
    }
    return window.api.get("/api/video/plan?story_id=" + st.storyId).then(function (d) {
      var shots = d.shots || [];
      var refs = (d.ref_plan && d.ref_plan[0] && d.ref_plan[0].pictures) || [];
      var videos = d.videos || [];
      var latest = videos[0];
      var seqIds = (d.ref_plan || []).map(function (s) { return s.sequence_id; });
      var takes = d.takes || {};
      main.innerHTML = "<h2>视频</h2>" +
        '<div style="display:grid;grid-template-columns:250px 1fr 300px;gap:12px;align-items:start">' +
        '<div id="vdLeft"></div><div id="vdMid"></div><div id="vdRight"></div></div>' +
        '<div id="vdBottom" style="margin-top:14px"></div>';
      document.getElementById("vdLeft").innerHTML = "<h3>镜头列表（" + shots.length + "）</h3>" +
        (shots.length ? shots.map(function (s, i) {
          return UI.card("Shot " + UI.esc(s.shot_id || ("S" + String(i + 1).padStart(2, "0"))),
            "<p>" + UI.badge(s.framing || "") + " " + UI.esc(s.duration || "") + "s</p>" +
            "<p>" + UI.esc((s.narrative_purpose || "").slice(0, 40)) + "</p>" +
            "<p>" + UI.badge("queued") + "未开始</p>");
        }).join("") : UI.empty("还没有分镜，去剧本页生成导演方案"));
      document.getElementById("vdMid").innerHTML = "<h3>播放器与 Take</h3>" +
        (latest ? '<video src="/files/' + UI.esc((latest.files && latest.files[0] || "").replace(/\\/g, "/")) +
          '" controls style="width:100%;max-height:300px;background:#000;border-radius:10px"></video>' +
          "<p class='sub'>" + UI.esc(latest.production_id) + "（已有成片，Phase 0–8 不发起生成）</p>" +
          (latest.files && latest.files[0]
            ? '<button class="btn primary" id="adoptTake">采用本 Take</button> ' : "") +
          '<button class="btn" id="newTake">生成新 Take</button> ' +
          '<button class="btn" id="stitchBtn">拼接预览</button>'
          : UI.empty("还没有视频成品", "去成品页查看已有成片", function () { location.hash = "#productions"; })) +
        '<div id="takeRes" style="margin-top:8px"></div>';
      document.getElementById("vdRight").innerHTML = "<h3>参考与连贯性检查</h3>" +
        (refs.length
          ? UI.card("参考素材（" + refs.length + " 张）", refs.map(function (r) {
              return "<p>• " + UI.esc(r.role || "") + "：" + UI.esc(r.natural_name || r.source || "") +
                "<br><span class='sub'>" + UI.esc(r.purpose || "") + "</span></p>";
            }).join(""))
          : UI.empty("该故事还没有参考计划（先跑文字链）")) +
        '<div id="contBox"></div><div id="shotNotes"></div>';
      document.getElementById("vdBottom").innerHTML = "<h3>成片拼接轨道</h3>" +
        '<div style="display:flex;gap:8px;flex-wrap:wrap">' + (seqIds.length ? seqIds.map(function (s) {
          var adopted = takes[s];
          return '<span style="background:#fff;border:1px solid #e6e0d6;border-radius:10px;padding:6px 12px;font-size:12.5px">' +
            UI.esc(s) + " " + UI.badge(adopted ? "adopted" : "queued") + "</span>";
        }).join("") : UI.empty("还没有 Sequence")) +
        '<button class="btn" id="addSeq">添加 Sequence</button></div>';
      document.getElementById("newTake").onclick = function () {
        document.getElementById("takeRes").innerHTML = '<p style="color:#8a6a3b">Phase 0–8 不发起生成；素材轮启用后这里可生成新 Take。</p>';
      };
      document.getElementById("addSeq").onclick = function () {
        document.getElementById("takeRes").innerHTML = '<p style="color:#8a6a3b">新 Sequence 由剧本扩展产生（Phase 7/素材轮）。</p>';
      };
      var adoptBtn = document.getElementById("adoptTake");
      if (adoptBtn) adoptBtn.onclick = function () {
        var seqId = seqIds[0] || "SEQ_001";
        var vpath = (latest.files && latest.files[0]) || "";
        window.api.post("/api/video/take/adopt", {
          story_id: st.storyId, sequence_id: seqId, take_id: "TAKE_00001", path: vpath
        }).then(function () {
          document.getElementById("takeRes").innerHTML = "<p style='color:#2e7d32'>已采用 " + seqId + "（状态账本已提交）。</p>";
        }).catch(function (e) { alert(e.message); });
      };
      document.getElementById("stitchBtn").onclick = function () {
        document.getElementById("takeRes").innerHTML = "<p>正在拼接已有成片…</p>";
        window.api.post("/api/video/stitch", { story_id: st.storyId }).then(function (r) {
          document.getElementById("takeRes").innerHTML =
            "<p style='color:#2e7d32'>拼接完成：" + r.merged.path + "（" + r.merged.duration + "s）</p>" +
            '<video src="/files/' + UI.esc(r.merged.path.replace(/\\/g, "/")) +
            '" controls style="width:100%;max-height:260px;background:#000;border-radius:10px"></video>';
        }).catch(function (e) { document.getElementById("takeRes").innerHTML = "<p style='color:#c62828'>" + UI.esc(e.message) + "</p>"; });
      };
      var cont = document.getElementById("contBox");
      var prom = seqIds.length
        ? window.api.get("/api/video/continuity/" + seqIds[0]).then(function (c) {
            cont.innerHTML = UI.card("连贯性检查（文字层）", c.checks.map(function (x) {
              return "<p>" + (x.ok ? "✅" : "⚠️") + " " + UI.esc(x.item) + "：" + UI.esc(x.detail) + "</p>";
            }).join(""), "结论来自文字比对，不调用视觉模型");
          })
        : Promise.resolve();
      return prom.then(function () {
        var notes = document.getElementById("shotNotes");
        if (shots.length) {
          notes.innerHTML = UI.card("镜头运动备注", shots.slice(0, 5).map(function (s) {
            return "<p>" + UI.esc(s.shot_id || "") + " ｜ " + UI.esc(s.framing || "") +
              " ｜ " + UI.esc(s.camera_movement || "") + " ｜ " + UI.esc(s.camera_position || "") + "</p>";
          }).join(""));
        }
      });
    });
  }
};
