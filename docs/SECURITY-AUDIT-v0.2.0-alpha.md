# infra-TAK Security Audit (v0.2.0-alpha)

Date: 2026-03-12  
Scope: `app.py` web/API security posture and deployment hardening priorities for government use

---

## Executive Summary

infra-TAK is operationally strong but has several web-app security gaps that should be addressed before high-assurance/government deployment.  
Top risks are:

1. Trusting SSO headers without strict source validation
2. Command injection surfaces in shell command construction
3. Missing CSRF controls on state-changing APIs
4. Plaintext secret storage in settings

This document prioritizes fixes by risk and implementation effort.

---

## Findings (Prioritized)

### Critical

- **Header-based auth trust boundary**
  - Risk: If SSO headers are accepted from untrusted origins, login bypass is possible.
  - Current status: **Partially mitigated now** by trusting Authentik headers only when `request.remote_addr` is loopback (`127.0.0.1` / `::1`).
  - Remaining hardening: add explicit shared-secret proxy header validation and/or bind app to localhost behind Caddy only.

### High

- **Command injection risk in command strings**
  - Risk: User-influenced data interpolated into `shell=True` commands can become RCE.
  - Current status: **Partially mitigated now** for CloudTAK logs:
    - strict container-name regex allowlist
    - local `docker logs` switched to argv-style subprocess (no shell interpolation for container argument)
  - Remaining hardening: migrate more shell string calls to argv form where feasible.

- **Upload path traversal risk**
  - Risk: raw upload filename could write outside intended path.
  - Current status: **Mitigated now** using `secure_filename()` for TAK Server upload filenames.
  - Remaining hardening: add explicit extension allowlist + content-type sniffing where needed.

- **No CSRF protection on authenticated POST APIs**
  - Risk: Cross-site request forgery can trigger admin actions if session cookie is present.
  - Current status: Not mitigated.
  - Recommendation: CSRF token + Origin/Referer checks for all state-changing routes.

### Medium

- **Plaintext secrets in `.config/settings.json`**
  - Includes SSH password mode and third-party API keys (SMS providers).
  - Recommendation: migrate to environment/secret store and encrypt-at-rest for local persistence.

- **No brute-force/rate limit on login endpoints**
  - Recommendation: add `Flask-Limiter` to `/login`, `/`, and sensitive APIs.

- **Weak SSH trust mode for high assurance**
  - Current uses include `StrictHostKeyChecking=accept-new` and optional password mode.
  - Recommendation: key-only mode for production and host key pinning.

- **Missing explicit browser security headers**
  - Recommendation: add `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, and HSTS in HTTPS/FQDN mode.

---

## Immediate Changes Applied (This Session)

1. **Auth header trust gated to local proxy source**
   - `_apply_authentik_session()` now ignores `X-Authentik-Username` unless request source is loopback.

2. **CloudTAK logs endpoint hardened**
   - `container` query param validated (`^[a-zA-Z0-9][a-zA-Z0-9_.-]*$`)
   - `lines` clamped to 1..500
   - Local `docker logs` command uses argv subprocess call, not shell interpolation.

3. **TAK package upload filename hardening**
   - Added `werkzeug.utils.secure_filename()`
   - Rejects invalid filenames before writing to disk.

4. **CSRF baseline on state-changing APIs**
   - Added same-origin validation for `POST/PUT/PATCH/DELETE` under `/api/*` (Origin/Referer host must match request host).
   - Localhost-only Guard Dog script endpoint (`/api/guarddog/send-sms`) is exempt.

5. **Built-in rate limiting (no new dependency)**
   - Login POSTs (`/` and `/login`): 12 attempts / 5 minutes per client IP.
   - State-changing API calls (`/api/*`): 240 write requests / minute per client IP.

6. **Response security headers baseline**
   - Added:
     - `X-Content-Type-Options: nosniff`
     - `X-Frame-Options: DENY`
     - `Referrer-Policy: strict-origin-when-cross-origin`
     - `Permissions-Policy: geolocation=(), microphone=(), camera=()`
     - `Content-Security-Policy` (compatibility mode for existing inline-heavy templates)
     - `Strict-Transport-Security` on HTTPS requests

---

## Recommended Hardening Roadmap

### Phase 1 (next sprint)

- Implement CSRF protections on all state-changing routes.
- Add login and API rate limiting.
- Add baseline security headers via `@app.after_request`.
- Review/convert high-risk `shell=True` calls that include dynamic inputs.

### Phase 2

- Secrets management redesign (remove plaintext credentials from settings file where possible).
- SSH mode hardening profile for regulated deployments (key-only + pinned host keys).
- Add audit logging for privileged actions.

### Phase 3

- Optional RBAC model (beyond single console password).
- CI security checks (SAST, dependency scanning, secret scanning).
- Deployment hardening profile docs (gov baseline checklist).

---

## Government Deployment Guardrails (Minimum)

- Place infra-TAK behind VPN or private management network.
- Restrict public access to management/backdoor port.
- Enforce least-privilege SSH and key-only authentication.
- Rotate and store credentials in enterprise secrets management.
- Enable centralized logging and immutable audit retention.

---

## Node-RED Cert Identity (added 2026-05-05)

### Risk

Node-RED holds a TAK Server client cert for the Mission API and CoT streaming. By default this is `admin.pem` — TAK Server's bootstrap admin identity, with `ROLE_ADMIN` and full ownership authority over every mission. A compromised Node-RED runtime (malicious npm contrib, supply-chain attack on the editor, exfiltration via flow code) inherits **full TAK admin authority**, including:

- Ownership of every DataSync mission (read/write/delete)
- Ability to create new missions
- Ability to modify TAK Server configuration via Mission API endpoints that admin can access
- Ability to act as any user via subscribe + role-elevation

### Why admin in the first place

We tested LDAP-based scoped certs (`nodered-global-datasyncfeed` cert in April 2026) and they were blocked by an unfixed TAK Server bug: x509 certs that resolve groups via LDAP get **OUT-only** group direction even when the LDAP base group says BOTH. Without IN direction on the base group, the mission write API rejects the request. See `docs/GIS-TAK-DATASYNC-HANDOFF.md` 2026-04-12 entry for the exact failure modes.

TAK Portal — an official TAK ecosystem component — uses the same `admin.p12` (same identity as `admin.pem`) for its own integration with TAK Server. So Node-RED following this pattern is consistent with upstream design, not an outlier.

### Mitigation in progress (Phase 1A — see `docs/SPIKE-flatfile-nodered.md`)

The flat-file authentication path declares group membership directly in `UserAuthenticationFile.xml` (the same place admin is defined), bypassing the LDAP resolution that triggers the bug. If the validation spike (T1 + T3 in the SPIKE doc) confirms flat-file users get correct group direction, Node-RED can be migrated to a least-privilege `nodered` cert with:

- `ROLE_USER` (not ROLE_ADMIN)
- Group membership only in the DataSync feeds group
- Mission ownership only on missions it creates itself (via `creatorUid=nodered` on `PUT /Marti/api/missions/{name}`)

The codebase (as of 2026-05-05) is wired defensively: `nodered/deploy.sh` and the inline elevation hack in `nodered/build-flows.js` prefer `/certs/nodered.pem` if it exists, falling back to `/certs/admin.pem`. This means an operator can complete the spike, run `scripts/bootstrap-nodered-flatfile.sh apply`, and the Node-RED runtime automatically picks up the new least-privilege identity on the next deploy.

### Mitigation regardless of Phase 1A outcome (Phase 1B + 2)

These reduce blast radius even if the cert identity stays as admin:

- **Egress allowlist** (`docs/NODERED-EGRESS.md` + `scripts/nodered-egress-firewall.sh`): restricts the Node-RED container's outbound network to TAK Server, DNS, NTP, and an explicit list of external hostnames. Closes the cert-exfil scenario — even fully-compromised Node-RED cannot ship a stolen cert to an attacker host.
- **Container hardening** (Phase 2, applied automatically on next deploy): pinned image (no `:latest`), `cap_drop: [ALL]`, `no-new-privileges:true`, `user: 1000:1000`, `mem_limit: 2g`, port bound to `127.0.0.1:1880`.
- **Scoped cert mounts**: per-file binds (e.g. `/opt/tak/certs/files/admin.pem:/certs/admin.pem:ro`) instead of mounting the whole `/opt/tak/certs/files/` tree. Container only sees certs it actually uses.
- **Optional adminAuth** (`~/node-red/.env`): defense-in-depth on top of Caddy + Authentik. Useful against host-shell or SSH port-forward bypass scenarios.
- **CoT-level flow attribution** (`<__nodered flow="..."/>` inside `<detail>`): centralized log correlation can tie each CoT to a specific Configurator feed even when multiple feeds share a cert identity.

### Centralized logging recommendation

For correlation:

- Ship `docker logs nodered` to the same store as TAK Server's `/opt/tak/logs/` (Loki, ELK, Datadog, etc.).
- Index on the `<__nodered flow="...">` attribute when parsing CoT XML.
- Cross-reference TAK Server's mission API logs with Node-RED's HTTP request logs by `creatorUid` and timestamp.

This gives audit traceability per-feed even when multiple feeds use the same cert identity (the pre-Phase-1A reality).

### Status

| Control | Status as of 2026-05-05 |
|---|---|
| Compose hardening flags | Shipped (Phase 2) |
| Image pin (`nodered/node-red:4.0`) | Shipped |
| Port binding `127.0.0.1:1880` | Shipped (was 0.0.0.0:1880 on remote deploys) |
| Scoped cert mounts | Shipped (local deploy auto-detects; remote falls back to whole-tree) |
| Optional adminAuth via env | Shipped |
| CoT flow attribution `<__nodered>` | Shipped |
| Egress allowlist | Documented + opt-in script shipped (operators apply per-deploy) |
| Flat-file `nodered` user (Phase 1A) | Wiring shipped, gated on operator running the spike |
| Cert/passphrase rotation runbook | Out of scope per operator decision (May 2026) |

