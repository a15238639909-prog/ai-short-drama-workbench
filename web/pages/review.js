/* pages/review.js — 验收与追溯 + 问题中心（Phase 8）。 */
window.Pages = window.Pages || {};
window.Pages.review = {
  render: function (main, state) {
    if (!state.storyId) {
      main.innerHTML = UI.empty("先在顶部选择一个故事项目");
      return Promise.resolve();
    }
    return window.api.get("/api/textchain/" + state.storyId).then(function (d) {
      var v = d.validation || {}, checks = v.checks || [];
      var trace = d.trace || [];
      main.innerHTML = "<h2>验收与追溯</h2>" +
        UI.card("文字链验收：<b>" + UI.esc(v.result || "—") + "</b>（" + checks.length + " 项，规则唯一来源 cores/text_checks.py）",
          '<table><tr><th>检查项</th><th>结果</th><th>说明</th></tr>' + checks.map(function (c) {
            return "<tr><td>" + UI.esc(c.item) + "</td><td>" +
              (c.ok ? UI.badge("PASS") : (c.skipped ? UI.badge("candidate") : UI.badge("FAIL"))) +
              "</td><td>" + UI.esc(String(c.detail || "").slice(0, 70)) + "</td></tr>";
          }).join("") + "</table>");
      main.innerHTML += "<h3>来源链</h3>" + trace.map(function (t) {
        return UI.card("<b>" + UI.esc(t.stage) + "</b>", "<p class='sub'>" + UI.esc(t.source || "") +
          "</p><p>" + UI.esc(String(t.content || "").slice(0, 160)) + "</p>");
      }).join("");
      main.innerHTML += '<div id="pb" style="margin-top:12px"></div>';
      return window.api.get("/api/problems").then(function (pd) {
        return window.api.get("/api/assets?story_id=" + state.storyId).then(function (ad) {
          var groups = ad.groups || {};
          var vo = {};
          (groups.visuals || []).forEach(function (x) { if (x.status === "adopted") vo[x.owner_id] = true; });
          var missing = [];
          (groups.characters || []).forEach(function (c) { if (!vo[c.character_id]) missing.push("人物：" + (c.name || c.character_id)); });
          (groups.scenes || []).forEach(function (s) { if (!vo[s.scene_id]) missing.push("场景：" + (s.name || s.scene_id)); });
          var fails = checks.filter(function (c) { return !c.ok && !c.skipped; });
          var tasks = (pd.failed_tasks || []).slice(0, 5);
          var rows = [];
          fails.forEach(function (f) {
            rows.push('<div class="card"><b>' + UI.badge("FAIL") + UI.esc(f.item) +
              "</b><p>" + UI.esc(String(f.detail || "").slice(0, 80)) +
              '</p><button class="btn small" data-fix="script">去修（剧本/资产）</button></div>');
          });
          missing.forEach(function (m) {
            rows.push('<div class="card"><b>' + UI.badge("candidate") + "缺失资产：" + UI.esc(m) +
              '</b><button class="btn small" data-fix="assets">去资产页</button></div>');
          });
          tasks.forEach(function (t) {
            rows.push('<div class="card"><b>' + UI.badge("failed") + "失败任务：" + UI.esc(t.title || t.id) +
              "</b><p>" + UI.esc(String(t.error || "").slice(0, 80)) +
              '</p><button class="btn small" data-fix="logs">看日志</button></div>');
          });
          document.getElementById("pb").innerHTML = "<h3>问题中心（" + rows.length + "）</h3>" +
            (rows.length ? rows.join("") : UI.empty("没有待处理问题 🎉"));
          document.querySelectorAll("[data-fix]").forEach(function (b) {
            b.onclick = function () {
              var target = b.getAttribute("data-fix");
              if (target === "logs") { location.hash = "#logs"; }
              else if (target === "assets") { location.hash = "#assets"; }
              else { location.hash = "#script"; }
            };
          });
        });
      });
    });
  }
};
