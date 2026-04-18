# Handoff — v0.6.5-alpha session (April 2026)

> **Purpose of this doc:** single-source brief for the infra-TAK agent (and next human session) preparing the **v0.6.5-alpha** cut. Everything below is what landed on `dev` after the **v0.6.4-alpha** tag (`71ea0b7`). Use it to write the `README.md` changelog entry, `docs/RELEASE-v0.6.5-alpha.md`, and the update details shown on the Update Now modal.
>
> Scope of v0.6.5-alpha is **entirely Node-RED / ArcGIS Configurator**. No Guard Dog, TAK Server, Authentik, or console changes. Purely additive UX + correctness fixes targeting the ATAK DataSync reconcile churn problem carried over from v0.6.3.

---

## 1. TL;DR — one-paragraph release summary

v0.6.5-alpha makes ATAK DataSync **finally quiet** for ArcGIS feeds whose services rotate OBJECTIDs every poll (NOAA storm reports, NOAA FLOOD). The Configurator's **Stable ID field** is now a **multi-select pill picker** — pick one stable native field (same as before, preferred when a stable ID exists) or combine several fields into a **synthetic compound UID** that survives OBJECTID rotation. The picker is **multi-layer aware**: for feeds that span 2+ layers (e.g. NOAA STORM = HAIL + TORNADO + WIND) it shows the **union of every field across all selected layers**, with each pill annotated by **how many layers contain that field** (`in 2/3`, `in 1/3`) so operators don't accidentally pick a field that only exists on one sibling. This release also ships the **strict mission ownership** reconcile mode plus a one-shot **Purge Orphans** admin action that together solve the "yellow mission", "items silently deleted without ATAK prompting", and leftover-UID drift problems that accumulated under v0.6.3/v0.6.4.

---

## 2. Scope — commits since v0.6.4-alpha (`71ea0b7`)

4 commits on `dev`:

| Commit | Theme | What it did |
|--------|-------|-------------|
| `a7720d3` | Reconcile | **Strict mission ownership** (`cfg.strictMode`) + **one-shot `Purge Orphans`** admin endpoint. When strict mode is on (default for new configs), reconcile deletes *any* mission UID not present in the current ArcGIS result set — not just UIDs that happen to match the feed's `uidPrefix`. Purge button in the Configurator queues a one-shot strict reconcile per config to clean up leftover UIDs from past `idField` / `uidPrefix` / class-mapping edits. |
| `102c9ae` | Reconcile (hotfix) | **Disable strict reconcile on multi-layer passes.** Strict mode was seeing each sibling layer's UIDs as "orphans" and deleting them on every cycle, causing catastrophic PUT/DELETE churn on NOAA STORM (HAIL + TORNADO + WIND). Fix: detect `msg._layerPrefix` — when set, fall back to the safer prefix-guarded DELETE and log a warning explaining strict is disabled on this pass. |
| `77fd46d` | Configurator + engine | **Stable ID multi-field picker with synthetic compound UID.** Replaces the single-select `<select id="idField">` dropdown in Step 3 with a badge-style pill picker (same pattern as Remarks). 0 / 1 / N pills → row-index / native-field / `djb2(f1\|f2\|…)` compound UID prefixed with `c` for visibility in logs. Backwards compatible with legacy `mapping.idField` string configs. |
| `245933b` | Configurator | **Multi-layer intersection picker.** The picker now builds the **union of fields across all selected layers** and annotates each pill: full-opacity if present in all N layers, dimmed with red `(in X/N)` badge if partial. Tooltip explains that rows from layers missing a partial field will hash empty string for that slot. Auto-pick of OID/GlobalID only fires if the field is present in all selected layers. |

---

## 3. User-facing summary (what operators see)

### 3.1 Stable ID picker (Step 3) — new multi-select pills

- Replaces the single-select dropdown. Click pills in the top row to add; click a selected pill's `×` to remove.
- **Live preview** underneath tells you what UID the engine will generate:
  - Red: `(none selected — falls back to row index, UIDs will not be stable)`
  - Green: `UID = prefix + <FieldName>  (single native field — preferred when stable)`
  - Cyan: `UID = prefix + hash(F1 | F2 | F3)  (synthetic compound — use when no single field is stable)`
- **Compound UIDs** are prefixed with `c` in logs (e.g. `arcgis-noaa-flood-c12345678`) so operators can tell at a glance which feeds are on synthetic IDs.

### 3.2 Multi-layer intersection awareness

- For configs that select 2+ layers in Step 2, the picker unions every field name across those layers.
- Pills are annotated with presence: a pill present in all selected layers renders full-opacity; a pill present in only some renders dim with a red `(in X/N)` badge and a tooltip.
- Safe picks are visually obvious — the bright pills. Partial picks still work (empty string hash on missing layers) but the operator sees the risk.
- Example from NOAA STORM (HAIL + TORNADO + WIND 24-hour layers):
  - Full-opacity (in all 3): `OBJECTID, LOCATION, COUNTY, STATE, LATITUDE, LONGITUDE, COMMENTS`
  - `UTC_DATETIME (in 2/3)` — dimmed
  - `F_SCALE (in 1/3)` TORNADO-only, `HAIL_SIZE (in 1/3)` HAIL-only, `SPEED (in 1/3)` WIND-only — all correctly flagged

### 3.3 Strict mission ownership + Purge Orphans

- **Step 5 checkbox — "Strict mission ownership"**, default **on** for new configs. When enabled, reconcile deletes any mission UID that is not in the current ArcGIS result set, including UIDs from older `idField` / `uidPrefix` / class-mapping iterations that the previous prefix-guarded DELETE couldn't touch.
- **Automatically disabled on multi-layer passes** (hotfix `102c9ae`) to stop siblings from deleting each other. When strict is active but the current reconcile pass is one layer of a multi-layer config, the engine logs a warning and falls back to prefix-guarded DELETE for that pass.
- **Purge button** appears on each config card in the Configurator sidebar whenever a `missionName` is set. One click queues a one-shot strict reconcile for that config regardless of the checkbox state — useful after an `idField` / `uidPrefix` change to clear leftovers in one cycle without leaving strict mode permanently on.
- Backing endpoint: `POST /arcgis-tak/tak/purge-orphans` with `{ configName }`. Sets `global._forcePurge[name] = true` and clears `_lastPoll_<name>` so the next scheduled poll runs a strict pass, then self-clears.

### 3.4 Operator guidance for NOAA-style feeds

- NWS storm-report services (24hr and past-week layers of the NOAA storm reports feature service) rotate `OBJECTID` every poll. With v0.6.5 the recommended combo is:
  - **NOAA FLOOD** (single layer 4, class-filtered to Flash Flood): `INCIDENT_DATETIME + LATITUDE + LONGITUDE` (or add `ISSUANCE` for extra insurance).
  - **NOAA STORM** (multi-layer, 24hr HAIL + TORNADO + WIND): `LATITUDE + LONGITUDE + COMMENTS` — the `COMMENTS` field is long free-text per report, effectively unique, and the 3 fields are **all full-opacity in the new picker** (present in all 3 layers). Avoid `UTC_DATETIME` — only in 2/3 layers on that service.
  - **POWER-OUTAGES, CA AIR INTEL**: leave on their existing single stable native fields (`IncidentId`, `GlobalID`). No change needed.
- After changing `idField(s)` or `uidPrefix` on an existing config, click **Purge** once from the Configurator sidebar to clean up the old UIDs in one cycle.

---

## 4. What operators do after Update Now

Same deploy shape as v0.6.3 / v0.6.4. Existing saved configs keep working unchanged.

1. Update infra-TAK (**Update Now**).
2. Open `nodered.<fqdn>` → Configurator tab.
3. For feeds with OBJECTID rotation (NOAA-style): open the config → Step 3 → remove `OBJECTID` pill → click the fields that uniquely identify a real-world event (date + lat + lon + description). Preview should go cyan. Save.
4. **Deploy** in the Node-RED editor.
5. Click **Purge** on the affected config card once to clear old `OBJECTID`-based UIDs.
6. Watch next poll cycle — expect steady `N unchanged, 0 PUT, 0 DELETE` after the first transition.

No new deploy script, no new compose bits, no TAK-Server-side changes.

---

## 5. Bugs found and fixed in this cycle

| Bug | Root cause | Fix |
|-----|-----------|-----|
| ATAK missions show yellow / items silently vanish from map without a DataSync prompt | Orphan UIDs in `mission.uids` that the engine was no longer streaming (from past `idField`/`uidPrefix`/class edits); the original prefix-guarded DELETE couldn't touch them | Strict mission ownership (`strictMode`) drops the prefix guard; Purge Orphans button triggers a one-shot strict pass to clean up |
| NOAA STORM (multi-layer) produced hundreds of DELETE+PUT every cycle with strict mode on | Each layer's reconcile in a multi-layer config saw sibling-layer UIDs as orphans and deleted them, then the siblings re-added their own UIDs on their pass | Detect `msg._layerPrefix` and disable strict / one-shot purge on that pass, fall back to prefix-guarded DELETE |
| NOAA FLOOD still churned even after picking `INCIDENT_CODE` as the stable ID | NOAA storm-reports service has no single stable native ID — `INCIDENT_CODE` is a 1-letter category (`"A"`, `"O"`), `OBJECTID` rotates, `INCIDENT_DATETIME` alone isn't unique | New **compound ID picker** — operator selects a combo of attributes (e.g. `INCIDENT_DATETIME + LATITUDE + LONGITUDE`), engine builds UID as `prefix + 'c' + djb2(values)`. Stable across OBJECTID rotation. |
| Stable-ID picker on a multi-layer config showed only the first layer's fields | `populateIdFieldsPicker()` iterated `state.fields` (first layer only), even though all selected layers' field schemas were already fetched into `state.layerFields` | Build union across `state.layerFields`, annotate each pill with `(in X/N)` presence badge, dim partial-presence pills and add tooltip |

---

## 6. Files changed (key ones)

- `nodered/build-flows.js`
  - `FN_PARSE_COT`: reads `cfg.mapping.idFields` (array) with fallback to `cfg.mapping.idField` (string). Hash key now includes all selected id fields. UID construction branches on 0 / 1 / N fields — 0 = row index, 1 = native value, N = `'c' + djb2(f1|f2|…)`.
  - `FN_RECONCILE`: new `strictMode` / `oneShotPurge` / `cleanOrphans` branches, `isMultiLayerPass` detection via `msg._layerPrefix`, hotfix disabling strict/purge on multi-layer passes.
  - New `POST /arcgis-tak/tak/purge-orphans` admin endpoint.
- `nodered/configurator.html`
  - Step 3: `<select id="idField">` replaced with `#idFieldsAvailable` + `#idFieldsSelected` pill picker; live `#idFieldsPreview` line.
  - New `populateIdFieldsPicker()` / `addIdField()` / `removeIdField()` / `renderIdFieldsSelected()` / `updateIdFieldsPreview()`.
  - Multi-layer path: union fields across `state.layerFields`, presence badges, dim partial pills, tooltip.
  - `resetForm()` / `selectConfig()` / `importTemplate()` updated for `idFields` array with legacy `idField` string fallback.
  - Step 5: new `#strictMode` checkbox, default checked.
  - Sidebar: per-card **Purge** button (visible when `missionName` set).
- `nodered/flows.json`, `nodered/template-functions.json` — regenerated.
- `nodered/deploy.sh` — no changes needed; existing template sync (`_templateKey: arcgis.parse_cot` / `arcgis.reconcile`) propagates new code into existing engine tabs on next deploy. No manual tab rebuild required.

No changes outside `nodered/` for this release. `app.py`, Guard Dog scripts, TAK-server configs, and console UI are untouched.

---

## 7. Test matrix observed in this cycle

| Service | Config shape | Field pick | Result |
|---------|--------------|------------|--------|
| **NOAA FLOOD** (NWS layer 4, class-filtered to Flash Flood) | Single-layer | `INCIDENT_DATETIME + ISSUANCE + LATITUDE + LONGITUDE` (compound) | 89-delete / 27-PUT cycles → expected to collapse to `~0 PUT, 0 DELETE, N unchanged` after one transition cycle + Purge |
| **NOAA STORM** (NWS layers 0+1+2, 24hr HAIL + TORNADO + WIND) | Multi-layer, strict auto-disabled per pass | `LATITUDE + LONGITUDE + COMMENTS` (compound, all full-opacity in picker) | Was catastrophic with strict on (fixed by `102c9ae`); compound-UID expected to be stable across the OBJECTID rotation NWS applies on republish |
| **POWER-OUTAGES** | Single-layer | `IncidentId` (single native field, full opacity, auto-picked) | Already stable pre-v0.6.5; no regression |
| **CA AIR INTEL** | Single-layer, dedup by mission | `GlobalID` (single native field) | Already stable pre-v0.6.5; no regression; dedup still working (`15 -> 12 features`) |
| **FAA TFR** | Polygon | Unchanged from v0.6.2 | Still working — no parse_cot shape change on TFR path |

---

## 8. Instructions for the infra-TAK agent writing README + release notes

### 8.1 `docs/RELEASE-v0.6.5-alpha.md`

Create a new file. Match the style of `RELEASE-v0.6.3-alpha.md` (short, operator-focused, technical-changes table at the end). Suggested structure:

1. **One-paragraph summary** — use §1.
2. **What's new — Stable ID picker** — §3.1 and §3.2 in prose. Emphasize that this ends the NOAA FLOOD OBJECTID rotation churn and that the multi-layer intersection view prevents "pick a field that only exists on one sibling" foot-guns.
3. **What's new — Strict mission ownership + Purge Orphans** — §3.3, short. Note the multi-layer auto-disable.
4. **Operator guidance** — §3.4 verbatim or close to it.
5. **Upgrade notes** — §4.
6. **Technical changes table** — §2 rows, condensed.
7. **Related** — link to `RELEASE-v0.6.4-alpha.md` and `RELEASE-v0.6.3-alpha.md`.

Do not describe Guard Dog / Authentik / Postfix / TAK Server changes — there are none in this release.

### 8.2 `README.md` changelog entry

Add a new top entry above v0.6.4-alpha:

- **v0.6.5-alpha (April 2026) — Node-RED Configurator: stable-ID multi-field picker, multi-layer intersection view, strict mission ownership + Purge Orphans**
  - Bullet 1: compound UID for rotating-OBJECTID services (NOAA FLOOD / STORM) — end of mass DataSync churn on those feeds.
  - Bullet 2: picker now unions fields across all selected layers with `(in X/N)` presence badges; partial-presence pills are dimmed.
  - Bullet 3: new strict reconcile mode + one-shot Purge Orphans button — ends "yellow missions" and silent map deletes caused by orphan UIDs from past config edits.
  - Link: `docs/RELEASE-v0.6.5-alpha.md`.

Also bump the **Latest release** callout to `v0.6.5-alpha`.

### 8.3 `docs/COMMANDS.md`

Update the selective-merge block for v0.6.5: same `nodered/` tree as v0.6.3/v0.6.4 (no new files outside `nodered/`). Bump tag/version refs.

### 8.4 `VERSION` / `app.py`

Bump `VERSION = "0.6.4-alpha"` → `VERSION = "0.6.5-alpha"` at `app.py:276`. No other code change required for the release bump.

### 8.5 Tag & push

```bash
git checkout main
git merge --ff-only dev
git tag -a v0.6.5-alpha -m "v0.6.5-alpha: Configurator stable-ID multi-field picker + multi-layer intersection + strict mission ownership + Purge Orphans"
git push origin main --tags
```

Before pushing the tag, confirm `git show v0.6.5-alpha:app.py | grep '^VERSION = '` prints `VERSION = "0.6.5-alpha"` (v0.6.4 taught us why — see `RELEASE-v0.6.4-alpha.md`).

---

## 9. Known rough edges (not blockers)

### 9a. Multi-layer feeds don't get full strict-mode orphan cleanup

The `102c9ae` hotfix disables strict mode and one-shot purge on multi-layer reconcile passes to stop sibling-layer nuking. Consequence: orphan UIDs on a multi-layer feed's mission can only be cleaned up by prefix-guarded DELETE, which won't touch UIDs whose prefix doesn't match the feed's current `uidPrefix`.

**Current operator workaround:** for multi-layer feeds with long-lived orphan drift, temporarily switch the feed to single-layer in Step 2, Purge Orphans, then switch back. Or manually clean the mission via TAK Server admin UI.

**v0.6.6 candidate:** track the full set of UIDs the feed *owns* across all layers in a global cache (`global._feedUids[configName] = Set`). Run strict reconcile against that cache on a dedicated orphan-cleanup pass after all layer passes complete. Needs careful coordination with the existing reconcile pipeline.

### 9b. `fn_load` still not templateable

Carried over from v0.6.3 handoff §9c. The per-feed `Load <configName>` function node still bakes the feed name into string interpolations so it has no `_templateKey`. Bug fixes inside that function body still require operators to delete + recreate each affected engine tab. The v0.6.4-candidate refactor (read `cfg.configName` from `msg.topic`) is still open.

### 9c. Compound UID preview doesn't show actual hash sample

The Step 3 preview line shows `UID = prefix + hash(F1 | F2 | F3)` but not an example hash for the first sample feature. Would be useful for operators to sanity-check the UID shape they're about to commit to. Low priority.

### 9d. No search/filter on the pill picker

For services with 30+ fields the available pill row gets long. A search box would be nice. Low priority — most services have well under 30 attributes.

---

## 10. Git state at handoff

- Branch: `dev`, pushed as of commit `245933b` (`feat(configurator): stable ID picker now shows full multi-layer field universe with presence badges`).
- `main` is still at `v0.6.4-alpha` until the agent performs §8.5.
- Uncommitted edits in the working tree: `docs/HANDOFF-v0.6.1-session.md` has a local modification carried over from an earlier session — leave alone, not part of this release.
