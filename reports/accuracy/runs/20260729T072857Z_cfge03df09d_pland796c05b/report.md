# Accuracy Benchmark — Run Report

**Run ID:** `20260729T072857Z_cfge03df09d_pland796c05b`  
**Started / finished (UTC):** 2026-07-29T07:28:57Z / 2026-07-29T07:40:53Z  
**Plan SHA-256:** `d796c05b7c407c008b2e965c6946c57de5f9f4a5b7135c903fbc1e11014ca2e4`  
**Config SHA-256:** `e03df09d999b56a33bad33c6ba0889892c65f37e41df520c40a23d82d6ab8526`  
**Status:** complete  
**External requests:** 0 (budget 400), cache hits 121

## Scope limit

> The reference geometry is **Valhalla's own 10-minute free-flow isochrone**. Every figure below measures agreement with Valhalla's model. None of it measures real-world drive time, which would require GPS traces, historical traffic data, or field sampling. Valhalla routes on posted speed limits with no live or historical traffic.

## Verdicts against the preregistered rule

Thresholds (frozen before the run): macro FI ≤ 0.1, macro FE ≤ 0.2, per-location FI ≤ 0.2 and FE ≤ 0.35, ≥ 4 locations with both rates defined at denominator ≥ 30.

| Candidate | Macro FI | Macro FE | Micro FI | Micro FE | Verdict |
|---|---|---|---|---|---|
| `equal_area_circle` | 9.1% (n=5) | 24.7% (n=5) | 11.0% | 23.7% | **NOT FIT FOR PURPOSE** |
| `inscribed_circle_fixed_center` | 0.0% (n=5) | 73.5% (n=5) | 0.0% | 66.9% | **NOT FIT FOR PURPOSE** |
| `true_isochrone` | 0.0% (n=5) | 0.0% (n=5) | 0.0% | 0.0% | **FIT FOR PURPOSE** |

### Two of these numbers are definitional and carry no information

- `true_isochrone` scores perfectly **by definition** — it is the reference compared with itself. That is not evidence of accuracy; it only means it adds no approximation on top of the model's.
- `inscribed_circle_fixed_center` has **FI = 0 by construction**: a circle inscribed in the isochrone is a subset of it, so no point inside the circle can be outside the reference. Its FI column is guaranteed, not measured. Only its **FE** column carries information, and it may be described solely as *geometric false inclusion relative to the Valhalla reference is zero* — never as real-world accuracy.

Only `equal_area_circle` has both columns free to vary, so it is the only candidate whose full result is empirical.

### Interpreting any macro/micro divergence

If macro and micro disagree, the **only** admissible reading is that locations are heterogeneous and that this correlates with POI count. It may **not** be read as *error concentrates in road-network type X* or *different network types need different boundary rules*: this run has exactly **one location per network type (n = 1)**, so network type, POI density and site idiosyncrasy are perfectly confounded. Such a divergence is a reason to enlarge the sample in Run 2, nothing more.

## Apple Park — `apple_park` (large_corporate_campus)

Snapped origin `37.334429, -122.012181` (84 m from the configured pin). CRS: AEQD centred on the snapped origin.

Isochrone: Polygon, 1 component(s), 0 hole(s), **92.11 km²**.

> **Snap-sensitivity diagnostic** (not part of the decision rule): the same attraction yields 25.78 km² unsnapped vs 92.11 km² after a 84 m snap — a **3.57×** change, radius 2865 m → 5415 m.

Radii — equal-area 5415 m · inscribed 3166 m · search cap 12488 m

Frozen POI universe: **12600** points (OSM 6156 kept / 57 deduped; Overture 6444 kept / 4023 deduped / 9833 below confidence 0.6). Overture release `2026-07-22.0`.

| Candidate | IoU | Sym. diff km² | Sym. diff % | POI FI | POI FE |
|---|---|---|---|---|---|
| `equal_area_circle` | 0.660 | 37.75 | 41.0% | 16.0% (374/2342) | 17.8% (427/2395) |
| `inscribed_circle_fixed_center` | 0.342 | 60.62 | 65.8% | 0.0% (0/965) | 59.7% (1430/2395) |
| `true_isochrone` | 1.000 | 0.00 | 0.0% | 0.0% (0/2395) | 0.0% (0/2395) |

**Guardrail G1** (equal-area identity): FI area 18.8765 km² vs FE area 18.8771 km², |Δ| 0.000578 km² (6.27e-06 of area) — PASS. These are one number, not two findings.  
**Guardrail G2** (projection): AEQD 92.1060 km² vs LAEA 92.1060 km², relative difference 8.21e-08 — PASS.  
**Assertion A1** (no candidate touches the envelope edge): PASS (min slack 2498 m)

### Category split

| Category | Equal-area FI | Equal-area FE | Inscribed FI | Inscribed FE |
|---|---|---|---|---|
| dining | 15.4% (237/1535) | 17.6% (278/1576) | 0.0% (0/637) | 59.6% (939/1576) |
| health | 10.9% (12/110) | 28.5% (39/137) | 0.0% (0/49) | 64.2% (88/137) |
| education | 18.3% (38/208) | 12.4% (24/194) | 0.0% (0/80) | 58.8% (114/194) |
| lodging | 29.1% (16/55) | 30.4% (17/56) | 0.0% (0/29) | 48.2% (27/56) |
| shopping | 16.8% (31/184) | 10.5% (18/171) | 0.0% (0/73) | 57.3% (98/171) |
| fuel_ev | 10.2% (13/128) | 23.3% (35/150) | 0.0% (0/56) | 62.7% (94/150) |
| culture | 21.1% (4/19) | 11.8% (2/17) | 0.0% (0/4) | 76.5% (13/17) |
| parks | 22.3% (23/103) | 14.9% (14/94) | 0.0% (0/37) | 60.6% (57/94) |

### Directional consistency — Valhalla route-estimated free-flow travel time

> Directional routing checks measure consistency between the simplified boundary and Valhalla's routing model. They are **not** observed travel times and do **not** independently validate real-world traffic accuracy. Valhalla uses posted speed limits and carries no live traffic data.

| Bearing | Equal-area circle (min) | Inscribed circle (min) |
|---|---|---|
| 0° | 10.01 | 6.94 |
| 45° | 13.6 | 8.62 |
| 90° | 12.68 | 7.89 |
| 135° | 10.64 | 7.92 |
| 180° | 13.15 | 6.69 |
| 225° | 12.88 | 8.33 |
| 270° | 8.7 | 3.7 |
| 315° | 10.99 | 9.54 |

### Exploratory: target-FI radius (EXCLUDED from the decision)

Status `interior`, radius 4979 m, cap 12488 m, FI 10.0%.  
Excluded because it is POI-dependent (violating the POI-independence admission rule) and is tuned on the same POIs it is then scored against.

## Stanford University — `stanford_university` (university_campus)

Snapped origin `37.431535, -122.169293` (25 m from the configured pin). CRS: AEQD centred on the snapped origin.

Isochrone: Polygon, 1 component(s), 0 hole(s), **48.35 km²**.

> **Snap-sensitivity diagnostic** (not part of the decision rule): the same attraction yields 48.51 km² unsnapped vs 48.35 km² after a 25 m snap — a **1.00×** change, radius 3930 m → 3923 m.

Radii — equal-area 3923 m · inscribed 1492 m · search cap 6176 m

Frozen POI universe: **3032** points (OSM 1435 kept / 7 deduped; Overture 1597 kept / 941 deduped / 2382 below confidence 0.6). Overture release `2026-07-22.0`.

| Candidate | IoU | Sym. diff km² | Sym. diff % | POI FI | POI FE |
|---|---|---|---|---|---|
| `equal_area_circle` | 0.689 | 17.79 | 36.8% | 1.0% (13/1270) | 11.0% (156/1413) |
| `inscribed_circle_fixed_center` | 0.145 | 41.35 | 85.5% | 0.0% (0/453) | 67.9% (960/1413) |
| `true_isochrone` | 1.000 | 0.00 | 0.0% | 0.0% (0/1413) | 0.0% (0/1413) |

**Guardrail G1** (equal-area identity): FI area 8.8961 km² vs FE area 8.8964 km², |Δ| 0.000303 km² (6.27e-06 of area) — PASS. These are one number, not two findings.  
**Guardrail G2** (projection): AEQD 48.3503 km² vs LAEA 48.3503 km², relative difference 3.93e-08 — PASS.  
**Assertion A1** (no candidate touches the envelope edge): PASS (min slack 1235 m)

### Category split

| Category | Equal-area FI | Equal-area FE | Inscribed FI | Inscribed FE |
|---|---|---|---|---|
| dining | 0.4% (3/692) | 10.2% (78/767) | 0.0% (0/181) | 76.4% (586/767) |
| health | 1.1% (2/182) | 10.9% (22/202) | 0.0% (0/136) | 32.7% (66/202) |
| education | 4.3% (7/161) | 8.9% (15/169) | 0.0% (0/78) | 53.8% (91/169) |
| lodging | 0.0% (0/41) | 18.0% (9/50) | 0.0% (0/5) | 90.0% (45/50) |
| shopping | 0.0% (0/43) | 23.2% (13/56) | 0.0% (0/13) | 76.8% (43/56) |
| fuel_ev | 0.0% (0/32) | 23.8% (10/42) | 0.0% (0/4) | 90.5% (38/42) |
| culture | 0.0% (0/50) | 2.0% (1/51) | 0.0% (0/17) | 66.7% (34/51) |
| parks | 1.4% (1/69) | 10.5% (8/76) | 0.0% (0/19) | 75.0% (57/76) |

### Directional consistency — Valhalla route-estimated free-flow travel time

> Directional routing checks measure consistency between the simplified boundary and Valhalla's routing model. They are **not** observed travel times and do **not** independently validate real-world traffic accuracy. Valhalla uses posted speed limits and carries no live traffic data.

| Bearing | Equal-area circle (min) | Inscribed circle (min) |
|---|---|---|
| 0° | 11.03 | 3.65 |
| 45° | 9.32 | 4.73 |
| 90° | 9.16 | 5.32 |
| 135° | 12.43 | 5.71 |
| 180° | 11.21 | 7.84 |
| 225° | 14.41 | 4.4 |
| 270° | 10.63 | 4.88 |
| 315° | 9.71 | 8.46 |

### Exploratory: target-FI radius (EXCLUDED from the decision)

Status `interior`, radius 5309 m, cap 6176 m, FI 10.0%.  
Excluded because it is POI-dependent (violating the POI-independence admission rule) and is tuned on the same POIs it is then scored against.

## San Jose Mineta International Airport — `sjc_airport` (airport)

Snapped origin `37.366512, -121.925343` (500 m from the configured pin). CRS: AEQD centred on the snapped origin.

Isochrone: Polygon, 1 component(s), 0 hole(s), **68.44 km²**.

> **Snap-sensitivity diagnostic** (not part of the decision rule): the same attraction yields 0.64 km² unsnapped vs 68.44 km² after a 500 m snap — a **106.29×** change, radius 453 m → 4667 m.

Radii — equal-area 4667 m · inscribed 1472 m · search cap 9854 m

Frozen POI universe: **10157** points (OSM 4970 kept / 50 deduped; Overture 5187 kept / 3196 deduped / 7738 below confidence 0.6). Overture release `2026-07-22.0`.

| Candidate | IoU | Sym. diff km² | Sym. diff % | POI FI | POI FE |
|---|---|---|---|---|---|
| `equal_area_circle` | 0.527 | 42.42 | 62.0% | 23.1% (428/1849) | 46.4% (1230/2651) |
| `inscribed_circle_fixed_center` | 0.099 | 61.63 | 90.1% | 0.0% (0/149) | 94.4% (2502/2651) |
| `true_isochrone` | 1.000 | 0.00 | 0.0% | 0.0% (0/2651) | 0.0% (0/2651) |

**Guardrail G1** (equal-area identity): FI area 21.2093 km² vs FE area 21.2098 km², |Δ| 0.000429 km² (6.27e-06 of area) — PASS. These are one number, not two findings.  
**Guardrail G2** (projection): AEQD 68.4351 km² vs LAEA 68.4351 km², relative difference 7.01e-08 — PASS.  
**Assertion A1** (no candidate touches the envelope edge): PASS (min slack 1971 m)

### Category split

| Category | Equal-area FI | Equal-area FE | Inscribed FI | Inscribed FE |
|---|---|---|---|---|
| dining | 21.3% (265/1247) | 46.1% (841/1823) | 0.0% (0/100) | 94.5% (1723/1823) |
| health | 24.1% (19/79) | 50.0% (60/120) | 0.0% (0/2) | 98.3% (118/120) |
| education | 25.0% (31/124) | 51.1% (97/190) | 0.0% (0/8) | 95.8% (182/190) |
| lodging | 20.8% (22/106) | 29.4% (35/119) | 0.0% (0/27) | 77.3% (92/119) |
| shopping | 43.9% (36/82) | 60.0% (69/115) | undefined | 100.0% (115/115) |
| fuel_ev | 26.5% (22/83) | 46.5% (53/114) | 0.0% (0/7) | 93.9% (107/114) |
| culture | 8.1% (3/37) | 46.0% (29/63) | 0.0% (0/1) | 98.4% (62/63) |
| parks | 33.0% (30/91) | 43.0% (46/107) | 0.0% (0/4) | 96.3% (103/107) |

### Directional consistency — Valhalla route-estimated free-flow travel time

> Directional routing checks measure consistency between the simplified boundary and Valhalla's routing model. They are **not** observed travel times and do **not** independently validate real-world traffic accuracy. Valhalla uses posted speed limits and carries no live traffic data.

| Bearing | Equal-area circle (min) | Inscribed circle (min) |
|---|---|---|
| 0° | 14.75 | 8.03 |
| 45° | 15.56 | 5.98 |
| 90° | 12.11 | 5.64 |
| 135° | 9.16 | 3.76 |
| 180° | 11.06 | 4.97 |
| 225° | 11.41 | 8.23 |
| 270° | 11.59 | 8.74 |
| 315° | 11.84 | 4.95 |

### Exploratory: target-FI radius (EXCLUDED from the decision)

Status `interior`, radius 3481 m, cap 9854 m, FI 10.0%.  
Excluded because it is POI-dependent (violating the POI-independence admission rule) and is tuned on the same POIs it is then scored against.

## San Jose City Hall (downtown core) — `downtown_san_jose` (dense_urban_core)

Snapped origin `37.338238, -121.884902` (44 m from the configured pin). CRS: AEQD centred on the snapped origin.

Isochrone: Polygon, 1 component(s), 0 hole(s), **75.89 km²**.

> **Snap-sensitivity diagnostic** (not part of the decision rule): the same attraction yields 75.37 km² unsnapped vs 75.89 km² after a 44 m snap — a **1.01×** change, radius 4898 m → 4915 m.

Radii — equal-area 4915 m · inscribed 3058 m · search cap 10404 m

Frozen POI universe: **10364** points (OSM 5095 kept / 40 deduped; Overture 5269 kept / 3305 deduped / 8077 below confidence 0.6). Overture release `2026-07-22.0`.

| Candidate | IoU | Sym. diff km² | Sym. diff % | POI FI | POI FE |
|---|---|---|---|---|---|
| `equal_area_circle` | 0.743 | 22.41 | 29.5% | 4.2% (121/2849) | 13.7% (433/3161) |
| `inscribed_circle_fixed_center` | 0.387 | 46.52 | 61.3% | 0.0% (0/1706) | 46.0% (1455/3161) |
| `true_isochrone` | 1.000 | 0.00 | 0.0% | 0.0% (0/3161) | 0.0% (0/3161) |

**Guardrail G1** (equal-area identity): FI area 11.2042 km² vs FE area 11.2047 km², |Δ| 0.000476 km² (6.27e-06 of area) — PASS. These are one number, not two findings.  
**Guardrail G2** (projection): AEQD 75.8902 km² vs LAEA 75.8902 km², relative difference 5.63e-08 — PASS.  
**Assertion A1** (no candidate touches the envelope edge): PASS (min slack 2081 m)

### Category split

| Category | Equal-area FI | Equal-area FE | Inscribed FI | Inscribed FE |
|---|---|---|---|---|
| dining | 3.1% (59/1891) | 14.1% (301/2133) | 0.0% (0/1195) | 44.0% (938/2133) |
| health | 4.3% (6/140) | 20.2% (34/168) | 0.0% (0/51) | 69.6% (117/168) |
| education | 11.9% (27/226) | 5.2% (11/210) | 0.0% (0/120) | 42.9% (90/210) |
| lodging | 3.0% (3/99) | 16.5% (19/115) | 0.0% (0/48) | 58.3% (67/115) |
| shopping | 4.1% (6/148) | 16.5% (28/170) | 0.0% (0/80) | 52.9% (90/170) |
| fuel_ev | 1.8% (2/113) | 24.5% (36/147) | 0.0% (0/58) | 60.5% (89/147) |
| culture | 4.4% (4/91) | 0.0% (0/87) | 0.0% (0/70) | 19.5% (17/87) |
| parks | 9.9% (14/141) | 3.1% (4/131) | 0.0% (0/84) | 35.9% (47/131) |

### Directional consistency — Valhalla route-estimated free-flow travel time

> Directional routing checks measure consistency between the simplified boundary and Valhalla's routing model. They are **not** observed travel times and do **not** independently validate real-world traffic accuracy. Valhalla uses posted speed limits and carries no live traffic data.

| Bearing | Equal-area circle (min) | Inscribed circle (min) |
|---|---|---|
| 0° | 12.35 | 8.15 |
| 45° | 10.74 | 6.76 |
| 90° | 10.79 | 10.86 |
| 135° | 12.68 | 7.93 |
| 180° | 10.35 | 7.02 |
| 225° | 12.48 | 7.34 |
| 270° | 10.67 | 6.42 |
| 315° | 10.03 | 7.94 |

### Exploratory: target-FI radius (EXCLUDED from the decision)

Status `interior`, radius 5433 m, cap 10404 m, FI 10.0%.  
Excluded because it is POI-dependent (violating the POI-independence admission rule) and is tuned on the same POIs it is then scored against.

## Half Moon Bay State Beach — `half_moon_bay` (coastal_exurban_attraction)

Snapped origin `37.478382, -122.443643` (508 m from the configured pin). CRS: AEQD centred on the snapped origin.

Isochrone: Polygon, 1 component(s), 0 hole(s), **28.58 km²**.

> **Snap-sensitivity diagnostic** (not part of the decision rule): the same attraction yields 13.05 km² unsnapped vs 28.58 km² after a 508 m snap — a **2.19×** change, radius 2038 m → 3016 m.

Radii — equal-area 3016 m · inscribed 257 m · search cap 13031 m

Frozen POI universe: **2682** points (OSM 1147 kept / 26 deduped; Overture 1535 kept / 755 deduped / 1757 below confidence 0.6). Overture release `2026-07-22.0`.

| Candidate | IoU | Sym. diff km² | Sym. diff % | POI FI | POI FE |
|---|---|---|---|---|---|
| `equal_area_circle` | 0.204 | 37.79 | 132.2% | 1.1% (2/182) | 34.8% (96/276) |
| `inscribed_circle_fixed_center` | 0.007 | 28.37 | 99.3% | 0.0% (0/1) | 99.6% (275/276) |
| `true_isochrone` | 1.000 | 0.00 | 0.0% | 0.0% (0/276) | 0.0% (0/276) |

**Guardrail G1** (equal-area identity): FI area 18.8930 km² vs FE area 18.8932 km², |Δ| 0.000179 km² (6.27e-06 of area) — PASS. These are one number, not two findings.  
**Guardrail G2** (projection): AEQD 28.5791 km² vs LAEA 28.5791 km², relative difference 1.28e-07 — PASS.  
**Assertion A1** (no candidate touches the envelope edge): PASS (min slack 2606 m)

### Category split

| Category | Equal-area FI | Equal-area FE | Inscribed FI | Inscribed FE |
|---|---|---|---|---|
| dining | 0.0% (0/103) | 31.8% (48/151) | undefined | 100.0% (151/151) |
| health | 0.0% (0/4) | 50.0% (4/8) | undefined | 100.0% (8/8) |
| education | 0.0% (0/10) | 9.1% (1/11) | undefined | 100.0% (11/11) |
| lodging | 0.0% (0/16) | 48.4% (15/31) | undefined | 100.0% (31/31) |
| shopping | 0.0% (0/10) | 28.6% (4/14) | undefined | 100.0% (14/14) |
| fuel_ev | 0.0% (0/11) | 15.4% (2/13) | undefined | 100.0% (13/13) |
| culture | 0.0% (0/13) | 35.0% (7/20) | undefined | 100.0% (20/20) |
| parks | 13.3% (2/15) | 53.6% (15/28) | 0.0% (0/1) | 96.4% (27/28) |

### Directional consistency — Valhalla route-estimated free-flow travel time

> Directional routing checks measure consistency between the simplified boundary and Valhalla's routing model. They are **not** observed travel times and do **not** independently validate real-world traffic accuracy. Valhalla uses posted speed limits and carries no live traffic data.

| Bearing | Equal-area circle (min) | Inscribed circle (min) |
|---|---|---|
| 0° | 14.01 | 1.1 |
| 45° | 11.22 | 1.81 |
| 90° | 5.91 | 1.29 |
| 135° | 5.94 | 2.16 |
| 180° | 4.66 | 1.92 |
| 225° | 7.93 | 0.98 |
| 270° | 4.22 | 1.23 |
| 315° | 3.08 | 2.43 |

### Exploratory: target-FI radius (EXCLUDED from the decision)

Status `interior`, radius 10336 m, cap 13031 m, FI 9.9%.  
Excluded because it is POI-dependent (violating the POI-independence admission rule) and is tuned on the same POIs it is then scored against.

## Cross-cutting observations

### Shipped `coordinates[0]` truncation: real in code, not exercised here

`pipeline.boundary_from_isochrone` and `make_boundary.py` read only `features[0].geometry.coordinates[0]`, discarding extra MultiPolygon components and all holes. Across these five locations Valhalla returned a **single-component, hole-free Polygon every time**, so the measured area understatement was **0.000 % at all five**. The defect is therefore **latent, not active** at this sample. It is not evidence that the code is correct — only that this sample did not reach it.

| Location | Components | Holes | Area understated |
|---|---|---|---|
| apple_park | 1 | 0 | 0.000% |
| stanford_university | 1 | 0 | 0.000% |
| sjc_airport | 1 | 0 | 0.000% |
| downtown_san_jose | 1 | 0 | 0.000% |
| half_moon_bay | 1 | 0 | 0.000% |

### Reference stability: the 10-minute area is not a stable quantity

Diagnostic only — no part of the decision rule. Road snapping moves the origin by an implementation-chosen distance, and the reference geometry moves with it:

| Location | Snap (m) | Unsnapped km² | Snapped km² | Ratio |
|---|---|---|---|---|
| apple_park | 84 | 25.78 | 92.11 | **3.57×** |
| stanford_university | 25 | 48.51 | 48.35 | **1.00×** |
| sjc_airport | 500 | 0.64 | 68.44 | **106.29×** |
| downtown_san_jose | 44 | 75.37 | 75.89 | **1.01×** |
| half_moon_bay | 508 | 13.05 | 28.58 | **2.19×** |

Where this ratio is large, the modelled 10-minute area depends more on a snapping heuristic than on the destination itself. That bounds how precisely *any* boundary rule can be said to represent "10 minutes", independently of which candidate is chosen.
