# Port Exposure Policy

> **Status:** authoritative as of v0.9.12-alpha (2026-05-11). New services or upstream merges must classify every host port against this policy before merge to `main`.

The v0.9.11 CloudTAK PG_MEM incident was rooted in a single upstream port mapping (`5433:5432` published to `0.0.0.0` with default `docker:docker` credentials). Post-incident audit found similar Tier 1 / 0.0.0.0 / weak-auth combinations across multiple services we deploy. This policy is the canonical reference for **which ports are allowed to be public, which must be bound to loopback, and which must never have a host port at all.**

It is enforced by:

- Compose overrides using `ports: !reset` (Compose v2.24+ syntax) to wipe upstream port lists before re-declaring.
- `_auto_harden_*` post-update steps that re-apply the overrides on every Update Now (so upstream `git pull` regressions are auto-corrected).
- UFW `deny` rules on Tier 3/4 ports as belt-and-braces if the override is regressed or removed.
- New `_validate_ssh_target` / `_validate_snapshot_label` / Postgres identifier regex helpers for input boundaries that aren't ports but use the same trust model.

---

## Tier definitions

### Tier 1 — Public

Reachable from the open internet. UFW: `allow {port}`.

Required for any service whose protocol is consumed by external clients (TAK clients, browsers, video streaming endpoints, etc.). Authentication MUST be enforced at the application layer.

Examples: TAK Server 8089 (TLS, client-cert), Caddy 443 (TLS terminator + auth gate), MediaMTX RTSP 8554 (basic-auth + token), CloudTAK Media RTSP 18554.

### Tier 3 — Caddy-loopback

Bound to `127.0.0.1:{port}` inside the Docker port mapping (or by the service's own listen-address config). UFW: `deny {port}/tcp` belt-and-braces.

Reached only via Caddy on 443 from the public FQDN. Caddy enforces the auth boundary (Let's Encrypt cert + Authentik forward_auth where applicable) before proxying to loopback.

Examples: Authentik 9000/9443, TAK Portal 3000, Node-RED 1880, MediaMTX HLS 8888, MediaMTX webedit 5080, CloudTAK api/tiles 5000/5002, CloudTAK media-admin 9997.

**Critical:** Tier 3 services often ship from upstream with `0.0.0.0` bindings. Our compose overrides MUST use `ports: !reset` to entirely replace the upstream port list, NOT additive `ports:` (which leaves the upstream 0.0.0.0 mapping in place). See `_cloudtak_build_override_yml()` and `_write_takportal_override()` for the pattern.

### Tier 4 — Docker-internal

No host port at all (`ports: !reset []` in the override; Compose still wires up the container to the Docker network).

Reached only by sibling containers via Docker DNS (`postgresql:5432`, `redis:6379`, etc.).

Examples: Authentik PostgreSQL/Redis, CloudTAK PostGIS (post-v0.9.11), CloudTAK MinIO S3 9000, CloudTAK events worker 5003.

**This is the strongest tier.** A Tier 4 service has no externally-reachable surface — neither from the public internet nor from the host's other processes. Any service that's only consumed by another container in the same compose project should be Tier 4.

### Tier 5 — Source-scoped

Listening on `0.0.0.0` but UFW allows traffic only from a specific peer IP, with `deny {port}/tcp` as the catch-all.

Used for services that legitimately need to be reached by a single trusted peer over the public network, where a tunnel or Docker network can't satisfy the requirement.

Examples: Server One PostgreSQL 5432 (reachable from Server Two only), Server One Guard Dog health agent 8080 (reachable from console only), remote Authentik LDAP outpost 389/636 (reachable from console only — TAK Server's LDAP auth block calls `ldap://{remote_host}:389`).

**Critical:** Source-scoped rules must come BEFORE the `deny` rule and must NEVER be paired with an unconditional `allow {port}/tcp` (which would silently override the scope). The Server One two-server install had this bug pre-v0.9.12 — `ufw allow from {Server Two} to any port {db_port}` was immediately followed by `ufw allow {db_port}/tcp`, defeating the entire purpose.

---

## Service inventory (v0.9.12-alpha)

| Service | Port | Tier | Why |
|---------|------|------|-----|
| infra-TAK Console | 5001 | Tier 1 | Operator web UI (backdoor direct-IP access) |
| Caddy | 80 / 443 | Tier 1 | TLS terminator |
| TAK Server | 8089 | Tier 1 | TAK client TLS |
| TAK Server | 8443 / 8446 | Tier 1 | Admin WebGUI |
| MediaMTX | 8554 (RTSP), 8322 (RTSPS), 8890 (SRT), 8000/8001 (RTP/RTCP) | Tier 1 | Streaming clients |
| CloudTAK media | 18554 (RTSP), 11935 (RTMP), 18890 (SRT) | Tier 1 | Streaming clients (CloudTAK video tab) |
| Authentik | 9000 / 9443 | Tier 3 | Caddy proxies (was Tier 1 on remote installs pre-v0.9.12) |
| TAK Portal | 3000 | Tier 3 | Caddy proxies + forward_auth (was Tier 1 pre-v0.9.12) |
| Node-RED | 1880 | Tier 3 | Caddy proxies + forward_auth |
| MediaMTX | 8888 (HLS), 5080 (webedit), 9898 (admin API) | Tier 3 | Caddy proxies (was Tier 1 pre-v0.9.12) |
| CloudTAK | 5000 (api), 5002 (tiles), 18888 (media HLS), 9997 (media admin), 9002 (MinIO console) | Tier 3 | Caddy proxies (api/tiles were Tier 1 pre-v0.9.12; 9002 was Tier 1 pre-v0.9.11) |
| Authentik PostgreSQL / Redis | 5432 / 6379 | Tier 4 | Docker-internal |
| CloudTAK PostGIS | 5432 | Tier 4 | Docker-internal (was Tier 1 + default creds pre-v0.9.11 — root cause of PG_MEM incident) |
| CloudTAK MinIO S3 | 9000 | Tier 4 | Docker-internal (was Tier 1 pre-v0.9.11) |
| CloudTAK events worker | 5003 | Tier 4 | Docker-internal |
| Email Relay | 25 | Tier 4 (localhost-bound, native systemd) | Local Postfix |
| Server One PostgreSQL | 5432 (default) | Tier 5 | Source: Server Two IP |
| Server One Guard Dog health | 8080 | Tier 5 | Source: console IP (`settings.server_ip`) |
| Remote Authentik LDAP outpost | 389 / 636 | Tier 5 | Source: console IP (`settings.server_ip`) |

---

## Adding a new service or port

Before merging, every new host port mapping MUST:

1. Be classified explicitly in the PR description against this policy.
2. Use `ports: !reset` (Compose) or bind to `127.0.0.1` (systemd / native) for Tier 3.
3. Use `ports: !reset []` (Compose) for Tier 4 — no host port at all.
4. Apply UFW `deny {port}/tcp` for Tier 3/4 in the corresponding `_auto_harden_*` post-update step.
5. Add an entry to the Service inventory table above and to the README ports section.

If a port is Tier 1 (public), justify why in the PR — the default is "make it Tier 3 unless you have a real external consumer." Most admin UIs and worker queues belong on Tier 3.

If a port is Tier 5 (source-scoped), use `_fedhub_caddy_source_ip(settings)` (or the equivalent for the consumer) to fetch the source IP, source-scope FIRST, deny SECOND, NEVER `allow` unconditionally afterward.

---

## Validation checklist for security-sensitive PRs

- [ ] Any new ports declared? → classified in table above + Service inventory updated.
- [ ] Any new `subprocess.run(..., shell=True)` with f-string interpolation? → switch to argv, validate inputs.
- [ ] Any new SQL with f-string identifiers/values? → use `psql -v` for values, regex-validate identifiers against `^[A-Za-z_][A-Za-z0-9_]{0,62}$`.
- [ ] Any new SSH/SCP call? → it must go through `_ssh_probe`/`_scp_to_host` (both call `_validate_ssh_target` automatically).
- [ ] Any new file path built from user input? → use a label validator like `_validate_snapshot_label` (regex + `os.path.realpath` containment check).
- [ ] Any new hardcoded fallback secret? → use `secrets.token_urlsafe` + persist to disk on first run instead.
