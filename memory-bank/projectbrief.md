# Project Brief — infra-TAK

> Foundation document. All other memory bank files build on this.
> Source of truth for project scope. Update only when scope changes.

## What this project is

**infra-TAK** is a unified, browser-based management console for deploying and operating the entire **Team Awareness Kit (TAK)** ecosystem on a single Ubuntu 22.04 VPS (or split two-server topology).

> **Tagline:** "One clone. One password. One URL. Manage everything from your browser."

The product replaces the traditional pile of TAK admin tasks — SSHing into a host, hand-editing CoreConfig.xml, generating certificates with `makeCert.sh`, managing systemd units, configuring LDAP, wrangling Caddy, deploying federated hubs, etc. — with a single Python (Flask + Gunicorn) web app at port `5001` that automates all of it.

## Core requirements

1. **Single command install.** `git clone … && sudo ./start.sh` is the only thing the operator runs in a shell. After that, everything happens in the browser.
2. **Browser-only operation.** No more SSH for day-to-day operations. Module deploys, restarts, cert rotation, password resets, Authentik wiring, Node-RED flow updates, etc. are all UI-driven.
3. **Self-healing.** Idempotent post-update migrations (`_run_post_update`, the migration ladder in `app.py`) detect and repair drift in compose files, Authentik blueprints, CoreConfig.xml, Node-RED context, etc., on every "Update Now". Operators should never have to manually fix container state after an upgrade.
4. **Authentik-first identity.** All services that can sit behind Authentik forward auth do (TAK Portal, Node-RED, MediaMTX, etc.). LDAP outpost feeds TAK Server auth. Bootstrap is fully automated.
5. **Production-ready by default.** Gunicorn (not Flask dev server), HTTPS-only on `:5001`, Let's Encrypt via Caddy on `:443`, hardened compose files (`cap_drop: ALL`, `no-new-privileges`, scoped mounts).
6. **Recovery without panic.** A backdoor IP-mode admin URL (`https://<vps_ip>:5001`) bypasses Caddy/Authentik so the operator can always get in. `reset-console-password.sh` and `fix-console-after-pull.sh` are documented escape hatches. Universal SSH recovery block in the README pulls a known-good `main`.

## Goals (project north stars)

- **Universal installer.** Currently Ubuntu 22.04 LTS only; the goal is a single installer that supports any modern Linux server distro (RHEL family, Debian family).
- **Zero-touch upgrades.** "Update Now" must always be safe. Every release ships idempotent post-update migrations to fix any latent state drift, and a console rollback path lives in the UI for one-click revert to the previous version.
- **Operator-grade reliability for small/medium TAK deployments.** Target 50–500 user installations: fire/EMS, search-and-rescue, public safety, NGO field ops, defense contractor labs.
- **Truth lives in upstream docs.** When integrating with Authentik / TAK Server / Caddy / Node-RED, read the official documentation first; never trust an `.env` to mean the runtime is using it (see `.cursor/rules/consult-upstream-docs.mdc`).

## What this project is NOT

- It is **not** a fork of TAK Server. We deploy and configure the official `.deb` from tak.gov.
- It is **not** a SaaS. Every install is self-hosted and air-gappable (subject to Let's Encrypt / Authentik bootstrap reachability).
- It is **not** a CoT broker, federation router, or replacement for any TAK service. It is the management plane that wires those services together.
- It is **not** a replacement for ATAK/iTAK/WinTAK clients. Clients connect to the deployed TAK Server normally.

## Scope of components managed

| Component | Role |
|---|---|
| **TAK Server** | The official TAK Server (Java) — `.deb` upload, install, CoreConfig generation, cert management, LDAP wiring, JVM heap tuning, snapshots/rollback, plugin install. |
| **Federation Hub** | Local or remote-SSH deployment of TAK Server Federation Hub (Authentik OAuth, certs, Guard Dog monitoring). |
| **Authentik** | Identity provider — bootstrap creds, LDAP outpost (standalone container), embedded forward auth outpost, blueprint repair, brand/cookie/domain healing. |
| **TAK Portal** | User and certificate management portal (auto-configured Authentik + TAK Server integration). |
| **Caddy** | Let's Encrypt + reverse proxy + forward auth glue + per-vhost custom blocks. |
| **CloudTAK** | Browser-based TAK client (compose-driven). |
| **MediaMTX** | Video streaming server (RTSP/WebRTC/HLS) with Authentik-driven access. |
| **Node-RED** | Flow automation engine; the "Configurator" UI on `/configurator` drives ArcGIS / TFR / KML / Tablet Command / PulsePoint feeds → TAK Server (streaming CoT or DataSync Mission API). |
| **Email Relay** | Postfix on `localhost:25` for password recovery and Guard Dog alerts. |
| **Guard Dog** | TAK Server health monitoring, auto-recovery, boot sequencer, disk I/O timer, container log limits, per-service watch scripts. |
| **Fail2ban** | SSH + MediaMTX RTSP jails with UI controls. |

## Single source of truth

- `app.py` is the entire console (Flask + Gunicorn, HTTPS on `:5001`). It is **2.1 MB / ~36k lines** because every module's compose template, blueprint, helper, and migration lives in one file by design — easy to diff, easy to audit, easy to ship.
- `nodered/` is the Node-RED Configurator subsystem (own deploy script, own changelog, dynamic engine tabs in `flows.json`).
- `start.sh` is the bootstrap: detects OS, waits for apt locks, installs Python deps, sets admin password, writes systemd unit, starts the console.
- `docs/` holds release notes (one file per `vX.Y.Z-alpha`), HANDOFF files, plan docs (`PLAN-vX.Y.Z.md`), runbooks, and incident writeups.
