"""
RAILWAY STATION DIGITAL TWIN  -  Blender build script
=====================================================
Builds a complete, walkable 3D terminus station:
  * glass train-shed vault, concourse, ticket gates, kiosks, info desk, clock
  * island platform + two outer platforms, tactile strips, benches, info pillars
  * 2 tracks (rails, sleepers, ballast), buffer stops, catenary, signals
  * 2 electric trains (one parked, one arriving - animated)
  * ~120 passengers (standing, seated, walking - animated), luggage
  * pendant lamps with real lights, sun, sky, sign boards, departure board
  * "Station" sidebar panel (N key): walk mode, teleports, projection, Z-buffer view

HOW TO USE
  1. Open Blender 3.6 / 4.x  ->  Scripting workspace  ->  Open this file  ->  Run Script
  2. Press N in the 3D viewport -> "Station" tab -> "Start Walk Mode"
     (or press Shift+` in the viewport).  W A S D + mouse, Shift = run,
     Tab = toggle gravity / fly (Q,E = down/up), click = confirm, Esc = cancel.
"""
import bpy
import bmesh
import math
import random
from math import radians, sin, cos, pi, sqrt
from mathutils import Vector, Matrix, Euler

# ---------------------------------------------------------------- dimensions
X_MIN, X_MAX_VAULT = -50.0, 46.0       # vault / concourse extent
HALF_W = 15.0                          # half width of the shed
SPRING_Z, CROWN_Z = 6.0, 10.0          # vault heights
PLAT_H = 0.9                           # platform height above rail bed
TRACK_Y = (6.0, -6.0)
GAUGE = 1.435
TRACK_X0, TRACK_X1 = -31.0, 140.0
EYE = 1.7
FRAME_END = 624

M = {}          # material library
COLS = {}       # collections
I4 = Matrix.Identity(4)


# ================================================================= utilities
def reset():
    try:
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        pass
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.curves, bpy.data.materials, bpy.data.lights,
                 bpy.data.cameras, bpy.data.collections, bpy.data.actions):
        for b in list(coll):
            coll.remove(b)
    COLS.clear()
    M.clear()


def col(name):
    if name not in COLS:
        c = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(c)
        COLS[name] = c
    return COLS[name]


def _set(node, names, value):
    for n in names:
        s = node.inputs.get(n)
        if s is not None:
            try:
                s.default_value = value
                return True
            except Exception:
                pass
    return False


def mat(name, color, rough=0.5, metal=0.0, emit=None, strength=0.0, alpha=1.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes.get("Principled BSDF")
    _set(b, ["Base Color"], (*color, 1.0))
    _set(b, ["Roughness"], rough)
    _set(b, ["Metallic"], metal)
    if emit is not None:
        _set(b, ["Emission Color", "Emission"], (*emit, 1.0))
        _set(b, ["Emission Strength"], strength)
    if alpha < 1.0:
        _set(b, ["Alpha"], alpha)
        for attr, val in (("blend_method", 'BLEND'), ("shadow_method", 'NONE'),
                          ("surface_render_method", 'BLENDED'), ("use_backface_culling", False)):
            try:
                setattr(m, attr, val)
            except Exception:
                pass
    m.diffuse_color = (*color, alpha)
    return m


def tex_mat(name, kind, c1, c2, scale, rough=0.6, metal=0.0, bump=0.0):
    m = mat(name, c1, rough, metal)
    n, l = m.node_tree.nodes, m.node_tree.links
    b = n["Principled BSDF"]
    tc = n.new("ShaderNodeTexCoord")
    if kind == 'checker':
        t = n.new("ShaderNodeTexChecker")
        t.inputs['Color1'].default_value = (*c1, 1)
        t.inputs['Color2'].default_value = (*c2, 1)
        t.inputs['Scale'].default_value = scale
        l.new(tc.outputs['Object'], t.inputs['Vector'])
        l.new(t.outputs['Color'], b.inputs['Base Color'])
    elif kind == 'brick':
        t = n.new("ShaderNodeTexBrick")
        t.inputs['Color1'].default_value = (*c1, 1)
        t.inputs['Color2'].default_value = (*c2, 1)
        t.inputs['Mortar'].default_value = (0.5, 0.48, 0.45, 1)
        t.inputs['Scale'].default_value = scale
        l.new(tc.outputs['Object'], t.inputs['Vector'])
        l.new(t.outputs['Color'], b.inputs['Base Color'])
        if bump:
            bn = n.new("ShaderNodeBump")
            bn.inputs['Strength'].default_value = bump
            l.new(t.outputs['Fac'], bn.inputs['Height'])
            l.new(bn.outputs['Normal'], b.inputs['Normal'])
    elif kind == 'noise':
        t = n.new("ShaderNodeTexNoise")
        t.inputs['Scale'].default_value = scale
        t.inputs['Detail'].default_value = 8.0
        r = n.new("ShaderNodeValToRGB")
        r.color_ramp.elements[0].color = (*c1, 1)
        r.color_ramp.elements[1].color = (*c2, 1)
        l.new(tc.outputs['Object'], t.inputs['Vector'])
        l.new(t.outputs['Fac'], r.inputs['Fac'])
        l.new(r.outputs['Color'], b.inputs['Base Color'])
        if bump:
            bn = n.new("ShaderNodeBump")
            bn.inputs['Strength'].default_value = bump
            l.new(t.outputs['Fac'], bn.inputs['Height'])
            l.new(bn.outputs['Normal'], b.inputs['Normal'])
    return m


class MB:
    """Tiny bmesh builder: many primitives -> one mesh with material slots."""
    AX = {'Z': I4,
          'X': Matrix.Rotation(radians(90), 4, 'Y'),
          'Y': Matrix.Rotation(radians(90), 4, 'X')}

    def __init__(self):
        self.bm = bmesh.new()

    def _tag(self, n0, mi, smooth=False):
        for f in list(self.bm.faces)[n0:]:
            f.material_index = mi
            if smooth:
                f.smooth = True

    def box(self, c, s, mi=0, pre=None, rz=0.0):
        m = (I4 if pre is None else pre) @ Matrix.Translation(c) @ Matrix.Rotation(rz, 4, 'Z') \
            @ Matrix.Diagonal((s[0], s[1], s[2], 1.0))
        n0 = len(self.bm.faces)
        bmesh.ops.create_cube(self.bm, size=1.0, matrix=m)
        self._tag(n0, mi)

    def cyl(self, c, r, d, mi=0, axis='Z', seg=20, r2=None, pre=None):
        m = (I4 if pre is None else pre) @ Matrix.Translation(c) @ MB.AX[axis]
        n0 = len(self.bm.faces)
        bmesh.ops.create_cone(self.bm, cap_ends=True, cap_tris=False, segments=seg,
                              radius1=r, radius2=(r if r2 is None else r2), depth=d, matrix=m)
        self._tag(n0, mi)

    def sphere(self, c, r, mi=0, scale=(1, 1, 1), pre=None):
        m = (I4 if pre is None else pre) @ Matrix.Translation(c) \
            @ Matrix.Diagonal((scale[0], scale[1], scale[2], 1.0))
        n0 = len(self.bm.faces)
        bmesh.ops.create_uvsphere(self.bm, u_segments=12, v_segments=8, radius=r, matrix=m)
        self._tag(n0, mi, smooth=True)

    def poly(self, pts, mi=0):
        n0 = len(self.bm.faces)
        vs = [self.bm.verts.new(p) for p in pts]
        self.bm.faces.new(vs)
        self._tag(n0, mi)


def finish(mb, name, mats, coll, loc=(0, 0, 0), rot=(0, 0, 0), parent=None):
    bm = mb.bm
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for m in mats:
        me.materials.append(m)
    me.update()
    return add_obj(name, me, coll, loc, rot, parent)


def add_obj(name, data, coll, loc=(0, 0, 0), rot=(0, 0, 0), parent=None):
    o = bpy.data.objects.new(name, data)
    coll.objects.link(o)
    o.location = loc
    o.rotation_euler = rot
    if parent is not None:
        o.parent = parent
    return o


FACE = {'-Y': (90, 0, 0), '+Y': (90, 0, 180), '+X': (90, 0, 90), '-X': (90, 0, -90)}


def add_text(txt, loc, face, size, material, coll, align='CENTER', extrude=0.015):
    cu = bpy.data.curves.new("txt", 'FONT')
    cu.body = txt
    cu.size = size
    cu.align_x = align
    cu.align_y = 'CENTER'
    cu.extrude = extrude
    cu.materials.append(material)
    o = bpy.data.objects.new("Text_" + txt[:14], cu)
    coll.objects.link(o)
    o.location = loc
    o.rotation_euler = [radians(a) for a in FACE[face]]
    return o


def curve_obj(name, splines, bevel, material, coll):
    cu = bpy.data.curves.new(name, 'CURVE')
    cu.dimensions = '3D'
    cu.bevel_depth = bevel
    cu.bevel_resolution = 1
    cu.resolution_u = 2
    for pts in splines:
        sp = cu.splines.new('POLY')
        sp.points.add(len(pts) - 1)
        for p, (x, y, z) in zip(sp.points, pts):
            p.co = (x, y, z, 1.0)
    cu.materials.append(material)
    o = bpy.data.objects.new(name, cu)
    coll.objects.link(o)
    return o


def point_light(name, loc, energy, color, coll, radius=0.25, shadow=False):
    ld = bpy.data.lights.new(name, 'POINT')
    ld.energy = energy
    ld.color = color
    ld.shadow_soft_size = radius
    try:
        ld.use_shadow = shadow
    except Exception:
        pass
    return add_obj(name, ld, coll, loc)


def arch_z(y):
    t = max(-1.0, min(1.0, y / HALF_W))
    return SPRING_Z + (CROWN_Z - SPRING_Z) * sqrt(1 - t * t)


def arch_pts(n=24):
    return [(-HALF_W * cos(pi * i / n), SPRING_Z + (CROWN_Z - SPRING_Z) * sin(pi * i / n))
            for i in range(n + 1)]


# ================================================================ materials
def make_materials():
    M['concrete'] = tex_mat("Concrete", 'noise', (0.42, 0.42, 0.43), (0.62, 0.62, 0.63), 25, 0.85, bump=0.15)
    M['platform'] = tex_mat("PlatformTiles", 'checker', (0.60, 0.60, 0.62), (0.52, 0.52, 0.55), 1.7, 0.55)
    M['marble'] = tex_mat("ConcourseMarble", 'checker', (0.82, 0.78, 0.70), (0.45, 0.40, 0.35), 0.8, 0.12)
    M['plaza'] = tex_mat("PlazaPaving", 'checker', (0.50, 0.48, 0.45), (0.42, 0.40, 0.38), 1.2, 0.8)
    M['coping'] = mat("Coping", (0.80, 0.80, 0.77), 0.6)
    M['tactile'] = mat("Tactile", (0.95, 0.72, 0.05), 0.6)
    M['ballast'] = tex_mat("Ballast", 'noise', (0.10, 0.10, 0.11), (0.32, 0.31, 0.30), 70, 0.95, bump=0.8)
    M['grass'] = tex_mat("Ground", 'noise', (0.05, 0.12, 0.03), (0.13, 0.20, 0.07), 5, 0.95, bump=0.3)
    M['sleeper'] = mat("Sleeper", (0.45, 0.45, 0.45), 0.9)
    M['rail'] = mat("Rail", (0.38, 0.31, 0.27), 0.35, 0.9)
    M['steel_dark'] = mat("SteelDark", (0.05, 0.06, 0.08), 0.4, 0.8)
    M['steel'] = mat("Steel", (0.52, 0.54, 0.58), 0.3, 1.0)
    M['brick'] = tex_mat("Brick", 'brick', (0.45, 0.18, 0.12), (0.33, 0.12, 0.09), 3.0, 0.85, bump=0.4)
    M['glass_wall'] = mat("GlassWall", (0.65, 0.82, 0.92), 0.05, 0.0, alpha=0.18)
    M['glass_roof'] = mat("GlassRoof", (0.55, 0.78, 0.95), 0.05, 0.0, alpha=0.22)
    M['wood'] = mat("Wood", (0.36, 0.20, 0.10), 0.6)
    M['white'] = mat("White", (0.9, 0.9, 0.9), 0.5)
    M['red'] = mat("RedPaint", (0.65, 0.04, 0.03), 0.4)
    M['black'] = mat("Black", (0.02, 0.02, 0.025), 0.6)
    M['lamp'] = mat("LampEmit", (1, 0.9, 0.7), 0.5, emit=(1.0, 0.82, 0.55), strength=14)
    M['green_e'] = mat("GreenEmit", (0.1, 1, 0.2), 0.5, emit=(0.1, 1.0, 0.2), strength=6)
    M['red_e'] = mat("RedEmit", (1, 0.05, 0.05), 0.5, emit=(1.0, 0.05, 0.05), strength=6)
    M['amber_e'] = mat("AmberEmit", (1, 0.55, 0.05), 0.5, emit=(1.0, 0.55, 0.05), strength=5)
    M['screen'] = mat("ScreenEmit", (0.2, 0.5, 1), 0.3, emit=(0.15, 0.45, 1.0), strength=3)
    M['sign_bg'] = mat("SignBoard", (0.02, 0.07, 0.20), 0.4)
    M['sign_txt'] = mat("SignText", (1, 1, 1), 0.4, emit=(1, 1, 1), strength=1.5)
    M['board_txt'] = mat("BoardText", (1, 0.6, 0.1), 0.4, emit=(1.0, 0.6, 0.1), strength=4)
    M['cafe_e'] = mat("CafeSign", (1, 0.3, 0.2), 0.4, emit=(1.0, 0.3, 0.15), strength=4)
    M['news_e'] = mat("NewsSign", (0.2, 0.8, 1), 0.4, emit=(0.2, 0.8, 1.0), strength=4)
    M['leaf'] = mat("Leaf", (0.08, 0.30, 0.07), 0.8)
    M['city'] = mat("City", (0.15, 0.17, 0.23), 0.5)
    M['yellow'] = mat("YellowPaint", (0.9, 0.7, 0.05), 0.5)
    M['clockface'] = mat("ClockFace", (0.95, 0.95, 0.9), 0.4, emit=(1, 1, 0.9), strength=0.8)
    M['skin'] = [mat("Skin%d" % i, c, 0.6) for i, c in enumerate(
        [(0.90, 0.72, 0.58), (0.75, 0.55, 0.40), (0.55, 0.37, 0.25), (0.33, 0.21, 0.14)])]
    M['shirt'] = [mat("Shirt%d" % i, c, 0.8) for i, c in enumerate(
        [(0.8, 0.1, 0.1), (0.1, 0.3, 0.75), (0.1, 0.5, 0.25), (0.9, 0.75, 0.1), (0.9, 0.9, 0.9),
         (0.15, 0.15, 0.18), (0.55, 0.15, 0.55), (0.95, 0.45, 0.1)])]
    M['pants'] = [mat("Pants%d" % i, c, 0.85) for i, c in enumerate(
        [(0.08, 0.1, 0.2), (0.05, 0.05, 0.06), (0.35, 0.28, 0.18), (0.25, 0.3, 0.4), (0.5, 0.5, 0.52)])]
    M['hair'] = [mat("Hair%d" % i, c, 0.7) for i, c in enumerate(
        [(0.04, 0.03, 0.02), (0.25, 0.14, 0.06), (0.75, 0.6, 0.3), (0.6, 0.6, 0.6), (0.4, 0.08, 0.05)])]
    M['bag'] = [mat("Bag%d" % i, c, 0.6) for i, c in enumerate(
        [(0.5, 0.05, 0.05), (0.05, 0.1, 0.35), (0.12, 0.12, 0.12), (0.45, 0.3, 0.1), (0.1, 0.4, 0.4)])]


# ============================================================ ground & floors
def build_ground():
    c = col("Ground")
    mb = MB()
    mb.box((0, 0, -0.45), (900, 900, 0.1), 0)
    finish(mb, "GroundPlane", [M['grass']], c)

    mb = MB()   # ballast
    mb.box(((X_MIN + 62 + 0) / 2 + 9, 0, -0.2), (62 - (-32) + 0.0, 30, 0.4), 0)
    mb.box(((62 + 140) / 2, 0, -0.2), (140 - 62, 18, 0.4), 0)
    finish(mb, "BallastBed", [M['ballast']], c)

    mb = MB()   # concourse floor + plaza
    mb.box(((X_MIN - 0.5 + -32) / 2, 0, PLAT_H / 2), (-32 - (X_MIN - 0.5), 2 * HALF_W, PLAT_H), 0)
    finish(mb, "ConcourseFloor", [M['marble']], c)

    mb = MB()
    mb.box((-72.5, 0, PLAT_H / 2), (45, 80, PLAT_H), 0)
    finish(mb, "EntrancePlaza", [M['plaza']], c)

    # platforms (surface, coping, tactile)
    mb = MB()
    x0, x1 = -32.0, 62.0
    xc, xl = (x0 + x1) / 2, x1 - x0
    mb.box((xc, 0, PLAT_H / 2), (xl, 8, PLAT_H), 0)
    mb.box((xc, 11.5, PLAT_H / 2), (xl, 7, PLAT_H), 0)
    mb.box((xc, -11.5, PLAT_H / 2), (xl, 7, PLAT_H), 0)
    for ye in (4.0, -4.0, 8.0, -8.0):
        s = 1 if ye in (4.0, -4.0) else -1
        edge_dir = 1 if ye > 0 else -1
        inward = -edge_dir if ye in (4.0, -4.0) else edge_dir  # direction into platform
        if ye in (4.0, -4.0):
            inward = -1 if ye > 0 else 1
        else:
            inward = 1 if ye > 0 else -1
        mb.box((xc, ye + inward * 0.15, PLAT_H + 0.01), (xl, 0.3, 0.025), 1)
        mb.box((xc, ye + inward * 0.55, PLAT_H + 0.01), (xl, 0.4, 0.03), 2)
    finish(mb, "Platforms", [M['platform'], M['coping'], M['tactile']], c)


# ==================================================================== tracks
def build_tracks():
    c = col("Tracks")
    mb = MB()
    L = TRACK_X1 - TRACK_X0
    xc = (TRACK_X0 + TRACK_X1) / 2
    for y0 in TRACK_Y:
        for s in (-1, 1):
            mb.box((xc, y0 + s * GAUGE / 2, 0.22), (L, 0.07, 0.16), 1)
        x = TRACK_X0 + 0.3
        while x < TRACK_X1:
            mb.box((x, y0, 0.07), (0.26, 2.6, 0.14), 0)
            x += 0.65
    finish(mb, "Tracks", [M['sleeper'], M['rail']], c)

    mb = MB()   # buffer stops
    for y0 in TRACK_Y:
        mb.box((-31.6, y0, 0.55), (0.5, 2.2, 0.9), 0)
        for s in (-1, 1):
            mb.cyl((-31.0, y0 + s * 0.9, 0.75), 0.16, 0.7, 1, axis='X')
            mb.cyl((-30.6, y0 + s * 0.9, 0.75), 0.24, 0.08, 1, axis='X')
    finish(mb, "BufferStops", [M['red'], M['steel']], c)

    # catenary: island masts, droppers inside the shed, wires
    mb = MB()
    for y0 in TRACK_Y:
        mb.box((xc, y0, 5.6), (L, 0.03, 0.03), 1)
        mb.box((xc, y0, 5.95), (L, 0.025, 0.025), 1)
    x = 54.0
    while x < TRACK_X1:
        mb.cyl((x, 0, 3.4), 0.12, 6.2, 0)
        mb.box((x, 0, 6.0), (0.12, 12.4, 0.12), 0)
        for y0 in TRACK_Y:
            mb.cyl((x, y0, 5.8), 0.015, 0.4, 1)
        x += 20.0
    for k in range(13):
        xr = X_MIN + 8 * k
        for y0 in TRACK_Y:
            mb.cyl((xr, y0, (5.95 + arch_z(y0)) / 2), 0.012, arch_z(y0) - 5.95, 1)
    finish(mb, "Catenary", [M['steel_dark'], M['steel']], c)

    # signals
    mb = MB()
    for (x, y) in ((74, 7.7), (74, -7.7), (110, 7.7), (110, -7.7)):
        mb.cyl((x, y, 2.2), 0.08, 4.4, 0)
        mb.box((x, y, 4.5), (0.35, 0.3, 1.1), 0)
        mb.cyl((x - 0.18, y, 4.85), 0.1, 0.04, 2, axis='X')
        mb.cyl((x - 0.18, y, 4.5), 0.1, 0.04, 3, axis='X')
        mb.cyl((x - 0.18, y, 4.15), 0.1, 0.04, 4, axis='X')
    finish(mb, "Signals", [M['steel_dark'], M['black'], M['red_e'], M['black'], M['green_e']], c)


# ==================================================================== shed
def build_shed():
    c = col("Shed")
    n = 24
    pts = arch_pts(n)
    nb = 12
    xs = [X_MIN + (X_MAX_VAULT - X_MIN) * i / nb for i in range(nb + 1)]

    mb = MB()   # glass roof
    for ix in range(nb):
        for i in range(n):
            (ya, za), (yb, zb) = pts[i], pts[i + 1]
            mb.poly([(xs[ix], ya, za), (xs[ix + 1], ya, za), (xs[ix + 1], yb, zb), (xs[ix], yb, zb)])
    finish(mb, "GlassRoof", [M['glass_roof']], c)

    # ribs + purlins
    for k in range(nb + 1):
        x = xs[k]
        prof = [(x, -HALF_W, PLAT_H)] + [(x, y, z) for (y, z) in pts] + [(x, HALF_W, PLAT_H)]
        o = curve_obj("Rib_%02d" % k, [prof], 0.17, M['steel_dark'], c)
    purl = [[(X_MIN, y, z), (X_MAX_VAULT, y, z)] for i, (y, z) in enumerate(pts) if 2 <= i <= n - 2 and i % 3 == 0]
    curve_obj("Purlins", purl, 0.06, M['steel_dark'], c)

    # side walls
    mb = MB()
    xc, xl = (X_MIN + X_MAX_VAULT) / 2, X_MAX_VAULT - X_MIN
    for s in (-1, 1):
        mb.box((xc, s * (HALF_W + 0.12), 1.9), (xl, 0.24, 2.0), 0)
        mb.box((xc, s * (HALF_W + 0.02), 4.45), (xl, 0.05, 3.1), 1)
        mb.box((xc, s * (HALF_W + 0.02), 4.4), (xl, 0.14, 0.1), 2)
        for ix in range(nb):
            mb.box(((xs[ix] + xs[ix + 1]) / 2, s * (HALF_W + 0.02), 4.45), (0.1, 0.14, 3.1), 2)
    # end wall (x = X_MIN): brick either side of the entrance, glass above
    for s in (-1, 1):
        mb.box((X_MIN - 0.15, s * 9.25, 1.9), (0.3, 11.5, 2.0), 0)
    for yy in (-3.5, 3.5):
        mb.box((X_MIN - 0.1, yy, 1.9), (0.3, 0.3, 2.0), 2)
    mb.box((X_MIN - 0.1, 0, 2.95), (0.3, 30, 0.2), 2)
    for yy in range(-12, 13, 3):
        zt = arch_z(float(yy))
        mb.box((X_MIN - 0.05, float(yy), (2.9 + zt) / 2), (0.14, 0.16, zt - 2.9), 2)
    finish(mb, "ShedWalls", [M['brick'], M['glass_wall'], M['steel_dark']], c)

    mb = MB()   # end glass polygon
    poly = [(X_MIN - 0.02, -HALF_W, 2.9)] + [(X_MIN - 0.02, y, z) for (y, z) in pts] + [(X_MIN - 0.02, HALF_W, 2.9)]
    mb.poly(poly)
    finish(mb, "EndGlass", [M['glass_wall']], c)

    # facade sign
    mb = MB()
    mb.box((X_MIN - 0.5, 0, 7.6), (0.3, 16, 2.0), 0)
    finish(mb, "FacadeBoard", [M['sign_bg']], c)
    add_text("CENTRAL STATION", (X_MIN - 0.7, 0, 7.6), '-X', 1.5, M['sign_txt'], c)
    add_text("Platforms 1 - 3  |  Trains  |  Tickets", (X_MIN - 0.7, 0, 6.85), '-X', 0.45, M['board_txt'], c)

    # pendant lamps + real lights
    mb = MB()
    for k in range(nb + 1):
        x = xs[k]
        for y in (-7.0, 0.0, 7.0):
            top = arch_z(y)
            lz = top - 1.8
            mb.cyl((x, y, (top + lz) / 2), 0.02, top - lz, 0, seg=6)
            mb.cyl((x, y, lz), 0.45, 0.3, 0, r2=0.12)
            mb.cyl((x, y, lz - 0.16), 0.38, 0.04, 1)
            if k % 2 == 0:
                point_light("Lamp_%d_%d" % (k, int(y)), (x, y, lz - 0.5), 650, (1.0, 0.88, 0.7), c, 0.3)
    finish(mb, "PendantLamps", [M['steel_dark'], M['lamp']], c)


# =============================================================== station deco
def build_bench_proto():
    mb = MB()
    for yo in (-0.14, 0.0, 0.14):
        mb.box((0, yo, 0.44), (1.9, 0.13, 0.04), 0)
    for zo in (0.70, 0.86):
        mb.box((0, 0.22, zo), (1.9, 0.03, 0.12), 0)
    for sx in (-0.85, 0.85):
        mb.box((sx, 0, 0.40), (0.05, 0.5, 0.04), 1)
        mb.box((sx, -0.2, 0.2), (0.05, 0.05, 0.4), 1)
        mb.box((sx, 0.2, 0.2), (0.05, 0.05, 0.4), 1)
        mb.box((sx, 0.22, 0.6), (0.05, 0.04, 0.55), 1)
    return finish(mb, "BenchProto", [M['wood'], M['steel_dark']], col("Furniture"))


BENCHES = []   # (x, y, rotZ_deg)


def build_furniture():
    c = col("Furniture")
    proto = build_bench_proto()
    proto.hide_viewport = proto.hide_render = True
    me = proto.data
    spots = []
    for x in (-28, -16, -4, 8, 20, 32):
        spots.append((x, 0.30, 180))
        spots.append((x, -0.30, 0))
    for x in (-27, -14, 0, 14, 28, 40):
        spots.append((x, 13.6, 0))
        spots.append((x, -13.6, 180))
    for (x, y) in ((-37, 6), (-37, -6), (-45, 6), (-45, -6)):
        spots.append((x, y, 90))
    for i, (x, y, r) in enumerate(spots):
        add_obj("Bench_%02d" % i, me, c, (x, y, PLAT_H), (0, 0, radians(r)))
        BENCHES.append((x, y, r))

    # info pillars with screens (island)
    mb = MB()
    for x in (-22, -10, 2, 14, 26, 38):
        mb.box((x, 0, PLAT_H + 1.6), (0.3, 0.3, 3.2), 0)
        for s in (-1, 1):
            mb.box((x, s * 0.18, PLAT_H + 2.5), (1.0, 0.06, 0.6), 1)
            mb.box((x, s * 0.215, PLAT_H + 2.5), (0.85, 0.01, 0.12), 2)
    finish(mb, "InfoPillars", [M['steel_dark'], M['screen'], M['amber_e']], c)

    # hanging platform signs
    mb = MB()
    for x in (-26, 22):
        for (y, label) in ((11.5, "PLATFORM 1  |  TRACK 1"), (0.0, "PLATFORM 2  |  TRACKS 1 & 2"),
                           (-11.5, "PLATFORM 3  |  TRACK 2")):
            top = arch_z(y)
            for s in (-1, 1):
                mb.cyl((x, y + s * 1.1, (top + 7.3) / 2), 0.025, top - 7.3, 0, seg=6)
            mb.box((x, y, 7.0), (0.12, 3.8, 0.8), 1)
            add_text(label, (x + 0.08, y, 7.0), '+X', 0.28, M['sign_txt'], c)
            add_text(label, (x - 0.08, y, 7.0), '-X', 0.28, M['sign_txt'], c)
    finish(mb, "HangingSigns", [M['steel_dark'], M['sign_bg']], c)

    # ---- concourse
    mb = MB()
    gx = -34.0
    for k in range(11):
        y = -10.8 + 2.4 * k
        mb.box((gx, y, PLAT_H + 0.5), (0.5, 0.3, 1.0), 0)
        mb.box((gx, y, PLAT_H + 1.02), (0.3, 0.18, 0.05), 1)
    finish(mb, "TicketGates", [M['steel'], M['green_e']], c)

    mb = MB()   # kiosks
    for s, (mi, label) in ((1, (1, "CAFE")), (-1, (2, "NEWS & GIFTS"))):
        mb.box((-44, s * 11.5, PLAT_H + 1.6), (5.0, 3.0, 3.2), 0)
        mb.box((-44, s * 11.5, PLAT_H + 3.3), (5.6, 3.6, 0.2), 3)
        mb.box((-41.4, s * 11.5, PLAT_H + 1.0), (0.1, 2.4, 0.8), 4)
        mb.box((-41.45, s * 11.5, PLAT_H + 2.3), (0.05, 2.4, 1.0), 5)
        add_text(label, (-41.55, s * 11.5, PLAT_H + 3.1), '+X', 0.5, M['cafe_e'] if mi == 1 else M['news_e'], c)
    finish(mb, "Kiosks", [M['steel_dark'], M['wood'], M['steel'], M['red'], M['wood'], M['lamp']], c)

    mb = MB()   # info desk + clock
    mb.cyl((-41, 0, PLAT_H + 0.55), 2.0, 1.1, 0, seg=32)
    mb.cyl((-41, 0, PLAT_H + 1.12), 2.2, 0.08, 1, seg=32)
    mb.cyl((-41, 0, PLAT_H + 1.0), 1.4, 0.4, 2, seg=24)
    finish(mb, "InfoDesk", [M['wood'], M['steel_dark'], M['screen']], c)

    mb = MB()
    cx, cz = -41.0, 7.0
    top = arch_z(0.0)
    mb.cyl((cx, 0, (top + cz) / 2), 0.04, top - cz, 0, seg=6)
    mb.cyl((cx, 0, cz), 1.0, 0.14, 0, axis='X', seg=40)
    mb.cyl((cx, 0, cz), 0.9, 0.16, 1, axis='X', seg=40)
    for h in range(12):
        a = radians(30 * h)
        pre = Matrix.Translation((cx, 0, cz)) @ Matrix.Rotation(a, 4, 'X')
        mb.box((0, 0, 0.78), (0.18, 0.05, 0.14 if h % 3 else 0.22), 0, pre=pre)
    pre_m = Matrix.Translation((cx, 0, cz)) @ Matrix.Rotation(radians(-60), 4, 'X')
    pre_h = Matrix.Translation((cx, 0, cz)) @ Matrix.Rotation(radians(-300), 4, 'X')
    mb.box((0, 0, 0.35), (0.2, 0.06, 0.75), 0, pre=pre_m)
    mb.box((0, 0, 0.25), (0.2, 0.09, 0.55), 0, pre=pre_h)
    finish(mb, "StationClock", [M['black'], M['clockface']], c)

    # departure board hanging from rib at x=-34
    mb = MB()
    for y in (-4.0, 4.0):
        mb.cyl((-34, y, (arch_z(y) + 6.5) / 2), 0.03, arch_z(y) - 6.5, 0, seg=6)
    mb.box((-34, 0, 5.2), (0.2, 9.4, 2.6), 0)
    finish(mb, "DepartureBoard", [M['black']], c)
    fx = -34.12
    add_text("DEPARTURES", (fx, 4.4, 6.15), '-X', 0.34, M['sign_txt'], c, 'LEFT')
    rows = [("10:42", "Harbour Express", "2", "On time"), ("10:55", "Riverside Local", "1", "On time"),
            ("11:08", "Northgate Intercity", "2", "Delayed 5"), ("11:20", "Airport Link", "3", "On time"),
            ("11:34", "Old Town Metro", "1", "Boarding"), ("11:47", "Lakeside Express", "3", "On time")]
    for i, (t, d, p, s) in enumerate(rows):
        z = 5.7 - i * 0.37
        add_text(t, (fx, 4.4, z), '-X', 0.25, M['board_txt'], c, 'LEFT')
        add_text(d, (fx, 3.3, z), '-X', 0.25, M['board_txt'], c, 'LEFT')
        add_text(p, (fx, -2.6, z), '-X', 0.25, M['board_txt'], c, 'LEFT')
        add_text(s, (fx, -3.4, z), '-X', 0.25, M['board_txt'], c, 'LEFT')

    # ticket machines + plants
    mb = MB()
    for y in (-13.5, -11.0, 11.0, 13.5):
        pass
    for k, y in enumerate((-8.0, -6.0, 6.0, 8.0)):
        mb.box((-48.8, y, PLAT_H + 0.9), (0.7, 0.8, 1.8), 0)
        mb.box((-48.44, y, PLAT_H + 1.3), (0.02, 0.5, 0.45), 1)
    for (x, y) in ((-36, 11), (-36, -11), (-48.5, 3), (-48.5, -3), (-33, 13), (-33, -13)):
        mb.cyl((x, y, PLAT_H + 0.3), 0.4, 0.6, 2, seg=12)
        mb.sphere((x, y, PLAT_H + 1.2), 0.7, 3, scale=(1, 1, 1.2))
    finish(mb, "Machines_Plants", [M['steel_dark'], M['screen'], M['wood'], M['leaf']], c)

    # platform-end lamp posts (outside the shed)
    mb = MB()
    for x in (50, 56, 62):
        for y in (-11.5, 0, 11.5):
            mb.cyl((x, y, PLAT_H + 2.2), 0.06, 4.4, 0, seg=8)
            mb.box((x, y, PLAT_H + 4.4), (0.5, 0.25, 0.1), 1)
    finish(mb, "OutdoorLamps", [M['steel_dark'], M['lamp']], c)


def build_surroundings():
    c = col("Surroundings")
    rnd = random.Random(5)
    mb = MB()
    for i in range(70):
        side = rnd.choice((-1, 1))
        x = rnd.uniform(-130, 190)
        y = side * rnd.uniform(48, 150)
        w, d, h = rnd.uniform(10, 26), rnd.uniform(10, 26), rnd.uniform(14, 70)
        mb.box((x, y, h / 2 - 0.4), (w, d, h), 0)
    for i in range(10):
        x, y = rnd.uniform(-190, -110), rnd.uniform(-70, 70)
        w, d, h = rnd.uniform(12, 24), rnd.uniform(12, 24), rnd.uniform(20, 60)
        mb.box((x, y, h / 2 - 0.4), (w, d, h), 0)
    finish(mb, "CitySkyline", [M['city']], c)
    mb = MB()
    for (x, y) in ((-60, 14), (-60, -14), (-66, 24), (-66, -24), (-78, 10), (-78, -10),
                   (-86, 22), (-86, -22), (-62, 34), (-62, -34)):
        mb.cyl((x, y, PLAT_H + 1.5), 0.25, 3.0, 0, seg=8)
        mb.sphere((x, y, PLAT_H + 4.2), 2.2, 1, scale=(1, 1, 1.15))
    finish(mb, "PlazaTrees", [M['wood'], M['leaf']], c)


# ===================================================================== trains
def build_carriage(name, stripe_mat, nose_neg, nose_pos, mats, coll):
    L, W, z0, z1 = 17.0, 2.9, 1.15, 4.2
    H = z1 - z0
    NL = 2.4
    mb = MB()
    mb.box((0, 0, (z0 + z1) / 2), (L, W, H), 0)
    for v in mb.bm.verts:
        if v.co.z > (z0 + z1) / 2:
            if nose_neg and v.co.x < -L / 2 + 0.01:
                v.co.x += NL
            if nose_pos and v.co.x > L / 2 - 0.01:
                v.co.x -= NL
    mb.bm.normal_update()
    for f in mb.bm.faces:
        if (nose_neg and f.normal.x < -0.3) or (nose_pos and f.normal.x > 0.3):
            f.material_index = 2
    # stripe
    xs = -L / 2 + (0.55 if nose_neg else -0.01)
    xe = L / 2 - (0.55 if nose_pos else -0.01)
    mb.box(((xs + xe) / 2, 0, z0 + 0.65), (xe - xs, W + 0.02, 0.35), 1)
    mb.box(((xs + xe) / 2, 0, z0 + 0.3), (xe - xs, W + 0.015, 0.06), 1)
    # windows & doors
    wins = [(-6.6, 2.2), (-2.2, 2.2), (2.2, 2.2), (6.6, 2.2)]
    if nose_neg:
        wins[0] = (-6.1, 1.3)
    if nose_pos:
        wins[3] = (6.1, 1.3)
    for s in (-1, 1):
        for (cx, ww) in wins:
            mb.box((cx, s * W / 2, z0 + 1.9), (ww, 0.06, 1.2), 2)
        for cx in (-4.25, 4.25):
            mb.box((cx, s * W / 2, z0 + 1.35), (1.4, 0.07, 2.5), 7)
            mb.box((cx, s * W / 2, z0 + 1.85), (0.9, 0.09, 1.1), 2)
    # roof + AC
    rs = (-L / 2 + NL + 0.2) if nose_neg else (-L / 2 + 0.2)
    re = (L / 2 - NL - 0.2) if nose_pos else (L / 2 - 0.2)
    mb.box(((rs + re) / 2, 0, z1 + 0.06), (re - rs, W * 0.9, 0.14), 3)
    for cx in (-3.0, 3.0):
        mb.box((cx, 0, z1 + 0.25), (2.4, 1.4, 0.3), 3)
    # underframe
    mb.box((0, 0, 0.95), (L - 5.0, 2.1, 0.4), 4)
    # bogies
    for bx in (-5.8, 5.8):
        mb.box((bx, 0, 0.75), (3.2, 1.5, 0.2), 5)
        for ax in (-0.95, 0.95):
            mb.cyl((bx + ax, 0, 0.72), 0.07, 1.6, 5, axis='Y', seg=8)
            for sy in (-1, 1):
                mb.cyl((bx + ax, sy * 0.7175, 0.72), 0.42, 0.13, 5, axis='Y', seg=24)
    # lights
    if nose_neg:
        for sy in (-1, 1):
            mb.box((-L / 2 + 0.28, sy * 0.95, z0 + 0.45), (0.1, 0.4, 0.2), 6)
        mb.box((-L / 2 + 1.95, 0, z0 + 2.55), (0.06, 1.5, 0.28), 8)
    if nose_pos:
        for sy in (-1, 1):
            mb.box((L / 2 - 0.28, sy * 0.95, z0 + 0.45), (0.1, 0.4, 0.2), 6)
        mb.box((L / 2 - 1.95, 0, z0 + 2.55), (0.06, 1.5, 0.28), 8)
    # gangway bellows
    if not nose_neg:
        mb.box((-L / 2 - 0.15, 0, z0 + 1.35), (0.34, 2.6, 2.7), 4)
    if not nose_pos:
        mb.box((L / 2 + 0.15, 0, z0 + 1.35), (0.34, 2.6, 2.7), 4)
    return finish(mb, name, mats, coll)


def build_train(name, y, x, stripe_color):
    c = col("Trains")
    mats = [mat(name + "_Body", (0.86, 0.88, 0.92), 0.3, 0.4),
            mat(name + "_Stripe", stripe_color, 0.35, 0.2),
            mat(name + "_Win", (0.02, 0.04, 0.07), 0.08, 0.0, emit=(1.0, 0.85, 0.55), strength=0.25),
            mat(name + "_Roof", (0.3, 0.32, 0.35), 0.5, 0.5),
            mat(name + "_Under", (0.04, 0.04, 0.05), 0.7),
            M['steel'],
            mat(name + "_Head", (1, 1, 1), 0.3, emit=(1, 1, 0.95), strength=25),
            mat(name + "_Door", (0.28, 0.32, 0.38), 0.4, 0.4),
            M['amber_e']]
    parent = bpy.data.objects.new(name, None)
    c.objects.link(parent)
    parent.location = (x, y, 0)
    a = build_carriage(name + "_CarA", None, True, False, mats, c)
    b = build_carriage(name + "_CarB", None, False, False, mats, c)
    d = build_carriage(name + "_CarC", None, False, True, mats, c)
    for o, cx in ((a, -17.3), (b, 0.0), (d, 17.3)):
        o.parent = parent
        o.location = (cx, 0, 0)
    return parent


# ================================================================= passengers
def person(pose, skin, shirt, pants, hair, bag, female, swing=0.0):
    mb = MB()
    ph = 0.85 if pose != 'sit' else 0.50
    if pose == 'sit':
        for sx in (-0.1, 0.1):
            mb.box((sx, -0.2, ph - 0.08), (0.15, 0.45, 0.15), 2)
            mb.box((sx, -0.43, (ph - 0.15) / 2), (0.13, 0.13, ph - 0.15), 2)
    else:
        a = radians(swing)
        for sx, sg in ((-0.1, 1), (0.1, -1)):
            pre = Matrix.Translation((sx, 0, ph)) @ Matrix.Rotation(a * sg, 4, 'X')
            mb.box((0, 0, -0.425), (0.15, 0.17, 0.85), 2, pre=pre)
    if female:
        mb.box((0, 0, ph + 0.28), (0.36, 0.22, 0.56), 1)
        mb.cyl((0, 0, ph - 0.05), 0.27, 0.42, 1, r2=0.16, seg=12)
    else:
        mb.box((0, 0, ph + 0.30), (0.42, 0.24, 0.60), 1)
    mb.sphere((0, 0, ph + 0.74), 0.115, 0, scale=(1, 1.05, 1.15))
    mb.sphere((0, 0.035, ph + 0.78), 0.122, 3, scale=(1, 1.05, 1.0))
    arm_a = {'stand': 0.0, 'walk': -swing, 'sit': -35.0}[pose]
    for sx, sg in ((-0.27, 1), (0.27, -1)):
        pre = Matrix.Translation((sx, 0, ph + 0.55)) @ Matrix.Rotation(radians(arm_a) * sg, 4, 'X')
        mb.box((0, 0, -0.27), (0.085, 0.09, 0.54), 1, pre=pre)
        mb.sphere((0, 0, -0.57), 0.05, 0, pre=pre)
    if bag == 'back':
        mb.box((0, 0.17, ph + 0.35), (0.3, 0.14, 0.4), 4)
    elif bag == 'suit':
        mb.box((0.42, -0.02, 0.28), (0.2, 0.42, 0.55), 4)
        mb.box((0.42, -0.02, 0.6), (0.03, 0.03, 0.12), 2)
    elif bag == 'shoulder':
        mb.box((-0.3, 0.05, ph + 0.15), (0.1, 0.28, 0.25), 4)
    return mb


def make_passenger(rnd, pose, loc, rot_deg, coll, bag=None):
    skin, shirt = rnd.choice(M['skin']), rnd.choice(M['shirt'])
    pants, hair = rnd.choice(M['pants']), rnd.choice(M['hair'])
    if bag is None:
        bag = rnd.choice([None, None, 'back', 'suit', 'shoulder'])
    mb = person(pose, skin, shirt, pants, hair, bag, rnd.random() < 0.4,
                swing=rnd.uniform(16, 24) if pose == 'walk' else 0.0)
    sc = rnd.uniform(0.92, 1.08)
    o = finish(mb, "Passenger", [skin, shirt, pants, hair, rnd.choice(M['bag'])], coll,
               loc=loc, rot=(0, 0, radians(rot_deg)))
    o.scale = (sc, sc, sc)
    return o


def key_walk(o, axis, fixed, a, b, rot0, f0=1):
    """Walk back and forth between a and b along an axis. Linear keys."""
    def pos(v):
        return (v, fixed, PLAT_H if o.get('plat', True) else PLAT_H) if axis == 'x' else (fixed, v, PLAT_H)
    keys = [(1, a, rot0), (300, b, rot0), (312, b, rot0 + 180), (611, a, rot0 + 180), (623, a, rot0 + 360)]
    for f, v, r in keys:
        o.location = pos(v)
        o.rotation_euler = (0, 0, radians(r))
        o.keyframe_insert('location', frame=f)
        o.keyframe_insert('rotation_euler', frame=f)


def build_passengers(train_x):
    c = col("Passengers")
    rnd = random.Random(21)
    z = PLAT_H
    door_x = [train_x + cx + d for cx in (-17.3, 0.0, 17.3) for d in (-4.25, 4.25)]

    def xspot():
        if rnd.random() < 0.55:
            return rnd.choice(door_x) + rnd.gauss(0, 1.3)
        return rnd.uniform(-28, 44)

    # waiting on platforms
    for _ in range(22):   # island north (faces +Y)
        make_passenger(rnd, 'stand', (xspot(), rnd.uniform(2.3, 3.1), z), 180 + rnd.uniform(-25, 25), c)
    for _ in range(14):   # island south (faces -Y)
        make_passenger(rnd, 'stand', (xspot(), rnd.uniform(-3.1, -2.3), z), rnd.uniform(-25, 25), c)
    for _ in range(12):   # outer north (faces -Y)
        make_passenger(rnd, 'stand', (xspot(), rnd.uniform(9.0, 10.0), z), rnd.uniform(-25, 25), c)
    for _ in range(8):    # outer south (faces +Y)
        make_passenger(rnd, 'stand', (xspot(), rnd.uniform(-10.0, -9.0), z), 180 + rnd.uniform(-25, 25), c)

    # seated on benches
    for (bx, by, br) in BENCHES:
        if rnd.random() < 0.65:
            for off in rnd.sample((-0.45, 0.45), rnd.choice((1, 2))):
                a = radians(br)
                ox = cos(a) * off - sin(a) * 0.08
                oy = sin(a) * off + cos(a) * 0.08
                make_passenger(rnd, 'sit', (bx + ox, by + oy, z), br, c)

    # concourse crowd (standing, chatting pairs, walkers)
    for _ in range(16):
        x, y = rnd.uniform(-48, -36), rnd.uniform(-9, 9)
        if (x + 41) ** 2 + y ** 2 < 3.0 ** 2:
            continue
        make_passenger(rnd, 'stand', (x, y, z), rnd.uniform(0, 360), c)
    for k in range(4):   # chatting pairs
        x, y = rnd.uniform(-47, -37), rnd.uniform(-8, 8)
        if (x + 41) ** 2 + y ** 2 < 3.5 ** 2:
            continue
        make_passenger(rnd, 'stand', (x, y, z), 0, c)
        make_passenger(rnd, 'stand', (x, y - 0.9, z), 180, c)

    # animated walkers
    lanes = [('x', 1.9, 90), ('x', -1.9, -90), ('x', 10.6, 90), ('x', -10.6, -90),
             ('x', 12.3, 90), ('x', -12.3, -90), ('x', 1.9, -90), ('x', -1.9, 90)]
    for i, (ax, fixed, r0) in enumerate(lanes):
        for j in range(2):
            a = rnd.uniform(-28, -5) if j == 0 else rnd.uniform(5, 18)
            b = a + rnd.uniform(20, 28)
            o = make_passenger(rnd, 'walk', (a, fixed, z), r0, c)
            key_walk(o, 'x', fixed + rnd.uniform(-0.3, 0.3), a, b, r0)
    for fixed in (-38.0, -46.0, -42.0, -35.5):
        for r0 in (180, 0):
            a = rnd.uniform(-12, -6)
            b = a + rnd.uniform(14, 20)
            if abs(fixed + 41) < 6:
                continue
            o = make_passenger(rnd, 'walk', (fixed, a, z), r0, c)
            key_walk(o, 'y', fixed + rnd.uniform(-0.4, 0.4), a, b, r0)
            # for y-walkers starting rotation: heading +Y needs 180, heading -Y needs 0
    # walkers heading toward the gates
    return c


# ========================================================== camera / world
def build_camera_tour():
    c = col("Camera")
    pts = [(-85, 0), (-58, 0), (-49, 0), (-43, 5.5), (-37, 3.5), (-34, 0), (-30, 2), (-10, 2),
           (15, 2), (40, 2), (58, 0)]
    cu = bpy.data.curves.new("TourPath", 'CURVE')
    cu.dimensions = '3D'
    cu.use_path = True
    cu.path_duration = FRAME_END - 4
    sp = cu.splines.new('BEZIER')
    sp.bezier_points.add(len(pts) - 1)
    for bp, (x, y) in zip(sp.bezier_points, pts):
        bp.co = (x, y, PLAT_H + EYE)
        bp.handle_left_type = bp.handle_right_type = 'AUTO'
    path = add_obj("TourPath", cu, c)
    rig = bpy.data.objects.new("TourRig", None)
    c.objects.link(rig)
    con = rig.constraints.new('FOLLOW_PATH')
    con.target = path
    con.use_curve_follow = True
    con.forward_axis = 'FORWARD_X'
    con.up_axis = 'UP_Z'
    cam_d = bpy.data.cameras.new("TourCamera")
    cam_d.lens = 20
    cam_d.clip_start = 0.05
    cam_d.clip_end = 600
    cam = add_obj("TourCamera", cam_d, c, (0, 0, 0), (radians(90), 0, radians(-90)), parent=rig)
    bpy.context.scene.camera = cam
    cu.eval_time = 0
    cu.keyframe_insert('eval_time', frame=1)
    cu.eval_time = cu.path_duration
    cu.keyframe_insert('eval_time', frame=FRAME_END - 1)

    # overview camera
    od = bpy.data.cameras.new("OverviewCamera")
    od.lens = 28
    od.clip_end = 800
    add_obj("OverviewCamera", od, c, (60, -75, 38), (radians(66), 0, radians(35)))


def build_world_and_render():
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, FRAME_END
    sc.render.fps = 24
    sc.render.resolution_x, sc.render.resolution_y = 1920, 1080
    for eng in ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'):
        try:
            sc.render.engine = eng
            break
        except Exception:
            continue
    for attr, val in (("use_gtao", True), ("use_ssr", True), ("use_bloom", True),
                      ("use_soft_shadows", True), ("taa_render_samples", 64),
                      ("use_raytracing", True), ("use_shadows", True)):
        try:
            setattr(sc.eevee, attr, val)
        except Exception:
            pass
    try:
        sc.view_settings.view_transform = 'AgX'
    except Exception:
        try:
            sc.view_settings.view_transform = 'Filmic'
        except Exception:
            pass

    w = bpy.data.worlds.get("StationWorld") or bpy.data.worlds.new("StationWorld")
    sc.world = w
    w.use_nodes = True
    nt = w.node_tree
    bg = nt.nodes.get("Background")
    try:
        sky = nt.nodes.new("ShaderNodeTexSky")
        for t in ('NISHITA', 'MULTIPLE_SCATTERING'):
            try:
                sky.sky_type = t
                break
            except Exception:
                continue
        try:
            sky.sun_elevation = radians(11)
            sky.sun_rotation = radians(90)
        except Exception:
            pass
        nt.links.new(sky.outputs[0], bg.inputs['Color'])
        bg.inputs['Strength'].default_value = 1.0
    except Exception:
        bg.inputs['Color'].default_value = (0.35, 0.5, 0.75, 1)
    # mist = normalised depth
    try:
        w.mist_settings.start = 0
        w.mist_settings.depth = 90
        w.mist_settings.falloff = 'LINEAR'
    except Exception:
        pass

    sd = bpy.data.lights.new("LowSun", 'SUN')
    sd.energy = 4.0
    sd.color = (1.0, 0.82, 0.62)
    sd.angle = radians(2.0)
    add_obj("LowSun", sd, col("Camera"), (0, 0, 60), (radians(78), 0, radians(-90)))

    # Z-buffer: depth + mist passes and a compositor setup
    try:
        vl = bpy.context.view_layer
        vl.use_pass_z = True
        vl.use_pass_mist = True
    except Exception:
        pass
    try:
        sc.use_nodes = True
        tree = sc.node_tree
        tree.nodes.clear()
        rl = tree.nodes.new("CompositorNodeRLayers")
        comp = tree.nodes.new("CompositorNodeComposite")
        mp = tree.nodes.new("CompositorNodeMapRange")
        mp.inputs['From Min'].default_value = 0
        mp.inputs['From Max'].default_value = 90
        mp.inputs['To Min'].default_value = 1
        mp.inputs['To Max'].default_value = 0
        view = tree.nodes.new("CompositorNodeViewer")
        tree.links.new(rl.outputs['Image'], comp.inputs['Image'])
        tree.links.new(rl.outputs['Depth'], mp.inputs['Value'])
        tree.links.new(mp.outputs['Value'], view.inputs['Image'])
    except Exception:
        pass


def build_train_animation():
    sc = bpy.context.scene
    t = bpy.data.objects.get("Train_B")
    if t:
        end = t.location.x
        t.location.x = 138
        t.keyframe_insert('location', frame=1)
        t.location.x = end
        t.keyframe_insert('location', frame=330)


# ============================================================= build driver
def build():
    reset()
    sc = bpy.context.scene
    make_materials()
    build_ground()
    build_tracks()
    build_shed()
    build_furniture()
    build_surroundings()
    train_x = -2.0
    build_train("Train_A", 6.0, train_x, (0.05, 0.25, 0.75))
    build_train("Train_B", -6.0, train_x, (0.9, 0.35, 0.05))
    build_train_animation()
    try:
        bpy.context.preferences.edit.keyframe_new_interpolation_type = 'LINEAR'
    except Exception:
        pass
    build_passengers(train_x)
    build_camera_tour()
    try:
        bpy.context.preferences.edit.keyframe_new_interpolation_type = 'BEZIER'
    except Exception:
        pass
    build_world_and_render()
    sc.frame_set(1)
    print("Station built:", len(bpy.data.objects), "objects")


# =================================================================== UI layer
VIEWPOINTS = {
    'PLAZA':      ("Entrance plaza",   (-82, 0, PLAT_H + EYE), -90, 0),
    'CONCOURSE':  ("Concourse",        (-44, 6, PLAT_H + EYE), -90, 0),
    'GATES':      ("Ticket gates",     (-37, 0, PLAT_H + EYE), -90, 0),
    'ISLAND':     ("Island platform",  (-29, 2, PLAT_H + EYE), -90, 0),
    'PLATFORM1':  ("Platform 1",       (-24, 11.5, PLAT_H + EYE), -90, 0),
    'PLATFORM_END': ("Look back from platform end", (44, 2, PLAT_H + EYE), 90, 0),
    'BIRD':       ("Bird's-eye view",  (20, -78, 55), 0, -38),
}


def _view3d(context):
    area = context.area if (context.area and context.area.type == 'VIEW_3D') else None
    if area is None:
        for a in context.screen.areas:
            if a.type == 'VIEW_3D':
                area = a
                break
    if area is None:
        return None, None, None
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    space = next((s for s in area.spaces if s.type == 'VIEW_3D'), None)
    return area, region, space


def set_view(space, loc, yaw, pitch):
    r3d = space.region_3d
    r3d.view_perspective = 'PERSP'
    eul = Euler((radians(90 + pitch), 0, radians(yaw)), 'XYZ')
    m = Matrix.Translation(loc) @ eul.to_matrix().to_4x4()
    r3d.view_matrix = m.inverted()


def setup_walk_prefs():
    try:
        w = bpy.context.preferences.inputs.walk_navigation
        w.use_gravity = True
        w.view_height = EYE
        w.jump_height = 0.6
        w.walk_speed = 4.0
        w.walk_speed_factor = 4.0
        w.mouse_speed = 1.0
    except Exception:
        pass


def setup_viewport(space):
    try:
        sh = space.shading
        sh.type = 'MATERIAL'
        sh.use_scene_lights = True
        sh.use_scene_world = True
    except Exception:
        pass
    space.clip_start = 0.05
    space.clip_end = 700
    space.lens = 22
    try:
        space.overlay.show_floor = False
        space.overlay.show_axis_x = space.overlay.show_axis_y = False
    except Exception:
        pass


class STATION_OT_walk(bpy.types.Operator):
    bl_idname = "station.walk"
    bl_label = "Start Walk Mode"
    bl_description = "First-person navigation: WASD + mouse, Shift run, Tab gravity, click to confirm"

    def execute(self, context):
        area, region, space = _view3d(context)
        if area is None:
            self.report({'ERROR'}, "No 3D viewport found")
            return {'CANCELLED'}
        setup_walk_prefs()
        space.region_3d.view_perspective = 'PERSP'
        with context.temp_override(area=area, region=region):
            bpy.ops.view3d.walk('INVOKE_DEFAULT')
        return {'FINISHED'}


class STATION_OT_goto(bpy.types.Operator):
    bl_idname = "station.goto"
    bl_label = "Go to viewpoint"
    where: bpy.props.StringProperty()

    def execute(self, context):
        area, region, space = _view3d(context)
        if area is None:
            return {'CANCELLED'}
        label, loc, yaw, pitch = VIEWPOINTS[self.where]
        set_view(space, loc, yaw, pitch)
        return {'FINISHED'}


class STATION_OT_setup(bpy.types.Operator):
    bl_idname = "station.setup_view"
    bl_label = "Prepare viewport (lit preview)"

    def execute(self, context):
        area, region, space = _view3d(context)
        if space is None:
            return {'CANCELLED'}
        setup_viewport(space)
        return {'FINISHED'}


class STATION_OT_depth(bpy.types.Operator):
    bl_idname = "station.depth_view"
    bl_label = "Depth view"
    mode: bpy.props.EnumProperty(items=[('MIST', "Mist depth", ""), ('Z', "Raw Z", ""), ('COMBINED', "Normal", "")])

    def execute(self, context):
        area, region, space = _view3d(context)
        if space is None:
            return {'CANCELLED'}
        try:
            context.view_layer.use_pass_z = True
            context.view_layer.use_pass_mist = True
            if self.mode == 'COMBINED':
                space.shading.render_pass = 'COMBINED'
                space.shading.type = 'MATERIAL'
            else:
                space.shading.type = 'RENDERED'
                space.shading.render_pass = self.mode
        except Exception as e:
            self.report({'WARNING'}, "Switch to Rendered mode and pick the Mist/Z pass in the shading popover (%s)" % e)
        return {'FINISHED'}


class STATION_OT_tour(bpy.types.Operator):
    bl_idname = "station.tour"
    bl_label = "Play guided tour camera"

    def execute(self, context):
        cam = bpy.data.objects.get("TourCamera")
        if cam:
            context.scene.camera = cam
            area, region, space = _view3d(context)
            if space:
                space.region_3d.view_perspective = 'CAMERA'
            context.scene.frame_set(1)
            bpy.ops.screen.animation_play()
        return {'FINISHED'}


class STATION_PT_main(bpy.types.Panel):
    bl_label = "Station Digital Twin"
    bl_idname = "STATION_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Station"

    def draw(self, context):
        lay = self.layout
        b = lay.box()
        b.label(text="Navigate", icon='VIEW_CAMERA')
        b.operator("station.setup_view", icon='SHADING_RENDERED')
        b.operator("station.walk", icon='PLAY')
        b.label(text="W A S D + mouse | Shift run")
        b.label(text="Tab gravity/fly (Q/E down/up)")
        b.label(text="Click = stay | Esc = cancel")
        b = lay.box()
        b.label(text="Teleport", icon='TRACKING')
        for key, (label, *_rest) in VIEWPOINTS.items():
            b.operator("station.goto", text=label).where = key
        b.operator("station.tour", icon='CAMERA_DATA')
        b = lay.box()
        b.label(text="Projection & Z-buffer", icon='VIEW_PERSPECTIVE')
        b.operator("view3d.view_persportho", text="Perspective / Orthographic")
        sp = context.space_data
        if sp and sp.type == 'VIEW_3D':
            b.prop(sp, "lens", text="Focal length")
            b.prop(sp, "clip_start", text="Near clip")
            b.prop(sp, "clip_end", text="Far clip")
        r = b.row(align=True)
        r.operator("station.depth_view", text="Depth").mode = 'MIST'
        r.operator("station.depth_view", text="Raw Z").mode = 'Z'
        r.operator("station.depth_view", text="Normal").mode = 'COMBINED'


CLASSES = (STATION_OT_walk, STATION_OT_goto, STATION_OT_setup, STATION_OT_depth,
           STATION_OT_tour, STATION_PT_main)


def register():
    for cls in CLASSES:
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


def prepare_first_view():
    try:
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                space = next(s for s in area.spaces if s.type == 'VIEW_3D')
                setup_viewport(space)
                label, loc, yaw, pitch = VIEWPOINTS['PLAZA']
                set_view(space, loc, yaw, pitch)
                area.spaces.active.show_region_ui = True
    except Exception:
        pass


if __name__ == "__main__":
    build()
    register()
    setup_walk_prefs()
    prepare_first_view()
