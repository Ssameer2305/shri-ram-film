"""Shared helpers: terrain/trail functions, material builders, mesh builders, fast keyframing."""
import math

import bpy
from mathutils import Matrix, Quaternion, Vector, noise

FPS = 24


# ---------------------------------------------------------------- terrain + trail
def trail_x(y):
    return 2.5 * math.sin(0.05 * y) + 0.8 * math.sin(0.13 * y + 1.0)


def trail_tangent(y):
    dx = 2.5 * 0.05 * math.cos(0.05 * y) + 0.8 * 0.13 * math.cos(0.13 * y + 1.0)
    return Vector((dx, 1.0, 0.0)).normalized()


def smoothstep(a, b, x):
    t = max(0.0, min(1.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


def terrain_h(x, y):
    big = 1.6 * noise.noise(Vector((x * 0.025, y * 0.025, 0.3)))
    mid = 0.35 * noise.noise(Vector((x * 0.12, y * 0.12, 1.7)))
    fine = 0.05 * noise.noise(Vector((x * 0.9, y * 0.9, 4.1)))
    wild = big + mid + fine
    path = 1.6 * noise.noise(Vector((trail_x(y) * 0.025, y * 0.025, 0.3))) + fine * 0.6
    d = abs(x - trail_x(y))
    return path + (wild - path) * smoothstep(1.0, 6.0, d)


def ground_point(x, y):
    return Vector((x, y, terrain_h(x, y)))


def trail_point(y):
    x = trail_x(y)
    return Vector((x, y, terrain_h(x, y)))


def heading_for_facing(direction):
    """Armature rest faces -Y. Returns Z rotation so the character faces `direction` (XY)."""
    return math.atan2(direction.x, -direction.y)


def char_matrix(pos, psi):
    return Matrix.Translation(pos) @ Matrix.Rotation(psi, 4, 'Z')


# ---------------------------------------------------------------- materials
def new_mat(name):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs[0], out.inputs[0])
    return m, nt, bsdf


def setp(bsdf, name, value):
    if name in bsdf.inputs:
        bsdf.inputs[name].default_value = value


def simple_mat(name, color, rough=0.5, metal=0.0, sheen=0.0, spec=0.5, emit=None):
    m, nt, b = new_mat(name)
    setp(b, "Base Color", (*color, 1.0))
    setp(b, "Roughness", rough)
    setp(b, "Metallic", metal)
    setp(b, "Sheen Weight", sheen)
    setp(b, "Specular IOR Level", spec)
    if emit:
        setp(b, "Emission Color", (*emit[0], 1.0))
        setp(b, "Emission Strength", emit[1])
    return m


def add_bump(nt, bsdf, scale=200.0, strength=0.1, kind="noise", detail=6.0):
    tc = nt.nodes.new("ShaderNodeTexCoord")
    if kind == "wave":
        tex = nt.nodes.new("ShaderNodeTexWave")
        tex.inputs["Scale"].default_value = scale
        tex.inputs["Distortion"].default_value = 4.0
    else:
        tex = nt.nodes.new("ShaderNodeTexNoise")
        tex.inputs["Scale"].default_value = scale
        tex.inputs["Detail"].default_value = detail
    nt.links.new(tc.outputs["Object"], tex.inputs["Vector"])
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = strength
    nt.links.new(tex.outputs[0] if kind == "wave" else tex.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return tex


def pbr_mat(name, tex, scale=1.0, tint=None, disp_strength=0.0):
    """Material from a Poly Haven texture set dict {diff, nor, rough, disp}."""
    m, nt, b = new_mat(name)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (scale, scale, scale)
    nt.links.new(tc.outputs["UV"], mp.inputs["Vector"])

    def img(path, noncolor):
        n = nt.nodes.new("ShaderNodeTexImage")
        n.image = bpy.data.images.load(path, check_existing=True)
        if noncolor:
            n.image.colorspace_settings.name = "Non-Color"
        nt.links.new(mp.outputs[0], n.inputs[0])
        return n

    if "diff" in tex:
        d = img(tex["diff"], False)
        if tint:
            mix = nt.nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.blend_type = "MULTIPLY"
            mix.inputs["Factor"].default_value = 1.0
            mix.inputs[7].default_value = (*tint, 1.0)
            nt.links.new(d.outputs[0], mix.inputs[6])
            nt.links.new(mix.outputs[2], b.inputs["Base Color"])
        else:
            nt.links.new(d.outputs[0], b.inputs["Base Color"])
    if "rough" in tex:
        nt.links.new(img(tex["rough"], True).outputs[0], b.inputs["Roughness"])
    if "nor" in tex:
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nt.links.new(img(tex["nor"], True).outputs[0], nm.inputs["Color"])
        nt.links.new(nm.outputs[0], b.inputs["Normal"])
    if "disp" in tex and disp_strength > 0:
        dn = nt.nodes.new("ShaderNodeDisplacement")
        dn.inputs["Scale"].default_value = disp_strength
        nt.links.new(img(tex["disp"], True).outputs[0], dn.inputs["Height"])
        out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
        nt.links.new(dn.outputs[0], out.inputs["Displacement"])
    return m


# ---------------------------------------------------------------- mesh builders
def link(obj, coll=None):
    (coll or bpy.context.scene.collection).objects.link(obj)
    return obj


def mesh_object(name, verts, faces, coll=None, smooth=True, uvs=None):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], [], faces)
    if uvs is not None:
        uvl = me.uv_layers.new(name="UVMap")
        uvl.data.foreach_set("uv", [c for loop_uv in uvs for c in loop_uv])
    me.update()
    if smooth:
        me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    obj = bpy.data.objects.new(name, me)
    return link(obj, coll)


def tube_geometry(points, radii, sides=8, ref_up=Vector((0, 0, 1)), closed_ends=True):
    """Return verts/faces of a tube along `points` with per-point radii."""
    verts, faces = [], []
    n = len(points)
    prev_side = None
    for i, p in enumerate(points):
        if i == 0:
            t = (points[1] - points[0]).normalized()
        elif i == n - 1:
            t = (points[-1] - points[-2]).normalized()
        else:
            t = (points[i + 1] - points[i - 1]).normalized()
        if prev_side is None:
            up = ref_up if abs(t.dot(ref_up)) < 0.95 else Vector((1, 0, 0))
            side = t.cross(up).normalized()
        else:
            side = (prev_side - t * prev_side.dot(t)).normalized()
        prev_side = side
        up2 = side.cross(t).normalized()
        for k in range(sides):
            a = 2 * math.pi * k / sides
            verts.append(p + (side * math.cos(a) + up2 * math.sin(a)) * radii[i])
    for i in range(n - 1):
        for k in range(sides):
            a = i * sides + k
            b = i * sides + (k + 1) % sides
            faces.append((a, b, b + sides, a + sides))
    if closed_ends:
        faces.append(tuple(range(sides))[::-1])
        faces.append(tuple(range((n - 1) * sides, n * sides)))
    return verts, faces


def look_quat(direction, up=Vector((0, 0, 1))):
    """Quaternion pointing an object's -Z at `direction` with +Y towards `up` (cameras/lights)."""
    return direction.normalized().to_track_quat('-Z', 'Y')


def quat_from_axes(y_axis, z_axis):
    y = y_axis.normalized()
    x = y.cross(z_axis).normalized()
    z = x.cross(y).normalized()
    m = Matrix((x, y, z)).transposed()
    return m.to_quaternion()


# ---------------------------------------------------------------- fast keyframing
class KeyStore:
    """Collect (id, data_path, index) -> [(frame, value)] and write all f-curves in bulk."""

    def __init__(self):
        self.data = {}
        self.interp = {}

    def key(self, idb, path, values, frame, interp="LINEAR"):
        if not hasattr(values, "__len__"):
            values = (values,)
        for i, v in enumerate(values):
            k = (idb, path, i)
            self.data.setdefault(k, []).append((float(frame), float(v)))
            self.interp[k] = interp

    def key_quat(self, idb, path, q, frame, prev_cache):
        """Quaternion keys with hemisphere continuity."""
        ck = (idb, path)
        prev = prev_cache.get(ck)
        if prev is not None and prev.dot(q) < 0:
            q = -q
        prev_cache[ck] = q
        self.key(idb, path, (q.w, q.x, q.y, q.z), frame)

    def flush(self):
        enum = {"CONSTANT": 0, "LINEAR": 1, "BEZIER": 2}
        for (idb, path, idx), keys in self.data.items():
            ad = idb.animation_data or idb.animation_data_create()
            if ad.action is None:
                ad.action = bpy.data.actions.new(f"{idb.name}_act")
            act = ad.action
            fc = act.fcurves.find(path, index=idx) or act.fcurves.new(path, index=idx)
            keys.sort()
            # de-duplicate frames (last wins)
            dedup = {}
            for f, v in keys:
                dedup[f] = v
            frames = sorted(dedup)
            kp = fc.keyframe_points
            start = len(kp)
            kp.add(len(frames))
            co = []
            for f in frames:
                co += [f, dedup[f]]
            if start == 0:
                kp.foreach_set("co", co)
            else:
                for j, f in enumerate(frames):
                    kp[start + j].co = (f, dedup[f])
            kp.foreach_set("interpolation", [enum[self.interp[(idb, path, idx)]]] * len(kp))
            fc.update()
        self.data.clear()
