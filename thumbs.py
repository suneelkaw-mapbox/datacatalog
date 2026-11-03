#!/usr/bin/env python3
"""Plan and render one Static Images API thumbnail per catalog row.

Usage: python3 -I thumbs.py <catalog.csv> <img_dir> [--render]
Writes <img_dir>/specs.json and, with --render, <img_dir>/<ID>.jpg via the mapbox CLI (logged in as suneelkaw).
"""
import csv, json, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

CSV, OUT = sys.argv[1], sys.argv[2]
RENDER = "--render" in sys.argv
W, H = 600, 340
HL = "F26B1D"      # highlight for the layer the card describes
ACC = "1F5FBF"     # accent for computed overlays (routes, pins)


def polyline(coords):
    """Google encoded polyline from [(lon, lat), ...]."""
    out, plat, plon = [], 0, 0
    for lon, lat in coords:
        ilat, ilon = round(lat * 1e5), round(lon * 1e5)
        for d in (ilat - plat, ilon - plon):
            d = ~(d << 1) if d < 0 else d << 1
            while d >= 0x20:
                out.append(chr((0x20 | (d & 0x1F)) + 63)); d >>= 5
            out.append(chr(d + 63))
        plat, plon = ilat, ilon
    # percent-encode anything the URL path or the CLI's comma split would misread
    return "".join(c if c.isalnum() or c in "-_.~" else "%%%02X" % ord(c) for c in "".join(out))


def layer(kind, sl, color=HL, width=2.5, op=0.85, radius=5, src="composite"):
    paint = {"fill": {"fill-color": "#" + color, "fill-opacity": op},
             "line": {"line-color": "#" + color, "line-width": width},
             "circle": {"circle-color": "#" + color, "circle-radius": radius, "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.5},
             "fill-extrusion": {"fill-extrusion-color": "#" + color, "fill-extrusion-height": ["coalesce", ["get", "height"], 12], "fill-extrusion-base": ["coalesce", ["get", "min_height"], 0], "fill-extrusion-opacity": op}}[kind]
    return json.dumps({"id": "hl", "type": kind, "source": src, "source-layer": sl, "paint": paint})


def spec(style, lon, lat, z, overlay="", bearing=0, pitch=0, addlayer=None, before=None):
    return {"style": style, "overlay": overlay, "lon": lon, "lat": lat, "z": z, "bearing": bearing, "pitch": pitch, "addlayer": addlayer, "before": before}


def poly(coords, stroke="000000", sw=0, fill=HL, fop=0.5):
    """Filled polygon overlay from [(lon, lat), ...] (closed automatically)."""
    pts = list(coords) + [coords[0]]
    return f"path-{sw}+{stroke}-0+{fill}-{fop}({polyline(pts)})"


def square(lon, lat, size, fill, fop):
    s = size / 2
    return poly([(lon - s, lat - s), (lon + s, lat - s), (lon + s, lat + s), (lon - s, lat + s)], fill=fill, fop=fop)


def heat_grid(lon0, lat0, n, step, seed=3):
    """A grid of squares with varying opacity: reads as an activity or density heatmap."""
    import math
    cells = []
    for i in range(n):
        for j in range(n):
            d = math.hypot(i - n / 2 + 0.5, j - n / 2 + 0.5) / (n / 2)
            wobble = ((i * 7 + j * 13 + seed) % 5) / 10
            op = max(0.0, min(0.85, 0.9 - d * 0.75 + wobble - 0.2))
            if op > 0.08:
                cells.append(square(lon0 + i * step, lat0 + j * step, step * 0.92, HL, round(op, 2)))
    return ",".join(cells)


def blob(lon, lat, rx, ry, fill, fop, n=10, rot=0.0):
    import math
    pts = [(lon + rx * math.cos(2 * math.pi * k / n + rot) * (1 + 0.15 * ((k * 3) % 3)), lat + ry * math.sin(2 * math.pi * k / n + rot) * (1 + 0.12 * ((k * 5) % 3))) for k in range(n)]
    return poly(pts, fill=fill, fop=fop)


def lanes(lon, lat, length, heading_deg, count, spacing, color=HL):
    """Parallel lane lines: stands in for HD lane geometry."""
    import math
    h = math.radians(heading_deg); dx, dy = math.sin(h) * length, math.cos(h) * length
    px, py = math.cos(h) * spacing, -math.sin(h) * spacing
    out = []
    for k in range(count):
        off = (k - (count - 1) / 2)
        a = (lon + px * off - dx / 2, lat + py * off - dy / 2); b = (lon + px * off + dx / 2, lat + py * off + dy / 2)
        out.append(f"path-{3 if k in (0, count - 1) else 2}+{color}-0.95({polyline([a, b])})")
    return ",".join(out)


# ---- routes and shapes used by the API rows
ROUTE = [(-122.3965, 37.7893), (-122.403, 37.794), (-122.421, 37.803), (-122.447, 37.806), (-122.470, 37.807), (-122.4783, 37.8199)]
ZIG = [(-122.4200, 37.7750), (-122.4185, 37.7762), (-122.4172, 37.7751), (-122.4158, 37.7765), (-122.4140, 37.7756), (-122.4125, 37.7770)]
ISO = [(-122.396, 37.800), (-122.384, 37.792), (-122.386, 37.779), (-122.400, 37.774), (-122.414, 37.778), (-122.418, 37.790), (-122.408, 37.800), (-122.396, 37.800)]
AREA = [(-122.430, 37.790), (-122.405, 37.792), (-122.402, 37.772), (-122.428, 37.770), (-122.430, 37.790)]
route = f"path-5+{ACC}-0.9({polyline(ROUTE)})"
pins = lambda pts, icon="", color=ACC, size="s": ",".join(f"pin-{size}{('-' + i) if i else ''}+{color}({lon},{lat})" for (lon, lat), i in pts)

# ---- per layer / product views
def by_layer(r):
    p, l, cat = r["Product / dataset"], r["Layer / component"], r["Category"]
    if (p, l) == ("Streets v8", "Road attributes for routing"): return spec("light-v11", -122.42, 37.78, 14, addlayer=layer("line", "road", width=2.2))
    # Illustrative overlays for datasets the base styles do not draw (Rebecca's review, 2026-10-21)
    MOVE = spec("dark-v11", -0.1278, 51.507, 12.2, overlay=heat_grid(-0.18, 51.478, 8, 0.0135))
    WEATHER = spec("outdoors-v12", 138.5, 36.3, 5.2, overlay=",".join([blob(136.5, 35.2, 1.4, 1.0, "1F5FBF", 0.35, rot=0.4), blob(139.2, 36.9, 1.0, 0.7, "1F5FBF", 0.55, rot=1.1), blob(141.2, 38.6, 0.9, 0.6, "1F5FBF", 0.4, rot=2.0)]))
    HD = spec("satellite-streets-v12", 8.6430, 50.0890, 17.2, bearing=35, overlay=lanes(8.6430, 50.0890, 0.0036, 35, 6, 0.000045))
    if p == "Data domain":
        DOM = {"Road network": spec("light-v11", 2.35, 48.86, 12, addlayer=layer("line", "road", width=1.3)),
               "HD and lane-level roads": HD,
               "Traffic and incidents": spec("navigation-day-v1", -118.25, 34.05, 11),
               "Places and POIs": spec("light-v11", -122.4075, 37.785, 15.5, addlayer=layer("circle", "poi_label", radius=5)),
               "Addresses and geocoding": spec("light-v11", -122.4345, 37.7605, 16.5, addlayer=layer("circle", "housenum_label", radius=3.5)),
               "Boundaries and place names": spec("light-v11", -98, 39, 3.2, addlayer=layer("line", "admin", width=2.5)),
               "Buildings and 3D": spec("light-v11", -73.985, 40.748, 15.5, bearing=-20, pitch=60, addlayer=layer("fill", "building", color="9AA7B6", op=0.9)),
               "Land and water": spec("light-v11", -120.04, 39.09, 9, addlayer=layer("fill", "water", op=0.8)),
               "Elevation": spec("outdoors-v12", 7.66, 45.98, 10.5, bearing=20, pitch=55),
               "Imagery": spec("satellite-v9", 55.138, 25.118, 12),
               "Transit and airports": spec("light-v11", -0.12, 51.51, 13, addlayer=layer("circle", "transit_stop_label", radius=4.5)),
               "Weather and environment": WEATHER,
               "Movement": MOVE,
               "EV charging": spec("streets-v12", -122.41, 37.78, 13, overlay=pins([((-122.40, 37.79), "charging-station"), ((-122.42, 37.77), "charging-station"), ((-122.43, 37.79), "charging-station"), ((-122.395, 37.775), "charging-station")])),
               "Parking": spec("streets-v12", -118.37, 34.03, 15.5, overlay=pins([((-118.372, 34.032), "parking"), ((-118.368, 34.028), "parking"), ((-118.375, 34.027), "parking")], color=HL, size="l")),
               "Reference and user context": spec("light-v11", 15, 25, 1.2, addlayer=layer("line", "admin", width=2))}
        return DOM[l]
    if p == "Movement": return spec("dark-v11", -0.1278, 51.507, 12.2, overlay=heat_grid(-0.18, 51.478, 8, 0.0135, seed=1 if l.startswith("Daily") else 4))
    if p == "Autopilot Map":
        return {"Physical objects": spec("satellite-streets-v12", 8.6430, 50.0890, 17.2, bearing=35, overlay=lanes(8.6430, 50.0890, 0.0036, 35, 6, 0.000045) + "," + pins([((8.6436, 50.0896), ""), ((8.6424, 50.0884), "")], color=ACC)),
                "Driving patterns": spec("satellite-streets-v12", 8.6430, 50.0890, 17.2, bearing=35, overlay=lanes(8.6430, 50.0890, 0.0036, 35, 3, 0.00009, color=ACC)),
                "Road topology & lane connectivity": spec("satellite-streets-v12", 8.6430, 50.0890, 16.5, bearing=35, overlay=lanes(8.6430, 50.0890, 0.006, 35, 4, 0.00007) + "," + lanes(8.6440, 50.0896, 0.004, 80, 2, 0.00007, color=ACC)),
                "Autopilot Map": HD}[l]
    if p == "3D Lanes": return HD
    if p == "Mapbox Standard" and l == "HD road details": return HD
    if p in ("Japan Weather Layers", "Global weather", "Navigation SDK weather"):
        if l == "Japan Weather Layers": return WEATHER
        if p == "Navigation SDK weather": return spec("navigation-day-v1", -87.63, 41.88, 10, overlay=blob(-87.75, 41.95, 0.35, 0.22, "1F5FBF", 0.45, rot=0.7))
        if p == "Global weather": return spec("outdoors-v12", 10, 30, 1.5, overlay=",".join([blob(-40, 35, 18, 9, "1F5FBF", 0.35), blob(100, 15, 14, 8, "1F5FBF", 0.4, rot=1.2), blob(20, -10, 12, 7, "1F5FBF", 0.3, rot=2.3)]))
    if p == "Air quality": return spec("dark-v11", -118.25, 34.05, 9, overlay=",".join([blob(-118.2, 34.05, 0.35, 0.2, "F26B1D", 0.5), blob(-117.9, 34.1, 0.2, 0.12, "F26B1D", 0.35, rot=0.9)]))
    if p == "Pollen and UV": return spec("outdoors-v12", -122.3, 37.9, 9, overlay=blob(-122.25, 37.85, 0.3, 0.2, "F26B1D", 0.4, rot=0.5))
    if p == "Hazard alerts": return spec("dark-v11", -81.5, 28.5, 7, overlay=",".join([blob(-81.8, 27.6, 1.3, 0.9, "F26B1D", 0.45), blob(-80.4, 29.4, 0.9, 0.6, "F26B1D", 0.35, rot=1.4)]))
    if p == "Parking (Parkopedia)": return spec("streets-v12", -118.37, 34.03, 15.5, overlay=pins([((-118.372, 34.032), "parking"), ((-118.368, 34.028), "parking"), ((-118.375, 34.027), "parking")], color=HL, size="l"))
    if p == "EV charging locations": return spec("streets-v12", -122.41, 37.78, 13, overlay=pins([((-122.40, 37.79), "charging-station"), ((-122.42, 37.77), "charging-station"), ((-122.43, 37.79), "charging-station"), ((-122.395, 37.775), "charging-station")]))
    if p == "Places POI dataset": return spec("light-v11", -122.4075, 37.785, 15.5, addlayer=layer("circle", "poi_label", radius=5))
    if p == "Address dataset": return spec("light-v11", -122.4345, 37.7605, 16.5, addlayer=layer("circle", "housenum_label", radius=3.5))
    if p == "3D landmarks": return spec("streets-v12", 2.2945, 48.858, 15.5, bearing=30, pitch=60)
    if p == "Procedural 3D buildings": return spec("light-v11", -87.632, 41.884, 15.3, bearing=-20, pitch=55, addlayer=layer("fill-extrusion", "building", op=0.75))
    if p == "Terrain-RGB v1": return spec("outdoors-v12", 86.93, 27.99, 10, bearing=20, pitch=50)
    if (p, l) == ("Streets v8", "structure"): return spec("light-v11", -73.9969, 40.7061, 15.2, addlayer=layer("line", "structure", width=4))
    if p == "Streets v8":
        V = {
            "admin": spec("light-v11", -106.45, 31.76, 8, addlayer=layer("line", "admin", width=3)),
            "aeroway": spec("light-v11", -122.379, 37.617, 13, addlayer=layer("line", "aeroway", width=2)),
            "airport_label": spec("light-v11", 4.9, 51.2, 6, addlayer=layer("circle", "airport_label", radius=6)),
            "building": spec("light-v11", -73.985, 40.748, 15, addlayer=layer("fill", "building")),
            "housenum_label": spec("light-v11", -122.4345, 37.7605, 16.5, addlayer=layer("circle", "housenum_label", radius=3.5)),
            "landuse_overlay": spec("light-v11", -80.9, 25.5, 8, addlayer=layer("fill", "landuse_overlay", op=0.7)),
            "landuse": spec("light-v11", -122.48, 37.769, 13, addlayer=layer("fill", "landuse", op=0.7)),
            "motorway_junction": spec("light-v11", -118.28, 34.03, 12.5, addlayer=layer("circle", "motorway_junction", radius=5)),
            "natural_label": spec("light-v11", -119.55, 37.75, 9, addlayer=layer("circle", "natural_label", radius=5)),
            "place_label": spec("light-v11", 10, 49, 4, addlayer=layer("circle", "place_label", radius=4)),
            "poi_label": spec("light-v11", -122.4075, 37.785, 15.5, addlayer=layer("circle", "poi_label", radius=4)),
            "road": spec("light-v11", 2.35, 48.86, 12, addlayer=layer("line", "road", width=1.3)),
            "structure": spec("light-v11", -122.478, 37.82, 13.5, addlayer=layer("line", "structure", width=3)),
            "transit_stop_label": spec("light-v11", -0.12, 51.51, 13, addlayer=layer("circle", "transit_stop_label", radius=4.5)),
            "water": spec("light-v11", -120.04, 39.09, 9, addlayer=layer("fill", "water", op=0.8)),
            "waterway": spec("light-v11", 4.9, 52.37, 13, addlayer=layer("line", "waterway", width=2)),
        }
        return V[l]
    if p == "Terrain v2":
        return {"contour": spec("outdoors-v12", -121.76, 46.85, 11), "hillshade": spec("outdoors-v12", -112.1, 36.1, 10), "landcover": spec("outdoors-v12", 8.0, 46.6, 8)}[l]
    if p == "Satellite": return spec("satellite-v9", 55.138, 25.118, 12)
    if p == "Terrain-RGB v1": return spec("satellite-v9", 86.93, 27.99, 10)
    if p == "Terrain-DEM v1": return spec("outdoors-v12", 7.66, 45.98, 11, bearing=30, pitch=60)
    if p == "Terrain-RGB v1 and Terrain-DEM v1": return spec("outdoors-v12", 7.66, 45.98, 10.5, bearing=20, pitch=55)
    if p == "Data domain":
        DOM = {"Road network": spec("light-v11", 2.35, 48.86, 12, addlayer=layer("line", "road", width=1.3)),
               "HD and lane-level roads": spec("satellite-streets-v12", 11.62, 48.17, 15.5, bearing=20, pitch=55),
               "Traffic and incidents": spec("navigation-day-v1", -118.25, 34.05, 11),
               "Places and POIs": spec("light-v11", -122.4075, 37.785, 15.5, addlayer=layer("circle", "poi_label", radius=4)),
               "Addresses and geocoding": spec("streets-v12", -122.3965, 37.7893, 14.5, overlay=f"pin-l+{ACC}(-122.3965,37.7893)"),
               "Boundaries and place names": spec("light-v11", -98, 39, 3.2, addlayer=layer("line", "admin", width=2.5)),
               "Buildings and 3D": spec("light-v11", -73.985, 40.748, 15.5, bearing=-20, pitch=60, addlayer=layer("fill", "building", color="9AA7B6", op=0.9)),
               "Land and water": spec("light-v11", -120.04, 39.09, 9, addlayer=layer("fill", "water", op=0.8)),
               "Elevation": spec("outdoors-v12", 7.66, 45.98, 10.5, bearing=20, pitch=55),
               "Imagery": spec("satellite-v9", 55.138, 25.118, 12),
               "Transit and airports": spec("light-v11", -0.12, 51.51, 13, addlayer=layer("circle", "transit_stop_label", radius=4.5)),
               "Weather and environment": spec("outdoors-v12", 137.5, 36.5, 4.6),
               "Movement": spec("dark-v11", -0.12, 51.51, 12),
               "EV charging": spec("streets-v12", -122.41, 37.78, 13, overlay=pins([((-122.40, 37.79), "charging-station"), ((-122.42, 37.77), "charging-station"), ((-122.43, 37.79), "charging-station")])),
               "Parking": spec("streets-v12", -118.37, 34.03, 15.5, overlay=pins([((-118.372, 34.032), "parking"), ((-118.368, 34.028), "parking")], color=HL)),
               "Reference and user context": spec("light-v11", 15, 25, 1.2, addlayer=layer("line", "admin", width=2))}
        return DOM[l]
    if p == "Navigation SDK weather": return spec("navigation-day-v1", -87.63, 41.88, 10)
    if (p, l) == ("Streets v8", "Road attributes for routing"): return spec("navigation-day-v1", -122.42, 37.78, 14)
    if p == "Optimization API v1 and v2": return spec("streets-v12", -122.42, 37.78, 12.5, overlay=pins([((-122.40, 37.79), "1"), ((-122.44, 37.80), "2"), ((-122.45, 37.775), "3"), ((-122.43, 37.76), "4")]))
    if (p, l) == ("Movement", "Movement"): return spec("dark-v11", -73.99, 40.74, 12)
    if (p, l) == ("Traffic Data", "Traffic Data"): return spec("navigation-day-v1", -0.12, 51.51, 11)
    if p == "Traffic v1 tileset and Traffic Data": return spec("navigation-day-v1", -0.12, 51.51, 11)
    if (p, l) == ("Autopilot Map", "Autopilot Map"): return spec("satellite-streets-v12", 8.62, 50.09, 14.5)
    if (p, l) == ("Places API", "Places"): return spec("streets-v12", -122.4019, 37.7868, 16, overlay=f"pin-l-cafe+{ACC}(-122.4019,37.7868),pin-s-restaurant+{ACC}(-122.4035,37.7875)")
    if (p, l) == ("Directions API", "Directions"): return spec("streets-v12", -122.437, 37.803, 12.3, overlay=route)
    if p == "Traffic v1 tileset": return spec("navigation-day-v1", -118.25, 34.05, 11)
    if p == "Boundaries v4":
        adm = layer("line", "admin", width=2.5)
        V = {"boundaries_admin_0": spec("light-v11", 15, 30, 1.2, addlayer=adm), "boundaries_admin_1": spec("light-v11", -98, 39, 3.2, addlayer=adm),
             "boundaries_admin_2": spec("light-v11", -97.5, 31, 6, addlayer=adm), "boundaries_admin_3": spec("light-v11", -0.1, 51.5, 9), "boundaries_admin_4": spec("light-v11", 2.35, 48.86, 11),
             "points_admin_<level>": spec("light-v11", -98, 39, 3.2, addlayer=layer("circle", "place_label", radius=4)),
             "boundaries_postal_1": spec("light-v11", 13.4, 52.52, 9), "boundaries_postal_2": spec("light-v11", 13.4, 52.52, 10), "boundaries_postal_3": spec("light-v11", 13.4, 52.52, 11), "boundaries_postal_4": spec("light-v11", 13.4, 52.52, 12),
             "points_postal_<level>": spec("light-v11", 13.4, 52.52, 10, addlayer=layer("circle", "place_label", radius=4)),
             "boundaries_stats_1": spec("light-v11", -87.65, 41.85, 8), "boundaries_stats_2": spec("light-v11", -87.65, 41.85, 9), "boundaries_stats_3": spec("light-v11", -87.65, 41.85, 10), "boundaries_stats_4": spec("light-v11", -87.65, 41.85, 11), "boundaries_stats_5": spec("light-v11", -87.65, 41.85, 12),
             "points_stats_<level>": spec("light-v11", -87.65, 41.85, 9, addlayer=layer("circle", "place_label", radius=4)),
             "boundaries_locality_1": spec("light-v11", 139.75, 35.68, 9), "boundaries_locality_2": spec("light-v11", 139.75, 35.68, 10), "boundaries_locality_3": spec("light-v11", 139.75, 35.68, 11), "boundaries_locality_4": spec("light-v11", 139.75, 35.68, 12),
             "points_locality_<level>": spec("light-v11", 139.75, 35.68, 10, addlayer=layer("circle", "place_label", radius=4)),
             "boundaries_legislative_1": spec("light-v11", -77.03, 38.9, 9), "boundaries_legislative_2": spec("light-v11", -77.03, 38.9, 10), "boundaries_legislative_3": spec("light-v11", -77.03, 38.9, 11), "boundaries_legislative_4": spec("light-v11", -77.03, 38.9, 12), "boundaries_legislative_5": spec("light-v11", -77.03, 38.9, 13),
             "points_legislative_<level>": spec("light-v11", -77.03, 38.9, 10, addlayer=layer("circle", "place_label", radius=4)),
             "boundaries_admin_lines": spec("light-v11", -104, 45, 4, addlayer=adm),
             "Lookup metadata": spec("dark-v11", 0, 25, 1.2),
             "Boundaries: Administrative": spec("light-v11", -98, 39, 3.2, addlayer=adm),
             "Boundaries: Postal": spec("light-v11", 13.4, 52.52, 10),
             "Boundaries: Statistical": spec("light-v11", -87.65, 41.85, 9),
             "Boundaries: Locality": spec("light-v11", 139.75, 35.68, 11),
             "Boundaries: Legislative": spec("light-v11", -77.03, 38.9, 10)}
        return V[l]
    if p == "Speed cameras": return spec("navigation-day-v1", -118.25, 34.05, 12.5, overlay=pins([((-118.26, 34.06), "police"), ((-118.24, 34.04), "police"), ((-118.27, 34.035), "police")], color=HL))
    if p == "Traffic Data": return spec("navigation-night-v1", -74.0, 40.73, 12) if l == "Live speeds" else spec("navigation-day-v1", -87.63, 41.88, 11)
    if p == "Movement": return spec("dark-v11", -0.12, 51.51, 12) if l == "Daily activity" else spec("dark-v11", -122.42, 37.77, 12)
    if p == "Autopilot Map": return {"Physical objects": spec("satellite-streets-v12", 8.64, 50.08, 15.5), "Driving patterns": spec("satellite-streets-v12", 8.68, 50.12, 14), "Road topology & lane connectivity": spec("satellite-streets-v12", 8.60, 50.05, 15)}[l]
    if p == "3D Lanes": return spec("satellite-streets-v12", 11.62, 48.17, 15.5, bearing=20, pitch=55)
    if p == "Geocoding API v6": return spec("streets-v12", -122.3965, 37.7893, 14.5, overlay=f"pin-l+{ACC}(-122.3965,37.7893)")
    if p == "Search Box API": return spec("streets-v12", -122.405, 37.787, 14, overlay=pins([((-122.3965, 37.7893), "cafe"), ((-122.409, 37.784), "restaurant"), ((-122.401, 37.792), "cafe"), ((-122.412, 37.790), "shop")]))
    if p == "Places API":
        return {"Place identity & business details": spec("streets-v12", -122.4019, 37.7868, 16, overlay=f"pin-l-cafe+{ACC}(-122.4019,37.7868)"),
                "Place quality, building & attributes": spec("streets-v12", -122.4019, 37.7868, 16.5, overlay=f"pin-l-star+{ACC}(-122.4019,37.7868)"),
                "Entrances & access points": spec("streets-v12", -122.4019, 37.7868, 17, overlay=f"pin-l+{ACC}(-122.4022,37.7866),pin-s-parking+{HL}(-122.4012,37.7872)"),
                "Place activity & photos": spec("streets-v12", -122.4019, 37.7868, 16, overlay=f"pin-l-attraction+{ACC}(-122.4019,37.7868)")}[l]
    if p == "Directions API":
        return {"Routes & travel estimates": spec("streets-v12", -122.437, 37.803, 12.3, overlay=route),
                "Route annotations": spec("streets-v12", -122.437, 37.803, 12.3, overlay=f"path-7+{HL}-0.9({polyline(ROUTE[:3])}),path-7+{ACC}-0.9({polyline(ROUTE[2:])})"),
                "Incidents & closures": spec("streets-v12", -122.437, 37.803, 12.3, overlay=route + f",pin-l-roadblock+{HL}(-122.421,37.803)"),
                "Guidance & intersections": spec("streets-v12", -122.403, 37.794, 15.5, overlay=f"path-6+{ACC}-0.9({polyline(ROUTE[:3])})"),
                "EV routing": spec("streets-v12", -122.437, 37.803, 12.3, overlay=route + f",pin-l-charging-station+{HL}(-122.447,37.806)")}[l]
    if p == "Matrix API": return spec("streets-v12", -122.42, 37.78, 12.5, overlay=pins([((-122.40, 37.79), "a"), ((-122.44, 37.80), "b"), ((-122.43, 37.76), "1"), ((-122.39, 37.77), "2"), ((-122.45, 37.775), "3")]))
    if p == "Isochrone API": return spec("streets-v12", -122.40, 37.787, 13, overlay=f"path-2+{ACC}-1+{ACC}-0.3({polyline(ISO)}),pin-s+{HL}(-122.3965,37.7893)")
    if p == "Map Matching API": return spec("streets-v12", -122.416, 37.776, 15.5, overlay=f"path-4+{HL}-0.9({polyline(ZIG)})")
    if p == "Optimization API v1": return spec("streets-v12", -122.42, 37.78, 12.5, overlay=pins([((-122.40, 37.79), "1"), ((-122.44, 37.80), "2"), ((-122.45, 37.775), "3"), ((-122.43, 37.76), "4"), ((-122.39, 37.77), "5")]))
    if p == "Optimization API v2": return spec("streets-v12", -122.42, 37.78, 12.5, overlay=pins([((-122.40, 37.79), "1"), ((-122.44, 37.80), "2"), ((-122.45, 37.775), "3")]) + "," + pins([((-122.43, 37.76), "1"), ((-122.39, 37.77), "2"), ((-122.41, 37.755), "3")], color=HL))
    if p == "EV Charge Finder API": return spec("streets-v12", -122.41, 37.78, 13, overlay=pins([((-122.40, 37.79), "charging-station"), ((-122.42, 37.77), "charging-station"), ((-122.43, 37.79), "charging-station"), ((-122.395, 37.775), "charging-station")]))
    if p == "Mapbox Standard":
        return {"poi": spec("streets-v12", -73.9857, 40.7484, 16), "place-labels": spec("streets-v12", -73.98, 40.75, 10.5), "buildings": spec("light-v11", -73.985, 40.748, 15.5, bearing=-20, pitch=60, addlayer=layer("fill", "building", color="9AA7B6", op=0.9)),
                "landmark-icons": spec("streets-v12", 2.2945, 48.858, 15.5), "indoor-labels": spec("streets-v12", -122.387, 37.616, 17), "3D scene assets": spec("streets-v12", -73.98, 40.757, 15.5, bearing=30, pitch=60), "HD road details": spec("satellite-streets-v12", -118.37, 34.03, 17)}[l]
    if p == "Your datasets": return spec("light-v11", -122.42, 37.78, 12.5, overlay=pins([((-122.40, 37.79), ""), ((-122.44, 37.80), ""), ((-122.43, 37.76), ""), ((-122.39, 37.77), "")], color=HL))
    if p == "Your vector tilesets": return spec("light-v11", -122.416, 37.781, 13, overlay=f"path-2+{HL}-1+{HL}-0.35({polyline(AREA)})")
    if p == "Your raster tilesets": return spec("satellite-v9", -93.5, 42.0, 13)
    if p == "Your raster arrays": return spec("dark-v11", -95, 38, 3.5)
    if p == "Japan Weather Layers":
        V = {"weather-jp-nowcast": spec("dark-v11", 139.7, 35.7, 8), "weather-jp-nowcast-last-60m": spec("dark-v11", 135.5, 34.7, 8), "weather-jp-rain-1-6": spec("outdoors-v12", 138, 36.5, 5.5), "weather-jp-rain-7-15": spec("outdoors-v12", 136, 35.5, 5.5),
             "weather-jp-temperature-0-39": spec("light-v11", 138, 37, 4.8), "weather-jp-temperature-40-78": spec("light-v11", 141, 39, 5), "weather-jp-wind-0-39": spec("outdoors-v12", 130.5, 32.5, 6), "weather-jp-wind-40-78": spec("outdoors-v12", 141.5, 43, 6),
             "weather-jp-snowdepth": spec("satellite-streets-v12", 141.35, 43.06, 9), "weather-jp-snowdepth-last-6hr": spec("satellite-streets-v12", 138.6, 36.9, 9), "weather-jp-alert": spec("streets-v12", 139.8, 35.72, 11), "weather-jp-landslide": spec("outdoors-v12", 135.7, 34.4, 10),
             "weather-jp-river-flood": spec("light-v11", 139.85, 35.75, 11, addlayer=layer("line", "waterway", width=3)),
             "Japan Weather Layers": spec("outdoors-v12", 137.5, 36.5, 4.6)}
        return V[l]
    if p == "Indoor v3": return spec("streets-v12", -122.3875, 37.6155, 17)
    if p == "Transit v2": return spec("light-v11", 139.767, 35.681, 13, addlayer=layer("circle", "transit_stop_label", radius=4.5))
    if p == "Bathymetry v2": return spec("outdoors-v12", -150, 10, 2.5)
    if p == "Countries v1": return spec("light-v11", 15, 25, 1.2, addlayer=layer("line", "admin", width=2))
    if p == "NAIP": return spec("satellite-v9", -93.62, 41.98, 13.5)
    if p == "Traffic incidents": return spec("navigation-day-v1", -118.25, 34.05, 12, overlay=f"pin-l-roadblock+{HL}(-118.24,34.055),pin-s-roadblock+{HL}(-118.27,34.04)")
    if p == "Search (bulk)": return spec("dark-v11", -122.41, 37.78, 13, overlay=pins([((-122.40, 37.79), "cafe"), ((-122.42, 37.77), "cafe"), ((-122.43, 37.79), "cafe"), ((-122.395, 37.775), "cafe"), ((-122.415, 37.785), "cafe"), ((-122.405, 37.765), "cafe")], color=HL))
    if p == "Global weather": return spec("outdoors-v12", 10, 30, 1.5)
    # AI-lab gap rows (2026-10-19)
    LABV = {
        "Place answers": spec("streets-v12", -122.4019, 37.7868, 16, overlay=f"pin-l-star+{HL}(-122.4019,37.7868),pin-s-restaurant+{ACC}(-122.4035,37.7875),pin-s-cafe+{ACC}(-122.4005,37.7858)"),
        "Place commerce": spec("streets-v12", -122.4019, 37.7868, 16, overlay=f"pin-l-restaurant+{HL}(-122.4019,37.7868)"),
        "Place change feed": spec("dark-v11", -122.41, 37.78, 13, overlay=pins([((-122.40, 37.79), "shop"), ((-122.42, 37.77), "shop"), ((-122.43, 37.79), "shop")], color=HL)),
        "Address validation": spec("streets-v12", -122.4345, 37.7605, 16.5, overlay=f"pin-l-building+{HL}(-122.4345,37.7605)"),
        "Entity cross-links": spec("light-v11", 2.2945, 48.858, 15.5, overlay=f"pin-l-monument+{HL}(2.2945,48.858)"),
        "Location context": spec("streets-v12", -122.3965, 37.7893, 14.5, overlay=f"pin-l+{HL}(-122.3965,37.7893)"),
        "Air quality": spec("dark-v11", -118.25, 34.05, 9), "Pollen and UV": spec("outdoors-v12", -122.3, 37.9, 9), "Hazard alerts": spec("dark-v11", -81.5, 28.5, 7),
        "Live transit departures": spec("light-v11", -0.12, 51.51, 13, addlayer=layer("circle", "transit_stop_label", radius=4.5)), "Parking availability": spec("streets-v12", -118.37, 34.03, 15.5, overlay=pins([((-118.372, 34.032), "parking"), ((-118.368, 34.028), "parking")], color=HL)),
        "EV charger availability": spec("streets-v12", -122.41, 37.78, 13, overlay=pins([((-122.40, 37.79), "charging-station"), ((-122.42, 37.77), "charging-station")], color=HL)),
        "Tolls and speed limits": spec("streets-v12", -122.437, 37.803, 12.3, overlay=route), "Local events": spec("dark-v11", -73.9935, 40.7505, 14, overlay=f"pin-l-stadium+{HL}(-73.9935,40.7505)"),
        "Street-level imagery": spec("satellite-streets-v12", -122.4194, 37.7749, 16), "3D assets export": spec("streets-v12", -73.98, 40.757, 15.5, bearing=30, pitch=60),
        "Places corpus": spec("dark-v11", -98, 39, 3.2), "Road graph": spec("dark-v11", 2.35, 48.86, 11, addlayer=layer("line", "road", width=1.2)), "Address corpus": spec("dark-v11", -0.12, 51.5, 10), "Geospatial QA benchmark": spec("light-v11", 0, 20, 1.2),
        "Area statistics": spec("light-v11", -87.65, 41.85, 9), "Building and parcel attributes": spec("light-v11", -73.985, 40.748, 15.5, addlayer=layer("fill", "building", op=0.7)),
        "Country and locale facts": spec("light-v11", 15, 25, 1.2, addlayer=layer("line", "admin", width=2)), "GeoMemories": spec("streets-v12", -122.42, 37.78, 12.5, overlay=pins([((-122.40, 37.79), "heart"), ((-122.44, 37.80), "heart"), ((-122.43, 37.76), "heart")], color=HL)),
        "Promoted pins": spec("streets-v12", -122.405, 37.787, 14, overlay=f"pin-l-cafe+{HL}(-122.4019,37.7868),pin-s-cafe+{ACC}(-122.409,37.784),pin-s-cafe+{ACC}(-122.401,37.792)"),
        "ECMWF IFS analysis (open)": spec("dark-v11", -30, 40, 1.6),
    }
    if p in LABV: return LABV[p]
    return spec("light-v11", 0, 20, 1.5)


rows = list(csv.DictReader(open(CSV)))
specs = {}
for r in rows:
    s = by_layer(r)
    s["caption"] = f'{s["style"]} · z{s["z"]}'
    specs[r["ID"]] = s
json.dump(specs, open(f"{OUT}/specs.json", "w"), indent=1)
print("specs", len(specs))

if RENDER:
    def render(item):
        rid, s = item
        path = f"{OUT}/{rid}.jpg"
        if os.path.exists(path) and os.path.getsize(path) > 2000:
            return rid, "cached"
        cmd = ["mapbox", "static", "get-image", "-u", "mapbox"]
        if s["addlayer"]:
            cmd += ["--addlayer", s["addlayer"]]
        cmd += [s["style"], s["overlay"], str(s["lon"]), str(s["lat"]), str(s["z"]), str(s["bearing"]), str(s["pitch"]), str(W), str(H), "@2x", ".jpeg"]   # 2x: 1200x680 so retina screens get a sharp tile (Will White, 2026-10-26)
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
            if st != "ok" and st != "cached":
                print(rid, st)
    n = sum(1 for rid in specs if os.path.exists(f"{OUT}/{rid}.jpg"))
    print("rendered", n, "of", len(specs))
