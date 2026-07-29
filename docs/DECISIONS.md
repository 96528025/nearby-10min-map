# Decision Record

Decisions made in Run 1, with the evidence behind them and the alternatives
that were rejected. Each entry states what would change the decision.

Evidence base: `docs/CURRENT_STATE_AUDIT.md`,
`reports/accuracy/BENCHMARK_PLAN.md` (preregistered), and run
`20260729T072857Z_cfge03df09d_pland796c05b` under `reports/accuracy/runs/`.

---

## D-0 · What Run 1's benchmark can and cannot support

Recorded first because every other decision depends on it.

The benchmark's reference geometry is **Valhalla's own 10-minute `auto`
isochrone**. It therefore measures agreement with Valhalla's free-flow model
and nothing else. It does **not** validate the product's real-world
"10-minute drive" promise. Valhalla routes on posted speed limits with no live
or historical traffic; establishing real-world accuracy would need GPS traces,
historical traffic data, or field sampling, none of which this project has.

Two consequences that are easy to get wrong and are binding on all reporting:

- `true_isochrone` scoring 0 % error is **definitional** — it is the reference
  compared with itself.
- `inscribed_circle_fixed_center` scoring 0 % false inclusion is **also
  definitional** — a circle inscribed in the isochrone is a subset of it, so no
  point inside it can be outside the reference. Its FI column is guaranteed by
  construction, not measured.

Only the equal-area circle has both error columns free to vary, so it is the
only candidate whose result is fully empirical.

---

## D-1 · Architecture: pre-generated locations + static hosting

**Decision: adopt static hosting for the near-term public MVP.** No hard
blocker was found. Nothing is implemented in Run 1 — this records the decision
only.

Per the round's scope, benchmark results decide *how the boundary is drawn*,
never whether to re-open "call external APIs live from a public site".

### Comparison

| | Pre-generated + static | Runtime service (today) |
|---|---|---|
| Runtime exposure to Nominatim / Photon / Overpass / Valhalla | **None** — called only at build time, on infrastructure under my control | Every search hits four free public instances from a public site |
| Runtime exposure to map tiles | **Unchanged — still present** | Present |
| Failure modes | Build fails → yesterday's data ships | Any upstream 502/429 → user sees an error |
| Latency | Static JSON | ~8 s phase 1 measured, plus ~1 min Overture |
| Concurrency | CDN | One unbounded background thread per cache miss |
| Abuse surface | None | Unauthenticated endpoints that fan out to public APIs |
| Coverage | Fixed, verified set | Any geocodable attraction |
| Cost | ~0 | Hosting + eventual self-hosted routing |

### Three things this comparison must not blur

1. **Static removes *runtime* exposure, not the dependency.** Nominatim,
   Photon, Overpass, Valhalla and the Overture CLI are still used — at build
   time, under my control, at a cadence I choose, where a failure blocks a
   deploy instead of breaking a user's session.
2. **Static does not remove the map-tile dependency.** The browser still
   requests `tile.openstreetmap.org` for every view. That is a separate,
   still-live runtime dependency on a best-effort, no-SLA service that may
   block heavy users without notice (`ATTRIBUTION_AUDIT.md` §4). Static
   hosting changes nothing about it.
3. **Static changes the product promise.** "Type any attraction" becomes
   "choose from a set of verified destinations". This is a deliberate trade of
   coverage for reliability, not a downgrade — and given D-2, the current
   arbitrary-attraction path is shipping a boundary that fails its own
   preregistered acceptance rule, so unrestricted coverage is currently
   coverage of a wrong answer. **Run 1 records this consequence and implements
   no UI change.**

**Rejected — keep the public runtime service.** Four unthrottled public
dependencies, no rate limiting, no auth, and an unbounded thread per cache miss
(`CURRENT_STATE_AUDIT.md` §13) make this unsuitable for a public URL. The
README already says commercial use requires self-hosted instances.

**Rejected — self-host routing/geocoding now.** Correct eventually, far too
large for the current stage, and unnecessary if generation is a build step.

**What would change this:** a product requirement for genuinely arbitrary
destinations, which would force self-hosted Valhalla + Nominatim first.

---

## D-2 · Boundary representation: adopt the true isochrone

**Decision: render the true Valhalla isochrone polygon. Retire the
equal-area circle as the displayed boundary.**

Decided strictly among formal (POI-independent) candidates, using the
thresholds frozen in `BENCHMARK_PLAN.md` §3 before the run.

### Result against the preregistered rule

Thresholds, frozen before any measurement: macro FI ≤ 0.10, macro FE ≤ 0.20,
per-location FI ≤ 0.20 and FE ≤ 0.35, and ≥ 4 of 5 locations with both rates
defined at denominator ≥ 30.

| Candidate | Macro FI | Macro FE | Micro FI | Micro FE | Verdict |
|---|---|---|---|---|---|
| `equal_area_circle` | 9.1 % (n=5) | **24.7 %** | 11.0 % | 23.7 % | **NOT FIT** — fails P2, P3 |
| `inscribed_circle_fixed_center` | 0.0 %\* | **73.5 %** | 0.0 %\* | 66.9 % | **NOT FIT** — fails P2, P3 |
| `true_isochrone` | 0.0 %† | 0.0 %† | 0.0 %† | 0.0 %† | **FIT** |

\* definitional, see D-0 † definitional, see D-0

Per-location IoU against the reference:

| Location | Network type | Equal-area IoU | Equal-area POI FI | Equal-area POI FE |
|---|---|---|---|---|
| Apple Park | corporate campus | 0.660 | 16.0 % | 17.8 % |
| Stanford | university campus | 0.689 | 1.0 % | 11.0 % |
| **SJC airport** | airport | **0.527** | **23.1 %** | **46.4 %** |
| Downtown San Jose | dense urban core | 0.743 | 4.2 % | 13.7 % |
| **Half Moon Bay** | coastal exurban | **0.204** | 1.1 % | 34.8 % |

### Why the circle is retired

- It **fails P2 outright**: macro FE 24.7 % against a 20 % bound. Roughly one
  in four facilities the model places within 10 minutes is not shown.
- It **fails P3 at SJC** on both caps (FI 23.1 % > 20 %, FE 46.4 % > 35 %) —
  precisely the anisotropic ring-road-plus-freeway case the README's single
  Apple Park sample could not anticipate.
- Half Moon Bay's IoU of 0.204 shows what an equal-area summary does to a
  ribbon-shaped reachable area: the areas match by construction while the
  shapes barely overlap.
- The README's supporting evidence — "9.5–12.0 min across 8 bearings" at Apple
  Park — is a single location, is not reproducible from the repository
  (`CURRENT_STATE_AUDIT.md` §10), and is a Valhalla-vs-Valhalla check
  regardless.

### Why the inscribed circle is rejected

Its zero false inclusion is guaranteed by construction (D-0), so it buys
nothing that is not definitional, and it pays for it with macro FE 73.5 % —
hiding roughly three quarters of reachable facilities. At Half Moon Bay the
inscribed radius collapses to 257 m and the circle contains **1** POI out of
276 reachable ones. A map that hides three quarters of what it could show is
not a more conservative product; it is a less useful one.

### Why implementation cost is not a counter-argument

Under D-1 the isochrone polygon is already computed at build time — the current
code fetches it and then throws it away after reading its area
(`pipeline.boundary_from_isochrone`). Rendering it means storing a polygon in
the static JSON and handing it to the same `L.geoJSON` call that draws the
circle today. There is no runtime cost and no new dependency.

### What is explicitly *not* claimed

Adopting the isochrone does **not** make the map accurate about real-world
drive time. It removes one approximation layer (circle vs model) and leaves
the model-vs-reality gap entirely untouched. Copy must continue to say
"approximately 10 minutes", must continue to disclose free-flow assumptions,
and must not be upgraded to a stronger claim on the strength of this decision.

**Rejected — dual mode (circle by default, isochrone on toggle).** Keeps the
failing representation as the default and doubles the surface to explain.
Reconsider only if user research shows the jagged polygon actively confuses
people, which no evidence currently addresses.

**Rejected — keep the circle and shrink/grow it to a tuned radius.** See D-3.

**What would change this:** evidence that users misread the isochrone shape, or
a future run with more locations per network type showing the circle within
thresholds. Any such change requires a new plan version and run ID, never a
threshold edit.

---

## D-3 · The target-FI radius stays exploratory

**Decision: excluded from the D-2 decision. Recorded as a Run 1b direction.**

Two independent disqualifications:

1. **POI-dependent, so it fails the admission rule.** It needs POIs to be
   solved, while the POI fetch extent needs candidate boundaries to be defined
   — a closed loop.
2. **Tuning on the answer.** Solving a radius from a location's own POIs and
   then reporting that location's error demonstrates only that a known location
   can be calibrated to a threshold, not that the rule generalises.

It was still computed, under the loop-breaking constraints in
`BENCHMARK_PLAN.md` §6.4: solved only after the POI universe was frozen,
restricted to `[0, search_cap_radius]`, with the search rule and tie-breaker
fixed in advance, and with any solution at the cap marked `censored` and the
envelope **not** enlarged to re-tune.

Promoting it later requires a calibration/held-out split, or learning a general
rule on calibration locations and evaluating on locations that took no part in
tuning. Five locations cannot support that split.

---

## D-4 · Anti-circularity: metrics whose inputs depend on what they test

Recorded because this project has produced the same class of error repeatedly.

**Rule adopted:** before computing any metric, state how its inputs were
produced. If input generation depends on the thing being tested, the metric is
not evidence.

Four instances found and how each is handled:

| Instance | Why it is circular | Handling |
|---|---|---|
| `verify_inside` as a "hard boundary gate" | Every facility reaching it was already filtered by the same predicate against the same geometry, so it cannot fire | Documented in `CURRENT_STATE_AUDIT.md` §6; pinned by a characterisation test. Not cited as evidence |
| Point-in-polygon "validation" of accuracy | Tests membership in a self-drawn shape; says nothing about travel time | Foundation test 6 is named and documented to exclude the drive-time reading, with a guard test on the wording |
| Directional `/route` checks on the boundary | `/route` and `/isochrone` share one road graph and one costing model — model self-consistency | Reported only as "Valhalla route-estimated free-flow travel time", never as observed time |
| POI false-exclusion on the existing dataset | The shipped pipeline fetches POIs only inside the circle, so FE is **structurally zero** regardless of how wrong the circle is | Benchmark re-fetches over a union envelope of POI-independent geometry, dedups once, freezes, and scores all candidates on that one universe |

A fifth was found while designing the benchmark and is handled by
construction: using a POI-derived radius to define the POI fetch extent (D-3).

---

## D-5 · Geometry guardrails as bug detectors, not findings

For an equal-area candidate, `area(circle − iso) = A − I = area(iso − circle)`
identically. The two difference areas are **one number, not two independent
findings**, and `IoU = I / (2A − I)` is a function of that same number. The
geometric side of an equal-area candidate has one degree of freedom.

The benchmark therefore uses their measured difference purely as a correctness
check on the geometry code (G1, tolerance 0.005 relative). Observed: `6.3e-06`
at all five locations — consistent with the ~6e-6 polygonisation error of a
1024-gon circle, and confirming the projection and polygon algebra are sound.
G2 cross-checks every isochrone area against an equal-area projection: worst
case `1.3e-07` against a `1e-3` tolerance.

The identity holds only for equal-area candidates. The inscribed circle's two
difference areas genuinely differ, and both are reported.

---

## D-6 · Findings recorded but deliberately not acted on in Run 1

**The reference geometry is far less stable than the product implies.**
Road snapping moves the origin, and the modelled area moves with it:

| Location | Snap | Unsnapped | Snapped | Ratio |
|---|---|---|---|---|
| SJC airport | 500 m | 0.64 km² | 68.44 km² | **106×** |
| Apple Park | 84 m | 25.78 km² | 92.11 km² | **3.57×** |
| Half Moon Bay | 508 m | 13.05 km² | 28.58 km² | 2.19× |
| Stanford | 25 m | 48.51 km² | 48.35 km² | 1.00× |
| Downtown San Jose | 44 m | 75.37 km² | 75.89 km² | 1.01× |

Verified independently of the benchmark by re-requesting both coordinates
directly. Two consequences:

- **The shipped Apple Park static view and the live search path disagree about
  the same place.** `fetch_isochrone.sh` does not snap (2.9 km circle);
  `/api/area` does (5.4 km circle). Same attraction, two different answers,
  depending on which code path the user hits.
- Where the ratio is large, the modelled "10-minute area" depends more on a
  snapping heuristic than on the destination. That bounds how precisely *any*
  boundary rule can represent "10 minutes", independently of D-2.

**Not acted on in Run 1** (out of scope): this is a pipeline-semantics
question, and choosing a snapping policy is a product decision needing its own
evidence. It is the strongest candidate for Run 2.

**The `coordinates[0]` truncation is real in code but was not exercised here.**
`pipeline.boundary_from_isochrone` and `make_boundary.py` read only the first
ring of the first feature, discarding extra MultiPolygon components and all
holes. All five locations returned single-component, hole-free polygons, so the
measured area understatement was **0.000 % at all five**. The defect is
**latent, not active** at this sample — which is not evidence that the code is
correct, only that this sample did not reach it. Recorded, not fixed, because
fixing it changes shipped boundary geometry and belongs with D-2's
implementation.

**`norm_name` deletes accents instead of folding them**, so "Café Rêve" and
"Cafe Reve" do not cross-source dedup. Pinned by a test as a known limitation;
fixing it changes merge behaviour and needs its own evidence.

---

## D-7 · Process decisions

- **Preregistration.** Thresholds and locations were frozen, hashed and
  committed before the benchmark script existed. The hash is a content anchor,
  not a timestamp; ordering evidence comes from the `preflight.json` manifest
  (written before the first network call) and from commit order. No threshold
  was changed after seeing results, and none may be — only a new plan version
  with a new run ID, retaining the old run.
- **Run directories are immutable once published.** `latest.json` is a pointer
  only. Exactly one run is committed. Two earlier runs of the same
  configuration were produced and discarded before any commit while artifact
  serialisation and report rendering were finalised; all three were numerically
  identical and none was ever published.
- **Caching is permitted, substitution is not.** Exact-key cache hits are
  reused and every datum reports its fetch time and cache-hit status. The final
  run made 0 network requests and 121 cache hits, reproducing the online run
  byte-for-byte apart from timestamps.
- **README is intentionally untouched.** Every discrepancy is recorded in
  `CURRENT_STATE_AUDIT.md` §9. Rewriting it before the boundary decision is
  implemented would document a product that does not exist yet.
