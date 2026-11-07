/* SectorTray phase 2 (from design/handoff_v2/phase2.js; layout uses s.fils when given): layout editor + silverware tray.
   Loads after app.js and extends window.ST with ST.P2. No dependencies.
   - Sample data (filaments, bin designs, placed copies, utensils)
   - Layout model: problems (overlap / overhang / too tall), gaps, free-space search
   - layoutSVG(): the round-1 plan SVG plus layout layers (gaps, bins, conflicts, selection, ghost, gap selection)
   - Tray model: margins, footprint, bed fit, issue stack, split proposal
   - traySVG(), utensilShape(), utensilDiagram() */
(function () {
  'use strict';
  var ST = window.ST, G = 42, WALL = 1.6;
  var NS = 'http://www.w3.org/2000/svg';

  /* ---------------- Sample data ---------------- */
  var FIL = {
    blue: { name: 'Blue', type: 'PLA', color: '#2f5fa8' },
    white: { name: 'Signal White', type: 'PETG', color: '#eceae4' },
    graphite: { name: 'Graphite', type: 'PETG', color: '#3b3f44' },
    orange: { name: 'Safety Orange', type: 'PLA', color: '#f26b1d' }
  };
  var DESIGNS = [
    { id: 'U', name: 'Utensil bin', w: 2, d: 5, u: 3, qty: 2, fil: 'blue', kind: 'bin' },
    { id: 'S', name: 'Spoon bin', w: 1, d: 5, u: 3, qty: 1, fil: 'graphite', kind: 'bin' },
    { id: 'G', name: 'Gadget tray', w: 4, d: 3, u: 2, qty: 1, fil: 'white', kind: 'bin' },
    { id: 'T', name: 'Silverware tray', w: 5, d: 5, u: 3, qty: 1, fil: 'white', kind: 'tray', slots: 4 }
  ];
  var PLACED = [
    { key: 'U-1', design: 'U', x: 0, y: 0, rot: false },
    { key: 'S-1', design: 'S', x: 2, y: 0, rot: false },
    { key: 'G-1', design: 'G', x: 4, y: 8, rot: false }
  ];
  var MARGINS = { Snug: 3, Normal: 5, Easy: 7 };
  var PRESETS = {
    'Dinner fork': { kind: 'fork', len: 202, handle: 15, head: 28 },
    'Salad fork': { kind: 'fork', len: 178, handle: 14, head: 30 },
    'Dinner knife': { kind: 'knife', len: 230, handle: 16, head: 22 },
    'Butter knife': { kind: 'knife', len: 170, handle: 14, head: 20 },
    'Soup spoon': { kind: 'spoon', len: 191, handle: 15, head: 40 },
    'Dessert spoon': { kind: 'spoon', len: 180, handle: 14, head: 36 },
    'Teaspoon': { kind: 'spoon', len: 155, handle: 12, head: 33 },
    'Serving spoon': { kind: 'spoon', len: 198, handle: 18, head: 55 },
    'Custom': { kind: 'custom', len: 150, handle: 15, head: 0 }
  };
  var UTENSILS = [
    { id: 'u1', name: 'Dinner fork', kind: 'fork', len: 202, handle: 15, head: 28, count: 8 },
    { id: 'u2', name: 'Dinner knife', kind: 'knife', len: 230, handle: 16, head: 22, count: 8 },
    { id: 'u3', name: 'Soup spoon', kind: 'spoon', len: 191, handle: 15, head: 40, count: 8 },
    { id: 'u4', name: 'Dessert spoon', kind: 'spoon', len: 180, handle: 14, head: 36, count: 8 },
    { id: 'u5', name: 'Teaspoon', kind: 'spoon', len: 155, handle: 12, head: 33, count: 8 }
  ];
  var BED = [250, 220];

  /* ---------------- Helpers ---------------- */
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  // square size [x, y] of the drawer on this page (ST.PITCH, set by shell.js); 42 × 42 is standard Gridfinity
  function pit(ax) { var p = (ST && ST.PITCH) || [G, G]; return ax === 'y' ? p[1] : p[0]; }
  function sqMM(n, ax) { return n * pit(ax) - 0.5; }
  function squaresFor(mm) { return Math.max(1, Math.ceil((mm + 0.5) / G)); }
  function r1(n) { return Math.round(n * 10) / 10; }
  function mm(n) { return ST.num(n) + ' mm'; }
  /* SVG length text in the primary unit (inch fractions as tspans) */
  function tLen(v) {
    if (ST.P.unit !== 'in') return ST.num(v);
    var f = ST.frac(v, ST.P.den), s = '';
    if (f.w || !f.n) s += f.w + (f.n ? '\u2009' : '');
    if (f.n) s += '<tspan font-size=".68em" dy="-.42em">' + f.n + '</tspan><tspan font-size=".68em" dy=".42em">\u2044' + f.d + '</tspan>';
    return s + '\u2033';
  }
  function cfg() { return ST.skinCfg(); }
  function endMark(x, y, dx, dy) {
    if (cfg().ends === 'arrow') { var l = 4.4, w = 1.3, bx = x - dx * l, by = y - dy * l; return '<path class="st-plan__arrow" d="M' + x + ' ' + y + 'L' + (bx - dy * w) + ' ' + (by + dx * w) + 'L' + (bx + dy * w) + ' ' + (by - dx * w) + 'Z"/>'; }
    return '<line class="st-plan__tick" x1="' + (x - 1.8) + '" y1="' + (y + 1.8) + '" x2="' + (x + 1.8) + '" y2="' + (y - 1.8) + '"/>';
  }
  function ln(x1, y1, x2, y2, c) { return '<line class="' + c + '" x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 + '"/>'; }
  /* Horizontal dimension: object edge at yObj, dimension line at y */
  function dimH(x1, x2, y, yObj, label, extra) {
    var s = y < yObj ? -1 : 1, o = '';
    o += ln(x1, yObj + s * 2, x1, y + s * 2.5, 'st-plan__ext') + ln(x2, yObj + s * 2, x2, y + s * 2.5, 'st-plan__ext') + ln(x1, y, x2, y, 'st-plan__dim');
    o += endMark(x1, y, -1, 0) + endMark(x2, y, 1, 0);
    o += '<text class="st-plan__dim-text' + (extra || '') + '" x="' + (x1 + x2) / 2 + '" y="' + (s < 0 ? y - 3 : y + 9.5) + '">' + label + '</text>';
    return o;
  }
  function dimV(y1, y2, x, xObj, label, extra) {
    var s = x > xObj ? 1 : -1, o = '', ty = (y1 + y2) / 2, tx = s > 0 ? x - 3 : x - 3;
    o += ln(xObj + s * 2, y1, x + s * 2.5, y1, 'st-plan__ext') + ln(xObj + s * 2, y2, x + s * 2.5, y2, 'st-plan__ext') + ln(x, y1, x, y2, 'st-plan__dim');
    o += endMark(x, y1, 0, -1) + endMark(x, y2, 0, 1);
    o += '<text class="st-plan__dim-text' + (extra || '') + '" x="' + tx + '" y="' + ty + '" transform="rotate(-90 ' + tx + ' ' + ty + ')">' + label + '</text>';
    return o;
  }

  /* =====================================================================
     LAYOUT MODEL
     ===================================================================== */
  function design(designs, id) { for (var i = 0; i < designs.length; i++) if (designs[i].id === id) return designs[i]; return null; }
  /* a placed copy's turn in degrees clockwise from above (0, 90, 180, 270; true = 90 from older drawers);
     the label tab sits on the back wall, so it faces back, right, front or left */
  function quarter(p) { return p.rot === true ? 90 : ((+p.rot || 0) % 360 + 360) % 360; }
  function sideways(p) { return quarter(p) % 180 !== 0; }
  function size(p, designs) { var d = design(designs, p.design); return sideways(p) ? { w: d.d, d: d.w } : { w: d.w, d: d.d }; }
  function rectOf(p, designs) { var s = size(p, designs); return { x: p.x, y: p.y, w: s.w, d: s.d }; }
  function inter(a, b) {
    var x = Math.max(a.x, b.x), y = Math.max(a.y, b.y), x2 = Math.min(a.x + a.w, b.x + b.w), y2 = Math.min(a.y + a.d, b.y + b.d);
    return x2 > x && y2 > y ? { x: x, y: y, w: x2 - x, d: y2 - y } : null;
  }
  function outside(r, nx, ny) {
    var o = [];
    if (r.x < 0) o.push({ x: r.x, y: r.y, w: Math.min(-r.x, r.w), d: r.d });
    if (r.x + r.w > nx) o.push({ x: Math.max(nx, r.x), y: r.y, w: r.x + r.w - Math.max(nx, r.x), d: r.d });
    var ix = Math.max(r.x, 0), iw = Math.min(r.x + r.w, nx) - ix;
    if (iw > 0 && r.y < 0) o.push({ x: ix, y: r.y, w: iw, d: Math.min(-r.y, r.d) });
    if (iw > 0 && r.y + r.d > ny) o.push({ x: ix, y: Math.max(ny, r.y), w: iw, d: r.y + r.d - Math.max(ny, r.y) });
    return o;
  }
  function area(rs) { return rs.reduce(function (s, r) { return s + r.w * r.d; }, 0); }
  /* Everything the UI needs to know about a layout */
  function analyzeLayout(L, placed, designs) {
    var byKey = {}, overlaps = [], overhang = [], tall = [], occ = {};
    placed.forEach(function (p) { byKey[p.key] = { over: [], hang: 0, tall: false }; });
    placed.forEach(function (p, i) {
      var a = rectOf(p, designs), d = design(designs, p.design);
      for (var j = i + 1; j < placed.length; j++) {
        var b = rectOf(placed[j], designs), x = inter(a, b);
        if (x) { overlaps.push({ a: p.key, b: placed[j].key, rect: x }); byKey[p.key].over.push(placed[j].key); byKey[placed[j].key].over.push(p.key); }
      }
      var o = outside(a, L.nx, L.ny);
      if (o.length) { overhang.push({ key: p.key, rects: o, n: area(o) }); byKey[p.key].hang = area(o); }
      if (d.u > L.binU) { tall.push(p.key); byKey[p.key].tall = true; }
      for (var x0 = Math.max(0, a.x); x0 < Math.min(L.nx, a.x + a.w); x0++) for (var y0 = Math.max(0, a.y); y0 < Math.min(L.ny, a.y + a.d); y0++) occ[x0 + ',' + y0] = (occ[x0 + ',' + y0] || 0) + 1;
    });
    var gaps = [];
    for (var y = 0; y < L.ny; y++) for (var x = 0; x < L.nx; x++) if (!occ[x + ',' + y]) gaps.push({ x: x, y: y });
    var unplaced = designs.map(function (d) { var n = placed.filter(function (p) { return p.design === d.id; }).length; return { id: d.id, left: Math.max(0, d.qty - n), placed: n }; });
    return {
      byKey: byKey, overlaps: overlaps, overhang: overhang, tall: tall, occ: occ, gaps: gaps,
      used: L.nx * L.ny - gaps.length, total: L.nx * L.ny, unplaced: unplaced,
      unplacedCount: unplaced.reduce(function (s, u) { return s + u.left; }, 0),
      problemCount: overlaps.length + overhang.length + tall.length
    };
  }
  function cellsFree(L, occ, r, ignoreOcc) {
    for (var x = r.x; x < r.x + r.w; x++) for (var y = r.y; y < r.y + r.d; y++) { if (x < 0 || y < 0 || x >= L.nx || y >= L.ny) return false; if (!ignoreOcc && occ[x + ',' + y]) return false; }
    return true;
  }
  /* First free spot, scanning rows from the back-left. Tries rotated too. */
  function freeSpot(L, occ, w, d) {
    var tries = [[w, d, false], [d, w, true]];
    for (var t = 0; t < tries.length; t++) {
      for (var y = 0; y <= L.ny - tries[t][1]; y++) for (var x = 0; x <= L.nx - tries[t][0]; x++)
        if (cellsFree(L, occ, { x: x, y: y, w: tries[t][0], d: tries[t][1] })) return { x: x, y: y, rot: tries[t][2] ? 90 : 0 };
      if (w === d) break;
    }
    return null;
  }
  /* Largest empty rectangle containing cell (cx, cy): used by double-click on a gap */
  function growGap(L, occ, cx, cy) {
    if (occ[cx + ',' + cy]) return null;
    var best = { x: cx, y: cy, w: 1, d: 1 };
    for (var x0 = cx; x0 >= 0 && !occ[x0 + ',' + cy]; x0--) for (var x1 = cx; x1 < L.nx && !occ[x1 + ',' + cy]; x1++) {
      var y0 = cy, y1 = cy, ok;
      while (y0 - 1 >= 0) { ok = true; for (var a = x0; a <= x1; a++) if (occ[a + ',' + (y0 - 1)]) ok = false; if (!ok) break; y0--; }
      while (y1 + 1 < L.ny) { ok = true; for (var b = x0; b <= x1; b++) if (occ[b + ',' + (y1 + 1)]) ok = false; if (!ok) break; y1++; }
      var r = { x: x0, y: y0, w: x1 - x0 + 1, d: y1 - y0 + 1 };
      if (r.w * r.d > best.w * best.d) best = r;
    }
    return best;
  }
  function binLabel(p, designs) { var d = design(designs, p.design), n = +p.key.split('-')[1]; return d.name + (d.qty > 1 ? ' ' + n : ''); }

  /* ---------------- Layout SVG layers ----------------
     Layers inserted into the round-1 plan SVG, in this order, after the dims:
     st-plan__gaps · st-plan__bins · st-plan__conflicts · st-plan__selection · st-plan__gapsel · st-plan__ghost */
  function layoutLayers(L, s) {
    var A = s.A, designs = s.designs, id = s.id || 'lay', o = [], FILS = s.fils || FIL, FIL0 = FILS[Object.keys(FILS)[0]] || FIL.white;
    var selSet = s.selSet && s.selSet.length ? s.selSet : (s.sel ? [s.sel] : []), isSel = function (k) { return selSet.indexOf(k) >= 0; };
    var GX = (L.pitch || [G, G])[0], GY = (L.pitch || [G, G])[1];
    var px = function (gx) { return L.fx + gx * GX; }, py = function (gy) { return L.fy + gy * GY; };
    o.push('<defs><pattern id="st-err-' + id + '" patternUnits="userSpaceOnUse" width="5" height="5" patternTransform="rotate(45)"><line class="st-plan__err-line" x1="0" y1="0" x2="0" y2="5"/></pattern></defs>');
    /* gaps: one dotted socket per empty square */
    if (!s.mini) o.push('<g class="st-plan__gaps">' + A.gaps.map(function (g) { return '<rect class="st-plan__gap-cell" data-cell="' + g.x + ',' + g.y + '" x="' + (px(g.x) + 5) + '" y="' + (py(g.y) + 5) + '" width="' + (GX - 10) + '" height="' + (GY - 10) + '" rx="5"/>'; }).join('') + '</g>');
    /* bins */
    o.push('<g class="st-plan__bins">' + s.placed.map(function (p) {
      var d = design(designs, p.design), r = rectOf(p, designs), st = A.byKey[p.key] || { over: [], hang: 0, tall: false };
      var x = px(r.x) + 1, y = py(r.y) + 1, w = r.w * GX - 2, h = r.d * GY - 2, f = FILS[d.fil] || FIL0;
      var cls = 'st-bin' + (d.kind === 'tray' ? ' st-bin--tray' : '') + (isSel(p.key) ? ' is-selected' : '') + (p.key === s.lifted || (s.liftedSet && s.liftedSet.indexOf(p.key) >= 0) ? ' is-lifted' : '') + (st.tall ? ' is-too-tall' : '') + (st.over.length || st.hang ? ' is-problem' : '');
      var g = '<g class="' + cls + '" data-key="' + p.key + '"' + (s.mini ? '' : ' tabindex="0" role="button" aria-label="' + esc(binLabel(p, designs)) + ', ' + r.w + ' by ' + r.d + ', ' + d.u + 'u, column ' + (r.x + 1) + ' row ' + (r.y + 1) + (st.over.length ? ', overlaps' : '') + (st.hang ? ', hangs past the grid' : '') + (st.tall ? ', too tall for the drawer' : '') + '"') + '>';
      g += '<rect class="st-bin__body" x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" rx="3" style="fill:' + f.color + '"/>';
      g += '<rect class="st-bin__wall" x="' + (x + 2.4) + '" y="' + (y + 2.4) + '" width="' + (w - 4.8) + '" height="' + (h - 4.8) + '" rx="2"/>';
      if (d.tab) {  // label tab: a band along the wall it's on
        var q = quarter(p), T = 3.2, tb = q === 0 ? [x + 2.4, y + 2.4, w - 4.8, T] : q === 90 ? [x + w - 2.4 - T, y + 2.4, T, h - 4.8] : q === 180 ? [x + 2.4, y + h - 2.4 - T, w - 4.8, T] : [x + 2.4, y + 2.4, T, h - 4.8];
        g += '<rect class="st-bin__tab" x="' + tb[0] + '" y="' + tb[1] + '" width="' + tb[2] + '" height="' + tb[3] + '" rx="1"/>';
      }
      if (d.kind === 'tray') { var n = d.slots || 4, sw = (w - 10) / n; for (var i = 0; i < n; i++) g += '<rect class="st-bin__slot" x="' + (x + 5 + i * sw + 1.2) + '" y="' + (y + 8) + '" width="' + (sw - 2.4) + '" height="' + (h - 16) + '" rx="' + Math.min(6, sw / 3) + '"/>'; }
      if (st.tall) g += '<rect class="st-bin__stripes" x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" rx="3" fill="url(#st-err-' + id + ')"/>';
      if (!s.mini) {
        var name = binLabel(p, designs).toUpperCase(), max = Math.max(3, Math.floor((w - 12) / 4.4));
        if (name.length > max) name = name.slice(0, max - 1) + '…';
        var meta = r.w + '×' + r.d + ' · ' + d.u + 'u', cw = Math.min(w - 6, Math.max(name.length * 4.4, meta.length * 3.7) + 8);
        g += '<rect class="st-bin__chip" x="' + (x + 3) + '" y="' + (y + 3) + '" width="' + cw + '" height="19" rx="1.5"/>';
        g += '<text class="st-bin__name" x="' + (x + 7) + '" y="' + (y + 11.2) + '">' + esc(name) + '</text>';
        g += '<text class="st-bin__meta" x="' + (x + 7) + '" y="' + (y + 18.6) + '">' + meta + '</text>';
        if (st.tall) { var bt = d.u + 'u · max ' + L.binU + 'u', bw = bt.length * 3.9 + 8; g += '<rect class="st-bin__badge" x="' + (x + w - bw - 3) + '" y="' + (y + h - 14) + '" width="' + bw + '" height="11" rx="1.5"/><text class="st-bin__badge-text" x="' + (x + w - bw + 1) + '" y="' + (y + h - 6.2) + '">' + bt + '</text>'; }
      }
      return g + '</g>';
    }).join('') + '</g>');
    if (s.mini) return o.join('');
    /* conflicts: overlap regions + overhang */
    var c = A.overlaps.map(function (ov) { var r = ov.rect; return '<rect class="st-plan__conflict" x="' + px(r.x) + '" y="' + py(r.y) + '" width="' + r.w * GX + '" height="' + r.d * GY + '" fill="url(#st-err-' + id + ')"/>'; });
    A.overhang.forEach(function (oh) { oh.rects.forEach(function (r) { c.push('<rect class="st-plan__overhang" x="' + px(r.x) + '" y="' + py(r.y) + '" width="' + r.w * GX + '" height="' + r.d * GY + '"/>'); }); });
    o.push('<g class="st-plan__conflicts" pointer-events="none">' + c.join('') + '</g>');
    /* selection */
    var sps = s.placed.filter(function (p) { return isSel(p.key); });
    var selRects = sps.map(function (sp) { var sr = rectOf(sp, designs); return '<rect class="st-plan__sel" x="' + (px(sr.x) - 2) + '" y="' + (py(sr.y) - 2) + '" width="' + (sr.w * GX + 4) + '" height="' + (sr.d * GY + 4) + '" rx="4" pointer-events="none"/>'; }).join('');
    /* resize handles: one selected bin (not a tray, not while dragging) gets a grip on each edge */
    var handles = '';
    if (s.resize && sps.length === 1 && design(designs, sps[0].design).kind !== 'tray') {
      var hr = rectOf(sps[0], designs), hx0 = px(hr.x), hy0 = py(hr.y), hx1 = px(hr.x + hr.w), hy1 = py(hr.y + hr.d), T = 10, mx = (hx0 + hx1) / 2, my = (hy0 + hy1) / 2;
      handles = [['n', hx0 + 8, hy0 - T / 2, hx1 - hx0 - 16, T], ['s', hx0 + 8, hy1 - T / 2, hx1 - hx0 - 16, T],
                 ['w', hx0 - T / 2, hy0 + 8, T, hy1 - hy0 - 16], ['e', hx1 - T / 2, hy0 + 8, T, hy1 - hy0 - 16]].map(function (h) {
        return '<rect class="st-plan__edge st-plan__edge--' + h[0] + '" data-edge="' + h[0] + '" x="' + h[1] + '" y="' + h[2] + '" width="' + Math.max(4, h[3]) + '" height="' + Math.max(4, h[4]) + '"/>';
      }).join('') + [['n', mx, hy0], ['s', mx, hy1], ['w', hx0, my], ['e', hx1, my]].map(function (h) {
        return '<rect class="st-plan__grip" pointer-events="none" x="' + (h[1] - 5) + '" y="' + (h[2] - 5) + '" width="10" height="10" rx="2"/>';
      }).join('');
    }
    o.push('<g class="st-plan__selection">' + selRects + handles + '</g>');
    /* gap selection */
    if (s.gap) {
      var gr = s.gap, gx = px(gr.x), gy = py(gr.y), gw = gr.w * GX, gd = gr.d * GY, lab = gr.ok ? gr.w + ' × ' + gr.d + ' gap · ' + ST.num(gr.w * GX - 0.5) + ' × ' + ST.num(gr.d * GY - 0.5) + ' mm' : 'Includes filled squares';
      o.push('<g class="st-plan__gapsel ' + (gr.ok ? 'is-ok' : 'is-bad') + '" pointer-events="none"><rect class="st-plan__gapsel-rect" x="' + gx + '" y="' + gy + '" width="' + gw + '" height="' + gd + '" rx="3"/><rect class="st-plan__gapsel-tag" x="' + gx + '" y="' + (gy - 13) + '" width="' + (lab.length * 3.9 + 8) + '" height="11" rx="1.5"/><text class="st-plan__gapsel-text" x="' + (gx + 4) + '" y="' + (gy - 4.8) + '">' + lab + '</text></g>');
    } else o.push('<g class="st-plan__gapsel"></g>');
    /* drag ghost */
    if (s.ghosts && s.ghosts.length) {
      var gs = s.ghosts, ok = gs.every(function (q) { return q.ok; }), m0 = gs.msg || '', top = gs.reduce(function (a, q) { return q.y + q.d > a.y + a.d ? q : a; }, gs[0]);
      o.push('<g class="st-plan__ghost ' + (ok ? 'is-ok' : 'is-bad') + '" pointer-events="none">' + gs.map(function (q) {
        return '<rect class="st-plan__ghost-rect" x="' + (px(q.x) + 1) + '" y="' + (py(q.y) + 1) + '" width="' + (q.w * GX - 2) + '" height="' + (q.d * GY - 2) + '" rx="3"/>';
      }).join('') + (m0 ? '<rect class="st-plan__ghost-tag" x="' + (px(top.x) + 1) + '" y="' + (py(top.y + top.d) + 2) + '" width="' + (m0.length * 3.9 + 8) + '" height="11" rx="1.5"/><text class="st-plan__ghost-text" x="' + (px(top.x) + 5) + '" y="' + (py(top.y + top.d) + 10.2) + '">' + esc(m0) + '</text>' : '') + '</g>');
    } else if (s.ghost) {
      var gh = s.ghost, hx = px(gh.x), hy = py(gh.y), hw = gh.w * GX, hd = gh.d * GY, msg = gh.msg || '';
      o.push('<g class="st-plan__ghost ' + (gh.ok ? 'is-ok' : 'is-bad') + '" pointer-events="none"><rect class="st-plan__ghost-rect" x="' + (hx + 1) + '" y="' + (hy + 1) + '" width="' + (hw - 2) + '" height="' + (hd - 2) + '" rx="3"/>' +
        (msg ? '<rect class="st-plan__ghost-tag" x="' + (hx + 1) + '" y="' + (hy + hd + 2) + '" width="' + (msg.length * 3.9 + 8) + '" height="11" rx="1.5"/><text class="st-plan__ghost-text" x="' + (hx + 5) + '" y="' + (hy + hd + 10.2) + '">' + esc(msg) + '</text>' : '') + '</g>');
    } else o.push('<g class="st-plan__ghost"></g>');
    return o.join('');
  }
  /* Full layout drawing: round-1 plan minus piece labels and bed overlay, plus layout layers */
  function layoutSVG(L, s) {
    var svg = ST.planSVG(L, { id: s.id || 'lay', mini: !!s.mini });
    if (s.mini) return svg.replace('</svg>', layoutLayers(L, s) + '</svg>');
    svg = svg.replace(/<g class="st-plan__labels"[\s\S]*?(<g class="st-plan__dims")/, '$1')
      .replace(/ tabindex="0" role="button" aria-label="Piece [^"]*"/g, '')
      .replace('<g class="st-plan__bed" pointer-events="none"></g>', '')
      .replace('</svg>', layoutLayers(L, s) + '</svg>')
      .replace('class="st-plan"', 'class="st-plan st-plan--layout"');
    return svg;
  }

  /* =====================================================================
     TRAY MODEL — real sizing from web/js/traymath.js (twin of sectortray/tray.py), which mirrors
     Gridfinity Extended's silverware script: slots alternate head/handle, +5.7 mm wall clearance,
     whole 42 mm squares. Long trays can print as two halves that clip together.
     state: { utensils (u.out = left out), margin: 'Snug'|'Normal'|'Easy', split, halves, bed: [w, d], bedName }
     ===================================================================== */
  var TM = window.TrayMath || null;  // pages without the wizard don't load traymath.js
  if (TM) MARGINS = TM.MARGINS;
  function bedOf(state) { return (state && state.bed) || BED; }
  function bedFits(gx, gy, bed) { return TM.bedFits(gx, gy, bed || BED); }
  function maxSq(bed, ax) { return TM.maxSq(bed || BED, ax); }
  function headW(u) { return Math.max(u.head || u.handle, u.handle); }
  function slotW(u, m) { return headW(u) + 2 * m; }
  function footprint(us, m) { var f = TM.footprint(us, m); return { wmm: r1(f.wmm + TM.CLR), dmm: r1(f.dmm + TM.CLR), gx: f.gx, gy: f.gy }; }
  function depthSq(u, m) { return TM.squares(u.len + 2 * m, 'y'); }
  /* Can a tray this many squares deep print, in one piece or (when allowed) as two halves? */
  function depthOK(gy, bed, allowHalves) { return gy <= maxSq(bed, 'y') || (allowHalves && !!TM.halves(1, gy, bed)); }
  function halvesOf(fp, bed) { return TM.halves(fp.gx, fp.gy, bed); }
  /* Contiguous split into the fewest trays across the width, then the smallest largest tray */
  function splitSlots(us, m, max) {
    var n = us.length, best = null;
    function fits(a, b) { return b - a >= 2 && TM.footprint(us.slice(a, b), m).gx <= max; }
    for (var k = 2; k <= Math.floor(n / 2) && !best; k++) {
      (function rec(start, parts) {
        if (parts.length === k - 1) { if (fits(start, n)) { var gs = parts.concat([[start, n]]); var score = Math.max.apply(null, gs.map(function (g) { return TM.footprint(us.slice(g[0], g[1]), m).gx; })); if (!best || score < best.score) best = { score: score, groups: gs }; } return; }
        for (var e = start + 2; e < n; e++) if (fits(start, e)) rec(e, parts.concat([[start, e]]));
      })(0, []);
    }
    return best ? best.groups.map(function (g) { var s = us.slice(g[0], g[1]); return { from: g[0] + 1, to: g[1], utensils: s, fp: footprint(s, m) }; }) : null;
  }
  /* The issue stack. Order: too long even in halves → needs halves → margin → too wide → too few.
     Width is judged without utensils that can't fit at all, so fixes never fight. */
  function analyzeTray(state) {
    var us = state.utensils, mName = state.margin, m = MARGINS[mName], bed = bedOf(state), max = maxSq(bed), maxMM = sqMM(max), H = !!state.halves;
    var inTray = us.filter(function (u) { return !u.out; });
    var tooLong = [], needHalves = [], needSnug = [], issues = [];
    inTray.forEach(function (u) {
      var snug = depthSq(u, MARGINS.Snug), now = depthSq(u, m);
      if (!depthOK(snug, bed, true)) tooLong.push(u);                       // not even in two halves
      else if (!H && snug > maxSq(bed, 'y')) needHalves.push(u);             // only halves can hold it
      else if (!depthOK(now, bed, H) && depthOK(snug, bed, H)) needSnug.push(u);
    });
    var keep = inTray.filter(function (u) { return tooLong.indexOf(u) < 0; });
    var fpNow = footprint(keep, m), fpSnug = footprint(keep, MARGINS.Snug), wideNow = fpNow.gx > max, wideSnug = fpSnug.gx > max;
    var maxY = maxSq(bed, 'y'), maxLen = r1(sqMM(maxY, 'y') - TM.CLR - 2 * MARGINS.Snug), maxLenHalves = 0;
    for (var g = maxY + 1; g <= 4 * maxY; g++) if (TM.halves(1, g, bed)) maxLenHalves = r1(g * TM.pitch('y') - TM.CLR - 2 * MARGINS.Snug);
    tooLong.forEach(function (u) { issues.push({ kind: 'too-long', utensil: u, need: r1(u.len + 2 * MARGINS.Snug + TM.CLR), needSq: depthSq(u, MARGINS.Snug), max: max, maxMM: maxMM, longest: maxLenHalves || maxLen }); });
    if (needHalves.length) issues.push({ kind: 'halves', utensils: needHalves, need: r1(Math.max.apply(null, needHalves.map(function (u) { return u.len; })) + 2 * MARGINS.Snug + TM.CLR), max: max, maxMM: maxMM, longest: maxLen, fp: fpNow, bed: bed });
    if (needSnug.length || (wideNow && !wideSnug && m > MARGINS.Snug)) {
      if (m > MARGINS.Snug) issues.push({ kind: 'margin', utensils: needSnug, m: m, mName: mName, max: max, maxMM: maxMM, widthToo: wideNow && !wideSnug, wNow: fpNow.wmm, wSnug: fpSnug.wmm, gxSnug: fpSnug.gx, slots: keep.length, halvesToo: !H && needSnug.length > 0 });
    }
    var split = null;
    if (state.split) split = splitSlots(keep, m, max);
    else if (wideSnug || (wideNow && m === MARGINS.Snug)) { var prop = splitSlots(keep, m, max); issues.push({ kind: 'too-wide', slots: keep.length, w: fpNow.wmm, wSnug: fpSnug.wmm, mName: mName, max: max, maxMM: maxMM, proposal: prop }); }
    if (keep.length < 2) issues.push({ kind: 'too-few', n: keep.length });
    var trays = state.split && split ? split : [{ from: 1, to: keep.length, utensils: keep, fp: fpNow }];
    trays.forEach(function (t) { t.halves = H && !bedFits(t.fp.gx, t.fp.gy, bed) ? halvesOf(t.fp, bed) : null; });
    var ready = !issues.length && keep.length >= 2 && trays.every(function (t) { return bedFits(t.fp.gx, t.fp.gy, bed) || t.halves; });
    return { m: m, mName: mName, max: max, maxMM: maxMM, inTray: inTray, keep: keep, out: us.filter(function (u) { return u.out; }), tooLong: tooLong, needHalves: needHalves, needSnug: needSnug, issues: issues, fp: footprint(inTray, m), fpKeep: fpNow, trays: trays, split: !!(state.split && split), halves: H, ready: ready, bed: bed, bedName: state.bedName || 'printer' };
  }
  function bedSpare(fp, bed) {
    bed = bed || BED; var a = sqMM(fp.gx), b = sqMM(fp.gy, 'y');
    if (a <= bed[0] && b <= bed[1]) return [r1(bed[0] - a), r1(bed[1] - b)];
    if (a <= bed[1] && b <= bed[0]) return [r1(bed[1] - a), r1(bed[0] - b)];
    return null;
  }

  /* ---------------- Utensil silhouettes ----------------
     Local coords: x = 0 at the handle end → x = len at the head end; y centered on 0.
     Simple parametric shapes from length / handle / head only. */
  function utensilShape(u) {
    var L = u.len, hw = u.handle, Hw = Math.max(u.head || hw, hw), nw = Math.max(hw * 0.42, 3.2), k = u.kind || 'custom', d, det = '', headX;
    var start = 'M0 0Q0 ' + (-hw / 2) + ' ' + hw * 0.6 + ' ' + (-hw / 2);
    var back = function () { return 'L' + hw * 0.6 + ' ' + hw / 2 + 'Q0 ' + hw / 2 + ' 0 0Z'; };
    if (k === 'spoon') {
      var bl = Math.min(L * 0.36, Hw * 1.45), bx = L - bl, nX = L * 0.56;
      d = start + 'L' + nX + ' ' + (-nw / 2) + 'L' + bx + ' ' + (-nw / 2) + 'C' + (bx + bl * 0.12) + ' ' + (-Hw / 2) + ' ' + L + ' ' + (-Hw * 0.5) + ' ' + L + ' 0C' + L + ' ' + Hw * 0.5 + ' ' + (bx + bl * 0.12) + ' ' + Hw / 2 + ' ' + bx + ' ' + nw / 2 + 'L' + nX + ' ' + nw / 2 + back();
      headX = bx + bl * 0.52;
    } else if (k === 'fork') {
      var hl = Math.min(L * 0.3, Hw * 1.9), hx = L - hl, nX2 = L * 0.6;
      d = start + 'L' + nX2 + ' ' + (-nw / 2) + 'L' + hx + ' ' + (-nw / 2) + 'C' + (hx + hl * 0.28) + ' ' + (-nw / 2) + ' ' + (hx + hl * 0.3) + ' ' + (-Hw / 2) + ' ' + (hx + hl * 0.48) + ' ' + (-Hw / 2) + 'L' + L + ' ' + (-Hw / 2) + 'L' + L + ' ' + Hw / 2 + 'L' + (hx + hl * 0.48) + ' ' + Hw / 2 + 'C' + (hx + hl * 0.3) + ' ' + Hw / 2 + ' ' + (hx + hl * 0.28) + ' ' + nw / 2 + ' ' + hx + ' ' + nw / 2 + 'L' + nX2 + ' ' + nw / 2 + back();
      for (var i = 1; i < 4; i++) { var ty = -Hw / 2 + Hw * i / 4; det += 'M' + (L - hl * 0.46) + ' ' + ty + 'L' + L + ' ' + ty; }
      headX = hx + hl * 0.62;
    } else if (k === 'knife') {
      var bx2 = L * 0.48, bw = Hw;
      d = start + 'L' + bx2 + ' ' + (-nw * 0.7) + 'L' + (bx2 + 4) + ' ' + (-bw * 0.42) + 'L' + (L - bw * 0.9) + ' ' + (-bw * 0.42) + 'Q' + L + ' ' + (-bw * 0.42) + ' ' + L + ' ' + (-bw * 0.05) + 'Q' + (L - bw * 0.3) + ' ' + bw * 0.58 + ' ' + (L - bw * 2.2) + ' ' + bw * 0.58 + 'L' + (bx2 + 4) + ' ' + bw * 0.58 + 'L' + bx2 + ' ' + nw * 0.7 + back();
      headX = L - bw * 2.2; Hw = bw;
    } else {
      d = start + 'L' + (L - Hw / 2) + ' ' + (-Hw / 2) + 'Q' + L + ' ' + (-Hw / 2) + ' ' + L + ' 0Q' + L + ' ' + Hw / 2 + ' ' + (L - Hw / 2) + ' ' + Hw / 2 + back();
      headX = L - Hw * 0.6;
    }
    return { d: d, detail: det, headX: headX, headW: Hw, handleW: hw };
  }
  /* Measuring diagram for one utensil card. data-dim on each dimension group lets the
     focused field light up its dimension (.is-focus). */
  function utensilDiagram(u) {
    var s = utensilShape(u), L = u.len, Wm = Math.max(s.headW, s.handleW), top = -Wm / 2 - 20, h = Wm + 46;
    var o = '<svg class="st-utensil-diagram" viewBox="-12 ' + top + ' ' + (L + 24) + ' ' + h + '" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Where to measure the ' + esc(u.name) + '">';
    o += '<path class="st-utensil" d="' + s.d + '"/>' + (s.detail ? '<path class="st-utensil__detail" d="' + s.detail + '"/>' : '');
    o += '<g class="st-diagram__dim" data-dim="len">' + dimH(0, L, Wm / 2 + 13, Wm / 2, 'LENGTH ' + tLen(L)) + '</g>';
    var hx = s.handleW * 0.6;
    o += '<g class="st-diagram__dim" data-dim="handle">' + ln(hx, -s.handleW / 2, hx, s.handleW / 2, 'st-plan__dim') + endMark(hx, -s.handleW / 2, 0, -1) + endMark(hx, s.handleW / 2, 0, 1) +
      '<polyline class="st-plan__leader" points="' + hx + ',' + (-s.handleW / 2 - 2) + ' ' + (hx + 6) + ',' + (top + 9) + ' ' + (hx + 12) + ',' + (top + 9) + '"/><text class="st-plan__note" x="' + (hx + 14) + '" y="' + (top + 11.5) + '">HANDLE ' + tLen(u.handle) + '</text></g>';
    var headLabel = u.head ? tLen(u.head) : 'SAME AS HANDLE';
    o += '<g class="st-diagram__dim" data-dim="head">' + ln(s.headX, -s.headW / 2, s.headX, s.headW / 2, 'st-plan__dim') + endMark(s.headX, -s.headW / 2, 0, -1) + endMark(s.headX, s.headW / 2, 0, 1) +
      '<polyline class="st-plan__leader" points="' + s.headX + ',' + (-s.headW / 2 - 2) + ' ' + (s.headX - 6) + ',' + (top + 9) + ' ' + (s.headX - 12) + ',' + (top + 9) + '"/><text class="st-plan__note" text-anchor="end" x="' + (s.headX - 14) + '" y="' + (top + 11.5) + '">HEAD ' + headLabel + '</text></g>';
    return o + '</svg>';
  }

  /* ---------------- Tray SVG ----------------
     viewBox in mm, origin at the tray's back-left corner; the drawer front at the bottom.
     Slots alternate: slot 1's head is at the back, slot 2's at the front, and so on (as the script packs them).
     Layers: st-tray__squares · st-tray__body · st-tray__limit · st-tray__slots · st-tray__utensils · st-tray__labels · st-tray__dims · st-tray__bed */
  function trayLayout(us, m, gx, gy) { return TM.slots(us, m, gx, gy); }
  function traySVG(t, o) {
    o = o || {};
    var m = o.m, us = t.utensils, fp = t.fp, gx = fp.gx, gy = fp.gy, W = sqMM(gx), D = sqMM(gy, 'y'), bed = o.bed || BED, max = o.max || maxSq(bed), maxMM = sqMM(max), mini = !!o.mini;
    var slots = trayLayout(us, m, gx, gy), pad = mini ? [4, 4, 4, 4] : [16, 34, 40, 30], hv = t.halves;
    var out = ['<svg class="st-tray' + (mini ? ' st-tray--mini' : '') + '" viewBox="' + (-pad[0]) + ' ' + (-pad[1]) + ' ' + (W + pad[0] + pad[2]) + ' ' + (D + pad[1] + pad[3]) + '" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Tray ' + gx + ' by ' + gy + ' squares with ' + us.length + ' slots' + (hv ? ', printed in two halves' : '') + '">'];
    var GX = pit('x'), GY = pit('y'), sq = []; for (var i = 1; i < gx; i++) sq.push(ln(i * GX, 0, i * GX, D, 'st-tray__square')); for (var j = 1; j < gy; j++) sq.push(ln(0, j * GY, W, j * GY, 'st-tray__square'));
    out.push('<g class="st-tray__body"><rect x="0" y="0" width="' + W + '" height="' + D + '" rx="4"/></g><g class="st-tray__squares">' + sq.join('') + '</g>');
    var lim = [];
    if (gx > max) lim.push('<rect class="st-tray__over" x="' + maxMM + '" y="0" width="' + (W - maxMM) + '" height="' + D + '"/>' + ln(maxMM, -6, maxMM, D + 6, 'st-tray__limit-line') + (mini ? '' : '<text class="st-tray__limit-text" x="' + (maxMM - 2) + '" y="' + (D + 12) + '" text-anchor="end">BED LIMIT · ' + max + ' SQ</text>'));
    if (hv) {
      var cutY = D - hv.front * pit('y') + 0.25;  // front half counts from the front (bottom) edge
      lim.push(ln(-6, cutY, W + 6, cutY, 'st-tray__cut-line') + (mini ? '' : '<text class="st-tray__cut-text" x="2" y="' + (cutY - 2.5) + '">CLIPS TOGETHER HERE · BACK ' + hv.back + ' SQ / FRONT ' + hv.front + ' SQ</text>'));
    } else if (gy > max) {
      lim.push('<rect class="st-tray__over" x="0" y="' + maxMM + '" width="' + W + '" height="' + (D - maxMM) + '"/>' + ln(-6, maxMM, W + 6, maxMM, 'st-tray__limit-line') + (mini ? '' : '<text class="st-tray__limit-text" x="2" y="' + (maxMM - 2.5) + '">BED LIMIT · ' + max + ' SQ</text>'));
    }
    out.push('<g class="st-tray__limit" pointer-events="none">' + lim.join('') + '</g>');
    out.push('<g class="st-tray__slots">' + slots.map(function (s) {
      var bad = o.flag && o.flag(s.u), r = 3, top = s.headBack ? s.head : s.handle, bot = s.headBack ? s.handle : s.head, y0 = s.y0, y1 = s.y0 + s.len, cx = s.cx;
      var p = 'M' + (cx - top / 2 + r) + ' ' + y0 + 'L' + (cx + top / 2 - r) + ' ' + y0 + 'Q' + (cx + top / 2) + ' ' + y0 + ' ' + (cx + top / 2) + ' ' + (y0 + r) + 'L' + (cx + bot / 2) + ' ' + (y1 - r) + 'Q' + (cx + bot / 2) + ' ' + y1 + ' ' + (cx + bot / 2 - r) + ' ' + y1 + 'L' + (cx - bot / 2 + r) + ' ' + y1 + 'Q' + (cx - bot / 2) + ' ' + y1 + ' ' + (cx - bot / 2) + ' ' + (y1 - r) + 'L' + (cx - top / 2) + ' ' + (y0 + r) + 'Q' + (cx - top / 2) + ' ' + y0 + ' ' + (cx - top / 2 + r) + ' ' + y0 + 'Z';
      return '<path class="st-tray__slot' + (bad ? ' is-problem' : '') + (o.drag === s.i ? ' is-dragging' : '') + '" data-slot="' + s.i + '" d="' + p + '"' + (mini ? '' : ' tabindex="0" role="button" aria-label="Slot ' + (s.i + 1) + ', ' + esc(s.u.name) + ', head to the ' + (s.headBack ? 'back' : 'front') + '"') + '/>';
    }).join('') + '</g>');
    out.push('<g class="st-tray__utensils" pointer-events="none">' + slots.map(function (s) {
      var sh = utensilShape(s.u), tr = s.headBack ? 'translate(' + s.cx + ' ' + (s.y0 + s.len - m) + ') rotate(-90)' : 'translate(' + s.cx + ' ' + (s.y0 + m) + ') rotate(90)';
      return '<g transform="' + tr + '"><path class="st-utensil" d="' + sh.d + '"/>' + (sh.detail ? '<path class="st-utensil__detail" d="' + sh.detail + '"/>' : '') + '</g>';
    }).join('') + '</g>');
    if (!mini) {
      out.push('<g class="st-tray__labels" pointer-events="none">' + slots.map(function (s) {
        var n = String((o.offset || 0) + s.i + 1), nm = s.u.name.replace(/^(Dinner|Soup|Dessert|Serving) /, '').toUpperCase(), mx = Math.max(3, Math.floor(s.head / 4.2));
        if (nm.length > mx) nm = nm.slice(0, mx - 1) + '…';
        return '<g class="st-tray__label"><circle class="st-plan__balloon" cx="' + s.cx + '" cy="' + (D + 9) + '" r="5.5"/><text class="st-tray__num" text-anchor="middle" x="' + s.cx + '" y="' + (D + 11.4) + '">' + n + '</text><text class="st-tray__name" text-anchor="middle" x="' + s.cx + '" y="' + (D + 23) + '">' + esc(nm) + '</text></g>';
      }).join('') + '</g>');
      out.push('<g class="st-tray__dims" pointer-events="none">' +
        dimH(0, W, -16, 0, gx + ' SQ · ' + tLen(W), ' st-plan__dim-text--overall') +
        '<text class="st-tray__need" x="' + W / 2 + '" y="-4" text-anchor="middle">SLOTS NEED ' + ST.num(fp.wmm) + ' MM</text>' +
        dimV(0, D, W + 22, W, gy + ' SQ · ' + tLen(D), ' st-plan__dim-text--overall') + '</g>');
      if (o.showBed) {
        var bw = bed[0], bd = bed[1], pieceD = hv ? sqMM(Math.max(hv.front, hv.back), 'y') + TM.CLIP : D;
        if (!(W <= bw && pieceD <= bd) && (W <= bd && pieceD <= bw)) { bw = bed[1]; bd = bed[0]; }
        var ok = hv ? true : bedFits(gx, gy, bed), bx = W / 2 - bw / 2, by = (hv ? D - pieceD : D / 2 - pieceD / 2) + pieceD / 2 - bd / 2;
        out.push('<g class="st-tray__bed" pointer-events="none"><rect class="st-plan__bed-outline' + (ok ? '' : ' is-misfit') + '" x="' + bx + '" y="' + by + '" width="' + bw + '" height="' + bd + '"/><rect class="st-plan__bed-tag" x="' + bx + '" y="' + by + '" width="128" height="11"/><text class="st-plan__bed-text" x="' + (bx + 3.5) + '" y="' + (by + 7.8) + '">BED ' + bed[0] + ' × ' + bed[1] + (hv ? ' · EACH HALF FITS' : ok ? ' · FITS' : ' · TOO BIG') + '</text></g>');
      }
    }
    out.push('</svg>');
    return out.join('');
  }

  /* ---------------- Issue cards (HTML) ---------------- */
  function list(names) { return names.length < 2 ? names.join('') : names.slice(0, -1).join(', ') + ' and ' + names[names.length - 1]; }
  function lower(n) { return n.charAt(0).toLowerCase() + n.slice(1); }
  function short(u) { return esc(lower(u.name.replace(/^Dinner /, ''))); }
  function bedText(is) { return (is.bed || BED)[0] + ' × ' + (is.bed || BED)[1] + ' mm bed'; }
  function issueHTML(is, i) {
    var ic = '<svg class="st-icon" aria-hidden="true"><use href="icons.svg#i-info"/></svg>', h = '', body = '', acts = '', foot = '';
    var wallNote = ' (' + 'length + 2 × margin + ' + ST.num(TM.CLR) + ' mm for the tray walls)';
    if (is.kind === 'too-long') {
      var u = is.utensil;
      h = esc(u.name) + ' is too long for this printer, even in two halves';
      body = 'The ' + short(u) + ' is <b>' + mm(u.len) + '</b>. Even at Snug (3 mm) it needs ' + mm(is.need) + wallNote + ', which is ' + is.needSq + ' squares. Two halves on this bed can hold utensils up to about ' + mm(is.longest) + '.';
      acts = '<button class="st-btn st-btn--sm" type="button" data-fix="leave-out" data-id="' + u.id + '">Leave the ' + short(u) + ' out</button><a class="st-btn st-btn--sm st-btn--ghost" href="settings.html#printer">Printer settings</a>';
      foot = 'It stays on the list as “not in tray”, so you know why.';
    } else if (is.kind === 'halves') {
      var names = is.utensils.map(function (x) { return x.name; }), f0 = is.utensils[0];
      h = esc(list(names)) + (names.length > 1 ? ' are' : ' is') + ' too long for a one-piece tray';
      body = 'The ' + short(f0) + ' is <b>' + mm(f0.len) + '</b>. Even at Snug it needs ' + mm(is.need) + wallNote + '. One piece on the ' + bedText(is) + ' holds utensils up to about ' + mm(is.longest) + '. Print the tray as <b>two halves that clip together</b>: each half fits the bed, and both sit in the baseplate sockets, so the grid keeps them lined up.';
      acts = '<button class="st-btn st-btn--sm" type="button" data-fix="halves">Print in two halves</button>' + is.utensils.map(function (x) { return '<button class="st-btn st-btn--sm st-btn--ghost" type="button" data-fix="leave-out" data-id="' + x.id + '">Leave the ' + short(x) + ' out</button>'; }).join('');
      foot = 'The cut runs across the tray on a grid line. The utensils lie across the join.';
    } else if (is.kind === 'margin') {
      var nm = is.utensils.map(function (x) { return x.name; });
      if (nm.length) {
        var f = is.utensils[0];
        h = esc(list(nm)) + (nm.length > 1 ? ' need' : ' needs') + ' the Snug margin';
        body = 'At ' + is.mName + ' (' + is.m + ' mm) the ' + short(f) + ' needs <b>' + mm(r1(f.len + 2 * is.m + TM.CLR)) + '</b> of depth' + wallNote + '. That rounds up to ' + depthSq(f, is.m) + ' squares, too long for the bed. At Snug (3 mm) it needs ' + mm(r1(f.len + 6 + TM.CLR)) + ', so the tray fits in ' + depthSq(f, 3) + ' squares.';
        if (is.widthToo) body += ' Snug also narrows the tray from ' + mm(r1(is.wNow)) + ' to ' + mm(r1(is.wSnug)) + ' across.';
      } else {
        h = 'The slots need the Snug margin to fit across';
        body = 'At ' + is.mName + ' (' + is.m + ' mm) the ' + is.slots + ' slots need <b>' + mm(r1(is.wNow)) + '</b> across, more than ' + is.max + ' squares (' + mm(is.maxMM) + '). At Snug (3 mm) they need ' + mm(r1(is.wSnug)) + ', which fits in ' + is.gxSnug + ' squares.';
      }
      acts = '<button class="st-btn st-btn--sm" type="button" data-fix="snug">Use Snug margin</button>' + (is.halvesToo ? '<button class="st-btn st-btn--sm st-btn--ghost" type="button" data-fix="halves">Keep ' + is.mName + ', print in two halves</button>' : '');
      foot = 'Snug leaves 3 mm around every utensil. They still drop in, with less wiggle.';
    } else if (is.kind === 'too-wide') {
      h = is.slots + ' slots are too wide for one tray';
      body = 'Side by side they need <b>' + mm(r1(is.w)) + '</b> across at ' + is.mName + (is.mName !== 'Snug' ? ' (' + mm(r1(is.wSnug)) + ' at Snug)' : '') + ', more than ' + is.max + ' squares (' + mm(is.maxMM) + '). Split them into trays that each fit the bed:';
      if (is.proposal) body += '<span class="st-split">' + is.proposal.map(function (t, k) {
        return '<span class="st-split__tray"><span class="st-split__art">' + traySVG(t, { m: MARGINS[is.mName], mini: true, max: is.max }) + '</span><span class="st-split__label"><b>Tray ' + (k + 1) + '</b> · slots ' + t.from + (t.to > t.from ? '–' + t.to : '') + '</span><span class="st-split__size">' + t.fp.gx + ' × ' + t.fp.gy + ' squares · ' + ST.num(sqMM(t.fp.gx)) + ' × ' + ST.num(sqMM(t.fp.gy)) + ' mm</span></span>';
      }).join('') + '</span>';
      acts = is.proposal ? '<button class="st-btn st-btn--sm" type="button" data-fix="split">Split into ' + is.proposal.length + ' trays</button>' : '';
      foot = is.proposal ? 'Both trays go into the drawer’s bin list, ready to place in Layout.' : 'No split keeps at least two slots in each tray. Leave a utensil out, or use Snug.';
    } else if (is.kind === 'too-few') {
      h = 'A tray needs at least two utensil types';
      body = is.n ? 'Only one utensil type is left in the tray. Put one back, add another type, or make a plain bin for it in Bins.' : 'Every utensil is left out. Put at least two back, or add utensil types in Measure.';
    }
    return '<article class="st-issue st-issue--' + is.kind + '" aria-labelledby="iss-' + i + '"><div class="st-issue__head"><span class="st-issue__num">' + (i + 1) + '</span>' + ic + '<h3 class="st-issue__title" id="iss-' + i + '">' + h + '</h3></div><p class="st-issue__body">' + body + '</p>' + (acts ? '<div class="st-issue__actions">' + acts + '</div>' : '') + (foot ? '<p class="st-issue__foot">' + foot + '</p>' : '') + '</article>';
  }

  ST.P2 = {
    G: G, WALL: WALL, FIL: FIL, DESIGNS: DESIGNS, PLACED: PLACED, MARGINS: MARGINS, PRESETS: PRESETS, UTENSILS: UTENSILS, BED: BED,
    esc: esc, sqMM: sqMM, squaresFor: squaresFor, tLen: tLen, dimH: dimH, dimV: dimV,
    design: design, size: size, quarter: quarter, sideways: sideways, rectOf: rectOf, analyzeLayout: analyzeLayout, cellsFree: cellsFree, freeSpot: freeSpot, growGap: growGap, binLabel: binLabel,
    layoutLayers: layoutLayers, layoutSVG: layoutSVG,
    bedFits: bedFits, maxSq: maxSq, slotW: slotW, footprint: footprint, depthSq: depthSq, depthOK: depthOK, halvesOf: halvesOf, splitSlots: splitSlots, analyzeTray: analyzeTray, bedSpare: bedSpare,
    utensilShape: utensilShape, utensilDiagram: utensilDiagram, trayLayout: trayLayout, traySVG: traySVG, issueHTML: issueHTML
  };
})();
