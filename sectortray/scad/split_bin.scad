// SectorTray: one piece of a bin that's too big for the printer bed, or the bow-tie keys that join pieces.
// The whole bin is rendered first (kennetek, centred on X/Y, floor of the feet at Z 0); this file cuts it.
// Every position is worked out in sectortray/split.py; this file only draws what it's given.
//
//   part    "piece" or "keys"
//   stl     whole bin STL (absolute path, forward slashes)
//   box     [x0, x1, y0, y1]   the piece's region; open sides are far away
//   clips   [[axis, c, v0, n, dir, z0, z1], ...]  plates this piece carries across a seam
//           axis 0: seam is the plane x = c (plate runs along x); axis 1: plane y = c
//           v0 = inner face of the wall the plate sits on, n = +1/-1 direction into the bin,
//           dir = +1/-1 direction the plate reaches past the seam, z0..z1 = plate height
//   pockets [[axis, c, p], ...]  key pockets on seams of this piece, centred at p along the seam
//   keys    number of keys to lay out (part "keys")

part = "piece";
stl = "";
box = [-1e4, 1e4, -1e4, 1e4];
clips = [];
pockets = [];
keys = 0;

gap = 0.15;          // joint clearance per side (Settings → Fit)
seam = 0.1;          // each cut face is trimmed this much so printed pieces don't push each other apart
floor_z = 7;         // top of the bin's solid floor (kennetek BASE_HEIGHT)

clip_t = 1.6;        // plate thickness
clip_in = 6;         // length fused to this piece's wall
clip_out = 8;        // length reaching past the seam
clip_tip = 0.8;      // chamfer on the wall side of the tip, so it slides in

key_len = 18;        // across the seam
key_end = 10;        // width of each end
key_waist = 5;       // width at the seam
key_t = 1.4;         // key thickness; the pocket is key_t + 0.1 deep so keys sit just below the floor
key_round = 0.6;
// The key's fit, from printed tests on another project with bow-tie keys: with the pocket `gap` bigger all
// round, a key the pocket's exact size was loose, and a full-length key bottomed out at its ends and held the
// seam open. So the key is grown 0.25 mm per side (a press fit on the angled flanks at the default 0.15 gap;
// a bigger Joint clearance loosens it) and stops 0.3 mm short of each pocket end, so only the flanks grip.
key_grow = 0.25;
key_end_clear = 0.3;

module whole() { import(stl, convexity = 10); }

// Plate profile in (u, v): u across the seam (seam at 0, reaching +u), v away from the wall face (v0 at 0).
module clip_profile(fused) {
  if (fused)
    polygon([[-clip_in, -0.4], [-seam, -0.4], [-seam, gap + clip_t], [-clip_in, gap + clip_t]]);
  else
    polygon([[-seam - 0.01, gap], [clip_out - clip_tip, gap], [clip_out, gap + clip_tip], [clip_out, gap + clip_t], [-seam - 0.01, gap + clip_t]]);
}
// Place something built in clip coordinates (U across the seam, V off the wall, Z up from z0) into the bin.
module at_clip(cl) {
  axis = cl[0]; c = cl[1]; v0 = cl[2]; n = cl[3]; dir = cl[4]; z0 = cl[5];
  // u = c + dir*U, v = v0 + n*V
  m = axis == 0
    ? [[dir, 0, 0, c], [0, n, 0, v0], [0, 0, 1, z0], [0, 0, 0, 1]]
    : [[0, n, 0, v0], [dir, 0, 0, c], [0, 0, 1, z0], [0, 0, 0, 1]];
  multmatrix(m) children();
}
// The part past the seam hangs over nothing while its own piece prints, so its underside rises at 45°
// from the seam: every layer then rests on the one below and it prints without support. The face that
// grips the other piece's wall is unchanged; the plate is just shorter towards its tip.
module clip_overhang(h) {
  intersection() {
    linear_extrude(height = h) clip_profile(false);
    rotate([90, 0, 0]) linear_extrude(height = 40, center = true)
      polygon([[-seam - 1, -1], [-seam, -1], [-seam, 0], [clip_out + 1, clip_out + 1 + seam], [clip_out + 1, h + 1], [-seam - 1, h + 1]]);
  }
}
module clip(cl) {
  axis = cl[0]; n = cl[3]; h = cl[6] - cl[5];
  at_clip(cl) linear_extrude(height = h) clip_profile(true);
  // The part past the seam is trimmed wherever the other piece has something in the way (a label tab, the
  // scoop, a divider), keeping `gap` from it: subtract the bin nudged towards the plate.
  difference() {
    at_clip(cl) clip_overhang(h);
    translate(axis == 0 ? [0, n * gap * 0.9, 0] : [n * gap * 0.9, 0, 0]) whole();
  }
}

module key_shape(grow) {
  offset(r = key_round + grow) offset(delta = -key_round)
    polygon([[-key_len / 2, -key_end / 2], [0, -key_waist / 2], [key_len / 2, -key_end / 2],
             [key_len / 2, key_end / 2], [0, key_waist / 2], [-key_len / 2, key_end / 2]]);
}
// One key, printed flat: flanks grown by key_grow, ends trimmed to stop key_end_clear short of the pocket's.
module key() {
  half = key_len / 2 + gap - key_end_clear;
  intersection() {
    linear_extrude(height = key_t) key_shape(key_grow);
    translate([-half, -key_end, -1]) cube([2 * half, 2 * key_end, key_t + 2]);
  }
}
module pocket(pk) {
  axis = pk[0]; c = pk[1]; p = pk[2];
  translate(axis == 0 ? [c, p, floor_z - key_t - 0.1] : [p, c, floor_z - key_t - 0.1])
    rotate(axis == 0 ? 0 : 90)
      linear_extrude(height = key_t + 0.1 + 2) key_shape(gap);
}

if (part == "keys") {
  for (i = [0 : keys - 1])
    translate([i * (key_end + 4), 0, 0]) rotate(90) key();
} else {
  difference() {
    union() {
      intersection() {
        whole();
        translate([box[0], box[2], -1]) cube([box[1] - box[0], box[3] - box[2], 1000]);
      }
      for (cl = clips) clip(cl);
    }
    for (pk = pockets) pocket(pk);
  }
}
