# v0.7.4-alpha Release Notes

**Auto-heal for corrupted Node-RED context — engines no longer silently fail when `arcgis_configs` is the wrong shape.**

---

## ⚠️ Action Required: Resync LDAP to TAK Server

If you haven't already done this, **do it now.**

Go to **TAK Server page → Resync LDAP to TAK Server**.

This fixes password changes taking up to 24 hours to propagate to ATAK/iTAK devices. After Resync, new passwords take effect within 2 minutes. Applies to every existing deployment.

---

## The bug v0.7.3 missed

v0.7.3 fixed the three known root causes for *configs disappearing on update*. Testing on a real server with corrupted state in 0.7.3 turned up a fourth, subtler bug that v0.7.3 didn't cover:

**Symptom:** After `Update Now`, the Configurator UI showed all your configs correctly (ArcGIS feeds, TC agencies, etc.) — but the engine tabs that actually poll those feeds **silently did nothing**, and Node-RED logs showed `no config in global arcgis_configs`. Your CA-TFR / AIR-INTEL / ArcGIS engines went dark even though the data was right there in the Configurator.

### Root cause

On disk, in `/data/context/global/global.json`, `arcgis_configs` ended up stored as a JSON-stringified **literal string**:

```json
{
  "arcgis_configs": "[{\"configName\":\"CA AIR INTEL\",...}]",
  "tc_configs": [ { ... } ]
}
```

Note `arcgis_configs` is a `"..."` string vs. `tc_configs` which is a real `[...]` array.

Every dynamic engine tab does this:

```js
var configs = global.get('arcgis_configs') || [];
for (var i = 0; i < configs.length; i++) {
  if (configs[i].configName === 'CA AIR INTEL') { ... }
}
```

When `configs` is a 800-character string, `configs.length` is **800** (the character count). The loop iterates 800 times, with `configs[i]` being a single character like `"["` or `"{"`. `configs[i].configName` is always `undefined`. The engine finds nothing and silently stops.

The Configurator UI's `fn_load` parses the string before returning, which is why the UI looked correct. Engines did not — they trusted that what `global.set()` was given is what `global.get()` returns.

### The fix (three layers, defense in depth)

**Layer 1: Auto-heal on every Node-RED startup (the durable fix).**

A new `ctx_cleanup_fn` flow node runs at every Node-RED startup (5 seconds in). It walks every known config key (`arcgis_configs`, `tc_configs`, `pp_configs`, `tak_settings`, `ipaws_config`) and:

- If the value is a `{msg, format}` wrapped envelope → unwraps it
- If the value is a JSON-stringified literal string `"[...]"` → parses it
- If the value's type doesn't match what's expected (array vs. object) → resets to empty
- If the value is missing entirely → initializes to `[]` or `{}`

Then re-saves via `global.set()`, which writes the cleaned value to disk. **One restart and your context is healed in place**, no manual intervention.

**Layer 2: Type coercion in `fn_deploy_restore`.**

When `deploy.sh` pushes context via REST after a deploy, `fn_deploy_restore` now force-coerces every value to its expected type. If a key is supposed to be an array and the value isn't, it stores `[]`. Better to lose the value and have a working engine than to store a string and have a silently-broken engine.

Missing keys (like `pp_configs` if the multi-agency migration left it blank) are now initialized to empty arrays instead of being skipped. Previously, `pp_configs` could disappear entirely from disk because nothing ever re-created it.

**Layer 3: `deploy.sh` normalize is loud and stricter.**

The python normalize step in `deploy.sh` previously suppressed all errors with `2>/dev/null`. If it failed for any reason, the script silently kept the raw API response. Now:

- stderr is captured to a logfile and printed inline
- Type coercion happens at normalize time, not just at restore time
- All expected keys are guaranteed to exist in the normalized output (initialized to `[]` / `{}` if missing)
- If normalize fails, you'll see `!! NORMALIZE FAILED` in the deploy log instead of silent corruption

| File | Change |
|------|--------|
| `nodered/build-flows.js` | Added `ctx_cleanup_fn` flow node + dedicated startup inject. Strengthened `fn_deploy_restore` with `EXPECTED` type map and per-key coercion. Initializes missing keys instead of skipping. |
| `nodered/deploy.sh` | Python normalize step now type-coerces, initializes missing keys, prints warnings inline, and surfaces failures loudly. |
| `app.py` | Version bump to `0.7.4-alpha`. |

---

## How recovery works for users on 0.7.3 with already-corrupted context

Update to 0.7.4. On the first restart, `ctx_cleanup_fn` will fire 5 seconds in. If your `arcgis_configs` was the bad string, you'll see this in the Node-RED debug sidebar:

```
[warn] [function:Normalize corrupted context values] Context auto-heal: normalized arcgis_configs(N)
```

Where `N` is the recovered config count. After that warn appears, your engines are back online — no Emergency Restore needed.

If `pp_configs` was missing and you have no PulsePoint agencies, you'll see `initialized empty pp_configs` and that's fine.

If you DO need an Emergency Restore, your backups are still at:

- `/data/config-backups/latest.json` (latest deploy)
- `/data/config-backups/backup_<timestamp>.json` (previous deploys)
- `/opt/tak/nodered-ctx-backup.json` (host-side persistent snapshot)

The 0.7.4 deploy.sh writes properly normalized data to all three of those, so future restores from those snapshots are safe.

---

## Carried forward from 0.7.3

All of this still applies:

- `flushInterval: 0` on `localfilesystem` context — every `global.set()` is synchronous to disk
- `docker exec -i ... cat >` for context writes — proper `node-red` ownership, no `EACCES` race
- Migration normalizes the REST API response before writing `global.json`
- ArcGIS save no longer hangs on `Saving…`
- Multi-agency PulsePoint
- External / managed database deployment mode
- Caddy / JKS certificate display logic (green ≥30 days, red <30 days)
- Tablet Command AVL Integration with discrete CoT streaming ports
- LDAP session_duration: 120s (immediate password propagation)

---

## Update path

```bash
# On the server
cd /opt/tak/infra-TAK
git fetch origin
git checkout main
git pull
sudo systemctl restart takwerx-console
```

Then on the Console: **Settings → Update Now**, or run `bash nodered/deploy.sh` if you only want to redeploy Node-RED without touching the rest of the stack.

After deploy, refresh the Configurator and check the Node-RED debug sidebar for the auto-heal warn line. If you see `Context auto-heal: all keys clean` you were already healthy.
