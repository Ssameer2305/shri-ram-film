"""blender -b --python pipeline/build_scene.py -- --assets DIR --out project/shri_ram_intro.blend --quality preview|final"""
import argparse
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy  # noqa: E402

QUALITY = {
    "preview": dict(res=(1280, 536), samples=24, threshold=0.06, volume_step=4.0, mblur=False),
    "final": dict(res=(1920, 804), samples=96, threshold=0.025, volume_step=1.5, mblur=True),
}


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--quality", default="preview")
    return ap.parse_args(argv)


def render_settings(scene, q):
    Q = QUALITY[q]
    r = scene.render
    r.engine = "CYCLES"
    scene.cycles.device = "CPU"
    r.resolution_x, r.resolution_y = Q["res"]
    r.resolution_percentage = 100
    r.fps = 24
    scene.frame_start, scene.frame_end = 1, 1440
    scene.cycles.samples = Q["samples"]
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = Q["threshold"]
    scene.cycles.use_denoising = True
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
    scene.cycles.max_bounces = 6
    scene.cycles.diffuse_bounces = 2
    scene.cycles.glossy_bounces = 2
    scene.cycles.transmission_bounces = 4
    scene.cycles.volume_bounces = 0
    scene.cycles.transparent_max_bounces = 24
    scene.cycles.caustics_reflective = False
    scene.cycles.caustics_refractive = False
    scene.cycles.blur_glossy = 1.0
    scene.cycles.volume_step_rate = Q["volume_step"]
    scene.cycles.volume_max_steps = 96
    scene.cycles.use_light_tree = True
    r.use_persistent_data = True
    r.use_motion_blur = Q["mblur"]
    r.motion_blur_shutter = 0.5
    r.motion_blur_position = "END"
    r.film_transparent = False
    vs = scene.view_settings
    vs.view_transform = "AgX"
    for look in ("AgX - Medium High Contrast", "AgX - Base Contrast", "None"):
        try:
            vs.look = look
            break
        except TypeError:
            continue
    vs.exposure = 0.55
    r.image_settings.file_format = "PNG"
    r.image_settings.color_depth = "8"
    r.image_settings.compression = 30
    scene.view_layers[0].use_pass_mist = True


def compositor(scene):
    scene.use_nodes = True
    nt = scene.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    rl = nt.nodes.new("CompositorNodeRLayers")
    comp = nt.nodes.new("CompositorNodeComposite")
    # atmospheric depth haze from the mist pass
    haze = nt.nodes.new("CompositorNodeMixRGB")
    haze.blend_type = "SCREEN"
    haze.inputs[2].default_value = (0.95, 0.78, 0.55, 1)
    mistscale = nt.nodes.new("CompositorNodeMath")
    mistscale.operation = "MULTIPLY"
    mistscale.inputs[1].default_value = 0.14
    nt.links.new(rl.outputs["Mist"], mistscale.inputs[0])
    nt.links.new(mistscale.outputs[0], haze.inputs[0])
    nt.links.new(rl.outputs["Image"], haze.inputs[1])
    # soft bloom on highlights
    glare = nt.nodes.new("CompositorNodeGlare")
    glare.glare_type = "FOG_GLOW"
    glare.quality = "HIGH"
    glare.threshold = 0.85
    glare.mix = -0.82
    glare.size = 8
    nt.links.new(haze.outputs[0], glare.inputs[0])
    # warm highlights / cool shadows
    cb = nt.nodes.new("CompositorNodeColorBalance")
    cb.correction_method = "LIFT_GAMMA_GAIN"
    cb.lift = (0.985, 1.0, 1.03)
    cb.gamma = (1.0, 0.99, 0.97)
    cb.gain = (1.06, 1.0, 0.92)
    nt.links.new(glare.outputs[0], cb.inputs[1])
    # subtle chromatic edge + vignette
    lens = nt.nodes.new("CompositorNodeLensdist")
    lens.inputs["Dispersion"].default_value = 0.008
    nt.links.new(cb.outputs[0], lens.inputs["Image"])
    ell = nt.nodes.new("CompositorNodeEllipseMask")
    ell.width, ell.height = 0.92, 0.95
    blur = nt.nodes.new("CompositorNodeBlur")
    blur.size_x = blur.size_y = 220
    blur.filter_type = "FAST_GAUSS"
    nt.links.new(ell.outputs[0], blur.inputs[0])
    vig = nt.nodes.new("CompositorNodeMixRGB")
    vig.blend_type = "MULTIPLY"
    vmap = nt.nodes.new("CompositorNodeMapRange")
    vmap.inputs["To Min"].default_value = 0.62
    vmap.inputs["To Max"].default_value = 1.0
    nt.links.new(blur.outputs[0], vmap.inputs[0])
    nt.links.new(lens.outputs[0], vig.inputs[1])
    nt.links.new(vmap.outputs[0], vig.inputs[2])
    vig.inputs[0].default_value = 1.0
    nt.links.new(vig.outputs[0], comp.inputs[0])


def main():
    a = args()
    t0 = time.time()
    with open(os.path.join(a.assets, "manifest.json")) as f:
        manifest = json.load(f)
    bpy.ops.wm.read_homefile(use_empty=True)
    scene = bpy.context.scene
    scene.name = "ShriRam_Intro"
    import environment
    import character
    import animation
    env = environment.build(manifest, a.quality)
    print(f"environment built {time.time() - t0:.0f}s", flush=True)
    char = character.build(manifest)
    print(f"character built {time.time() - t0:.0f}s", flush=True)
    anim = animation.build(char, env, scene)
    print(f"animation keyed {time.time() - t0:.0f}s", flush=True)
    render_settings(scene, a.quality)
    compositor(scene)
    scene["quality"] = a.quality
    scene["shots"] = json.dumps(animation.SHOTS)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    try:
        bpy.ops.file.pack_all()
    except Exception as e:  # noqa: BLE001
        print("!! pack_all", e)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out), compress=True)
    print(f"saved {a.out} {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
