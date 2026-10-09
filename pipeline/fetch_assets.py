"""Download every external asset the film needs (runs in CI with plain python3).

All environment assets come from Poly Haven (CC0). The character is generated with
MPFB (MakeHuman Plugin For Blender, GPL code / CC0 system assets).
"""
import json
import os
import sys
import time
import urllib.request
import zipfile

UA = {"User-Agent": "shri-ram-film-ci/1.0"}

MODELS = [
    "island_tree_01", "island_tree_02", "island_tree_03", "tree_small_02",
    "dead_tree_trunk_02", "tree_stump_01", "root_cluster_01", "root_cluster_02",
    "single_root", "fern_02", "shrub_01", "shrub_02", "shrub_04",
    "pachira_aquatica_01", "calathea_orbifolia_01", "grass_medium_02", "moss_01",
    "rock_moss_set_01", "boulder_01", "dry_branches_medium_01",
]
TEXTURES = ["forest_floor", "chinese_hackberry_bark", "leaves_forest_ground"]
HDRI = "sunset_forest"

MPFB_EXT = "https://extensions.blender.org/api/v1/extensions/?type=add-on"
MPFB_ASSETS = "http://files.makehumancommunity.org/asset_packs/makehuman_system_assets/makehuman_system_assets_cc0.zip"
CINZEL = "https://github.com/google/fonts/raw/main/ofl/cinzel/Cinzel%5Bwght%5D.ttf"


def get(url, dest=None, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            if dest:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as f:
                    f.write(data)
            return data
        except Exception as e:  # noqa: BLE001
            print(f"  retry {i + 1} {url}: {e}", flush=True)
            time.sleep(3 * (i + 1))
    raise RuntimeError("download failed: " + url)


def api(path):
    return json.loads(get("https://api.polyhaven.com/" + path))


def main(root, res):
    os.makedirs(root, exist_ok=True)
    manifest = {"models": {}, "textures": {}, "hdri": None}

    for mid in MODELS:
        try:
            files = api("files/" + mid)
            g = files["gltf"].get(res) or files["gltf"]["1k"]
            g = g["gltf"]
            base = os.path.join(root, "models", mid)
            gpath = os.path.join(base, mid + ".gltf")
            if not os.path.exists(gpath):
                print("model", mid, flush=True)
                get(g["url"], gpath)
                for rel, inc in g.get("include", {}).items():
                    get(inc["url"], os.path.join(base, rel))
            manifest["models"][mid] = gpath
        except Exception as e:  # noqa: BLE001
            print("!! model skipped", mid, e, flush=True)

    for tid in TEXTURES:
        try:
            files = api("files/" + tid)
            out = {}
            for key, name in (("Diffuse", "diff"), ("nor_gl", "nor"), ("Rough", "rough"),
                              ("Displacement", "disp"), ("AO", "ao")):
                if key not in files:
                    continue
                entry = files[key].get(res) or files[key]["1k"]
                fmt = entry.get("jpg") or entry.get("png")
                p = os.path.join(root, "textures", tid, f"{tid}_{name}.{fmt['url'].rsplit('.', 1)[1]}")
                if not os.path.exists(p):
                    get(fmt["url"], p)
                out[name] = p
            manifest["textures"][tid] = out
            print("texture", tid, flush=True)
        except Exception as e:  # noqa: BLE001
            print("!! texture skipped", tid, e, flush=True)

    files = api("files/" + HDRI)
    h = files["hdri"].get("4k" if res != "1k" else "2k")["hdr"]
    hp = os.path.join(root, "hdri", HDRI + ".hdr")
    if not os.path.exists(hp):
        get(h["url"], hp)
    manifest["hdri"] = hp
    print("hdri", HDRI, flush=True)

    # MPFB extension + its CC0 system assets (skins, eyes, eyebrows, eyelashes)
    ext = json.loads(get(MPFB_EXT))
    mp = next(e for e in ext["data"] if e["id"] == "mpfb")
    zp = os.path.join(root, "mpfb", "mpfb.zip")
    if not os.path.exists(zp):
        get(mp["archive_url"], zp)
    manifest["mpfb_zip"] = zp
    ap = os.path.join(root, "mpfb", "system_assets.zip")
    sysdir = os.path.join(root, "mpfb", "system_assets")
    if not os.path.isdir(sysdir):
        get(MPFB_ASSETS, ap)
        with zipfile.ZipFile(ap) as z:
            z.extractall(sysdir)
        os.remove(ap)
    manifest["mpfb_assets"] = sysdir
    print("mpfb", mp["version"], flush=True)

    fp = os.path.join(root, "fonts", "Cinzel.ttf")
    if not os.path.exists(fp):
        get(CINZEL, fp)
    manifest["font"] = fp

    with open(os.path.join(root, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print("assets ready", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "2k")
