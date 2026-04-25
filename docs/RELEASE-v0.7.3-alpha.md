# v0.7.3-alpha Release Notes

**Definitive fix for the chronic Configurator-configs-getting-wiped-on-update bug + auto-heal for already-corrupted state.**

---

## ⚠️ Action Required: Resync LDAP to TAK Server

If you haven't already done this, **do it now.**

Go to **TAK Server page → Resync LDAP to TAK Server**.

This fixes password changes taking up to 24 hours to propagate to ATAK/iTAK devices. After Resync, new passwords take effect within 2 minutes. Applies to every existing deployment.

---

## The big one: Configurator configs no longer get wiped on update — and corrupted state is auto-healed on startup

This was the chronic bug: saved ArcGIS feeds, Tablet Command agencies, PulsePoint agencies, TAK Settings, and IPAWS config were getting blown away on `Update Now` runs and people had to use Emergency Restore. **No more.**

There were actually **four** independent bugs chained together. The first three are the persistence layer. The fourth is the recovery layer for anyone whose state was already corrupted by the first three.

### Bug 1, 2, 3: persistence (the original chronic bug)

The configs live in Node-RED's **global context** (`global.set('arcgis_configs', …)` etc.) and persist on disk at `/data/context/global/global.json` via the `localfilesystem` context store. Three things were silently breaking that persistence:

1. **`flushInterval` was missing from fresh-deploy `settings.js`.** Node-RED's default is **30 seconds**. So a config saved within 30 seconds of any container restart (compose-recreate, SIGKILL on slow shutdown) was *only in memory* — never on disk.

2. **The migration path in `_auto_nodered_settings()` wrote the wrong on-disk format.** When upgrading older installs without `contextStorage`, we were writing the raw REST API response (`{default: {arcgis_configs: {msg: "...", format: "..."}}}`) directly to `global.json`. But Node-RED's `localfilesystem` store expects plain `{key: value}` on disk. Result: Node-RED loaded the file, saw a single bogus key called `"default"`, and `global.get('arcgis_configs')` returned `undefined`. **All configs invisible.**

3. **The migration used `docker cp`, which writes as root.** Node-RED runs as `node-red` (UID 1000) and got `EACCES: permission denied` on startup — same bug v0.7.2 patched with a chown but with a race.

### The fix

| File | Change |
|------|--------|
| `app.py` (fresh deploys) | `settings.js` template now sets `contextStorage.default.config.flushInterval: 0` so every `global.set()` is **synchronous to disk**. Also adds `fs: require('fs')` to `functionGlobalContext`. |
| `app.py` (`_auto_nodered_settings`) | Migration now NORMALISES the REST API response (strips `default` namespace, unwraps `{msg, format}` envelopes) before writing `global.json` — so Node-RED actually reads the configs back on startup. |
| `app.py` (`_auto_nodered_settings`) | Migration switched from `docker cp` to `docker exec -i nodered sh -c "cat > …"` so the file is written **as the `node-red` user**. No more `EACCES`, no chown race. Falls back to `docker cp + chown` only if `docker exec -i` fails (older Docker). |
| `nodered/deploy.sh` | Already had the matching normalisation, `docker exec cat >` write path, and `flushInterval: 0` settings.js patch from v0.7.2 prep work — those continue to apply on every deploy as belt-and-suspenders. |

### Bug 4: corrupted state from past versions auto-heals on startup

Even after fixing bugs 1-3, real-world testing turned up a fourth, subtler bug: on boxes that had been through earlier corrupted-write cycles, `arcgis_configs` could end up on disk as a JSON-stringified **literal string** instead of an array:

```json
{
  "arcgis_configs": "[{\"configName\":\"CA AIR INTEL\",...}]",
  "tc_configs": [ { ... } ]
}
```

Note `arcgis_configs` is `"..."` (a string) vs `tc_configs` which is `[...]` (a real array). The Configurator UI handled this case (`fn_load` does `JSON.parse(string)`), so the UI looked correct. **But every dynamic engine tab does this**:

```js
var configs = global.get('arcgis_configs') || [];
for (var i = 0; i < configs.length; i++) { ... }
```

When `configs` is an 800-character string, `configs.length` is **800** (the character count). The loop iterates 800 times, with `configs[i]` being a single character like `"["` or `"{"`. `configs[i].configName` is always `undefined`. The engine finds nothing, logs `no config in global arcgis_configs`, and silently does nothing. Symptom: UI shows your CA AIR INTEL feed, but no polls ever happen.

**The fix is three layers of defense.**

| Layer | What it does |
|-------|--------------|
| `nodered/build-flows.js` (new `ctx_cleanup_fn`) | Runs **at every Node-RED startup** (5s after boot). Walks every known config key, unwraps `{msg, format}` envelopes, JSON.parses stringified arrays, type-coerces to expected shape, initializes missing keys to `[]` or `{}`. **One restart auto-heals corrupted context in place — no Emergency Restore needed.** |
| `nodered/build-flows.js` (`fn_deploy_restore`) | Now type-coerces every restored value via an `EXPECTED` type map. A bad string can never get stored where an array is expected. Missing keys (like `pp_configs` after the multi-agency migration) get initialized instead of skipped. |
| `nodered/deploy.sh` (python normalize) | Now strict and loud: type-coerces in python too, initializes missing keys, prints warnings inline (`COERCED arcgis_configs: str -> []`), surfaces failures with `!! NORMALIZE FAILED` instead of silently keeping raw API data. |

After updating, watch the Node-RED debug sidebar for one of:

- `Context auto-heal: normalized arcgis_configs(N)` — your N configs were string-corrupted, now they're back as a real array
- `Context auto-heal: initialized empty pp_configs` — missing key, recreated as `[]`
- `Context auto-heal: all keys clean` — you were already healthy

**For your data:** if `arcgis_configs` was stored as a stringified array (the most common corruption mode), the actual config bytes are still on disk — `JSON.parse` recovers them losslessly. Your `serviceUrl`, `where` clauses, field mappings, colors, remarks templates — everything comes back exactly as you saved it. If a key was completely missing (e.g. `pp_configs` lost during multi-agency migration), it gets re-created as empty and you'll need to re-add those agencies in the Configurator.

### What this means for you

- **New installs**: Saves are written to disk *the instant you click Save*. Configs are durable across any restart, kill, or update.
- **Existing installs (any version)**: On the next `Update Now`, `deploy.sh` will patch your `settings.js` to add `flushInterval: 0` if it's missing, normalise any in-memory state correctly, and write it to disk with proper ownership. After that one update, you're on the same durable footing as a fresh install.
- **Emergency Restore is back to being an emergency tool**, not a routine post-update step.

### If your configs are gone right now

Use **Configurator → ⋯ menu → Restore from backup** as before. Pick the most recent snapshot that has your configs. After restore, do `Update Now` to land on v0.7.3-alpha — that update should be the last one where you have to think about this.

---

## Bug Fix carried over from v0.7.2: Node-RED start race fixed

v0.7.2 introduced the post-`docker cp` `chown` to fix `EACCES` on the migration path, but the chown raced against Node-RED's startup on some servers (Node-RED would `open()` the file before chown finished). v0.7.3 closes the race entirely by **never letting `docker cp` write the file in the first place** — `docker exec ... cat >` writes as the `node-red` user from the start, no chown needed.

If a server is stuck on v0.7.1/v0.7.2 with Node-RED crash-looping, run on the host:

```bash
VOL=$(docker inspect nodered --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}')
chown -R 1000:1000 "$VOL/context"
docker restart nodered
```

Then **Update Now** to v0.7.3-alpha.

---

## Other fixes carried into this release

- **Caddy cert display thresholds**: green ≥30 days, red <30 days. No more yellow/orange "warning" state for an auto-renewing cert. Renewal fires before 30 days; if we've gone below that, renewal failed and we go straight to red.
- **Guard Dog cert alerts**: TAK Server JKS alert moved from ≤40 days to ≤25 days (matches the 30-day renewal window — alert only if renewal *missed*).
- **TAK Server JKS auto-renewal**: window is 35 days (renews from Caddy's `.crt`/`.key` once Caddy itself has rolled at ≤30d).
- **External / Managed Database deployment mode**: AWS RDS, Azure DB for PostgreSQL, Google Cloud SQL all supported as alternatives to the bundled PostgreSQL container. See `docs/EXTERNAL-DB-SETUP.md`.
- **ArcGIS save reliability**: 15-second timeout on save, hard error reporting on hang, fixed `TypeError: configs.findIndex` from stringified context, no more "Saving…" hangs forever.
- **Multi-agency PulsePoint**: PulsePoint configs now follow the same model as Tablet Command — multiple agencies, each with its own credentials and CoT stream port.
- **Color picker warnings**: silenced the "specified value …" warnings that appeared while typing partial hex codes.
- **PulsePoint + Tablet Command logos** in the top nav bar.

---

## Files changed

| File | Why |
|------|-----|
| `app.py` | `flushInterval: 0` + `fs: require('fs')` in fresh-deploy `settings.js`; migration normalises format and writes as `node-red` user |
| `nodered/deploy.sh` | Already had the matching write-as-`node-red` and `flushInterval: 0` patches; runs on every deploy |
| `nodered/build-flows.js` | `_coerceArr` guards on every `fn_save`/`fn_*_save`; new `ctx_cleanup_fn` flow node auto-heals corrupted context on every Node-RED startup; `fn_deploy_restore` now type-coerces via `EXPECTED` map and initializes missing keys |
| `nodered/deploy.sh` | Python normalize is strict + loud (logs warnings, surfaces failures, type-coerces, initializes missing keys) |
| `nodered/configurator.html` | 15s save timeout, color picker warning fix, parallelised loads |
| `docs/EXTERNAL-DB-SETUP.md` | New — managed/external PostgreSQL setup guide |
| `docs/GUARDDOG.md` | Updated alert thresholds |
| `scripts/guarddog/tak-cert-watch.sh` | Alert at ≤25 days (not ≤40) |
