# Attribution / NOTICE Audit

**Date:** 2026-07-29 · **Scope:** Run 1 — audit and record only. No vendor
switching, no tile prefetching, no UI changes are made in this round.

This is an audit, not a from-scratch build: `map/index.html:116` already
attributes OpenStreetMap, Valhalla (FOSSGIS) and Overture Maps in the Leaflet
attribution control. The questions are whether that attribution is always
*visible*, whether the data artifacts record enough provenance, and whether the
Overture obligations are actually met for the themes and release in use.

---

## 1. Is the attribution always visible?

**Finding: no — it is occluded at short viewport heights.** This is a policy
issue, not a cosmetic one: the OSMF tile usage policy requires visible licence
attribution and states it must not be hidden beneath UI, behind toggles, or
off-screen.

The `.info-panel` is `position: absolute; top: 12px; right: 12px` with
`max-height: calc(100vh - 40px)` (and `calc(100vh - 30px)` under the
`max-width: 900px` media query, `index.html:19, 79`). Because the element does
not use `box-sizing: border-box`, its 28 px of vertical padding is added on top
of `max-height`, so the panel's bottom edge lands *below* `100vh - 12px`
whenever its content is tall enough to hit the cap — which the default Apple
Park view always is.

Measured in the browser against the running app:

| Viewport | Panel rect (l,t,r,b) | Attribution rect | Overlap |
|---|---|---|---|
| 1280 × 800 (desktop) | — | — | No |
| 375 × 812 (mobile) | — | — | No (≈25 px clearance) |
| **414 × 420** | 140, 12, 402, **430** | 0, **386**, 414, 420 | **Yes** |

At 414 × 420 the panel bottom (430 px) is past both the attribution control
(386–420 px) and the viewport itself (420 px), so the attribution is covered
*and* the panel is clipped. Landscape phones and short desktop windows land in
this range routinely.

Secondary observation at 375 px: `.search-box` computes to
`width: min(280px, calc(100vw - 330px))` = **45 px** (`index.html:78`). The
flex row cannot shrink below its button, so the search control overflows and
the button overlaps the info panel. Not an attribution problem, but it is in
the same layout defect family.

**Recorded for a later round** (no UI change in Run 1):
- Give `.info-panel` `box-sizing: border-box` and reserve the attribution
  strip, e.g. `max-height: calc(100vh - 56px)`.
- Verify at 414 × 420 and 375 × 667 that the panel never intersects
  `.leaflet-control-attribution`.

---

## 2. Do the data artifacts record provenance?

| Artifact | Generated time | Source named | Release/version | Verdict |
|---|---|---|---|---|
| `map/data/isochrone.json` | yes | yes (Valhalla FOSSGIS, costing, contour, free-flow disclosed) | no engine/graph version | Good, minor gap |
| `map/data/boundary.json` | yes | method string | n/a | OK |
| `map/data/facilities.json` | yes | "OpenStreetMap (Overpass API) + Overture Maps places" | **no Overture release** | **Gap** |
| `/api/area` responses | yes | same string | **no Overture release** | **Gap** |

**The Overture release is recorded nowhere in the shipped product.**
`pipeline.merge_overture` (pipeline.py:282) invokes the CLI without `-r`, so it
silently takes whatever "latest" was at run time. Two runs a month apart are
not comparable and neither can be reproduced, and the release is exactly what
determines which NOTICE applies (§3).

The Run 1 benchmark does this correctly: it pins `-r 2026-07-22.0` and records
the release in `results.json` and `poi_universe.json`. The production pipeline
should adopt the same in a later round.

---

## 3. Overture: obligations are per theme *and* per source record

The generic line "Data from Overture" in `README.md:64` is **not sufficient**.
Overture licences by theme, and within the Places theme by the source dataset
of each individual record.

Theme in use: **Places only** (`--type=place`, pipeline.py:283). Release used
by the benchmark: **`2026-07-22.0`**.

### This is not hypothetical — Foursquare records are actually ingested

Tallied from the cached `2026-07-22.0` Places response for the Half Moon Bay
envelope, counting only records that pass this project's own filters
(confidence ≥ 0.6, mappable category, has a name):

| Source dataset | Licence declared in the record | Kept records |
|---|---|---|
| Overture-signals | CDLA-Permissive-2.0 | 15002 |
| Overture | CDLA-Permissive-2.0 | 2290 |
| meta | CDLA-Permissive-2.0 | 1293 |
| **Foursquare** | **Apache-2.0** | **419** |
| BrightQuery | CDLA-Permissive-2.0 | 315 |
| Microsoft | CDLA-Permissive-2.0 | 202 |
| AllThePlaces | CC0-1.0 | 47 |
| DAC | CDLA-Permissive-2.0 | 14 |

(One bounding box only; every benchmark location shows the same mix. Each
GeoJSON feature carries a `sources[]` array with a per-entry `license` field,
which is how the table above was produced — it is a property of the data, not
an assumption.)

So the Places theme delivers **three different licences at once**, and the
Apache-2.0 subset is live in this project's output.

### What Apache-2.0 requires here

For the Foursquare-sourced subset the project must:

1. Include a copy of the **Apache License 2.0**.
2. Retain the Foursquare **NOTICE** — `Copyright 2024 Foursquare Labs, Inc.`
   Upstream publishes it at
   <https://opensource.foursquare.com/places-notice-txt/>.
3. **State that files were changed, and when.** This project unambiguously
   modifies the data: it filters by confidence, drops unmappable categories,
   deduplicates against OSM, subsets to a boundary, and rounds coordinates.
   The `generated_utc` field partially covers the "when", but nothing states
   the "changed" part.

CDLA-Permissive-2.0 and CC0-1.0 records do not add obligations beyond the
existing citation, but the required citation itself is
`Overture Maps Foundation, overturemaps.org`.

### Current state vs required

| Requirement | Status |
|---|---|
| Overture named in UI attribution | **Met** (`index.html:116`) |
| Citation "Overture Maps Foundation, overturemaps.org" | Partially — UI links overturemaps.org but does not use the citation string |
| Overture release recorded in outputs | **Not met** (§2) |
| Apache-2.0 licence copy included | **Not met** |
| Foursquare NOTICE retained | **Not met** |
| Statement that the data was modified + date | **Not met** ("when" only) |
| Per-source licence breakdown documented | **Not met before this audit**; the table above is the first record of it |

**To do in a later round** (no files added in Run 1 beyond this audit):
add `NOTICE` and `LICENSES/Apache-2.0.txt` at the repository root; record
`overture_release` in `facilities.json` and in `/api/area` responses; pin `-r`
in `pipeline.merge_overture`; add a one-line "modified from Overture Places
release X on DATE by filtering, deduplication and boundary subsetting" to the
data metadata and the page footer.

---

## 4. OpenStreetMap tiles — recorded accurately, not exaggerated

`map/index.html:114` loads `https://tile.openstreetmap.org/{z}/{x}/{y}.png`
directly in the browser.

What the OSMF tile usage policy actually says:

- **Permitted:** interactive viewing where the client requests only tiles for
  the current viewport; re-visits served from a local cache that honours the
  server's caching headers; normal browser behaviour with modest short-range
  look-ahead.
- **Prohibited:** bulk downloading or scraping; pre-seeding large areas or
  multiple zoom levels in advance; automated scans across wide bounding boxes;
  "download for offline use" / "save area for later" features; any background
  job fetching tiles the user is not actively viewing; sending
  `Cache-Control: no-cache` or similar; plain HTTP; generic library-default
  User-Agents.
- **Service level:** best-effort, explicitly **no SLA or guarantee**, and the
  Foundation may **block access without notice** if usage degrades the service.
- **Attribution:** visible "© OpenStreetMap contributors", conventionally
  bottom-right, and **not hidden beneath UI, behind toggles, or off-screen**.

**Assessment of this project's current usage:**

- The app does normal interactive Leaflet viewport fetching with no
  prefetching, no seeding and no offline packaging → **this is permitted use.**
- It uses HTTPS → compliant.
- It does not set `no-cache` and does not interfere with browser caching →
  compliant, and Run 1 deliberately leaves that behaviour alone, since honouring
  upstream cache headers is something the policy *requires*, not something to
  avoid.
- Attribution text is present but **can be occluded** (§1) → **this is the one
  live compliance defect**, and it is a policy requirement rather than a
  nicety.

**Correct conclusion:** compliant low-volume interactive use is allowed. The
risk is not that the project is violating the policy by using the tiles; it is
that a best-effort, no-SLA, blockable-without-notice service must not become an
**irreplaceable production dependency**. Static hosting does **not** remove
this dependency — the browser still requests tiles at view time (see
`DECISIONS.md`, D-1).

**Recorded for a later round:** make the tile URL and attribution string
configurable so a paid or self-hosted provider can be substituted without a
code change. **Run 1 implements no vendor switch and performs no prefetching,
pre-seeding, bulk download, proxy archiving or offline packaging.**

---

## 5. Other attribution observations

- `map/index.html:9-10, 109` load Leaflet.markercluster CSS and JS from unpkg
  **without SRI `integrity` attributes**, while Leaflet itself has them
  (`:7-8, 107-108`). Inconsistent, and a supply-chain gap rather than an
  attribution one.
- `fetch_facilities.py:75` still sends the User-Agent
  `apple-park-visitor-map (educational project)` while `pipeline.py:32` sends
  `visitor-area-map (educational project)`. Both identify the application
  rather than using a library default, so both satisfy the "identify yourself"
  requirement, but the stale one should be unified.
- Valhalla (FOSSGIS) and Nominatim/Photon are attributed in the UI and README.
  All are free public instances with their own usage policies; the README
  already warns that commercial deployment requires self-hosted or paid
  instances. That warning is accurate and should stay.

---

## 6. Summary of open items

| # | Item | Severity |
|---|---|---|
| 1 | Info panel occludes tile attribution at short viewports | **High** (explicit policy requirement) |
| 2 | Foursquare NOTICE + Apache-2.0 copy + "modified" statement absent, while Foursquare records are demonstrably ingested | **High** (licence obligation) |
| 3 | Overture release not pinned and not recorded in product outputs | Medium (reproducibility + determines which NOTICE applies) |
| 4 | Overture citation string not used verbatim | Low |
| 5 | markercluster assets lack SRI | Low |
| 6 | Tile URL not configurable; no fallback provider | Medium (risk, not a violation) |
| 7 | Stale User-Agent in `fetch_facilities.py` | Low |

None of these are fixed in Run 1 by design; this round records them.

**Sources:**
[Overture attribution & licensing](https://docs.overturemaps.org/attribution/) ·
[Foursquare places NOTICE](https://opensource.foursquare.com/places-notice-txt/) ·
[OSMF tile usage policy](https://operations.osmfoundation.org/policies/tiles/)
