/* ui_actions.js — 每一块内容的标准动作组，全站唯一实现。

   用户定的规矩：**所有的选项所有的步骤都要有这几个动作**
     1. 没内容 → 自动生成
     2. 有内容 → 自动扩写生成
     3. 有内容改了 → 可以重新生成
     4. 生成好了 → 可以保存
     5. 人物设定图 → 可以重新生成
     6. 人物设定图 → 换一套服装设计

   做成组件而不是每页手写，是为了：漏一个就全站都漏，补一个就全站都有。
   任何新加的内容块都必须用 UIActions.row() 渲染动作，不许自己拼按钮。 */
window.UIActions = (function () {
  var esc = function (s) { return window.UI.esc(s); };

  /* 状态徽章：绿=已存盘 / 橙=改过未保存 / 灰=还没有内容 */
  function stateChip(state) {
    var map = {
      saved: ["#218838", "✓ 已保存"],
      dirty: ["#b5762e", "● 改过未保存"],
      empty: ["#8b8375", "还没有内容"]
    };
    var x = map[state] || map.empty;
    return '<span style="font-size:12px;color:' + x[0] + '">' + x[1] + "</span>";
  }


  /* 生成类操作要几十秒（Qwen 冷启动 30–60 秒）。没有加载态，用户会以为按钮是坏的。
     所有耗时操作统一用 busy() 包起来：按钮禁用 + 转字 + 计时，结束自动恢复。 */
  function busy(btn, text, promise) {
    if (!btn) return promise;
    var old = btn.innerHTML, t0 = Date.now(), timer;
    btn.disabled = true;
    var tick = function () {
      var s = Math.round((Date.now() - t0) / 1000);
      btn.innerHTML = (text || "生成中") + "… " + s + "s";
    };
    tick(); timer = setInterval(tick, 1000);
    var done = function () {
      clearInterval(timer); btn.disabled = false; btn.innerHTML = old;
    };
    return Promise.resolve(promise).then(function (r) { done(); return r; },
                                         function (e) { done(); throw e; });
  }


  /* 全局进度条（日志面板右上角）。做法照搬 8848 的 pbar：
       busy(文字)      不知道要多久 —— 来回滑动
       run(比例, 文字)  知道百分比   —— 走条
       stuck(文字)     卡住         —— 变红停住
       done()          收起
     耗时操作统一调它，用户就不用盯着一个没反应的按钮猜。 */
  var P = {
    _set: function (cls, frac, note) {
      var b = document.getElementById("gBar"), n = document.getElementById("gNote");
      if (b) {
        b.className = "pbar" + (cls ? " " + cls : "");
        var i = b.querySelector("i");
        if (i && frac != null) i.style.width = Math.round(frac * 100) + "%";
      }
      if (n) n.textContent = note || "";
    },
    busy: function (note) { P._set("busy", null, note); P._open(); },
    run: function (frac, note) { P._set("run", frac, note); P._open(); },
    stuck: function (note) { P._set("stuck", null, note); P._open(); },
    done: function (note) {
      P._set("", 0, note || "空闲");
      if (note) setTimeout(function () { P._set("", 0, "空闲"); }, 4000);
    },
    /* 进度条已常驻顶栏（在日志按钮右边），不用再展开日志面板 */
    _open: function () {},
    /* 把一个 Promise 包起来：开始 busy，成功 done，失败 stuck */
    wrap: function (note, promise) {
      P.busy(note);
      return Promise.resolve(promise).then(function (r) { P.done("完成"); return r; },
                                           function (e) { P.stuck("失败"); throw e; });
    },
    /* 分步任务：steps 是 [{note, run}]，逐步跑并推进进度 */
    steps: function (steps) {
      var i = 0, total = (steps || []).length;
      var next = function (acc) {
        if (i >= total) { P.done("完成 " + total + "/" + total); return acc; }
        var s = steps[i++];
        P.run((i - 1) / total, s.note + "（" + i + "/" + total + "）");
        return Promise.resolve(s.run(acc)).then(next, function (e) {
          P.stuck(s.note + " 失败"); throw e;
        });
      };
      return next();
    }
  };

  /**
   * 渲染一块内容的标准动作行。
   * opts:
   *   id        这块内容的唯一 id（按钮的 data-act 会带上它）
   *   hasContent  有没有内容
   *   dirty     改过还没保存
   *   kind      "text"（默认）| "character_image"（多两个人物图专用动作）
   *   gated     生成是否被门控（true 时生成类按钮禁用并给提示）
   *   labels    覆盖默认按钮文案
   */
  function row(opts) {
    var o = opts || {};
    var g = o.gated ? " disabled title='生成轮启用后可用'" : "";
    var L = o.labels || {};
    var btn = function (act, text, cls, dis) {
      return '<button class="btn' + (cls ? " " + cls : "") + '" data-act="' + act +
        '" data-id="' + esc(o.id || "") + '"' + (dis || "") + ">" + esc(text) + "</button>";
    };
    var parts = [];

    if (o.redesign) {
      // 人物卡主流程：保存当前卡片后按当前设定出图。可按页面需要隐藏单独保存按钮。
      parts.push(btn("generate", L.generate || "🎨 按当前人设生成设计图", "primary", g));
      if (!o.hideSave) parts.push(btn("save", L.save || "💾 保存", o.dirty ? "primary" : ""));
    } else if (!o.hasContent) {
      // 1. 没内容 → 自动生成
      parts.push(btn("generate", L.generate || "✨ 自动生成", "primary", g));
    } else {
      // 2. 有内容 → 扩写；3. 改了 → 重新生成
      parts.push(btn("expand", L.expand || "✦ 自动扩写", "", g));
      parts.push(btn("regenerate", L.regenerate || "🔄 重新生成", "", g));
      // 4. 生成好了 → 保存
      parts.push(btn("save", L.save || "💾 保存", o.dirty ? "primary" : ""));
    }

    if (o.kind === "character_image") {
      // 5 / 6. 人物设定图专用
      if (!o.hideRegenSheet) parts.push(btn("regen_sheet", L.regenSheet || "🎭 重新生成设定图", "", g));
      /* 换衣服直接改人物卡服装，再走人物卡主生成按钮。 */
    } else if (o.kind === "scene_image") {
      // 场景只有"重新生成图"。换服装是人物专有的，场景没有对应动作——
      // 曾经放过一个「换光线方案」，点了什么都不做，是个假按钮，已删。
      parts.push(btn("regen_sheet", L.regenSheet || "🏛 重新生成场景图", "", g));
    }

    return '<div class="actions" style="display:flex;gap:8px;align-items:center;' +
      'flex-wrap:wrap;margin-top:12px">' + parts.join("") +
      (o.hideState ? "" : '<span style="margin-left:auto">' +
        stateChip(o.hasContent ? (o.dirty ? "dirty" : "saved") : "empty") + "</span>") + "</div>";
  }

  /**
   * 绑定动作。handlers = {generate, expand, regenerate, save, regen_sheet, change_outfit}
   * 未提供的动作会给出统一提示，而不是点了没反应。
   */
  function bind(root, handlers) {
    var h = handlers || {};
    [].forEach.call((root || document).querySelectorAll("[data-act]"), function (b) {
      b.onclick = function () {
        var act = b.getAttribute("data-act");
        var id = b.getAttribute("data-id");
        if (typeof h[act] === "function") {
          var out = h[act](id, b);
          // 生成类动作耗时长，自动挂加载态；同步返回的动作原样放行
          if (out && typeof out.then === "function"
              && ["generate", "expand", "regenerate", "regen_sheet", "change_outfit"].indexOf(act) >= 0) {
            return busy(b, b.textContent.replace(/^[^一-龥]+/, "").slice(0, 6), out);
          }
          return out;
        }
        window.UI.toast ? window.UI.toast("这个动作还没接上：" + act)
                        : alert("这个动作还没接上：" + act);
      };
    });
  }

  /* 扩写对照视图：左原文、右扩写、新增部分高亮、一键只要原文。
     用户长期强调：扩写只能补，不能改原意。 */
  function diffView(original, expanded, onKeepOriginal) {
    var box = document.createElement("div");
    box.className = "card";
    box.style.marginTop = "12px";
    var added = String(expanded || "");
    // 原文逐句保留的部分不高亮，其余当作新增
    String(original || "").split(/([。！？\n])/).forEach(function (seg) {
      if (seg && seg.length > 1) added = added.replace(seg, "" + seg + "");
    });
    added = esc(added).replace(//g, "</mark>").replace(//g, "<mark>");
    box.innerHTML =
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">' +
      "<div><b style='font-size:14px'>原文</b><pre style='white-space:pre-wrap;font-size:13px;" +
      "line-height:1.7;background:#f7f5f0;border-radius:8px;padding:12px;margin-top:8px'>" +
      esc(original) + "</pre></div>" +
      "<div><b style='font-size:14px'>扩写后（黄色是新增的）</b><pre style='white-space:pre-wrap;" +
      "font-size:13px;line-height:1.7;background:#fffdf5;border-radius:8px;padding:12px;margin-top:8px'>" +
      "<mark style='background:#fdf0c8'>" + added + "</mark></pre></div></div>" +
      '<p style="margin-top:12px"><button class="btn" id="keepOrig">只要原文</button></p>';
    box.querySelector("#keepOrig").onclick = function () {
      if (onKeepOriginal) onKeepOriginal(original);
      box.remove();
    };
    return box;
  }

  return { row: row, bind: bind, diffView: diffView, stateChip: stateChip,
           busy: busy, progress: P };
})();
