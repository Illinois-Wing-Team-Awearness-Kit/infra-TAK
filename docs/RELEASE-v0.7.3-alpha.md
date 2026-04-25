# v0.7.3-alpha Release Notes

---

## ⚠️ ACTION REQUIRED FOR EXISTING DEPLOYMENTS

After pulling this update, go to the **TAK Server page → Resync LDAP to TAK Server**.

This fixes password changes taking up to 24 hours to take effect on ATAK/iTAK devices. The fix is applied automatically every time Resync runs — one click and your deployment is current.

---

# Part 1 — Immediate Password Propagation via LDAP Session Fix

## What's New

### Password Changes Now Take Effect Immediately

**Problem (pre-v0.7.3):** When a user or admin reset a password in TAK Portal (or directly in Authentik), the new password could take up to 24 hours to work on iTAK/ATAK. The old password continued to authenticate for the remainder of the cached session.

**Root cause:** Authentik's LDAP outpost runs with `bind_mode: cached`. A successful bind is cached for the lifetime of the user's authentication session. The `ldap-authentication-login` User Login stage had `session_duration: seconds=0`, which Authentik interprets as a full browser session — effectively ~24 hours for LDAP binds. Password changes made via TAK Portal (which directly call Authentik's `set_password` API) do not invalidate existing cached sessions.

**Fix:** Set `session_duration: seconds=120` on the `ldap-authentication-login` stage. Cached bind sessions now expire in 2 minutes. A user who changes their password will be able to authenticate with the new password within 2 minutes of the reset.

This is the correct and only reliable knob for this behavior. `token_validity` on the LDAP provider (a previous investigation path) is silently ignored by Authentik for LDAP providers — it only applies to OAuth/proxy providers.

### Why Not `bind_mode: direct`?

Direct mode re-authenticates against Authentik on every single LDAP bind — which TAK Server issues on a ~2-second polling cycle. At scale (hundreds of users, active sessions) this overwhelms the Authentik worker. Cached mode with a short session duration is the correct tradeoff: low resource usage, fast propagation.

### Self-Healing via Resync

The fix is enforced every time **Resync LDAP to TAK Server** runs — the console looks up `ldap-authentication-login` by name and patches `session_duration` regardless of its current value. Existing deployments that ran Resync after pulling this update are already fixed. Fresh deploys get the correct value baked into the blueprint.

## What Changed

| File | Change |
|------|--------|
| `app.py` | Blueprint YAML: `session_duration: seconds=0 → seconds=120` (two copies) |
| `app.py` | `_create_ldap_stage()` call: `seconds=0 → seconds=120` (fresh install path) |
| `app.py` | New unconditional PATCH in `_ensure_ldap_flow_authentication_none()`: looks up `ldap-authentication-login` and patches `session_duration=seconds=120` on every Resync run |
| `app.py` | Removed `token_validity: minutes=2` from all `providers/ldap` PATCH calls (wrong field, was silently ignored) |
| `app.py` | VERSION bumped to `0.7.3-alpha` |

## Operator Action Required

**For existing deployments:** After pulling this update, go to the TAK Server page → **Resync LDAP to TAK Server**. This patches the live Authentik stage immediately. Verify with:

```bash
TOKEN=$(grep AUTHENTIK_BOOTSTRAP_TOKEN ~/authentik/.env | cut -d= -f2)
curl -s -H "Authorization: Bearer $TOKEN" \
  'http://127.0.0.1:9090/api/v3/stages/user_login/?search=ldap' | \
  python3 -c "import sys,json; r=json.loads(sys.stdin.read())['results']; [print(f'name={s[\"name\"]} session_duration={s.get(\"session_duration\")}') for s in r]"
```

Expected output: `name=ldap-authentication-login session_duration=seconds=120`

**Fresh deployments:** No action needed — the blueprint sets the correct value at deploy time.

## Testing

1. Create a user in TAK Portal, set a password, log in on ATAK/iTAK — should work immediately.
2. Reset the password to a new value in TAK Portal.
3. Try the new password on the device — should authenticate immediately.
4. Wait ~2 minutes, then try the old password — should be rejected.

## Files Changed

- `app.py` — fix + VERSION bump
- `docs/RELEASE-v0.7.3-alpha.md` — this file
- `README.md` — latest release updated
- `STATUS.md` — session state updated
- `docs/HANDOFF-LDAP-AUTHENTIK.md` — LDAP cache behavior documented

---

# v0.7.3-alpha — Node-RED Configurator: ArcGIS Save Fix, Multi-Agency PulsePoint, UI Polish

## Critical Bug Fix: ArcGIS Save Hanging Indefinitely

**Problem:** Clicking **Save & Generate Config JSON** in the ArcGIS configurator caused the UI to display "Saving…" and hang forever. The form never collapsed, no success message appeared. This affected every ArcGIS, TFR, and KML config save attempt.

**Root cause (from Node-RED logs):**
```
[error] [function:Save to global context]
TypeError: configs.findIndex is not a function
```

The `fn_save` function node read `global.get('arcgis_configs')` and assumed it was a JavaScript array. After a deploy/restore cycle, the context value was silently stored as a stringified array (`"[]"`) or a localfilesystem REST API envelope (`{msg: "[]", format: "..."}`) rather than a real array. Calling `.findIndex()` on a string throws `TypeError`, the function node never returns `msg`, the http-response node never fires, and the HTTP request hangs until timeout.

`fn_load` had a 3-line unwrap guard (which is why `/arcgis-tak/config/load` returned correctly). The save and all other mutator nodes did not.

**Fix (`nodered/build-flows.js`):**
- New `ARRAY_GUARD` constant defines a `_coerceArr(v)` helper that handles all three rotten formats: `{msg, format}` envelope, double-serialized string, and anything-not-an-array → `[]`
- Injected into: `fn_save` (ArcGIS/TFR/KML), `fn_tc_save`, `fn_tc_delete`, `FN_PP_SAVE`, `FN_PP_LOAD`, `FN_PP_DELETE`
- `unwrapCtxVal` in `/config/deploy-restore` now also handles bare strings — this was the source of the corruption, writing stringified arrays into context on every deploy, planting the landmine that `fn_save` tripped on

**Commits:** `2d11f8a`

## ArcGIS Save UX: 15s Timeout, Console Diagnostics, Race-Free Collapse

Even before the root cause was identified, a series of frontend improvements were made to `generateConfig()`:

- **Hard 15s AbortController timeout** on the save fetch — the UI now shows `"Save timed out after 15s — check Node-RED is running"` instead of hanging silently forever
- **Console diagnostics**: `[save] POST /arcgis-tak/config/save <name>`, `[save] response {...}`, `[save] FAILED <error>` logged at every step for visibility
- **Reordered post-save flow**: reload saved-config list → collapse form → fire ensureEngineTab in background. Previously `ensureEngineTab` triggered a full Node-RED redeploy mid-sequence, causing `/arcgis-tak/config/load` to briefly 502 and blocking the UI collapse
- **`loadSavedConfigs` hardened**: new `_fetchT(url, opts, ms)` helper wraps every Node-RED API call with a 10s AbortController timeout. A single stuck endpoint can no longer hang the page on load or after save. IPAWS/PP/TAK-settings fetches now run in parallel via `Promise.allSettled`

**Commits:** `d23f96e`

## Multi-Agency PulsePoint (like Tablet Command)

PulsePoint was refactored from a single global configuration to a **multi-agency model** matching the Tablet Command design:

- Each agency gets its own named config card in Saved Configurations
- Each agency has its own independent Node-RED engine tab, its own CoT TCP streaming port, and its own PulsePoint API credentials
- Configs stored as `pp_configs[]` array in global context (was a single flat `pulsepoint_config` object)
- Migration: existing single `pulsepoint_config` is automatically migrated to the first entry in `pp_configs[]` on first load
- New API endpoints: `POST /pp/config/save`, `POST /pp/config/delete`, `GET /pp/config/load`
- Each agency's engine tab polls the PulsePoint CAD API, builds CoT for active incidents, and streams to a dedicated TCP port on the TAK server

**Commits:** `fce3a0b`, `68f28e5`

## ArcGIS DataSync Form: Field Visibility by Mode

When **Data Sync Mission** is checked in Step 5 (TAK Integration & Save):
- **CoT TCP stream port** field is hidden (not relevant for DataSync)
- **Saved-list note** field is hidden (streaming-only label)
- **Strict mission ownership** block remains visible

When **Data Sync Mission** is unchecked (broadcast/channel streaming mode):
- Stream port and saved-list note fields are shown
- Strict mission ownership block is hidden

`toggleDataSync()` enforces this on checkbox change, form reset, and config load.

**Commits:** `8e1f196`

## UI: PulsePoint and Tablet Command in Top Nav

PulsePoint and Tablet Command logos and labels are now displayed in the top navigation bar of the Configurator alongside the existing source buttons, providing quick one-click access to their configuration panels.

**Commits:** `4eb7e57`

## Color Input Warning Spam Fixed

Typing a hex color value (e.g. `#FFd500`) into the text companion input was generating a browser console warning on every keystroke:
```
The specified value "#FFd50" does not conform to the required format.
```
This happened because the `oninput` handler was pushing every partial value directly to `<input type="color">`, which rejects anything not matching `#rrggbb`.

**Fix:** New `syncColorPick(pickId, v)` helper only writes to the color picker when the value matches `/^#[0-9A-Fa-f]{6}$/`. Applied to ArcGIS, TFR, and KML stroke/fill text inputs.

**Commits:** `d23f96e`

## Files Changed (Node-RED / Configurator)

| File | Change |
|------|--------|
| `nodered/build-flows.js` | `ARRAY_GUARD` + `_coerceArr()` injected into all CRUD mutators; `unwrapCtxVal` hardened for bare strings; `fn_tc_delete` gains backup snippet |
| `nodered/build-flows.js` | Multi-agency PulsePoint: `pp_configs[]`, `FN_PP_SAVE`, `FN_PP_LOAD`, `FN_PP_DELETE`, `makePulsepointTab()` |
| `nodered/configurator.html` | ArcGIS save: 15s timeout, console diagnostics, race-free collapse, `_fetchT()` on all fetches |
| `nodered/configurator.html` | DataSync field visibility toggle |
| `nodered/configurator.html` | PulsePoint + TC nav bar logos |
| `nodered/configurator.html` | `syncColorPick()` — suppress color input console warnings |
| `nodered/template-functions.json` | PP engine function node templates added |
