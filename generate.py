#!/usr/bin/env python3
"""Render the image-first catalog page: every dataset as static HTML, plus agents.txt (and llms.txt) for agents.

Usage: python3 -I generate.py <template.html> <catalog.csv> <img_dir> <sheet_url> <out.html> [img_url_prefix] [--site] [--base URL]
The template holds the chrome and the script; this file renders the index, the chapters and each dataset entry. Images
come from <img_dir> (3:2 renders from thumbs_v3.py) and are recompressed into <img_dir>/small, referenced as
<prefix><ID>.jpg (or embedded without a prefix). --site prepends the doctype, charset and viewport that the artifact
skeleton otherwise supplies. --base is the page's public URL, used in permalinks and agents.txt.
Names, lead datasets and API links are read from the template's script so the page, the Sheet view and this file agree.
"""
import base64, csv, datetime, html, json, os, re, subprocess, sys

SITE = "--site" in sys.argv
args = [a for a in sys.argv[1:] if a != "--site"]
BASE = ""
if "--base" in args:
    i = args.index("--base"); BASE = args[i + 1]; del args[i:i + 2]
TPL, CSV, IMG, SHEET, OUT = args[:5]
PREFIX = args[5] if len(args) > 5 else None
TODAY = datetime.date.today().isoformat()
rows = list(csv.DictReader(open(CSV, encoding="utf-8")))
small = os.path.join(IMG, "small"); os.makedirs(small, exist_ok=True)
tpl = open(TPL, encoding="utf-8").read()
e = lambda s: html.escape(s or "", quote=True)

# ---- rules shared with the page script
def js_const(name):
    m = re.search(r"const %s=(\{.*?\}|new Set\(\[.*?\]\)|\[.*?\]);" % name, tpl, re.S); s = m.group(1)
    if s.startswith("new Set("): s = s[len("new Set("):-1]
    return json.loads(s)
NAMES, LAB, LEAD, API_DOCS = js_const("NAMES"), set(js_const("LAB")), js_const("LEAD"), js_const("API_DOCS")
PRICING, SALES = "https://www.mapbox.com/pricing", "https://www.mapbox.com/contact/sales/"
cap = lambda s: s[:1].upper() + s[1:] if s else s
def name(r):
    key = r["Product / dataset"] + "|" + r["Layer / component"]
    if key in NAMES: return NAMES[key]
    if r["Product / dataset"] in LAB: return r["Product / dataset"]
    n = r["Layer / component"] or r["Product / dataset"]
    n = re.sub(r"^Boundaries: ", "", n).replace("_", " ").replace("<level>", "")
    n = re.sub(r"^boundaries (?=(admin|postal|stats|locality|legislative)\b)", "", n, flags=re.I).strip()
    return cap(n)
slugify = lambda s: re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")
what = lambda r: re.sub(r"\s*\(\d+ documented fields\)", "", cap(r["What you get"]))
def tag(r):
    s = r["Status"]
    if s.startswith("Preview"): return ("preview", "Preview")
    return None   # licensed data says Contact sales on its button, not as a badge (Suneel, 2026-11-02)
avail = lambda r: 0 if r["Status"].startswith(("GA", "Commercial")) else 1 if r["Status"].startswith("Preview") else 2 if r["Status"].startswith("Planned") else 3

# ---- the page's rows
page_rows = [r for r in rows if r["Catalog type"] in ("Dataset", "Domain") and not r["Status"].startswith(("Gap", "Planned"))]
src_order = {r["ID"]: i for i, r in enumerate(page_rows)}
by_id = {r["ID"]: r for r in page_rows}
is_domain = lambda r: r["Product / dataset"] == "Data domain"
children = lambda r: [x for x in page_rows if x["Parent ID"] == r["ID"]]
def domain_of(r):
    x = r
    while x and not is_domain(x): x = by_id.get(x["Parent ID"])
    return x
def weight(r):
    n = name(r)
    return LEAD.index(n) if n in LEAD else 50 + {"High": 0, "Medium": 1, "Low": 2}.get(r["AI / agent relevance"], 1)
order = lambda r: (avail(r), weight(r), src_order[r["ID"]])
domains = [r for r in page_rows if is_domain(r)]
entries = {d["ID"]: sorted(children(d), key=order) for d in domains}
seen = set()
def give_slug(r):
    s = slugify(name(r)) or r["ID"]
    if s in seen: s = f"{s}-{int(r['ID'].split('-')[1])}"
    seen.add(s); r["Slug"] = s
for d in domains: give_slug(d)
for d in domains:
    for r in entries[d["ID"]]:
        give_slug(r)
        for k in children(r): give_slug(k)
for r in rows: r.setdefault("Slug", "")
shown = [r for d in domains for r in entries[d["ID"]]]

def link_apis(text):
    out = []
    for part in re.split(r"\s*[;,]\s*", text or ""):
        if not part: continue
        hit = next((u for k, u in API_DOCS.items() if k.lower() in part.lower()), None)
        out.append((part, hit))
    return out
def api_for(r):
    return next((k for k in API_DOCS if k.lower() in (r["APIs and interfaces"] or "").lower()), None)

# ---- images (3:2)
images, total = {}, 0
for r in rows:
    rid = r["ID"]; src = os.path.join(IMG, rid + ".jpg"); dst = os.path.join(small, rid + ".jpg")
    if not os.path.exists(src): continue
    if not os.path.exists(dst) or os.path.getmtime(src) > os.path.getmtime(dst):
        subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "78", src, "--out", dst], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    b = open(dst, "rb").read(); total += len(b)
    images[rid] = (PREFIX + rid + ".jpg") if PREFIX else ("data:image/jpeg;base64," + base64.b64encode(b).decode())

# ---- the two facts under the description: how it ships and what it costs, in a few words each
def short_price(r):
    p = (r["Pricing model"] or "").strip(); low = p.lower()
    commercial = r["Status"].startswith("Commercial") or "Commercial" in r["Access gate"]
    if commercial or "contact sales" in low or "license" in low or "contract" in low: return ("Contact sales", SALES)
    if "free" in low: return ("Free", PRICING)
    if "paygo" in low or "pay as you go" in low or "per request" in low: return ("Pay as you go", PRICING)
    if "included" in low: return ("Included with the SDK", PRICING)
    if "billing in progress" in low: return ("Pricing to come", PRICING)
    return (cap(re.split(r"[.;(]", p)[0][:40]) if p else "", PRICING if p else "")

# ---- one dataset entry
MORE = [("Format", "Format / encoding"), ("Updated", "Update cadence and freshness"), ("Price", "Pricing model"), ("Useful for", "Primary use cases"), ("Source", "Source and provenance"),
        ("License terms", "License terms (derivatives, storage, attribution)")]   # the essentials only; the full record is in the Sheet and the JSON export
PUBLIC = [("ID", "ID"), ("Slug", "Slug"), ("Category", "Category"), ("Product / dataset", "Product / dataset"), ("Layer / component", "Layer / component"), ("What you get", "What you get"), ("Data type", "Data type"),
          ("Source and provenance", "Source and provenance"), ("Origin", "Origin"), ("Status", "Status"), ("Resolution / zoom range", "Resolution / zoom range"), ("Update cadence and freshness", "Update cadence and freshness"),
          ("Primary use cases", "Primary use cases"), ("Solution area", "Solution area"), ("AI / agent relevance", "AI / agent relevance"), ("MCP tool today", "MCP tool today"), ("One-call agent interface", "One-call agent interface"),
          ("Access form factor", "Access form factor"), ("APIs and interfaces", "APIs and interfaces"), ("Format / encoding", "Format / encoding"), ("Bulk-queryable as a dataset", "Bulk-queryable as a dataset"), ("Access gate", "Access gate"),
          ("Pricing model", "Pricing model"), ("Price or SKU link", "Price or SKU link"), ("License terms (derivatives, storage, attribution)", "License terms (derivatives, storage, attribution)"), ("Model training allowed", "Model training allowed"),
          ("Cacheable fields and window", "Cacheable fields and window"), ("Rate limit / quota", "Rate limit / quota"), ("Docs link", "Docs link"), ("Last reviewed", "Last reviewed")]
SEARCH = ["Product / dataset", "Layer / component", "What you get", "Category", "Data type", "Primary use cases", "Access form factor", "APIs and interfaces", "Status", "Access gate"]
def hay(r):
    parts = [name(r)] + [r.get(k, "") for k in SEARCH]
    for k in children(r): parts += [name(k), k["What you get"]]
    return " | ".join(parts).lower()
def entry_html(r, title=None, sub_override=None):
    p = by_id.get(r["Parent ID"]); t = tag(r); kids = children(r); api = api_for(r)
    commercial = r["Status"].startswith("Commercial") or "Commercial" in r["Access gate"]
    docs = r["Docs link"].strip(); img = images.get(r["ID"])
    sub = sub_override if sub_override is not None else (name(p) if p and not is_domain(p) else "")
    shown_name = title or name(r)
    ff = f'<a href="{e(API_DOCS[api])}" target="_blank" rel="noopener" title="{e(api)} docs">{e(r["Access form factor"])}</a>' if api else e(r["Access form factor"])
    # one line under the sentence: how it ships (linked to its API docs), Docs, Details. Price is the same for nearly everything, so it stays in Details.
    apis = ", ".join(f'<a href="{e(u)}" target="_blank" rel="noopener">{e(x)}</a>' if u else e(x) for x, u in link_apis(r["APIs and interfaces"]))
    sales = f'<dt>Licensing</dt><dd><a href="{SALES}" target="_blank" rel="noopener">Contact sales</a> for a contract; a token alone does not unlock this dataset.</dd>' if commercial else ""
    more = (f"<dt>How it ships</dt><dd>{ff}</dd>" + sales + (f"<dt>APIs</dt><dd>{apis}</dd>" if apis else "")
            + "".join(f"<dt>{e(l)}</dt><dd>{e(r[c])}</dd>" for l, c in MORE if r.get(c, "").strip()))
    levels = ""
    if kids:
        unit = "levels" if r["Product / dataset"] == "Boundaries v4" else "layers"
        levels = (f'<details open><summary>{len(kids)} {unit} inside</summary><ul class="levels">' +
                  "".join(f'<li id="{e(k["Slug"])}"><a class="lk" href="#{e(k["Slug"])}"><b>{e(name(k))}</b></a><span>{e(what(k))}</span>' + (f'<span><a href="{e(k["Docs link"])}" target="_blank" rel="noopener">Docs</a></span>' if k["Docs link"].strip() else "") + "</li>" for k in kids) + "</ul></details>")
    # the call to action: the docs when there are any, and Contact sales for anything that needs a contract
    cta = ((f'<a class="btn primary" href="{e(docs)}" target="_blank" rel="noopener">Read the docs <span aria-hidden="true">→</span></a>' if docs else "")
           + (f'<a class="btn{"" if docs else " primary"}" href="{SALES}" target="_blank" rel="noopener">Contact sales</a>' if commercial else ""))
    attrs = " ".join(f'data-{k}="{e(r[c] or "(blank)")}"' for k, c in (("status", "Status"), ("ff", "Access form factor"), ("ai", "AI / agent relevance"), ("bulk", "Bulk-queryable as a dataset")))
    return (f'<article class="ds{" has-img" if img else ""}" id="{e(r["Slug"])}" data-id="{e(r["ID"])}" {attrs} data-hay="{e(hay(r))}">'
            + (f'<div class="img"><img src="{e(img)}" alt="Map view of {e(name(r))}" loading="lazy" width="600" height="400"></div>' if img else "")
            + '<div class="words">'
            + (f'<span class="tag {t[0]}">{e(t[1])}</span>' if t else "")
            + f'<h3><a href="#{e(r["Slug"])}">{e(shown_name)}</a></h3>'
            + (f'<div class="sub">{e(sub)}</div>' if sub else "")
            + f'<p class="what">{e(what(r))}</p>'
            + (f'<div class="cta">{cta}</div>' if cta else "")
            + f'<div class="more" id="more-{e(r["ID"])}"><dl class="kv">{more}</dl>{levels}</div>'
            + "</div></article>")

sections, index, dchips = [], [], []
for d in domains:
    es = entries[d["ID"]]
    if not es: continue
    if len(es) == 1:
        # a domain with a single dataset is one entry, titled with the domain name (the dataset's own name becomes the sub line)
        r = es[0]; sub = name(r) if name(r) != name(d) else None
        sections.append(f'<section class="dom single" id="{e(d["Slug"])}">' + entry_html(r, title=name(d), sub_override=sub) + "</section>")
        index.append(f'<div class="ix" data-slug="{e(d["Slug"])}"><a href="#{e(d["Slug"])}" data-slug="{e(d["Slug"])}">{e(name(d))}</a><ul><li hidden><a href="#{e(r["Slug"])}" data-slug="{e(r["Slug"])}">{e(name(d))}</a></li></ul></div>')
        dchips.append(f'<a class="dchip" href="#{e(d["Slug"])}" data-slug="{e(d["Slug"])}">{e(name(d))}</a>')
        continue
    sections.append(f'<section class="dom" id="{e(d["Slug"])}"><div class="chapter"><div><h2>{e(name(d))}</h2><p>{e(what(d))}</p></div></div>' + "".join(entry_html(r) for r in es) + "</section>")
    index.append(f'<div class="ix" data-slug="{e(d["Slug"])}"><a href="#{e(d["Slug"])}" data-slug="{e(d["Slug"])}">{e(name(d))}</a><ul>' + "".join(f'<li><a href="#{e(r["Slug"])}" data-slug="{e(r["Slug"])}">{e(name(r))}</a></li>' for r in es) + "</ul></div>")
    dchips.append(f'<a class="dchip" href="#{e(d["Slug"])}" data-slug="{e(d["Slug"])}">{e(name(d))}</a>')
facts_line = f"{len(shown)} datasets in {len([d for d in domains if entries[d['ID']]])} domains. Every entry is a permalink and the page is plain HTML, so agents can read it as is or as agents.txt."
export = [{l: r.get(c, "") for l, c in PUBLIC if r.get(c, "").strip()} for r in shown]

# ---- agents.txt
def txt_entry(r):
    d = domain_of(r); lines = [f"### {name(r)}", f"URL: {BASE}#{r['Slug']}", f"Domain: {name(d) if d else ''}", f"Status: {(tag(r) or ('', 'Available'))[1]} ({r['Status']})", f"Description: {what(r)}"]
    for label, col in (("How it ships", "Access form factor"), ("APIs and interfaces", "APIs and interfaces"), ("Format", "Format / encoding"), ("Data type", "Data type"), ("Updated", "Update cadence and freshness"),
                       ("Resolution / zoom range", "Resolution / zoom range"), ("Price", "Pricing model"), ("License terms", "License terms (derivatives, storage, attribution)"),
                       ("Model training allowed", "Model training allowed"), ("Useful for", "Primary use cases"), ("Source", "Source and provenance"), ("Docs", "Docs link")):
        val = (r.get(col) or "").strip()
        if not val: continue
        if col == "APIs and interfaces": val = "; ".join(f"{p} ({u})" if u else p for p, u in link_apis(val))
        lines.append(f"{label}: {val}")
    kids = children(r)
    if kids: lines.append("Layers inside: " + "; ".join(f"{name(k)} ({BASE}#{k['Slug']}): {what(k)}" for k in kids))
    return "\n".join(lines) + "\n"
agents = [f"# Mapbox data catalog", f"Generated {TODAY}. Page: {BASE or 'this page'}. Each dataset has a permalink ({BASE}#<slug>); the page itself is static HTML with the same content.",
          "Datasets reach an agent three ways: the hosted runtime MCP server (https://mcp.mapbox.com/mcp) for search, geocoding, routing, matrices, isochrones and map matching;",
          "the DevKit MCP server (https://mcp-devkit.mapbox.com/mcp) for point lookups into any vector tileset (Tilequery) plus styles and tokens;",
          f"and REST with an access token for tiles, rasters, files and feeds. Datasets marked Contact sales need a contract before a token works ({SALES}).",
          f"Sign up for a token: https://account.mapbox.com/auth/signup/ . Pricing: {PRICING} . Docs index: https://docs.mapbox.com/llms.txt",
          "Handling rules: longitude then latitude; store the retrieval time, source URL, product version and area with any result; typical traffic is historical and Places activity is historical busyness;",
          "Movement never exposes individual trajectories; treat 401 and 403 as a missing contract or token scope and 429 as a rate limit.", ""]
for d in domains:
    es = entries[d["ID"]]
    if not es: continue
    agents.append(f"## {name(d)}\n{what(d)}\n"); agents += [txt_entry(r) for r in es]
AGENTS = "\n".join(agents)

page = (tpl.replace("__DATA__", json.dumps(export, ensure_ascii=False).replace("</", "<\\/")).replace("__SHEET__", SHEET).replace("__BASE__", BASE).replace("__DATE__", TODAY)
        .replace("__FACTS__", facts_line).replace("__DCHIPS__", "".join(dchips)).replace("__INDEX__", "".join(index)).replace("__SECTIONS__", "".join(sections)))
if SITE:
    page = '<!doctype html>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n' + page
open(OUT, "w", encoding="utf-8").write(page)
outdir = os.path.dirname(os.path.abspath(OUT))
for fn in ("agents.txt", "llms.txt"):
    open(os.path.join(outdir, fn), "w", encoding="utf-8").write(AGENTS)
print(f"rows {len(rows)} shown {len(shown)} domains {len(domains)} images {len(images)} image bytes {total/1e6:.2f} MB html {len(page)/1e6:.2f} MB agents.txt {len(AGENTS)/1e3:.0f} KB")
