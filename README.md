# Nearby 10-Minute Drive Map · 景点周边十分钟车程地图

[![CI](https://github.com/96528025/nearby-10min-map/actions/workflows/ci.yml/badge.svg)](https://github.com/96528025/nearby-10min-map/actions/workflows/ci.yml)

Type a destination, confirm which place you meant, and see the area a car can
reach from it in about ten minutes, with restaurants, hotels, parks and other
visitor facilities inside that area. The boundary is the road-network isochrone
returned by the Valhalla routing engine, drawn exactly as returned, not a circle
standing in for it.

**Live demo:** [nearby-10min-map.onrender.com](https://nearby-10min-map.onrender.com)
(free tier; the first search after an idle period can take about a minute to
wake the service, and the page says so while it waits).

Stack: React 19 + TypeScript + React Leaflet; FastAPI; one Docker image on
Render; GitHub Actions running pytest, Vitest and Playwright.

## The part worth reading

"10-minute drive" is a model output, and an earlier version of this app
approximated that output with a circle of equal area. Rather than assume the
circle was close enough, the project measured it.

**Method.** Five Bay Area destinations with different road-network shapes
(corporate campus, university, airport, dense downtown, coastal town). For
each, the shipped circle was scored against Valhalla's own 10-minute isochrone
using every named facility in the area: *false inclusion* (shown but not
reachable under the model) and *false exclusion* (reachable but hidden).
Thresholds were frozen before any request was made; the plan file is
SHA-256-hashed into every run; a preflight manifest is written before the
first network call; run directories are immutable.

**Result.** The circle scored 9.1% macro false inclusion and 24.7% macro false
exclusion (11.0% / 23.7% micro) against limits of 10% and 20%. Verdict: not
fit for purpose. About one facility in four that the model says is reachable
was not being shown; at the airport site the rates were 23.1% / 46.4%.

**Change.** The circle was retired. The API now returns Valhalla's polygon
(Polygon or MultiPolygon, every component and interior hole kept) and uses
that single geometry object for display, the facility filter, the Overture
merge and a final consistency check. The bundled startup view was regenerated
through the same code.

**A bug the benchmark surfaced.** The old code read
`features[0].geometry.coordinates[0]`, silently dropping extra polygon
components and all holes. None of the five sample locations triggered it, and
the report says exactly that: latent, not active, at this sample. It is fixed
on both the build-time and runtime paths and covered by polygon, hole and
multipolygon fixtures.

**What it does not show.** The benchmark measures agreement with Valhalla's
free-flow model, not real-world travel time; the isochrone's 0% / 0% score
against itself is definitional. One more diagnostic from the same run: snapping
the origin to the nearest public road changed the modelled area 3.57x at Apple
Park and 106x at San José airport, which bounds how precisely any boundary can
mean "ten minutes".

[Run of record](reports/accuracy/runs/20260729T082833Z_cfge03df09d_pland796c05b/report.md) ·
[preregistered plan](reports/accuracy/BENCHMARK_PLAN.md) ·
[decision record](docs/DECISIONS.md#d-2--boundary-representation-adopt-the-true-isochrone)

## How a search flows

```text
Browser (React + TypeScript + React Leaflet)
├── /data/*.json    committed Apple Park snapshot, no upstream calls
├── /api/geocode    explicit-submit place search
└── /api/area       boundary + facilities, then polling until a terminal state

FastAPI (one process)
├── /api/health     liveness only
├── /api/geocode    file cache → one in-flight request per normalised query
│                   → ≥ 1 s between upstream starts → Nominatim + Photon
├── /api/area       cache key = coordinates rounded to 4 decimals
│   ├── phase 1     snap to a public road → Valhalla auto isochrone (denoise 0.3)
│   │               → OSM facilities via Overpass → respond "enriching"
│   └── phase 2     background thread merges Overture Places → atomic cache replace
└── /               built Vite app, mounted last
```

1. **Geocode on submit only.** Queries are NFKC-normalised, whitespace-collapsed
   and case-folded into a cache key. Hits return at once; identical misses
   share one upstream request; distinct misses start at most one upstream call
   per second.
2. **The user confirms a candidate.** A fuzzy geocoder never silently picks the
   destination.
3. **Phase 1.** The point is snapped to the nearest motorway-to-residential
   class road, probing outward in 500 m rings up to 2 km for campuses and
   airports whose pin sits far from any public road. Overpass is queried over
   the polygon's full bounding box and results are filtered point-in-polygon.
4. **Phase 2.** One background flight per cache key merges Overture Places
   (confidence >= 0.6, deduplicated against OSM) and atomically replaces the
   cache entry. If the worker died mid-flight, the next request for that key
   resumes the work.
5. **Terminal states are explicit.** `complete` (OSM + Overture, or Overture
   alone if Overpass was down), `osm_only` (Overture failed or disabled), or a
   labelled `nominal_radius_circle` fallback of 3 km when routing itself fails.
   A failed Overpass lookup keeps the boundary and returns a schema-complete
   empty collection with a coverage warning.

Every response carries its provenance: method, costing, denoise, the
free-flow assumption, source URLs, Overture release and dedup rules.

**Frontend.** A typed reducer with nine states (`idle`, `geocoding`,
`candidates`, `empty`, `loadingArea`, `enriching`, `complete`, `osmOnly`,
`error`) drops events for a candidate that is no longer selected, so a slow
older search cannot overwrite a newer one. Each search gets an
`AbortController` and a generation number. Polling backs off from 2 s to a
15 s ceiling and stops after 3 consecutive failures or 5 minutes, always
keeping the last good map on screen with a reason. A wake-up notice appears
after 5 s; a phase-1 request is abandoned and offered for retry after 150 s.
Status text is in an `aria-live` region; tile attribution is always visible.

## Bundled startup data

The page opens on a committed Apple Park snapshot so it works before any
upstream is contacted: a Valhalla response recorded 2026-07-12
(`map/data/isochrone.json`), the 25.76 km² Polygon built from it by the same
function the API uses (`boundary.json`), 921 facilities in 8 categories
rebuilt offline from the benchmark's 12,600-point frozen POI universe
(`facilities.json`, whose metadata records the run id and universe SHA-256),
and 6 curated landmarks.

## Run locally

Prerequisites: Python 3.11 and Node.js 24.15 or newer.

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m uvicorn app:app --port 8642 --app-dir map/server
```

In a second terminal:

```bash
cd web
npm ci
npm run dev        # http://localhost:5173, proxies /api and /data to :8642
```

Production shape (FastAPI serves the built bundle; no CORS, no backend URL in
the client):

```bash
cd web && npm run typecheck && npm run build && cd ..
.venv/bin/python -m uvicorn app:app --port 8642 --app-dir map/server
```

Or the deployed container:

```bash
docker build -t nearby-10min-map .
docker run --rm -p 10000:10000 nearby-10min-map
```

## Tests and CI

266 checks, all runnable offline:

| Suite | Count | Covers |
|---|---:|---|
| pytest | 213 | Isochrone components and holes, boundary/facility agreement, degraded modes, cache lifecycle and legacy-entry migration, rate limiting and coalescing, deduplication, provenance fields, licence files, benchmark scoring |
| Vitest + React Testing Library | 51 | Reducer transitions, API error shapes, retries and deadlines, stale-response rejection, attribution labels, Leaflet geometry rendering |
| Playwright | 2 | `enriching → complete` and `enriching → osm_only` in a real browser with every off-origin request intercepted |

The Python suite replaces `socket.socket` with a failing stub, so any test that
reaches a real network fails loudly; five recorded Valhalla responses under
`tests/fixtures/valhalla/` stand in for the routing engine.

```bash
.venv/bin/pytest -rs

cd web
npm run typecheck
npm test
npm run build
npx --no-install playwright install chromium
npm run test:e2e
```

GitHub Actions runs the Python 3.11 and Node 24 jobs on every push and pull
request with read-only repository permissions. Render deploys only when checks
pass (`autoDeployTrigger: checksPass`) and probes `/api/health`. The
Dockerfile builds `web/dist` in a Node stage, installs the Python runtime in a
separate slim stage, copies the NOTICE and Apache-2.0 texts into the image,
and runs as a non-root user.

## Configuration

| Variable | Default | Effect |
|---|---:|---|
| `ENABLE_OVERTURE` | `true` | `false` makes `osm_only` the terminal state |
| `OVERTURE_RELEASE` | `2026-08-19.0` | Overture Places release recorded in every enriched response |
| `NOMINAL_RADIUS_M` | `3000` | Fallback circle radius, used only when routing fails |
| `GEOCODE_TIMEOUT_SECONDS` / `VALHALLA_LOCATE_TIMEOUT_SECONDS` / `SNAP_TOTAL_TIMEOUT_SECONDS` / `VALHALLA_ISOCHRONE_TIMEOUT_SECONDS` / `OVERTURE_PROCESS_TIMEOUT_SECONDS` | `15` / `5` / `20` / `30` / `600` | Per-stage upstream budgets in seconds |

`UPSTREAM_USER_AGENT` and the Overpass / Overture HTTP timeouts are also
configurable; see [`map/server/pipeline.py`](map/server/pipeline.py) and the
overrides in [`render.yaml`](render.yaml).

## Scope decisions

- **The boundary is a model estimate.** Valhalla routes on posted speed limits
  with no live or historical traffic. Copy says "approximately 10 minutes" and
  the response metadata says why.
- **Road snapping is part of the answer.** Where a destination sits far from a
  public road, the snap point moves the modelled area more than the destination
  does. The app snaps, records the snap distance, and the bundled view
  discloses that it is unsnapped.
- **The fallback is labelled, not hidden.** `nominal_radius_circle` is a fixed
  3 km circle that discards the snapped point, carries a visible warning, and
  is the only mode that reports a radius.
- **One process, opportunistic caches.** Request coalescing, the geocode rate
  limiter and the enrichment single-flight are `threading.Lock` constructs
  inside one FastAPI process. Caches are JSON files on the container's
  ephemeral disk with no TTL. There is no authentication, quota or job queue,
  and the public upstreams (Nominatim, Photon, Valhalla, Overpass, Overture
  storage, OSM tiles) offer no SLA. Real traffic would need owned or contracted
  routing, geocoding and POI infrastructure.
- **Facility counts are not an inventory.** They depend on source freshness,
  category mapping, a 0.6 Overture confidence floor and heuristic
  deduplication (same name within 150 m for OSM; name and address rules across
  sources).
- **Pinned Overture releases rotate.** `OVERTURE_RELEASE` in code and in the
  Blueprint must move together.

## Repository map

| Path | Holds |
|---|---|
| `map/server/` | `app.py` (routes, two-phase area), `pipeline.py` (snap, isochrone, OSM, Overture), `geocode_cache.py` (cache, coalescing, rate limit) |
| `map/scripts/` | `verify.py` (shared geometry predicates) and the bundled-data build scripts |
| `web/src/` | `state/` (reducer, polling constants), `api/` (typed client), `components/AreaMap.tsx` |
| `tests/`, `web/src/**/*.test.*`, `web/e2e/` | pytest, Vitest, Playwright |
| `scripts/benchmark_accuracy.py`, `config/`, `reports/accuracy/` | Benchmark script, frozen inputs, preregistered plan, immutable runs |
| `docs/` | `DECISIONS.md` (evidence and rejected alternatives); `CURRENT_STATE_AUDIT.md` and `ATTRIBUTION_AUDIT.md` are historical audits of the 2026-07-29 codebase and do not describe current behaviour |

## Data sources and licence

| Source | Use | Note |
|---|---|---|
| [OpenStreetMap](https://www.openstreetmap.org/copyright) | Raster tiles and Overpass facilities | ODbL; attribution shown on the map |
| [Valhalla, FOSSGIS public server](https://gis-ops.com/global-open-valhalla-server-online/) (`valhalla1.openstreetmap.de`) | Road snap and 10-minute isochrone | Community service; free-flow costing |
| [Nominatim](https://nominatim.org) and [Photon](https://photon.komoot.io) | Geocoding candidates | Explicit submit, cached and rate-limited at the backend |
| [Overture Maps](https://overturemaps.org) | Optional facility enrichment | Release and transformations recorded in response metadata |

Code is under the [MIT License](LICENSE). Map data is © OpenStreetMap
contributors (ODbL). Facility results may include modified Overture Maps
Foundation and Foursquare Places data; the required notices and the
Apache-2.0 text are in [`NOTICE`](NOTICE) and
[`LICENSES/Apache-2.0.txt`](LICENSES/Apache-2.0.txt).
