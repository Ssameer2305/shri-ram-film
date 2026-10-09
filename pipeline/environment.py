"""Ancient Indian forest: terrain, giant buttress-root trees, Poly Haven vegetation, sun, sky, haze."""
import math
import random

import bpy
from mathutils import Vector, noise

import util
from util import terrain_h, trail_x

SUN_ELEV = math.radians(24)
SUN_AZIM = math.radians(18)   # rotation away from pure +Y

# Giant trunks; S10 hero framing trees are listed first.
HERO_Y = 16.0


def sun_vector():
    """Unit vector pointing TOWARDS the sun."""
    return Vector((math.sin(SUN_AZIM) * math.cos(SUN_ELEV),
                   math.cos(SUN_AZIM) * math.cos(SUN_ELEV),
                   math.sin(SUN_ELEV))).normalized()


def _coll(name, parent=None):
    c = bpy.data.collections.new(name)
    (parent or bpy.context.scene.collection).children.link(c)
    return c


# ---------------------------------------------------------------- library import
def import_library(manifest):
    lib_root = _coll("LIBRARY")
    libs = {}
    for mid, path in manifest["models"].items():
        c = _coll("LIB_" + mid, lib_root)
        lc = bpy.context.view_layer.layer_collection.children["LIBRARY"].children[c.name]
        bpy.context.view_layer.active_layer_collection = lc
        before = set(bpy.data.objects)
        try:
            bpy.ops.import_scene.gltf(filepath=path)
        except Exception as e:  # noqa: BLE001
            print("!! gltf import failed", mid, e)
            continue
        new = [o for o in bpy.data.objects if o not in before]
        for o in new:
            for uc in list(o.users_collection):
                if uc != c:
                    uc.objects.unlink(o)
            if c not in o.users_collection:
                c.objects.link(o)
        # bounding box (world) for scaling decisions
        zs, xs, ys = [], [], []
        for o in new:
            if o.type == "MESH":
                for corner in o.bound_box:
                    w = o.matrix_world @ Vector(corner)
                    xs.append(w.x)
                    ys.append(w.y)
                    zs.append(w.z)
        if zs:
            c["height"] = max(zs) - min(zs)
            c["radius"] = max(max(xs) - min(xs), max(ys) - min(ys)) / 2
        libs[mid] = c
        print("imported", mid, len(new), "objects", flush=True)
    bpy.context.view_layer.active_layer_collection = bpy.context.view_layer.layer_collection
    bpy.context.view_layer.layer_collection.children["LIBRARY"].exclude = True
    return libs


def instance(lib, loc, rot_z=0.0, scale=1.0, coll=None, tilt=0.0):
    e = bpy.data.objects.new(lib.name.replace("LIB_", "") + "_inst", None)
    e.instance_type = "COLLECTION"
    e.instance_collection = lib
    e.location = loc
    e.rotation_euler = (random.uniform(-tilt, tilt), random.uniform(-tilt, tilt), rot_z)
    e.scale = (scale, scale, scale)
    (coll or bpy.context.scene.collection).objects.link(e)
    return e


# ---------------------------------------------------------------- terrain
def build_ground(manifest, coll):
    size, n = 170.0, 220
    verts, faces, uvs = [], [], []
    for j in range(n + 1):
        for i in range(n + 1):
            x = -size / 2 + size * i / n
            y = -size / 2 + size * j / n
            verts.append((x, y, terrain_h(x, y)))
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            faces.append((a, a + 1, a + n + 2, a + n + 1))
            for (ii, jj) in ((i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)):
                uvs.append((ii * size / n / 2.5, jj * size / n / 2.5))
    g = util.mesh_object("ForestFloor", [Vector(v) for v in verts], faces, coll, uvs=uvs)
    tex = manifest["textures"].get("forest_floor") or manifest["textures"].get("leaves_forest_ground")
    if tex:
        g.data.materials.append(util.pbr_mat("M_ForestFloor", tex, 1.0, tint=(0.92, 0.88, 0.8)))
    else:
        g.data.materials.append(util.simple_mat("M_ForestFloor", (0.18, 0.13, 0.08), 0.9))
    return g


# ---------------------------------------------------------------- giant trees
def giant_tree(name, base, radius, height, coll, bark_mat, seed):
    rnd = random.Random(seed)
    rings, seg = 46, 28
    phase = rnd.uniform(0, 6.28)
    lobes = rnd.choice([4, 5, 6, 7])
    lean = Vector((rnd.uniform(-0.04, 0.04), rnd.uniform(-0.04, 0.04), 0))
    verts, faces, uvs_v = [], [], []
    zs = []
    for r in range(rings + 1):
        t = r / rings
        z = (t ** 1.6) * height - 0.8          # dense rings near the ground for buttresses
        zs.append(z)
        for s in range(seg):
            th = 2 * math.pi * s / seg
            flare = 1.0 + 0.9 * math.exp(-max(z, 0) / 1.1)
            butt = 1.0 + 0.55 * math.exp(-max(z, 0) / 1.6) * max(0.0, math.cos(lobes * th + phase)) ** 3
            taper = 1.0 - 0.5 * t
            n = noise.noise(Vector((math.cos(th) * 1.3, math.sin(th) * 1.3, z * 0.35 + seed)))
            rr = radius * flare * butt * taper * (1 + 0.12 * n)
            off = lean * z
            verts.append(Vector((base.x + off.x + rr * math.cos(th), base.y + off.y + rr * math.sin(th), base.z + z)))
    for r in range(rings):
        for s in range(seg):
            a = r * seg + s
            b = r * seg + (s + 1) % seg
            faces.append((a, b, b + seg, a + seg))
    uvs = []
    circ = 2 * math.pi * radius
    for r in range(rings):
        for s in range(seg):
            for (rr, ss) in ((r, s), (r, s + 1), (r + 1, s + 1), (r + 1, s)):
                uvs.append((ss / seg * circ / 2.0, zs[rr] / 2.0))
    obj = util.mesh_object(name, verts, faces, coll, uvs=uvs)
    obj.data.materials.append(bark_mat)
    sub = obj.modifiers.new("Sub", "SUBSURF")
    sub.levels, sub.render_levels = 0, 1
    disp = obj.modifiers.new("Bark", "DISPLACE")
    t = bpy.data.textures.get("BarkTex") or bpy.data.textures.new("BarkTex", "STUCCI")
    t.noise_scale = 0.35
    disp.texture = t
    disp.strength = 0.06 * radius
    disp.texture_coords = "GLOBAL"
    return obj


def hanging_vines(trees, coll, mat, count=40, seed=7):
    rnd = random.Random(seed)
    cu = bpy.data.curves.new("Vines", "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = 0.012
    cu.bevel_resolution = 1
    for _ in range(count):
        t = rnd.choice(trees)
        b = Vector(t["base"])
        top = b + Vector((rnd.uniform(-3, 3), rnd.uniform(-3, 3), rnd.uniform(12, 22)))
        length = rnd.uniform(6, 13)
        sp = cu.splines.new("POLY")
        npts = 14
        sp.points.add(npts - 1)
        for k in range(npts):
            f = k / (npts - 1)
            p = top + Vector((math.sin(f * 3 + _) * 0.3, math.cos(f * 2 + _) * 0.3, -length * f))
            sp.points[k].co = (p.x, p.y, p.z, 1)
            sp.points[k].radius = 1.0 - 0.6 * f
    o = bpy.data.objects.new("Vines", cu)
    coll.objects.link(o)
    o.data.materials.append(mat)
    return o


# ---------------------------------------------------------------- scattering
def scatter(libs, coll, rnd):
    def pick(*names):
        return [libs[n] for n in names if n in libs]

    big_trees = pick("island_tree_01", "island_tree_02", "island_tree_03")
    small_trees = pick("tree_small_02", "fir_sapling_medium")
    shrubs = pick("shrub_01", "shrub_02", "shrub_04", "pachira_aquatica_01")
    ferns = pick("fern_02", "calathea_orbifolia_01")
    ground = pick("grass_medium_02", "moss_01", "dry_branches_medium_01")
    rocks = pick("rock_moss_set_01", "boulder_01")
    dead = pick("dead_tree_trunk_02", "tree_stump_01")

    def place(lst, count, rmin, rmax, smin, smax, extent=70, ymin=-70, ymax=70, tilt=0.05):
        if not lst:
            return
        placed = 0
        tries = 0
        while placed < count and tries < count * 20:
            tries += 1
            y = rnd.uniform(ymin, ymax)
            x = trail_x(y) + rnd.choice((-1, 1)) * rnd.uniform(rmin, rmax)
            if abs(x) > extent:
                continue
            lib = rnd.choice(lst)
            s = rnd.uniform(smin, smax)
            instance(lib, (x, y, terrain_h(x, y) - 0.05), rnd.uniform(0, 6.28), s, coll, tilt)
            placed += 1

    place(big_trees, 70, 7, 60, 1.6, 2.8)
    place(small_trees, 50, 3.5, 40, 0.9, 1.6)
    place(dead, 14, 3, 30, 1.0, 1.6)
    place(rocks, 30, 2.2, 30, 0.5, 1.6, tilt=0.2)
    place(shrubs, 260, 1.6, 30, 0.7, 1.5)
    place(ferns, 420, 1.2, 25, 0.8, 1.8, tilt=0.15)
    place(ground, 420, 1.0, 20, 0.6, 1.4, tilt=0.1)


def roots_on_trail(libs, coll):
    """Roots crossing the trail for the ground-level step shot."""
    out = []
    for lib_name, y, s in (("single_root", -3.4, 1.0), ("root_cluster_01", 4.5, 0.8),
                           ("root_cluster_02", -9.0, 0.9), ("single_root", 9.8, 0.9)):
        if lib_name in libs:
            x = trail_x(y)
            out.append(instance(libs[lib_name], (x, y, terrain_h(x, y) - 0.02), math.radians(90), s, coll))
    return out


# ---------------------------------------------------------------- lighting + world
def build_world(manifest):
    w = bpy.data.worlds.new("ForestSky")
    bpy.context.scene.world = w
    w.use_nodes = True
    nt = w.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    env = nt.nodes.new("ShaderNodeTexEnvironment")
    env.image = bpy.data.images.load(manifest["hdri"])
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Rotation"].default_value = (0, 0, math.radians(200))
    nt.links.new(tc.outputs["Generated"], mp.inputs[0])
    nt.links.new(mp.outputs[0], env.inputs[0])
    nt.links.new(env.outputs[0], bg.inputs[0])
    bg.inputs["Strength"].default_value = 1.1
    nt.links.new(bg.outputs[0], out.inputs["Surface"])
    w.mist_settings.start = 4.0
    w.mist_settings.depth = 70.0
    w.mist_settings.falloff = "QUADRATIC"
    return w


def build_sun():
    sd = bpy.data.lights.new("Sun", "SUN")
    sd.energy = 11.0
    sd.angle = math.radians(0.6)
    sd.color = (1.0, 0.82, 0.62)
    s = bpy.data.objects.new("Sun", sd)
    bpy.context.scene.collection.objects.link(s)
    s.rotation_mode = "QUATERNION"
    s.rotation_quaternion = sun_vector().to_track_quat("Z", "Y")
    return s


def build_haze(coll, quality):
    """Low-density volume so the sun carves visible shafts through the canopy."""
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 5, 14))
    v = bpy.context.active_object
    v.name = "AtmosphereVolume"
    v.scale = (120, 140, 30)
    for c in list(v.users_collection):
        c.objects.unlink(v)
    coll.objects.link(v)
    m = bpy.data.materials.new("M_Haze")
    m.use_nodes = True
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    pv = nt.nodes.new("ShaderNodeVolumePrincipled")
    pv.inputs["Density"].default_value = 0.0032 if quality == "final" else 0.0028
    pv.inputs["Anisotropy"].default_value = 0.65
    pv.inputs["Color"].default_value = (1.0, 0.93, 0.82, 1)
    # density falls off with height, plus slow noise
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tc.outputs["Object"], sep.inputs[0])
    mr = nt.nodes.new("ShaderNodeMapRange")
    mr.inputs["From Min"].default_value = -0.5
    mr.inputs["From Max"].default_value = 0.3
    mr.inputs["To Min"].default_value = 1.6
    mr.inputs["To Max"].default_value = 0.2
    nt.links.new(sep.outputs["Z"], mr.inputs["Value"])
    nz = nt.nodes.new("ShaderNodeTexNoise")
    nz.inputs["Scale"].default_value = 6.0
    nt.links.new(tc.outputs["Object"], nz.inputs["Vector"])
    mul = nt.nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    nt.links.new(mr.outputs[0], mul.inputs[0])
    nt.links.new(nz.outputs["Fac"], mul.inputs[1])
    mul2 = nt.nodes.new("ShaderNodeMath")
    mul2.operation = "MULTIPLY"
    mul2.inputs[1].default_value = pv.inputs["Density"].default_value * 2.0
    nt.links.new(mul.outputs[0], mul2.inputs[0])
    nt.links.new(mul2.outputs[0], pv.inputs["Density"])
    nt.links.new(pv.outputs[0], out.inputs["Volume"])
    v.data.materials.append(m)
    v.visible_shadow = False
    return v


def build_dust(coll):
    """Floating pollen/dust motes catching the backlight."""
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(trail_x(10), 10, 2.0))
    em = bpy.context.active_object
    em.name = "DustEmitter"
    em.scale = (10, 34, 3.2)
    for c in list(em.users_collection):
        c.objects.unlink(em)
    coll.objects.link(em)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=1.0)
    mote = bpy.context.active_object
    mote.name = "DustMote"
    for c in list(mote.users_collection):
        c.objects.unlink(mote)
    coll.objects.link(mote)
    mote.location = (0, 0, -50)
    mote.data.materials.append(util.simple_mat("M_Dust", (1.0, 0.9, 0.7), 0.6, emit=((1.0, 0.85, 0.6), 0.6)))
    ps = em.modifiers.new("Dust", "PARTICLE_SYSTEM").particle_system
    st = ps.settings
    st.count = 2500
    st.frame_start = 1
    st.frame_end = 1
    st.lifetime = 5000
    st.emit_from = "VOLUME"
    st.physics_type = "NO"
    st.render_type = "OBJECT"
    st.instance_object = mote
    st.particle_size = 0.0018
    st.size_random = 0.7
    em.show_instancer_for_render = False
    return em


def build(manifest, quality):
    rnd = random.Random(1987)
    random.seed(1987)
    env = _coll("ENVIRONMENT")
    libs = import_library(manifest)
    build_ground(manifest, env)

    btex = manifest["textures"].get("chinese_hackberry_bark")
    bark = util.pbr_mat("M_GiantBark", btex, 1.0, tint=(0.85, 0.8, 0.75)) if btex else \
        util.simple_mat("M_GiantBark", (0.2, 0.15, 0.1), 0.9)
    giants = []
    hx = trail_x(HERO_Y)
    # (x, y, trunk radius). Footprint of a trunk incl. buttresses is ~3x its radius.
    specs = [
        # hero framing pair (behind him in S10; he faces +X)
        (hx - 7.0, HERO_Y + 3.2, 1.5), (hx - 6.2, HERO_Y - 4.0, 1.3),
    ]
    for y in range(-48, 46, 8):
        side = 1 if (y // 8) % 2 == 0 else -1
        r = rnd.uniform(0.9, 1.5)
        specs.append((trail_x(y) + side * (3 * r + rnd.uniform(2.2, 5.0)), y + rnd.uniform(-2, 2), r))
        if rnd.random() < 0.5:
            r = rnd.uniform(0.8, 1.3)
            specs.append((trail_x(y) - side * (3 * r + rnd.uniform(4.0, 9.0)), y + rnd.uniform(-2, 2), r))
    for i in range(16):
        y = rnd.uniform(-60, 60)
        specs.append((trail_x(y) + rnd.choice((-1, 1)) * rnd.uniform(16, 38), y, rnd.uniform(1.1, 1.8)))
    crowns = [libs[n] for n in ("island_tree_01", "island_tree_02", "island_tree_03") if n in libs]
    for i, (x, y, r) in enumerate(specs):
        if abs(x - trail_x(y)) < 3 * r + 2.0:
            x = trail_x(y) + math.copysign(3 * r + 2.0, x - trail_x(y) or 1)
        base = Vector((x, y, terrain_h(x, y)))
        height = rnd.uniform(26, 34)
        o = giant_tree(f"GiantTree_{i:02d}", base, r, height, env, bark, i)
        o["base"] = tuple(base)
        o["radius"] = r
        giants.append(o)
        # crown: a cluster of Poly Haven trees forming the canopy at the top of each trunk
        for k in range(3 if crowns else 0):
            lib = crowns[(i + k) % len(crowns)]
            s = 13.0 / max(lib.get("height", 10.0), 1.0)
            a = rnd.uniform(0, 6.28)
            off = Vector((math.cos(a), math.sin(a), 0)) * r * 0.8
            instance(lib, base + off + Vector((0, 0, height * 0.62)), rnd.uniform(0, 6.28), s, env, 0.15)

    vine_mat = util.simple_mat("M_Vine", (0.09, 0.11, 0.05), 0.8)
    hanging_vines(giants, env, vine_mat)
    scatter(libs, env, rnd)
    roots = roots_on_trail(libs, env)
    build_world(manifest)
    sun = build_sun()
    haze = build_haze(env, quality)
    dust = build_dust(env)
    return {"libs": libs, "giants": giants, "sun": sun, "haze": haze, "roots": roots, "env": env}
