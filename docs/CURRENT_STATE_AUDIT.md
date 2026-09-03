# Historical Current-State Audit (2026-07-29)

> **Historical snapshot, not current architecture.** This audit describes the
> repository as it existed on 2026-07-29, before the React frontend, automated
> test suites, CI, deployment files, public-service safeguards, and routed
> isochrone migration were implemented. References below to an equal-area
> circle, `map/index.html`, or missing tests are preserved as findings about
> that audited snapshot. For current behavior, start with the repository
> [`README.md`](../README.md) and source at `HEAD`.

**Audit date:** 2026-07-29
**Snapshot audited:** the initial repository state before history cleanup (the
repository had no other history at audit time)
**Method:** every statement below was checked against source code or against a
live run of the application on this machine. Where a claim could not be
reproduced, it is marked as such rather than repeated.

**Maintenance note (2026-08-30):** before Phase 0, a one-time history cleanup
removed an editor-specific local launch configuration. Commit IDs therefore
changed. The benchmark provenance mapping is recorded in `DECISIONS.md`;
immutable benchmark run artifacts were not edited.

This document records *what was true at the audited snapshot*, not what should
be true now. It deliberately did not fix the README because README changes
were out of scope for Run 1. Discrepancies were listed in §9 for later work.

---

## 1. How the application was run

Clean environment, following the README verbatim:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app:app --port 8642 --app-dir map/server
```

Result: **works**. Python 3.14.6, FastAPI 0.140.13, uvicorn 0.51.0. The server
started, served the page, and both API endpoints returned live data.

Observed live behaviour:

| Step | Result |
|---|---|
| `GET /` | 200, Apple Park view renders from static `map/data/*.json` |
| `GET /api/geocode?q=Stanford University&bias_lat=…&bias_lon=…` | 200, 6 candidates, Nominatim exact match first |
| `GET /api/area?lat=37.4313138&lon=-122.1693654&name=Stanford University` | 200 in **~8 s**, `status:"enriching"`, 569 facilities, snap distance 25 m, radius 3921 m, isochrone area 48.3 km² |
| same URL polled after Overture phase | `status:"complete"`, **1270** facilities |

So the end-to-end product flow is real and functional. This is worth stating
plainly, because several *claims about* that flow do not hold up (§9).

### Transitive dependency note

`requirements.txt` lists only `fastapi`, `uvicorn[standard]`, `overturemaps`.
`shapely`, `pyarrow`, `numpy` are installed **transitively** via `overturemaps`
and are not declared. Nothing in the app currently imports them, so this is
latent rather than broken — but any code that starts relying on `shapely`
(including the Phase B benchmark) must declare it explicitly.

---

## 2. Actual user flow (from code, not README)

```
Page load (index.html:398 loadApplePark)
  └─ fetch data/boundary.json, data/facilities.json, data/landmarks.json
     → renders the FROZEN Apple Park snapshot generated 2026-07-12.
       No backend call is made for the default view.

Search (index.html:320 doSearch)
  └─ GET /api/geocode  → pipeline.geocode (Nominatim exact + Photon fuzzy,
                          merged, deduped by (name, lat3dp, lon3dp), capped 6)
     └─ user clicks one candidate  (this is the "user confirms location" step)
        └─ GET /api/area (app.py:74)
           ├─ cache hit  → return cached JSON immediately
           └─ cache miss →
              1. pipeline.snap_to_drivable   Valhalla /locate, ring probe
              2. pipeline.fetch_isochrone    Valhalla /isochrone, 10 min, auto
              3. pipeline.boundary_from_isochrone → EQUAL-AREA CIRCLE
              4. pipeline.osm_facilities     Overpass over the CIRCLE's bbox
              5. pipeline.verify_inside      (see §6 — this gate cannot fire)
              6. write cache, return status:"enriching"
              7. background thread: pipeline.merge_overture (Overture CLI
                 download over the CIRCLE's bbox) → status:"complete"
           frontend polls the same URL every 6 s until complete / osm_only
```

Two things a reader of the README would not expect:

- **The default Apple Park view never touches the backend.** It is static JSON
  committed to the repo. The app is already, for its default view, a static
  site.
- **The boundary used for everything downstream is the circle, not the
  isochrone.** The isochrone is computed, its area is read, and then the
  isochrone is discarded. It is never stored per-request and never rendered.

---

## 3. Architecture

**Frontend** — single file `map/index.html` (401 lines), no build step.
Leaflet 1.9.4 + Leaflet.markercluster 1.5.3, both from the unpkg CDN.
Leaflet's CSS/JS carry SRI `integrity` hashes (index.html:7-8, 107-108);
**markercluster's CSS and JS do not** (index.html:9-10, 109). Bilingual
zh/en labels throughout. Eight category layers with per-category toggles;
`dining` is off by default on first render (index.html:227).

**Backend** — `map/server/app.py` (114 lines), FastAPI. Two GET endpoints plus
`StaticFiles` mounted at `/` serving the `map/` directory. Threading for the
Overture phase. No auth, no rate limiting, no CORS config, no health endpoint,
no structured logging.

**Pipeline** — `map/server/pipeline.py` (335 lines), pure functions, imports
three helpers from `map/scripts/` via `sys.path` injection (pipeline.py:21-28).

**CLI scripts** — `map/scripts/`: `fetch_isochrone.sh` → `make_boundary.py` →
`fetch_facilities.py` → `merge_overture.py` → `verify.py`. These regenerate the
Apple Park static snapshot.

---

## 4. External data sources and service dependencies

| Service | Endpoint | Called from | When | Failure handling |
|---|---|---|---|---|
| Nominatim | `nominatim.openstreetmap.org` | pipeline.py:61 | geocode | `try/except: pass` — silently skipped, Photon alone continues |
| Photon | `photon.komoot.io` | pipeline.py:75 | geocode | `try/except: pass` — silently skipped |
| Valhalla `/locate` | `valhalla1.openstreetmap.de` | pipeline.py:112 | road snapping | returns `None` → falls back to unsnapped point |
| Valhalla `/isochrone` | same host | pipeline.py:163 | boundary | raises → HTTP 502 |
| Overpass | `overpass-api.de` | fetch_facilities.py:78 | OSM POIs | 4 attempts, 30/60/90 s backoff on 429/504 only |
| Overture | `overturemaps` CLI subprocess | pipeline.py:282 | POI enrichment | 600 s timeout; on any failure → `status:"osm_only"`, phase-1 data still served |
| OSM tiles | `tile.openstreetmap.org` | index.html:114 | every map view | browser default (broken tiles) |

**All six network services are public free instances with no SLA.** The
`overturemaps` CLI additionally downloads Parquet from cloud storage inside a
request-scoped background thread.

Stale identifier: the Overpass `User-Agent` still reads
`apple-park-visitor-map (educational project)` (fetch_facilities.py:75) while
the pipeline's is `visitor-area-map (educational project)` (pipeline.py:32).

---

## 5. Caching and degradation

**Cache** — `map/cache/<lat:.4f>_<lon:.4f>.json`, whole-response JSON, written
by `app.py:104` and `app.py:62/68`. Gitignored. Two defects:

1. **Key ignores the requested name.** `slug_for` (app.py:32) uses coordinates
   only. Two attractions rounded to the same 4 dp (~11 m) share a cache entry,
   and the stored `name` is whichever request arrived first.
2. **No expiry, no versioning.** Nothing invalidates an entry when the pipeline
   changes. A cache written by today's code is served forever by tomorrow's.

**Degradation ladder** (this part is genuinely well built):

- Nominatim down → Photon-only geocoding.
- Photon down → Nominatim-only geocoding.
- Both down → empty candidate list, front end shows "No results".
- Valhalla `/locate` down → no snapping, isochrone from the raw geocoded pin.
- Valhalla `/isochrone` down → HTTP 502, no map.
- Overpass down → HTTP 502, no map.
- Overture down/slow → `status:"osm_only"`, OSM-only map still served. The
  front end surfaces this honestly (index.html:381-383).

**Restart recovery** — `app.py:81-85` re-launches the Overture thread for any
cached entry still marked `enriching`. Reasonable, but if enrichment crashes
the process repeatedly the entry is retried on every request forever.

---

## 6. The boundary gate does not do what it is documented to do

README methodology item 7 says *"强制边界校验：任何设施出界即整体报错"*
(hard boundary check: any facility outside the boundary aborts the whole run).

`pipeline.verify_inside` (pipeline.py:328-335) does test every facility with
`point_in_polygon` against the boundary and raises `AssertionError` on failure.
But every facility reaching it was already filtered by the *same predicate*
against the *same geometry*:

- `osm_facilities` skips anything failing `point_in_polygon` (pipeline.py:240)
- `merge_overture` skips anything failing `point_in_polygon` (pipeline.py:302)

**The assertion is therefore structurally incapable of firing.** It is a
tautology, not a check. It has never caught anything and never can.

This matters beyond tidiness. It is the first of several places where the
project validates a thing against itself and reads the result as evidence:

| Apparent validation | What it actually tests |
|---|---|
| `verify_inside` "hard gate" | that a filter filtered (always true) |
| `scripts/verify.py` landmark check | that 6 hand-picked points sit inside a circle |
| README "8 bearings, 9.5–12.0 min" | Valhalla `/route` vs Valhalla `/isochrone` — same graph, same costing model |

None of these is evidence about ten-minute drive-time accuracy. The first two
are not evidence about anything external at all. The third is a *model
self-consistency* check, which is a legitimate thing to measure but must never
be reported as real-world accuracy.

---

## 7. Geometry defects

**7.1 — Only the first ring of the first feature is used.**
Both `pipeline.boundary_from_isochrone` (pipeline.py:172) and
`make_boundary.py` (line 23) read:

```python
ring = iso["features"][0]["geometry"]["coordinates"][0]
```

This silently discards (a) any additional polygon of a `MultiPolygon`, and
(b) every hole. The equal-area radius is then computed from a partial area.
For the committed Apple Park isochrone this happens to be harmless — it is a
single-ring `Polygon` with 431 vertices (verified). For other locations it is
unverified, and airports/campuses are exactly the shapes that produce
disconnected components and holes. Phase B measures this on real responses.

**7.2 — Hardcoded longitude scale.**
`make_boundary.py:18` and `merge_overture.py:108` hardcode
`M_PER_DEG_LON = 88000` "at 37.3°N". The correct value there is
`111320·cos(37.3°) ≈ 88555`, a 0.63 % error. Recomputing the committed Apple
Park isochrone area with the correct scale gives **25.76 km²** rather than the
stored **25.61 km²** — so the committed `radius_m: 2855` is slightly
understated. `pipeline.py` does this correctly (`m_per_deg_lon(lat)`,
pipeline.py:37), but it *imports* `merge_overture.same_place`, so the
cross-source dedup distance for every arbitrary location is still computed
with Cupertino's longitude scale. Negligible in the Bay Area, wrong anywhere
else.

**7.3 — Shoelace closing edge.** `zip(pts, pts[1:])` omits the last→first
edge. Verified non-issue: GeoJSON rings are explicitly closed and the
committed ring satisfies `first == last`.

**7.4 — Area computed in a local flat approximation, not a projected CRS.**
Acceptable at this scale, but undocumented and unverified. Phase B uses a
proper projected CRS and reports the difference.

---

## 8. Implemented vs not implemented

**Implemented and verified working:**

- Arbitrary-attraction search with user-confirmed geocoding candidates
- Dual geocoder merge (Nominatim + Photon) with view bias
- Road snapping with outward ring probing
- Valhalla 10-minute `auto` isochrone
- Equal-area circle boundary
- Overpass POI fetch, 8 categories, named-only
- Overture merge with confidence ≥ 0.6 and three-rule cross-source dedup
- Two-phase loading with front-end polling and honest failure messaging
- Disk cache with mid-enrichment restart recovery
- Bilingual UI, category toggles, marker clustering, "Open in Google Maps"

**Not implemented (regardless of what the README implies):**

- Any automated test of any kind (no test file exists in the repository)
- CI, linting, type checking
- Rate limiting, auth, request budgets, health checks, structured logs
- Deployment of any kind; no Dockerfile, no platform config
- Telemetry / analytics
- Self-hosted routing or geocoding instances (README Roadmap, correctly marked)
- Multi-anchor isochrone intersection (README Roadmap, correctly marked)
- Trip-brief export (README Roadmap, correctly marked)
- Any storage or rendering of the true isochrone for searched locations
- Any accuracy measurement that is reproducible from this repository (§10)

---

## 9. README vs code — every discrepancy found

| # | README says | Code / data actually | Severity |
|---|---|---|---|
| 1 | Methodology 7: "任何设施出界即整体报错" | The gate is a tautology and cannot fire (§6) | **High** — presented as the project's core safety property |
| 2 | Methodology 4: "Apple Park 实测圆边界 8 个方向车程 9.5–12.0 分钟" and the same line in `boundary.json.metadata.calibration` and on-page (index.html:300) | No script, no stored response, no raw output in the repo produces this. Not reproducible. It is also a Valhalla-vs-Valhalla check, not a real-world measurement (§6) | **High** — it is the sole quantitative accuracy claim |
| 3 | Sprint 2.5: "接入 Overture（366→942 设施）" | Committed `facilities.json` totals **833** (547/50/60/29/61/54/2/30) | Medium — headline number does not match shipped data |
| 4 | `map/scripts/` is "同一套逻辑的命令行版本" (CLI version of the same logic) | It is not the same logic: the CLI path has **no road-snapping step at all**, hardcodes the Apple Park centre, and uses the wrong longitude constant (§7.2). Only `pipeline.py` snaps | Medium |
| 5 | Quick Start: default view is "预置的、经人工核实的数据" (pre-verified, human-verified data) | The only verification that exists is `scripts/verify.py`, which point-in-polygon-checks **6 landmarks** — not the 833 facilities. No human verification of the facility list is recorded | Medium |
| 6 | Sprint 1: "6 个精选地标逐个用路由引擎实测车程（13.3 分钟的 De Anza College 等 5 个候选被验证淘汰）" | `landmarks.json` holds 6 landmarks with `drive_min`. The 5 rejected candidates and the 13.3-minute figure exist nowhere in the repo | Low — plausible but unreproducible |
| 7 | Architecture diagram step 5: "OSM Overpass（同步，先出图）+ Overture Maps（后台补全，~1 分钟）" | Correct, but Overture took **longer than the stated ~1 min** for Stanford in the live run | Low |
| 8 | Data-source table lists Overture as "开放数据，含 Meta/Microsoft/Foursquare 贡献" | No Overture **release version** is recorded anywhere in code or in any data artifact, and no NOTICE requirement is captured. See `ATTRIBUTION_AUDIT.md` | Medium (licensing) |
| 9 | `facilities.json.metadata.filter` = `"named facilities inside the 10-min drive isochrone"` (also fetch_facilities.py:133) | The filter is the **circle**, not the isochrone. The shipped data artifact mislabels its own provenance | **High** — this is the isochrone/circle conflation baked into the data itself |
| 10 | README Quick Start creates `.venv` in the project root | The original repository included a non-portable editor launch configuration. Before Phase 0, it was removed from the project history rather than retained as repository configuration. The cleanup changed commit IDs; the benchmark provenance mapping is recorded in `DECISIONS.md` | **Resolved before Phase 0** |

Additionally, `fetch_facilities.py`'s own docstring says "inside the circular
boundary" while the metadata string it writes says "inside the 10-min drive
isochrone" — the file contradicts itself.

---

## 10. Which accuracy claims are reproducible today

**Reproducible from this repository:** none.

| Claim | Reproducible? | Why |
|---|---|---|
| "circle has the same area as the routed isochrone" | **Partially** — recomputable from committed `isochrone.json`, and it does *not* reproduce exactly: correct longitude scale gives 25.76 km², stored value is 25.61 km² (§7.2) | |
| "9.5–12.0 min across 8 bearings" | **No** | No script, no stored Valhalla responses, no bearings list, no timestamped output |
| "366 → 942 facilities" | **No** | Shipped data has 833; no run log |
| "every facility is point-in-polygon verified" | **Yes, but vacuous** | True and meaningless (§6) |
| "within a 10-minute drive" (the product promise) | **No** | Nothing in the repo measures travel time against anything external to Valhalla |

This is the gap Phase B exists to close — and Phase B can only close part of
it. Phase B establishes agreement with **Valhalla's own free-flow model**.
Real-world ten-minute accuracy would require GPS traces, historical traffic, or
field sampling, none of which this project has.

---

## 11. Current POI fetch extent (input to Phase B)

`pipeline.osm_facilities` (pipeline.py:216-218) and `pipeline.merge_overture`
(pipeline.py:278) both compute their query bbox with `_bbox(geometry)`
(pipeline.py:209-213), where `geometry` is **the circle**. `_bbox` reads
`coordinates[0]` — the circle's single ring — and returns its min/max lat/lon.

So the current POI universe is: **the axis-aligned bounding box of the
equal-area circle, then filtered to points inside that circle.**

The consequence for benchmarking is decisive and is why the existing dataset
cannot be reused:

> Any POI that lies inside the true isochrone but outside the circle was never
> fetched. Computing a POI false-exclusion rate on the existing dataset would
> return **structurally zero**, no matter how bad the circle is.

Phase B must therefore re-fetch over a union envelope covering the isochrone,
all formal candidate boundaries, and a search cap — not over any single circle.

---

## 12. Missing tests, monitoring, deployment

**Tests:** none. No `tests/`, no pytest, no fixtures, no recorded API
responses. The functions with the most subtle logic — `point_in_polygon`
(holes, MultiPolygon), `same_place` (three merge rules), `addr_key` (regex
normalisation), `near_duplicate` — are entirely uncovered.

**Monitoring:** none. Failures surface only as tracebacks on stdout
(`app.py:64`) and as HTTP 502.

**Deployment:** none. Running the app requires a local venv and a local port.
At audit time, an editor-specific launch configuration was machine-specific
and broken for any other user; it was removed from the project history before
Phase 0.

---

## 13. Known risks

**Technical**

1. Six unthrottled public-API dependencies, no SLA, no self-hosted fallback.
2. `coordinates[0]` truncation (§7.1) — unquantified until Phase B.
3. Unbounded background threads: one `/api/area` miss = one thread running an
   `overturemaps` subprocess for up to 600 s. No queue, no concurrency cap. A
   handful of concurrent misses will exhaust memory or the process table.
4. Cache has no expiry and no schema version (§5).
5. `map/cache/` grows without bound; ~197 KB for one Stanford entry.
6. Two CDN assets without SRI (§3).
7. The frontend polls every 6 s indefinitely if the backend never reaches a
   terminal status.

**Product**

1. The central promise — "10-minute drive" — has no evidence behind it that is
   independent of the routing engine that drew the shape.
2. The circle is presented to users as the area of record. Every facility list,
   count, and popup ("位于约 10 分钟车程范围内") is scoped to the circle while
   the copy says isochrone.
3. Free-flow only: at rush hour the true area is materially smaller. Disclosed
   in the UI, but the facility list is not adjusted.
4. POI completeness sits between OSM and Google. Disclosed.
5. An absolute local filesystem path is published in a public repository.

---

## 14. Summary

The application works, the degradation design is thoughtful, and the two-phase
loading is well executed. The problem is not the engineering — it is that the
project's stated accuracy guarantees are either tautological (§6), not
reproducible (§10), or describe a geometry the code does not actually use (§9
items 1, 9). Run 1's benchmark exists to replace all of them with one number
that can be recomputed on demand, and to be explicit about the one thing that
number still cannot tell us.
