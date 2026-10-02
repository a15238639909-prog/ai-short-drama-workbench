/* ui.js — 卡片/徽章/空态/模态等公共组件。 */
window.UI = (function () {
  function badge(status) {
    const s = String(status || "").toLowerCase();
    const map = {
      "adopted": "已采用", "candidate": "候选", "superseded": "已替代",
      "running": "制作中", "queued": "排队中", "waiting_user": "等待确认",
      "completed": "已完成", "failed": "失败", "cancelled": "已取消", "paused": "已暂停"
    };
    return '<span class="badge ' + s + '">' + (map[s] || s) + "</span>";
  }
  function empty(text, btnLabel, onClick) {
    return '<div class="empty">' + text +
      (btnLabel ? '<br><button class="btn primary" id="emptyBtn">' + btnLabel + "</button>" : "") +
      "</div>";
  }
  function card(title, bodyHtml, footerHtml) {
    return '<div class="card">' +
      (title ? "<h3>" + title + "</h3>" : "") + bodyHtml +
      (footerHtml ? '<div class="sub" style="margin-top:8px">' + footerHtml + "</div>" : "") +
      "</div>";
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function openModal(html) {
    document.getElementById("modalBox").innerHTML = html;
    document.getElementById("modal").style.display = "flex";
  }
  function closeModal() {
    document.getElementById("modal").style.display = "none";
  }
  document.getElementById("modal").addEventListener("click", function (e) {
    if (e.target.id === "modal") closeModal();
  });
  /* 轻提示。全站有十几处在调 UI.toast，但它一直不存在——
     点了按钮后台明明跑起来了，前端却抛 TypeError，看着像按钮坏了。
     实测里「全部生成」就是这么"没反应"的。 */
  var toastTimer = null;
  function toast(msg, tone) {
    var box = document.getElementById("toastBox");
    if (!box) {
      box = document.createElement("div");
      box.id = "toastBox";
      document.body.appendChild(box);
    }
    box.className = "show" + (tone ? " " + tone : "");
    box.textContent = String(msg == null ? "" : msg);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { box.className = ""; }, 3200);
  }

  return { badge: badge, empty: empty, card: card, esc: esc,
           openModal: openModal, closeModal: closeModal, toast: toast };
})();

/* 原文/扩写对照视图：新增部分高亮 + 一键「只要原文」回退。 */
(function () {
  var UI = window.UI;
  UI.compareView = function (original, current, onRollback) {
    var o = String(original || ""), c = String(current || "");
    function cp(a, b) { var n = 0; while (n < a.length && n < b.length && a[n] === b[n]) n++; return n; }
    function cs(a, b) { var n = 0; while (n < a.length && n < b.length && a[a.length - 1 - n] === b[b.length - 1 - n]) n++; return n; }
    var head = cp(o, c), tail = cs(o, c);
    var added = c.slice(head, c.length - tail);
    var before = c.slice(0, head), after = c.slice(c.length - tail);
    UI.openModal("<h3>扩写对照</h3>" +
      "<p><b>原文：</b></p><pre style='white-space:pre-wrap;background:#f7f5f0;padding:8px;border-radius:8px;font-size:13px'>" +
      UI.esc(o) + "</pre>" +
      "<p><b>当前（新增部分高亮）：</b></p><pre style='white-space:pre-wrap;background:#f7f5f0;padding:8px;border-radius:8px;font-size:13px'>" +
      UI.esc(before) + "<mark style='background:#ffe9a8'>" + UI.esc(added) + "</mark>" +
      UI.esc(after) + "</pre>" +
      "<p class='note-gray'>规则：原文尽量逐字保留，AI 只追加；数字、引号、人名地名一个不换。</p>" +
      "<p><button class='btn' id='cmpRoll'>只要原文</button> <button class='btn' id='cmpClose'>关闭</button></p>");
    document.getElementById("cmpClose").onclick = UI.closeModal;
    document.getElementById("cmpRoll").onclick = function () {
      if (onRollback) onRollback();
      UI.closeModal();
    };
  };
})();
