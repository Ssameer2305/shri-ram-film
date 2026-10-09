"""Choreography: pose solver (FK/IK on the mixamo rig), walk cycle, archery, ten directed shots,
camera moves, per-shot lighting and a flock of birds. Everything is keyed per frame."""
import math

import bpy
from mathutils import Matrix, Quaternion, Vector

import util
from character import BRACE, DRAW_LEN, P
from environment import HERO_Y, sun_vector
from util import FPS, char_matrix, heading_for_facing, terrain_h, trail_point, trail_tangent

TOTAL = 1440

# name, first frame, last frame
SHOTS = [
    ("S01_establish", 1, 144),
    ("S02_roots", 145, 264),
    ("S03_track", 265, 408),
    ("S04_grip", 409, 528),
    ("S05_birds", 529, 624),
    ("S06_alert", 625, 744),
    ("S07_eyes", 745, 840),
    ("S08_draw_arrow", 841, 1008),
    ("S09_orbit_draw", 1009, 1176),
    ("S10_hero", 1177, 1440),
]

WALK_V = 0.9
WALK_T = 1.2
DUTY = 0.6
STAND_Y = 11.0
ROOT_Y = -3.4


def ease(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def ramp(t, a, b):
    return ease((t - a) / (b - a)) if b > a else float(t >= a)


def Rx(a):
    return Quaternion((1, 0, 0), a)


def Ry(a):
    return Quaternion((0, 1, 0), a)


def Rz(a):
    return Quaternion((0, 0, 1), a)


def euler_q(pitch=0.0, yaw=0.0, roll=0.0):
    """pitch: nod forward(+), yaw: turn to character's left(+), roll: tilt."""
    return Rz(yaw) @ Rx(-pitch) @ Ry(roll)


def frame_quat(d, n):
    x = d.normalized()
    y = (n - x * n.dot(x)).normalized()
    z = x.cross(y)
    return Matrix((x, y, z)).transposed().to_quaternion()


def two_bone(S, T, l1, l2, pole):
    v = T - S
    d = max(abs(l1 - l2) + 1e-3, min(v.length, l1 + l2 - 1e-4))
    u = v.normalized()
    c = max(-1.0, min(1.0, (l1 * l1 + d * d - l2 * l2) / (2 * l1 * d)))
    a = math.acos(c)
    p = pole - u * pole.dot(u)
    if p.length < 1e-6:
        p = Vector((0, -1, 0)) - u * u.y
    p.normalize()
    E = S + (u * math.cos(a) + p * math.sin(a)) * l1
    Tr = S + u * d
    return (E - S).normalized(), (Tr - E).normalized()


class Pose:
    def __init__(self):
        self.hips_off = Vector((0, 0, -0.015))
        self.hips_rot = Quaternion()
        self.spine = [Quaternion()] * 5        # Spine, Spine1, Spine2, Neck, Head (relative)
        self.feet = {}                          # side -> (ankle Vector, toe_up radians)
        self.arms = {}                          # side -> ("ik", wrist, pole) | ("fk", upper, fore)
        self.hands = {}                         # side -> (dir, palm_normal) or None
        self.curl = {"Left": 0.35, "Right": 0.35}
        self.thumb = {"Left": 0.3, "Right": 0.3}


def clone(ps):
    c = Pose()
    c.hips_off = ps.hips_off.copy()
    c.hips_rot = ps.hips_rot.copy()
    c.spine = list(ps.spine)
    for k in ("feet", "arms", "hands", "curl", "thumb"):
        setattr(c, k, dict(getattr(ps, k)))
    return c


class Solver:
    def __init__(self, R):
        self.R = R
        self.B = R["bones"]
        self.order = []
        seen = set()

        def visit(n):
            if n in seen:
                return
            p = self.B[n]["parent"]
            if p:
                visit(p)
            seen.add(n)
            self.order.append(n)
        for n in self.B:
            visit(n)

    def H(self, n):
        return self.B[P + n]["head"]

    def jdir(self, a, b):
        return (self.H(b) - self.H(a)).normalized()

    def jlen(self, a, b):
        return (self.H(b) - self.H(a)).length

    def solve(self, ps):
        D, pos = {}, {}

        def rel(parent, child):
            return pos[parent] + D[P + parent] @ (self.H(child) - self.H(parent))

        D[P + "Hips"] = ps.hips_rot
        pos["Hips"] = self.H("Hips") + ps.hips_off
        prev = "Hips"
        for name, E in zip(("Spine", "Spine1", "Spine2", "Neck", "Head"), ps.spine):
            pos[name] = rel(prev, name)
            D[P + name] = D[P + prev] @ E
            prev = name
        Dh = D[P + "Hips"]
        for side in ("Left", "Right"):
            up, lo, ft, toe = side + "UpLeg", side + "Leg", side + "Foot", side + "ToeBase"
            pos[up] = rel("Hips", up)
            ankle, toe_up = ps.feet[side]
            knee_fwd = Dh @ Vector((0.08 if side == "Left" else -0.08, -1, 0))
            l1, l2 = self.jlen(up, lo), self.jlen(lo, ft)
            td, sd = two_bone(pos[up], ankle, l1, l2, knee_fwd)
            D[P + up] = (Dh @ self.jdir(up, lo)).rotation_difference(td) @ Dh
            D[P + lo] = (D[P + up] @ self.jdir(lo, ft)).rotation_difference(sd) @ D[P + up]
            pos[lo] = pos[up] + td * l1
            pos[ft] = pos[lo] + sd * l2
            yaw = Rz(Dh.to_euler().z * 0.5)
            D[P + ft] = yaw @ Rx(-toe_up)
            D[P + toe] = yaw @ Rx(-min(0.0, toe_up) * 0.0)
        Dc = D[P + "Spine2"]
        for side in ("Left", "Right"):
            sh, ar, fa, hd = side + "Shoulder", side + "Arm", side + "ForeArm", side + "Hand"
            pos[sh] = rel("Spine2", sh)
            D[P + sh] = Dc
            pos[ar] = rel(sh, ar)
            la, lf = self.jlen(ar, fa), self.jlen(fa, hd)
            spec = ps.arms[side]
            if spec[0] == "ik":
                ud, fd = two_bone(pos[ar], spec[1], la, lf, spec[2])
            else:
                ud, fd = (Dc @ spec[1]).normalized(), (Dc @ spec[2]).normalized()
            D[P + ar] = (D[P + sh] @ self.jdir(ar, fa)).rotation_difference(ud) @ D[P + sh]
            D[P + fa] = (D[P + ar] @ self.jdir(fa, hd)).rotation_difference(fd) @ D[P + ar]
            pos[fa] = pos[ar] + ud * la
            pos[hd] = pos[fa] + fd * lf
            d0, n0 = self.R[side + "_palm"]
            hf = ps.hands.get(side)
            if hf:
                D[P + hd] = frame_quat(*hf) @ frame_quat(d0, n0).inverted()
            else:
                D[P + hd] = D[P + fa]
            Dhd = D[P + hd]
            d, n = Dhd @ d0, Dhd @ n0
            pos[side + "_grip"] = pos[hd] + d * 0.075 + n * 0.025
            pos[side + "_frame"] = (d, n)
            curl = ps.curl[side]
            for fn, mult in (("Index", 0.9), ("Middle", 1.0), ("Ring", 1.05), ("Pinky", 1.1)):
                parent = hd
                for j, jm in ((1, 1.0), (2, 1.15), (3, 0.8)):
                    bn = f"{side}Hand{fn}{j}"
                    if P + bn not in self.B:
                        break
                    rd = self.B[P + bn]["dir"]
                    ax = Dhd @ rd.cross(n0)
                    if ax.length < 1e-6:
                        ax = Dhd @ Vector((1, 0, 0))
                    D[P + bn] = Quaternion(ax.normalized(), curl * mult * jm) @ D[P + parent]
                    parent = bn
            parent = hd
            for j in (1, 2, 3):
                bn = f"{side}HandThumb{j}"
                if P + bn not in self.B:
                    break
                rd = self.B[P + bn]["dir"]
                ax = Dhd @ rd.cross(n0)
                if ax.length < 1e-6:
                    ax = Dhd @ Vector((1, 0, 0))
                D[P + bn] = Quaternion(ax.normalized(), ps.thumb[side] * (0.3 if j == 1 else 0.6)) @ D[P + parent]
                parent = bn
        # head-attached points
        Dhead = D[P + "Head"]
        for key in ("eye_mid", "eyeL", "eyeR", "chin", "head_center"):
            pos[key] = pos["Head"] + Dhead @ (self.R[key] - self.H("Head"))
        for key in ("quiver_top", "quiver_base"):
            pos[key] = pos["Spine2"] + Dc @ (self.R[key] - self.H("Spine2"))
        # local rotations
        local = {}
        for n in self.order:
            q_r = self.B[n]["q"]
            Dc_ = D.get(n)
            par = self.B[n]["parent"]
            Dp = D.get(par, Quaternion()) if par else Quaternion()
            if Dc_ is None:
                Dc_ = Dp
                D[n] = Dc_
            local[n] = q_r.inverted() @ Dp.inverted() @ Dc_ @ q_r
        loc = self.B[P + "Hips"]["q"].to_matrix().inverted() @ ps.hips_off
        return local, loc, pos, D


# ---------------------------------------------------------------- choreography helpers
class Choreo:
    def __init__(self, R, solver):
        self.R = R
        self.s = solver
        H = solver.H
        self.ankle = {s: H(s + "Foot") for s in ("Left", "Right")}

    # ---- base standing pose
    def stand(self, t, M, stance=0.0):
        ps = Pose()
        br = math.sin(2 * math.pi * t / 4.4)
        ps.spine = [Quaternion(), euler_q(pitch=-0.012 * br), euler_q(pitch=-0.01 * br), Quaternion(), Quaternion()]
        for side, sx in (("Left", 1), ("Right", -1)):
            a = self.ankle[side].copy()
            a.x += sx * (0.025 + stance)
            ps.feet[side] = (self.ground_fix(a, M), 0.0)
        ps.hips_off = Vector((0, 0, -0.018 + 0.002 * br))
        return ps

    def ground_fix(self, a, M):
        w = M @ a
        dz = terrain_h(w.x, w.y) - M.translation.z
        return a + Vector((0, 0, dz))

    def carry_bow(self, ps, t, swing=0.0):
        """Left hand holds the bow upright at the side, elbow bent."""
        sh = self.s.H("LeftArm")
        wrist = sh + Vector((0.045, -0.2 - 0.02 * swing, -0.36))
        ps.arms["Left"] = ("ik", wrist, Vector((0.3, 0.6, -1)))
        ps.hands["Left"] = (Vector((0.05, -1, -0.22)), Vector((-1, 0, 0)))
        ps.curl["Left"] = 1.05
        ps.thumb["Left"] = 0.8

    def relaxed_right(self, ps, swing=0.0):
        th = math.radians(13) * swing
        up = Rx(-th) @ Vector((-0.16, 0.02, -1)).normalized()
        fo = Rx(-th - math.radians(18)) @ Vector((-0.1, 0.0, -1)).normalized()
        ps.arms["Right"] = ("fk", up, fo)
        ps.curl["Right"] = 0.4
        ps.thumb["Right"] = 0.3

    # ---- walk cycle (local frame, root moving at WALK_V)
    def walk(self, t, M_of, t_offset=0.0, step_over_y=None):
        M = M_of(t)
        ps = Pose()
        tt = t + t_offset
        S = WALK_V * WALK_T * DUTY
        ph = (tt / WALK_T) % 1.0
        for side, off, sx in (("Left", 0.0, 1), ("Right", 0.5, -1)):
            u = (ph + off) % 1.0
            a0 = self.ankle[side].copy()
            a0.x += sx * 0.012
            lift_extra = 0.0
            if u < DUTY:
                s = u / DUTY
                y = a0.y - S / 2 + S * s
                z = a0.z
                toe_up = math.radians(9) * (1 - ramp(s, 0.0, 0.18)) - math.radians(22) * ramp(s, 0.78, 1.0)
                z += 0.11 * math.sin(max(0.0, -toe_up))
            else:
                s = (u - DUTY) / (1 - DUTY)
                y = a0.y + S / 2 - S * ease(s)
                if step_over_y is not None:
                    # foot world-y at swing start/end, lift higher if a root lies between
                    t0 = tt - s * (1 - DUTY) * WALK_T - t_offset
                    t1 = t0 + (1 - DUTY) * WALK_T
                    y0 = (M_of(t0) @ Vector((0, a0.y + S / 2, 0))).y
                    y1 = (M_of(t1) @ Vector((0, a0.y - S / 2, 0))).y
                    if min(y0, y1) - 0.05 < step_over_y < max(y0, y1) + 0.05:
                        lift_extra = 0.11
                z = a0.z + (0.075 + lift_extra) * math.sin(math.pi * s) + 0.03 * (1 - s) * math.sin(math.pi * s)
                toe_up = -math.radians(22) * (1 - ramp(s, 0.0, 0.35)) + math.radians(12) * ramp(s, 0.6, 1.0)
            a = Vector((a0.x, y, z))
            ps.feet[side] = (self.ground_fix(a, M), toe_up)
        bob = -0.04 + 0.012 * math.cos(4 * math.pi * (ph - 0.3))
        sway = 0.018 * math.sin(2 * math.pi * ph)
        ps.hips_off = Vector((sway, 0.0, bob))
        ps.hips_rot = euler_q(yaw=math.radians(4) * math.sin(2 * math.pi * ph), roll=math.radians(2) * math.sin(2 * math.pi * ph))
        ps.spine = [euler_q(pitch=0.03), euler_q(yaw=-math.radians(3) * math.sin(2 * math.pi * ph)),
                    euler_q(yaw=-math.radians(2) * math.sin(2 * math.pi * ph)), Quaternion(),
                    euler_q(pitch=-0.03, yaw=math.radians(2) * math.sin(2 * math.pi * ph + 1))]
        swing = math.sin(2 * math.pi * ph)
        self.carry_bow(ps, t, swing)
        self.relaxed_right(ps, -swing)
        return ps

    # ---- archery building blocks (local frame; target direction is +X)
    def aim_axes(self):
        Yb = Vector((1, 0, -0.02)).normalized()
        Zb = Vector((0, -0.12, 1))
        Zb = (Zb - Yb * Zb.dot(Yb)).normalized()
        return Yb, Zb

    def low_axes(self):
        Yb = Vector((0.72, -0.5, -0.32)).normalized()
        Zb = Vector((-0.35, -0.1, 1))
        Zb = (Zb - Yb * Zb.dot(Yb)).normalized()
        return Yb, Zb

    def set_bow_hand(self, ps, G, Yb, Zb):
        d = Yb
        n = Yb.cross(Zb).normalized()
        wrist = G - d * 0.075 - n * 0.025
        ps.arms["Left"] = ("ik", wrist, Vector((0.1, 0.4, -1)))
        ps.hands["Left"] = (d, n)
        ps.curl["Left"] = 1.05
        ps.thumb["Left"] = 0.8

    def set_draw_hand(self, ps, N, Yb, Zb, curl=1.05):
        d = Yb
        n = Zb.cross(Yb).normalized()
        wrist = N - d * 0.06 - n * 0.022 + Zb * 0.012
        ps.arms["Right"] = ("ik", wrist, Vector((-1, 0.3, -0.2)))
        ps.hands["Right"] = (d, n)
        ps.curl["Right"] = curl
        ps.thumb["Right"] = 0.45


def bow_from_pos(pos):
    d, n = pos["Left_frame"]
    Yb = d.normalized()
    Zb = n.cross(d).normalized()
    return pos["Left_grip"], Yb, Zb


# ---------------------------------------------------------------- main builder
def build(char, env, scene):
    R = char["R"]
    rig, bow, string, nocked = char["rig"], char["bow"], char["string"], char["nocked"]
    q_arrow = bpy.data.objects[R["quiver_draw_arrow"]]
    solver = Solver(R)
    ch = Choreo(R, solver)
    ks = util.KeyStore()
    qcache = {}
    rig.rotation_mode = "QUATERNION"

    cam_data = bpy.data.cameras.new("FilmCamera")
    cam = bpy.data.objects.new("FilmCamera", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.rotation_mode = "QUATERNION"
    cam_data.sensor_width = 36.0
    cam_data.dof.use_dof = True
    cam_data.clip_start = 0.02
    cam_data.clip_end = 400

    key_l = bpy.data.lights.new("KeyLight", "AREA")
    key_l.size = 1.4
    key_l.color = (1.0, 0.88, 0.74)
    key = bpy.data.objects.new("KeyLight", key_l)
    rim_l = bpy.data.lights.new("RimLight", "AREA")
    rim_l.size = 1.0
    rim_l.color = (1.0, 0.8, 0.55)
    rim = bpy.data.objects.new("RimLight", rim_l)
    for o in (key, rim):
        scene.collection.objects.link(o)
        o.rotation_mode = "QUATERNION"

    birds = make_birds(scene, 9)

    def M_walk(y0):
        def M_of(t):
            y = y0 + WALK_V * t
            p = trail_point(y)
            return char_matrix(p, heading_for_facing(trail_tangent(y)))
        return M_of

    def M_stand(y):
        p = trail_point(y)
        return lambda t: char_matrix(p, heading_for_facing(trail_tangent(y)))

    def M_archer():
        p = trail_point(HERO_Y)
        tan = trail_tangent(HERO_Y)
        facing = Vector((tan.y, -tan.x, 0))   # shooting direction (+X local) follows the trail
        return lambda t: char_matrix(p, heading_for_facing(facing))

    sunv = sun_vector()

    # ---------------- per-shot evaluation: returns dict for one time value
    def evaluate(shot, t):
        """t = seconds since the start of the shot."""
        st = {"cam_lens": 35.0, "fstop": 4.0, "key": 0.0, "rim": 0.0,
              "nocked_vis": False, "qarrow_vis": True, "draw": 0.0, "arrow": None}
        name = shot
        if name == "S01_establish":
            M_of = M_walk(-15.0)
            M = M_of(t)
            ps = ch.walk(t, M_of)
            f = t / 6.0
            y = -44 + 6 * ease(f)
            cp = trail_point(y) + Vector((0.7, 0, 0.5 + 1.9 * ease(f)))
            tgt = (trail_point(-6) + Vector((0, 0, 3.0))).lerp(M.translation + Vector((0, 0, 1.4)), ease(f))
            st.update(cam=cp, tgt=tgt, cam_lens=28, fstop=8.0, focus=(M.translation - cp).length)
        elif name == "S02_roots":
            M_of = M_walk(-6.6)
            M = M_of(t)
            ps = ch.walk(t, M_of, step_over_y=ROOT_Y)
            yc = -4.5 + 1.1 * ease(t / 5.0)
            base = trail_point(yc)
            cp = base + Vector((0.8, 0, 0.13))
            cp.z = max(cp.z, terrain_h(cp.x, cp.y) + 0.11)
            tgt = trail_point(yc - 0.9) + Vector((-0.05, 0, 0.12))
            st.update(cam=cp, tgt=tgt, cam_lens=30, fstop=2.8, focus_pts=("LeftFoot", "RightFoot"))
        elif name == "S03_track":
            M_of = M_walk(-0.4)
            M = M_of(t)
            ps = ch.walk(t, M_of)
            back = 2.7 - 0.5 * ease(t / 6.0)
            cp = M @ Vector((0.5, back, 0.45))
            cp.z = max(cp.z, terrain_h(cp.x, cp.y) + 0.3)
            tgt = M @ Vector((0.0, -2.5, 1.9))
            st.update(cam=cp, tgt=tgt, cam_lens=24, fstop=4.0, focus_pts=("Head",))
        elif name in ("S04_grip", "S05_birds", "S06_alert", "S07_eyes"):
            M = M_stand(STAND_Y)(t)
            T0 = {"S04_grip": 0.0, "S05_birds": 5.0, "S06_alert": 9.0, "S07_eyes": 14.0}[name]
            tt = T0 + t
            ps = ch.stand(tt, M)
            ch.carry_bow(ps, tt)
            ch.relaxed_right(ps)
            ps.curl["Left"] = 0.6 + 0.5 * ramp(tt, 0.5, 2.6)
            ps.thumb["Left"] = 0.5 + 0.3 * ramp(tt, 0.5, 2.6)
            # look up at the birds, then hear something to the right and turn
            look_up = ramp(tt, 5.2, 6.4) * (1 - ramp(tt, 8.4, 9.6))
            turn = ramp(tt, 9.8, 11.4)
            head_yaw = math.radians(28) * look_up - math.radians(52) * turn
            head_pitch = -math.radians(24) * look_up + math.radians(2) * turn
            ps.spine[2] = euler_q(yaw=-math.radians(8) * turn)
            ps.spine[3] = euler_q(yaw=head_yaw * 0.3, pitch=head_pitch * 0.3)
            ps.spine[4] = euler_q(yaw=head_yaw * 0.7, pitch=head_pitch * 0.7)
            if name == "S04_grip":
                st.update(cam_lens=85, fstop=2.0, focus_pts=("Left_grip",), key=60.0,
                          cam_rel=("Left_grip", Vector((0.42, -0.36, 0.06)), Vector((0.36, -0.30, 0.03)), t / 5.0),
                          key_rel=Vector((1.2, -1.3, 1.7)))
            elif name == "S05_birds":
                head = M @ Vector((-0.35, 0.4, 1.72))
                perch = M @ Vector((-3.0, -7.0, 14.0))     # hidden in the canopy; they burst out
                fly = bird_center(perch, sunv, (tt - 5.8))
                tgt = perch.lerp(fly, ramp(t, 0.6, 3.8))
                st.update(cam=head, tgt=tgt, cam_lens=32, fstop=5.6, focus=(tgt - head).length,
                          birds=(perch, tt - 5.8))
            elif name == "S06_alert":
                d = 2.7 - 0.6 * ease(t / 5.0)
                st.update(cam_lens=50, fstop=2.8, focus_pts=("Head",), key=120.0,
                          cam_rel=("Hips", Vector((-d, -1.1, 0.55)), Vector((-d, -1.1, 0.55)), 0.0),
                          look_at="eye_mid", key_rel=Vector((-1.6, -1.8, 2.1)))
            else:
                st.update(cam_lens=105, fstop=2.8, focus_pts=("eye_mid",), key=70.0, rim=80.0,
                          face_cam=(0.46 - 0.06 * ease(t / 4.0)), key_rel=Vector((-1.0, -1.5, 1.9)))
        elif name == "S08_draw_arrow":
            M = M_archer()(t)
            ps = ch.stand(t, M, stance=0.13)
            Yl, Zl = ch.low_axes()
            sh = solver.H("LeftArm")
            G = sh + Vector((0.12, -0.36, -0.36))
            ch.set_bow_hand(ps, G, Yl, Zl)
            yaw = math.radians(70) * ramp(t, 5.0, 6.5)
            ps.spine[3] = euler_q(yaw=yaw * 0.3, pitch=0.05)
            ps.spine[4] = euler_q(yaw=yaw * 0.7, pitch=0.1 - 0.1 * ramp(t, 5.0, 6.5))
            # solve once for body landmarks (quiver top) before placing the right hand
            pre = clone(ps)
            ch.relaxed_right(pre)
            _, _, pos0, D0 = solver.solve(pre)
            qtop, qbase = pos0["quiver_top"], pos0["quiver_base"]
            qaxis = (qtop - qbase).normalized()
            grip_reach = qtop + Vector((0, 0, 0.03))
            Gl, Yb, Zb = bow_from_pos(pos0)
            N0 = Gl - Yb * BRACE + Zb * 0.035
            out_pt = qtop + qaxis * 0.72
            over = solver.H("RightArm") + Vector((0.25, -0.42, 0.22))
            arrow_q_pull = (-qaxis).to_track_quat("Y", "Z")
            arrow_q_nock = util.quat_from_axes(Yb, Zb)
            st["qarrow_vis"] = t < 2.3
            if t < 0.5:
                ch.relaxed_right(ps)
            else:
                # hand path as a target for the grip point
                if t < 2.3:
                    k = ramp(t, 0.5, 2.1)
                    rest_grip = solver.H("RightHand") + Vector((0, -0.05, -0.05))
                    gp = rest_grip.lerp(grip_reach, k) + Vector((0, 0.08, 0.12)) * math.sin(math.pi * k)
                    curl = 0.4 + 0.7 * ramp(t, 2.0, 2.3)
                    arrow = None
                elif t < 3.9:
                    k = ramp(t, 2.3, 3.9)
                    gp = grip_reach.lerp(out_pt, k)
                    curl = 1.1
                    arrow = ("hand", arrow_q_pull)
                elif t < 5.2:
                    k = ease((t - 3.9) / 1.3)
                    a = out_pt.lerp(over, k)
                    b = over.lerp(N0, k)
                    gp = a.lerp(b, k)
                    curl = 1.1
                    arrow = ("hand", arrow_q_pull.slerp(arrow_q_nock, k))
                else:
                    gp = N0
                    curl = 1.05
                    arrow = None
                    st["draw"] = 0.0
                    st["nocked_vis"] = True
                d_r = Yb if t >= 3.9 else -qaxis
                n_r = Zb.cross(Yb).normalized() if t >= 3.9 else Vector((-1, 0.2, 0)).normalized()
                n_r = (n_r - d_r * n_r.dot(d_r)).normalized()
                wrist = gp - d_r * 0.06 - n_r * 0.022
                ps.arms["Right"] = ("ik", wrist, Vector((-1, 0.4, -0.6)))
                ps.hands["Right"] = (d_r, n_r) if t >= 3.0 else None
                ps.curl["Right"] = curl
                ps.thumb["Right"] = 0.5
                if arrow is not None:
                    st["arrow"] = arrow
                    st["nocked_vis"] = True
            # behind his left shoulder: sees the right hand go to the quiver and bring the arrow forward
            cp = M @ Vector((0.62 - 0.1 * ease(t / 7.0), 1.35, 1.78))
            tgt = M @ Vector((-0.05, -0.45, 1.4))
            st.update(cam=cp, tgt=tgt, cam_lens=30, fstop=4.0, focus_pts=("Right_grip",), key=100.0,
                      key_rel=Vector((0.6, -1.9, 2.0)))
        elif name in ("S09_orbit_draw", "S10_hero"):
            M = M_archer()(t)
            tt = t if name == "S09_orbit_draw" else 7.0 + t
            ps = ch.stand(tt, M, stance=0.13)
            # body landmarks for the anchor (head turned to the target)
            yaw = math.radians(70) + math.radians(8) * ramp(tt, 0.0, 1.2)
            relax = ramp(tt, 7.0 + 4.6, 7.0 + 6.8)            # S10: let down the draw
            settle = ramp(tt, 7.0 + 6.6, 7.0 + 9.0)            # S10: stand tall, look out
            yaw = yaw * (1 - 0.45 * settle)
            ps.spine[1] = euler_q(pitch=-0.03 * settle)
            ps.spine[2] = euler_q(pitch=-0.02 - 0.03 * settle)
            ps.spine[3] = euler_q(yaw=yaw * 0.3)
            ps.spine[4] = euler_q(yaw=yaw * 0.7, pitch=-0.04 + 0.08 * settle)
            pre = clone(ps)
            ch.relaxed_right(pre)
            ch.set_bow_hand(pre, solver.H("LeftArm") + Vector((0.12, -0.36, -0.36)), *ch.low_axes())
            _, _, pos0, _ = solver.solve(pre)
            anchor = pos0["chin"] + Vector((0.0, -0.045, -0.035))
            Ya, Za = ch.aim_axes()
            G_aim = anchor + Ya * DRAW_LEN
            G_low = solver.H("LeftArm") + Vector((0.12, -0.36, -0.36))
            Yl, Zl = ch.low_axes()
            raise_k = ramp(tt, 0.2, 1.4) * (1 - ramp(tt, 7.0 + 6.4, 7.0 + 8.6))
            G = G_low.lerp(G_aim, raise_k)
            Yb = Yl.slerp(Ya, raise_k) if hasattr(Yl, "slerp") else (Yl.lerp(Ya, raise_k)).normalized()
            Yb = Yb.normalized()
            Zb = Zl.lerp(Za, raise_k)
            Zb = (Zb - Yb * Zb.dot(Yb)).normalized()
            draw = ramp(tt, 1.5, 3.4) * (1 - relax)
            ch.set_bow_hand(ps, G, Yb, Zb)
            N = G - Yb * (BRACE + draw * (DRAW_LEN - BRACE)) + Zb * 0.035
            tremble = 0.0015 * math.sin(tt * 23.0) * draw
            ch.set_draw_hand(ps, N + Vector((0, 0, tremble)), Yb, Zb)
            st.update(draw=draw, nocked_vis=True, qarrow_vis=False)
            if name == "S09_orbit_draw":
                a0, a1 = math.radians(-12), math.radians(-128)
                a = a0 + (a1 - a0) * ease(t / 7.0)
                rr = 2.8
                cp = M @ Vector((rr * math.sin(a), -rr * math.cos(a), 0.42))
                cp.z = max(cp.z, terrain_h(cp.x, cp.y) + 0.3)
                tgt = M @ Vector((0.2, 0.0, 1.5))
                st.update(cam=cp, tgt=tgt, cam_lens=30, fstop=4.0, focus_pts=("Head",), key=140.0, rim=110.0,
                          key_rel=Vector((0.2, -2.3, 1.9)))
            else:
                f = ease(t / 11.0)
                cp = M @ Vector((0.7 + 1.6 * f, -3.0 - 10.5 * f, 0.9 + 4.2 * f))
                cp.z = max(cp.z, terrain_h(cp.x, cp.y) + 0.4)
                tgt = M @ Vector((0.0, 0.0, 1.45 + 0.4 * f))
                st.update(cam=cp, tgt=tgt, cam_lens=30 - 6 * f, fstop=5.6, focus_pts=("Head",), key=160.0,
                          rim=120.0, key_rel=Vector((0.8, -2.6, 2.3)))
        else:
            raise ValueError(name)

        local, loc, pos, D = solver.solve(ps)
        st.update(M=M, local=local, loc=loc, pos=pos)
        world = {k: (M @ v if isinstance(v, Vector) else v) for k, v in pos.items()}
        # camera helpers relative to body parts
        if "cam_rel" in st:
            part, o0, o1, k = st["cam_rel"]
            off = o0.lerp(o1, ease(k))
            anchor_w = world[part] if part in world else M @ pos[part]
            Mrot = M.to_3x3()
            st["cam"] = anchor_w + Mrot @ off
            st["tgt"] = world[st.get("look_at", part)]
        if "face_cam" in st:
            Dhead = D[P + "Head"]
            fwd = (M.to_3x3() @ (Dhead @ Vector((0, -1, 0)))).normalized()
            up = (M.to_3x3() @ (Dhead @ Vector((0, 0, 1)))).normalized()
            em = world["eye_mid"]
            st["cam"] = em + fwd * st["face_cam"] + up * 0.01 + (M.to_3x3() @ Vector((0.02, 0, 0)))
            st["tgt"] = em
        if "focus_pts" in st:
            pts = [world[p] for p in st["focus_pts"]]
            fp = sum(pts, Vector()) / len(pts)
            st["focus"] = (fp - st["cam"]).length
        if "key_rel" in st:
            st["key_pos"] = M @ st["key_rel"]
            st["key_tgt"] = M @ Vector((0, 0, 1.5))
            st["rim_pos"] = M @ Vector((-st["key_rel"].x * 0.6, 2.2, 2.3))
        # bow placement from the actual (solved) bow hand
        G, Yb, Zb = bow_from_pos(pos)
        st["bow"] = (G, util.quat_from_axes(Yb, Zb))
        if st["arrow"] is not None and st["arrow"][0] == "hand":
            st["arrow"] = (pos["Right_grip"], st["arrow"][1])
        if st["nocked_vis"] and st["arrow"] is None:
            Nk = G - Yb * (BRACE + st["draw"] * (DRAW_LEN - BRACE)) + Zb * 0.035
            st["arrow"] = (Nk, util.quat_from_axes(Yb, Zb))
        return st

    # ---------------- key everything
    for (name, f0, f1) in SHOTS:
        scene.timeline_markers.new(name, frame=f0)
        times = [(f, (f - f0) / FPS) for f in range(f0, f1 + 1)]
        times.insert(0, (f0 - 0.5, -0.5 / FPS))        # motion-blur sample before the cut
        for (f, t) in times:
            st = evaluate(name, t)
            M = st["M"]
            ks.key(rig, "location", M.translation, f)
            ks.key_quat(rig, "rotation_quaternion", M.to_quaternion(), f, qcache)
            for bn, q in st["local"].items():
                ks.key_quat(rig, f'pose.bones["{bn}"].rotation_quaternion', q, f, qcache)
            ks.key(rig, f'pose.bones["{P}Hips"].location', st["loc"], f)
            G, qb = st["bow"]
            ks.key(bow, "location", G, f)
            ks.key_quat(bow, "rotation_quaternion", qb, f, qcache)
            for ob in (bow, string):
                ks.key(ob.data.shape_keys, 'key_blocks["Drawn"].value', st["draw"], f)
            if st["arrow"] is not None:
                ks.key(nocked, "location", st["arrow"][0], f)
                ks.key_quat(nocked, "rotation_quaternion", st["arrow"][1], f, qcache)
            ks.key(nocked, "hide_render", (not st["nocked_vis"]), f, "CONSTANT")
            ks.key(q_arrow, "hide_render", (not st["qarrow_vis"]), f, "CONSTANT")
            cp, tg = st["cam"], st["tgt"]
            ks.key(cam, "location", cp, f)
            ks.key_quat(cam, "rotation_quaternion", util.look_quat(tg - cp), f, qcache)
            ks.key(cam_data, "lens", st["cam_lens"], f)
            ks.key(cam_data, "dof.focus_distance", max(0.1, st["focus"]), f)
            ks.key(cam_data, "dof.aperture_fstop", st["fstop"], f)
            ks.key(key_l, "energy", st["key"], f)
            ks.key(rim_l, "energy", st["rim"], f)
            if "key_pos" in st:
                ks.key(key, "location", st["key_pos"], f)
                ks.key_quat(key, "rotation_quaternion", util.look_quat(st["key_tgt"] - st["key_pos"]), f, qcache)
                ks.key(rim, "location", st["rim_pos"], f)
                ks.key_quat(rim, "rotation_quaternion", util.look_quat(st["key_tgt"] - st["rim_pos"]), f, qcache)
            if "birds" in st:
                key_birds(ks, birds, st["birds"][0], st["birds"][1], sunv, f, qcache)
        print("keyed", name, flush=True)
    # birds only exist on screen during S05
    s05 = next(s for s in SHOTS if s[0] == "S05_birds")
    for b in birds:
        parts = [b] + [bpy.data.objects[b[f"wing{s}"]] for s in (1, -1)]
        for o in parts:
            ks.key(o, "hide_render", 1.0, 1, "CONSTANT")
            ks.key(o, "hide_render", 0.0, s05[1] - 0.5, "CONSTANT")
            ks.key(o, "hide_render", 1.0, s05[2] + 0.5, "CONSTANT")
    ks.flush()
    return {"camera": cam, "key": key, "rim": rim, "birds": birds}


# ---------------------------------------------------------------- birds
def make_birds(scene, n):
    import character
    mat = util.simple_mat("M_Bird", (0.04, 0.035, 0.03), 0.6)
    birds = []
    for i in range(n):
        mb = character.MB()
        v, f = character.uv_sphere(Vector((0, 0, 0)), 0.06, 10, 6, (0.55, 1.0, 0.5))
        mb.add(v, f, 0)
        body = mb.build(f"Bird_{i}", [mat], scene.collection)
        body.rotation_mode = "QUATERNION"
        body.location = (0, 0, -100)
        for side in (1, -1):
            wv = [Vector((0, 0.03, 0)), Vector((side * 0.17, 0.0, 0)), Vector((side * 0.15, -0.05, 0)), Vector((0, -0.04, 0))]
            w = util.mesh_object(f"Bird_{i}_wing{side}", wv, [(0, 1, 2, 3)], scene.collection, smooth=False)
            w.data.materials.append(mat)
            w.parent = body
            body[f"wing{side}"] = w.name
        birds.append(body)
    return birds


def bird_center(perch, sunv, t):
    if t <= 0:
        return perch.copy()
    d = (Vector((sunv.x, sunv.y, 0.0)).normalized() * 3.2 + Vector((0.8, 0, 1.7))) * t
    return perch + d + Vector((0, 0, 0.15 * t * t))


def key_birds(ks, birds, perch, t, sunv, f, qcache):
    for i, b in enumerate(birds):
        delay = i * 0.07
        tt = t - delay
        off = Vector(((i % 3) * 0.35 - 0.35, (i // 3) * 0.3, (i % 2) * 0.1))
        p = bird_center(perch, sunv, tt) + off * (1 + max(0.0, tt) * 0.6)
        pn = bird_center(perch, sunv, tt + 0.05) + off * (1 + max(0.0, tt + 0.05) * 0.6)
        vel = pn - p
        q = vel.normalized().to_track_quat("Y", "Z") if vel.length > 1e-5 else Quaternion()
        ks.key(b, "location", p, f)
        ks.key_quat(b, "rotation_quaternion", q, f, qcache)
        flap = math.radians(55) * math.sin(2 * math.pi * 9.0 * max(0.0, tt) + i) if tt > 0 else 0.0
        for side in (1, -1):
            w = bpy.data.objects[b[f"wing{side}"]]
            ks.key(w, "rotation_euler", (0.0, -side * flap, 0.0), f)
