/* P328②：出片前门的对话框——分镜页和设定页共用。
   后端 /api/timeline/<sid>/gate-chat：{ep} 只读；{ep, text} 解析执行并回复。
   全部问题答完（all_clear）→ onClear(force) 由页面接着出片。
   只重画消息区和候选按钮，输入框不动（进度轮询每秒重画别的框，输入框在这里才不丢焦点）。 */
window.GateChat = (function () {
  function esc(s) { return window.UI && window.UI.esc ? window.UI.esc(s) : String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  var CIRC = "①②③④⑤⑥⑦⑧⑨⑩";
  var STATE = {};                                   /* box.id → {sid, ep, asks, messages, busy, onClear} */

  function mount(box, opts) {
    if (!box) return;
    var key = opts.sid + "#" + opts.ep + "|" + JSON.stringify((opts.asks || []).map(function (a) { return [a.kind, a.ref, a.question]; }));
    if (box.dataset.gcKey === key && box.firstChild) { return; }        /* 同一批问题已经挂着：不重画（别把用户正在打的字冲掉） */
    box.dataset.gcKey = key;
    var st = { sid: opts.sid, ep: opts.ep, asks: opts.asks || [], messages: [], busy: false, onClear: opts.onClear, n: opts.n || 5, key: key };
    STATE[key] = st;
    box.innerHTML =
      '<div style="margin:10px 0;padding:10px 12px;background:#fbf8f2;border:1px solid #efe9de;border-radius:10px;font-size:13px;line-height:1.7">' +
      '<div style="font-weight:600;color:#5a3b1e;margin-bottom:6px">💬 出片前还有 <span data-gc-left>' + st.asks.length + '</span> 处要你定——用一句话回答就行，答完自动接着出片</div>' +
      '<div data-gc-msgs style="max-height:260px;overflow:auto;padding:2px 0"></div>' +
      '<div data-gc-chips style="display:flex;gap:6px;flex-wrap:wrap;margin:6px 0"></div>' +
      '<div style="display:flex;gap:6px;align-items:center">' +
      '<input data-gc-input placeholder="比如：这句是苏蔓说的 / ②不用拍 / 补上 / 就按现在的出" style="flex:1;height:32px;padding:0 10px;border-radius:8px;border:1px solid var(--line);font-size:13px">' +
      '<button class="btn primary small" data-gc-send style="height:32px">发送</button></div></div>';
    var inp = box.querySelector("[data-gc-input]"), btn = box.querySelector("[data-gc-send]");
    btn.onclick = function () { send(box, st, inp.value); };
    inp.onkeydown = function (e) { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); send(box, st, inp.value); } };
    load(box, st);
  }

  function load(box, st) {
    window.api.post("/api/timeline/" + st.sid + "/gate-chat", { ep: st.ep, asks: st.asks })       /* 带上页面看到的问题：后端没开过对话就按它开 */
      .then(function (r) { apply(box, st, r, false); })
      .catch(function (e) { paint(box, st); });
  }

  function apply(box, st, r, fromSend) {
    if (box.dataset.gcKey !== st.key) return;                 /* 在途请求回来时已经换了一批问题：丢弃，不动新框、不触发旧 onClear */
    if (r && Array.isArray(r.messages)) st.messages = r.messages;
    if (r && Array.isArray(r.asks) && r.asks.length) st.asks = r.asks;
    st.allClear = !!(r && r.all_clear);
    st.force = !!(r && r.force);
    paint(box, st);
    if (r && r.all_clear && fromSend && st.onClear && !st.cleared) {
      st.cleared = true;
      st.onClear(!!r.force);
    }
  }

  function send(box, st, text) {
    text = String(text || "").trim();
    if (!text || st.busy) return;
    st.busy = true;
    var inp = box.querySelector("[data-gc-input]");
    if (inp) inp.value = "";
    st.messages = st.messages.concat([{ who: "you", text: text }]);
    paint(box, st, true);
    window.api.post("/api/timeline/" + st.sid + "/gate-chat", { ep: st.ep, text: text, n: st.n })
      .then(function (r) { st.busy = false; apply(box, st, r, true); })
      .catch(function (e) { st.busy = false; st.messages = st.messages.concat([{ who: "qwen", text: "没处理成：" + (e.message || "") }]); paint(box, st); });
  }

  function paint(box, st, thinking) {
    var m = box.querySelector("[data-gc-msgs]"), c = box.querySelector("[data-gc-chips]"), l = box.querySelector("[data-gc-left]");
    if (!m) return;
    m.innerHTML = st.messages.map(function (x) {
      var you = x.who === "you";
      return '<div style="display:flex;' + (you ? "justify-content:flex-end" : "") + ';margin:3px 0"><div style="max-width:92%;padding:5px 9px;border-radius:8px;white-space:pre-wrap;' +
        (you ? "background:#e8e0d0;color:#2a2620" : "background:#fff;border:1px solid #efe9de;color:#2f2921") + '">' + esc(x.text) + "</div></div>";
    }).join("") + (thinking ? '<div class="note-gray" style="margin:2px 0">正在处理…</div>' : "");
    m.scrollTop = m.scrollHeight;
    var open = st.asks.filter(function (a) { return !a.answered; });
    if (l) l.textContent = String(open.length);
    if (c) {
      var chips = [];
      st.asks.forEach(function (a, i) {
        if (a.answered) return;
        var circ = CIRC[i] || (i + 1) + ".";
        (a.options || []).forEach(function (o) {
          chips.push('<button class="btn small" data-gc-chip="' + esc(circ + " " + o) + '">' + esc(circ + " " + o) + "</button>");
        });
      });
      if (open.length > 1) chips.push('<button class="btn small" data-gc-chip="就按现在的出片">全部就按现在的出</button>');
      /* 打开时就已经全答完（上次答完但没出成片）：不自动出，给一个明确的按钮 */
      if (!open.length && st.allClear && !st.cleared) chips.push('<button class="btn primary small" data-gc-go="1">▶ 接着出片</button>');
      c.innerHTML = chips.join("");
      var go = c.querySelector("[data-gc-go]");
      if (go) go.onclick = function () { if (st.onClear && !st.cleared) { st.cleared = true; st.onClear(!!st.force); } };
      [].forEach.call(c.querySelectorAll("[data-gc-chip]"), function (b) {
        b.onclick = function () { send(box, st, b.getAttribute("data-gc-chip")); };
      });
    }
  }

  function clear(box) { if (box) { box.innerHTML = ""; delete box.dataset.gcKey; } }
  return { mount: mount, clear: clear };
})();
