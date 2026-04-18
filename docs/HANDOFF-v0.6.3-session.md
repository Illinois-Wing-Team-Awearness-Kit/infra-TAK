# Handoff — v0.6.3-alpha session (April 2026)

> **Purpose of this doc:** single-source brief for the infra-TAK agent (and next human session) preparing the **v0.6.3-alpha** cut. Everything below is what has landed on `dev` since the **v0.6.2-alpha** tag (`a26d495`). Use it to write `README.md` changelog entry, `docs/RELEASE-v0.6.3-alpha.md`, and the update details shown on the Update Now modal.
>
> Scope of v0.6.3-alpha is **almost entirely Node-RED / ArcGIS Configurator**. No Guard Dog, TAK Server, Authentik, or console changes. Nothing breaking — purely additive UX + correctness fixes.

---

## 1. TL;DR — one-paragraph release summary

v0.6.3-alpha completes the operator-facing Node-RED ArcGIS DataSync path: the Configurator now handles **multi-layer feeds**, **points and polygons in the same config**, **per-class icons/colors** (Step 3c), **template-based labels and remarks**, **ALL CAPS callsigns**, **uniform icon picker for single-class point layers**, **template export/import**, **epoch-millisecond time fields** (for NOAA-style services), and much more. It also fixes a cluster of reconciliation bugs that caused mass ATAK notifications after deploys (OBJECTID rotation, duplicate engine tabs, burst polling, unstable geometry hashing, stale `_subscribed` cache), plus several state-leak bugs in the Configurator when switching between saved configs.

---

## 2. Scope — commits since v0.6.2-alpha (`a26d495`)

48 commits on `dev`. Grouped by theme:

### 2a. ArcGIS Configurator — new features

| Commit | What it added |
|--------|---------------|
| `5d95d1d` | ArcGIS **multi-geometry** + **per-class icons** + **template export/import** |
| `34ab4e7` | **Multi-layer selection** — check multiple layers in one config; one flow queries them all, fans out per-layer |
| `fa0f660` | Hide polygon style fields when selected geometry is **Point** |
| `c4d774b` | **Uniform icon picker** for single-class point layers |
| `09d0c11` | Icon picker: **grid/list toggle** so long icon names are fully visible |
| `7d8475e` | **Custom label** + **ALL CAPS** option for ArcGIS callsign / map label |
| `f443a72` | **Per-layer remarks fields**, custom label, and per-layer field fetching for multi-layer configs |
| `7a08b78` | **No-color option** for per-class icons — lets ATAK render the native 2525B/icon-set color |
| `ee93b38` | **ATAK preset color swatches** + custom picker + no-color in class mapping |
| `b2ad391` | **Class mapping refactor** — expandable per-class style panels (Step 3c) |
| `b219ec8` | **Template-based label and remarks** with field insert buttons |
| `e5b64cc` | **Pill-toggle UX** for label and remarks field selection |
| `63bcf1e` | Clean up empty field values in templates — no orphan spaces or pipes in rendered remarks |
| (this session, commit `1faee6d`) | **Time field epoch-ms checkbox** — `mapping.timeFieldEpochMs` — enables true rolling time window for services like NOAA storm reports where `INCIDENT_DATETIME` is stored as epoch ms. Engine emits `field >= <cutoffMs>` instead of a `DATE 'YYYY-MM-DD'` literal. |

### 2b. Reconciliation / notification churn fixes

| Commit | What it fixed |
|--------|---------------|
| `8e7f032` | **`_subscribed` cache cleared on every deploy** — prevents stale subscribe-skip after flow deploy/restart |
| `91531f7` | **Dedup duplicate engine tabs** on deploy (same label, different IDs) so the same feed isn't polled twice |
| `c7e9e28` | **Fix burst polling after deploy** — moved `_lastPoll` from flow context (wiped on deploy) to **global context** keyed by feed name; stabilized feature hash |
| `e9c17d2` | **Stable polygon/polyline hash** — vertex count + rounded centroid instead of full coordinate dump with `spatialReference` |
| `519518b` | **Class mapping dedup** — `fetchClassValues` was returning hundreds of duplicates; now uses a `seen` map like `fetchDistinct` |

### 2c. Configurator state-leak / correctness (this session)

| Commit | What it fixed |
|--------|---------------|
| `1faee6d` | **Step 4 (Time window / TTL / labels / remarks / UID) no longer hidden** when class mapping (Step 3c) is active on point layers. Only the uniform-icon sub-block is hidden. |
| `1faee6d` | **`selectConfig()` now calls `resetForm()` first**, so switching saved configs (e.g. NOAA → CA AIR INTEL) no longer leaks `classField`, `classMappings`, `remarksFields`, `timeFieldEpochMs`, etc. from the previous selection. |
| `1faee6d` | **Missing values applied, not skipped** — if a loaded config has no `classField`/`classes`, the UI now explicitly blanks them instead of leaving prior state visible. |

### 2d. Multi-layer plumbing

| Commit | What it fixed |
|--------|---------------|
| `64ca19d` | Only include layer name in UIDs for **multi-layer** configs (single-layer configs get clean UIDs without the layer suffix) |
| `3502295` | Pass `takSettings` through the multi-layer fan-out so every layer inherits mission/cert config |
| `5278e0f` | Icon catalog: declare `fs` module in Node-RED function `libs` (was using `require()` which fails under functionExternalModules) |

---

## 3. User-facing summary (what operators see)

### 3.1 Multi-geometry, multi-layer, per-class icons

- A single Configurator entry can include **multiple layers** from one ArcGIS Feature Service (select multiple layers in Step 2).
- A single entry can cover **point and polygon geometry** — Step 4 automatically hides polygon-only style fields when geometry is Point, and shows the icon picker instead.
- **Step 3c — Class Mapping** assigns **different icons and colors per feature class** (e.g. `INCIDENT_TYPE` → different icon for Flash Flood vs Hail). Each class row expands into its own style panel with ATAK preset color swatches, custom color picker, a “no color” option (lets the native icon color render), and per-class CoT type.
- **Uniform icon picker** still available for single-class point layers (no Step 3c needed).
- **Layer auto-class** — multi-layer configs auto-populate class values from layer names so each layer can get its own icon/color without needing a domain field.

### 3.2 Labels, remarks, UIDs

- **Template-based label and remarks** — click pill-style field toggles in Step 4 to compose a callsign template like `{INCIDENT_TYPE} — {LOCATION}` and a remarks template like `Reported: {INCIDENT_DATETIME} | Magnitude: {MAGNITUDE}`. Empty values are stripped so there are no orphan ` | ` separators.
- **Custom label** + **ALL CAPS** override for the CoT callsign.
- **UID prefix** auto-derives from the service/layer name; multi-layer configs include the layer slug in UIDs so points from different layers don’t collide.

### 3.3 Time filtering

- **Step 3 — Time field** selects the attribute that holds the timestamp.
- **Step 4 — Time window** (N minutes/hours/days) filters the ArcGIS query server-side **and** sets CoT `stale`.
- New **“Time values are epoch milliseconds”** checkbox under Time field — required for services like NOAA storm reports where `INCIDENT_DATETIME` is a 13-digit epoch-ms number. Without it, the engine emits a `DATE 'YYYY-MM-DD'` literal that doesn’t match numeric storage and only gives a calendar-day cutoff.

### 3.4 Icon picker

- **Grid / list view toggle** so long 2525B or iconset names are fully readable in list view.
- Searchable across all mounted icon sets.

### 3.5 Template export / import

- Each saved config can be exported as a reusable JSON template (mapping, style, class mapping, remarks template, TTL, UID prefix, Data Sync flag) and re-imported on a different service/layer as a starting point.

### 3.6 Reconciliation (ATAK notification churn)

- After-deploy **burst polling is gone** (`_lastPoll` is global, survives deploy).
- **Duplicate engine tabs** from previous deploys are automatically de-duplicated by label on every deploy.
- **Polygon/polyline hashes are stable** across poll cycles — vertex count + rounded centroid only, no spatial-reference metadata.
- **Stale `_subscribed` cache** is cleared on every deploy so fresh mission subscriptions always take effect (never need to wipe `global.json`).

---

## 4. What operators do after Update Now

Identical shape to v0.6.2-alpha. Only the Configurator UI gains new fields; existing saved configs keep working (missing fields default to prior behavior).

1. Update infra-TAK (`Update Now`).
2. Open `nodered.<fqdn>`, go to the **Configurator** tab.
3. Click a saved config card → **Step 4 is visible** (this was broken on point configs before v0.6.3 — see §5 known-issue fix).
4. For **epoch-ms layers** (e.g. NOAA Storm Reports past week): pick the **Time field**, tick **“Time values are epoch milliseconds”**, set **Time window = 24 hours** for last-24h.
5. Save, then **Deploy** in the Node-RED editor (TLS node unchanged since v0.6.2 — still mounts `/certs/admin.pem` + `/certs/admin.key` + key passphrase).

No new deploy script, no new compose bits, no TAK-Server-side changes.

---

## 5. Bugs found and fixed in this cycle

| Bug | Root cause | Fix |
|-----|-----------|-----|
| After deploy, ATAK received hundreds of add/remove notifications every poll | Burst polling (flow-context `_lastPoll` wiped on deploy), duplicate engine tabs, OBJECTID rotation, unstable geometry hashing (full `JSON.stringify` including `spatialReference`) | Global `_lastPoll` keyed by feed; tab dedup on deploy; stable ID fields doc’d; geometry hash = vertex count + rounded centroid |
| Mission subscribe skipped on fresh deploy (stale `_subscribed` cache) | `_subscribed` map survived deploys in global context with no reset | `Build subscribe URL` node clears `_subscribed` in its `initialize` block on every deploy/restart |
| Class mapping (Step 3c) showed hundreds of duplicate values | `fetchClassValues()` didn’t dedupe; the `/arcgis-tak/arcgis/distinct` response can include raw duplicates | Dedupe with a `seen` map, same pattern as `fetchDistinct()` |
| Step 4 (TTL / labels / remarks) disappeared on point configs | `fetchClassValues()` + `autoPopulateLayerClasses()` hid all of `#step4` whenever a class field was set | Replaced with `updatePointStyleNoteVisibility()` that only toggles the uniform-icon sub-block; Step 4 always visible |
| Opening one saved config rendered the previous config’s class mapping / remarks on top of it | `selectConfig()` did not reset form state before loading the selected config | Call `resetForm()` first; then explicitly blank `classField`, `classMappings`, `remarksFields`, `timeFieldEpochMs` when the new config doesn’t specify them |
| Rolling time filter did nothing on NOAA-style services | Engine always emitted `timeField >= DATE 'YYYY-MM-DD'`, which doesn’t match epoch-ms numeric fields and is only a calendar-day cutoff anyway | New `mapping.timeFieldEpochMs` flag; engine emits `timeField >= <cutoffMs>` when true |
| Multi-layer configs: UIDs collided between layers | Single-layer UID prefix logic applied | Include the layer slug in UIDs only when the config is multi-layer |
| Multi-layer configs: second/Nth layer lacked TAK settings | `takSettings` wasn’t attached to fan-out messages | Pass `msg.takSettings` through the fan-out loop |
| Icon catalog loader crashed | Used `require('fs')` at runtime under Node-RED functionExternalModules | Declared `fs` as a `libs` entry on the function node |

---

## 6. Files changed (key ones)

- `nodered/build-flows.js` — `FN_BUILD_QUERY` (epoch-ms branch, rolling time filter), stable geometry hash, global `_lastPoll`, multi-layer fan-out, `takSettings` propagation, `_subscribed` cache clear.
- `nodered/configurator.html` — Step 2/3/3b/3c/4 UI additions: multi-layer checkboxes, geometry-aware style toggle, class-mapping Step 3c with expandable panels, icon picker grid/list toggle, uniform icon picker, template-based label/remarks with pill toggles, ATAK preset color swatches + no-color option, template export/import, epoch-ms checkbox, `updatePointStyleNoteVisibility()`, `resetForm()` on `selectConfig`.
- `nodered/deploy.sh` — engine tab label dedup.
- `nodered/flows.json`, `nodered/template-functions.json` — regenerated.
- `.cursorrules` — unchanged; still enforces empty `FEEDS` array (no static tabs shipped).

No changes outside `nodered/` for this release. `app.py`, Guard Dog scripts, and console UI are untouched.

---

## 7. Test matrix observed in this cycle

| Service | Geometry | Fields used | Result |
|---------|----------|-------------|--------|
| **NOAA Storm Reports — past week** | Point | `INCIDENT_TYPE` (class), `INCIDENT_DATETIME` (time, epoch ms), `LOCATION` (label) | Per-class icons render in ATAK; last-24h filter works with epoch-ms checkbox |
| **CA AIR INTEL** (pre-existing polygon config) | Polygon | Existing OBJECTID/geom | Opens cleanly with Step 4 intact; no cross-contamination from previously opened NOAA config |
| **Power Outages** (pre-existing) | Point | Stable `IncidentId` as ID field | No OBJECTID-rotation churn; dedup stable across poll cycles |
| **FAA TFR** | Polygon | `notam_id` | Unchanged from v0.6.2 — still working |

---

## 8. Instructions for the infra-TAK agent writing README + release notes

### 8.1 `docs/RELEASE-v0.6.3-alpha.md`

Create a new file. Match the style of `RELEASE-v0.6.2-alpha.md` (short, operator-focused, technical-changes table at the end). Suggested structure:

1. **One-paragraph summary** — use §1 of this doc.
2. **What’s new in the Configurator** — flatten §3 into prose. Lead with **Per-class icons & colors**, **Multi-layer / multi-geometry**, **Epoch-ms time filter**, **Template-based labels & remarks**, **Template export/import**.
3. **Reconciliation fixes** — short subsection, bullet per fix from §2b.
4. **Configurator correctness fixes** — brief: Step 4 restored for point configs, state no longer leaks between saved configs. Reference §2c.
5. **Upgrade notes** — §4 verbatim (or nearly).
6. **Technical changes** table — §2 rows, condensed.
7. **Related** — link to `RELEASE-v0.6.2-alpha.md` and `RELEASE-v0.6.0-alpha.md`.

Do not describe Guard Dog / Authentik / Postfix / TAK Server changes — there aren’t any in this release. Link to v0.6.0-alpha for those.

### 8.2 `README.md` changelog entry

Add a new top entry above v0.6.2-alpha:

- **v0.6.3-alpha (April 2026) — Node-RED Configurator: multi-layer, per-class icons, epoch-ms time filter, reconciliation fixes**
  - 2–3 bullets summarizing §3.1, §3.3, and §3.6.
  - Link: `docs/RELEASE-v0.6.3-alpha.md`.

Also update the **Latest release** callout to `v0.6.3-alpha` and point the Node-RED operator path at the new release doc (still reads v0.6.2 end-state but add a sentence that v0.6.3 extends the Configurator).

### 8.3 `docs/COMMANDS.md`

Update the selective-merge block for v0.6.3: include the same `nodered/` tree that was in v0.6.2 (no new files outside `nodered/`). Bump tag/version refs.

### 8.4 `VERSION` / `app.py`

Bump `VERSION = "0.6.2-alpha"` → `VERSION = "0.6.3-alpha"` at `app.py:276`. No other code change required for the release bump.

### 8.5 Tag & push

```bash
git checkout main
git merge --ff-only dev    # or merge commit if a fast-forward isn't possible
git tag -a v0.6.3-alpha -m "v0.6.3-alpha: Node-RED Configurator multi-layer + per-class icons + epoch-ms time filter + reconciliation fixes"
git push origin main --tags
```

Then delete/refresh `agent-transcripts` cache if needed; Guard Dog on all boxes will surface Update Available.

---

## 9. Known rough edges (not blockers)

- **No explicit custom date range** in Step 4 — only a rolling window ending at `now()`. If operators need “between X and Y”, they currently have to use the ArcGIS-side `source.where` by hand-editing the saved JSON. Not in v0.6.3; candidate for v0.6.4.
- **Icon catalog is read at deploy time**; adding new icon sets to `/opt/tak/icons` requires a `./deploy.sh`. Documented; fine for now.
- **Large services (>5k features)**: Configurator still previews only the first 50 features. Filtering UI scales fine, but the preview table isn’t paginated. Not a regression — carried over from v0.6.2.

### 9a. OBJECTID rotation on NOAA-style services — hash does not save us

**Problem observed 2026-04-17 on NOAA FLOOD feed.** With `idField = OBJECTID`, a steady-state reconcile produced ~80 DELETE + ~15 PUT on a ~40-item feed, despite no real-world changes. Cause: NOAA republishes the "past week" storm reports feed periodically and **all OBJECTIDs rotate**. Every UID (`uidPrefix + idField`) changes → reconcile treats every item as "old UID gone, new UID added" → mass ATAK notifications every cycle.

**Important clarification — the hash is for _change detection_, not _identity_:**

```js
// nodered/build-flows.js (FN_PARSE_COT)
var hp = [gKey];                                                // geometry
hp.push(String(a[cfg.mapping.idField] || ''));                  // ID field value
if (cfg.style.labelField) hp.push(String(a[cfg.style.labelField] || ''));
if (cfg.remarksFields) { for (...) hp.push(...); }
var _hash = djb2(hp.join('|'));                                 // change-detection hash

var uid = (cfg.uidPrefix || 'arcgis') + layerTag + String(idVal);  // identity still = idField
```

The **hash** only tells the engine whether a given **UID**'s content changed (skip vs re-stream). The **UID** itself is still built from `idField`. If `idField` rotates, the UID-level match in reconcile fails and items look deleted+added.

**Current operator workaround (v0.6.3):** pick a stable non-OBJECTID field. For NOAA storm reports, `INCIDENT_CODE` appears stable and matches what was done on the known-good NOAA STORM (multi-layer) and POWER-OUTAGES (`IncidentId`) feeds.

**v0.6.4 candidate — synthetic/compound ID option in the Configurator:**

When the service exposes no stable native ID (or the operator wants extra insurance), let Step 3 offer:

- A multi-select **"Compound ID fields"** picker (e.g. `INCIDENT_DATETIME + LATITUDE + LONGITUDE + INCIDENT_TYPE`).
- Engine builds UID from a hash of those fields' values instead of a single attribute.
- Stable across OBJECTID rotation because the underlying attributes describe the real-world event.

Implementation notes (for the agent doing v0.6.4):

1. Extend `mapping` schema in the saved config: `mapping.idFields` (array) alongside existing `mapping.idField` (string). If both present, `idFields` wins.
2. In `FN_PARSE_COT`, change:
   ```js
   var idVal = a[cfg.mapping.idField] || ('f' + i);
   ```
   to:
   ```js
   var idVal;
   if (cfg.mapping.idFields && cfg.mapping.idFields.length) {
     var parts = cfg.mapping.idFields.map(function(f) { return String(a[f] || ''); });
     idVal = djb2(parts.join('|'));  // stable synthetic ID
   } else {
     idVal = a[cfg.mapping.idField] || ('f' + i);
   }
   ```
3. Configurator Step 3: add an **"Advanced — synthetic ID"** toggle that reveals the multi-select. When enabled, `idField` is disabled and `idFields` is used.
4. Test matrix: NOAA FLOOD with compound `INCIDENT_DATETIME + LATITUDE + LONGITUDE + INCIDENT_TYPE` should give steady `0 streamed, N unchanged, 0 PUT, 0 DELETE` after one transition cycle.

**Until v0.6.4 ships:** operator guidance is to pick a stable attribute or accept per-republish churn on NOAA-style feeds. Document in the README changelog for v0.6.3 that feeds with rotating OBJECTIDs should use a non-OBJECTID ID field.

### 9b. Icon vs color mismatch between ATAK map and mission list

**Problem observed 2026-04-17.** User selected an iconset PNG (`Hazard Flood`) and a blue class color in Step 3c. Mission list shows a **blue bullet**, map shows the **original black/white icon** — no tint. Not a bug; ATAK only tints iconset PNGs that were authored as monochrome templates.

Operator options (already documented in v0.6.3 release path):

- Want blue markers on map → **clear the icon**, keep color → ATAK's default point marker renders blue.
- Want the icon to show cleanly → **pick the no-color (⊘) swatch** in Step 3c → list badge goes neutral.
- Leave as-is → list color = quick class identifier, map icon = specific incident type.

No engine change needed. A future nice-to-have: detect monochrome PNGs in the icon catalog and flag them as "tintable" in the picker.

### 9c. Load function node — not templateable (existing dynamic tabs miss code fixes)

**Observed 2026-04-17 while fixing `INVALID_EXPR` for spaces in configName.** The per-feed `Load <configName>` function node in each dynamic engine tab has the feed name baked in via several string interpolations (`configs[i].configName === '<name>'`, `node.warn('Polling: <name>')`, `topic: '<name>'`, etc.), so it carries **no `_templateKey`**. Result: bug fixes inside that function body do not auto-propagate via `deploy.sh` template sync — operators have to delete + re-save each affected engine tab.

Today's fix (`af7e721`, sanitize `pollKey` for `global.get`/`set`) is already in `ENGINE_TAB_TEMPLATE`, so **new** tabs are fine. Existing tabs created pre-fix must be recreated.

**v0.6.4 candidate:** refactor `fn_load` to read `cfg.configName` entirely from `msg.topic` (set by the inject node) or by looking up the enclosing tab's label. That removes the string interpolations, the function body becomes identical across all feeds, and it can carry a `_templateKey` like the other shared engine nodes — deploy.sh will auto-sync future fixes with no manual tab rebuild.

---

## 10. Git state at handoff

- Branch: `dev`, pushed as of commit `1faee6d` (`Fix Step 4 (TTL) missing on point configs + stale state on config switch`).
- `main` is still at `v0.6.2-alpha` until the agent performs §8.5.
- No uncommitted changes in `nodered/`. `docs/HANDOFF-v0.6.1-session.md` has a local modification from a prior session — leave alone or roll its content into §9 of that doc; not part of this release.
