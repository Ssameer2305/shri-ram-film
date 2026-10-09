"""blender -b project.blend --python pipeline/render_shard.py -- --shard I --shards N --out DIR
   blender -b project.blend --python pipeline/render_shard.py -- --stills --out DIR"""
import argparse
import json
import os
import sys
import time

import bpy


def debug_views(sc, out):
    """Character check renders from fixed angles with a soft fill light (not part of the film)."""
    from mathutils import Vector
    rig = bpy.data.objects.get("ShriRam_Rig")
    if rig is None:
        return
    film_cam = sc.camera
    cd = bpy.data.cameras.new("DebugCam")
    cam = bpy.data.objects.new("DebugCam", cd)
    sc.collection.objects.link(cam)
    cd.dof.use_dof = False
    ld = bpy.data.lights.new("DebugFill", "AREA")
    ld.size = 3.0
    ld.energy = 400
    lamp = bpy.data.objects.new("DebugFill", ld)
    sc.collection.objects.link(lamp)
    sc.camera = cam
    views = [
        ("dbg_front", 684, (0.3, -3.6, 1.0), (0, 0, 0.95), 35, None),
        ("dbg_side", 684, (3.6, 0.2, 1.0), (0, 0, 0.95), 35, None),
        ("dbg_back", 684, (0.3, 3.6, 1.3), (0, 0, 1.0), 35, None),
        ("dbg_face", 684, (0.0, -0.75, 1.68), (0, 0, 1.62), 50, None),
        ("dbg_bowhand", 468, (0.0, 0.0, 0.0), None, 50, "mixamorig:LeftHand"),
        ("dbg_aim", 1092, (2.0, -2.8, 1.4), (0.2, 0, 1.35), 35, None),
        ("dbg_pull_arrow", 935, (-1.6, 2.6, 1.7), (0, 0, 1.4), 35, None),
    ]
    for name, f, cpos, tgt, lens, bone in views:
        sc.frame_set(f)
        M = rig.matrix_world
        if bone:
            pb = rig.pose.bones[bone]
            g = M @ pb.tail
            c = g + M.to_3x3() @ Vector((0.45, -0.45, 0.12))
            t = g
        else:
            c, t = M @ Vector(cpos), M @ Vector(tgt)
        cam.location = c
        cam.rotation_mode = "QUATERNION"
        cam.rotation_quaternion = (t - c).to_track_quat("-Z", "Y")
        cd.lens = lens
        lamp.location = c + Vector((0, 0, 1.2))
        lamp.rotation_mode = "QUATERNION"
        lamp.rotation_quaternion = (t - lamp.location).to_track_quat("-Z", "Y")
        sc.render.filepath = os.path.join(out, f"{name}.png")
        bpy.ops.render.render(write_still=True)
        print("debug view", name, flush=True)
    sc.camera = film_cam


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--out", required=True)
    ap.add_argument("--stills", action="store_true")
    ap.add_argument("--samples", type=int, default=0)
    a = ap.parse_args(argv)
    sc = bpy.context.scene
    sc.render.threads_mode = "AUTO"
    if a.samples:
        sc.cycles.samples = a.samples
    os.makedirs(a.out, exist_ok=True)
    if a.stills:
        shots = json.loads(sc["shots"])
        frames = [(name, (f0 + f1) // 2) for name, f0, f1 in shots]
        sc.render.resolution_percentage = 50
        for name, f in frames:
            t = time.time()
            sc.frame_set(f)
            sc.render.filepath = os.path.join(a.out, f"{name}_f{f:04d}.png")
            bpy.ops.render.render(write_still=True)
            print(f"still {name} frame {f} {time.time() - t:.1f}s", flush=True)
        debug_views(sc, a.out)
        return
    total = sc.frame_end - sc.frame_start + 1
    per = -(-total // a.shards)
    f0 = sc.frame_start + a.shard * per
    f1 = min(sc.frame_end, f0 + per - 1)
    print(f"shard {a.shard}/{a.shards}: frames {f0}-{f1}", flush=True)
    for f in range(f0, f1 + 1):
        t = time.time()
        sc.frame_set(f)
        sc.render.filepath = os.path.join(a.out, f"frame_{f:04d}.png")
        bpy.ops.render.render(write_still=True)
        print(f"frame {f} {time.time() - t:.1f}s", flush=True)
    with open(os.path.join(a.out, "range.txt"), "w") as fh:
        fh.write(f"{f0} {f1}\n")


if __name__ == "__main__":
    main()
