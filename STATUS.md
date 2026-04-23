# infra-TAK — Working Status

> Update this file at the end of every session. `@STATUS.md` at the start of a new chat to resume instantly.

---

## Active branch
`dev` — VPS pulls from this branch via `git pull && bash nodered/deploy.sh --no-pull`

## VPS
- Host: `172.93.50.47` (tak-10)
- Repo path: `/root/infra-TAK`
- Container: `nodered`
- Configurator: `http://172.93.50.47:1880/configurator`
- Node-RED editor: `http://172.93.50.47:1880`

---

## What was just shipped (latest 3 commits)

| Commit | What |
|--------|------|
| `be3291e` | **fix:** added `tls: 'tls_tak'` to Tablet Command and PulsePoint TCP out nodes — they were missing TLS, CoT would not have flowed over encrypted connection |
| `caff840` | **fix:** deploy.sh now validates context backup before stopping Node-RED; falls back to persistent host snapshot `/opt/tak/nodered-ctx-backup.json`; hard-aborts instead of silently wiping configs |
| `8172389` | **feat:** per-source CoT TCP stream port on all 5 sources (ArcGIS, TC, PulsePoint, TFR, KML) — each source's config panel has its own "CoT TCP stream port" field; global TAK Settings port is the fallback |

---

## What we are testing next

1. **Tablet Command AVL streaming** — configure a TC agency in the Configurator, set a discrete CoT stream port, deploy, verify CoT appears in Node-RED debug and on TAK Server/ATAK
2. **PulsePoint streaming** — same flow, confirm incidents arrive as CoT on the right port
3. **Per-source port routing on TAK Server** — verify each source's ingest port routes to the correct channel/group

---

## Architecture summary

### Data sources and their TAK delivery method

| Source | Streaming TCP CoT | DataSync (Mission API) | Notes |
|--------|:-----------------:|:----------------------:|-------|
| ArcGIS Feature Service | ✅ (toggle) | ✅ (toggle) | dataSyncEnabled checkbox switches mode |
| Tablet Command AVL | ✅ | ❌ | strict streaming only |
| PulsePoint | ✅ | ❌ | strict streaming only |
| FAA TFR | ✅ | ✅ | both simultaneously |
| KML Network Link | ✅ | ✅ | both simultaneously |

### Per-source CoT TCP port
Every source config now saves `cotStreamPort`. build-flows.js resolves port as:
```
Number(cfg.cotStreamPort) || Number(tak.streamingPort) || 8089
```
Leave blank → falls back to global streaming port in TAK Settings.

### Config persistence
Configs live in **Node-RED global context**, not in `flows.json`. deploy.sh backs up context via REST API before stopping NR, validates it, then restores after. Persistent host snapshot: `/opt/tak/nodered-ctx-backup.json`.

---

## Key files

| File | Purpose |
|------|---------|
| `nodered/configurator.html` | All UI — source panels, TAK settings, saved config cards |
| `nodered/build-flows.js` | Generates `flows.json` from configurator + engine templates |
| `nodered/flows.json` | Generated — never edit directly, never `docker cp` directly |
| `nodered/deploy.sh` | Safe deploy: backup context → merge flows → stop → install → restore → start |
| `nodered/static/` | Static assets copied to `/data/public/` in container (logos, icons) |
| `.cursorrules` | Persistent AI rules — read before making changes |

---

## Known issues / watch list
- `Skipped configurator.html template injection (EACCES)` — appears in deploy log when build-flows.js runs inside the container; harmless (template injection already ran on the host side)
- TLS node shows `tls=undefined` in deploy log for dynamic engine tabs (existing tabs from before the TLS fix) — will resolve once those tabs are rebuilt via Configurator

---

## Deploy cheat sheet (VPS)
```bash
# Standard deploy (pull + deploy)
cd infra-TAK && git pull && bash nodered/deploy.sh --no-pull

# If flows.json conflict blocks git pull
git checkout -- nodered/flows.json && git pull && bash nodered/deploy.sh --no-pull

# Force fresh install (NO configs will be restored — destructive)
bash nodered/deploy.sh --force-empty-context
```
