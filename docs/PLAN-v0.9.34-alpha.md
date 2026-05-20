# Plan — v0.9.34-alpha: Node-RED KML multipart polygon / polyline support

**Status:** IMPLEMENTED — pending field validation on dev.

---

## Problem

The KML → CoT pipeline (`FN_KML_TO_FEATURES`) had the same limitation as the ArcGIS pipeline did before v0.9.33: only the first polygon or path was processed per placemark.

KML expresses multi-polygon features via `<MultiGeometry>` containing multiple `<Polygon>` children, e.g.:

```xml
<Placemark>
  <name>Big Fire</name>
  <MultiGeometry>
    <Polygon>  <!-- main perimeter -->
      <outerBoundaryIs>...</outerBoundaryIs>
    </Polygon>
    <Polygon>  <!-- spot fire -->
      <outerBoundaryIs>...</outerBoundaryIs>
    </Polygon>
  </MultiGeometry>
</Placemark>
```

The old code used `.match()` which only returned the first `<Polygon>` match, so multi-polygon placemarks silently dropped all rings after the first.

---

## Fix

`FN_KML_TO_FEATURES` — `parsePlacemarks()` in `nodered/build-flows.js`:

**Before:**
- Single `.match()` for `<Polygon>` → builds `rings: [ring]` (one ring)
- Single `.match()` for `<LineString>` → builds `paths: [path]` (one path)

**After:**
- `polyRe` exec loop over all `<Polygon>` children → builds `rings: [ring0, ring1, …]`
- `lsRe` exec loop over all `<LineString>` children → builds `paths: [path0, path1, …]`
- Single-polygon / single-path placemarks: identical output (rings/paths array has one entry)

The multi-ring/path geometry objects feed directly into `FN_PARSE_COT` — the same fan-out logic added in v0.9.33 handles them with no further changes needed.

---

## Scope

- `nodered/build-flows.js` — `FN_KML_TO_FEATURES` (~15 lines changed)
- `nodered/template-functions.json` + `nodered/flows.json` — regenerated
- `app.py` — VERSION → 0.9.34-alpha

No changes to reconciler, configurator, or any ArcGIS flow path.
