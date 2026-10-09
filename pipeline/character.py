"""Shri Ram: MPFB human + mixamo-compatible rig, costume built from the body surface,
groomed hair with topknot, tilak, rudraksha mala, armlets, bracers, sandals, bow, quiver, arrows."""
import glob
import importlib
import math
import os
import random
import shutil

import bpy
import bmesh
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

import util

MPFB_PKG = "bl_ext.user_default.mpfb"
P = "mixamorig:"

SKIN_TINT = (0.50, 0.60, 1.10)       # luminous blue-lavender divine skin, per the reference sheet
SAFFRON = (0.9, 0.42, 0.03)
VERMILION = (0.72, 0.12, 0.04)
GOLD = (0.83, 0.58, 0.24)
LEATHER = (0.23, 0.12, 0.05)
WOOD = (0.36, 0.19, 0.07)
RUDRAKSHA = (0.25, 0.11, 0.05)


# ---------------------------------------------------------------- MPFB setup
def mpfb_services():
    import addon_utils
    addon_utils.enable(MPFB_PKG, default_set=True, persistent=True)
    hs = importlib.import_module(MPFB_PKG + ".services.humanservice").HumanService
    return hs


def install_system_assets(src):
    data = os.path.join(bpy.utils.extension_path_user(MPFB_PKG, create=True), "data")
    root = None
    for dp, dn, fn in os.walk(src):
        if "eyes" in dn and ("skins" in dn or "eyebrows" in dn):
            root = dp
            break
    if root is None:
        print("!! MPFB system assets layout not recognised")
        return data
    os.makedirs(data, exist_ok=True)
    shutil.copytree(root, data, dirs_exist_ok=True)
    print("MPFB assets ->", data, os.listdir(data))
    return data


def find_asset(data, sub, prefer=()):
    files = sorted(glob.glob(os.path.join(data, sub, "**", "*.mhclo"), recursive=True))
    for p in prefer:
        for f in files:
            if p in os.path.basename(f).lower():
                return f
    return files[0] if files else None


# ---------------------------------------------------------------- helpers
class MB:
    """Mesh builder with material indices."""

    def __init__(self):
        self.v, self.f, self.mi = [], [], []

    def add(self, verts, faces, mat=0):
        o = len(self.v)
        self.v += [Vector(v) for v in verts]
        self.f += [tuple(i + o for i in face) for face in faces]
        self.mi += [mat] * len(faces)

    def build(self, name, mats, coll=None, smooth=True):
        obj = util.mesh_object(name, self.v, self.f, coll, smooth)
        for m in mats:
            obj.data.materials.append(m)
        obj.data.polygons.foreach_set("material_index", self.mi)
        return obj


def uv_sphere(center, r, seg=10, rings=6, scale=(1, 1, 1)):
    verts, faces = [], []
    for i in range(rings + 1):
        th = math.pi * i / rings
        for j in range(seg):
            ph = 2 * math.pi * j / seg
            verts.append(center + Vector((r * scale[0] * math.sin(th) * math.cos(ph),
                                          r * scale[1] * math.sin(th) * math.sin(ph),
                                          r * scale[2] * math.cos(th))))
    for i in range(rings):
        for j in range(seg):
            a = i * seg + j
            b = i * seg + (j + 1) % seg
            faces.append((a, b, b + seg, a + seg))
    return verts, faces


def ring_points(center, axis, radius, n):
    axis = axis.normalized()
    ref = Vector((0, 0, 1)) if abs(axis.z) < 0.9 else Vector((1, 0, 0))
    u = axis.cross(ref).normalized()
    w = axis.cross(u).normalized()
    return [center + (u * math.cos(2 * math.pi * k / n) + w * math.sin(2 * math.pi * k / n)) * radius
            for k in range(n)]


def parent_to_bone(obj, rig, bone):
    mw = obj.matrix_world.copy()
    obj.parent = rig
    obj.parent_type = "BONE"
    obj.parent_bone = bone
    bpy.context.view_layer.update()
    obj.matrix_world = mw


def skin_material(data):
    m, nt, b = util.new_mat("M_ShriRam_Skin")
    tex = None
    if data:
        cands = sorted(f for f in glob.glob(os.path.join(data, "skins", "**", "*.png"), recursive=True)
                       if "diffuse" in f.lower() and "female" not in f.lower())
        pref = ([f for f in cands if "light" in f.lower() and "young" in f.lower()]
                or [f for f in cands if "light" in f.lower()] or cands)
        tex = pref[0] if pref else None
    tint = nt.nodes.new("ShaderNodeRGB")
    tint.outputs[0].default_value = (*SKIN_TINT, 1)
    if tex:
        im = nt.nodes.new("ShaderNodeTexImage")
        im.image = bpy.data.images.load(tex)
        hs = nt.nodes.new("ShaderNodeHueSaturation")
        hs.inputs["Saturation"].default_value = 0.0
        hs.inputs["Value"].default_value = 0.95
        nt.links.new(im.outputs[0], hs.inputs["Color"])
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.blend_type = "MULTIPLY"
        mix.inputs["Factor"].default_value = 1.0
        nt.links.new(hs.outputs[0], mix.inputs[6])
        nt.links.new(tint.outputs[0], mix.inputs[7])
        nt.links.new(mix.outputs[2], b.inputs["Base Color"])
        print("skin texture", tex)
    else:
        nt.links.new(tint.outputs[0], b.inputs["Base Color"])
    util.setp(b, "Subsurface Weight", 0.32)
    util.setp(b, "Subsurface Radius", (1.0, 0.6, 0.45))
    util.setp(b, "Subsurface Scale", 0.012)
    util.setp(b, "Roughness", 0.47)
    util.setp(b, "Specular IOR Level", 0.42)
    util.setp(b, "Coat Weight", 0.04)
    util.add_bump(nt, b, scale=1800.0, strength=0.05)
    return m


def procedural_eyes(eyes):
    """MPFB procedural eyes: set a warm dark-brown iris. Returns False if not procedural."""
    recolored, procedural = False, False
    for slot in eyes.material_slots:
        m = slot.material
        if not m or not m.use_nodes:
            continue
        trees = [m.node_tree] + [n.node_tree for n in m.node_tree.nodes if n.type == "GROUP" and n.node_tree]
        procedural = procedural or not any(n.type == "TEX_IMAGE" for nt in trees for n in nt.nodes)
        for nt in trees:
            for n in nt.nodes:
                for inp in n.inputs:
                    nm = inp.name.lower()
                    if "iris" in nm and "color" in nm and inp.type == "RGBA":
                        inp.default_value = (0.16, 0.075, 0.03, 1) if "minor" in nm else (0.085, 0.04, 0.018, 1)
                        recolored = True
    print("procedural eyes:", procedural, "iris recolored:", recolored)
    return procedural


def eye_material(data, eyes):
    imgs = sorted(glob.glob(os.path.join(data, "eyes", "**", "*.png"), recursive=True))
    pick = ([f for f in imgs if "brown" in os.path.basename(f).lower() and "light" not in os.path.basename(f).lower()]
            or [f for f in imgs if "brown" in f.lower()] or imgs)
    m, nt, b = util.new_mat("M_Eyes")
    if pick:
        im = nt.nodes.new("ShaderNodeTexImage")
        im.image = bpy.data.images.load(pick[0])
        nt.links.new(im.outputs[0], b.inputs["Base Color"])
        print("eye texture", pick[0])
    else:
        util.setp(b, "Base Color", (0.12, 0.06, 0.03, 1))
    util.setp(b, "Roughness", 0.3)
    util.setp(b, "Coat Weight", 1.0)
    util.setp(b, "Coat Roughness", 0.03)
    util.setp(b, "Subsurface Weight", 0.1)
    eyes.data.materials.clear()
    eyes.data.materials.append(m)


def fabric_material(name, color, border=None, gold_pattern=False):
    m, nt, b = util.new_mat(name)
    util.setp(b, "Base Color", (*color, 1))
    util.setp(b, "Roughness", 0.78)
    util.setp(b, "Sheen Weight", 0.22)
    util.setp(b, "Sheen Tint", (1.0, 0.9, 0.7, 1))
    util.setp(b, "Subsurface Weight", 0.08)
    util.setp(b, "Specular IOR Level", 0.3)
    # woven micro texture
    tc = nt.nodes.new("ShaderNodeTexCoord")
    wave1 = nt.nodes.new("ShaderNodeTexWave")
    wave1.inputs["Scale"].default_value = 900
    wave2 = nt.nodes.new("ShaderNodeTexWave")
    wave2.inputs["Scale"].default_value = 900
    wave2.bands_direction = "Y"
    nt.links.new(tc.outputs["Object"], wave1.inputs[0])
    nt.links.new(tc.outputs["Object"], wave2.inputs[0])
    add = nt.nodes.new("ShaderNodeMath")
    nt.links.new(wave1.outputs[1], add.inputs[0])
    nt.links.new(wave2.outputs[1], add.inputs[1])
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.08
    nt.links.new(add.outputs[0], bump.inputs["Height"])
    nt.links.new(bump.outputs[0], b.inputs["Normal"])
    if gold_pattern:
        # gold brocade motif
        mag = nt.nodes.new("ShaderNodeTexMagic")
        mag.inputs["Scale"].default_value = 60
        nt.links.new(tc.outputs["Object"], mag.inputs[0])
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        ramp.color_ramp.elements[0].position = 0.55
        ramp.color_ramp.elements[1].position = 0.6
        nt.links.new(mag.outputs["Fac"], ramp.inputs[0])
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.inputs[6].default_value = (*color, 1)
        mix.inputs[7].default_value = (*GOLD, 1)
        nt.links.new(ramp.outputs[0], mix.inputs["Factor"])
        nt.links.new(mix.outputs[2], b.inputs["Base Color"])
        nt.links.new(ramp.outputs[0], b.inputs["Metallic"])
    return m


def carved_material(name, base, gold_mask=True, bump_scale=30.0):
    m, nt, b = util.new_mat(name)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    wave = nt.nodes.new("ShaderNodeTexWave")
    wave.inputs["Scale"].default_value = bump_scale
    wave.inputs["Distortion"].default_value = 7.0
    wave.inputs["Detail"].default_value = 4.0
    wave.wave_profile = "SAW"
    nt.links.new(tc.outputs["Object"], wave.inputs[0])
    wood = nt.nodes.new("ShaderNodeTexNoise")
    wood.inputs["Scale"].default_value = 3.0
    wood.inputs["Detail"].default_value = 8.0
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (12, 12, 0.6)
    nt.links.new(tc.outputs["Object"], mp.inputs[0])
    nt.links.new(mp.outputs[0], wood.inputs[0])
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (base[0] * 0.55, base[1] * 0.5, base[2] * 0.45, 1)
    ramp.color_ramp.elements[1].color = (*base, 1)
    nt.links.new(wood.outputs["Fac"], ramp.inputs[0])
    col = ramp.outputs[0]
    if gold_mask:
        gm = nt.nodes.new("ShaderNodeValToRGB")
        gm.color_ramp.elements[0].position = 0.62
        gm.color_ramp.elements[1].position = 0.7
        nt.links.new(wave.outputs["Fac"], gm.inputs[0])
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        nt.links.new(gm.outputs[0], mix.inputs["Factor"])
        nt.links.new(col, mix.inputs[6])
        mix.inputs[7].default_value = (*GOLD, 1)
        col = mix.outputs[2]
        met = nt.nodes.new("ShaderNodeMath")
        met.operation = "MULTIPLY"
        met.inputs[1].default_value = 0.75
        nt.links.new(gm.outputs[0], met.inputs[0])
        nt.links.new(met.outputs[0], b.inputs["Metallic"])
    nt.links.new(col, b.inputs["Base Color"])
    util.setp(b, "Roughness", 0.42)
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.35
    nt.links.new(wave.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs[0], b.inputs["Normal"])
    return m


# ---------------------------------------------------------------- body sampling
class BodySample:
    """Rest-pose body geometry in armature space with per-vertex bone weights."""

    def __init__(self, body):
        saved = [(m, m.show_viewport, m.show_render) for m in body.modifiers]
        for m in body.modifiers:
            m.show_viewport = False
        dg = bpy.context.evaluated_depsgraph_get()
        dg.update()
        ev = body.evaluated_get(dg)
        me = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=dg)
        for m, v, r in saved:
            m.show_viewport = v
        mw = body.matrix_world
        self.co = [mw @ v.co for v in me.vertices]
        nm = mw.to_3x3().inverted().transposed()
        self.no = [(nm @ v.normal).normalized() for v in me.vertices]
        self.polys = [tuple(p.vertices) for p in me.polygons]
        names = {g.index: g.name for g in body.vertex_groups}
        self.w = [dict() for _ in me.vertices]
        src = body.data.vertices
        for i, v in enumerate(src):
            if i >= len(self.w):
                break
            for g in v.groups:
                self.w[i][names[g.group]] = g.weight
        self.body_mask = [("body" in d and d["body"] > 0.5) or ("body" not in names.values()) for d in self.w]
        bpy.data.meshes.remove(me)
        idx = [i for i in range(len(self.co)) if self.body_mask[i]]
        self.kd = KDTree(len(idx))
        for i in idx:
            self.kd.insert(self.co[i], i)
        self.kd.balance()
        polys = [p for p in self.polys if all(self.body_mask[i] for i in p)]
        self.bvh = BVHTree.FromPolygons(self.co, polys)

    def wsum(self, i, keys):
        d = self.w[i]
        return sum(v for k, v in d.items() if k.startswith(P) and any(s in k for s in keys))

    def nearest_weights(self, p):
        _, i, _ = self.kd.find(p)
        return {k: v for k, v in self.w[i].items() if k.startswith(P)}


def bone_groups(obj, weights_per_vert):
    names = sorted({k for d in weights_per_vert for k in d})
    groups = {n: obj.vertex_groups.new(name=n) for n in names}
    for vi, d in enumerate(weights_per_vert):
        for k, w in d.items():
            groups[k].add([vi], w, "REPLACE")


def rig_mesh(obj, rig, weights_per_vert):
    bone_groups(obj, weights_per_vert)
    obj.parent = rig
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = rig
    return obj


def cloth_from_body(bs, name, select, offset, mat, rig, coll, fold=0.006, sub=1, fold_scale=0.06):
    keep = [i for i in range(len(bs.co)) if bs.body_mask[i] and select(i)]
    remap = {old: new for new, old in enumerate(keep)}
    faces = [tuple(remap[i] for i in p) for p in bs.polys if all(i in remap for i in p)]
    verts = [bs.co[i] + bs.no[i] * offset(i) for i in keep]
    obj = util.mesh_object(name, verts, faces, coll)
    obj.data.materials.append(mat)
    weights = [{k: v for k, v in bs.w[i].items() if k.startswith(P)} for i in keep]
    if fold > 0:
        # folds are displaced in rest space (before the armature) so they don't swim during motion
        d = obj.modifiers.new("Folds", "DISPLACE")
        t = bpy.data.textures.new(name + "_folds", "CLOUDS")
        t.noise_scale = fold_scale
        t.noise_depth = 2
        d.texture = t
        d.strength = fold
        d.mid_level = 0.5
        d.texture_coords = "LOCAL"
    rig_mesh(obj, rig, weights)
    s = obj.modifiers.new("Subsurf", "SUBSURF")
    s.levels, s.render_levels = 0, sub
    so = obj.modifiers.new("Thickness", "SOLIDIFY")
    so.thickness = 0.003
    print("cloth", name, len(verts), "verts")
    return obj


def seg_dist_2d(px, pz, a, b):
    ax, az = a
    bx, bz = b
    dx, dz = bx - ax, bz - az
    t = max(0.0, min(1.0, ((px - ax) * dx + (pz - az) * dz) / (dx * dx + dz * dz)))
    cx, cz = ax + t * dx, az + t * dz
    side = (px - ax) * dz - (pz - az) * dx
    return math.hypot(px - cx, pz - cz), side


# ---------------------------------------------------------------- hair
def hair_material():
    m = bpy.data.materials.new("M_Hair")
    m.use_nodes = True
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    h = nt.nodes.new("ShaderNodeBsdfHairPrincipled")
    try:
        h.parametrization = "MELANIN"
    except Exception:  # noqa: BLE001
        pass
    for k, v in (("Melanin", 0.88), ("Melanin Redness", 0.45), ("Roughness", 0.3),
                 ("Radial Roughness", 0.35), ("Coat", 0.05), ("Random Roughness", 0.2), ("Random Color", 0.15)):
        if k in h.inputs:
            h.inputs[k].default_value = v
    nt.links.new(h.outputs[0], out.inputs[0])
    return m


def build_hair(bs, R, coll, rig, n_guides=360, n_children=26000, seed=11):
    """Guide strands grown over the scalp with gravity + collision, then clumped child strands."""
    import bisect
    rnd = random.Random(seed)
    head_c = R["head_center"]
    rad = R["head_radius"]
    scalp = set()
    for i in range(len(bs.co)):
        if not bs.body_mask[i] or bs.w[i].get(P + "Head", 0) < 0.8:
            continue
        v = bs.co[i] - head_c
        back = util.smoothstep(-0.05, 0.06, v.y)
        zth = 0.05 * (1 - back) + (-0.09) * back
        if abs(v.x) > 0.055 and abs(v.y) < 0.035 and -0.06 < v.z < 0.03:
            continue  # ears
        if v.z > zth:
            scalp.add(i)
    R["scalp"] = scalp
    tris = []
    for p in bs.polys:
        if all(i in scalp for i in p):
            for k in range(1, len(p) - 1):
                tris.append((p[0], p[k], p[k + 1]))
    areas = []
    acc = 0.0
    for a, b, c in tris:
        acc += ((bs.co[b] - bs.co[a]).cross(bs.co[c] - bs.co[a])).length / 2
        areas.append(acc)
    print("scalp verts", len(scalp), "tris", len(tris))

    def sample():
        t = tris[bisect.bisect_left(areas, rnd.uniform(0, acc))]
        u, v = rnd.random(), rnd.random()
        if u + v > 1:
            u, v = 1 - u, 1 - v
        w = 1 - u - v
        p = bs.co[t[0]] * w + bs.co[t[1]] * u + bs.co[t[2]] * v
        n = (bs.no[t[0]] * w + bs.no[t[1]] * u + bs.no[t[2]] * v).normalized()
        return p, n

    M = 30
    bun_c = head_c + Vector((0, 0.045, rad * 0.95))

    def resample(pts, m):
        d = [0.0]
        for a, b in zip(pts, pts[1:]):
            d.append(d[-1] + (b - a).length)
        out = []
        for k in range(m):
            s = d[-1] * k / (m - 1)
            j = max(1, min(len(d) - 1, bisect.bisect_left(d, s)))
            f = (s - d[j - 1]) / max(1e-9, d[j] - d[j - 1])
            out.append(pts[j - 1].lerp(pts[j], f))
        return out

    def grow(root, n):
        v = root - head_c
        crown = v.z > rad * 0.55 and abs(v.x) < 0.07 and -0.07 < v.y < 0.07
        if crown:
            # combed up along the scalp into the topknot
            a = (root - head_c).normalized()
            b = (bun_c - head_c).normalized()
            lift = 0.003 + rnd.random() * 0.004
            pts = [root]
            for k in range(1, 14):
                f = k / 13
                dirv = a.slerp(b, f) if a.dot(b) < 0.9999 else a
                q = head_c + dirv * (rad + lift)
                loc, nrm, _, dist = bs.bvh.find_nearest(q)
                if loc is not None and (q - loc).dot(nrm) < 0.004:
                    q = loc + nrm * (0.004 + lift)
                pts.append(q)
            pts.append(bun_c)
            return resample(pts, M), True
        side = math.copysign(1.0, v.x) if abs(v.x) > 0.006 else rnd.choice((-1, 1))
        d = Vector((side * 0.22, 0.85, -0.45))
        d = (d - n * d.dot(n)).normalized()
        lift = 0.004 + rnd.random() * 0.008
        L = rnd.uniform(0.40, 0.52)
        steps = 40
        step = L / steps
        p = root + n * lift
        pts = [root, p]
        for k in range(steps):
            d = (d + Vector((0, 0.03, -0.30))).normalized()
            q = p + d * step
            c = q - head_c
            if c.length < rad + lift:
                q = head_c + c.normalized() * (rad + lift)
            loc, nrm, _, dist = bs.bvh.find_nearest(q)
            if loc is not None and dist < 0.02 and (q - loc).dot(nrm) < 0.014:
                q = loc + nrm * 0.014
            d = (q - p).normalized()
            p = q
            pts.append(p)
        return resample(pts, M), False

    guides = []
    kd = KDTree(n_guides)
    for g in range(n_guides):
        root, n = sample()
        pts, crown = grow(root, n)
        phase = rnd.uniform(0, 6.28)
        amp = rnd.uniform(0.006, 0.014)
        guides.append((root, pts, crown, phase, amp))
        kd.insert(root, g)
    kd.balance()

    cu = bpy.data.curves.new("ShriRam_Hair", "CURVE")
    cu.dimensions = "3D"
    for c in range(n_children):
        root, n = sample()
        _, gi, _ = kd.find(root)
        groot, gpts, crown, phase, amp = guides[gi]
        off = root - groot
        jit = Vector((rnd.gauss(0, 1), rnd.gauss(0, 1), rnd.gauss(0, 1))) * 0.004
        pts = []
        for k in range(M):
            f = k / (M - 1)
            clump = 0.0 if crown else 0.8 * f ** 0.7
            p = gpts[k] + off * (1 - clump) + jit * f
            if not crown:
                side = Vector((1, 0, 0)) if abs(gpts[k].x) < 0.05 else Vector((0, 1, 0))
                p += side * amp * f * math.sin(f * 14 + phase)
            pts.append(p)
        if not crown:
            cut = int(M * rnd.uniform(0.82, 1.0))
            pts = pts[:max(cut, 6)]
        sp = cu.splines.new("POLY")
        sp.points.add(len(pts) - 1)
        co = []
        for q in pts:
            co += [q.x, q.y, q.z, 1.0]
        sp.points.foreach_set("co", co)
    hair = bpy.data.objects.new("ShriRam_Hair", cu)
    coll.objects.link(hair)
    mat = hair_material()
    hair.data.materials.append(mat)
    converted = False
    try:
        for o in bpy.context.view_layer.objects:
            o.select_set(False)
        bpy.context.view_layer.objects.active = hair
        hair.select_set(True)
        bpy.ops.object.convert(target="CURVES")
        hair = bpy.context.view_layer.objects.active
        cv = hair.data
        npts = len(cv.points)
        if "radius" not in cv.attributes:
            cv.attributes.new("radius", "FLOAT", "POINT")
        radii = []
        for crv in cv.curves:
            m = crv.points_length
            radii += [0.00022 - 0.00016 * (k / max(1, m - 1)) for k in range(m)]
        cv.attributes["radius"].data.foreach_set("value", radii[:npts])
        if not cv.materials:
            cv.materials.append(mat)
        converted = True
        print("hair converted to Curves:", len(cv.curves), "strands")
    except Exception as e:  # noqa: BLE001
        print("!! hair conversion failed, using bevelled curves", e)
    if not converted:
        hair.data.bevel_depth = 0.0004
        hair.data.bevel_resolution = 0
    parent_to_bone(hair, rig, P + "Head")
    # topknot bun + gold band
    mb = MB()
    v, f = uv_sphere(bun_c, 0.048, 18, 10, (1.0, 0.92, 0.8))
    mb.add(v, f, 0)
    band = ring_points(bun_c + Vector((0, 0, -0.022)), Vector((0, 0.25, 1)), 0.046, 24)
    tv, tf = util.tube_geometry(band + [band[0]], [0.0055] * 25, 6, closed_ends=False)
    mb.add(tv, tf, 1)
    bun_mat = util.simple_mat("M_HairBun", (0.03, 0.018, 0.012), 0.38, sheen=0.4)
    util.add_bump(bun_mat.node_tree, bun_mat.node_tree.nodes["Principled BSDF"], 220.0, 0.6, kind="wave")
    bun = mb.build("ShriRam_Topknot", [bun_mat, util.simple_mat("M_GoldBand", GOLD, 0.3, 1.0)], coll)
    parent_to_bone(bun, rig, P + "Head")
    return hair, bun


# ---------------------------------------------------------------- props
def bow_shape(k, n=41, L=0.80):
    pts, radii = [], []
    for j in range(n):
        z = -L + 2 * L * j / (n - 1)
        a = abs(z) / L
        y = -0.06 * a ** 1.8 - 0.20 * k * a ** 2
        if a > 0.82:
            y += 1.9 * (a - 0.82) ** 2
        pts.append(Vector((0, y, z * (1 - 0.05 * k))))
        radii.append(0.019 if a < 0.09 else 0.016 - 0.008 * a)
    return pts, radii


BRACE = 0.19
DRAW_LEN = 0.66


def string_shape(k):
    bp, _ = bow_shape(k)
    top, bot = bp[-2], bp[1]
    nock = Vector((0, -BRACE - k * (DRAW_LEN - BRACE), 0))
    pts = [top.lerp(nock, t / 6) for t in range(6)] + [nock.lerp(bot, t / 6) for t in range(1, 7)]
    return pts


def build_bow(coll):
    v0, f = util.tube_geometry(*bow_shape(0.0), sides=10, ref_up=Vector((1, 0, 0)))
    v1, _ = util.tube_geometry(*bow_shape(1.0), sides=10, ref_up=Vector((1, 0, 0)))
    bow = util.mesh_object("Kodanda_Bow", v0, f, coll)
    bow.data.materials.append(carved_material("M_BowCarved", WOOD))
    bow.shape_key_add(name="Basis")
    kb = bow.shape_key_add(name="Drawn")
    for i, v in enumerate(v1):
        kb.data[i].co = v
    # grip wrap
    gp = [Vector((0, -0.002, z)) for z in (-0.075, 0.075)]
    gv, gf = util.tube_geometry(gp, [0.0215, 0.0215], 12)
    grip = util.mesh_object("Bow_Grip", gv, gf, coll)
    grip.data.materials.append(util.simple_mat("M_GripLeather", (0.12, 0.05, 0.02), 0.6))
    grip.parent = bow
    s0 = string_shape(0.0)
    s1 = string_shape(1.0)
    sv0, sf = util.tube_geometry(s0, [0.0011] * len(s0), 5, ref_up=Vector((1, 0, 0)))
    sv1, _ = util.tube_geometry(s1, [0.0011] * len(s1), 5, ref_up=Vector((1, 0, 0)))
    st = util.mesh_object("Bow_String", sv0, sf, coll)
    st.data.materials.append(util.simple_mat("M_String", (0.75, 0.68, 0.55), 0.5))
    st.shape_key_add(name="Basis")
    kb2 = st.shape_key_add(name="Drawn")
    for i, v in enumerate(sv1):
        kb2.data[i].co = v
    st.parent = bow
    return bow, st


def arrow_mesh(name, coll, mats):
    mb = MB()
    shaft = [Vector((0, y, 0)) for y in (0.0, 0.4, 0.80)]
    v, f = util.tube_geometry(shaft, [0.0045] * 3, 6)
    mb.add(v, f, 0)
    head = [Vector((0, y, 0)) for y in (0.785, 0.80, 0.83, 0.865, 0.885)]
    v, f = util.tube_geometry(head, [0.006, 0.0105, 0.0095, 0.004, 0.0003], 4)
    mb.add(v, f, 1)
    for k in range(3):
        a = 2 * math.pi * k / 3 + 0.3
        d = Vector((math.cos(a), 0, math.sin(a)))
        quad = [Vector((0, 0.03, 0)) + d * 0.004, Vector((0, 0.16, 0)) + d * 0.004,
                Vector((0, 0.13, 0)) + d * 0.019, Vector((0, 0.035, 0)) + d * 0.017]
        mb.add(quad, [(0, 1, 2, 3)], 2)
    v, f = util.tube_geometry([Vector((0, -0.012, 0)), Vector((0, 0.004, 0))], [0.0052, 0.0052], 6)
    mb.add(v, f, 3)
    return mb.build(name, mats, coll)


def build_quiver(coll, rig, R):
    mb = MB()
    axis_pts = [Vector((0, 0, z)) for z in (0.0, 0.05, 0.3, 0.58, 0.62)]
    radii = [0.048, 0.052, 0.056, 0.06, 0.061]
    v, f = util.tube_geometry(axis_pts, radii, 16, ref_up=Vector((1, 0, 0)), closed_ends=False)
    mb.add(v, f + [tuple(range(16))[::-1]], 0)
    for z, r in ((0.04, 0.054), (0.6, 0.063)):
        ring = ring_points(Vector((0, 0, z)), Vector((0, 0, 1)), r, 20)
        tv, tf = util.tube_geometry(ring + [ring[0]], [0.006] * 21, 6, closed_ends=False)
        mb.add(tv, tf, 1)
    q = mb.build("Quiver", [carved_material("M_QuiverLeather", (0.42, 0.24, 0.1), True, 45.0),
                            util.simple_mat("M_QuiverGold", GOLD, 0.3, 1.0)], coll)
    # world placement behind the right shoulder (character faces -Y, right side is -X)
    base = R["quiver_base"]
    top = R["quiver_top"]
    axis = (top - base).normalized()
    q.matrix_world = Matrix.Translation(base) @ axis.to_track_quat("Z", "Y").to_matrix().to_4x4()
    mats = [util.simple_mat("M_ArrowShaft", (0.55, 0.42, 0.25), 0.55),
            util.simple_mat("M_ArrowHead", (0.62, 0.45, 0.25), 0.28, 1.0),
            util.simple_mat("M_Fletch", (0.93, 0.88, 0.78), 0.75, sheen=0.5),
            util.simple_mat("M_Nock", (0.1, 0.05, 0.02), 0.5)]
    proto = arrow_mesh("Arrow_Proto", coll, mats)
    proto.hide_render = True
    proto.hide_viewport = True
    arrows = []
    rnd = random.Random(3)
    for k in range(9):
        a = 2 * math.pi * k / 9
        r = 0.028 if k else 0.0
        o = bpy.data.objects.new(f"QuiverArrow_{k}", proto.data)
        coll.objects.link(o)
        o.parent = q
        # arrow +Y points down into the quiver; nock sticks out the top
        o.matrix_parent_inverse.identity()
        o.location = (r * math.cos(a), r * math.sin(a), 0.89 + rnd.uniform(-0.03, 0.02))
        # Rx(-90) turns the arrow's +Y into -Z: head down in the quiver, fletching out the top
        o.rotation_euler = (math.radians(-90) + rnd.uniform(-0.04, 0.04), 0.0, rnd.uniform(0, 6.28))
        arrows.append(o)
    parent_to_bone(q, rig, P + "Spine2")
    nocked = bpy.data.objects.new("Arrow_Nocked", proto.data)
    coll.objects.link(nocked)
    nocked.parent = rig
    nocked.rotation_mode = "QUATERNION"
    return q, arrows, nocked


# ---------------------------------------------------------------- rest data
def rest_data(rig, bs, eyes_obj):
    bones = rig.data.bones
    R = {"bones": {}}
    for b in bones:
        R["bones"][b.name] = {
            "head": b.head_local.copy(), "tail": b.tail_local.copy(),
            "q": b.matrix_local.to_quaternion(), "dir": (b.tail_local - b.head_local).normalized(),
            "len": b.length, "parent": b.parent.name if b.parent else None,
        }
    B = R["bones"]
    hb = B[P + "Head"]
    hv = [bs.co[i] for i in range(len(bs.co)) if bs.body_mask[i] and bs.w[i].get(P + "Head", 0) > 0.95]
    zmax = max(v.z for v in hv)
    c = sum(hv, Vector()) / len(hv)
    c.z = (c.z + zmax) / 2 - 0.01
    R["head_center"] = c
    R["head_radius"] = sum((v - c).length for v in hv if v.z > c.z) / max(1, len([v for v in hv if v.z > c.z]))
    R["head_top"] = zmax
    R["face_front_y"] = min(v.y for v in hv)
    if eyes_obj is not None and eyes_obj.type == "MESH":
        mw = eyes_obj.matrix_world
        ev = [mw @ v.co for v in eyes_obj.data.vertices]
        le = [v for v in ev if v.x > 0]
        re = [v for v in ev if v.x <= 0]
        R["eyeL"] = sum(le, Vector()) / len(le)
        R["eyeR"] = sum(re, Vector()) / len(re)
    else:
        R["eyeL"] = c + Vector((0.032, -0.075, -0.01))
        R["eyeR"] = c + Vector((-0.032, -0.075, -0.01))
    eye_mid = (R["eyeL"] + R["eyeR"]) / 2
    R["eye_mid"] = eye_mid
    R["chin"] = Vector((0, R["face_front_y"] + 0.025, eye_mid.z - 0.105))
    # hands: palm normals + finger directions
    for side, sgn in (("Left", 1), ("Right", -1)):
        h = B[P + side + "Hand"]
        d = (B[P + side + "HandMiddle1"]["head"] - h["head"]).normalized()
        r = (B[P + side + "HandIndex1"]["head"] - B[P + side + "HandPinky1"]["head"])
        r = (r - d * r.dot(d)).normalized()
        n = d.cross(r) if side == "Left" else r.cross(d)
        R[side + "_palm"] = (d, n.normalized())
    sh = B[P + "RightShoulder"]["head"]
    sp2 = B[P + "Spine2"]["head"]
    R["quiver_base"] = Vector((sh.x * 0.2 - 0.03, sp2.y + 0.17, sp2.z - 0.22))
    R["quiver_top"] = Vector((B[P + "RightArm"]["head"].x * 0.85, sp2.y + 0.2, B[P + "RightArm"]["head"].z + 0.36))
    feet = [bs.co[i] for i in range(len(bs.co)) if bs.body_mask[i]]
    R["ground_z"] = min(v.z for v in feet)
    return R


# ---------------------------------------------------------------- ornaments
def build_ornaments(bs, R, rig, coll):
    B = R["bones"]
    wood = util.simple_mat("M_Rudraksha", RUDRAKSHA, 0.55)
    util.add_bump(wood.node_tree, wood.node_tree.nodes["Principled BSDF"], 400.0, 0.4)
    gold = util.simple_mat("M_Gold", GOLD, 0.28, 1.0)
    leather = carved_material("M_Bracer", (0.45, 0.27, 0.12), True, 55.0)

    # rudraksha mala on the chest
    mb = MB()
    neck = B[P + "Neck"]["head"]
    pts = []
    for k in range(64):
        a = 2 * math.pi * k / 64
        depth = 0.5 - 0.5 * math.cos(a)          # 0 at back, 1 at front
        p = neck + Vector((0.085 * math.sin(a), 0.07 * math.cos(a) - 0.01, -0.03 - 0.17 * depth ** 2.2))
        loc, nrm, _, _ = bs.bvh.find_nearest(p)
        if loc is not None:
            p = loc + nrm * 0.0085
        pts.append(p)
    for p in pts:
        v, f = uv_sphere(p, 0.0075, 8, 5)
        mb.add(v, f, 0)
    mala = mb.build("Rudraksha_Mala", [wood], coll)
    parent_to_bone(mala, rig, P + "Spine2")

    out = [mala]
    for side in ("Left", "Right"):
        ub = B[P + side + "Arm"]
        fb = B[P + side + "ForeArm"]

        def radius_at(c, axis, group):
            ds = []
            for i in range(len(bs.co)):
                if not bs.body_mask[i] or bs.w[i].get(group, 0) < 0.5:
                    continue
                v = bs.co[i] - c
                if abs(v.dot(axis)) < 0.012:
                    ds.append((v - axis * v.dot(axis)).length)
            return (sum(ds) / len(ds)) if ds else 0.045

        # beaded armlet
        c = ub["head"] + ub["dir"] * ub["len"] * 0.58
        r = radius_at(c, ub["dir"], P + side + "Arm") + 0.007
        mb = MB()
        for k, p in enumerate(ring_points(c, ub["dir"], r, 18)):
            v, f = uv_sphere(p, 0.0078, 8, 5)
            mb.add(v, f, 0)
        ring = ring_points(c + ub["dir"] * 0.014, ub["dir"], r - 0.001, 20)
        tv, tf = util.tube_geometry(ring + [ring[0]], [0.0035] * 21, 6, closed_ends=False)
        mb.add(tv, tf, 1)
        arml = mb.build(f"Armlet_{side}", [wood, gold], coll)
        parent_to_bone(arml, rig, P + side + "Arm")
        out.append(arml)
        # carved bracer near the wrist
        p0 = fb["head"] + fb["dir"] * fb["len"] * 0.62
        p1 = fb["head"] + fb["dir"] * fb["len"] * 0.93
        r0 = radius_at(p0, fb["dir"], P + side + "ForeArm") + 0.009
        r1 = radius_at(p1, fb["dir"], P + side + "ForeArm") + 0.008
        tv, tf = util.tube_geometry([p0, (p0 + p1) / 2, p1], [r0, (r0 + r1) / 2 + 0.002, r1], 20, closed_ends=False)
        br = util.mesh_object(f"Bracer_{side}", tv, tf, coll)
        br.data.materials.append(leather)
        so = br.modifiers.new("Thick", "SOLIDIFY")
        so.thickness = 0.004
        parent_to_bone(br, rig, P + side + "ForeArm")
        out.append(br)
        # sandals
        foot_vs = [bs.co[i] for i in range(len(bs.co)) if bs.body_mask[i] and
                   (bs.w[i].get(P + side + "Foot", 0) + bs.w[i].get(P + side + "ToeBase", 0)) > 0.6]
        if foot_vs:
            minx, maxx = min(v.x for v in foot_vs), max(v.x for v in foot_vs)
            miny, maxy = min(v.y for v in foot_vs), max(v.y for v in foot_vs)
            gz = R["ground_z"]
            mb = MB()
            cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
            hx, hy = (maxx - minx) / 2 + 0.008, (maxy - miny) / 2 + 0.01
            outline = []
            for k in range(24):
                a = 2 * math.pi * k / 24
                outline.append(Vector((cx + hx * math.cos(a), cy + hy * math.sin(a), 0)))
            top = [p + Vector((0, 0, gz + 0.003)) for p in outline]
            bot = [p + Vector((0, 0, gz - 0.013)) for p in outline]
            verts = top + bot
            faces = [tuple(range(24)), tuple(range(24, 48))[::-1]]
            faces += [(k, 24 + k, 24 + (k + 1) % 24, (k + 1) % 24) for k in range(24)]
            mb.add(verts, faces, 0)
            # straps hug the real foot cross-section at three points along its length
            for frac, zmax in ((0.22, 0.09), (0.5, 0.14), (0.86, 0.16)):
                fy = miny + (maxy - miny) * frac
                sl = [v for v in foot_vs if abs(v.y - fy) < 0.012 and v.z < gz + zmax]
                if len(sl) < 4:
                    continue
                x0, x1 = min(v.x for v in sl), max(v.x for v in sl)
                z1 = max(v.z for v in sl)
                ecx, ecz = (x0 + x1) / 2, (gz + z1) / 2
                ax, az = (x1 - x0) / 2 + 0.006, (z1 - gz) / 2 + 0.006
                angs = [-0.25 + (math.pi + 0.5) * k / 15 for k in range(16)]   # arc over the top of the foot
                ring = [Vector((ecx + ax * math.cos(a), fy, max(gz - 0.004, ecz + az * math.sin(a)))) for a in angs]
                tv, tf = util.tube_geometry(ring, [0.0042] * len(ring), 5, closed_ends=True)
                mb.add(tv, tf, 1)
            sandal = mb.build(f"Sandal_{side}", [util.simple_mat("M_SandalSole", (0.2, 0.1, 0.04), 0.7),
                                                 util.simple_mat("M_SandalStrap", LEATHER, 0.5)], coll)
            parent_to_bone(sandal, rig, P + side + "Foot")
            out.append(sandal)
    return out


def build_tilak(bs, R, rig, coll):
    em = R["eye_mid"]
    white = util.simple_mat("M_TilakWhite", (0.92, 0.9, 0.85), 0.6)
    red = util.simple_mat("M_TilakRed", (0.78, 0.06, 0.02), 0.45, emit=((0.8, 0.1, 0.02), 0.15))
    mb = MB()

    def on_skin(x, z, lift):
        p = Vector((x, R["face_front_y"] - 0.02, z))
        loc, nrm, _, _ = bs.bvh.find_nearest(p)
        return loc + nrm * lift if loc is not None else p

    z0, z1 = em.z + 0.02, em.z + 0.068
    for x in (-0.0075, 0.0075):
        pts = [on_skin(x * (0.55 + 0.45 * t), z0 + (z1 - z0) * t, 0.0009) for t in [i / 10 for i in range(11)]]
        v, f = util.tube_geometry(pts, [0.0016] * len(pts), 5)
        mb.add(v, f, 0)
    pts = [on_skin(0.0, z0 + 0.004 + (z1 - z0 - 0.01) * t, 0.0013) for t in [i / 10 for i in range(11)]]
    v, f = util.tube_geometry(pts, [0.0019] * len(pts), 5)
    mb.add(v, f, 1)
    t = mb.build("Tilak", [white, red], coll)
    parent_to_bone(t, rig, P + "Head")
    return t


# ---------------------------------------------------------------- main
def build(manifest, coll_name="SHRI_RAM"):
    coll = bpy.data.collections.new(coll_name)
    bpy.context.scene.collection.children.link(coll)
    HS = mpfb_services()
    data = install_system_assets(manifest["mpfb_assets"])
    macro = {"gender": 1.0, "age": 0.52, "muscle": 0.92, "weight": 0.56, "proportions": 1.0,
             "height": 0.7, "cupsize": 0.5, "firmness": 0.5,
             "race": {"asian": 0.55, "caucasian": 0.35, "african": 0.10}}
    body = HS.create_human(macro_detail_dict=macro)
    body.name = "ShriRam_Body"
    rig = HS.add_builtin_rig(body, "mixamo")
    rig.name = "ShriRam_Rig"
    for pb in rig.pose.bones:
        pb.rotation_mode = "QUATERNION"
    eyes = None
    for sub, pref in (("eyes", ("high-poly", "low-poly")), ("eyebrows", ("eyebrow001",)),
                      ("eyelashes", ("eyelashes01", "eyelashes02"))):
        f = find_asset(data, sub, pref)
        if not f:
            print("!! no asset for", sub)
            continue
        try:
            mtype = "PROCEDURAL_EYES" if sub == "eyes" else "MAKESKIN"
            try:
                o = HS.add_mhclo_asset(f, body, asset_type=sub, subdiv_levels=0, material_type=mtype)
            except Exception as e:  # noqa: BLE001
                print("!! retry", sub, "with MAKESKIN:", e)
                o = HS.add_mhclo_asset(f, body, asset_type=sub, subdiv_levels=0, material_type="MAKESKIN")
            print("added", sub, os.path.basename(f))
            if sub == "eyes":
                eyes = o
        except Exception as e:  # noqa: BLE001
            print("!! failed", sub, e)
    for o in list(bpy.data.objects):
        if o == body or o == rig or o.parent in (body, rig):
            for c in list(o.users_collection):
                c.objects.unlink(o)
            coll.objects.link(o)

    body.data.materials.clear()
    body.data.materials.append(skin_material(data))
    if not any(m.type == "SUBSURF" for m in body.modifiers):
        s = body.modifiers.new("Subsurf", "SUBSURF")
        s.levels, s.render_levels = 0, 1
    bpy.context.view_layer.update()

    bs = BodySample(body)
    R = rest_data(rig, bs, eyes)
    B = R["bones"]
    waist_z = B[P + "Spine"]["head"].z + 0.03
    ankle_z = B[P + "LeftFoot"]["head"].z
    knee_z = B[P + "LeftLeg"]["head"].z
    lsh, rsh = B[P + "LeftArm"]["head"], B[P + "RightArm"]["head"]
    lhip, rhip = B[P + "LeftUpLeg"]["head"], B[P + "RightUpLeg"]["head"]
    arms = ("Arm", "ForeArm", "Hand")

    saffron = fabric_material("M_Dhoti", SAFFRON)
    uttariya = fabric_material("M_Uttariya", (0.95, 0.55, 0.05))
    border = fabric_material("M_Border", VERMILION, gold_pattern=True)
    sash = fabric_material("M_Kamarband", (0.55, 0.06, 0.02), gold_pattern=True)
    strap = carved_material("M_Strap", (0.3, 0.16, 0.07), True, 70.0)

    def dhoti_sel(i):
        z = bs.co[i].z
        return ankle_z + 0.03 < z < waist_z and bs.wsum(i, arms) < 0.15

    def dhoti_off(i):
        z = bs.co[i].z
        mid_calf = (knee_z + ankle_z) / 2
        return (0.025 + 0.045 * util.smoothstep(waist_z, knee_z + 0.1, z)
                + 0.03 * util.smoothstep(knee_z, mid_calf, z)
                - 0.055 * util.smoothstep(ankle_z + 0.13, ankle_z + 0.04, z))

    cloth_from_body(bs, "Dhoti", dhoti_sel, dhoti_off, saffron, rig, coll, fold=0.022, fold_scale=0.11)
    cloth_from_body(bs, "Kamarband", lambda i: waist_z - 0.07 < bs.co[i].z < waist_z + 0.035 and bs.wsum(i, arms) < 0.15,
                    lambda i: 0.055, sash, rig, coll, fold=0.008)

    p1 = (lsh.x - 0.02, lsh.z + 0.07)
    p2 = (rhip.x - 0.05, waist_z - 0.05)

    def torso(i):
        z = bs.co[i].z
        return waist_z - 0.03 < z < lsh.z + 0.14 and bs.wsum(i, ("Head", "Neck")) < 0.3 and bs.wsum(i, arms) < 0.45

    def utt_sel(i):
        d, _ = seg_dist_2d(bs.co[i].x, bs.co[i].z, p1, p2)
        return torso(i) and d < 0.085

    def border_sel(i):
        d, side = seg_dist_2d(bs.co[i].x, bs.co[i].z, p1, p2)
        return torso(i) and 0.06 < d < 0.1 and side < 0

    cloth_from_body(bs, "Uttariya", utt_sel, lambda i: 0.013, uttariya, rig, coll, fold=0.005)
    cloth_from_body(bs, "Uttariya_Border", border_sel, lambda i: 0.017, border, rig, coll, fold=0.005)
    s1 = (rsh.x + 0.03, rsh.z + 0.07)
    s2 = (lhip.x + 0.03, waist_z - 0.06)
    cloth_from_body(bs, "QuiverStrap", lambda i: torso(i) and seg_dist_2d(bs.co[i].x, bs.co[i].z, s1, s2)[0] < 0.024,
                    lambda i: 0.024, strap, rig, coll, fold=0.0)

    build_hair(bs, R, coll, rig)
    cap_mat = util.simple_mat("M_HairCap", (0.028, 0.017, 0.011), 0.42, sheen=0.3)
    util.add_bump(cap_mat.node_tree, cap_mat.node_tree.nodes["Principled BSDF"], 300.0, 0.5, kind="wave")
    cloth_from_body(bs, "HairCap", lambda i: i in R["scalp"], lambda i: 0.0025, cap_mat, rig, coll, fold=0.0)
    if eyes is not None and not procedural_eyes(eyes):
        eye_material(data, eyes)
    build_tilak(bs, R, rig, coll)
    build_ornaments(bs, R, rig, coll)
    bow, string = build_bow(coll)
    bow.parent = rig
    bow.rotation_mode = "QUATERNION"
    quiver, q_arrows, nocked = build_quiver(coll, rig, R)
    R["quiver_draw_arrow"] = q_arrows[1].name
    return {"rig": rig, "body": body, "bow": bow, "string": string, "quiver": quiver,
            "quiver_arrows": q_arrows, "nocked": nocked, "R": R, "coll": coll}
