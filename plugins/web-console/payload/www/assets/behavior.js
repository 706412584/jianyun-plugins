/* 控件行为层：只切类名与 data-*，不含任何颜色/尺寸字面量。
   皮肤由 tokens.css 的 html[data-skin] 变量块决定，二者互不感知。 */
(function () {
  'use strict';

  /* ---------- 皮肤切换：一个属性驱动全系统 ---------- */
  window.setSkin = function (key) {
    document.documentElement.setAttribute('data-skin', key);
    try { localStorage.setItem('ui-skin', key); } catch (e) {}
    document.querySelectorAll('[data-skin-pick]').forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-skin-pick') === key);
    });
  };

  /* 首屏恢复上次皮肤，避免闪一下默认皮肤 */
  (function () {
    var saved = null;
    try { saved = localStorage.getItem('ui-skin'); } catch (e) {}
    if (saved) document.documentElement.setAttribute('data-skin', saved);
  })();

  /* ---------- Toast ---------- */
  var toastTimer;
  window.toast = function (msg) {
    var t = document.getElementById('toast');
    if (!t) return;
    t.textContent = msg;
    t.classList.add('on');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.remove('on'); }, 2000);
  };

  /* ---------- 基础控件 ---------- */
  window.tg = function (el) {
    var g = el.querySelector('.toggle') || el;
    if (g) g.classList.toggle('on');
  };
  window.ck = function (el) {
    var c = el.querySelector('.checkbox') || el;
    if (c) c.classList.toggle('checked');
  };
  window.step = function (btn, d) {
    var v = btn.parentElement.querySelector('.val');
    var n = Math.min(99, Math.max(1, parseInt(v.textContent, 10) + d));
    v.textContent = n;
  };
  window.rmChip = function (x) {
    var c = x.closest('.chip');
    if (c) c.remove();
  };
  window.fakeLoad = function (b) {
    if (b.dataset.busy) return;
    b.dataset.busy = '1';
    var old = b.innerHTML;
    b.innerHTML = '<span class="spin"></span> 处理中';
    b.classList.add('btn-loading');
    setTimeout(function () {
      b.innerHTML = old;
      b.classList.remove('btn-loading');
      delete b.dataset.busy;
    }, 1600);
  };

  /* ---------- 互斥组：单选 / 分段 / Tab ---------- */
  document.querySelectorAll('[data-radio]').forEach(function (g) {
    g.addEventListener('click', function (e) {
      var r = e.target.closest('.radio');
      if (!r) return;
      g.querySelectorAll('.radio').forEach(function (x) { x.classList.remove('selected'); });
      r.classList.add('selected');
    });
  });
  document.querySelectorAll('[data-seg]').forEach(function (g) {
    g.addEventListener('click', function (e) {
      var b = e.target.closest('button');
      if (!b) return;
      g.querySelectorAll('button').forEach(function (x) { x.classList.remove('active'); });
      b.classList.add('active');
    });
  });
  document.querySelectorAll('[data-tabs]').forEach(function (g) {
    g.addEventListener('click', function (e) {
      var b = e.target.closest('.tab,.nav-item');
      if (!b) return;
      g.querySelectorAll('.tab,.nav-item').forEach(function (x) { x.classList.remove('active'); });
      b.classList.add('active');
    });
  });

  /* ---------- 涟漪：只给实心/软底面与列表行 ---------- */
  document.addEventListener('click', function (e) {
    var b = e.target.closest('.btn,.cell,.seg button');
    if (!b) return;
    if (b.classList.contains('btn-disabled') ||
        b.classList.contains('btn-ghost') ||
        b.classList.contains('fab')) return;
    var r = b.getBoundingClientRect();
    var s = Math.max(r.width, r.height) * 2;
    var sp = document.createElement('span');
    sp.className = 'ripple';
    sp.style.width = sp.style.height = s + 'px';
    sp.style.left = (e.clientX - r.left - s / 2) + 'px';
    sp.style.top = (e.clientY - r.top - s / 2) + 'px';
    b.appendChild(sp);
    setTimeout(function () { sp.remove(); }, 600);
  });

  /* ---------- 浮层：下拉菜单 / Popconfirm 点外关闭 + Esc ---------- */
  function closeFloats() {
    document.querySelectorAll('.dd.open,.popc.open').forEach(function (d) { d.classList.remove('open'); });
  }
  document.addEventListener('click', function (e) {
    document.querySelectorAll('.dd.open,.popc.open').forEach(function (d) {
      if (!d.contains(e.target)) d.classList.remove('open');
    });
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      closeFloats();
      document.querySelectorAll('.mask.on').forEach(function (m) { m.classList.remove('on'); });
    }
  });
  window.menuPick = function (name) { closeFloats(); toast('菜单动作：' + name); };

  /* ---------- 手风琴：互斥展开 ---------- */
  window.acc = function (h) {
    var it = h.parentElement;
    var was = it.classList.contains('open');
    it.parentElement.querySelectorAll('.acc-item').forEach(function (x) { x.classList.remove('open'); });
    if (!was) it.classList.add('open');
  };

  /* ---------- 星级评分 ---------- */
  window.rate = function (s) {
    var g = s.closest('.stars');
    var i = Array.prototype.indexOf.call(g.children, s);
    g.querySelectorAll('.star').forEach(function (x, j) { x.classList.toggle('on', j <= i); });
  };

  /* ---------- 方向性滑动切换 ---------- */
  window.slideGo = function (n, btn) {
    var a = document.getElementById('sc1');
    var b = document.getElementById('sc2');
    if (!a || !b) return;
    if (n === 1) { a.className = 'sc cur'; b.className = 'sc off-r'; }
    else { b.className = 'sc cur'; a.className = 'sc off-l'; }
    btn.parentElement.querySelectorAll('button').forEach(function (x) { x.classList.remove('active'); });
    btn.classList.add('active');
  };

  /* ---------- 数字滚动：900ms 缓出，只用于 KPI 首屏 ---------- */
  window.countUp = function (id, target) {
    var el = document.getElementById(id);
    if (!el) return;
    var t0 = performance.now();
    (function f(now) {
      var p = Math.min(1, (now - t0) / 900);
      var e = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.floor(target * e).toLocaleString();
      if (p < 1) requestAnimationFrame(f);
    })(t0);
  };

  /* ---------- 报错抖动：只抖出错的那一个控件 ---------- */
  window.shakeInput = function (id) {
    var g = document.getElementById(id);
    if (!g) return;
    g.classList.remove('shake');
    g.classList.add('input-error');
    void g.offsetWidth;
    g.classList.add('shake');
  };

  /* ---------- 弹层 ---------- */
  window.openMask = function (id) {
    var m = document.getElementById(id);
    if (m) m.classList.add('on');
  };
  window.closeMask = function (id) {
    var m = document.getElementById(id);
    if (m) m.classList.remove('on');
  };

  /* ---------- OTP 分格：跳格 / 退格 / 粘贴分发 ---------- */
  document.querySelectorAll('.otp').forEach(function (g) {
    var ins = g.querySelectorAll('input');
    ins.forEach(function (inp, i) {
      inp.addEventListener('input', function () {
        inp.value = inp.value.replace(/\D/g, '').slice(0, 1);
        if (inp.value && ins[i + 1]) ins[i + 1].focus();
      });
      inp.addEventListener('keydown', function (e) {
        if (e.key === 'Backspace' && !inp.value && ins[i - 1]) ins[i - 1].focus();
      });
    });
    ins[0].addEventListener('paste', function (e) {
      e.preventDefault();
      var d = (e.clipboardData.getData('text') || '').replace(/\D/g, '').slice(0, ins.length);
      for (var k = 0; k < ins.length; k++) ins[k].value = d[k] || '';
      ins[Math.min(d.length || 0, ins.length - 1)].focus();
    });
  });
})();
