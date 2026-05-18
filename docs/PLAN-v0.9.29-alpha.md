# Plan — v0.9.29-alpha

> **Status:** DRAFT
> **Target:** v0.9.29-alpha
> **Scope:** Node-RED ArcGIS geometry handling fix for multipart polygon features (outer rings only).
>
> **Origin:** Moved from `PLAN-v0.9.28-alpha.md` (2026-05-17) to make room for v0.9.28-alpha "Enterprise Authentik PG Scaling" (raises `max_connections` 500→2000 + PG memory tuning + autotune cap 60%→75% + pool fleet constant 250/50→750/150 + PgBouncer MAX_CLIENT_CONN 1000→5000 + Channels-baseline telemetry — sized for 12-core / 48 GB enterprise hardware to absorb Authentik #20714 channels_postgres leak at production scale). Originally moved from v0.9.23 → v0.9.24 (2026-05-14) → v0.9.25 → v0.9.26 → v0.9.27 → v0.9.28 → v0.9.29 (all 2026-05-16/17). Node-RED scope is unchanged across all moves.

---

## Why v0.9.29 exists

ArcGIS feeds that return multipart wildfire geometry (for example one incident feature containing a main perimeter and spot-fire polygons) currently render only one polygon in DataSync output.

The current ArcGIS parser path in Node-RED only processes the first polygon ring (`g.rings[0]`). This drops additional outer polygons that belong to the same feature geometry.

This release adds multipart support while preserving existing dedup/reconcile behavior.

---

## Item 1 — ArcGIS multipart polygon support (outer rings only)
> _Scope preserved verbatim across all version moves; only the version header and date references updated._

### Problem

- `arcgis.parse_cot` builds polygon CoT from only `g.rings[0]`.
- Additional rings in the same ArcGIS feature are ignored.
- Result: one rendered polygon when source geometry includes multiple outer polygons.

### Goal

- Emit one CoT event per polygon part for multipart polygon geometries.
- Include only outer rings (skip interior hole rings).
- Keep current dedup semantics unchanged (`dedupField` + `timeField` still means "keep latest per dedup value").

### Implementation plan

1. **Update parser fan-out in `FN_PARSE_COT`**
   - File: `nodered/build-flows.js`
   - Replace single-ring polygon flow with per-part iteration.
   - Polygon: iterate all rings, filter to outer rings.
   - Polyline: iterate all paths (parity for multipart lines).
   - Point: unchanged.

2. **Add outer-ring filter**
   - Determine ring winding orientation and emit only outer rings.
   - Skip degenerate rings (fewer than 3 distinct vertices).

3. **Add deterministic per-part identity**
   - Preserve base UID generation from configured `idFields`.
   - Append part suffix for multipart geometries (example: `-r0`, `-r1`).
   - Include part index + part geometry in `_hash` input so reconcile detects part-level updates.

4. **Build CoT per emitted part**
   - Compute centroid from that part only.
   - Build `detail.link` from that part vertices only.
   - Keep style, class mapping, labels, remarks, and TTL behavior unchanged.

5. **Regenerate Node-RED templates/flows**
   - Regenerate `nodered/template-functions.json` and `nodered/flows.json` from `build-flows.js`.
   - Do not change deploy safety behavior.

6. **Documentation update**
   - Update `docs/NODERED.md` to replace the current "only `g.rings[0]`" limitation note with the new outer-ring behavior.
   - Document UID suffix behavior for multipart output.

---

## Planned files touched

- `nodered/build-flows.js`
- `nodered/template-functions.json`
- `nodered/flows.json`
- `docs/NODERED.md`
- `docs/RELEASE-v0.9.29-alpha.md` (new, planned)

---

## Acceptance criteria

- [ ] A single ArcGIS feature containing multiple outer rings emits one CoT event per outer ring.
- [ ] Interior hole rings are not emitted as standalone polygons.
- [ ] With `dedupField = missionName`, latest mission record is selected, then all outer rings from that selected feature are emitted.
- [ ] Reconcile behavior remains stable (no repeated PUT/DELETE churn for unchanged multipart features).
- [ ] Existing single-ring feeds remain unchanged.

---

## Test plan

1. Configure ArcGIS feed with source data containing one feature with 3 polygons (main perimeter + two spot fires) in one geometry.
2. Run one poll cycle and inspect Node-RED logs for CoT build count.
3. Verify TAK/DataSync shows all expected polygons.
4. Re-run poll with unchanged source and confirm reconcile reports unchanged/no churn.
5. Modify one polygon part in source and confirm only affected UID hash/path changes.

---

_Plan moved from v0.9.23 → v0.9.24 (2026-05-14) → v0.9.25 → v0.9.26 → v0.9.27 → v0.9.28 (2026-05-16) → v0.9.29 (2026-05-17 for enterprise Authentik PG scaling). Original Node-RED scope unchanged._
