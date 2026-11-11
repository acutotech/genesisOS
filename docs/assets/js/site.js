/* Page behaviour: navigation, scroll reveals, the Imager and terminal animations,
   the pin header, the sensor scan, Genesis Settings tabs, counters and the
   feature index.
   Everything here is progressive: without scripts the page is plain and complete. */
(function () {
  'use strict';

  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var finePointer = window.matchMedia && window.matchMedia('(hover: hover) and (pointer: fine)').matches;
  var $ = function (id) { return document.getElementById(id); };
  var all = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var NS = 'http://www.w3.org/2000/svg';

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, reduce ? 0 : ms); }); }
  function whenSeen(node, fn, margin) {
    if (!node) return;
    if (!('IntersectionObserver' in window)) { fn(); return; }
    var io = new IntersectionObserver(function (entries) {
      if (entries.some(function (e) { return e.isIntersecting; })) { io.disconnect(); fn(); }
    }, { rootMargin: margin || '0px 0px -18% 0px' });
    io.observe(node);
  }

  // ----------------------------------------------------- navigation, progress
  var nav = $('nav');
  var bar = $('progress-bar');
  var scrolling = false;
  function onScroll() {
    scrolling = false;
    var y = window.scrollY || window.pageYOffset;
    var max = document.documentElement.scrollHeight - window.innerHeight;
    if (nav) nav.classList.toggle('is-scrolled', y > 24);
    if (bar) bar.style.transform = 'scaleX(' + (max > 0 ? Math.min(1, y / max) : 0).toFixed(4) + ')';
    explode();
  }
  window.addEventListener('scroll', function () { if (!scrolling) { scrolling = true; requestAnimationFrame(onScroll); } }, { passive: true });
  window.addEventListener('resize', function () { if (!scrolling) { scrolling = true; requestAnimationFrame(onScroll); } }, { passive: true });

  var toggle = document.querySelector('.nav-toggle');
  var menu = $('nav-menu');
  function closeMenu() { if (menu) menu.classList.remove('is-open'); if (toggle) toggle.setAttribute('aria-expanded', 'false'); }
  if (toggle && menu) {
    toggle.addEventListener('click', function () {
      var open = menu.classList.toggle('is-open');
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    menu.addEventListener('click', function (e) { if (e.target.closest('a')) closeMenu(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeMenu(); });
  }

  if ('IntersectionObserver' in window && menu) {
    var links = {};
    all('a[href^="#"]', menu).forEach(function (a) { links[a.getAttribute('href').slice(1)] = a; });
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        Object.keys(links).forEach(function (id) { links[id].classList.toggle('is-current', id === e.target.id); });
      });
    }, { rootMargin: '-45% 0px -50% 0px' });
    Object.keys(links).forEach(function (id) { var s = $(id); if (s) spy.observe(s); });
  }

  // ------------------------------------------------------------------ reveal
  if ('IntersectionObserver' in window && !reduce) {
    var seen = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add('is-in'); seen.unobserve(e.target); }
      });
    }, { rootMargin: '0px 0px -12% 0px', threshold: 0.08 });
    all('[data-reveal]').forEach(function (n) { seen.observe(n); });
  } else {
    all('[data-reveal]').forEach(function (n) { n.classList.add('is-in'); });
  }

  // -------------------------------------------- card spotlight, magnetic buttons
  if (finePointer) {
    document.addEventListener('pointermove', function (e) {
      var card = e.target.closest && e.target.closest('.card');
      if (!card) return;
      var r = card.getBoundingClientRect();
      card.style.setProperty('--mx', (e.clientX - r.left) + 'px');
      card.style.setProperty('--my', (e.clientY - r.top) + 'px');
    }, { passive: true });

    if (!reduce) {
      all('.magnetic').forEach(function (b) {
        b.addEventListener('pointermove', function (e) {
          var r = b.getBoundingClientRect();
          var x = (e.clientX - r.left - r.width / 2) / r.width;
          var y = (e.clientY - r.top - r.height / 2) / r.height;
          b.style.transform = 'translate(' + (x * 10).toFixed(1) + 'px,' + (y * 8).toFixed(1) + 'px)';
        });
        b.addEventListener('pointerleave', function () { b.style.transform = ''; });
      });
    }
  }

  // ------------------------------------------- the glass layers pull apart
  var layers = $('layers');
  function explode() {
    if (!layers) return;
    if (reduce) { layers.style.setProperty('--gap', '46px'); return; }
    var r = layers.getBoundingClientRect();
    var vh = window.innerHeight;
    if (r.bottom < -100 || r.top > vh + 100) return;
    var p = Math.max(0, Math.min(1, (vh - r.top) / (vh * 0.9)));
    var max = window.innerWidth < 640 ? 38 : 64;
    layers.style.setProperty('--gap', (p * p * (3 - 2 * p) * max).toFixed(1) + 'px');
  }

  // ------------------------------------------------------------ Imager demo
  var imager = $('imager-demo');
  var imRun = 0;
  function runImager() {
    if (!imager) return;
    var run = ++imRun;
    var fields = all('em[data-type]', imager);
    var fill = $('im-fill');
    var status = $('im-status');
    var button = $('im-replay');
    var boots = all('#boot-list li');
    var alive = function () { return run === imRun; };

    fields.forEach(function (f) { f.textContent = ''; f.parentNode.classList.remove('is-typing'); });
    boots.forEach(function (b) { b.classList.remove('done'); });
    fill.style.transition = 'none';
    fill.style.width = '0';
    status.textContent = 'Ready';
    status.className = '';
    button.disabled = true;

    var chain = sleep(350);
    fields.forEach(function (f) {
      chain = chain.then(function () {
        if (!alive()) return;
        var text = f.getAttribute('data-type');
        f.parentNode.classList.add('is-typing');
        var p = Promise.resolve();
        text.split('').forEach(function (ch) {
          p = p.then(function () { if (alive()) f.textContent += ch; return sleep(f.classList.contains('pw') ? 46 : 22); });
        });
        return p.then(function () { f.parentNode.classList.remove('is-typing'); return sleep(160); });
      });
    });
    chain.then(function () {
      if (!alive()) return;
      status.textContent = 'Writing the card…';
      fill.style.transition = reduce ? 'none' : 'width 2.4s cubic-bezier(.3,.1,.3,1)';
      fill.style.width = '72%';
      return sleep(2500);
    }).then(function () {
      if (!alive()) return;
      status.textContent = 'Reading it back…';
      fill.style.transition = reduce ? 'none' : 'width 1.3s ease';
      fill.style.width = '100%';
      return sleep(1450);
    }).then(function () {
      if (!alive()) return;
      status.textContent = 'Done: written and verified.';
      status.className = 'ok';
      var p = sleep(500);
      boots.forEach(function (b) {
        p = p.then(function () { if (alive()) b.classList.add('done'); return sleep(b.classList.contains('wipe') ? 0 : 330); });
      });
      return p;
    }).then(function () {
      if (!alive()) return;
      button.disabled = false;
      button.textContent = 'Write again';
    });
  }
  whenSeen(imager, runImager, '0px 0px -30% 0px');
  if ($('im-replay')) $('im-replay').addEventListener('click', runImager);

  // --------------------------------------------------------------- terminals
  function prompt(body) {
    var a = el('span', 'pr', 'nodoai@nodobox');
    var b = el('span', 'pd', '~');
    body.appendChild(a);
    body.appendChild(document.createTextNode(':'));
    body.appendChild(b);
    body.appendChild(document.createTextNode(' $ '));
  }
  function runTerminal(body) {
    var script;
    try { script = JSON.parse(body.getAttribute('data-term')); } catch (e) { return; }
    body.textContent = '';
    var caret = el('span', 'caret');
    var chain = Promise.resolve();
    script.forEach(function (step) {
      chain = chain.then(function () {
        prompt(body);
        var cmd = document.createTextNode('');
        body.appendChild(cmd);
        body.appendChild(caret);
        var p = sleep(420);
        step.c.split('').forEach(function (ch) {
          p = p.then(function () { cmd.data += ch; return sleep(26 + (ch === ' ' ? 40 : 0)); });
        });
        return p.then(function () { return sleep(260); }).then(function () {
          caret.remove();
          body.appendChild(document.createTextNode('\n'));
          if (step.o) body.appendChild(el('span', 'o', step.o + '\n'));
          return sleep(380);
        });
      });
    });
    chain.then(function () { prompt(body); body.appendChild(caret); });
  }
  all('.term-body[data-term]').forEach(function (body) { whenSeen(body, function () { runTerminal(body); }, '0px 0px -25% 0px'); });

  // -------------------------------------------------------------- pin header
  var gpio = $('gpio');
  var cap = $('gpio-cap');
  var BUSES = {
    i2c: { pins: { 3: 'SDA', 5: 'SCL' }, text: 'I²C: SDA on pin 3 (GPIO 2), SCL on pin 5 (GPIO 3)' },
    spi: { pins: { 19: 'MOSI', 21: 'MISO', 23: 'SCLK', 24: 'CE0', 26: 'CE1' }, text: 'SPI: MOSI pin 19, MISO pin 21, SCLK pin 23, CE0 pin 24, CE1 pin 26' },
    uart: { pins: { 8: 'TXD', 10: 'RXD' }, text: 'Serial port: TXD on pin 8 (GPIO 14), RXD on pin 10 (GPIO 15)' },
    w1: { pins: { 7: 'DATA' }, text: '1-Wire: data on pin 7 (GPIO 4), once you switch it on' }
  };
  var POWER = { 1: 1, 2: 1, 4: 1, 17: 1 };
  var GROUND = { 6: 1, 9: 1, 14: 1, 20: 1, 25: 1, 30: 1, 34: 1, 39: 1 };
  var pinNodes = {};
  if (gpio) {
    var order = [];
    var n;
    for (n = 2; n <= 40; n += 2) order.push(n);   // top row: even pins
    for (n = 1; n <= 39; n += 2) order.push(n);   // bottom row: odd pins, pin 1 at the left
    order.forEach(function (num) {
      var pin = el('span', 'pin ' + (num % 2 ? 'bot' : 'top') + (POWER[num] ? ' pwr' : GROUND[num] ? ' gnd' : ''));
      pin.title = 'Pin ' + num;
      pin.appendChild(el('b'));
      gpio.appendChild(pin);
      pinNodes[num] = pin;
    });
  }
  function selectBus(name) {
    var bus = BUSES[name];
    if (!bus || !gpio) return;
    gpio.classList.add('has-sel');
    Object.keys(pinNodes).forEach(function (num) {
      var label = bus.pins[num];
      pinNodes[num].classList.toggle('lit', !!label);
      pinNodes[num].firstChild.textContent = label || '';
    });
    cap.textContent = bus.text;
    all('#hw-chips [data-bus]').forEach(function (c) {
      c.classList.toggle('is-sel', c.dataset.bus === name);
      c.setAttribute('aria-pressed', c.dataset.bus === name ? 'true' : 'false');
    });
  }
  all('#hw-chips [data-bus]').forEach(function (c) {
    c.addEventListener('click', function () { selectBus(c.dataset.bus); });
    c.addEventListener('pointerenter', function () { if (finePointer) selectBus(c.dataset.bus); });
    c.addEventListener('focus', function () { selectBus(c.dataset.bus); });
  });
  selectBus('i2c');

  // -------------------------------------------------------------- sensor scan
  var scanGrid = $('scan-grid');
  if (scanGrid) {
    var boxes = [];
    'ABCDEFGHIJK'.split('').forEach(function (letter, i) {
      var base = 0x10 + i * 4;
      var box = el('div', 'sbox');
      box.appendChild(el('b', '', letter));
      box.appendChild(el('span', '', '0x' + base.toString(16).toUpperCase() + '–' + (base + 2).toString(16).toUpperCase()));
      scanGrid.appendChild(box);
      boxes.push(box);
    });
    var FOUND = [0, 2, 5]; // A, C and F, as in the desktop above
    if (reduce) {
      FOUND.forEach(function (i) { boxes[i].classList.add('is-found'); });
    } else {
      var scanTimer = 0;
      var pos = 0;
      var sweep = 0;
      var stepScan = function () {
        boxes.forEach(function (b, i) { b.classList.toggle('is-probe', i === pos); });
        if (FOUND.indexOf(pos) !== -1 && sweep >= FOUND.indexOf(pos)) boxes[pos].classList.add('is-found');
        pos++;
        if (pos >= boxes.length) {
          pos = 0;
          sweep++;
          if (sweep > 7) { sweep = 0; boxes.forEach(function (b) { b.classList.remove('is-found'); }); }
        }
      };
      if ('IntersectionObserver' in window) {
        new IntersectionObserver(function (entries) {
          clearInterval(scanTimer);
          if (entries[0].isIntersecting) scanTimer = setInterval(stepScan, 170);
        }).observe(scanGrid);
      } else {
        scanTimer = setInterval(stepScan, 170);
      }
    }
  }

  // --------------------------------------------------------- Genesis Settings
  // what each page of the real app holds (genesis_settings.py)
  // rows: [title, switch state or value text, subtitle]; titles and subtitles are the app's own
  var PAGES = {
    interfaces: {
      title: 'Interfaces',
      text: 'The header pins and remote access. VNC is served by wayvnc and works with any VNC viewer on port 5900. SSH and VNC accept the account password.',
      rows: [
        ['I2C', true, 'Inter-Integrated Circuit bus on GPIO 2/3'],
        ['SPI', true, 'Serial Peripheral Interface on GPIO 7-11'],
        ['Serial port', true, 'UART on GPIO 14/15 for devices (Pi 5: uart0)'],
        ['Serial console', false, 'Login shell on the serial port (usually off)'],
        ['1-Wire', false, 'Dallas 1-Wire bus (w1-gpio overlay)'],
        ['Camera auto-detect', true, 'Detect CSI cameras at boot'],
        ['SSH server', true, 'Remote shell access (port 22)'],
        ['VNC server', true, 'Remote desktop with wayvnc (port 5900)']
      ]
    },
    system: {
      title: 'System',
      text: 'Who the box is and how it starts.',
      rows: [
        ['Hostname', 'nodobox', 'Also reachable as <hostname>.local on the local network'],
        ['Time zone', 'Asia/Kolkata', 'Area/City, for example Asia/Kolkata'],
        ['Language', 'en_US.UTF-8', 'System locale (log out to apply)'],
        ['Desktop auto-login', true, 'Log straight into the desktop at boot'],
        ['Boot splash', true, 'Show the Genesis logo while booting'],
        ['Password-less sudo', false, 'Do not ask for the password for admin commands (less secure)'],
        ['Account password', 'Change password', 'Change the password of the current user'],
        ['Power', 'Reboot · Shut down', 'Restart or switch off the device']
      ]
    },
    appearance: {
      title: 'Appearance',
      text: 'Everything on this page changes the desktop the moment you set it.',
      rows: [
        ['Wallpaper', 'Horizon', 'Click a wallpaper to use it on the desktop'],
        ['Accent colour', '#4f8cff', 'Used by the panel, launcher, controls and applications'],
        ['Panel position', 'Top', 'Where the floating panel lives'],
        ['Glass opacity', '62%', 'How solid the panel and cards look over the wallpaper'],
        ['Blank screen after', '10 minutes', 'Turns the display off when idle'],
        ['Advanced', 'shell.conf', 'Pinned apps, clock format and more in shell.conf']
      ]
    },
    connectivity: {
      title: 'Connectivity',
      text: 'Networks and wireless devices.',
      rows: [
        ['Wi-Fi', 'Open', 'Join or forget wireless networks'],
        ['Wired and advanced', 'Open', 'Static addresses, VPN, hotspots'],
        ['Bluetooth', 'Open', 'Pair audio devices, keyboards and phones'],
        ['Wi-Fi country', 'IN', 'Sets the allowed channels; required before Wi-Fi works'],
        ['IP addresses', '', 'The addresses the box has right now']
      ]
    },
    display: {
      title: 'Display & Sound',
      text: 'Screens and audio devices.',
      rows: [
        ['Displays', 'Open', 'Resolution, rotation and arrangement'],
        ['Audio', 'Open', 'Output device, volume and input levels']
      ]
    },
    software: {
      title: 'Software',
      text: 'Updates and the tools that come with the box.',
      rows: [
        ['Raspberry Pi Configuration', 'Open', 'The full Raspberry Pi system settings tool'],
        ['Recommended software', 'Open', 'Install curated applications'],
        ['Update the system', 'Update', 'Fetch and install updates in a terminal'],
        ['Visual Studio Code', 'Open', 'Editor with the Python extension']
      ]
    },
    about: {
      title: 'About',
      text: 'What this box is running, at a glance.',
      rows: [
        ['Genesis OS', '', 'Image name and build date'],
        ['Base', '', 'The Debian release underneath'],
        ['Device', '', 'The board model'],
        ['Kernel', '', 'The running kernel version'],
        ['Memory', '', 'Installed memory'],
        ['Storage', '', 'Used and free space'],
        ['Python', '', 'Your default python and the system python3'],
        ['Visual Studio Code', '', 'The installed version'],
        ['Session', 'genesis', 'The desktop session in use'],
        ['Source', '', 'The commit the image was built from']
      ]
    }
  };
  var tabs = all('#set-tabs [role="tab"]');
  var panel = $('set-panel');
  function showPage(tab, focus) {
    var page = PAGES[tab.dataset.page];
    tabs.forEach(function (t) {
      var on = t === tab;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
    });
    if (focus) tab.focus();
    if (!panel || !page) return;
    panel.setAttribute('aria-labelledby', tab.id);
    panel.textContent = '';
    panel.appendChild(el('h3', '', page.title));
    panel.appendChild(el('p', '', page.text));
    var rows = el('div', 'set-rows');
    page.rows.forEach(function (r, i) {
      var row = el('div', 'set-row');
      row.style.setProperty('--i', i);
      var left = el('span');
      left.appendChild(el('b', '', r[0]));
      if (r[2]) left.appendChild(el('small', '', r[2]));
      row.appendChild(left);
      if (r[1] === true || r[1] === false) row.appendChild(el('i', 'sw' + (r[1] ? ' on' : '')));
      else if (r[1]) row.appendChild(el('em', '', r[1]));
      rows.appendChild(row);
    });
    panel.appendChild(rows);
  }
  tabs.forEach(function (tab, i) {
    tab.addEventListener('click', function () { showPage(tab); });
    tab.addEventListener('keydown', function (e) {
      var step = e.key === 'ArrowDown' || e.key === 'ArrowRight' ? 1 : e.key === 'ArrowUp' || e.key === 'ArrowLeft' ? -1 : 0;
      if (e.key === 'Home') { e.preventDefault(); showPage(tabs[0], true); return; }
      if (e.key === 'End') { e.preventDefault(); showPage(tabs[tabs.length - 1], true); return; }
      if (!step) return;
      e.preventDefault();
      showPage(tabs[(i + step + tabs.length) % tabs.length], true);
    });
  });
  if (tabs.length) showPage(tabs[0]);

  // ---------------------------------------------------------------- counters
  all('[data-count]').forEach(function (node) {
    var target = parseInt(node.getAttribute('data-count'), 10);
    if (reduce || !('IntersectionObserver' in window)) { node.textContent = target; return; }
    whenSeen(node, function () {
      var t0 = performance.now();
      var dur = 1500;
      (function frame(now) {
        var p = Math.min(1, (now - t0) / dur);
        node.textContent = Math.round(target * (1 - Math.pow(1 - p, 3)));
        if (p < 1) requestAnimationFrame(frame);
      })(t0);
    }, '0px 0px -10% 0px');
  });

  // ----------------------------------------------------------- feature index
  // Without scripts the whole list is on the page. With them: one area at a time,
  // a search across all of it, and long groups folded to their first two rows.
  (function () {
    var box = $('idx');
    if (!box) return;
    var chips = all('.idx-chip', box);
    var groups = all('.idx-group', box);
    var input = $('idx-q');
    var count = $('idx-count');
    var emptyMsg = $('idx-empty');
    var PREVIEW = 6;
    var cat = 'all';
    var total = 0;

    var data = groups.map(function (g) {
      var rows = all('li', g).map(function (li) {
        var name = li.querySelector('b');
        // names that carry markup (code, kbd) are left as they are when searching
        return { node: li, name: name, text: li.textContent.toLowerCase(), plain: !name.children.length, label: name.textContent };
      });
      total += rows.length;
      var more = el('button', 'idx-more');
      more.type = 'button';
      g.appendChild(more);
      var entry = { node: g, cat: g.dataset.cat, title: g.querySelector('h3').firstChild.textContent.trim(), rows: rows, more: more, open: false };
      more.addEventListener('click', function () { entry.open = true; apply(true, entry); });
      return entry;
    });

    function escapeRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

    // mark the searched word in a name
    function mark(row, words) {
      if (!row.plain) return;
      row.name.textContent = row.label;
      if (!words.length) return;
      var re = new RegExp('(' + words.map(escapeRe).join('|') + ')', 'ig');
      row.name.textContent = '';
      row.label.split(re).forEach(function (part, i) {
        if (!part) return;
        row.name.appendChild(i % 2 ? el('mark', '', part) : document.createTextNode(part));
      });
    }

    function apply(animate, only) {
      var q = input.value.trim().toLowerCase();
      var words = q ? q.split(/\s+/) : [];
      var shown = 0;
      var fresh = [];
      data.forEach(function (g) {
        var inCat = cat === 'all' || g.cat === cat;
        var unfolded = cat !== 'all' || words.length > 0 || g.open;
        var matched = 0;
        var visible = 0;
        g.rows.forEach(function (row) {
          var ok = inCat && words.every(function (w) { return row.text.indexOf(w) !== -1; });
          if (ok) matched++;
          var show = ok && (unfolded || matched <= PREVIEW);
          if (show) {
            visible++;
            if (row.node.hidden && (!only || only === g)) fresh.push(row.node);
          }
          row.node.hidden = !show;
          row.node.classList.remove('is-in');
          mark(row, words);
        });
        g.node.hidden = matched === 0;
        g.more.hidden = matched - visible <= 0;
        g.more.textContent = 'Show all ' + matched;
        shown += matched;
      });
      emptyMsg.hidden = shown !== 0;
      if (words.length) count.textContent = shown + ' of ' + total + ' match';
      else if (cat === 'all') count.textContent = total + ' entries in ' + data.length + ' groups';
      else count.textContent = shown + ' entries';
      if (animate && !reduce) {
        void box.offsetWidth; // one reflow, so the entrance animation restarts
        fresh.slice(0, 60).forEach(function (node, i) {
          node.style.setProperty('--n', Math.min(i, 30));
          node.classList.add('is-in');
        });
      }
    }

    chips.forEach(function (chip) {
      chip.addEventListener('click', function () {
        cat = chip.dataset.cat;
        chips.forEach(function (c) {
          var on = c === chip;
          c.classList.toggle('is-on', on);
          c.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
        data.forEach(function (g) { g.rows.forEach(function (r) { r.node.hidden = true; }); });
        apply(true);
      });
    });
    var typing = 0;
    input.addEventListener('input', function () {
      clearTimeout(typing);
      typing = setTimeout(function () { apply(false); }, 90);
    });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && input.value) { input.value = ''; apply(false); }
    });
    apply(false);
  })();

  // the icons in the sprite are referenced with href; very old Safari wants xlink:href
  if (!('href' in document.createElementNS(NS, 'use'))) {
    all('use').forEach(function (u) { u.setAttributeNS('http://www.w3.org/1999/xlink', 'xlink:href', u.getAttribute('href')); });
  }

  onScroll();
})();
