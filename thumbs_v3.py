#!/usr/bin/env python3
"""One visual family for every entry image: a light base map, one accent color for the layer being shown, 3:2 at 2x.

Usage: python3 -I thumbs_v3.py <catalog.csv> <out_dir> [--render] [--force]
Reuses the per-layer recipes in thumbs.py (same folder) for framing and overlays, then normalizes them: the base style is
light-v11 everywhere except imagery (satellite-v9), terrain (outdoors-v12, also light) and live traffic (navigation-day-v1,
the only style that draws traffic). Entries thumbs.py does not know get a recipe here. Writes <out_dir>/specs.json and,
with --render, <out_dir>/<ID>.jpg (1200x800) via the mapbox CLI. Existing files are kept unless --force.
"""
import csv, json, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

CSV, OUT = sys.argv[1], sys.argv[2]
RENDER, FORCE = "--render" in sys.argv, "--force" in sys.argv
os.makedirs(OUT, exist_ok=True)
PLAN_CSV = os.path.join(os.path.dirname(os.path.abspath(CSV)), "catalog-plan.csv")   # thumbs.py plans every row of the CSV it is given at import; the pre-Mathew catalog is the one it knows
sys.argv = ["thumbs.py", PLAN_CSV if os.path.exists(PLAN_CSV) else CSV, OUT]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import thumbs as T                              # noqa: E402  (helpers: spec, layer, pins, polyline, heat_grid, route, HL, ACC, by_layer)

W, H = 600, 400
LIGHT = {"satellite-v9": "satellite-v9", "outdoors-v12": "outdoors-v12", "navigation-day-v1": "navigation-day-v1", "navigation-night-v1": "navigation-day-v1"}
rows = list(csv.DictReader(open(CSV, encoding="utf-8")))
by_id = {r["ID"]: r for r in rows}
dom_ids = {r["ID"] for r in rows if r["Catalog type"] == "Domain"}
entries = [r for r in rows if r["Catalog type"] == "Dataset" and r["Parent ID"] in dom_ids]

S, L, P = T.spec, T.layer, T.pins
HL = T.HL
def custom(r):
    """Recipes for entries thumbs.py does not recognize (rows that came from Mathew's sheet or were renamed)."""
    l, p = r["Layer / component"], r["Product / dataset"]
    if l == "Traffic Data": return S("navigation-day-v1", -0.12, 51.51, 11.5)
    if l == "3D Lane Data / Autopilot Map": return S("satellite-streets-v12", 8.62, 50.09, 15)
    if l == "Movement": return S("light-v11", -0.126, 51.512, 11.9, overlay=T.heat_grid(-0.18, 51.478, 8, 0.0135, seed=1))
    if (p, l) == ("Streets v8", "road"): return S("light-v11", 2.3522, 48.8566, 13.4, addlayer=L("line", "road", width=1.6))
    if (p, l) == ("Streets v8", "building"): return S("light-v11", -73.985, 40.748, 15.2, addlayer=L("fill", "building", op=0.75))
    if l == "Mapbox Boundaries": return S("light-v11", 10.5, 50.5, 4.6, addlayer=L("line", "admin", width=1.8))
    if l == "Indoor v3": return S("light-v11", -122.3875, 37.6165, 16.2, overlay=P([((-122.389, 37.616), "airport"), ((-122.386, 37.617), "cafe"), ((-122.3855, 37.6155), "shop")], color=HL))
    if l == "Toll cost calculation": return S("light-v11", -122.437, 37.803, 12.3, overlay=T.route)
    if l == "Neighborhoods": return S("light-v11", -73.985, 40.74, 12.2, addlayer=L("circle", "place_label", radius=6))
    if l == "Street Names": return S("light-v11", -122.42, 37.78, 14.2, addlayer=L("line", "road", width=2))
    if l == "Postcodes": return S("light-v11", -0.12, 51.51, 11.2, addlayer=L("circle", "housenum_label", radius=2.5))
    if l == "Countries": return S("light-v11", 15, 48, 3.2, addlayer=L("line", "admin", width=1.6))
    if l == "Entrance Data": return S("light-v11", -73.9855, 40.7484, 16.8, overlay=P([((-73.9857, 40.7486), "building"), ((-73.9849, 40.7479), "building"), ((-73.9862, 40.7480), "building")], color=HL))
    if l == "Landmark icons/POIs": return S("light-v11", 2.3230, 48.8640, 12.6, overlay=P([((2.2945, 48.8584), "landmark"), ((2.3376, 48.8606), "museum"), ((2.3499, 48.8530), "religious-christian"), ((2.2950, 48.8738), "attraction"), ((2.3431, 48.8867), "religious-christian")], color=HL, size="l"))
    if l == "Elevated Structures": return S("light-v11", -73.9985, 40.7072, 15.8, bearing=-35, pitch=60, addlayer=L("line", "structure", width=5))
    if l == "3D landmarks and building models": return S("light-v11", -73.9830, 40.7560, 15.0, bearing=-25, pitch=55, addlayer=L("fill-extrusion", "building", op=0.8))
    if l == "In-dash weather": return S("light-v11", -87.65, 41.88, 9, overlay=T.heat_grid(-88.1, 41.6, 8, 0.11, seed=2))
    if p == "Terrain-RGB v1": return S("outdoors-v12", 7.66, 45.98, 10.5, bearing=20, pitch=55)
    return None

specs = {}
for r in entries:
    try: s = custom(r) or T.by_layer(r)
    except Exception: s = S("light-v11", 0, 20, 1.5)
    s = dict(s)
    s["style"] = LIGHT.get(s["style"], "light-v11")
    if s["style"] == "light-v11" and not s.get("addlayer") and not s.get("overlay") and r["Layer / component"] not in ("Composite imagery",):
        s["addlayer"] = None
    s["caption"] = f'{s["style"]} · z{s["z"]}'
    specs[r["ID"]] = s
json.dump(specs, open(f"{OUT}/specs.json", "w"), indent=1)
print("specs", len(specs))

if RENDER:
    def render(item):
        rid, s = item
        path = f"{OUT}/{rid}.jpg"
        if os.path.exists(path) and os.path.getsize(path) > 2000 and not FORCE: return rid, "cached"
        cmd = ["mapbox", "static", "get-image", "-u", "mapbox"]
        if s.get("addlayer"): cmd += ["--addlayer", s["addlayer"]]
        cmd += [s["style"], s.get("overlay", ""), str(s["lon"]), str(s["lat"]), str(s["z"]), str(s.get("bearing", 0)), str(s.get("pitch", 0)), str(W), str(H), "@2x", ".jpeg"]
        with open(path, "wb") as f:
            p = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE)
        if p.returncode != 0 or os.path.getsize(path) < 2000:
            err = p.stderr.decode(errors="replace")[:200]
            try: os.remove(path)
            except OSError: pass
            return rid, "FAIL " + err
        return rid, "ok"
    with ThreadPoolExecutor(max_workers=5) as ex:
        for rid, st in ex.map(render, specs.items()):
            if st not in ("ok", "cached"): print(rid, st)
    print("rendered", sum(1 for rid in specs if os.path.exists(f"{OUT}/{rid}.jpg")), "of", len(specs))
