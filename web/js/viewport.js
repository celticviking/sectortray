/* SectorTray 3D viewport (three.js), following design/handoff_v1/DESIGN.md §7.
   Usage (ES module):
     import { createViewport } from './js/viewport.js';
     const vp = createViewport(document.getElementById('viewport'));
     await vp.loadSTL(url); vp.setColor('#2f4f8f', 'PLA');            // one bin, centered
     await vp.loadAssembly({ parts: [{ url, x, y, z?, rot?, selected?, color, kind }], drawer: { W, D, H, fx, fy, ghost } });
       (x, y = back-left corner of the part in plan mm; z = height its bottom sits at; rot = turned 90°)
     vp.setView('iso'); vp.setStale(true);
   Geometry arrives Z-up from OpenSCAD and is turned Y-up here. Drawer front faces +Z.
   Assembly coordinates are the plan's: millimeters from the drawer's back-left inside corner,
   x to the right, y towards the front. */
import * as THREE from 'three';
import { OrbitControls } from '../vendor/three/addons/OrbitControls.js';
import { STLLoader } from '../vendor/three/addons/STLLoader.js';

const GRID = 42;
// Theme colors can be any CSS color (some skins use oklch(), which THREE.Color can't parse),
// so paint one pixel with it and read back sRGB.
const swatchCtx = document.createElement('canvas').getContext('2d', { willReadFrequently: true });
const css = (name, fallback) => {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  swatchCtx.clearRect(0, 0, 1, 1);
  swatchCtx.fillStyle = fallback;
  swatchCtx.fillStyle = v;
  swatchCtx.fillRect(0, 0, 1, 1);
  const [r, g, b] = swatchCtx.getImageData(0, 0, 1, 1).data;
  return '#' + [r, g, b].map(n => n.toString(16).padStart(2, '0')).join('');
};
const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const roughness = kind => (kind === 'PETG' ? 0.4 : 0.55);

export function createViewport(host) {
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true });
  } catch (e) {
    return unavailable(host);
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const canvas = renderer.domElement;
  canvas.className = 'st-viewport__canvas';
  host.insertBefore(canvas, host.firstChild);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(30, 1, 1, 20000);
  const controls = new OrbitControls(camera, canvas);
  // Middle-drag zooms the camera; stop the browser's middle-click autoscroll from scrolling the page as well.
  canvas.addEventListener('mousedown', e => { if (e.button === 1) e.preventDefault(); });
  canvas.addEventListener('auxclick', e => { if (e.button === 1) e.preventDefault(); });
  // Click (not drag) on a part that has an id → onPick(id, event); empty space → onPick(null, event)
  const picker = new THREE.Raycaster(), ndc = new THREE.Vector2();
  let pickCb = null, down = null;
  canvas.addEventListener('pointerdown', e => { down = e.button === 0 ? { x: e.clientX, y: e.clientY } : null; });
  canvas.addEventListener('pointerup', e => {
    const d = down; down = null;
    if (!d || !pickCb || e.button !== 0 || Math.hypot(e.clientX - d.x, e.clientY - d.y) > 4) return;
    const r = canvas.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    picker.setFromCamera(ndc, camera);
    const pickable = parts.filter(p => p.id != null && p.pickable);
    const hit = picker.intersectObjects(pickable.map(p => p.mesh), false)[0];
    pickCb(hit ? pickable.find(p => p.mesh === hit.object).id : null, e);
  });
  controls.enableDamping = false;
  controls.screenSpacePanning = true;
  controls.addEventListener('change', render);

  // Calm workshop light: warm sky, key from front-upper-left, weak rim from back-right.
  const hemi = new THREE.HemisphereLight(0xfff4e6, 0x6b6f75, 1.6);
  const key = new THREE.DirectionalLight(0xffffff, 1.9);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  key.shadow.radius = 6;
  key.shadow.bias = -0.0005;
  const rim = new THREE.DirectionalLight(0xffffff, 0.45);
  scene.add(hemi, key, key.target, rim);

  const shadowFloor = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.ShadowMaterial({ opacity: 0.25 }));
  shadowFloor.rotation.x = -Math.PI / 2;
  shadowFloor.receiveShadow = true;
  scene.add(shadowFloor);

  const root = new THREE.Group();      // printable parts
  const overlay = new THREE.Group();   // drawer ghost, drawer-top plane, tallest-bin marker
  scene.add(root, overlay);
  const edgeMaterial = new THREE.LineBasicMaterial({ transparent: true, opacity: 0.25 });
  const selEdgeMaterial = new THREE.LineBasicMaterial();  // selected part: accent edges (DESIGN.md §7)
  let parts = [];                      // [{ mesh, edges, material, id, selected }]
  let lit = null;                      // id of the highlighted part (highlight())
  const litColor = new THREE.Color();
  let grid = null, gridOffset = null, stale = false, hovered = null, drawer = null;
  const size = new THREE.Vector3(84, 30, 84), center = new THREE.Vector3(0, 15, 0);
  let view = 'iso', anim = null;

  function applyTheme() {
    scene.background = new THREE.Color(css('--st-color-vp-bg', '#1c1d1f'));
    edgeMaterial.color.set(css('--st-color-text', '#e8e6e1'));
    selEdgeMaterial.color.set(css('--st-color-accent', '#d64933'));
    litColor.set(css('--st-color-accent', '#d64933'));
    paintHighlight();
    buildGrid();
    if (drawer) buildOverlay(drawer);
    render();
  }

  // 42 mm floor grid that fades out a few cells past the model; lines line up with the Gridfinity cells.
  function buildGrid() {
    if (grid) { scene.remove(grid); grid.geometry.dispose(); grid.material.dispose(); }
    const GX = drawer && drawer.pitch ? drawer.pitch[0] : GRID, GZ = drawer && drawer.pitch ? drawer.pitch[1] : GRID;
    const cells = Math.ceil(Math.max(size.x, size.z) / Math.min(GX, GZ)) + 10;
    const half = cells * Math.max(GX, GZ) / 2, pts = [], cols = [];
    const ox = gridOffset ? gridOffset.x : (Math.round(size.x / GRID) % 2) * GRID / 2;
    const oz = gridOffset ? gridOffset.z : (Math.round(size.z / GRID) % 2) * GRID / 2;
    const base = new THREE.Color(css('--st-color-vp-grid', '#3a3c40')), bg = new THREE.Color(css('--st-color-vp-bg', '#1c1d1f'));
    const reach = Math.max(size.x, size.z) / 2 + 4.5 * GRID;
    const fade = (x, z) => { const t = Math.min(1, Math.hypot(x, z) / reach); return bg.clone().lerp(base, 1 - t * t); };
    for (let i = -cells / 2; i <= cells / 2; i++) {
      const pxl = i * GX, pzl = i * GZ;  // one line every square, each direction in its own square size
      for (const [x1, z1, x2, z2] of [[pxl + ox, -half + oz, pxl + ox, half + oz], [-half + ox, pzl + oz, half + ox, pzl + oz]]) {
        for (let s = 0; s < cells; s++) { // split lines so the fade follows distance
          const a = s / cells, b = (s + 1) / cells;
          const ax = x1 + (x2 - x1) * a, az = z1 + (z2 - z1) * a, bx = x1 + (x2 - x1) * b, bz = z1 + (z2 - z1) * b;
          pts.push(ax, 0.05, az, bx, 0.05, bz);
          const ca = fade(ax, az), cb = fade(bx, bz);
          cols.push(ca.r, ca.g, ca.b, cb.r, cb.g, cb.b);
        }
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
    g.setAttribute('color', new THREE.Float32BufferAttribute(cols, 3));
    grid = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ vertexColors: true }));
    scene.add(grid);
  }

  // ---- parts ----
  function clearParts() {
    parts.forEach(p => { root.remove(p.mesh, p.edges); p.mesh.geometry.dispose(); p.edges.geometry.dispose(); p.material.dispose(); });
    parts = [];
    hovered = null;
  }
  function addPart(geometry, color, kind, place) {
    geometry.rotateX(-Math.PI / 2);              // OpenSCAD Z-up → three Y-up (back → -Z)
    if (place && place.rot) geometry.rotateY(-(place.rot === true ? 90 : place.rot) * Math.PI / 180);  // turned clockwise on the grid (seen from above)
    geometry.computeBoundingBox();
    const bb = geometry.boundingBox;
    if (place) geometry.translate(place.x - bb.min.x, (place.elev || 0) - bb.min.y, place.z - bb.min.z);
    else geometry.translate(-(bb.min.x + bb.max.x) / 2, -bb.min.y, -(bb.min.z + bb.max.z) / 2);
    geometry.computeVertexNormals();
    const material = new THREE.MeshStandardMaterial({ color: color || 0xeceae4, roughness: roughness(kind), metalness: 0 });
    const mesh = new THREE.Mesh(geometry, material);
    mesh.castShadow = mesh.receiveShadow = true;
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geometry, 30), place && place.selected ? selEdgeMaterial : edgeMaterial);
    root.add(mesh, edges);
    parts.push({ mesh, edges, material, id: place && place.id, pickable: !!(place && place.pickable), selected: !!(place && place.selected) });
  }
  // Tint one part (by the id given in loadAssembly) in the accent color, e.g. the baseplate piece under the pointer.
  function paintHighlight() {
    parts.forEach(p => {
      const on = lit != null && p.id === lit;
      p.edges.material = on || p.selected ? selEdgeMaterial : edgeMaterial;
      p.material.emissive.copy(on ? litColor : new THREE.Color(0));
      p.material.emissiveIntensity = on ? 0.35 : 0;
    });
  }
  function setSelected(ids) {  // accent edges on these parts, without reloading anything
    parts.forEach(p => { p.selected = p.id != null && ids.indexOf(p.id) >= 0; });
    paintHighlight();
    render();
  }
  function onPick(cb) { pickCb = cb; }
  function highlight(id) {
    if (id === lit) return;
    lit = id == null ? null : id;
    paintHighlight();
    render();
  }
  function finish(before) {
    const box = new THREE.Box3().setFromObject(root);
    if (drawer) box.expandByObject(overlay);
    box.getSize(size);
    box.getCenter(center);
    host.classList.add('has-model');
    const r = Math.max(size.x, size.y, size.z);
    shadowFloor.scale.set(r * 6, r * 6, 1);
    key.position.set(center.x - r * 1.2, r * 1.5, center.z + r * 1.6);  // front-upper-left, ~35°
    key.target.position.copy(center);
    rim.position.set(center.x + r * 1.5, r * 0.8, center.z - r * 1.5);
    const cam = key.shadow.camera;
    cam.left = cam.bottom = -r * 1.2; cam.right = cam.top = r * 1.2; cam.near = 1; cam.far = r * 6;
    cam.updateProjectionMatrix();
    buildGrid();
    setStale(stale);
    if (!before) setView(view, false);
    else if (before.distanceTo(size) > 1) setView(view, true);
    else render();
  }

  // Drawer as a ghost: far walls faintly shaded, edges as lines, the drawer top as an accent plane,
  // and a dashed box showing the tallest bin that fits.
  function buildOverlay(d) {
    overlay.children.slice().forEach(o => { overlay.remove(o); o.geometry && o.geometry.dispose(); o.material && o.material.dispose(); });
    if (!d || !d.ghost) return;
    const text = css('--st-color-text', '#e8e6e1'), accent = css('--st-color-accent', '#d64933');
    const H = d.H || (d.plateH || 5) + 20;
    const box = new THREE.BoxGeometry(d.W, H, d.D);
    // Outline the drawer's walls and floor; when the height is known its top edge is the accent rim below.
    const walls = new THREE.Mesh(box, new THREE.MeshBasicMaterial({ color: text, transparent: true, opacity: 0.05, side: THREE.BackSide, depthWrite: false }));
    walls.position.y = H / 2;
    const outline = new THREE.LineSegments(new THREE.EdgesGeometry(box), new THREE.LineBasicMaterial({ color: text, transparent: true, opacity: 0.35 }));
    outline.position.y = H / 2;
    overlay.add(walls, outline);
    if (d.H) {
      const top = new THREE.Mesh(new THREE.PlaneGeometry(d.W, d.D), new THREE.MeshBasicMaterial({ color: accent, transparent: true, opacity: 0.07, side: THREE.DoubleSide, depthWrite: false }));
      top.rotation.x = -Math.PI / 2; top.position.y = d.H;
      // drawn last and on top, so the drawer box's own top edges don't hide it
      const rim = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.PlaneGeometry(d.W, d.D)), new THREE.LineBasicMaterial({ color: accent, depthTest: false }));
      rim.rotation.x = -Math.PI / 2; rim.position.y = d.H; rim.renderOrder = 2;
      overlay.add(top, rim);
    }
    if (d.binTop && d.binBottom != null) {
      const h = d.binTop - d.binBottom, GX = d.pitch ? d.pitch[0] : GRID, GZ = d.pitch ? d.pitch[1] : GRID;
      const g = new THREE.EdgesGeometry(new THREE.BoxGeometry(GX - 0.5, h, GZ - 0.5));
      const marker = new THREE.LineSegments(g, new THREE.LineDashedMaterial({ color: accent, dashSize: 4, gapSize: 2.5 }));
      marker.computeLineDistances();
      marker.renderOrder = 1;
      marker.position.set(-d.W / 2 + d.fx + GX / 2, d.binBottom + h / 2, -d.D / 2 + d.fy + GZ / 2);
      overlay.add(marker);
    }
  }

  // ---- camera ----
  function viewPose(name) {
    // Zoom to fit: bounding-sphere radius against the narrower of the two fields of view, plus margin.
    const r = size.length() / 2;
    const vfov = THREE.MathUtils.degToRad(camera.fov / 2);
    const hfov = Math.atan(Math.tan(vfov) * camera.aspect);
    const dist = r / Math.sin(Math.min(vfov, hfov)) * 1.2;
    const target = center.clone();
    const el = THREE.MathUtils.degToRad(name === 'top' ? 89.5 : name === 'front' ? 8 : 35);
    const az = THREE.MathUtils.degToRad(name === 'iso' ? 45 : 0);
    const pos = new THREE.Vector3(Math.sin(az) * Math.cos(el), Math.sin(el), Math.cos(az) * Math.cos(el)).multiplyScalar(dist).add(target);
    return { pos, target };
  }
  function setView(name, animate = true) {
    view = name;
    const to = viewPose(name);
    if (!animate || reducedMotion()) { camera.position.copy(to.pos); controls.target.copy(to.target); controls.update(); render(); return; }
    const from = { pos: camera.position.clone(), target: controls.target.clone() }, t0 = performance.now(), dur = 280;
    cancelAnimationFrame(anim);
    const ease = t => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);
    const step = now => {
      const t = Math.min(1, (now - t0) / dur), k = ease(t);
      camera.position.lerpVectors(from.pos, to.pos, k);
      controls.target.lerpVectors(from.target, to.target, k);
      controls.update(); render();
      if (t < 1) anim = requestAnimationFrame(step);
    };
    anim = requestAnimationFrame(step);
  }

  // ---- look ----
  function setColor(hex, kind) {
    parts.forEach(p => { p.material.color.set(hex); p.material.roughness = roughness(kind); });
    defaultLook = { hex, kind };
    render();
  }
  let defaultLook = { hex: '#eceae4', kind: 'PLA' };
  function setStale(on) {
    stale = !!on;
    host.classList.toggle('is-stale', stale);
    parts.forEach(p => {
      p.material.transparent = stale; p.material.opacity = stale ? 0.45 : 1; p.material.depthWrite = !stale;
      p.material.needsUpdate = true;
    });
    edgeMaterial.opacity = stale ? 0.12 : hovered ? 0.6 : 0.25;
    render();
  }

  // Hover: the part under the pointer brightens slightly, edges strengthen.
  const ray = new THREE.Raycaster(), mouse = new THREE.Vector2();
  canvas.addEventListener('pointermove', e => {
    if (!parts.length || e.buttons) return;
    const r = canvas.getBoundingClientRect();
    mouse.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(mouse, camera);
    const hit = ray.intersectObjects(parts.map(p => p.mesh), false)[0];
    const over = hit ? parts.find(p => p.mesh === hit.object) : null;
    if (over !== hovered) {
      hovered = over;
      paintHighlight();  // restores the highlighted part's tint, clears the rest
      if (hovered && !(lit != null && hovered.id === lit)) hovered.material.emissive.setScalar(0.06);
      edgeMaterial.opacity = stale ? 0.12 : hovered ? 0.6 : 0.25;
      render();
    }
  });

  // ---- sizing / drawing ----
  let raf = 0;
  function render() {
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = 0; renderer.render(scene, camera); });
  }
  function resize() {
    const w = host.clientWidth, h = host.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    render();
  }
  new ResizeObserver(resize).observe(host);
  document.addEventListener('st:prefs', applyTheme);

  const loader = new STLLoader();
  async function fetchGeometry(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error('Couldn’t load the rendered model (' + res.status + ').');
    return loader.parse(await res.arrayBuffer());
  }

  // One part, centered. Re-frames when its size changes; same size keeps the user's orbit.
  async function loadSTL(url) {
    const geometry = await fetchGeometry(url);
    const before = parts.length ? size.clone() : null;
    clearParts();
    drawer = null; gridOffset = null; buildOverlay(null);
    addPart(geometry, defaultLook.hex, defaultLook.kind, null);
    finish(before);
  }

  // Several parts placed in drawer coordinates, with the drawer drawn as a ghost.
  async function loadAssembly({ parts: list, drawer: d }) {
    const geometries = await Promise.all(list.map(p => fetchGeometry(p.url)));
    const before = parts.length ? size.clone() : null;
    clearParts();
    drawer = d;
    const GX = d.pitch ? d.pitch[0] : GRID, GZ = d.pitch ? d.pitch[1] : GRID;
    gridOffset = { x: ((d.fx - d.W / 2) % GX + GX) % GX, z: ((d.fy - d.D / 2) % GZ + GZ) % GZ };
    geometries.forEach((g, i) => {
      const p = list[i];
      addPart(g, p.color, p.kind, { x: p.x - d.W / 2, z: p.y - d.D / 2, elev: p.z || 0, rot: p.rot === true ? 90 : (+p.rot || 0), selected: !!p.selected, id: p.id, pickable: !!p.pickable });
    });
    paintHighlight();
    buildOverlay(d);
    finish(before);
  }

  applyTheme();
  resize();
  setView('iso', false);
  return { loadSTL, loadAssembly, setColor, setView, setStale, resize, highlight, setSelected, onPick, get hasModel() { return parts.length > 0; } };
}

function unavailable(host) {
  const note = host.querySelector('.st-viewport__note');
  if (note) note.textContent = 'The 3D preview needs WebGL, which this browser has turned off. Exports still work.';
  const noop = () => {};
  return { loadSTL: async () => {}, loadAssembly: async () => {}, setColor: noop, setView: noop, setStale: noop, resize: noop, highlight: noop, setSelected: noop, onPick: noop, hasModel: false, unavailable: true };
}
