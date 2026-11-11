/* Hero background: a slow aurora in the wallpaper's colours (WebGL fragment shader)
   and the constellation lines of the Genesis artwork (2D canvas).
   Both stop when the hero is off screen or the tab is hidden, and draw a single
   still frame when the visitor prefers reduced motion. No WebGL: the CSS gradient
   behind the canvas stays. */
(function () {
  'use strict';

  var hero = document.getElementById('top');
  var aurora = document.getElementById('aurora');
  var stars = document.getElementById('stars');
  if (!hero || !aurora || !stars) return;

  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var visible = true;
  var mouse = { x: 0.5, y: 0.5, tx: 0.5, ty: 0.5, px: -9999, py: -9999 };

  // ------------------------------------------------------------------ aurora
  var VERT = 'attribute vec2 p; void main(){ gl_Position = vec4(p, 0.0, 1.0); }';
  var FRAG = [
    'precision mediump float;',
    'uniform vec2 u_res; uniform float u_t; uniform vec2 u_m;',
    'float hash(vec2 p){ p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }',
    'float noise(vec2 p){ vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);',
    '  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x), f.y); }',
    'float fbm(vec2 p){ float v = 0.0, a = 0.5; for (int i = 0; i < 4; i++) { v += a * noise(p); p = p * 2.03 + vec2(1.7, 9.2); a *= 0.5; } return v; }',
    'void main(){',
    '  vec2 uv = gl_FragCoord.xy / u_res;',
    '  vec2 p = (gl_FragCoord.xy - 0.5 * u_res) / u_res.y;',
    '  float t = u_t * 0.045;',
    '  vec2 q = vec2(fbm(p * 1.15 + vec2(0.0, t)), fbm(p * 1.15 + vec2(5.2, -t * 1.3)));',
    '  vec2 r = vec2(fbm(p * 1.5 + 3.2 * q + vec2(1.7, 9.2) + t * 0.7), fbm(p * 1.5 + 3.2 * q + vec2(8.3, 2.8) - t * 0.5));',
    '  float f = fbm(p * 1.25 + 2.8 * r + (u_m - 0.5) * 0.35);',
    '  vec3 deep = vec3(0.027, 0.043, 0.071);',
    '  vec3 navy = vec3(0.062, 0.110, 0.200);',
    '  vec3 steel = vec3(0.200, 0.345, 0.600);',
    '  vec3 teal = vec3(0.120, 0.470, 0.540);',
    '  vec3 col = mix(deep, navy, smoothstep(0.15, 0.80, f));',
    '  col = mix(col, steel, smoothstep(0.42, 0.98, f * f * 1.7 + q.x * 0.34) * 0.72);',
    '  col = mix(col, teal, smoothstep(0.50, 1.00, r.y * f * 2.0) * 0.42);',
    '  col += vec3(0.090, 0.150, 0.270) * pow(max(0.0, 1.0 - length(uv - vec2(0.80, 0.98))), 2.3);',
    '  float v = smoothstep(1.20, 0.20, length(p * vec2(0.78, 1.08)));',
    '  col = mix(deep, col, v);',
    '  col += (hash(gl_FragCoord.xy + fract(u_t)) - 0.5) * 0.012;',
    '  gl_FragColor = vec4(col, 1.0);',
    '}'
  ].join('\n');

  var gl = null;
  var uRes, uT, uM;
  try {
    gl = aurora.getContext('webgl', { antialias: false, alpha: false, depth: false, stencil: false, powerPreference: 'low-power' });
  } catch (e) { gl = null; }

  function compile(type, src) {
    var s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    return gl.getShaderParameter(s, gl.COMPILE_STATUS) ? s : null;
  }

  if (gl) {
    var vs = compile(gl.VERTEX_SHADER, VERT);
    var fs = compile(gl.FRAGMENT_SHADER, FRAG);
    var prog = vs && fs ? gl.createProgram() : null;
    if (prog) {
      gl.attachShader(prog, vs);
      gl.attachShader(prog, fs);
      gl.linkProgram(prog);
    }
    if (!prog || !gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      gl = null;
    } else {
      gl.useProgram(prog);
      var buf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
      var loc = gl.getAttribLocation(prog, 'p');
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
      uRes = gl.getUniformLocation(prog, 'u_res');
      uT = gl.getUniformLocation(prog, 'u_t');
      uM = gl.getUniformLocation(prog, 'u_m');
      aurora.addEventListener('webglcontextlost', function (e) { e.preventDefault(); gl = null; aurora.classList.remove('is-on'); });
    }
  }

  // the picture is soft, so half resolution is plenty and keeps small GPUs cool
  function sizeAurora() {
    if (!gl) return;
    var w = Math.max(2, Math.round(hero.clientWidth * 0.5));
    var h = Math.max(2, Math.round(hero.clientHeight * 0.5));
    if (aurora.width !== w || aurora.height !== h) {
      aurora.width = w;
      aurora.height = h;
      gl.viewport(0, 0, w, h);
    }
  }

  function drawAurora(seconds) {
    if (!gl) return;
    gl.uniform2f(uRes, aurora.width, aurora.height);
    gl.uniform1f(uT, seconds);
    gl.uniform2f(uM, mouse.x, 1 - mouse.y);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  // ----------------------------------------------------------- constellation
  var ctx = stars.getContext('2d');
  var nodes = [];
  var dpr = 1;
  var W = 0;
  var H = 0;
  var LINK = 150;

  function seed(i) { // deterministic scatter: the same sky on every visit
    var x = Math.sin(i * 127.1 + 311.7) * 43758.5453;
    return x - Math.floor(x);
  }

  function sizeStars() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = hero.clientWidth;
    H = hero.clientHeight;
    stars.width = Math.round(W * dpr);
    stars.height = Math.round(H * dpr);
    var count = Math.max(24, Math.min(84, Math.round((W * H) / 24000)));
    nodes = [];
    for (var i = 0; i < count; i++) {
      nodes.push({
        x: seed(i * 3 + 1) * W,
        y: seed(i * 3 + 2) * H,
        vx: (seed(i * 3 + 3) - 0.5) * 0.16,
        vy: (seed(i * 3 + 4) - 0.5) * 0.16,
        r: 0.6 + seed(i * 3 + 5) * 1.2
      });
    }
  }

  function drawStars(step) {
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    var i, j, a, b, dx, dy, d;
    for (i = 0; i < nodes.length; i++) {
      a = nodes[i];
      a.x += a.vx * step;
      a.y += a.vy * step;
      if (a.x < -20) a.x = W + 20; else if (a.x > W + 20) a.x = -20;
      if (a.y < -20) a.y = H + 20; else if (a.y > H + 20) a.y = -20;
    }
    ctx.lineWidth = 1;
    for (i = 0; i < nodes.length; i++) {
      a = nodes[i];
      for (j = i + 1; j < nodes.length; j++) {
        b = nodes[j];
        dx = a.x - b.x;
        dy = a.y - b.y;
        d = dx * dx + dy * dy;
        if (d < LINK * LINK) {
          ctx.strokeStyle = 'rgba(170,200,240,' + (0.16 * (1 - Math.sqrt(d) / LINK)).toFixed(3) + ')';
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
      }
      // lines reach for the pointer
      dx = a.x - mouse.px;
      dy = a.y - mouse.py;
      d = dx * dx + dy * dy;
      var near = d < 220 * 220 ? 1 - Math.sqrt(d) / 220 : 0;
      if (near > 0) {
        ctx.strokeStyle = 'rgba(140,190,255,' + (0.34 * near).toFixed(3) + ')';
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(mouse.px, mouse.py);
        ctx.stroke();
      }
      ctx.fillStyle = 'rgba(215,230,250,' + (0.38 + near * 0.6).toFixed(3) + ')';
      ctx.beginPath();
      ctx.arc(a.x, a.y, a.r + near * 1.3, 0, 6.2832);
      ctx.fill();
    }
  }

  // ------------------------------------------------------------------- loop
  var raf = 0;
  var last = 0;
  var start = performance.now();

  function frame(now) {
    raf = 0;
    if (!visible || document.hidden) return;
    var dt = Math.min(64, now - last || 16);
    last = now;
    mouse.x += (mouse.tx - mouse.x) * 0.04;
    mouse.y += (mouse.ty - mouse.y) * 0.04;
    drawAurora((now - start) / 1000);
    drawStars(dt / 16.7);
    raf = requestAnimationFrame(frame);
  }

  function play() {
    if (reduce || raf) return;
    last = performance.now();
    raf = requestAnimationFrame(frame);
  }

  function resize() {
    sizeAurora();
    sizeStars();
    drawAurora(reduce ? 12 : (performance.now() - start) / 1000);
    drawStars(0);
  }

  hero.addEventListener('pointermove', function (e) {
    var r = hero.getBoundingClientRect();
    mouse.tx = (e.clientX - r.left) / r.width;
    mouse.ty = (e.clientY - r.top) / r.height;
    mouse.px = e.clientX - r.left;
    mouse.py = e.clientY - r.top;
  }, { passive: true });
  hero.addEventListener('pointerleave', function () { mouse.px = mouse.py = -9999; }, { passive: true });

  var resizeTimer = 0;
  window.addEventListener('resize', function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(resize, 120);
  }, { passive: true });

  document.addEventListener('visibilitychange', function () { if (!document.hidden) play(); });

  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function (entries) {
      visible = entries[0].isIntersecting;
      if (visible) play();
    }, { threshold: 0 }).observe(hero);
  }

  resize();
  if (gl) aurora.classList.add('is-on');
  play();
})();
