/* 与桌面下载器共用服务、目录和记录，不加载或运行 AI。 */
(function () {
  window.Pages = window.Pages || {};
  window.Pages.downloads = {
    render: async function (main) {
      var panel = document.createElement("section");
      panel.textContent = "正在连接视频下载…";
      main.replaceChildren(panel);
      try {
        await window.api.post("/api/downloads/open", {});
        if (panel.parentNode !== main) return;
        var frame = document.createElement("iframe");
        frame.title = "视频下载";
        frame.src = "/download-tool/";
        frame.style.cssText = "display:block;width:100%;height:100%;border:0;background:#f4f5f2";
        main.classList.add("legacyframe");
        main.replaceChildren(frame);
      } catch (error) {
        if (panel.parentNode !== main) return;
        panel.textContent = "视频下载暂时无法连接：" + error.message + " ";
        var retry = document.createElement("button");
        retry.textContent = "重新连接";
        retry.onclick = function () { window.Pages.downloads.render(main); };
        panel.appendChild(retry);
      }
    }
  };
})();
