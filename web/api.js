/* api.js — 唯一的 fetch 封装。后端统一 {"ok":true,"data":...} / {"ok":false,"error":"..."}。 */
window.api = (function () {
  async function call(method, path, body) {
    let r;
    try {
      r = await fetch(path, {
        method: method,
        headers: { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body)
      });
    } catch (e) {
      throw new Error("网络请求失败：" + e.message);
    }
    let j;
    try { j = await r.json(); } catch (e) { throw new Error("服务返回了无法解析的内容"); }
    if (j && j.ok === false) throw new Error(j.error || "请求失败");
    return j && j.data !== undefined ? j.data : j;
  }
  return {
    get: function (p) { return call("GET", p); },
    post: function (p, b) { return call("POST", p, b || {}); }
  };
})();
