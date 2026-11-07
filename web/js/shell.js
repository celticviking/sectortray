/* SectorTray app shell: talks to the local server and wires the shared header.
   Loads after st.js. Adds ST.api, ST.dialog, ST.esc, ST.projectId; then fires
   `st:shell` with { project } (the current drawer, or null). Page scripts listen for it. */
(function () {
  'use strict';
  var LS = window.localStorage;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; });
  }

  /* ---------------- Server ---------------- */
  var offlineEl = null;
  function offline(on) {
    if (!offlineEl) {
      offlineEl = document.createElement('div');
      offlineEl.className = 'st-offline'; offlineEl.setAttribute('role', 'alert'); offlineEl.hidden = true;
      offlineEl.innerHTML = '<svg class="st-icon" aria-hidden="true"><use href="icons.svg#i-alert"/></svg>' +
        '<span>Can’t reach the SectorTray server. Start SectorTray again, then reload this page.</span>';
      document.body.appendChild(offlineEl);
    }
    offlineEl.hidden = !on;
  }

  /* Drawer revisions: every save of a drawer sends the revision this page last saw, and the server refuses a
     save from a page that missed another tab's or window's save (409) instead of silently undoing it.
     Saves of one drawer go one at a time, so this page's own quick saves never trip over each other. */
  var REV = {}, saving = {};
  function noteRev(j) {
    if (!j) return;
    if (j.id && typeof j.rev === 'number') REV[j.id] = j.rev;
    else if (j.id) REV[j.id] = REV[j.id] == null ? 0 : REV[j.id];
    (j.projects || []).forEach(noteRev);
  }
  var staleShown = false;
  function stale(e) {
    if (staleShown) return;
    staleShown = true;
    dialog({ title: 'Changed in another window', text: esc(e.message), confirm: 'Reload' })
      .then(function (ok) { staleShown = false; if (ok) location.reload(); });
  }
  function api(method, path, body) {
    var m = method === 'PATCH' && /^projects\/([a-z0-9-]+)$/.exec(path);
    if (!m) return send(method, path, body);
    var id = m[1];
    var run = function () { return send(method, path, Object.assign({}, body, REV[id] != null ? { rev: REV[id] } : {})); };
    var p = (saving[id] || Promise.resolve()).then(run, run);
    saving[id] = p.catch(function () {});
    return p.catch(function (e) { if (e.status === 409) stale(e); throw e; });
  }
  function send(method, path, body) {
    var opt = { method: method, headers: {} };
    if (method !== 'GET') { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body || {}); }
    return fetch('/api/' + path, opt).then(function (r) {
      offline(false);
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) { var e = new Error(j.error || ('Server said ' + r.status)); e.status = r.status; e.log = j.log; throw e; }
        if (/^projects(\/[a-z0-9-]+)?$/.test(path)) noteRev(j);
        return j;
      });
    }, function () {
      offline(true);
      var e = new Error('Can’t reach the SectorTray server.'); e.offline = true; throw e;
    });
  }

  /* ---------------- Current drawer ----------------
     ?p=<id> in the URL wins; otherwise the last drawer opened. */
  function projectId() {
    var q = new URLSearchParams(location.search).get('p');
    if (q) { try { LS.setItem('st.project', q); } catch (e) { /* storage off */ } return q; }
    try { return LS.getItem('st.project'); } catch (e) { return null; }
  }
  /* Forget the remembered drawer (only if it's `id`, when given). */
  function forget(id) {
    try { if (!id || LS.getItem('st.project') === id) LS.removeItem('st.project'); } catch (e) { /* storage off */ }
  }

  function wireHeader(project) {
    var pages = ['planner.html', 'bin-builder.html', 'layout-editor.html', 'print-list.html'];
    document.querySelectorAll('.st-tabs .st-tab').forEach(function (a) {
      var file = a.getAttribute('href').split('?')[0];
      if (project && pages.indexOf(file) >= 0) a.setAttribute('href', file + '?p=' + encodeURIComponent(project.id));
    });
    var tag = document.querySelector('.st-header .st-project-tag');
    if (tag) {
      if (project) { tag.textContent = project.tag || project.name; tag.title = project.name; tag.hidden = false; }
      else tag.hidden = true;
    }
  }

  function previewNote() {
    var m = document.body.dataset.preview;
    if (!m) return;
    var n = document.createElement('div');
    n.className = 'st-preview-note';
    n.innerHTML = '<b>Sample data</b><span>This screen is still the design mockup. It gets wired to your drawers in Phase 1, milestone ' + esc(m) + '.</span>';
    var h = document.querySelector('.st-header');
    h.parentNode.insertBefore(n, h.nextSibling);
  }

  /* ---------------- Dialog ----------------
     ST.dialog({ title, text (HTML), fields: [{ name, label, value, sub, max }], confirm, danger, onConfirm(values) → Promise })
     Resolves with onConfirm's result, or null if canceled. onConfirm errors show inline and keep the dialog open. */
  function dialog(o) {
    return new Promise(function (resolve) {
      var d = document.createElement('dialog');
      d.className = 'st-dialog';
      d.innerHTML = '<form class="st-panel" method="dialog">' +
        '<div class="st-panel__head"><h2 class="st-tag">' + esc(o.title) + '</h2><span class="st-panel__slot" aria-hidden="true"></span></div>' +
        '<div class="st-panel__body">' +
          (o.text ? '<p class="st-dialog__text">' + o.text + '</p>' : '') + (o.html || '') +
          (o.fields || []).map(function (f, i) {
            return '<div class="st-field"><label class="st-field__label" for="dlg-' + i + '">' + esc(f.label) +
              (f.sub ? '<span class="st-field__sub">' + esc(f.sub) + '</span>' : '') + '</label>' +
              '<input class="st-field__input" id="dlg-' + i + '" name="' + esc(f.name) + '" value="' + esc(f.value || '') + '" maxlength="' + (f.max || 80) + '" autocomplete="off"></div>';
          }).join('') +
          '<p class="st-dialog__error" role="alert"></p>' +
          '<div class="st-dialog__foot"><button class="st-btn st-btn--ghost" type="button" data-cancel>' + esc(o.cancel || 'Cancel') + '</button>' +
          '<button class="st-btn ' + (o.danger ? 'st-btn--danger' : 'st-btn--primary') + '" type="submit">' + esc(o.confirm || 'OK') + '</button></div>' +
        '</div></form>';
      document.body.appendChild(d);
      var form = d.querySelector('form'), err = d.querySelector('.st-dialog__error'), done = false;
      function close(v) { if (done) return; done = true; d.close(); d.remove(); resolve(v); }
      d.querySelector('[data-cancel]').addEventListener('click', function () { close(null); });
      d.addEventListener('cancel', function (e) { e.preventDefault(); close(null); });
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        var vals = {};
        (o.fields || []).forEach(function (f) { vals[f.name] = form.elements[f.name].value.trim(); });
        var btn = form.querySelector('[type=submit]'); btn.disabled = true; err.textContent = '';
        Promise.resolve(o.onConfirm ? o.onConfirm(vals) : vals).then(close, function (ex) { err.textContent = ex.message; btn.disabled = false; });
      });
      d.showModal();
      var first = d.querySelector('input, select'); if (first) { first.focus(); if (first.select && first.tagName === 'INPUT') first.select(); }
    });
  }

  /* ---------------- New drawer (used from several places) ---------------- */
  function newDrawer() {
    return dialog({
      title: 'New drawer',
      text: 'Give it a name you’ll recognize. The short label goes on the tape in the header.',
      fields: [{ name: 'name', label: 'Name', value: '', sub: 'e.g. Kitchen — silverware drawer' },
               { name: 'tag', label: 'Short label', value: '', sub: 'optional, e.g. Kitchen · Silverware', max: 40 }],
      confirm: 'Create drawer',
      onConfirm: function (v) { return api('POST', 'projects', { name: v.name, tag: v.tag || null }); }
    }).then(function (p) { if (p) location.href = 'planner.html?p=' + encodeURIComponent(p.id); });
  }

  /* ---------------- Printer bed fit ----------------
     A bin's footprint is squares × 42 − 0.5 mm; it may print turned 90° on the bed.
     bed = {width, depth} in mm (project.layout.bed, from Settings → Printer). */
  ST.PITCH = [42, 42];  // the current drawer's square size [x, y] (set when a drawer loads; Planner updates it)
  ST.setPitch = function (p) {
    ST.PITCH = p && p.length === 2 ? [+p[0], +p[1]] : [42, 42];
    if (window.TrayMath) window.TrayMath.setPitch(ST.PITCH);
  };
  ST.pitchText = function (p) {  // "41.3 × 40.9 mm squares" or "" for standard
    p = p || ST.PITCH;
    return Math.abs(p[0] - 42) < 1e-6 && Math.abs(p[1] - 42) < 1e-6 ? '' : ST.num(p[0]) + ' × ' + ST.num(p[1]) + ' mm squares';
  };
  function binFit(w, d, bed, pitch) {
    var p = pitch || ST.PITCH, W = w * p[0] - 0.5, D = d * p[1] - 0.5, bw = bed.width, bd = bed.depth, g = Math.max(p[0], p[1]);
    var big = Math.floor((Math.max(bw, bd) + 0.5) / g), small = Math.floor((Math.min(bw, bd) + 0.5) / g);
    return { ok: (W <= bw && D <= bd) || (W <= bd && D <= bw), W: W, D: D, max: [big, small],
      bed: bw + ' × ' + bd + ' mm', largest: big + ' × ' + small + ' squares' };
  }
  ST.binFit = binFit;

  /* Show a folder in Explorer. Inside the desktop window the window opens it itself, so Windows lets it come
     to the front (a folder opened by the background server lands behind the app). */
  function revealPath(path) {
    var wv = window.chrome && window.chrome.webview;
    if (wv && path) wv.postMessage('reveal:' + path);
  }
  /* ---- the slicer (Settings → Printer): its name on buttons ([data-slicer-name]) and in messages ----
     Remembered in localStorage so buttons show the right name before settings arrive. */
  ST.slicer = { name: (function () { try { return localStorage.getItem('st.slicerName'); } catch (e) { return null; } })() || 'PrusaSlicer', found: true, multibed: true };
  ST.setSlicer = function (info) {
    if (!info) return;
    ST.slicer = info;
    try { localStorage.setItem('st.slicerName', info.name); } catch (e) { /* private window */ }
    document.querySelectorAll('[data-slicer-name]').forEach(function (el) { el.textContent = info.name; });
  };
  function nameSlicer() { document.querySelectorAll('[data-slicer-name]').forEach(function (el) { el.textContent = ST.slicer.name; }); }

  /* filament estimate: an exported part's volume (cm³) → grams. Measured against PrusaSlicer (0.2 mm layers,
     2 perimeters, 15% gyroid): bins and trays use ~0.58 of their solid volume, baseplates ~0.68. */
  var DENSITY = { PLA: 1.24, PETG: 1.27 };
  ST.grams = function (volume, material, kind) {
    if (volume == null) return null;
    return volume * (DENSITY[material] || 1.25) * (kind === 'baseplate' ? 0.68 : 0.58);
  };
  ST.gramsText = function (g) { return g == null ? '' : '\u2248 ' + (g >= 1000 ? (g / 1000).toFixed(2) + ' kg' : Math.round(g) + ' g'); };

  /* toast after POST …/open: the slicer gets one 3MF with named parts laid out on the bed (or several beds) */
  ST.openedToast = function (r, extra) {
    var n = r.opened, beds = r.beds || 1, name = esc(ST.slicer.name);
    ST.toast('Opening ' + n + ' part' + (n === 1 ? '' : 's') + ' in ' + name + (beds > 1 ? ' on ' + beds + ' beds' : '') + ' as <b>' + esc(r.file || '') + '</b>.' +
      (beds > 1 && !ST.slicer.multibed ? ' If parts land off the bed, use ' + name + '\u2019s Arrange.' : '') + (extra || ''));
  };
  ST.reveal = function (pid, what) {
    var desktop = !!(window.chrome && window.chrome.webview);
    return api('POST', 'projects/' + pid + '/reveal', { what: what || 'exports', open: !desktop })
      .then(function (r) { if (desktop) revealPath(r.path); return r; }, function (e) { ST.toast(esc(e.message), 'error'); });
  };
  ST.revealPath = revealPath;

  /* Ctrl+S: everything already saves as you go; this saves anything still waiting, and says so. */
  var saveHooks = [];
  ST.onSaveNow = function (fn) { saveHooks.push(fn); };
  document.addEventListener('keydown', function (e) {
    if (!(e.ctrlKey || e.metaKey) || e.altKey || (e.key !== 's' && e.key !== 'S')) return;
    e.preventDefault();
    Promise.all(saveHooks.map(function (f) { return Promise.resolve().then(f); })).then(function () {
      ST.toast('All changes saved. SectorTray saves as you go, so there\u2019s no need to press Ctrl+S.', 'success');
    }, function (x) { ST.toast('Not saved: ' + esc((x && x.message) || 'unknown problem'), 'error'); });
  });

  /* Text size (Settings → Appearance): browser-style zoom. The desktop window zooms natively; a browser uses CSS zoom. */
  var SCALES = [1, 1.15, 1.3];
  function textScale() { var v = 1; try { v = parseFloat(LS.getItem('st.textScale')) || 1; } catch (e) { /* storage off */ } return SCALES.indexOf(v) >= 0 ? v : 1; }
  function applyScale(v) {
    var wv = window.chrome && window.chrome.webview;
    if (wv) { wv.postMessage('zoom:' + v); document.documentElement.style.zoom = ''; }
    else document.documentElement.style.zoom = v === 1 ? '' : String(v);
  }
  ST.textScale = textScale;
  ST.setTextScale = function (v) { try { LS.setItem('st.textScale', String(v)); } catch (e) { /* storage off */ } applyScale(v); };

  /* Printer presets: build volume from each maker's published specs (mm). Anything else: Custom. */
  ST.PRINTERS = [
    { name: 'Prusa CORE One', bed_w: 250, bed_d: 220, max_h: 270 },
    { name: 'Prusa MK4S', bed_w: 250, bed_d: 210, max_h: 220 },
    { name: 'Prusa MK3S+', bed_w: 250, bed_d: 210, max_h: 210 },
    { name: 'Prusa MINI+', bed_w: 180, bed_d: 180, max_h: 180 },
    { name: 'Prusa XL', bed_w: 360, bed_d: 360, max_h: 360 },
    { name: 'Bambu Lab A1', bed_w: 256, bed_d: 256, max_h: 256 },
    { name: 'Bambu Lab A1 mini', bed_w: 180, bed_d: 180, max_h: 180 },
    { name: 'Bambu Lab P1S / P1P', bed_w: 256, bed_d: 256, max_h: 256 },
    { name: 'Bambu Lab X1 Carbon', bed_w: 256, bed_d: 256, max_h: 256 },
    { name: 'Creality K1 / K1C', bed_w: 220, bed_d: 220, max_h: 250 },
    { name: 'Creality K1 Max', bed_w: 300, bed_d: 300, max_h: 300 },
    { name: 'Creality Ender-3 V3 SE', bed_w: 220, bed_d: 220, max_h: 250 }
  ];
  ST.printerPreset = function (p) {  // the preset these settings match, or null (custom)
    return ST.PRINTERS.filter(function (x) { return x.bed_w === p.bed_w && x.bed_d === p.bed_d && x.max_h === p.max_h && x.name === p.name; })[0] || null;
  };

  /* Pieces for a bin printed in parts — twin of sectortray/split.py (auto_runs / runs_of / is_split).
     Returns { on, x: [squares…], y: [squares…], count, fits } or null for trays. */
  function balanced(n, k) { var b = Math.floor(n / k), e = n % k, out = []; for (var i = 0; i < k; i++) out.push(b + (i < e ? 1 : 0)); return out; }
  function autoRuns(w, d, bed) {
    var best = null;
    for (var kx = 1; kx <= w; kx++) for (var ky = 1; ky <= d; ky++) {
      if (!binFit(Math.ceil(w / kx), Math.ceil(d / ky), bed).ok) continue;
      var key = [kx * ky, Math.abs(kx - ky)];
      if (!best || key[0] < best.key[0] || (key[0] === best.key[0] && key[1] < best.key[1])) best = { key: key, kx: kx, ky: ky };
      break;
    }
    if (!best) return { x: balanced(w, w), y: balanced(d, d) };
    return { x: balanced(w, best.kx), y: balanced(d, best.ky) };
  }
  function sum(a) { return a.reduce(function (s, v) { return s + v; }, 0); }
  function splitPlan(b, bed) {
    if (!b || b.kind === 'tray' || !bed) return null;
    var s = b.split || {}, auto = autoRuns(b.w, b.d, bed);
    var x = s.x && sum(s.x) === b.w ? s.x : auto.x, y = s.y && sum(s.y) === b.d ? s.y : auto.y;
    var fits = x.every(function (a) { return y.every(function (c) { return binFit(a, c, bed).ok; }); });
    return { on: !!s.on && x.length * y.length > 1, x: x, y: y, count: x.length * y.length, fits: fits, custom: !!(s.x || s.y), auto: auto };
  }
  ST.splitPlan = splitPlan;
  /* Settings → New bins: "print too-big bins in pieces automatically". Turns pieces on once for a bin that has
     never had a split setting (split null); a bin whose pieces were turned off by hand stays as it is. */
  ST.autoSplit = function (b, bed, newBins) {
    if (!b || b.kind === 'tray' || b.split || !bed || !(newBins && newBins.auto_split)) return false;
    if (binFit(b.w, b.d, bed).ok) return false;
    b.split = { on: true, joint: 'clips', x: null, y: null };
    return true;
  };

  ST.api = api; ST.esc = esc; ST.dialog = dialog; ST.newDrawer = newDrawer; ST.forgetProject = forget;

  /* ---------------- Desktop window (SectorTray.exe, WebView2) ----------------
     The window has no Windows title bar: the header is the title bar. It is draggable (CSS app-region, or a
     drag request to the host on older runtimes) and gets minimise / maximise / close buttons. */
  function desktopChrome() {
    var wv = window.chrome && window.chrome.webview;
    if (!wv) return;
    var root = document.documentElement, header = document.querySelector('.st-header');
    root.classList.add('st-desktop');
    if (!header) return;
    var ico = function (d) { return '<svg viewBox="0 0 10 10" aria-hidden="true"><path d="' + d + '"/></svg>'; };
    var box = document.createElement('div');
    box.className = 'st-winctl';
    box.innerHTML =
      '<button type="button" class="st-winctl__btn" data-win="minimize" aria-label="Minimise" title="Minimise">' + ico('M0 5.5h10') + '</button>' +
      '<button type="button" class="st-winctl__btn" data-win="toggle-maximize" aria-label="Maximise" title="Maximise" id="st-win-max">' + ico('M.5 .5h9v9h-9z') + '</button>' +
      '<button type="button" class="st-winctl__btn st-winctl__btn--close" data-win="close" aria-label="Close" title="Close">' + ico('M0 0l10 10M10 0L0 10') + '</button>';
    header.appendChild(box);
    box.addEventListener('click', function (e) { var b = e.target.closest('[data-win]'); if (b) wv.postMessage(b.dataset.win); });
    var appRegion = true;
    // Fallback for runtimes without app-region: drag from empty header space, double-click to maximise
    header.addEventListener('mousedown', function (e) {
      if (appRegion || e.button !== 0 || e.target.closest('a,button,select,input,label,.st-seg,.st-project-tag')) return;
      if (e.detail === 2) wv.postMessage('toggle-maximize'); else wv.postMessage('drag');
    });
    wv.addEventListener('message', function (e) {
      var m = e.data; if (!m || m.type !== 'window') return;
      appRegion = !!m.appRegion;
      root.classList.toggle('st-desktop--max', m.state === 'maximized');
      root.classList.toggle('st-desktop--inactive', !m.active);
      var max = document.getElementById('st-win-max');
      max.setAttribute('aria-label', m.state === 'maximized' ? 'Restore' : 'Maximise');
      max.title = m.state === 'maximized' ? 'Restore' : 'Maximise';
      max.innerHTML = m.state === 'maximized' ? ico('M2.5 .5h7v7M.5 2.5h7v7h-7z') : ico('M.5 .5h9v9h-9z');
    });
    wv.postMessage('hello');
  }

  /* ---------------- "?" hints stay inside the window ----------------
     The pop-up is pinned to the window (position: fixed, so panels can't clip it), centered over its "?",
     pushed in from the window's edges, and opened below the "?" when there's no room above. */
  function hintPlacement() {
    var cur = null, GAP = 8, EDGE = 8;
    function place(hint) {
      var pop = hint && hint.querySelector('.st-hint__pop'), btn = hint && hint.querySelector('.st-hint__btn');
      if (!pop || !btn) return;
      cur = hint;
      if (!pop.classList.contains('is-placed')) {  // switch positioning without animating the jump
        pop.style.transition = 'none';
        pop.classList.add('is-placed');
        void pop.offsetWidth;
        pop.style.transition = '';
      }
      // sizes in screen px; with CSS zoom (Text size in a browser) the left/top we set get multiplied by it
      var z = parseFloat(document.documentElement.style.zoom) || 1, pr = pop.getBoundingClientRect();
      var b = btn.getBoundingClientRect(), w = pr.width, h = pr.height;
      var x = Math.min(Math.max(EDGE, b.left + b.width / 2 - w / 2), window.innerWidth - w - EDGE);
      var y = b.top - GAP - h, below = y < EDGE;
      if (below) y = Math.min(b.bottom + GAP, window.innerHeight - h - EDGE);
      pop.style.left = Math.max(EDGE, x) / z + 'px'; pop.style.top = Math.max(EDGE, y) / z + 'px';
      pop.classList.toggle('is-below', below);
    }
    function from(e) { return e.target.closest && e.target.closest('.st-hint'); }
    document.addEventListener('mouseover', function (e) { var h = from(e); if (h && h !== cur) place(h); else if (!h && cur && !cur.classList.contains('is-open') && !cur.contains(document.activeElement)) cur = null; });
    document.addEventListener('focusin', function (e) { var h = from(e); if (h) place(h); });
    document.addEventListener('click', function (e) { var h = from(e); if (h) place(h); });
    function again() { if (cur) place(cur); }
    window.addEventListener('scroll', again, true);
    window.addEventListener('resize', again);
  }

  function start() {
    nameSlicer();
    api('GET', 'settings').then(function (s) { ST.setSlicer(s.slicer); }, function () { /* offline banner covers it */ });
    desktopChrome();
    applyScale(textScale());
    hintPlacement();
    previewNote();
    var id = projectId();
    var load = id ? api('GET', 'projects/' + encodeURIComponent(id)).catch(function (e) { if (e.status === 404) forget(); return null; }) : Promise.resolve(null);
    load.then(function (project) {
      ST.project = project;
      ST.setPitch(project && project.layout && !project.layout.error ? project.layout.pitch : null);
      wireHeader(project);
      document.dispatchEvent(new CustomEvent('st:shell', { detail: { project: project } }));
    });
  }
  // after st.js's init (also on DOMContentLoaded), so fields are bound before page scripts get st:shell
  if (document.readyState !== 'complete') document.addEventListener('DOMContentLoaded', start); else start();
})();
