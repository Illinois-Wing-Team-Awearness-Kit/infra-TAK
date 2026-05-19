# v0.9.33-alpha — Node-RED ArcGIS multipart polygon / polyline support + reconciler 0-features guard

**Date:** 2026-05-19
**Type:** Feature release — drop-in update via Update Now.
**Status:** RELEASED 2026-05-19 — field validated on tak-10 (test12.taktical.net) against live CalFire FIRIS and POWER OUTAGES ArcGIS feeds.

---

## TL;DR

ArcGIS feeds that return multipart geometry (e.g. a wildfire feature with a main perimeter + spot-fire polygons) now render **all outer polygons** in TAK DataSync, not just the first ring. Each outer ring becomes a separate CoT event with a deterministic `-r0`, `-r1`, … UID suffix. Multipart polylines fan out similarly with `-p0`, `-p1`, … suffixes. Single-ring / single-path feeds are entirely unchanged.

---

## What changed

### `nodered/build-flows.js` — `FN_PARSE_COT`

**Before (v0.9.32 and earlier):**
- Polygon geometry: centroid and vertices always taken from `g.rings[0]` only. All other rings silently ignored.
- Polyline geometry: path always taken from `g.paths[0]` only.
- One CoT event emitted per ArcGIS feature regardless of ring/path count.

**After (v0.9.33):**
- `ringArea2x(ring)` helper computes the signed area of a ring via the shoelace formula. Outer rings (clockwise in ArcGIS REST / signed area ≤ 0) are kept; interior hole rings (CCW / signed area > 0) are skipped; rings with fewer than 4 vertices are skipped as degenerate.
- If no rings pass the outer-ring test (non-standard winding source data), all rings are treated as outer (defensive fallback — better to show all than nothing).
- Per-part fan-out: each outer ring becomes one `_parts` entry with its own centroid, vertices, UID, and hash.
- UID suffix: single-ring features keep their existing UID (no change, no re-stream on upgrade). Multipart features append `-r0`, `-r1`, … so every ring is a distinct mission Map Item.
- Per-part hash: `djb2(featureHash + '|r' + idx + '|' + ringGeoKey)` — ring-level change detection. Changing one spot-fire polygon doesn't re-stream the main perimeter.
- Callsign: all ring parts of a feature share the same callsign (base UID or configured label). Human-readable labeling stays coherent ("Big Creek Fire" appears on each ring polygon).
- Reconcile / DELETE: no changes to reconciler logic. Orphaned ring UIDs (rings that disappear between polls) are automatically DELETEd from the mission by the existing reconciler.

### `nodered/template-functions.json` + `nodered/flows.json`

Regenerated from `build-flows.js` — `arcgis.parse_cot` template updated.

### `nodered/build-flows.js` — `FN_RECONCILE` — 0-features delete guard

Added a second guard before the delete loop. When ArcGIS returns `200 OK` with zero features
(TTL gap, brief source outage, quiet polling moment), all deletes are now skipped — same behavior
as an HTTP error response. Previously, a 0-feature 200 response wiped every UID from the mission;
ATAK fired a spurious "mission deleted" notification and content recovered on the next poll.

### `docs/NODERED.md`

"Multi-polygon support" open item replaced with a full behavior description of the v0.9.33 implementation.

---

## Upgrade notes

**No operator action required.** This is a behavior change only in `FN_PARSE_COT`:

- Existing feeds with single-ring polygon features: zero visible change. UID unchanged, hash unchanged, no extra re-stream on first post-upgrade poll.
- Feeds with multipart polygon features: first post-upgrade poll emits one CoT per outer ring. Mission gains `-r1`, `-r2`, … UIDs in addition to the existing `-r0` (which is the same as the old single-UID). Old single-UID (no suffix) is preserved as `-r0` equivalent — wait, actually for multipart, the base UID becomes `-r0` (since `_isMultiPoly = true` applies to the whole feature). So on the first poll of a previously single-tracked multipart feature, the old UID disappears and `-r0`, `-r1`, … appear. The reconciler will DELETE the old UID and PUT the new ring UIDs. This is a one-time transition on first post-upgrade poll — expected and correct.

---

## Field validation (tak-10, 2026-05-19)

- [x] CalFire FIRIS feed (CA AIR INTEL): Sandy fire (2 rings — main perimeter + spot fire) correctly produces 2 Map Items in DataSync (`-r0`, `-r1`)
- [x] Santa Rosa fire (2 rings): main perimeter + 14-acre secondary polygon, both emitted
- [x] Dedup by `mission` field + `poly_DateCurrent` timeField: only latest FIRIS snapshot per incident survives
- [x] POWER OUTAGES ArcGIS feed: 148–154 active outages tracked; 0-features guard confirmed in logs; no spurious "mission deleted" ATAK notifications observed
- [x] Reconciler 0-features guard: verified `skipping deletes to protect mission contents` log line fires on quiet polls
- [x] Single-ring feeds: behavior unchanged from v0.9.32
- [x] Post-update auto-deploy: `_auto_nodered_flows()` ran `deploy.sh --no-pull` automatically on Update Now; no manual deploy required
