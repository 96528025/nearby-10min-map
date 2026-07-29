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

---

## D-8 · Review findings on the benchmark implementation, and their fixes

An independent review of the Run 1 artifacts (not just the execution log)
accepted the research conclusions but found the benchmark script not yet fit to
publish. All five findings were reproduced against the files and are correct.
This section records them, the fixes, and — importantly — what was **not**
changed.

### Fixed in the script

| # | Finding | Fix |
|---|---|---|
| 1 | A run with failed sub-requests could still be reported `complete`. `BUDGET.blocked` was written into results but the status line tested only the per-location exception list, so failed directional routes and skipped locations were invisible in the status. | Status is now derived from `blocked + BUDGET.blocked + skipped`. Any incomplete work yields `benchmark blocked` and is listed in the report under "Incomplete work". |
| 2 | The preregistered validity gates were **recorded but not enforced**. The plan says an A1 failure means "the run fails" (§7) and a G1 failure means "results must not be interpreted until it is fixed" (§4); the script only wrote `pass: false` and carried on to publish verdicts. | New `validity_failures()` collects G1/G2/A1 breaches. On any breach the run is marked `invalid: preregistered guardrail failed`, `run_valid: false`, and **verdicts are withheld** rather than published. The report leads with a RUN INVALID section. |
| 3 | The 1208-line benchmark had **no tests at all**. The 97 passing tests all targeted the original pipeline, so "97 passed" said nothing about the code that produces the decision. | `tests/test_benchmark_accuracy.py` adds 47 tests covering verdict logic P1–P4 (including the boundary cases and the "macro hides one bad location" case P3 exists for), undefined handling, macro-vs-micro pooling, validity gating, run-status determination, the geometry helpers, envelope/A1 slack, the exploratory solver (interior / censored / infeasible / non-monotonic), cache-key composition and provenance. Suite is now **144 tests, offline**. |
| 4 | Runs recorded only plan and config hashes — not the script, commit, or dependency versions — so two runs could not be told apart. | `provenance()` records `script_sha256`, git commit and dirty flag, Python and library versions, hosts, and the Overture release, in both `preflight.json` and `results.json`. |
| 5 | The plan requires a directional check for **every** formal candidate, but the script ran it only for the two circles. | `boundary_point_at_bearing()` handles arbitrary geometry by ray intersection (taking the outermost crossing, so concave isochrones are measured at their true edge). All three formal candidates now get 8 bearings. Circle behaviour is unchanged — verified byte-identical to the earlier run. |

Writing the tests immediately paid for itself: they caught a `NameError` on
`LineString` in the newly added ray-intersection code, which would have crashed
the next real run, and they corrected one of my own expectations about the
exploratory solver (returning the cap **was** right under the preregistered
"largest radius meeting the target" rule; my test was wrong, not the code).

### Plan v1 errata — disclosed, NOT edited

`BENCHMARK_PLAN.md` contains two internal inconsistencies:

1. §5 justifies the AEQD choice with "every formal candidate is a circle
   *centred on the same origin*", but the formal candidates include
   `true_isochrone`, which is not a circle.
2. §4 requires 8 bearings "per formal candidate", which the original run did
   not do for `true_isochrone`.

**The plan file was deliberately left byte-identical.** Editing a frozen,
hashed preregistration to make it agree with the implementation is exactly the
failure mode preregistration exists to prevent, and it would invalidate the
`plan_sha256` recorded in the published run. Both errata are recorded here
instead; a corrected **plan v2** with a new hash will be issued for Run 2.

Neither erratum affects any verdict. The substantive claim behind §5 — that
origin-centred AEQD makes the circular candidates exact — still holds, and the
isochrone's area distortion under AEQD is verified empirically by G2 (worst
case 1.3e-07 against a 1e-3 tolerance). The directional check is explicitly a
non-deciding guardrail metric (§2, §4).

### Two runs are retained

| Run | Script | Notes |
|---|---|---|
| `20260729T072857Z_…` | `f7017491…` (1208 lines) | Original. Verdicts and aggregates as published; independently re-verified by recomputing FI/FE from `poi_universe.json`. Lacks provenance and the `true_isochrone` directional check. |
| `20260729T081336Z_…` | `bccf6dbe…` | Run of record. Produced by the gated, tested script; adds provenance and full plan §4 conformance. 32 new requests, 129 cache hits. |

**Verdicts and aggregates are byte-identical between the two runs.** The new
run supersedes the old one for citation purposes and `latest.json` points at
it; the old run is **not** deleted or modified, per the immutability rule.
Re-running was chosen over leaving the gap because a published run that does
not satisfy its own frozen plan is the same class of undisclosed deviation this
round exists to eliminate.

### Still open after this round

- The Valhalla public instance exposes no graph build version through
  `/isochrone`, `/route` or `/locate`. Isochrone geometry, IoU and route times
  therefore **cannot be re-derived from the repository alone** at a later date,
  only re-measured against whatever graph is live then. This is now stated in
  every run's `provenance.reproducibility_note`. POI-level rates **are** fully
  recomputable from the committed `poi_universe.json`.
- The ~490 MB raw response cache remains gitignored. Committing it would make
  the geometry independently recomputable at a size no portfolio repository
  should carry; a targeted alternative (committing only the five isochrone
  responses, a few hundred KB) is the right Run 2 fix.

---

## D-9 · Second review round: partial runs must not publish verdicts

A follow-up review confirmed the D-8 fixes and found two more. Both are
correct and both are fixed.

### 1. The status tests did not test production code

`TestRunStatus` reimplemented the status rule inside the test file, so it
asserted against a *copy* of the logic. A regression in the real path would
have left those tests green — the same self-referential mistake catalogued in
D-4, reintroduced in the tests written to prevent it.

The rule is now a single production function, `run_status()`, and the tests
call it directly.

### 2. A blocked run still published verdicts — and that can flip the answer

This is material, not hypothetical. P4 tolerates **4 of 5** locations, so a
run that loses one still clears the evidence floor and publishes a formal
conclusion on a sample that is no longer the preregistered one.

Measured on the run of record, dropping each location in turn and re-scoring
the equal-area circle:

| Location dropped | Macro FI | Macro FE | Verdict |
|---|---|---|---|
| apple_park | 7.4 % | 26.5 % | NOT FIT |
| stanford_university | 11.1 % | 28.2 % | NOT FIT |
| **sjc_airport** | 5.6 % | **19.3 %** | **FIT FOR PURPOSE** ← flips |
| downtown_san_jose | 10.3 % | 27.5 % | NOT FIT |
| half_moon_bay | 11.1 % | 22.2 % | NOT FIT |
| *(none — full sample)* | 9.1 % | 24.7 % | NOT FIT |

**Had the single SJC Overpass or Valhalla request failed, this round would
have concluded the opposite of what it concluded** — and the conclusion would
have been decided, in effect, by whichever request happened to fail. SJC is
the worst-performing location, so losing it is not a neutral loss.

`run_status()` now returns `publish_verdicts`, and **verdicts are withheld
from any run that is not `complete`**. A dedicated test pins the SJC flip so
the reason for the rule cannot be lost.

Failed sub-metrics withhold too, even though a directional route cannot change
a POI rate. Systematic route failures would indicate a degraded routing
service, which would make the isochrones themselves suspect — and deciding
case by case which failures are benign reintroduces exactly the discretion
preregistration exists to remove.

### 3. Provenance: process fixed, and one field was measuring the wrong thing

The reviewer noted that run `20260729T081336Z` recorded an older commit and a
dirty worktree, because it was executed before its own script was committed.
The prescribed order — commit script and tests, run from a clean tree, then
commit the artifacts — is correct and was followed for the run of record.

Investigating also exposed a defect in the flag itself: it used
`git status --porcelain`, which counts untracked files. Since a run writes its
own output directory before finishing, **every** run reported as dirty,
whatever tree it was launched from. The field is now
`git_tracked_files_modified` (tracked modifications only), with a test pinning
the semantics against git.

Run of record `20260729T082833Z` now closes the chain: `git_commit` equals
`HEAD`, `script_sha256` equals the hash of the committed script at that
commit, `git_tracked_files_modified` is `false`, 0 network requests, 161 cache
hits. Verdicts and aggregates remain byte-identical to every earlier run.

### Run directories now present

| Run | Status | Why retained |
|---|---|---|
| `20260729T072857Z_…` | superseded | Original; independently verified by the reviewer |
| `20260729T081336Z_…` | superseded | Added provenance and plan §4 conformance |
| `20260729T082833Z_…` | **run of record** | Clean provenance chain, gated and tested script |

All three carry identical verdicts and aggregates. Two further runs were
produced and discarded before any commit while serialisation, rendering and
the provenance flag were finalised; none was ever published. Superseded runs
are retained rather than deleted, per the immutability rule — pruning them to
a single canonical run plus hashes is a Run 2 decision, not one to take
unilaterally here.
