# infra-TAK v0.6.3-alpha — Node-RED ArcGIS Configurator (multi-layer, per-class icons, epoch-ms time)

Release date: April 2026

---

## Summary

v0.6.3-alpha completes the operator-facing Node-RED ArcGIS DataSync path in the **Configurator**: **multi-layer feeds**, **points and polygons in one config**, **per-class icons and colors** (Step 3c), **template-based labels and remarks**, **ALL CAPS callsigns**, **uniform icon picker** for single-class point layers, **template export/import**, **epoch-millisecond time fields** (for NOAA-style services), and more. It also fixes reconciliation issues that caused mass ATAK notifications after deploys (OBJECTID rotation sensitivity, duplicate engine tabs, burst polling, unstable geometry hashing, stale `_subscribed` cache), plus Configurator state leaks when switching between saved feeds.

**Stack note:** This release is **Node-RED / Configurator focused** (`nodered/`). The console only bumps **`VERSION`** so **Update Now** surfaces the new flows and `deploy.sh` behavior. **Guard Dog** still auto-deploys on version change (scripts + timers), then **Node-RED post-update** runs (compose hardening, `settings.js`, **`nodered/deploy.sh --no-pull`** flow sync) — same pattern as v0.6.2.

---

## What’s new in the Configurator

### Multi-geometry, multi-layer, per-class icons

- Select **multiple layers** from one ArcGIS Feature Service (Step 2). One flow queries them all and fans out per layer.
- **Point and polygon** in the same entry — Step 4 hides polygon-only style fields when geometry is Point and shows the icon path instead.
- **Step 3c — Class mapping:** different **icons and colors per feature class** (e.g. `INCIDENT_TYPE`). Each class row expands with ATAK preset swatches, custom color, **no color** (native icon tint), and per-class CoT type.
- **Uniform icon picker** remains for single-class point layers.
- Multi-layer configs can **auto-class** from layer names so each layer gets its own style without a domain field.

### Labels, remarks, UIDs

- **Template-based label and remarks** — compose callsigns and remarks from field tokens; empty values are stripped (no orphan `|` separators).
- **Custom label** + **ALL CAPS** for CoT callsign.
- **UID prefix** derives from service/layer; multi-layer configs add a layer slug so UIDs do not collide across layers.

### Time filtering

- **Time field** selects the timestamp attribute.
- **Time window** (rolling N minutes/hours/days) filters the ArcGIS query and sets CoT `stale`.
- **“Time values are epoch milliseconds”** — for services like NOAA storm reports where the time column is numeric epoch ms. The engine emits `field >= <cutoffMs>` instead of a `DATE 'YYYY-MM-DD'` literal.

### Icon picker

- **Grid / list** toggle so long 2525B / iconset names are readable.
- Search across mounted icon sets.

### Template export / import

- Export a saved mapping as JSON and import it on another service/layer as a starting point.

---

## Reconciliation fixes (fewer spurious ATAK notifications)

- **`_lastPoll` in global context** — survives deploy; removes burst polling right after restart.
- **Duplicate engine tabs** deduplicated by label on deploy.
- **Stable polygon/polyline hash** — vertex count + rounded centroid (not full coordinate + `spatialReference` dumps).
- **`_subscribed` cleared on deploy** — fresh mission subscriptions always apply.
- **Class value dedup** in `fetchClassValues` (no hundreds of duplicate rows).

---

## Configurator correctness

- **Step 4** (TTL, labels, remarks, time window) stays visible for **point** configs when class mapping is active — only the uniform-icon sub-block toggles.
- **`selectConfig()`** calls **`resetForm()`** first so switching saved configs (e.g. NOAA → another feed) does not leak `classField`, `classMappings`, `remarksFields`, `timeFieldEpochMs`, etc.

---

## Upgrade notes (operators)

1. **Update Now** (or pull `main` / tag and restart `takwerx-console`).
2. Open **`nodered.<fqdn>`** → **Configurator**.
3. For **epoch-ms** layers (e.g. NOAA): set **Time field**, enable **epoch milliseconds**, set rolling window (e.g. 24 h).
4. **Save**, then **Deploy** in the Node-RED editor if prompted. TLS path unchanged from v0.6.2 (`/certs/admin.pem` + passphrase).

Existing saved configs keep working; new fields default sensibly.

### Rotating OBJECTID (NOAA-style feeds)

If the service republishes and **OBJECTID** values rotate, reconcile may still show DELETE+ADD churn — **identity** is still `idField`, not the content hash. Prefer a **stable business ID** (e.g. `INCIDENT_CODE`, `IncidentId`, `GlobalID`) in the Configurator when available.

---

## Technical changes (condensed)

| Area | Change |
|------|--------|
| **Configurator** | Multi-layer checkboxes, Step 3c class panels, icon grid/list, template label/remarks, export/import, epoch-ms flag, `resetForm` / Step 4 visibility |
| **Engine (`build-flows.js`)** | Epoch-ms query branch, global `_lastPoll`, stable geometry hash, multi-layer fan-out + `takSettings`, `_subscribed` reset, icon catalog `fs` lib |
| **Deploy** | Engine tab label dedup in `deploy.sh` |
| **Artifacts** | Regenerated `flows.json`, `template-functions.json` |

---

## Related

- [RELEASE-v0.6.2-alpha.md](RELEASE-v0.6.2-alpha.md) — DataSync operator end-state (certs, `FEEDS` empty, post-update compose).
- [RELEASE-v0.6.0-alpha.md](RELEASE-v0.6.0-alpha.md) — Guard Dog disk I/O, swappiness, Node-RED introduction.
