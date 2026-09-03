# Nearby 10-Minute Drive Map · 景点周边十分钟车程地图

[![CI](https://github.com/96528025/nearby-10min-map/actions/workflows/ci.yml/badge.svg)](https://github.com/96528025/nearby-10min-map/actions/workflows/ci.yml)

A deployed React + FastAPI geospatial application that turns a confirmed
destination into a model-estimated 10-minute driving area, shows nearby
visitor facilities, and makes data provenance and degraded states visible.

**Live demo:** [nearby-10min-map.onrender.com](https://nearby-10min-map.onrender.com)

The normal boundary is the routed Valhalla isochrone itself, including every
returned polygon component and interior hole. It is a free-flow model based on
road-network costs, not a measurement of traffic or actual travel time.

## What this project demonstrates

| Area | Implementation |
|---|---|
| Product flow | Search a destination, confirm one geocoding candidate, then explore eight bilingual facility categories on an interactive map |
| Frontend | React 19, TypeScript, React Leaflet, accessible status messaging, and an explicit state machine for loading, success, empty, degraded, and error paths |
| Backend | FastAPI with cache-first geocoding, in-process request coalescing, bounded upstream calls, atomic JSON cache writes, and two-phase area responses |
| Geospatial contract | Attempted road snap → Valhalla `auto` isochrone → the same Polygon/MultiPolygon used for display, POI filtering, enrichment, and consistency checks |
| Failure handling | A labelled fixed-radius fallback when routing fails, OSM-only terminal results when enrichment is unavailable, and visible warnings for incomplete coverage |
| Delivery | Multi-stage Docker image, non-root Python runtime, Render Blueprint, and GitHub Actions for Python, TypeScript, component, build, and browser checks |

The bundled Apple Park view loads immediately from committed JSON and makes no
geocoding, routing, or facility API request. A submitted search exercises the
live API path. The free Render service can be slow after an idle period, so the
UI shows a delayed wake-up notice and provides retryable request deadlines.

## Architecture

```text
Browser
├── React + TypeScript + React Leaflet
│   ├── /data/*        committed Apple Park snapshot
│   ├── /api/geocode   explicit-submit place search
│   ├── /api/area      boundary + facilities, then polling
│   └── OSM raster tiles with visible attribution
│
└── FastAPI
    ├── /api/health    local liveness; no upstream request
    ├── /api/geocode   file cache → single flight → 1 s start interval
    │                  → Nominatim + Photon
    ├── /api/area      four-decimal coordinate cache key
    │   ├── phase 1    road snap → Valhalla isochrone → OSM facilities
    │   └── phase 2    background Overture merge → atomic cache replace
    ├── /data          committed JSON snapshot
    └── /              built Vite application, mounted last
```

The Vite development server proxies the same absolute `/api/*` and `/data/*`
paths used in production. The deployed image therefore needs neither a
hard-coded backend URL nor CORS configuration.

### Live request lifecycle

1. `/api/geocode` runs only after form submission. Normalized identical misses
   share one in-process request, and cached results bypass the global one-second
   upstream-start interval.
2. The user confirms a candidate instead of letting a fuzzy geocoder silently
   choose the destination.
3. `/api/area` attempts to snap the point to a public drivable road, requests a
   10-minute Valhalla `auto` isochrone with `denoise=0.3`, and queries Overpass
   over the geometry's complete bounding box.
4. The first usable response contains the boundary and OSM facilities with
   `status="enriching"`. One background enrichment flight per coordinate key
   and process merges qualifying Overture Places.
5. The client polls with bounded exponential backoff until `complete` or
   `osm_only`. Abort signals and request-generation checks prevent older
   searches from overwriting newer ones.

The API's terminal states are deliberately distinct:

- `complete`: Overture enrichment finished; facilities combine OSM and Overture
  when both lookups succeeded, or identify Overture-only coverage when the OSM
  lookup failed.
- `osm_only`: Overture failed or was disabled; the boundary and any OSM results
  remain available.
- An Overpass failure does not remove the boundary; phase one returns an empty,
  schema-complete OSM collection with a coverage warning, and Overture can still
  populate the terminal result.

## Current boundary contract

### Normal path: `routed_isochrone`

`pipeline.isochrone_geometry` combines all polygonal features returned by
Valhalla into one GeoJSON `Polygon` or `MultiPolygon`. No `coordinates[0]`
shortcut is used: disconnected components and interior holes are preserved.
React Leaflet renders that geometry and fits the viewport around its full
bounds.

That exact geometry object is also used to:

- define the Overpass and Overture query envelope;
- filter OSM facilities;
- filter and merge Overture Places; and
- re-check response consistency before totals are reported.

The consistency check can catch a code path that bypasses filtering. It does
not independently validate drive time because it reuses the display predicate.

### Routing failure: `nominal_radius_circle`

If the Valhalla isochrone request fails, the API discards any snapped point and
returns a fixed 3 km circle around the requested coordinates. The response says
that no road-network input produced the boundary, includes a visible warning,
and uses the same circle for display and filtering. This is the only current
mode that reports a radius.

### Retired representation

The former routed equal-area circle is not a current boundary mode. Legacy
cache entries carrying `routed_equal_area_circle`, or no mode at all, are
treated as misses and recomputed. The bundled Apple Park boundary was also
regenerated as the routed isochrone.

A preregistered five-location benchmark measured the retired circle against
Valhalla's own geometry: 9.1% macro false inclusion and 24.7% macro false
exclusion, failing the frozen acceptance rule. That result motivated the
migration; it does **not** validate real-world travel time. Likewise, the
current isochrone's zero error against itself is definitional, not an accuracy
claim. See [the decision record](docs/DECISIONS.md#d-2--boundary-representation-adopt-the-true-isochrone)
and [run of record](reports/accuracy/runs/20260729T082833Z_cfge03df09d_pland796c05b/report.md).

## Bundled demo data

The committed Apple Park snapshot is a deterministic startup view, not a live
query:

- `map/data/isochrone.json` is a recorded, unsnapped Valhalla response from
  2026-07-12.
- `map/data/boundary.json` is a 25.76 km² Polygon generated from that recorded
  response through the same boundary code used by the API.
- `map/data/facilities.json` contains 921 facilities across eight categories.
  It was rebuilt offline from 12,600 frozen benchmark candidates and filtered
  with the production predicate.
- `map/data/landmarks.json` contains six curated Apple Park landmarks.

Those checks establish artifact reproducibility and
dataset-to-display-boundary consistency. They do not establish facility
quality or real-world drive-time accuracy. Live searches may also differ from
the bundled view because they attempt road snapping and use current upstream
data.

## Run locally

Prerequisites: Python 3.11 and Node.js 24.15 or newer.

Install the backend and test dependencies:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
```

Start FastAPI:

```bash
.venv/bin/python -m uvicorn app:app --port 8642 --app-dir map/server
```

In a second terminal, start Vite:

```bash
cd web
npm ci
npm run dev
```

Open `http://localhost:5173`. To exercise the production-shaped single-service
path, build the frontend first and then use the same FastAPI command:

```bash
cd web
npm ci
npm run typecheck
npm run build
cd ..
.venv/bin/python -m uvicorn app:app --port 8642 --app-dir map/server
```

The application is also runnable as the deployed container shape:

```bash
docker build -t nearby-10min-map .
docker run --rm -p 10000:10000 nearby-10min-map
```

## Tests and CI

The current suite contains 266 deterministic checks:

| Suite | Count | What it covers |
|---|---:|---|
| pytest | 213 | Geometry components and holes, boundary/facility agreement, degradation, cache lifecycle, rate limiting, deduplication, provenance, licensing, and benchmark logic |
| Vitest + React Testing Library | 51 | State transitions, API errors, retries and timeouts, stale-response prevention, attribution, provenance labels, and Leaflet geometry rendering |
| Playwright | 2 | `enriching → complete` and `enriching → osm_only` browser flows |

Run the same checks as CI:

```bash
.venv/bin/pytest -rs

cd web
npm run typecheck
npm test
npm run build
npx --no-install playwright install chromium
npm run test:e2e
```

The Python suite blocks socket creation. Frontend tests mock fetches, and the
Playwright scenarios intercept API responses and map tiles while rejecting
unexpected external requests. Passing tests therefore do not depend on public
upstream availability.

GitHub Actions runs Python 3.11 and Node 24 jobs on pushes and pull requests.
The Render Blueprint uses `autoDeployTrigger: checksPass`; the multi-stage image
builds `web/dist`, installs the Python runtime separately, retains attribution
files, runs as a non-root user, and exposes `/api/health` as its health check.

## Configuration

The committed defaults are intentionally explicit:

| Variable | Default | Effect |
|---|---:|---|
| `ENABLE_OVERTURE` | `true` | Set `false` to make OSM-only results an explicit terminal mode |
| `OVERTURE_RELEASE` | `2026-08-19.0` | Pins live enrichment provenance |
| `NOMINAL_RADIUS_M` | `3000` | Fixed fallback radius used only when the routed isochrone cannot be produced |
| `UPSTREAM_USER_AGENT` | repository contact URL | Identifies backend requests to public services |
| `GEOCODE_TIMEOUT_SECONDS` | `15` | Per-geocoder timeout |
| `VALHALLA_LOCATE_TIMEOUT_SECONDS` | `5` | Per-road-snap locate timeout |
| `SNAP_TOTAL_TIMEOUT_SECONDS` | `20` | Total road-snap probing budget |
| `VALHALLA_ISOCHRONE_TIMEOUT_SECONDS` | `30` | Routed-boundary request timeout |
| `OVERTURE_PROCESS_TIMEOUT_SECONDS` | `600` | Background enrichment process timeout |

The pipeline also defines bounded Overpass and Overture HTTP timeouts, while
the Blueprint overrides the Overpass defaults. See [`render.yaml`](render.yaml)
and [`map/server/pipeline.py`](map/server/pipeline.py) for the complete
configuration surface.

## Honest limitations

- **The boundary is a model estimate.** Valhalla uses free-flow road costs and
  has no live or historical traffic. Rush hour, closures, parking, weather, and
  time spent leaving a campus or airport are absent.
- **Road snapping is consequential.** Large campuses and airports can produce
  substantially different isochrones depending on the selected origin and
  nearby drivable edge. The UI discloses that the bundled view is unsnapped.
- **The fallback is not routed.** `nominal_radius_circle` is a fixed-radius
  degraded mode and must not be interpreted as a 10-minute road-network area.
- **Public upstreams have no application SLA.** Photon, Nominatim, Valhalla,
  Overpass, Overture storage, and OSM tiles may be slow, limited, unavailable,
  or updated independently.
- **Facility coverage is incomplete and variable.** Results depend on source
  freshness, category mapping, confidence filtering, and heuristic
  deduplication. Counts are not a stable inventory.
- **Caches are opportunistic.** The free deployment has no persistent disk and
  no TTL. The area key uses coordinates rounded to four decimals, not the place
  name, so very close candidates can share the first cached label.
- **This is a portfolio-scale service.** The API has no authentication, user
  quota, durable job queue, or global concurrency budget for distinct area
  requests. Commercial traffic would require owned or contracted geocoding,
  routing, POI, and tile infrastructure.
- **Pinned Overture releases expire.** The code and Blueprint values must be
  updated together when a pinned public release rotates out.

## Evidence and audit trail

- [`docs/CURRENT_STATE_AUDIT.md`](docs/CURRENT_STATE_AUDIT.md) is a historical
  audit of the pre-upgrade 2026-07-29 codebase. Its equal-area-circle,
  single-file frontend, and no-test findings are not descriptions of current
  behavior.
- [`docs/DECISIONS.md`](docs/DECISIONS.md) records the benchmark decisions and
  later implementation status. Equal-area-circle references there describe a
  retired candidate retained for auditability.
- [`reports/accuracy/BENCHMARK_PLAN.md`](reports/accuracy/BENCHMARK_PLAN.md) and
  immutable run directories preserve the preregistered model-consistency
  benchmark.
- [`docs/ATTRIBUTION_AUDIT.md`](docs/ATTRIBUTION_AUDIT.md) records the original
  attribution findings and subsequent remediation.

## Data sources and license

| Source | Use | Note |
|---|---|---|
| [OpenStreetMap](https://www.openstreetmap.org/copyright) | Raster tiles and Overpass facilities | ODbL; visible attribution retained |
| [Valhalla public server](https://gis-ops.com/global-open-valhalla-server-online/) | Road snap and routed isochrone | Community service; no SLA |
| [Photon](https://photon.komoot.io) and [Nominatim](https://nominatim.org) | Geocoding candidates | Explicit-submit search with backend caching and rate limiting |
| [Overture Maps](https://overturemaps.org) | Optional facility enrichment | Places release and transformations recorded in generated metadata |

Code is available under the [MIT License](LICENSE). Map data is © OpenStreetMap
contributors under ODbL. POI results can include modified Overture Maps
Foundation and Foursquare Places data. Required notices and the Apache-2.0 text
are retained in [`NOTICE`](NOTICE) and [`LICENSES/Apache-2.0.txt`](LICENSES/Apache-2.0.txt).
