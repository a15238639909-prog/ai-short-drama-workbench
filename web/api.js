/* api.js — 唯一的 fetch 封装。后端统一 {"ok":true,"data":...} / {"ok":false,"error":"..."}。 */
window.api = (function () {
  var runtimeEpoch = 0, runtimeIdle = false, cancelledTurns=0;
  window.addEventListener('workbench-runtime-reset', function(event) {
    if(event.detail && event.detail.phase==='begin'){runtimeEpoch++;runtimeIdle=true;}
    if(event.detail && event.detail.phase==='done')runtimeIdle=true;
    if(event.detail && event.detail.phase==='failed')runtimeIdle=false;
  });
  function cancelled(){cancelledTurns++;setTimeout(function(){cancelledTurns--;},0);var e=new Error('任务已清理，请重新开始');e.code='RUNTIME_CLEARED';return e;}
  async function call(method, path, body) {
    var epoch=runtimeEpoch;
    if(method==='POST' && !/\/gpu\/(clear|pause|resume)$|\/progress$|\/status$|\/get$|\/cancel$/.test(path))runtimeIdle=false;
    let r;
    try {
      r = await fetch(path, {
        method: method,
        headers: { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body)
      });
    } catch (e) {
      if(epoch!==runtimeEpoch)throw cancelled();
      throw new Error("网络请求失败：" + e.message);
    }
    let j;
    try { j = await r.json(); } catch (e) { throw new Error("服务返回了无法解析的内容"); }
    if(epoch!==runtimeEpoch)throw cancelled();
    var data=j&&j.data!==undefined?j.data:j;
    if(data && (data.cleared_at || (data.job&&data.job.cleared_at) || (data.task&&data.task.cleared_at)))throw cancelled();
    if (j && j.ok === false) throw new Error(j.error || "请求失败");
    return j && j.data !== undefined ? j.data : j;
  }
  return {
    isRuntimeIdle:function(){return runtimeIdle||cancelledTurns>0;},
    get: function (p) { return call("GET", p); },
    post: function (p, b) { return call("POST", p, b || {}); }
  };
})();
