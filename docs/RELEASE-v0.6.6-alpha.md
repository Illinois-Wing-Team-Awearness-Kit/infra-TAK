# infra-TAK v0.6.6-alpha — Guard Dog disk I/O fixes + toggles; Node-RED deploy safety

Release date: April 2026

---

## Summary

**v0.6.6-alpha** hardens **Guard Dog’s disk I/O performance monitor**: the “percent drop” alert math is corrected, operators can **turn the benchmark timer on/off** and **mute only disk I/O email/SMS** while keeping other Guard Dog alerts. The console syncs systemd and a small on-disk flag so behavior matches the UI after **Update Guard Dog** or restart.

**Node-RED:** **`nodered/deploy.sh`** now **stops the container before writing** merged `flows.json`, then restores **global and flow context** from backup so **Configurator saves** (ArcGIS/KML configs, TAK settings) are not wiped by a hot reload during deploy. This release also includes **KML engine** and **Configurator** polish (see below). See **[docs/TESTING-NODERED-DEPLOYS.md](TESTING-NODERED-DEPLOYS.md)** — still **never** raw `docker cp` of `flows.json` into the container.

---

## Guard Dog — Disk I/O performance

### Bug fix: bogus “100% drop” alerts

The watch script used **`bc` with `scale=0`**, which truncated the ratio **last-hour / 24-hour** to **0** whenever it was below 1. That made **`(1 − 0) × 100 = 100`**, so even a **~1%** noise swing could fire the “70%+ drop” path and spam mail. The calculation now uses adequate precision (**`scale=2`**) and compares with **`bc`** correctly.

### UI: benchmark timer toggle

- **Guard Dog → Disk I/O Performance → “Run disk I/O benchmark (systemd timer, CSV + dashboard)”**  
  Controls whether **`takdiskioguard.timer`** is enabled. Setting is **`guarddog_diskio_monitor_enabled`** (default on).  
- When **Guard Dog is globally disabled**, the preference is still saved; the timer runs again only after **Enable**, consistent with other timers.

### UI: mute only disk I/O notifications

- **“Send email & SMS for disk I/O degradation”** — uncheck to keep the **benchmark + CSV + dashboard** but **not** send email/SMS for this monitor. Other Guard Dog alerts are unchanged.  
- Setting: **`guarddog_diskio_email_enabled`** (default on). The console maintains **`/opt/tak-guarddog/diskio_email_off`** when alerts are off.

### API

- **`POST /api/guarddog/diskio-monitor`** — JSON body may include **`enabled`** and/or **`email_enabled`** (booleans). Updates settings, applies **`takdiskioguard.timer`**, and syncs the email-off file.

### Plumbing

- **`_guarddog_sync_diskio_email_off_file()`** runs on deploy, **Update Guard Dog**, **Enable Guard Dog**, and **`_auto_update_guarddog()`** so servers stay consistent after pull/restart.

---

## Node-RED / Configurator

### Deploy safety (`deploy.sh`)

- **Stop → write `flows.json` → restore credentials + context → start** avoids persisting an empty global context during a running-container hot reload (which could clear Configurator data). Backs up **`global.json`** and legacy **`flow_arcgis_cfg.json`** before merge, restores after install.

### KML polling engine (runtime fix)

- **Problem:** The KML→features path used **`require('url'/'https'/'http')`** in a **function** node → **`ReferenceError`** in Node-RED’s sandbox.
- **Fix:** Regenerated engine tab in **`build-flows.js`**: **`build_kml` → GET KML → `FN_KML_CHECK_NL` (two outputs) → GET inner KML → `parse_kml` → parse/reconcile**. **`FN_KML_CHECK_NL`** resolves NetworkLink with **`new URL()`** (no `require`); no async in function nodes on this path.

### KML Configurator (UX)

- **Step 3 Save section** matches **ArcGIS**: Save & Generate Config JSON, Copy, Download, Export Template, Import Template; status line; JSON output.
- **Poll interval** in Step 3 (**`kmlPollIntervalStep3`**) stays in sync with Step 1.
- **Fresh config:** after Fetch, **no auto-selection** of stable ID / label / remarks (blank defaults until the operator picks).
- **Reopen saved config:** runs a live **Fetch**, shows the sample table, then restores saved field selections (ID, time, dedup, label, remarks).
- **`buildKmlConfigObject()`** shared by save / copy / download / export; remarks preview placeholder cleanup.

### Visual — source type & nav

- **Google Earth** logo on KML source-type control and top nav pill.
- **ArcGIS** and **FAA** logos on ArcGIS / FAA TFR source-type selection buttons.

### Docs touch-up

- **[nodered/CHANGELOG-nodered-v0.6.5-alpha.md](../nodered/CHANGELOG-nodered-v0.6.5-alpha.md)** and **[RELEASE-v0.6.5-alpha.md](RELEASE-v0.6.5-alpha.md)** — finer detail on KML save / auto-fetch / engine where the first pass was light.

Full Node-RED bullet list: **[nodered/CHANGELOG-nodered-v0.6.6-alpha.md](../nodered/CHANGELOG-nodered-v0.6.6-alpha.md)**.

---

## Upgrade notes

1. **Update Now** (or checkout tag + **`sudo systemctl restart takwerx-console`**).  
2. **Guard Dog → ↻ Update Guard Dog** once (scripts, timers, disk I/O flags).  
3. **Node-RED:** from repo root on the server, **`./nodered/deploy.sh --no-pull`** after pulling (or rely on post-update flow if your version runs it).  
4. Confirm **Disk I/O** checkboxes and, if you use disk I/O mail, **Send test email** still works.

---

## Pre-release testing (maintainers)

Follow **[docs/TESTING-UPDATES.md](TESTING-UPDATES.md)** (fake **`VERSION`**, **Update Now** end-to-end) **before** pushing the **Git tag**. Node-RED smoke tests: **[docs/TESTING-NODERED-DEPLOYS.md](TESTING-NODERED-DEPLOYS.md)**.

**Release to `main`:** **[docs/COMMANDS.md](COMMANDS.md)** → **Merge dev → main (selective — release only)** — update the **`docs/RELEASE-v0.6.6-alpha.md`** path, commit message, **`tag`**, and Python **`VERSION`** check; **`git push origin main`** then **`git tag` / `git push origin v0.6.6-alpha`**.

---

## Related

- [RELEASE-v0.6.5-alpha.md](RELEASE-v0.6.5-alpha.md) — KML Configurator + ArcGIS stable-ID / Purge.  
- [GUARDDOG.md](GUARDDOG.md) — monitor list includes disk I/O performance.
