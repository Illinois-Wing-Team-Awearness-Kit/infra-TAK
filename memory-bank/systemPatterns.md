# System Patterns — infra-TAK

> Architecture, key technical decisions, and design patterns. Updated when major patterns change.

## High-level architecture

```
                       ┌─────────────────────────────────────┐
                       │  Operator browser                   │
                       │  https://<host>/                    │
                       └────────────────┬────────────────────┘
                                        │ HTTPS :443 (FQDN mode)
                                        │ HTTPS :5001 (IP / backdoor)
                                        ▼
                       ┌─────────────────────────────────────┐
                       │  Caddy (reverse proxy + LE)         │
                       │  - infratak.<fqdn>      → :5001     │
                       │  - auth.<fqdn>          → Authentik │
                       │  - portal.<fqdn>        → TAK Portal│
                       │  - nodered.<fqdn>       → :1880     │
                       │  - tak.<fqdn>           → :8446     │
                       │  - cloudtak/map/stream/fedhub.<fqdn>│
                       └────────────────┬────────────────────┘
                                        │
       ┌────────────────────────────────┼────────────────────────────────┐
       ▼                                ▼                                ▼
┌────────────────┐              ┌─────────────────┐              ┌──────────────┐
│ infra-TAK app  │              │ Authentik (4 ct)│              │ TAK Server   │
│  app.py        │ ←── auto ──→ │ server / worker │ ←── LDAP ──→ │ + Postgres   │
│  Gunicorn :5001│   configures │ ldap / postgres │   :389/636   │  /opt/tak/   │
└────────────────┘              └─────────────────┘              └──────────────┘
       │                                                                 │
       │  ssh / docker / subprocess                                      │
       ├─────────► TAK Portal  (forward auth via Caddy)                  │
       ├─────────► Node-RED    (:1880, Configurator at /configurator)    │
       ├─────────► CloudTAK    (browser TAK client)                      │
       ├─────────► MediaMTX    (RTSP/WebRTC/HLS)                         │
       ├─────────► Federation Hub (local OR remote SSH)                  │
       ├─────────► Email Relay (Postfix on localhost:25)                 │
       └─────────► Guard Dog   (systemd timers + watch scripts)          │
                                                                          │
                                ┌────────────── pg_dump / restore ───────┘
                                │
                          ┌─────▼──────┐
                          │ Snapshots  │  /opt/tak/snapshots/<label>/
                          │ - cot.pgdump
                          │ - CoreConfig.xml
                          │ - UserAuthenticationFile.xml
                          │ - takserver.default
                          │ - certs/
                          └────────────┘
```

## Key architectural decisions

### 1. Single-file backend (`app.py`, ~36k lines, 2.1 MB)

**Decision:** All console code lives in one Python file. No package layout, no blueprint registry, no per-module file split.

**Why:** Easy to grep, easy to diff, easy to audit. One file = one source of truth. Every module's compose template, Authentik blueprint, helper, and migration lives next to the route that uses it. New maintainers can `rg "function_name"` and find every caller. Refactoring into a package layout has been considered and rejected — the cost of cross-file imports and lost grep-ability outweighs aesthetic benefits.

**Rule of thumb:** When adding a feature, append it; do not create a new file unless the feature is genuinely an external surface (e.g., `nodered/build-flows.js` is a Node.js subsystem, not Python).

### 2. Idempotent post-update migration ladder

**Decision:** Every release ships migrations as `_run_post_update()` in `app.py`, called automatically when the version on disk differs from the recorded `settings.last_version`.

**Pattern:** Each migration is a self-contained function: detects current state, no-ops if already migrated, applies the change otherwise, records outcome to `settings.<feature>_<migration>` (e.g., `settings.authentik_trusted_proxy_cidrs_fix = {last_outcome: 'fixed' | 'idempotent-noop', ...}`).

**Examples:**
- `_authentik_apply_official_tunings` — adds `AUTHENTIK_WEB__WORKERS=4`, `AUTHENTIK_CACHE__TIMEOUT_FLOWS=600`, etc., to `~/authentik/.env` if missing; recreates server+worker only if env actually changed.
- `_authentik_fix_trusted_proxy_cidrs` — appends `AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS=172.16.0.0/12,...` to fix the v0.8.9 silent-default bug.
- `_authentik_fix_ldap_flow_recursion` — flips `evaluate_on_plan: true` → `false` on the three LDAP flow stage bindings (the v0.8.8 fix).
- `_auto_harden_containers` — adds `cap_drop: ALL` / `no-new-privileges` / removes `docker.sock` from compose files (the CVE-2026-31431 fix).
- `_auto_nodered_settings` — migrates Node-RED to filesystem-backed context storage; exports in-memory state via REST API first.

**Cardinal rule:** Migrations that change Authentik env vars must use `_recreate_authentik_server_worker()` (server + worker only). **Never recreate the LDAP outpost** — it has a bind cache, and recreating it triggers thundering-herd reconnects from active TAK clients (the v0.8.1 incident).

### 3. Verify, don't assume (consult upstream docs)

**Pattern:** After applying any non-trivial config, **run the upstream's verification command** and assert the runtime is using the value:
- Authentik: `docker exec authentik-worker-1 ak dump_config` → parse JSON → assert.
- TAK Server: read CoreConfig.xml back, parse XML, assert auth block contents.
- Postgres: `psql -c "SHOW <setting>"`.
- Node-RED: REST API `/admin/context/global` returns actual loaded context.

**Why:** v0.8.2 → v0.8.6 shipped five releases of band-aids on `AUTHENTIK_WEB_WORKERS=4` (single underscore — silently ignored by Authentik 2026.x). Only fixed in v0.8.7 after consulting [docs.goauthentik.io](https://docs.goauthentik.io/install-config/configuration/) and discovering the correct name is `AUTHENTIK_WEB__WORKERS` (double underscore). The runtime verifier (`_authentik_verify_runtime_config`) was added in the same release to make this class of bug impossible to ship again. See `.cursor/rules/consult-upstream-docs.mdc`.

### 4. Node-RED Configurator: dynamic engine tabs in `flows.json`

**Decision:** `flows.json` ships with **zero static feed tabs**. The `FEEDS` array in `nodered/build-flows.js` is intentionally empty. Operator-created feeds (ArcGIS, TFR, KML, Tablet Command, PulsePoint) become **dynamic engine tabs** added at runtime via the Configurator UI.

**Why:** If we shipped named feeds in `flows.json`, every customer would inherit those tabs (e.g., "CA AIR INTEL", "POWER-OUTAGES" — real production feed names). They live in **Node-RED global context**, not in `flows.json`.

**Deploy must preserve dynamic tabs:** `nodered/deploy.sh` does:
1. Backup global context via REST API → `/opt/tak/nodered-ctx-backup.json` on the host.
2. **SAFETY GATE** — abort if no valid context found (operator's saved configs would be wiped).
3. Stop container, merge preserved engine tabs into the new `flows.json`, restore credentials, install.
4. Restore context after start.

**Cardinal rule (in `.cursorrules`):**
> **NEVER raw-copy `flows.json` into the container.** `docker cp flows.json nodered:/data/flows.json` wipes dynamic engine tabs.
> **Always use `./nodered/deploy.sh`.** No exceptions.

### 5. Authentik LDAP outpost: never recreate, never thundering-herd

**Decision:** Anywhere in the migration ladder, a `docker compose up -d --force-recreate worker server ldap` is **forbidden**. Server + worker only. The LDAP outpost (`authentik-ldap-1`) is recreated **only** when the outpost's `docker-compose.yml` configuration genuinely changed (token rotation, routing change, etc.) — never as a side effect of an env var migration.

**Why:** TAK clients (ATAK, iTAK, WinTAK) maintain authenticated sessions backed by the LDAP outpost's bind cache. Recreating the outpost clears the cache, every client reconnects simultaneously, Postgres connection pool exhausts, CPU pegs at 800-1500%. This is the v0.8.1 incident.

### 6. Two-tier configuration: shipped template + idempotent post-update patcher

**Pattern:** For every long-lived compose file (Authentik, TAK Portal, CloudTAK, Node-RED, Federation Hub, MediaMTX), there are **two writers**:
- **Template** (in `app.py`): generates the file on first deploy.
- **`_auto_<service>_*` patcher**: runs on every "Update Now"; idempotently adds new flags / removes deprecated ones / fixes drift on existing installs.

**Why:** A v0.7 customer who upgrades to v0.9.4 needs the new hardening flags applied to their already-deployed compose file. Re-deploying is destructive; idempotent string-anchored patches are not.

### 7. Persistent operator state lives outside the repo

| State | Location | Notes |
|---|---|---|
| Console password (hash) | `<install_dir>/.config/auth.json` (chmod 600) | Pinned in systemd unit; survives `git pull`. |
| Console settings (FQDN, last_version, migration outcomes) | `<install_dir>/.config/settings.json` | Read with `_load_settings()`, write with `_save_settings()`. |
| TAK Server config | `/opt/tak/CoreConfig.xml`, `/opt/tak/UserAuthenticationFile.xml`, `/opt/tak/certs/files/` | Snapshots in `/opt/tak/snapshots/<label>/`. |
| Authentik | `~/authentik/.env`, `~/authentik/docker-compose.yml`, `~/authentik/blueprints/` | Patched idempotently. |
| TAK Portal | `~/TAK-Portal/docker-compose.yml`, `~/TAK-Portal/server/data/settings.json` | Patched idempotently. |
| Node-RED Configurator configs | Node-RED global context (in-container) → backed up to `/opt/tak/nodered-ctx-backup.json` on host | **Never** in `flows.json`. |
| Email Relay | `/etc/postfix/main.cf` (managed) | |
| Guard Dog scripts | `/opt/tak-guarddog/` (deployed from `scripts/guarddog/`) | systemd timers wire them in. |

### 8. Backdoor + recovery as design contracts

- **Backdoor:** `https://<vps_ip>:5001` always responds, never sits behind Caddy or Authentik. Designed to work when everything else is broken.
- **Universal recovery (SSH):** README block does `git fetch https://github.com/takwerx/infra-TAK.git main && git checkout --force -B main FETCH_HEAD` regardless of broken `origin`. Future-proof against forks, typos, mirrors.
- **`fix-console-after-pull.sh`:** if a `git pull` corrupts the systemd unit's `WorkingDirectory`, this rewrites it and resets the password.
- **`reset-console-password.sh`:** updates `.config/auth.json` without needing to log in.

### 9. Branches: `dev` is upstream of customer testing; `main` is what `Update Now` pulls by default

- `dev` — VPS pulls from this branch via `git pull && bash nodered/deploy.sh --no-pull`. Maintainer iteration happens here. Test VPS (`tak-10`, `responder`) tracks `dev`.
- `main` — customer-facing. Tagged releases (`vX.Y.Z-alpha`) live here. README + changelog reflect main.
- **Selective merge** from `dev` → `main` for releases (see `docs/COMMANDS.md`).

## File-level component map

| Path | Role |
|---|---|
| `app.py` | The console (Flask + Gunicorn). All routes, all module logic, all migrations. |
| `start.sh` | Launcher: OS detect → apt locks → Python deps → password prompt → systemd unit → start. |
| `pull-dev-and-restart.sh` | One-line dev-branch pull + restart for maintainer test VPS. |
| `fix-console-after-pull.sh` | Repair systemd unit WorkingDirectory + reset password. |
| `reset-console-password.sh` | Reset `.config/auth.json` admin password. |
| `nodered/build-flows.js` | Generates `flows.json` from `template-functions.json` + Configurator UI. **`FEEDS=[]` always.** |
| `nodered/configurator.html` | The whole Configurator UI (source panels, TAK settings, saved config cards). |
| `nodered/deploy.sh` | Safe deploy — context backup, merge dynamic tabs, stop, install, restore, start. **The only safe way to push flows.** |
| `nodered/flows.json` | Generated artifact. Never edit, never `docker cp` directly. |
| `nodered/static/` | Logos / icons copied to `/data/public/` in the container. |
| `scripts/guarddog/` | All Guard Dog watch scripts (TAK boot sequencer, TAK post-start, per-service watchers). Deployed to `/opt/tak-guarddog/`. |
| `scripts/bootstrap-nodered-flatfile.sh` | (Phase 1A) Adds `<user identifier="nodered">` to UAF + generates flat-file nodered cert. |
| `scripts/nodered-egress-firewall.sh` | (Opt-in) iptables egress allowlist for the Node-RED container. |
| `scripts/set-docker-log-limits.sh` | Apply Docker log-driver limits across all containers. |
| `scripts/fix-mediamtx-stream-redirect.sh` | One-shot MediaMTX repair script. |
| `scripts/ldap-diagnose-and-fix.sh` | Standalone LDAP diagnose tool. |
| `scripts/mediamtx-pill-style-on-server.py` | UI styling helper for MediaMTX page. |
| `static/` | JS for the console (`firewall.js`, `guarddog.js`, `log-tools.js`, `takserver.js`, `authentik-branding/`, logos). |
| `modules/` | Currently empty (`__init__.py` only) — reserved for future modular split. |
| `docs/` | Release notes, plans, HANDOFFs, runbooks, security audits. |
| `.cursor/rules/` | Project rules: `consult-upstream-docs.mdc`, `cursor-memory-bank.mdc`. |
| `.cursorrules` | Top-level Cursor rules — Node-RED safety contracts. |
| `STATUS.md` | Working status — update at end of every session, `@STATUS.md` to resume. |
| `README.md` | Customer-facing documentation. |
| `TESTING.md` | Manual test procedures. |
| `mediamtx_ldap_overlay.py` | Standalone helper for MediaMTX + LDAP integration (legacy / utility). |

## Component relationships (deploy order)

```
1. Caddy SSL          set FQDN, get LE certs
        ↓
2. Authentik          identity provider + LDAP outpost
        ↓
3. Email Relay        SMTP for password recovery (optional)
        ↓
4. TAK Server         upload .deb, deploy, configure
        ↓
5. Connect LDAP       patches CoreConfig, creates webadmin in Authentik
        ↓
6. TAK Portal         user/cert management
        ↓
7. Anything else      CloudTAK, Node-RED, MediaMTX, Federation Hub, Guard Dog
```

Order is enforced because each step's success creates the artifacts the next step expects (FQDN cert, Authentik token, TAK CA, etc.).
