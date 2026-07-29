# Benchmark Plan — preregistered

**Status:** frozen before any benchmark network request was issued.
**Applies to:** `scripts/benchmark_accuracy.py`, `config/benchmark_locations.yaml`.

This file is hashed (SHA-256) and the hash is written into every run's
`results.json` and `preflight.json`. If this file changes, the hash changes, a
new run ID is required, and previous run directories are never overwritten.

**On what a hash proves.** The SHA-256 is a *content-integrity anchor*: it shows
that the plan used by a given run is byte-identical to the plan published here.
It is **not** a timestamp and does not by itself prove the plan predates the
results. Ordering evidence comes from two other places: the `preflight.json`
manifest, which the script writes **before it opens any network connection**,
and — when the §10a identity check permits committing — the commit order in Git
history. The preflight manifest is written unconditionally, so the
preregistration chain survives even if commits are blocked.

---

## 1. What this benchmark can and cannot establish

The reference geometry is the 10-minute `auto` isochrone returned by Valhalla
for each location. Therefore:

> This benchmark measures how faithfully a simplified display boundary
> reproduces **Valhalla's model-estimated free-flow 10-minute service area**.
> It does **not** measure real-world ten-minute drive time. Establishing that
> would require GPS traces, historical traffic data, or field sampling, none of
> which this project has. Valhalla computes from posted speed limits and
> contains no live or historical traffic.

Every number this benchmark produces inherits that ceiling. No result may be
described as validating the product's real-world "10-minute drive" promise.

### Consequence for the true-isochrone candidate

The true isochrone scores exactly `FI = 0`, `FE = 0`, `IoU = 1` against the
reference. This is **definitional, not evidence** — it is being compared with
itself. Its actual merit is narrower and should be stated only as: it adds no
approximation error on top of the model's. It is *not* "100 % accurate".

---

## 2. Primary metric (decides the outcome)

POI-level rates, computed per location and per candidate boundary against the
single frozen POI universe:

```
FI = |POI in candidate AND NOT in isochrone| / |POI in candidate|
FE = |POI in isochrone AND NOT in candidate| / |POI in isochrone|
```

POI-level rates are primary because they are what a user actually experiences:
a facility shown that the model does not place within 10 minutes (FI), or a
facility the model does place within 10 minutes that the map never shows (FE).

**Undefined handling.** A zero denominator is reported as
`undefined / insufficient POIs`, never as `0%`, and is **excluded from every
average** — never coerced to zero.

**Aggregation.** Both are reported:

- **Macro** — locations equally weighted, undefined items excluded, and the
  participating location count `n` reported alongside every macro figure.
- **Micro (pooled)** — all numerators summed and all denominators summed, then
  divided once. Undefined only if the pooled denominator is zero.

**Category split.** FI and FE are additionally reported per facility category
(dining / health / education / lodging / shopping / fuel_ev / culture / parks),
because categories are not spatially distributed alike.

**Sparse locations.** A location must not pass merely because it had no wrong
POIs. Every rate is reported with its denominator, and evidence strength is
judged on the denominator, not on the rate alone (see P4).

### Interpretation constraint (binding)

If macro and micro diverge materially, the **only** admissible conclusion is
that locations are heterogeneous, and that this heterogeneity correlates with
POI count. It may **not** be read as "error concentrates in road-network type
X" or "different network types need different boundary rules". Run 1 has
exactly **one location per network type (n = 1)**, so type, POI density, and
individual site idiosyncrasy are perfectly confounded. Such a divergence is
recorded only as a reason to enlarge the sample in Run 2.

---

## 3. Pass/fail rule (preregistered, not revisable after results)

A candidate boundary is **FIT FOR PURPOSE** as the displayed boundary iff all
four hold:

| ID | Criterion |
|---|---|
| **P1** | macro-average FI ≤ **0.10** |
| **P2** | macro-average FE ≤ **0.20** |
| **P3** | no single location with a defined rate exceeds **FI 0.20** or **FE 0.35** |
| **P4** | at least **4 of 5** locations have *both* FI and FE defined with denominator ≥ **30**; otherwise the verdict is `INSUFFICIENT EVIDENCE`, not a pass |

### Why these numbers

- **FI is bounded tighter than FE (0.10 vs 0.20) deliberately.** FI is a
  *correctness* error — the map asserts a facility is within a 10-minute drive
  when the model says it is not. FE is a *completeness* error — a facility is
  missing. The README already discloses incompleteness as a known limitation of
  POI sourcing; it does not disclose that displayed items may be outside the
  area. Wrong is worse than missing here.
- **Why 0.10 for FI.** The UI hedges with "约 10 分钟 / ~10 min". A hedge can
  absorb boundary rounding. Above roughly one in ten displayed facilities being
  outside the modelled area, the word "approximately" is no longer describing
  rounding — it is describing a different area, and the copy becomes misleading.
  0.10 is the point at which I would stop being willing to ship "~10 min"
  unqualified.
- **Why the per-location cap P3 exists.** With n = 5, one catastrophic location
  at FI 0.30 alongside four at 0.05 still yields a macro average of 0.10 and
  would pass P1. P3 prevents a pass that is purely an artefact of averaging.
- **Why P4 exists.** Half Moon Bay is included precisely because it is sparse.
  Without P4, a candidate could "pass" on locations that contained almost no
  POIs to be wrong about.

**No threshold may be changed to make a result pass.** If a threshold turns out
to be badly designed, the only permitted remedy is a **new plan version, a new
hash, and a new run ID**, with the previous run retained and the change
disclosed in `docs/DECISIONS.md`.

---

## 4. Guardrail / explanatory metrics (do not decide anything)

**IoU** is the geometric summary statistic:

```
IoU = area(candidate ∩ isochrone) / area(candidate ∪ isochrone)
```

Symmetric-difference area is reported in km² and as a fraction of isochrone
area, as a more intuitive restatement of the same information.

### The equal-area identity (and why two areas are not two facts)

For the **equal-area circle**, with `area(circle) = area(isochrone) = A` and
intersection `I`:

```
area(circle − isochrone) = A − I = area(isochrone − circle)
```

The two difference areas are therefore **identically equal — one number, not
two independent pieces of evidence** — and `IoU = I / (2A − I)` is a
monotone function of that same number. The geometric side of an equal-area
candidate has exactly **one degree of freedom**.

This identity is used as a **correctness check on the geometry code**, not as a
finding:

> **Guardrail G1.** For the equal-area candidate,
> `|area(circle−iso) − area(iso−circle)| / A` must be < **0.005**. If it is
> not, the projection, the polygon algebra, or the isochrone area computation
> has a bug, and results must not be interpreted until it is fixed.

The identity holds **only** for equal-area candidates. The inscribed circle is
smaller than the isochrone, so its two difference areas genuinely differ and
both are reported.

> **Guardrail G2 (projection).** Isochrone area is computed in both AEQD and
> Lambert Azimuthal Equal-Area centred on the same point. Relative difference
> must be < **0.001**, else the projection handling is suspect.

### Directional routing consistency check

At **8 evenly spaced bearings** (0°, 45°, …, 315°) per formal candidate, a
Valhalla `/route` request is made from the snapped origin to the point where
that bearing crosses the candidate boundary, and the returned time is recorded.

**Mandatory wording.** These are reported as
**"Valhalla route-estimated free-flow travel time"**. The terms "actual drive
time", "real travel time", and any phrasing implying field observation are
prohibited.

> Directional routing checks measure consistency between the simplified
> boundary and Valhalla's routing model. They are **not** observed travel times
> and do **not** independently validate real-world traffic accuracy.

Valhalla routes on posted speed limits with no live or historical traffic.
`/route` and `/isochrone` share the same road graph and the same costing model,
so this is a **model self-consistency** measurement by construction.

---

## 5. Coordinate reference system

All area, intersection and union computation is performed in a **projected**
CRS. Computing area in EPSG:4326 is prohibited: its units are square degrees
and its distortion varies with latitude, which would silently corrupt IoU and
every area figure.

**Projection:** per-location **Azimuthal Equidistant (AEQD)** on the WGS84
datum, centred on that location's snapped origin. The exact PROJ string is
recorded per location in `results.json`.

Rationale: every formal candidate is a circle *centred on the same origin*, so
in origin-centred AEQD each candidate is an exact circle of its stated radius —
no polygonisation error in the candidate itself. Areal distortion at radius
`d` is `(d/R)/sin(d/R) ≈ 1 + (d/R)²/6`, i.e. ≈ 4×10⁻⁷ at d = 10 km — four
orders of magnitude below anything that matters here. Guardrail G2 verifies
this empirically against an exactly equal-area projection rather than assuming
it.

---

## 6. Candidate boundaries

### Admission rule

A **formal** candidate — one permitted to influence the Phase E decision — must
be **POI-independent**: determined solely by geometry and routing output, never
by the POI data it will subsequently be scored against. A rule fitted to POI
data and then evaluated on that same POI data is circular and is excluded.

### Formal candidates

1. **Equal-area circle** — current production behaviour. Centre = snapped
   origin; radius = `sqrt(A_total/π)` where `A_total` is the area of the **full**
   isochrone geometry (all `MultiPolygon` components, holes subtracted).
   *Note:* production reads only `coordinates[0]`; the benchmark computes the
   correct full area and reports both, so the size of the shipped bug is
   measured rather than inherited.
2. **Fixed-centre inscribed circle** — centre = snapped origin (the algorithm
   is **not** permitted to move it; moving the centre would mean the map is no
   longer centred on the destination, breaking the product's meaning); radius =
   the largest circle centred there that lies entirely within **the isochrone
   component containing that centre**. Holes, `MultiPolygon`, invalid geometry
   and numerical tolerance must all be handled. If the centre lies in no
   component, this is recorded as a geometry anomaly and the location is
   skipped rather than fudged.
   Its zero false inclusion may be described only as **geometric false
   inclusion relative to the Valhalla reference geometry being zero** — never
   as real-world accuracy.
3. **True isochrone** — the reference itself; scores are definitional (§1).

### Exploratory only — excluded from the Phase E decision

4. **Radius solved to hit a target false-inclusion rate.** Excluded for two
   independent reasons:
   - **POI-dependent**, so it fails the admission rule: it needs POIs to be
     solved, while the POI fetch extent needs candidates to be defined — a
     closed loop.
   - **Tuning on the answer**: solving the radius from a location's own POIs and
     then reporting that location's error demonstrates only that a known
     location can be calibrated to a threshold, not that the rule generalises.

   **Loop-breaking constraints.** Solved **only after** the POI universe is
   frozen; solution space restricted to `[0, search_cap_radius]`. FI(r) is a
   step function and may be non-monotonic, so the rule is stated in advance:
   evaluate FI at every candidate radius in `{0} ∪ {distance(origin, p) : p ∈
   universe} ∪ {search_cap_radius}`, and select the **largest** radius with
   `FI ≤ 0.10` (largest = maximum coverage at equal correctness). Tie-break:
   larger radius wins; if still tied, the numerically smaller value is taken for
   determinism. If no radius qualifies → `infeasible`. If the selected radius
   equals `search_cap_radius` → **`censored`**, and the query envelope must
   **not** be enlarged to re-tune.

   Promoting this to a formal rule later requires a calibration/held-out split
   or learning a general rule on calibration locations and evaluating on
   locations that took no part in tuning. Five locations cannot support that
   split; deferred to Run 1b.

---

## 7. Frozen POI universe (order is mandatory)

The existing pipeline fetches POIs only within the circle. Reusing that dataset
would make FE **structurally zero** regardless of how wrong the circle is. So:

1. Compute, **before any POI request**, three POI-independent objects:
   isochrone; **all formal candidates**; and a **search cap** = the smallest
   circle centred on the snapped origin that contains the isochrone component
   containing that origin (same MultiPolygon/holes/validity/tolerance handling
   as the inscribed circle). The exploratory target-FI candidate is **not**
   generated here — it would reintroduce the loop.
2. Take the **union** of isochrone + all formal candidates + search cap, take
   its bounding box, and expand by a recorded margin of **10 %**.
3. Fetch POIs over that envelope **once**.
4. Deduplicate **once**.
5. **Freeze** it to disk with Overture release, Overpass query parameters,
   category mapping, dedup rules and confidence threshold recorded.
6. Score **every** candidate on this identical frozen universe.

> **Assertion A1.** If any candidate boundary touches the query-envelope edge,
> the run **fails**: the margin was insufficient and the fetch must be redone
> larger. This is checked for the exploratory candidate too.

The envelope is much larger than a single circle, so the Overpass and Overture
budgets in §8 are set for the envelope, not the old circle-sized extent.

---

## 8. External API discipline

- Every Valhalla / Overpass / Overture response is cached to disk together with
  its request URL, parameters and timestamp.
- **Cache key** = SHA-256 over `{endpoint, coordinates, costing, contour
  minutes, all other request parameters, benchmark config hash}`.
- **Reuse rule:** an **exactly matching** key may be reused, and every datum
  reports its fetch time and cache-hit status. What is forbidden is presenting
  a **non-matching or stale** result as belonging to this run — not caching.
- **Budget:** ≤ 400 total external requests per run; ≤ 4 attempts per request;
  exponential backoff; ≥ 1.0 s throttle between Valhalla calls and ≥ 2.0 s
  between Overpass calls, respecting public-instance limits.
- **On persistent failure or rate limiting:** do not retry indefinitely, do not
  silently switch endpoints, do not fabricate. Keep completed cache entries,
  mark the run `benchmark blocked`, and list every outstanding request.

---

## 9. Decision procedure for Phase E

Applied to **formal candidates only**.

1. Any candidate failing P1–P4 is eliminated.
2. If exactly one survives, it is selected.
3. If several survive, select the lowest macro FI; tie-break on lowest macro FE.
4. If none survives, select the **true isochrone**, recording that its perfect
   score is definitional (§1) and that its merit is adding no approximation on
   top of the model's.

Implementation cost is explicitly **not** a tie-breaker: under a static
architecture the isochrone polygon is already computed at build time and
storing it costs a JSON field.

Exploratory results (§6.4) may be described in the report as a direction for
future work and must not appear in this procedure.

---

## 10. Outputs

Immutable per run:

```
reports/accuracy/runs/<run_id>/
    preflight.json     written BEFORE the first external request
    plan.md            verbatim copy of this file
    results.json       machine-readable; carries plan hash, config hash, run_id
    report.md          human-readable
    poi_universe.json  the frozen POI set + acquisition parameters
```

`reports/accuracy/latest.json` is a pointer to the newest run only and never
overwrites historical results.

Locations and bearings are fixed in advance by
`config/benchmark_locations.yaml` and §4. Neither may be reselected after
seeing results.
