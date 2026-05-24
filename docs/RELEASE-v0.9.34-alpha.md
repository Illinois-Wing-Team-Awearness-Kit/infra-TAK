# v0.9.34-alpha — Node-RED KML multipart polygon / polyline support + dedup multi-ring fix

**Date:** 2026-05-19
**Type:** Feature release — drop-in update via Update Now.
**Status:** RELEASED 2026-05-19 — field validated on tak-10 (test12.taktical.net) against live CalFire FIRIS KML feed, CA AIR INTEL ArcGIS feed, and POWER OUTAGES ArcGIS feed.

---

## TL;DR

Two related fixes for multi-ring geometry in Node-RED feeds:

1. **KML `<MultiGeometry>` support** — KML feeds whose placemarks use `<MultiGeometry>` with multiple `<Polygon>` or `<LineString>` children now render all rings/paths in TAK DataSync, not just the first one.

2. **Dedup multi-ring fix (FIRIS KML format)** — FIRIS and similar KML sources store each ring as a **separate placemark** all sharing the same `source` field. The previous dedup logic collapsed these to one ring per source (keeping only the first). The fix keeps **all rings from the latest snapshot** per dedup group. Also fixes date-string timestamp comparison (`Created On: 5/19/2026 11:22 AM`) which previously parsed as `NaN` and fell back to keeping whichever ring appeared first in the KML.

---

## What changed

### `nodered/build-flows.js` — `FN_KML_TO_FEATURES`

**Before (v0.9.33 and earlier):**
- `block.match(/<Polygon…>…<\/Polygon>/i)` — returns only the first `<Polygon>` in a placemark.
- A `<MultiGeometry>` with 3 polygons produced `rings: [firstRing]` — other polygons silently dropped.
- Same issue for `<LineString>`: first path only.

**After (v0.9.34):**
- `polyRe` exec loop iterates over **every** `<Polygon>` in the placemark block, extracting its `<outerBoundaryIs>` ring. Builds `rings: [ring0, ring1, …]`.
- `lsRe` exec loop iterates over **every** `<LineString>`, building `paths: [path0, path1, …]`.
- Single-polygon / single-path placemarks produce a one-element array — identical downstream behavior to v0.9.33.
- `FN_PARSE_COT`'s v0.9.33 multipart fan-out handles the multi-element arrays with no further changes.

### `nodered/build-flows.js` — `FN_BUILD_QUERY` — dedup multi-ring fix

**Before:**
- Dedup kept exactly one record per `dedupField` group using `Number(timeField)` comparison
- `Number("5/19/2026 11:22 AM")` = `NaN` → comparison always false → first record per group wins (wrong)
- FIRIS stores each ring as a separate placemark all with `source = "CA-VNC-SANDY-N42Z"` → all rings after the first were dropped

**After:**
- `toMs(v)` helper: passes epoch numbers through; falls back to `new Date(v).getTime()` for date strings — correctly parses FIRIS's `Created On` format
- Groups now hold `{ maxT, records: [] }` — all records matching the latest timestamp are kept
- FIRIS Sandy with 4 rings in latest snapshot → 4 CoT events; Sandy-N42Z with 2 rings → 2 CoT events
- ArcGIS feeds with epoch-ms timestamps: identical behavior to v0.9.33 (numeric comparison still used, single latest record kept when only one record has the max timestamp)

### `nodered/template-functions.json` + `nodered/flows.json`

Regenerated — `arcgis.parse_cot` (dedup logic) and `kml.xml_to_features` templates updated.

---

## Upgrade notes

**No operator action required.** Drop-in behavior change:

- KML feeds with single-polygon placemarks: zero visible change.
- KML feeds with `<MultiGeometry>` placemarks: first post-upgrade poll emits one CoT per outer ring (e.g. `-r0` main perimeter + `-r1` spot fire). The reconciler DELETEs the old single UID and PUTs the new ring UIDs. One-time transition on first poll — expected and correct.

---

## Field validation (tak-10, 2026-05-19)

- [x] FIRIS KML feed (KML FIRIS → mission KML TEST): `dedup by source: 24 -> 19 features` (was 12 before fix) — confirmed in Node-RED log
- [x] Transition poll: `8 streamed, 11 unchanged, 8 PUT, 1 DELETE` — reconciler correctly promoted new ring UIDs and cleaned stale single-ring UID
- [x] CA AIR INTEL ArcGIS feed: `0 streamed, 65 unchanged` — dedup behavior unchanged for epoch-ms timestamps
- [x] POWER OUTAGES ArcGIS feed: `0 streamed, 173 unchanged, 13 DELETE` — dedup and reconcile unaffected
- [x] All 19 FIRIS map items visible in TAK DataSync KML TEST mission after transition poll
- [x] Context survived deploy — `arcgis_configs(3)`, `tc_configs(1)`, `tak_settings` all restored
