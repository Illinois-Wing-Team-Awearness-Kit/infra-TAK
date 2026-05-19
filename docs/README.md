# docs/ — infra-TAK documentation index

Quick reference for what's in this folder. Internal docs (HANDOFF, session notes) stay on `dev` only. User-facing docs go to `main`.

---

## Architecture & design

| Doc | What it covers |
|-----|----------------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Full system map — how all components relate |
| [EXTERNAL-DEPS.md](EXTERNAL-DEPS.md) | External repos we depend on + how we use them |

---

## Operator runbooks

| Doc | What it covers |
|-----|----------------|
| [COMMANDS.md](COMMANDS.md) | Git workflow, deploy, merge dev→main, tagging |
| [PULL-AND-RESTART.md](PULL-AND-RESTART.md) | Update from dev or main on a VPS; then **`bash nodered/deploy.sh`** for Node-RED |
| [DISK-AND-LOGS.md](DISK-AND-LOGS.md) | Log rotation, disk cleanup |
| [GUARDDOG.md](GUARDDOG.md) | Guard Dog monitor reference |
| [WORKFLOW-8446-WEBADMIN.md](WORKFLOW-8446-WEBADMIN.md) | webadmin flat-file vs LDAP, 8446 login |
| [FED-HUB.md](FED-HUB.md) | Federation Hub setup reference |
| [FEDHUB-LOGIN-RUNBOOK.md](FEDHUB-LOGIN-RUNBOOK.md) | FedHub login troubleshooting |
| [AUTHENTIK-LOGIN-BRANDING.md](AUTHENTIK-LOGIN-BRANDING.md) | Authentik UI customization |
| [MEDIAMTX-TAKPORTAL-ACCESS.md](MEDIAMTX-TAKPORTAL-ACCESS.md) | MediaMTX + TAK Portal access patterns |
| [REFERENCES.md](REFERENCES.md) | External links and reference material |

---

## Integration deep-dives (dev branch)

| Doc | What it covers |
|-----|----------------|
| [GIS-TAK-DATASYNC-HANDOFF.md](GIS-TAK-DATASYNC-HANDOFF.md) | ArcGIS → Node-RED → DataSync full architecture, roles, mission design |
| [HANDOFF-LDAP-AUTHENTIK.md](HANDOFF-LDAP-AUTHENTIK.md) | Authentik LDAP outpost + TAK Server auth paths |
| [TAK-SERVER-LDAP-BEHAVIOR.md](TAK-SERVER-LDAP-BEHAVIOR.md) | Observed TAK Server LDAP chattiness and cache behavior |

---

## Session handoffs (dev branch — internal)

| Doc | Session |
|-----|---------|
| [HANDOFF-v0.6.7-session.md](HANDOFF-v0.6.7-session.md) | DataSync read-only missions, multi-flow, FedHub sudo, Postfix |
| [HANDOFF-v0.6.5-session.md](HANDOFF-v0.6.5-session.md) | Stable ID pills, strict reconcile, Purge |
| [HANDOFF-v0.6.3-session.md](HANDOFF-v0.6.3-session.md) | Node-RED Configurator v1 |
| [HANDOFF-v0.6.1-session.md](HANDOFF-v0.6.1-session.md) | Earlier session |

---

## Release notes

Latest: [RELEASE-v0.9.33-alpha.md](RELEASE-v0.9.33-alpha.md)

Older releases are on the [GitHub Releases tab](https://github.com/takwerx/infra-TAK/releases).

---

## Testing (maintainers)

| Doc | What it covers |
|-----|----------------|
| [TESTING-UPDATES.md](TESTING-UPDATES.md) | Pre-release smoke test protocol |
| [TESTING-NODERED-DEPLOYS.md](TESTING-NODERED-DEPLOYS.md) | Node-RED deploy verification |
| [NODERED-DEPLOY.md](NODERED-DEPLOY.md) | Node-RED deploy cheat sheet |
