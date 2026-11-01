/* Simple source-version-bound cut list. Source media are never modified. */
(function () {
  function copy(x) { return JSON.parse(JSON.stringify(x)); }
  function fullItems(data) {
    return data.sources.filter(function (s) { return s.usable; }).map(function (s) {
      return {segment_no:s.segment_no, scene_no:s.scene_no, fingerprint:s.fingerprint, in:0, out:s.duration};
    });
  }
  function move(items, index, delta) {
    var next = index + delta;
    if (next < 0 || next >= items.length) return;
    var item = items.splice(index, 1)[0]; items.splice(next, 0, item);
  }
  function open(sid, ep) {
    var old = document.getElementById('episodeEditDialog');
    if (old) { old.close(); old.remove(); }
    var dialog = document.createElement('dialog');
    dialog.id = 'episodeEditDialog';
    dialog.style.cssText = 'width:min(1050px,94vw);max-height:92vh;border:1px solid #d8d0c2;border-radius:14px;padding:22px;background:#faf8f3;color:#292725';
    document.body.appendChild(dialog);
    dialog.innerHTML = '正在读取本话视频…'; dialog.showModal();
    var data, items = [], selected = 0, dirty = false, busy = false;
    var esc = window.UI.esc;
    function el(name) { return dialog.querySelector('[data-edit="'+name+'"]'); }
    function source(item) {
      return data.sources.find(function (s) { return s.segment_no === item.segment_no && s.scene_no === item.scene_no && s.fingerprint === item.fingerprint; });
    }
    function status(message, error) {
      var box = el('status'); if (box) { box.textContent = message; box.style.color = error ? '#b63232' : '#486543'; }
    }
    function changed() { dirty = true; status('有未保存的剪辑改动'); }
    function close(discard) {
      if (busy) return;
      if (dirty && discard !== true) {
        status('剪辑改动尚未保存。');
        var box = el('status');
        var leave = document.createElement('button');
        leave.className = 'btn small'; leave.textContent = '放弃改动并关闭';
        leave.style.marginLeft = '8px'; leave.onclick = function () { close(true); };
        var stay = document.createElement('button');
        stay.className = 'btn small'; stay.textContent = '继续编辑';
        stay.style.marginLeft = '8px'; stay.onclick = function () { status('有未保存的剪辑改动'); };
        box.appendChild(leave); box.appendChild(stay);
        return;
      }
      var v = el('video'); if (v) v.pause(); dialog.close(); dialog.remove();
    }
    dialog.addEventListener('cancel', function (e) { e.preventDefault(); close(); });
    function render() {
      if (!dialog.isConnected) return;
      selected = Math.max(0, Math.min(selected, items.length - 1));
      var item = items[selected], src = item && source(item);
      dialog.innerHTML = '<div style="display:flex;justify-content:space-between;gap:12px"><strong>本话剪辑 · 第 '+ep+' 话</strong><button class="btn small" data-edit="close">关闭</button></div>'+
        '<p style="font-size:13px">选中片段，播放到合适位置后标记开始和结束。按列表顺序合成，画面与声音同步裁切。对白请保留完整句子，在自然停顿处切镜。</p>'+
        '<div style="display:grid;grid-template-columns:minmax(0,1fr) minmax(260px,1fr);gap:16px">'+
        '<div><video data-edit="video" controls preload="metadata" style="width:100%;max-height:350px;background:#171717"'+
        (src ? ' src="'+esc('/files/'+src.path.replace(/\\/g,'/').split('/').map(encodeURIComponent).join('/')+'?v='+src.fingerprint)+'"' : '')+'></video>'+
        (item ? '<div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:10px">开始 <input data-edit="in" aria-label="开始秒数" type="number" step="0.04" min="0" value="'+item.in.toFixed(3)+'" style="width:86px">秒　结束 <input data-edit="out" aria-label="结束秒数" type="number" step="0.04" min="0" value="'+item.out.toFixed(3)+'" style="width:86px">秒</div>'+ '<div style="display:flex;gap:7px;flex-wrap:wrap;margin:10px 0"><button class="btn small" data-edit="markIn">设为开始</button><button class="btn small" data-edit="markOut">设为结束</button><button class="btn small" data-edit="preview">播放保留区间</button></div>' : '<p>还没有可剪辑片段</p>')+
        '<p data-edit="status" role="status" style="font-size:13px"></p></div>'+
        '<div><div style="display:flex;gap:8px;flex-wrap:wrap"><select data-edit="addSource" aria-label="添加源片段">'+data.sources.filter(function(s){return s.usable;}).map(function(s,i){return '<option value="'+data.sources.indexOf(s)+'">场'+s.scene_no+' · 第'+s.segment_no+'段（'+s.duration.toFixed(2)+'秒）</option>';}).join('')+'</select><button class="btn small" data-edit="add">添加区间</button></div>'+
        '<div style="max-height:360px;overflow:auto;margin-top:8px">'+items.map(function (x,i) {
          return '<div style="display:flex;gap:5px;align-items:center;padding:7px;border:1px solid '+(i===selected?'#a47c39':'#ddd')+';margin-bottom:5px">'+
            '<button class="btn small" data-select="'+i+'" style="flex:1;text-align:left">'+(i+1)+'. 第'+x.segment_no+'段 · '+x.in.toFixed(2)+'–'+x.out.toFixed(2)+'秒'+(!source(x)?'（来源已更新）':'')+'</button>'+
            '<button class="btn small" aria-label="上移" data-up="'+i+'"'+(i===0?' disabled':'')+'>↑</button><button class="btn small" aria-label="下移" data-down="'+i+'"'+(i===items.length-1?' disabled':'')+'>↓</button><button class="btn small" data-remove="'+i+'">移出</button></div>';
        }).join('')+'</div></div></div>'+
        '<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:16px"><button class="btn primary" data-edit="saveMerge">保存并合成</button><button class="btn" data-edit="save">保存剪辑</button><button class="btn" data-edit="reset">恢复原整段拼接</button><span style="font-size:13px">原片保留。已保存的列表用于本话后续合成。</span></div>';
      el('close').onclick = close;
      if (data.errors.length) status(data.errors.join('；')+'；请移出旧区间并重新添加对应源片段。', true);
      else status(dirty ? '有未保存的剪辑改动' : data.enabled ? '已启用剪辑列表' : '当前使用原整段拼接；保存后启用剪辑');
      dialog.querySelectorAll('[data-select]').forEach(function (b) { b.onclick=function(){selected=+b.dataset.select;render();}; });
      ['up','down','remove'].forEach(function(action){dialog.querySelectorAll('[data-'+action+']').forEach(function(b){b.onclick=function(){
        var i=+b.dataset[action]; if(action==='remove'){items.splice(i,1);selected=Math.min(selected,items.length-1);}else{move(items,i,action==='up'?-1:1);selected=i+(action==='up'?-1:1);} changed(); render();
      };});});
      el('add').onclick=function(){var s=data.sources[+el('addSource').value];if(!s)return;items.push({segment_no:s.segment_no,scene_no:s.scene_no,fingerprint:s.fingerprint,in:0,out:s.duration});selected=items.length-1;changed();render();};
      if (item) {
        var video=el('video');
        video.onloadedmetadata=function(){if(item.in < video.duration)video.currentTime=item.in;};
        ['in','out'].forEach(function(key){function update(commit){
          var v=Number(el(key).value), start=key==='in'?v:item.in, end=key==='out'?v:item.out;
          if(!src || el(key).value==='' || !Number.isFinite(v) || start<0 || end-start<1/24 || end>src.duration+.001){if(commit)el(key).value=item[key].toFixed(3);status('区间须在视频时长内，且结束晚于开始至少一帧。',true);return;}
          item[key]=v;changed();
          var label=dialog.querySelector('[data-select="'+selected+'"]');
          if(label)label.textContent=(selected+1)+'. 第'+item.segment_no+'段 · '+item.in.toFixed(2)+'–'+item.out.toFixed(2)+'秒';
        }el(key).oninput=function(){update(false);};el(key).onchange=function(){update(true);};});
        ['In','Out'].forEach(function(key){el('mark'+key).onclick=function(){var field=el(key.toLowerCase());field.value=video.currentTime.toFixed(3);field.onchange();};});
        el('preview').onclick=function(){video.currentTime=item.in;video.ontimeupdate=function(){if(video.currentTime>=item.out){video.pause();video.ontimeupdate=null;}};video.play().catch(function(e){status(e.message,true);});};
      }
      function persist(action, merge) {
        if(busy)return;busy=true;dialog.querySelectorAll('button,input,select').forEach(function(x){x.disabled=true;});
        var body={sid:sid,ep:ep,revision:data.revision};if(action==='save')body.items=copy(items);
        window.api.post('/api/episode/edit/'+action,body).then(function(r){data=r;items=data.enabled?copy(data.items):fullItems(data);dirty=false;
          if(merge)return window.api.post('/api/timeline/'+encodeURIComponent(sid)+'/merge',{ep:ep}).then(function(){window.UI.toast('已开始合成，完成后在素材库的成片中查看');return '剪辑已保存，后台正在合成；可关闭此窗口。';});
          return action==='save'?'剪辑已保存，下次合成使用这些区间。':'已恢复原整段拼接。';
        }).then(function(message){busy=false;render();status(message);}).catch(function(e){busy=false;render();status(e.message,true);});
      }
      el('save').onclick=function(){if(!items.length){status('请添加至少一个区间，或点击恢复原整段拼接。',true);return;}persist('save');};
      el('saveMerge').onclick=function(){if(!items.length){status('请添加至少一个区间。',true);return;}persist('save',true);};
      el('reset').onclick=function(){persist('reset');};
    }
    return window.api.get('/api/episode/edit?sid='+encodeURIComponent(sid)+'&ep='+ep).then(function(r){data=r;items=data.enabled?copy(data.items):fullItems(data);render();}).catch(function(e){dialog.innerHTML='<p>'+esc(e.message)+'</p><button class="btn" data-edit="close">关闭</button>';el('close').onclick=close;});
  }
  window.EpisodeEdit={open:open, fullItems:fullItems, move:move};
})();
