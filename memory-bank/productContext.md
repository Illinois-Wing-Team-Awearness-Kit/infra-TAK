# Product Context — infra-TAK

> Why this project exists. The user, the problems, and the experience we're building.

## Why this project exists

TAK Server (Team Awareness Kit Server) is the open-source, government-released backbone for ATAK / iTAK / WinTAK situational-awareness clients used by fire, EMS, SAR, public safety, defense, and NGO field operations. It is **extremely powerful** and **extremely operator-hostile**:

- The official install path is a `.deb` package, hand-edited XML configuration (`CoreConfig.xml`), bash scripts for cert generation, and a sprinkling of system services that must be wired together correctly.
- LDAP integration involves shipping a custom `<auth>` block in `CoreConfig.xml`, generating a service account, and pointing the server at an LDAP backend. Most operators ship Authentik for this — wiring Authentik correctly is its own multi-hour job.
- TLS, federation, plugin install, certificate rotation, JVM heap tuning, log rotation, and disk monitoring are each their own footguns.
- Field updates ("the new TAK Server build broke our auth") historically meant SSH, edit XML, restart, hope.

The result: a 4-hour-to-multi-day install ritual for every customer, with brittle outcomes. **infra-TAK collapses that ritual into "click Deploy in the browser."**

## Problems we solve

| Problem | What we do about it |
|---|---|
| **TAK Server install is a multi-step bash + XML ritual** | Upload `.deb` in browser → click Deploy → CoreConfig generated, certs made, services started. |
| **LDAP/SSO setup with TAK Server is undocumented and fragile** | Authentik deploy is fully automated: blueprints, outposts, service accounts, CoreConfig patching, webadmin sync — all done by `_authentik_*` helpers. |
| **TAK Portal needs to know about TAK Server certs, Authentik token, FQDN** | "Sync TAK Server to TAK Portal" / "Update Config" buttons regenerate `settings.json` and copy certs into the container. |
| **Certs expire and nobody notices** | Guard Dog monitors cert age, alerts at 25 days, the UI shows green/red cert health cards. |
| **Authentik silently misconfigures itself across releases** | Idempotent post-update migrations + runtime config verifier (`ak dump_config`) — see `.cursor/rules/consult-upstream-docs.mdc` for the cautionary tale that motivated this discipline. |
| **Node-RED flows for ArcGIS / TFR / KML / TC AVL / PulsePoint require expert config** | Configurator UI (`http://<vps>:1880/configurator`) — operator points it at a feature service, picks fields, hits Save; flows generate and deploy automatically. |
| **Production updates clobber operator state** | `nodered/deploy.sh` backs up Node-RED global context (where Configurator configs live) before stopping the container, validates the backup, and restores after. **Never raw-`docker cp` `flows.json`** — it wipes dynamic engine tabs. |
| **Container CVEs (Copy Fail, etc.)** | All compose files are hardened (`cap_drop: ALL`, `no-new-privileges:true`, scoped mounts, removed `/var/run/docker.sock`). Hardening is applied idempotently to every existing install on Update Now. |
| **Operator forgets the password / Authentik dies / Caddy breaks** | Backdoor `https://<vps_ip>:5001` always works. `reset-console-password.sh` and `fix-console-after-pull.sh` are one-line recoveries. README universal recovery block pulls upstream `main`. |
| **Failed login flooding TAK Server** | Authentik Reputation Policy (v0.9.2) decrements source-IP score on each failed login, blocks at threshold. Fail2ban for SSH + MediaMTX RTSP. |
| **Rolling back a bad update is risky** | TAK Server snapshots (CoreConfig + UAF + certs + `pg_dump`) — daily timer, manual button, pre-upgrade automatic. Console rollback button records previous version and reverts in one click. |

## Who uses it

- **Fire / EMS / SAR teams** running TAK for incident command, where the IT person is also the deputy chief and has 30 minutes to deploy a working stack.
- **Public safety integrators** spinning up demo + production TAK servers for clients.
- **Defense / contractor labs** who need an Authentik-backed TAK environment without hiring a TAK SME.
- **NGO field operations** in low-bandwidth environments who need offline-tolerant deploys with Guard Dog auto-recovery.
- **TAK developers** who want a reliable test environment with snapshots and one-click rollback.

The persona is "competent sysadmin, not TAK expert" — knows how to SSH, knows what a container is, but does not want to read CoreConfig.xml docs, generate certificates by hand, or learn Authentik's blueprint format.

## How it should work (UX promises)

1. **One CLI command, then the browser.** `sudo ./start.sh` is the only shell command after `git clone`. Everything else happens at `https://<host>:5001`.
2. **Deploy order is documented and enforced.** The console nudges the operator: Caddy → Authentik → Email Relay → TAK Server → Connect LDAP → TAK Portal → everything else. Each step auto-configures the next.
3. **Buttons mean what they say.** "Update Config" regenerates and applies. "Resync LDAP to TAK Server" fully re-runs the LDAP fix flow. "Connect TAK Server to LDAP" does the full first-time LDAP wiring. "Take Snapshot Now" makes a snapshot. "Rollback" reverts. The README's *Actions Reference* table is the contract.
4. **The console never silently fails.** Long-running operations stream a `plog` line per step into a UI log panel. Migrations record their outcome to `settings.<feature>_<migration>` so operators can see exactly what happened.
5. **Recovery never requires expert knowledge.** Backdoor URL works. Universal recovery block in README works. `reset-console-password.sh` works. None of these depend on Authentik or Caddy being healthy.
6. **Updates are safe.** Pre-update snapshots, idempotent migrations, runtime verifiers, console rollback bar after every update. The default expectation is "Update Now never breaks me."
7. **Two-server / split topology is first-class.** TAK Server can split into Server One (Postgres) + Server Two (TAK Server). Federation Hub can be local or remote SSH. Authentik / CloudTAK / MediaMTX / Node-RED can deploy to remote hosts.

## What success looks like

- A new operator with no prior TAK experience can deploy a fully-functional TAK Server + Authentik + TAK Portal + Node-RED stack on a fresh Ubuntu 22.04 VPS in **under 30 minutes** (most of which is `apt`/Docker pulls and Authentik startup), then enroll an ATAK device via QR code without ever opening a terminal again.
- "Update Now" works on every release, on every existing install, idempotently, without operator intervention.
- When something does go wrong, the operator's first action is "click the obvious button" or "open the backdoor URL" — never "re-deploy from scratch."
