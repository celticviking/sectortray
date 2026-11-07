/* SectorTray core UI library (from design/handoff_v1/app.js; defaults: Toolchest, Dark).
   - Preferences (unit, fraction precision, skin, theme) in localStorage
   - Unit parsing + formatting (the signature interaction)
   - Plan-view SVG generator (the drawing rules documented in DESIGN.md)
   - Small helpers: toasts, length rendering, header wiring
   Everything hangs off window.ST. No dependencies. */
(function () {
  'use strict';
  var IN = 25.4, G = 42, U = 7, TOL_MM = 1.5875; // 1/16"
  var LS = window.localStorage;
  var root = document.documentElement;

  /* ---------------- Preferences ---------------- */
  var P = {
    unit: LS.getItem('st.unit') || 'in',
    den: +(LS.getItem('st.den') || 16),
    skin: LS.getItem('st.skin') || 'toolchest',
    theme: LS.getItem('st.theme') || 'dark'
  };
  function applyTheme() {
    root.dataset.skin = P.skin;
    if (P.theme === 'system') delete root.dataset.theme; else root.dataset.theme = P.theme;
  }
  function effectiveTheme() {
    if (P.theme !== 'system') return P.theme;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  function setPref(k, v) {
    P[k] = v; LS.setItem('st.' + k, v);
    if (k === 'skin' || k === 'theme') applyTheme();
    document.dispatchEvent(new CustomEvent('st:prefs', { detail: { key: k, value: v } }));
  }
  applyTheme();

  /* ---------------- Number formatting ---------------- */
  function gcd(a, b) { return b ? gcd(b, a % b) : a; }
  function frac(mm, den) {
    var t = Math.round(mm / IN * den), w = Math.floor(t / den), n = t % den, d = den;
    if (n) { var g = gcd(n, d); n /= g; d /= g; }
    return { w: w, n: n, d: d };
  }
  /* One decimal, rounding half up on the decimal value (69.85 → 69.9; toFixed alone gives 69.8 because of binary floats) */
  function num(mm) { var r = Math.round(mm * 10 + 1e-9) / 10; return r % 1 === 0 ? String(r) : r.toFixed(1); }
  /* HTML: 14 ⅜″ as <span class="st-frac">14 <span class="st-frac__n">3</span>⁄<span class="st-frac__d">8</span>″</span> */
  function inHTML(mm, den) {
    var f = frac(mm, den || P.den), s = '';
    if (f.w || !f.n) s += f.w;
    if (f.n) s += (f.w ? '\u2009' : '') + '<span class="st-frac__n">' + f.n + '</span>\u2044<span class="st-frac__d">' + f.d + '</span>';
    return '<span class="st-frac">' + s + '\u2033</span>';
  }
  function mmHTML(mm) { return '<span class="st-frac">' + num(mm) + '\u2009mm</span>'; }
  function pri(mm, u, den) { return (u || P.unit) === 'in' ? inHTML(mm, den) : mmHTML(mm); }
  function sec(mm, u, den) { return (u || P.unit) === 'in' ? mmHTML(mm) : inHTML(mm, den); }
  function both(mm, u, den) { return pri(mm, u, den) + ' <span class="st-len__sec">(' + sec(mm, u, den).replace(/<\/?span[^>]*>/g, '') + ')</span>'; }
  function pair(a, b, u, den) {
    u = u || P.unit;
    if (u === 'mm') return '<span class="st-frac">' + num(a) + ' × ' + num(b) + '\u2009mm</span>';
    return inHTML(a, den) + ' × ' + inHTML(b, den);
  }
  function tol(u) { return '±' + ((u || P.unit) === 'in' ? inHTML(TOL_MM, 16) : '1.6\u2009mm'); }
  /* Plain-text value for putting back into an input after a unit switch */
  function fmtText(mm, u, den) {
    if (u === 'mm') return num(mm) + ' mm';
    var f = frac(mm, den || P.den);
    return (f.w && f.n ? f.w + ' ' + f.n + '/' + f.d : f.n ? f.n + '/' + f.d : String(f.w)) + '"';
  }

  /* ---------------- Parsing ----------------
     Accepts: 14 3/8 · 14-3/8" · 14.375 in · 3/8 · 365 mm · 36.5 cm · 14 3/8 inches
     Returns { status: empty|parsed|ambiguous|error, mm, msg } */
  // also: 36,5 cm (comma decimals) · 1.5 ft · 2' · 0.4 m
  var UNITS = { mm: 1, cm: 10, m: 1000, in: IN, ft: 12 * IN };
  function parse(raw, primary, min) {
    primary = primary || P.unit; min = min == null ? G : min;
    var s = String(raw || '').trim().toLowerCase().replace(/[\u201d\u2033\u02ba]/g, '"').replace(/\s+/g, ' ');
    s = s.replace(/[’′]/g, '\'').replace(/(\d),(\d)/g, '$1.$2');  // 2′ = 2'; 36,5 = 36.5
    if (!s) return { status: 'empty', msg: '' };
    var unit = null;
    var um = s.match(/\s*(millimet(?:er|re)s?|mm|centimet(?:er|re)s?|cm|met(?:er|re)s?|m|inches|inch|in|"|feet|foot|ft|')\.?$/);
    if (um) {
      var t = um[1];
      unit = /^(mm|millim)/.test(t) ? 'mm' : t[0] === 'c' ? 'cm' : t[0] === 'm' ? 'm' : /^(f|')/.test(t) ? 'ft' : 'in';
      s = s.slice(0, um.index).trim();
    }
    var m = s.match(/^(?:(\d+(?:\.\d+)?)(?:[ -]+|$))?(?:(\d+)\/(\d+))?$/);
    if (!m || (!m[1] && !m[2])) return { status: 'error', msg: 'Try 14 3/8, 14.375 in, 365 mm or 36.5 cm.' };
    if (m[3] && +m[3] === 0) return { status: 'error', msg: 'A fraction can\u2019t have 0 on the bottom.' };
    var v = (m[1] ? parseFloat(m[1]) : 0) + (m[2] ? (+m[2]) / (+m[3]) : 0), amb = null, assumed = false;
    if (!unit) {
      assumed = true; unit = primary;
      if (primary === 'mm' && (m[2] || v < Math.min(40, min))) { unit = 'in'; amb = 'No unit typed, and ' + raw.trim() + ' mm is too small. Read as inches; add \u201cmm\u201d to override.'; }
      else if (primary === 'in' && v > 60) { unit = 'mm'; amb = 'No unit typed, and ' + v + ' in is over 5 ft. Read as mm; add \u201cin\u201d to override.'; }
    }
    var mm = v * UNITS[unit];
    if (mm < min) return { status: 'error', msg: min >= G ? 'Smaller than one 42 mm grid square.' : 'Too shallow for any bin.' };
    if (mm > 1500) return { status: 'error', msg: 'Over 1.5 m. Check the number.' };
    return { status: amb ? 'ambiguous' : 'parsed', mm: mm, msg: amb || (assumed ? 'No unit typed, so read as ' + (unit === 'in' ? 'inches' : 'mm') + '.' : '') };
  }

  /* ---------------- Gridfinity layout ----------------
     Placeholder math. The backend is authoritative for bin height. */
  function layout(W, D, H, bed) {
    bed = bed || [250, 220];
    var nx = Math.max(1, Math.floor(W / G)), ny = Math.max(1, Math.floor(D / G));
    var fx = (W - nx * G) / 2, fy = (D - ny * G) / 2, lim = Math.min(bed[0], bed[1]);
    function split(n, f) {
      var m = Math.max(1, Math.floor((lim - f) / G)), k = Math.ceil(n / m), b = Math.floor(n / k), e = n % k, r = [];
      for (var i = 0; i < k; i++) r.push(b + (i < e ? 1 : 0));
      return r;
    }
    function edges(parts, f, T) {
      var acc = f; return parts.map(function (p, i) { var a = i === 0 ? 0 : acc; acc += p * G; return [a, i === parts.length - 1 ? T : acc, p]; });
    }
    var cols = split(nx, fx), rows = split(ny, fy), ce = edges(cols, fx, W), re = edges(rows, fy, D), pieces = [];
    ce.forEach(function (c, ci) {
      re.forEach(function (r, ri) {
        var w = c[1] - c[0], d = r[1] - r[0], land = w <= bed[0] && d <= bed[1], rot = w <= bed[1] && d <= bed[0];
        var o = land || !rot ? [bed[0], bed[1]] : [bed[1], bed[0]];
        pieces.push({ id: String.fromCharCode(65 + ci) + (ri + 1), ci: ci, ri: ri, x: c[0], y: r[0], w: w, d: d, cx: c[2], cy: r[2], fits: land || rot, bw: o[0], bd: o[1], sx: o[0] - w, sy: o[1] - d });
      });
    });
    return { W: W, D: D, H: H, nx: nx, ny: ny, fx: fx, fy: fy, cols: cols, rows: rows, pieces: pieces, binU: Math.max(0, Math.floor((H - 12.4) / U)) };
  }

  /* ---------------- Plan view SVG ----------------
     viewBox is in millimeters (1 unit = 1 mm). Strokes are non-scaling (px).
     Layers (bottom → top): floor, filler, grid, pieces, outline, bins, labels, dims, bed. */
  function cssVar(n) { return getComputedStyle(root).getPropertyValue(n).trim(); }
  function skinCfg() {
    return { ends: cssVar('--st-plan-ends') || 'tick', label: cssVar('--st-plan-label') || 'tape', grid: cssVar('--st-plan-grid-style') || 'both' };
  }
  function tin(mm, den) {
    var f = frac(mm, den), s = '';
    if (f.w || !f.n) s += f.w + (f.n ? '\u2009' : '');
    if (f.n) s += '<tspan font-size=".68em" dy="-.42em">' + f.n + '</tspan><tspan font-size=".68em" dy=".42em">\u2044' + f.d + '</tspan>';
    return s + '\u2033';
  }
  function planSVG(L, o) {
    o = o || {};
    var u = o.unit || P.unit, den = o.den || P.den, cfg = o.cfg || skinCfg(), mini = !!o.mini;
    var GX = (L.pitch || [G, G])[0], GY = (L.pitch || [G, G])[1];  // square size (42 × 42 unless fitted to the drawer)
    var W = L.W, D = L.D, fx = L.fx, fy = L.fy, nx = L.nx, ny = L.ny, gw = nx * GX, gd = ny * GY;
    var m = mini ? [4, 4, 4, 4] : [30, 52, 58, 34];
    var T = function (mm) { return u === 'in' ? tin(mm, den) : num(mm); };
    var T2 = function (mm) { return u === 'in' ? num(mm) : tin(mm, den); };
    var hid = 'st-hatch-' + (o.id || 'p');
    var out = [];
    var ln = function (x1, y1, x2, y2, c) { return '<line class="' + c + '" x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 + '"/>'; };
    var end = function (x, y, dx, dy) {
      if (cfg.ends === 'arrow') { var l = 4.4, w = 1.3, bx = x - dx * l, by = y - dy * l; return '<path class="st-plan__arrow" d="M' + x + ' ' + y + 'L' + (bx - dy * w) + ' ' + (by + dx * w) + 'L' + (bx + dy * w) + ' ' + (by - dx * w) + 'Z"/>'; }
      return ln(x - 1.8, y + 1.8, x + 1.8, y - 1.8, 'st-plan__tick');
    };
    var txt = function (x, y, main, sub, cls, rot) {
      return '<text class="st-plan__dim-text' + (cls ? ' ' + cls : '') + '" x="' + x + '" y="' + y + '"' + (rot ? ' transform="rotate(-90 ' + x + ' ' + y + ')"' : '') + '>' + main +
        (sub ? '<tspan class="st-plan__dim-sub" font-size=".78em" dx=".45em">(' + sub + ')</tspan>' : '') + '</text>';
    };
    out.push('<svg class="st-plan' + (mini ? ' st-plan--mini' : '') + '" viewBox="' + (-m[0]) + ' ' + (-m[1]) + ' ' + (W + m[0] + m[2]) + ' ' + (D + m[1] + m[3]) + '" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Plan view: ' + nx + ' by ' + ny + ' grid, ' + L.pieces.length + ' baseplate pieces">');
    out.push('<defs><pattern id="' + hid + '" patternUnits="userSpaceOnUse" width="3.2" height="3.2" patternTransform="rotate(45)"><line class="st-plan__hatch-line" x1="0" y1="0" x2="0" y2="3.2"/></pattern></defs>');
    out.push('<g class="st-plan__floor"><rect x="0" y="0" width="' + W + '" height="' + D + '"/></g>');
    out.push('<g class="st-plan__filler" fill="url(#' + hid + ')">' + [[0, 0, W, fy], [0, fy + gd, W, D - fy - gd], [0, fy, fx, gd], [fx + gw, fy, W - fx - gw, gd]].map(function (r) { return '<rect x="' + r[0] + '" y="' + r[1] + '" width="' + r[2] + '" height="' + r[3] + '"/>'; }).join('') + '</g>');
    var g = [];
    if (cfg.grid !== 'sockets') { for (var i = 0; i <= nx; i++) g.push(ln(fx + i * GX, fy, fx + i * GX, fy + gd, 'st-plan__grid-line')); for (var j = 0; j <= ny; j++) g.push(ln(fx, fy + j * GY, fx + gw, fy + j * GY, 'st-plan__grid-line')); }
    if (cfg.grid !== 'lines') { for (var a = 0; a < nx; a++) for (var b = 0; b < ny; b++) g.push('<rect class="st-plan__socket" x="' + (fx + a * GX + 2.6) + '" y="' + (fy + b * GY + 2.6) + '" width="' + (GX - 5.2) + '" height="' + (GY - 5.2) + '" rx="4"/>'); }
    out.push('<g class="st-plan__grid">' + g.join('') + '</g>');
    out.push('<g class="st-plan__pieces">' + L.pieces.map(function (p) {
      return '<rect class="st-plan__piece' + (p.id === o.active ? ' is-active' : '') + (p.fits ? '' : ' is-overflow') + '" data-piece="' + p.id + '"' + (mini ? '' : ' tabindex="0" role="button" aria-label="Piece ' + p.id + ', ' + p.cx + ' by ' + p.cy + ' squares"') + ' x="' + p.x + '" y="' + p.y + '" width="' + p.w + '" height="' + p.d + '"/>';
    }).join('') + '</g>');
    out.push('<g class="st-plan__outline" pointer-events="none"><rect x="0" y="0" width="' + W + '" height="' + D + '"/></g>');
    if (o.bins) out.push(binsSVG(L, o.bins));
    if (!mini) {
      out.push('<g class="st-plan__labels" pointer-events="none">' + L.pieces.map(function (p) {
        var cx = p.x + p.w / 2, cy = p.y + p.d / 2, tag;
        if (cfg.label === 'balloon') tag = '<circle class="st-plan__balloon" cx="' + cx + '" cy="' + (cy - 7) + '" r="8"/><text class="st-plan__id" text-anchor="middle" x="' + cx + '" y="' + (cy - 4) + '">' + p.id + '</text>';
        else tag = '<rect class="st-plan__tag" x="' + (cx - 10.5) + '" y="' + (cy - 13.5) + '" width="21" height="11" rx="' + (cfg.label === 'tape' ? 1 : 0) + '"/><text class="st-plan__id" text-anchor="middle" x="' + cx + '" y="' + (cy - 5.2) + '">' + p.id + '</text>';
        return '<g class="st-plan__label">' + tag +
          '<text class="st-plan__size" text-anchor="middle" x="' + cx + '" y="' + (cy + 8) + '">' + T(p.w) + ' × ' + T(p.d) + '</text>' +
          '<text class="st-plan__cells" text-anchor="middle" x="' + cx + '" y="' + (cy + 17) + '">' + p.cx + ' × ' + p.cy + ' SQ</text></g>';
      }).join('') + '</g>');
      var d = [], hx = [0, fx, fx + gw, W], t1 = -17, t2 = -39, k;
      hx.forEach(function (x, i) { d.push(ln(x, -2, x, (i === 0 || i === 3 ? t2 : t1) - 2.5, 'st-plan__ext')); });
      d.push(ln(0, t1, W, t1, 'st-plan__dim'), ln(0, t2, W, t2, 'st-plan__dim'));
      for (k = 0; k < 3; k++) { if (cfg.ends === 'arrow') d.push(end(hx[k], t1, -1, 0), end(hx[k + 1], t1, 1, 0)); else d.push(end(hx[k], t1, 0, 0)); }
      if (cfg.ends !== 'arrow') d.push(end(W, t1, 0, 0));
      d.push(end(0, t2, -1, 0), end(W, t2, 1, 0));
      d.push(txt(fx + gw / 2, t1 - 3, nx + ' SQ · ' + T(gw)));
      d.push(txt(W / 2, t2 - 3, T(W), T2(W), 'st-plan__dim-text--overall'));
      var vy = [0, fy, fy + gd, D], r1 = W + 17, r2 = W + 41;
      vy.forEach(function (y, i) { d.push(ln(W + 2, y, (i === 0 || i === 3 ? r2 : r1) + 2.5, y, 'st-plan__ext')); });
      d.push(ln(r1, 0, r1, D, 'st-plan__dim'), ln(r2, 0, r2, D, 'st-plan__dim'));
      for (k = 0; k < 3; k++) { if (cfg.ends === 'arrow') d.push(end(r1, vy[k], 0, -1), end(r1, vy[k + 1], 0, 1)); else d.push(end(r1, vy[k], 0, 0)); }
      if (cfg.ends !== 'arrow') d.push(end(r1, D, 0, 0));
      d.push(end(r2, 0, 0, -1), end(r2, D, 0, 1));
      d.push(txt(r1 - 3, fy + gd / 2, ny + ' SQ · ' + T(gd), null, '', true));
      d.push(txt(r2 - 3, D / 2, T(D), T2(D), 'st-plan__dim-text--overall', true));
      var lx = fx / 2, ly = D - 30;
      d.push('<circle class="st-plan__leader-dot" cx="' + lx + '" cy="' + ly + '" r="1.1"/>');
      d.push('<polyline class="st-plan__leader" points="' + lx + ',' + ly + ' ' + (lx + 16) + ',' + (D + 16) + ' ' + (lx + 26) + ',' + (D + 16) + '"/>');
      var fl = L.plate && L.plate.filler, even = !fl || (Math.abs(fl.left - fl.right) < 0.01 && Math.abs(fl.back - fl.front) < 0.01);
      var side = function (a, b, na, nb) { return Math.abs(a - b) < 0.01 ? T(a) + ' EA. SIDE' : (a > 0.01 ? T(a) + ' ' + na : '') + (a > 0.01 && b > 0.01 ? ' + ' : '') + (b > 0.01 ? T(b) + ' ' + nb : ''); };
      var none = fl && fl.left + fl.right + fl.back + fl.front < 0.01;
      d.push('<text class="st-plan__note" x="' + (lx + 28) + '" y="' + (D + 19) + '">' + (none ? 'NO FILLER · SQUARES ' + ST.num(GX) + ' × ' + ST.num(GY) + ' MM, FITTED TO THE DRAWER' : even ? 'FILLER EA. SIDE · W ' + T(L.plate ? L.plate.filler_x : fx) + ' · D ' + T(L.plate ? L.plate.filler_y : fy)
        : 'FILLER · W ' + side(fl.left, fl.right, 'LEFT', 'RIGHT') + ' · D ' + side(fl.back, fl.front, 'BACK', 'FRONT')) + '</text>');
      d.push('<text class="st-plan__front" x="' + W + '" y="' + (D + 19) + '">FRONT ▾</text>');
      out.push('<g class="st-plan__dims" pointer-events="none">' + d.join('') + '</g>');
      out.push('<g class="st-plan__bed" pointer-events="none">' + bedSVG(L, o.active) + '</g>');
    }
    out.push('</svg>');
    return out.join('');
  }
  function bedSVG(L, id) {
    var p = L.pieces.filter(function (q) { return q.id === id; })[0];
    if (!p) return '';
    var bx = p.x + p.w / 2 - p.bw / 2, by = p.y + p.d / 2 - p.bd / 2;
    return '<rect class="st-plan__bed-outline' + (p.fits ? '' : ' is-misfit') + '" x="' + bx + '" y="' + by + '" width="' + p.bw + '" height="' + p.bd + '"/>' +
      '<rect class="st-plan__bed-tag" x="' + bx + '" y="' + by + '" width="150" height="11"/>' +
      '<text class="st-plan__bed-text" x="' + (bx + 3.5) + '" y="' + (by + 7.8) + '">' + (L.bed ? L.bed.name.replace(/^Prusa /, '').toUpperCase() + ' BED' : 'CORE ONE BED') + (p.fits ? ' ' + p.bw + ' × ' + p.bd + ' · ' + p.id + ' FITS' : ' · ' + p.id + ' TOO BIG') + '</text>';
  }
  /* Bins drawn on the grid (layout editor, project thumbnails). x/y/w/d in grid squares. */
  function binsSVG(L, bins) {
    return '<g class="st-plan__bins">' + bins.map(function (b, i) {
      var x = L.fx + b.x * GX + 1, y = L.fy + b.y * GY + 1, w = b.w * GX - 2, d = b.d * GY - 2;
      return '<g class="st-plan__bin-g" data-bin="' + i + '"><rect class="st-plan__bin" x="' + x + '" y="' + y + '" width="' + w + '" height="' + d + '" rx="3"/>' +
        (b.label ? '<text class="st-plan__bin-label" x="' + (x + 4) + '" y="' + (y + 11) + '">' + b.label + '</text>' : '') + '</g>';
    }).join('') + '</g>';
  }
  /* Mount an interactive plan. Hover/focus shows the bed; click pins it. */
  function mountPlan(el, opts) {
    var state = { L: opts.L, pin: opts.active || null, hover: null };
    function act() { return state.hover || state.pin; }
    function render() { el.innerHTML = planSVG(state.L, { active: act(), id: opts.id, bins: opts.bins }); }
    function sync() {
      var a = act();
      el.querySelectorAll('.st-plan__piece').forEach(function (r) { r.classList.toggle('is-active', r.dataset.piece === a); });
      var bed = el.querySelector('.st-plan__bed'); if (bed) bed.innerHTML = bedSVG(state.L, a);
      if (opts.onActive) opts.onActive(a);
    }
    el.addEventListener('mouseover', function (e) { var p = e.target.closest('[data-piece]'); if (p && state.hover !== p.dataset.piece) { state.hover = p.dataset.piece; sync(); } });
    el.addEventListener('mouseout', function (e) { if (e.target.closest('[data-piece]') && !(e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest('[data-piece]'))) { state.hover = null; sync(); } });
    el.addEventListener('focusin', function (e) { var p = e.target.closest('[data-piece]'); if (p) { state.hover = p.dataset.piece; sync(); } });
    el.addEventListener('focusout', function () { state.hover = null; sync(); });
    el.addEventListener('click', function (e) { var p = e.target.closest('[data-piece]'); if (p) { state.pin = p.dataset.piece; sync(); } });
    el.addEventListener('keydown', function (e) { var p = e.target.closest('[data-piece]'); if (p && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); state.pin = p.dataset.piece; sync(); } });
    document.addEventListener('st:prefs', render);
    render(); sync();
    return {
      update: function (L) { state.L = L; render(); sync(); },
      hover: function (id) { state.hover = id; sync(); },
      pin: function (id) { state.pin = id; sync(); },
      active: act
    };
  }

  /* ---------------- Length rendering ----------------
     <span data-mm="365.1"></span>               → primary unit
     <span data-mm="365.1" data-len="both">       → 14 ⅜″ (365.1)
     <span data-mm="365.1" data-len="sec">        → secondary unit
     <span data-mm="336,462" data-len="pair">     → 13 ¼″ × 18 3⁄16″
     <span data-tol></span>                       → ±1/16″ or ±1.6 mm */
  function renderLengths(scope) {
    (scope || document).querySelectorAll('[data-mm]').forEach(function (el) {
      var v = el.dataset.mm.split(',').map(Number), k = el.dataset.len || 'pri';
      el.innerHTML = k === 'pair' ? pair(v[0], v[1]) : k === 'both' ? both(v[0]) : k === 'sec' ? sec(v[0]) : pri(v[0]);
    });
    (scope || document).querySelectorAll('[data-tol]').forEach(function (el) { el.innerHTML = tol(); });
    (scope || document).querySelectorAll('[data-unit-name]').forEach(function (el) { el.textContent = P.unit === 'in' ? 'inches' : 'mm'; });
  }

  /* ---------------- Dimension fields ----------------
     <div class="st-field" data-dim="w" data-min="42"> … <input class="st-field__input"> … */
  function bindField(f) {
    var input = f.querySelector('.st-field__input'), timer = null;
    var min = f.dataset.min != null ? +f.dataset.min : G;
    function show(r) {
      f.dataset.state = r.status;
      var st = f.querySelector('.st-field__status'), p = f.querySelector('.st-field__primary'), s = f.querySelector('.st-field__secondary'), t = f.querySelector('.st-field__tol'), msg = f.querySelector('.st-field__msg');
      if (st) st.textContent = { parsed: 'Read as', ambiguous: 'Check unit', error: 'Can\u2019t read', empty: 'Enter a size' }[r.status] || st.textContent;
      if (r.mm) { if (p) p.innerHTML = pri(r.mm); if (s) s.innerHTML = sec(r.mm); if (t) t.innerHTML = tol(); }
      if (msg) msg.textContent = r.msg || (r.status === 'empty' ? 'Inches, mm or cm: 14 3/8, 365 mm, 36.5 cm' : '');
    }
    function commit() {
      var r = parse(input.value, P.unit, min);
      if (r.mm) f.dataset.mmExact = r.mm; else delete f.dataset.mmExact;
      show(r);
      f.dispatchEvent(new CustomEvent('st:dim', { bubbles: true, detail: { key: f.dataset.dim, mm: r.mm || null, status: r.status } }));
    }
    input.addEventListener('input', function () {
      f.dataset.state = 'typing';
      var st = f.querySelector('.st-field__status'); if (st) st.textContent = 'Reading';
      clearTimeout(timer); timer = setTimeout(commit, 280);
    });
    input.addEventListener('blur', function () { clearTimeout(timer); commit(); });
    input.addEventListener('keydown', function (e) { if (e.key === 'Enter') { clearTimeout(timer); commit(); } });
    /* Unit switch: rewrite the typed value in the new unit from the exact mm, so in↔mm round-trips never drift */
    document.addEventListener('st:prefs', function (e) {
      if (e.detail.key !== 'unit' && e.detail.key !== 'den') return;
      var r = parse(input.value, e.detail.key === 'unit' ? (P.unit === 'in' ? 'mm' : 'in') : P.unit, min);
      var exact = f.dataset.mmExact ? +f.dataset.mmExact : r.mm;
      if (r.mm && exact && f.dataset.static !== 'true') input.value = fmtText(exact, P.unit, P.den);
      if (f.dataset.static === 'true') { if (exact) show({ status: f.dataset.state, mm: exact, msg: f.querySelector('.st-field__msg') ? f.querySelector('.st-field__msg').textContent : '' }); return; }
      var r2 = parse(input.value, P.unit, min); if (exact) r2.mm = exact; show(r2);
    });
    if (f.dataset.static === 'true') { var r0 = parse(input.value, P.unit, min); if (r0.mm) { f.dataset.mmExact = r0.mm; ['primary', 'secondary', 'tol'].forEach(function (c) { var el = f.querySelector('.st-field__' + c); if (el) el.innerHTML = c === 'primary' ? pri(r0.mm) : c === 'secondary' ? sec(r0.mm) : tol(); }); } return; }
    commit();
  }

  /* ---------------- Header + controls ---------------- */
  function syncControls() {
    document.querySelectorAll('[data-st-set-unit]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.stSetUnit === P.unit)); });
    document.querySelectorAll('[data-st-set-den]').forEach(function (b) { b.setAttribute('aria-pressed', String(+b.dataset.stSetDen === P.den)); });
    document.querySelectorAll('[data-st-set-theme]').forEach(function (b) {
      var v = b.dataset.stSetTheme; b.setAttribute('aria-pressed', String(v === 'system' ? P.theme === 'system' : (P.theme === v || (b.closest('[data-theme-mode="binary"]') && effectiveTheme() === v))));
    });
    document.querySelectorAll('[data-st-skin]').forEach(function (s) { s.value = P.skin; });
  }
  function bindControls() {
    document.addEventListener('click', function (e) {
      var b = e.target.closest('[data-st-set-unit],[data-st-set-den],[data-st-set-theme]');
      if (!b) return;
      if (b.dataset.stSetUnit) setPref('unit', b.dataset.stSetUnit);
      if (b.dataset.stSetDen) setPref('den', +b.dataset.stSetDen);
      if (b.dataset.stSetTheme) setPref('theme', b.dataset.stSetTheme);
    });
    document.addEventListener('change', function (e) { if (e.target.matches('[data-st-skin]')) setPref('skin', e.target.value); });
    document.addEventListener('st:prefs', function () { syncControls(); renderLengths(); });
    /* Hints: tap/Enter toggles; Escape closes */
    document.addEventListener('click', function (e) {
      var h = e.target.closest('.st-hint__btn');
      document.querySelectorAll('.st-hint.is-open').forEach(function (x) { if (!h || x !== h.parentNode) { x.classList.remove('is-open'); x.querySelector('.st-hint__btn').setAttribute('aria-expanded', 'false'); } });
      if (h) { var open = h.parentNode.classList.toggle('is-open'); h.setAttribute('aria-expanded', String(open)); }
    });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') document.querySelectorAll('.st-hint.is-open').forEach(function (x) { x.classList.remove('is-open'); }); });
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', syncControls);
  }

  /* ---------------- Toasts ---------------- */
  function toast(msg, kind, action) {
    var stack = document.querySelector('.st-toast-stack');
    if (!stack) { stack = document.createElement('div'); stack.className = 'st-toast-stack'; stack.setAttribute('role', 'status'); stack.setAttribute('aria-live', 'polite'); document.body.appendChild(stack); }
    var t = document.createElement('div'); t.className = 'st-toast st-toast--' + (kind || 'info');
    t.innerHTML = '<span>' + msg + '</span>' + (action ? '<button class="st-toast__action" type="button">' + action + '</button>' : '');
    stack.appendChild(t);
    setTimeout(function () { t.style.transition = 'opacity 180ms'; t.style.opacity = '0'; setTimeout(function () { t.remove(); }, 200); }, action ? 8000 : 4200);
    return t;
  }

  /* ---------------- Sample data ---------------- */
  var SAMPLE = {
    kitchen: { id: 'kitchen', name: 'Kitchen — silverware drawer', tag: 'Kitchen · Silverware', W: 365.1, D: 495.3, H: 69.85, input: { w: '14 3/8', d: '19 1/2"', h: '2-3/4 in' }, material: 'PETG',
      bins: [{ x: 0, y: 0, w: 2, d: 5, label: 'UTENSIL' }, { x: 2, y: 0, w: 1, d: 5 }, { x: 3, y: 0, w: 1, d: 5 }, { x: 4, y: 0, w: 1, d: 5 }, { x: 5, y: 0, w: 1, d: 5 }, { x: 6, y: 0, w: 2, d: 5, label: 'UTENSIL' }, { x: 0, y: 5, w: 4, d: 3 }, { x: 4, y: 5, w: 4, d: 3 }] },
    workshop: { id: 'workshop', name: 'Workshop — bench drawer 3', tag: 'Workshop · Bench 3', W: 533.4, D: 412.75, H: 88.9, input: { w: '21"', d: '16 1/4"', h: '3 1/2"' }, material: 'PLA',
      bins: [{ x: 0, y: 0, w: 1, d: 4, label: 'SCREW' }, { x: 1, y: 0, w: 1, d: 4, label: 'SCREW' }, { x: 2, y: 0, w: 2, d: 2, label: 'HEX' }, { x: 2, y: 2, w: 2, d: 2, label: 'HEX' }, { x: 4, y: 0, w: 3, d: 3 }, { x: 7, y: 0, w: 5, d: 2 }] }
  };

  window.ST = {
    IN: IN, G: G, U: U, P: P, setPref: setPref, effectiveTheme: effectiveTheme,
    frac: frac, num: num, pri: pri, sec: sec, both: both, pair: pair, tol: tol, fmtText: fmtText, inHTML: inHTML, mmHTML: mmHTML,
    parse: parse, layout: layout, planSVG: planSVG, bedSVG: bedSVG, mountPlan: mountPlan, skinCfg: skinCfg,
    renderLengths: renderLengths, bindField: bindField, toast: toast, SAMPLE: SAMPLE
  };

  function init() {
    bindControls(); syncControls();
    document.querySelectorAll('.st-field[data-dim]').forEach(bindField);
    renderLengths();
    document.dispatchEvent(new CustomEvent('st:ready'));
  }
  /* DOMContentLoaded fires after every deferred script (e.g. phase2.js) has run */
  if (document.readyState !== 'complete') document.addEventListener('DOMContentLoaded', init); else init();
})();
