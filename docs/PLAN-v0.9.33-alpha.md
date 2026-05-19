# Plan — v0.9.33-alpha

> **Status:** IMPLEMENTED — pending field validation and release.
> **Target:** v0.9.33-alpha
> **Scope:** Node-RED ArcGIS multipart polygon / polyline support.
>
> **Origin:** Moved from `PLAN-v0.9.30-alpha.md` (originally v0.9.23 → v0.9.24 → v0.9.25 → v0.9.26 → v0.9.27 → v0.9.28 → v0.9.29 → v0.9.30 → v0.9.31 → v0.9.32 → v0.9.33). Node-RED scope unchanged across all version moves. Implemented 2026-05-19.

---

## Why v0.9.33 exists

ArcGIS feeds that return multipart wildfire geometry (one incident feature containing a main perimeter + spot-fire polygons) currently render only one polygon in DataSync output. The parser path only processes `g.rings[0]`, silently dropping all additional rings.

---

## Item 1 — ArcGIS multipart polygon / polyline support

### What was implemented

**File: `nodered/build-flows.js`** — `FN_PARSE_COT` function

1. **`ringArea2x(ring)` helper** — signed area via shoelace formula. ArcGIS REST outer rings are clockwise (signed area ≤ 0 in Y-up geographic coordinates); interior holes are counter-clockwise (signed area > 0).

2. **Outer-ring detection** — iterates all `g.rings`, keeps only rings with `length >= 4` (non-degenerate) and `ringArea2x <= 0` (outer). If none pass (non-standard winding data), falls back to treating all rings as outer.

3. **Per-part fan-out** — replaces the single `g.rings[0]` / `g.paths[0]` path with a `_parts` array:
   - Polygon: one entry per outer ring
   - Polyline: one entry per path
   - Point: single entry (unchanged behavior)

4. **UID suffix** — single-ring/path features: UID unchanged. Multipart: appends `-r0`, `-r1`, … (polygon) or `-p0`, `-p1`, … (polyline).

5. **Per-part hash** — single: `_hash` unchanged. Multipart: `djb2(featureHash + '|r' + idx + '|' + ringGeoKey)` — ring-level change detection without affecting unrelated parts.

6. **Callsign** — all parts share the base feature callsign (label field / template / base UID). Keeps human-readable labeling coherent for multi-ring fires.

7. **Reconcile / DELETE** — no changes needed. The reconciler already DELETEs mission UIDs not present in the current `_features` array, so vanished ring UIDs are automatically cleaned up.

### Files touched

- `nodered/build-flows.js`
- `nodered/template-functions.json` (regenerated)
- `nodered/flows.json` (regenerated)
- `docs/NODERED.md` (limitation note replaced with behavior doc)
- `docs/PLAN-v0.9.33-alpha.md` (this file)
- `docs/RELEASE-v0.9.33-alpha.md`

---

## Item 2 — Reconciler 0-features false-DELETE guard

### Problem

The existing delete guard only skips deletes on non-200 HTTP responses (`arcgisOk` check). When
ArcGIS returns `200 OK` with zero features (TTL gap, quiet data moment, brief source outage), the
delete loop ran against every existing mission UID, wiping the mission entirely. The next poll
recovered, but ATAK fired a spurious "mission deleted" notification to all subscribers.

### Fix

Added a second guard in `FN_RECONCILE`:

```javascript
} else if (Object.keys(arcgis).length === 0) {
  node.warn(topicCfg + ': 0 features from ArcGIS (status 200) — skipping deletes to protect mission contents');
```

Zero features on a 200 response → skip all deletes, same as a non-200 response.

---

## Acceptance criteria

- [ ] A single ArcGIS feature containing multiple outer rings emits one CoT event per outer ring.
- [ ] Interior hole rings are not emitted as standalone polygons.
- [ ] With `dedupField = missionName`, latest mission record is selected, then all outer rings from that selected feature are emitted.
- [ ] Reconcile behavior stable — no repeated PUT/DELETE churn for unchanged multipart features.
- [ ] Existing single-ring feeds: UID unchanged, hash unchanged, no re-stream on first poll after upgrade.
- [ ] 0-feature poll (HTTP 200) does not delete any UIDs from the mission.

---

## Test plan

1. Configure ArcGIS feed pointing at a service with one feature containing 3 rings (main perimeter + 2 spot fires).
2. Run one poll cycle — Node-RED log should show `3 CoT events built from 1 features`.
3. Verify TAK/DataSync shows all 3 polygons with UIDs `<prefix>-<id>-r0`, `-r1`, `-r2`.
4. Re-run poll with unchanged source — reconcile log should show `0 streamed, 3 unchanged`.
5. Modify one ring in source — confirm only that ring's UID hash changes and only that CoT is re-streamed.
6. Drop one ring in source — confirm the orphaned UID is DELETEd from the mission.
7. Run a single-ring feed — confirm UID has no suffix and behavior is identical to pre-v0.9.33.
