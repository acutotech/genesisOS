/* The interactive desktop on the page: a recreation of Genesis Shell in HTML,
   laid out at 1280x720 and scaled to its frame. Labels, sizes and colours follow
   the shell's code (panel.py, launcher.py, control_center.py, notifications.py,
   power.py, sensors.py) and the labwc configuration.
   The tour steps beside it drive its state; so do clicks on the panel itself. */
(function () {
  'use strict';

  var gd = document.getElementById('gd');
  var viewport = document.getElementById('gd-viewport');
  if (!gd || !viewport) return;

  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var $ = function (id) { return document.getElementById(id); };
  var NS = 'http://www.w3.org/2000/svg';

  // apps this image is known to carry (pinned ones first, in the default dock order)
  var APPS = [
    { id: 'chromium', name: 'Chromium', cls: 'app-chromium', icon: 'i-globe', group: 'Internet', pinned: true },
    { id: 'code', name: 'Visual Studio Code', cls: 'app-code', icon: 'i-code', group: 'Development', pinned: true },
    { id: 'files', name: 'Files', cls: 'app-files', icon: 'i-folder', group: 'System', pinned: true },
    { id: 'term', name: 'Terminal', cls: 'app-term', icon: 'i-terminal', group: 'System', pinned: true, running: true },
    { id: 'settings', name: 'Genesis Settings', cls: 'app-settings', icon: 'i-sliders', group: 'System', pinned: true },
    { id: 'rc', name: 'Raspberry Pi Configuration', cls: 'app-config', icon: 'i-chip', group: 'System' },
    { id: 'firefox', name: 'Firefox', cls: 'app-audio', icon: 'i-globe', group: 'Internet' },
    { id: 'blueman', name: 'Bluetooth Manager', cls: 'app-vnc', icon: 'i-bt', group: 'System' },
    { id: 'pavu', name: 'Volume Control', cls: 'app-image', icon: 'i-vol', group: 'Media' }
  ];
  var GROUP_ORDER = ['All', 'Internet', 'Development', 'Office', 'Graphics', 'Media', 'Games', 'Education', 'System', 'Accessories', 'Other'];

  var DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

  function icon(name) {
    var svg = document.createElementNS(NS, 'svg');
    var use = document.createElementNS(NS, 'use');
    use.setAttribute('href', '#' + name);
    svg.appendChild(use);
    return svg;
  }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function pad(n) { return n < 10 ? '0' + n : '' + n; }

  // ------------------------------------------------------------------ scale
  function fit() {
    var w = viewport.clientWidth;
    if (w > 0) gd.style.setProperty('--gd-s', (w / 1280).toFixed(5));
  }
  if ('ResizeObserver' in window) new ResizeObserver(fit).observe(viewport);
  window.addEventListener('resize', fit, { passive: true });
  fit();

  // ------------------------------------------------------------------ clock
  // panel: "%a %d %b   %H:%M"; notification center: "%A, %d %B %Y" (the shell's formats)
  function tick() {
    var d = new Date();
    $('gd-clock').textContent = DAYS[d.getDay()].slice(0, 3) + ' ' + pad(d.getDate()) + ' ' + MONTHS[d.getMonth()].slice(0, 3) + '   ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
    $('gd-date').textContent = DAYS[d.getDay()] + ', ' + pad(d.getDate()) + ' ' + MONTHS[d.getMonth()] + ' ' + d.getFullYear();
  }
  function calendar() {
    var cal = $('gd-cal');
    var now = new Date();
    var first = new Date(now.getFullYear(), now.getMonth(), 1);
    var startDay = first.getDay(); // Sunday first, as with the image's en_US locale
    var days = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
    var prevDays = new Date(now.getFullYear(), now.getMonth(), 0).getDate();
    $('gd-cal-month').textContent = MONTHS[now.getMonth()];
    $('gd-cal-year').textContent = now.getFullYear();
    cal.textContent = '';
    DAYS.forEach(function (d) { cal.appendChild(el('span', 'h', d.slice(0, 3))); });
    var i;
    for (i = startDay - 1; i >= 0; i--) cal.appendChild(el('span', 'o', prevDays - i));
    for (i = 1; i <= days; i++) cal.appendChild(el('span', i === now.getDate() ? 't' : '', i));
    var cells = startDay + days;
    for (i = 1; cells < 42; i++, cells++) cal.appendChild(el('span', 'o', i)); // always six weeks
  }
  tick();
  calendar();
  setInterval(tick, 15000);
  (function () {
    var d = new Date(Date.now() - 60000);
    $('gd-since').textContent = pad(d.getHours()) + ':' + pad(d.getMinutes());
  })();

  // ---------------------------------------------------------------- sensors
  // the card of the tile you click: letter, first address of its range, bus, time
  var stiles = Array.prototype.slice.call(gd.querySelectorAll('.gd-stile'));
  stiles.forEach(function (t) {
    t.addEventListener('click', function (ev) {
      ev.stopPropagation();
      touched();
      var letter = t.dataset.box.toUpperCase();
      stiles.forEach(function (x) { x.classList.toggle('sel', x === t); });
      $('gd-scard-cap').textContent = 'SENSOR ' + letter;
      $('gd-scard-title').textContent = 'Sensor box ' + letter;
      $('gd-scard-addr').textContent = t.dataset.addr;
      $('gd-scard-icon').src = 'assets/img/sensors/sensor-' + t.dataset.box + '.svg';
    });
  });

  // ------------------------------------------------------------------- dock
  var dock = $('gd-dock');
  APPS.filter(function (a) { return a.pinned; }).forEach(function (a) {
    var b = el('button', 'gd-app' + (a.running ? ' is-running is-focused' : ''));
    b.type = 'button';
    b.setAttribute('aria-label', a.name);
    b.title = a.name;
    var tile = el('i', 'app ' + a.cls);
    tile.appendChild(icon(a.icon));
    b.appendChild(tile);
    b.addEventListener('click', function () {
      touched();
      if (a.id === 'term') { b.classList.toggle('is-focused'); return; }
      b.classList.toggle('is-running');
    });
    dock.appendChild(b);
  });

  // --------------------------------------------------------------- launcher
  var chips = $('gd-chips');
  var grid = $('gd-apps');
  var searchText = $('gd-search-text');
  var empty = $('gd-empty');
  var query = '';
  var group = 'All';
  var cells = [];

  var present = {};
  APPS.forEach(function (a) { present[a.group] = true; });
  GROUP_ORDER.filter(function (g) { return g === 'All' || present[g]; }).forEach(function (g) {
    var c = el('button', 'gd-chip' + (g === 'All' ? ' is-on' : ''), g);
    c.type = 'button';
    c.addEventListener('click', function () { touched(); group = g; filter(); });
    chips.appendChild(c);
  });
  APPS.forEach(function (a, i) {
    var c = el('button', 'gd-cell');
    c.type = 'button';
    c.style.setProperty('--i', i);
    var tile = el('i', 'app ' + a.cls);
    tile.appendChild(icon(a.icon));
    c.appendChild(tile);
    c.appendChild(el('span', '', a.name));
    c.addEventListener('click', function () { touched(); setState('desktop'); });
    grid.appendChild(c);
    cells.push({ app: a, node: c });
  });

  function filter() {
    var q = query.trim().toLowerCase();
    var shown = 0;
    var firstShown = null;
    cells.forEach(function (c) {
      var ok = (group === 'All' || c.app.group === group) && (!q || c.app.name.toLowerCase().indexOf(q) !== -1);
      c.node.hidden = !ok;
      c.node.classList.remove('is-sel');
      if (ok) { shown++; if (!firstShown) firstShown = c.node; }
    });
    if (q && firstShown) firstShown.classList.add('is-sel');
    empty.classList.toggle('is-on', shown === 0);
    searchText.textContent = query;
    Array.prototype.forEach.call(chips.children, function (c) { c.classList.toggle('is-on', c.textContent === group); });
  }

  // ------------------------------------------------------------------ state
  var timers = [];
  function later(fn, ms) { var t = setTimeout(fn, ms); timers.push(t); return t; }
  function clearTimers() { timers.forEach(clearTimeout); timers = []; }
  function touched() { clearTimers(); }

  var dndSwitch = $('gd-dnd-switch');
  var osd = $('gd-osd');
  var osdFill = $('gd-osd-fill');
  var osdVal = $('gd-osd-val');
  var osdIcon = osd.querySelector('use');
  var osdTimer = 0;

  function showOsd(kind, value, hold) {
    osdIcon.setAttribute('href', kind === 'sun' ? '#i-sun' : '#i-vol');
    osdFill.style.width = value + '%';
    osdVal.textContent = Math.round(value) + '%';
    osd.classList.add('is-open');
    clearTimeout(osdTimer);
    if (hold !== true) osdTimer = setTimeout(function () { osd.classList.remove('is-open'); }, 1400);
  }

  function setState(state) {
    clearTimers();
    gd.dataset.state = state;
    Array.prototype.forEach.call(gd.querySelectorAll('[data-toggle]'), function (b) {
      b.classList.toggle('is-on', b.dataset.toggle === state && state !== 'desktop');
    });
    osd.classList.remove('is-open');
    if (state !== 'launcher') { query = ''; group = 'All'; filter(); }
    if (state === 'nc') $('gd-badge').hidden = true;
    if (state === 'sensors' && !dndSwitch.classList.contains('on')) $('gd-badge').hidden = false;
    autoplay(state);
  }

  // what each state shows by itself, until the visitor takes over
  function autoplay(state) {
    if (reduce) {
      if (state === 'launcher') { query = 'code'; filter(); }
      return;
    }
    if (state === 'launcher') {
      query = '';
      filter();
      'code'.split('').forEach(function (ch, i) {
        later(function () { query += ch; filter(); }, 1100 + i * 170);
      });
      later(function () { query = ''; filter(); }, 4600);
    } else if (state === 'osd') {
      var seq = [62, 70, 78, 86, 74, 58];
      seq.forEach(function (v, i) { later(function () { showOsd('vol', v, true); setVolume(v); }, 250 + i * 520); });
      later(function () { showOsd('sun', 80, true); }, 250 + seq.length * 520 + 500);
      later(function () { showOsd('sun', 100, true); }, 250 + seq.length * 520 + 1100);
    }
  }

  Array.prototype.forEach.call(gd.querySelectorAll('[data-toggle]'), function (b) {
    b.addEventListener('click', function (e) {
      e.stopPropagation();
      touched();
      var target = b.dataset.toggle;
      setState(gd.dataset.state === target ? 'desktop' : target);
    });
  });
  // a click on the bare desktop closes whatever is open, as in the shell
  gd.addEventListener('click', function (e) {
    if (e.target.closest('.gd-card, .gd-panel, .gd-cell, .gd-chip, .gd-search, .gd-launcher-foot, .gd-sensors')) return;
    if (gd.dataset.state !== 'desktop') { touched(); setState('desktop'); }
  });
  viewport.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && gd.dataset.state !== 'desktop') { setState('desktop'); return; }
    if (gd.dataset.state !== 'launcher') return;
    if (e.key === 'Backspace') { touched(); query = query.slice(0, -1); filter(); e.preventDefault(); }
    else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) { touched(); query += e.key; filter(); e.preventDefault(); }
  });

  // --------------------------------------------------------- control center
  var vol = $('gd-vol');
  var bright = $('gd-bright');
  function paint(input) {
    var min = +input.min || 0;
    input.style.setProperty('--v', ((input.value - min) / (input.max - min) * 100) + '%');
  }
  function setVolume(v) { vol.value = v; paint(vol); }
  vol.addEventListener('input', function () { touched(); paint(vol); showOsd('vol', +vol.value); });
  bright.addEventListener('input', function () {
    touched();
    paint(bright);
    gd.style.setProperty('--dim', ((100 - bright.value) / 100 * 0.78).toFixed(3));
    showOsd('sun', +bright.value);
  });
  paint(vol);
  paint(bright);

  var TILE_TEXT = { wifi: ['NodoAI', 'Off'], bt: ['On', 'Off'], dnd: ['On', 'Off'] };
  Array.prototype.forEach.call(gd.querySelectorAll('.gd-tile'), function (t) {
    t.addEventListener('click', function () {
      touched();
      var kind = t.dataset.tile;
      if (kind === 'shot') { flash(); return; }
      if (kind === 'displays') return;
      var on = t.classList.toggle('on');
      t.querySelector('small').textContent = TILE_TEXT[kind][on ? 0 : 1];
      if (kind === 'dnd') { dndSwitch.classList.toggle('on', on); if (on) $('gd-badge').hidden = true; }
    });
  });

  function flash() { // the screenshot tile
    var f = el('div');
    f.style.cssText = 'position:absolute;inset:0;z-index:70;background:#fff;opacity:.85;pointer-events:none;transition:opacity .5s';
    gd.appendChild(f);
    requestAnimationFrame(function () { requestAnimationFrame(function () { f.style.opacity = '0'; }); });
    setTimeout(function () { f.remove(); }, 600);
  }

  // overlays with real buttons are reachable only while they are visible (CSS visibility)
  ['gd-cc', 'gd-power', 'gd-launcher'].forEach(function (id) { $(id).removeAttribute('aria-hidden'); });
  viewport.tabIndex = 0;
  viewport.setAttribute('role', 'group');
  viewport.setAttribute('aria-label', 'Interactive recreation of the Genesis desktop');

  // ------------------------------------------------------------------- tour
  var steps = Array.prototype.slice.call(document.querySelectorAll('#tour-steps .step'));
  function activate(step) {
    steps.forEach(function (s) { s.classList.toggle('is-active', s === step); });
    if (gd.dataset.state !== step.dataset.state) setState(step.dataset.state);
  }
  if ('IntersectionObserver' in window && steps.length) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) activate(e.target); });
    }, { rootMargin: '-46% 0px -46% 0px', threshold: 0 });
    steps.forEach(function (s) { io.observe(s); });
  } else {
    steps.forEach(function (s) { s.classList.add('is-active'); });
  }

  // ------------------------------------------------------------- playground
  function radio(groupId, attr, apply) {
    var box = $(groupId);
    if (!box) return;
    var buttons = Array.prototype.slice.call(box.querySelectorAll('button'));
    function choose(b) {
      buttons.forEach(function (x) { x.setAttribute('aria-checked', x === b ? 'true' : 'false'); x.tabIndex = x === b ? 0 : -1; });
      apply(b.dataset[attr]);
    }
    buttons.forEach(function (b, i) {
      b.tabIndex = b.getAttribute('aria-checked') === 'true' ? 0 : -1;
      b.addEventListener('click', function () { choose(b); });
      b.addEventListener('keydown', function (e) {
        var step = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0;
        if (!step) return;
        e.preventDefault();
        var next = buttons[(i + step + buttons.length) % buttons.length];
        next.focus();
        choose(next);
      });
    });
  }
  radio('pg-walls', 'wall', function (v) { gd.dataset.wall = v; });
  radio('pg-panel', 'panel', function (v) { gd.dataset.panel = v; });
  radio('pg-accents', 'accent', function (hex) {
    // the accent is one setting on the box; here it recolours the page too
    var n = parseInt(hex.slice(1), 16);
    var root = document.documentElement.style;
    root.setProperty('--accent', hex);
    root.setProperty('--accent-rgb', (n >> 16) + ', ' + ((n >> 8) & 255) + ', ' + (n & 255));
  });

  // ------------------------------------------------- tilt in as it scrolls up
  var screen = document.querySelector('.device-screen');
  var device = $('device');
  var wide = window.matchMedia('(min-width: 981px)');
  var ticking = false;
  var holder = document.querySelector('.tour-device');
  function centre() {
    if (!holder || !wide.matches) return;
    var room = window.innerHeight - holder.offsetHeight;
    holder.style.setProperty('--dev-top', Math.max(84, Math.round(room / 2 + 20)) + 'px');
  }
  function tilt() {
    ticking = false;
    if (!screen || reduce || !wide.matches) {
      if (screen) { screen.style.setProperty('--tilt', 0); screen.style.setProperty('--sc', 1); }
      return;
    }
    var r = device.getBoundingClientRect();
    var vh = window.innerHeight;
    var p = Math.max(0, Math.min(1, (vh - r.top) / (vh * 0.85)));
    var e = 1 - Math.pow(1 - p, 3);
    screen.style.setProperty('--tilt', ((1 - e) * 24).toFixed(2));
    screen.style.setProperty('--sc', (0.9 + e * 0.1).toFixed(4));
  }
  function onScroll() { if (!ticking) { ticking = true; requestAnimationFrame(tilt); } }
  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', function () { centre(); onScroll(); }, { passive: true });
  window.addEventListener('load', centre);
  centre();
  tilt();

  filter();
})();
