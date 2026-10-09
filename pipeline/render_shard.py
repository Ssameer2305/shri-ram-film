"""blender -b project.blend --python pipeline/render_shard.py -- --shard I --shards N --out DIR
   blender -b project.blend --python pipeline/render_shard.py -- --stills --out DIR"""
import argparse
import json
import os
import sys
import time

import bpy


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
