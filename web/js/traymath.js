/* Silverware tray maths, mirroring Gridfinity Extended's gridfinity_silverware.scad.
   Shared by the wizard (browser: window.TrayMath) and the parity test (node: require()).
   sectortray/tray.py is the server-side twin; tests/test_tray.py checks they agree.

   Script facts (vendor/gridfinity_extended_openscad/gridfinity_silverware.scad):
     utensil = [narrow (handle), wide (head; 0 = same), length]; wide is max(narrow, wide)
     slots alternate head/handle, so neighbours pack: pitch = wall + 2m + max(wide_i/2 + narrow_j/2, narrow_i/2 + wide_j/2)
     width  = Σ pitches + 2m + wide_first/2 + wide_last/2
     squares across = ceil((width + 5.7) / 42), squares deep = ceil((longest + 2m + 5.7) / 42)
     the tray body is squares × 42 − 0.5 mm; spare width is shared out as extra margin
   Halves (Gridfinity Extended "extendable"): cut on a grid line; the clips add ~3.4 mm on the cut side. */
(function (root) {
  'use strict';
  var G = 42, WALL = 4, CLR = 5.7, CLIP = 3.4, MARGINS = { Snug: 3, Normal: 5, Easy: 7 };
  var P = [G, G];  // square size [across (x), front-to-back (y)]: the drawer's grid; 42 × 42 is standard
  function setPitch(p) { P = p && p.length === 2 ? [+p[0], +p[1]] : [G, G]; }
  function g(ax) { return ax === 'y' ? P[1] : P[0]; }

  function narrow(u) { return u.handle; }
  function wide(u) { return Math.max(u.handle, u.head || 0); }
  function sqMM(n, ax) { return n * g(ax) - 0.5; }
  function pitch(a, b, m) { return WALL + 2 * m + Math.max(wide(a) / 2 + narrow(b) / 2, narrow(a) / 2 + wide(b) / 2); }
  function widthNeed(us, m) {
    if (!us.length) return 0;
    var s = 0;
    for (var i = 0; i < us.length - 1; i++) s += pitch(us[i], us[i + 1], m);
    return s + 2 * m + wide(us[0]) / 2 + wide(us[us.length - 1]) / 2;
  }
  function depthNeed(us, m) { return us.length ? Math.max.apply(null, us.map(function (u) { return u.len; })) + 2 * m : 0; }
  function squares(mm, ax) { return Math.max(1, Math.ceil((mm + CLR) / g(ax) - 1e-9)); }
  function footprint(us, m) {
    var w = widthNeed(us, m), d = depthNeed(us, m);
    return { wmm: w, dmm: d, gx: squares(w, 'x'), gy: squares(d, 'y') };
  }
  function fitsBed(wmm, dmm, bed) { return (wmm <= bed[0] && dmm <= bed[1]) || (wmm <= bed[1] && dmm <= bed[0]); }
  function bedFits(gx, gy, bed) { return fitsBed(sqMM(gx, 'x'), sqMM(gy, 'y'), bed); }
  /* Most squares along one side that still fit the bed (the other side being 1 square); ax 'y' = depth. */
  function maxSq(bed, ax) { var n = 1; while (ax === 'y' ? bedFits(1, n + 1, bed) : bedFits(n + 1, 1, bed)) n++; return n; }
  /* Two halves cut across the depth: {front, back} squares (front = towards the drawer front), or null. */
  function halves(gx, gy, bed) {
    if (gy < 2 || bedFits(gx, gy, bed)) return null;
    var tries = [];
    for (var f = Math.ceil(gy / 2); f < gy; f++) tries.push(f);
    for (f = Math.ceil(gy / 2) - 1; f >= 1; f--) tries.push(f);
    for (var i = 0; i < tries.length; i++) {
      var a = tries[i], b = gy - a;
      if (fitsBed(sqMM(gx, 'x'), sqMM(a, 'y') + CLIP, bed) && fitsBed(sqMM(gx, 'x'), sqMM(b, 'y') + CLIP, bed)) return { front: a, back: b };
    }
    return null;
  }
  /* Slot positions for drawing, in mm from the tray body's back-left corner. Slot 0 has its head at the back,
     then they alternate. Spare width is shared out as extra margin, as the script does. */
  function slots(us, m, gx, gy) {
    if (!us.length) return [];
    var W = sqMM(gx, 'x'), D = sqMM(gy, 'y'), need = widthNeed(us, m) + CLR, me = m + Math.max(0, gx * g('x') - need) / us.length / 2;
    var span = widthNeed(us, me), x = (W - span) / 2 + me + wide(us[0]) / 2, out = [];
    for (var i = 0; i < us.length; i++) {
      if (i) x += pitch(us[i - 1], us[i], me);
      var len = us[i].len + 2 * m;
      out.push({ i: i, u: us[i], cx: x, head: wide(us[i]) + 2 * me, handle: narrow(us[i]) + 2 * me, len: len,
                 y0: Math.max(0, (D - len) / 2), headBack: i % 2 === 0, m: me });
    }
    return out;
  }

  var api = { G: G, setPitch: setPitch, pitch: g, WALL: WALL, CLR: CLR, CLIP: CLIP, MARGINS: MARGINS, sqMM: sqMM, widthNeed: widthNeed, depthNeed: depthNeed,
              squares: squares, footprint: footprint, fitsBed: fitsBed, bedFits: bedFits, maxSq: maxSq, halves: halves, slots: slots };
  if (typeof module !== 'undefined' && module.exports) module.exports = api; else root.TrayMath = api;
})(typeof window !== 'undefined' ? window : this);
