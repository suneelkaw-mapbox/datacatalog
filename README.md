# Mapbox data catalog

A static, image-first catalog of every dataset Mapbox makes: one page, one entry per dataset, a permalink for each, and the
same content as `site/agents.txt` for agents. Live internally at https://hey.mapbox.com/data-catalogV2/ (Okta).

## What is here

- `site/` — the published page (`index.html`), its thumbnails and `agents.txt` / `llms.txt`. Serve this folder as is; there is no runtime build.
- `catalog.csv` — the inventory the page is built from, public columns only. One row per dataset or domain; `Catalog type`
  says which rows reach the page (Dataset, Domain) and `Parent ID` nests datasets under domains and layers under datasets.
  The inventory itself is maintained in Mathew Antony's Data Catalog sheet; this file is generated from it.
- `template.html` — the page chrome and its small script (search, index tracking, copy buttons). Display names, lead
  datasets and API doc links live in the `NAMES`, `LEAD` and `API_DOCS` constants at the top of the script.
- `generate.py` — renders `site/index.html` and `agents.txt` from the template and the CSV.
- `thumbs_v3.py` and `thumbs.py` — render one map image per entry with the Mapbox Static Images API (light base, one
  accent colour, 1200x800). `renders/` holds the full-size renders; `site/thumbs/` the compressed copies.

## Rebuild

```
python3 -I generate.py template.html catalog.csv renders "https://docs.google.com/spreadsheets/d/<sheet>" site/index.html thumbs/ --site --base https://hey.mapbox.com/data-catalogV2/
```

`generate.py` recompresses `renders/*.jpg` into `renders/small/` on the first run; copy those into `site/thumbs/`.
To re-render images you need the Mapbox CLI signed in (`mapbox static get-image`): `python3 -I thumbs_v3.py catalog.csv renders --render`.

## Design notes

Each entry is the map image (top-aligned with the text), the name, one sentence, and a call-to-action button: Read the docs,
and Contact sales where a contract is needed. The details are always shown: how it ships (linked to its API reference),
licensing, APIs, format, update cadence, price, uses, source and license terms, and any layers inside.
Domains with a single dataset read as one entry. Everything is in the HTML, so agents and search engines read it without
running the script.
