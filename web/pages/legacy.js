/* legacy.js — 并入「本地模型工作台」(app.py) 的四大模块：
   问AI / 虚拟人物 / 出图词 / 视频导演台。
   每个板块用一个 iframe 加载 /legacy/?tab=xxx（前后端原封不动跑在同一个 8853 服务里，
   靠 iframe 做隔离，避免 app.py 的 7000 行共享 JS 与 v41 冲突）。 */
(function () {
  window.Pages = window.Pages || {};

  function legacyPage(tab) {
    return {
      render: function (main) {
        // #main 默认有 20px 内边距和滚动；嵌入整页时去掉，让 iframe 铺满。
        main.classList.add("legacyframe");
        main.innerHTML =
          '<iframe title="legacy" src="/legacy/?tab=' + tab +
          '" style="display:block;width:100%;height:100%;border:0;background:#f3f0ea"></iframe>';
      }
    };
  }

  window.Pages.chat = legacyPage("chat");        // 💬 问AI
  window.Pages.vchar = legacyPage("vchar");      // 🎭 虚拟人物
  window.Pages.prompt = legacyPage("prompt");    // 🎨 出图词（用户 9-18：改回旧版）
  window.Pages.director = legacyPage("director"); // 🎥 视频导演台
})();
