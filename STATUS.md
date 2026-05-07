# infra-TAK — Working Status

> Update this file at the end of every session. `@STATUS.md` at the start of a new chat to resume instantly.

---

## Active branch
`dev` — VPS pulls from this branch via `git pull && bash nodered/deploy.sh --no-pull`

## VPS
- Host: `172.93.50.47` (tak-10)
- Repo path: `/home/takwerx/infra-TAK`
- Container: `nodered`
- Configurator: `http://172.93.50.47:1880/configurator`
- Node-RED editor: `http://172.93.50.47:1880`

---

## What was just shipped (latest 3 commits)

| Commit | What |
|--------|------|
| `e21ddba` | **fix:** `ldap-authentication-login` session_duration=seconds=120 — password changes now propagate within 2 min instead of 24 hrs; revert token_validity (wrong field, silently ignored) |
| `104d056` | **fix (reverted):** token_validity=minutes=2 on LDAP provider — this was the wrong field; superseded by e21ddba |
| `fa1e859` | **fix:** TC AVL flow creation + add test connection button |

---

## What we are testing next

1. **LDAP password propagation** — confirmed working on tak-10; new password takes effect immediately, old password rejected within 2 min
2. **Node-RED work (separate chat)** — Tablet Command AVL streaming, PulsePoint, per-source port routing

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
