/* 人工修稿：只读检查、精确定位、局部替换。没有模型调用或自动整篇重写。 */
window.ProseEditor = (function () {
  function mount(host, options) {
    var esc = UI.esc, report = null, undo = null, serial = 0;
    var input = options.input;
    host.innerHTML = '<div style="padding:14px 18px;border-top:1px solid #ddd5c8;background:#faf8f3">' +
      '<b>文字疑点与修改</b><p class="sub">列出程序能找到的疑点，由你确认。没有提示也不代表故事没有问题。</p>' +
      '<div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn small" data-check>检查本话疑点</button>' +
      '<button class="btn small success" data-save' + (options.locked ? ' disabled' : '') + '>保存正文修改</button>' +
      '<button class="btn small" data-undo disabled>撤销刚才替换</button></div>' +
      '<p class="sub" data-message role="status" aria-live="polite"></p><div data-results></div></div>';
    var message = host.querySelector('[data-message]'), results = host.querySelector('[data-results]');
    function note(t) { message.textContent = t; }
    function changed() {
      serial++; report = null;
      results.innerHTML = '';
      note('正文已修改，请保存；疑点列表已失效，可重新检查。');
    }
    input.addEventListener('input', changed);
    function replaceAll(text) {
      if (options.locked) return;
      undo = input.value;
      input.value = text;
      input.dispatchEvent(new Event('input', {bubbles: true}));
      host.querySelector('[data-undo]').disabled = false;
      input.focus();
    }
    host.querySelector('[data-undo]').onclick = function () {
      if (undo === null || options.locked) return;
      var old = undo; undo = null;
      input.value = old; input.dispatchEvent(new Event('input', {bubbles: true}));
      this.disabled = true;
      note('已撤销刚才替换。请保存正文修改。');
    };
    host.querySelector('[data-save]').onclick = function () {
      var button = this; button.disabled = true; note('正在保存…');
      options.save().then(function () { note('正文已保存。'); })
        .catch(function (e) { note('保存失败：' + e.message); })
        .finally(function () { button.disabled = !!options.locked; });
    };
    function range(ref) {
      if (!report || input.value !== report.text || ref.no !== options.no) return null;
      var re = /[^\r\n]+/g, m, i = 0;
      while ((m = re.exec(input.value))) {
        i++;
        if (i === ref.paragraph && m[0].trim() === ref.quote) {
          var start = m.index + m[0].indexOf(ref.quote);
          return {start: start, end: start + ref.quote.length};
        }
      }
      return null;
    }
    function show(r) {
      report = r;
      note(r.issues.length ? '找到 ' + r.issues.length + ' 条待核对项；这是疑点，不是判错。' : '本次未发现这几类疑点，仍需通读人物、动作衔接和故事节奏。');
      results.innerHTML = '<p class="sub">' + esc(r.scope) + '</p>' +
        (r.previous_tail ? '<details style="margin:8px 0"><summary>对照第 ' + esc(r.previous_no) + ' 话结尾，检查本话是否接得上</summary>' +
          '<p style="white-space:pre-wrap;line-height:1.8">' + esc(r.previous_tail) + '</p></details>' : '') +
        r.issues.map(function (issue, ix) {
          return '<section data-issue="' + ix + '" style="border:1px solid #ded6c9;border-radius:8px;padding:12px;margin:10px 0">' +
            '<b>' + esc(issue.kind) + '</b><p>' + esc(issue.detail) + '</p><p><b>怎么改：</b>' + esc(issue.advice) + '</p>' +
            issue.references.map(function (ref, j) {
              return '<div style="margin:8px 0"><span class="sub">第 ' + esc(ref.no) + ' 话 · 第 ' + esc(ref.paragraph) + ' 段' + (ref.no !== options.no ? '（前文对照）' : '') + '</span>' +
                '<blockquote style="white-space:pre-wrap;line-height:1.8;margin:6px 0;padding-left:12px;border-left:3px solid #d6c9b4">' + esc(ref.quote) + '</blockquote>' +
                (ref.no === options.no ? '<button class="btn small" data-locate="' + ix + ':' + j + '">定位原文</button> ' +
                  '<button class="btn small" data-edit="' + ix + ':' + j + '"' + (options.locked ? ' disabled' : '') + '>修改这一段</button>' : '') + '</div>';
            }).join('') + '<button class="btn small" data-ignore="' + ix + '">本次略过</button><div data-edit-box></div></section>';
        }).join('');
      results.querySelectorAll('[data-ignore]').forEach(function (b) {
        b.onclick = function () { this.closest('section').hidden = true; note('已略过这一项；重新检查时仍会显示。正文没有改动。'); };
      });
      results.querySelectorAll('[data-locate], [data-edit]').forEach(function (b) {
        b.onclick = function () {
          var editing = this.hasAttribute('data-edit');
          var keys = this.getAttribute(editing ? 'data-edit' : 'data-locate').split(':');
          var ref = report.issues[+keys[0]].references[+keys[1]], at = range(ref);
          if (!at) { note('正文已变化，请重新检查后再定位。'); return; }
          if (!editing) {
            input.focus(); input.setSelectionRange(at.start, at.end);
            input.scrollIntoView({block: 'center', behavior: 'smooth'});
            note('已选中第 ' + ref.paragraph + ' 段原文，可直接修改。'); return;
          }
          var box = this.closest('section').querySelector('[data-edit-box]');
          box.innerHTML = '<label style="display:block;margin-top:12px">修改后这一段<textarea aria-label="修改后这一段" style="width:100%;min-height:130px;line-height:1.8">' + esc(ref.quote) + '</textarea></label>' +
            '<button class="btn small primary" data-apply>替换到正文</button> <button class="btn small" data-cancel>取消</button>' +
            '<p class="sub">只替换这一段。替换后请保存，可撤销刚才替换。</p>';
          box.querySelector('[data-cancel]').onclick = function () { box.innerHTML = ''; };
          box.querySelector('[data-apply]').onclick = function () {
            var checked = range(ref);
            if (!checked) { note('正文已变化，本次未替换，请重新检查。'); return; }
            var replacement = box.querySelector('textarea').value;
            replaceAll(input.value.slice(0, checked.start) + replacement + input.value.slice(checked.end));
          };
        };
      });
    }
    function check() {
      var ticket = ++serial, text = input.value;
      note('正在核对文字…'); results.innerHTML = ''; report = null;
      return options.check(text).then(function (r) {
        if (ticket === serial && input.isConnected && input.value === text) show(r);
      }).catch(function (e) { if (ticket === serial) note('检查未完成：' + e.message); });
    }
    host.querySelector('[data-check]').onclick = check;
    check();
    return {replaceAll: replaceAll};
  }
  return {mount: mount};
})();
