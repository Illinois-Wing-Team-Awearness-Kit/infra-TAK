# Security Incident — PG_MEM / PGMiner cryptominer on CloudTAK PostGIS

**Incident ID:** infra-TAK-2026-05-10-001
**Status:** CONTAINED. Upstream fix pending. infra-TAK mitigation shipped as v0.9.11-alpha.
**Severity:** Critical (RCE → cryptominer → persistence → mass-exploitable upstream default)
**Discovered:** 2026-05-10 by infra-TAK operator while investigating sustained 1000%+ CPU on `responder` after publishing v0.9.5
**Initial infection:** Approximately 2026-05-08 11:55 UTC (file mtime of `gcmanager-1.so` on `responder`)
**Affected:** Every infra-TAK install running CloudTAK on a public-IP host that has not applied v0.9.11 hardening
**Root cause:** Upstream `dfpc-coe/CloudTAK` `docker-compose.yml` ships postgis bound to `0.0.0.0:5433` with hardcoded `POSTGRES_PASSWORD=docker` — NOT infra-TAK customization

---

## Summary

A live cryptominer compromise of CloudTAK's postgis container was identified on infra-TAK dev host `responder` during routine investigation of a CPU spike initially attributed to Authentik task-log bloat (the v0.9.5–v0.9.7 hotfix chain). The compromise was caused by a `docker:docker` superuser brute-force against the publicly-exposed postgis port 5433. The attacker dropped a malicious shared library (`gcmanager-1.so`) into the postgis data volume via `COPY FROM PROGRAM` and modified `postgresql.conf`'s `shared_preload_libraries` for persistence. The library is a known cryptominer (PG_MEM / PGMiner family, see Aqua Nautilus and Palo Alto Unit 42 writeups) that mines Monero with C2 over Tor SOCKS5.

Sister dev host `tak-10` was verified clean of persistence artifacts but identically vulnerable (same upstream defaults).

The root cause is in upstream `dfpc-coe/CloudTAK` and affects every CloudTAK install with a public IP, regardless of whether the operator uses infra-TAK. The maintainer has been notified. infra-TAK v0.9.11-alpha ships downstream mitigation (port lockdown via `!reset` in override, UFW deny rules, strong random `POSTGRES_PASSWORD` on fresh installs, automated compromise detection + quarantine during Update Now).

---

## Timeline

| Date / Time (UTC) | Event |
|---|---|
| 2026-05-08 ~11:55 | `gcmanager-1.so` first dropped on `responder` (file mtime). Initial brute-force window. |
| 2026-05-08 onward | Miner active. ~1000–1100% CPU mining Monero. Network C2 via Tor SOCKS5. Operator attributes spike to Authentik (legitimate v0.9.5 regression also active on the box). |
| 2026-05-10 | Operator publishes infra-TAK v0.9.6, v0.9.7, v0.9.8, v0.9.9 trying to fix what they believe is an Authentik task-log + orphan-process issue. CPU stays pegged because cause is unrelated. |
| 2026-05-10 ~22:00 | Operator notices `cloudtak-postgis-1` is the actual high-CPU container, not Authentik. |
| 2026-05-10 ~22:30 | Process forensics: `comm` field of "postgres" worker is `epggfmfdnr` (random 10-letter string), `argv[0]` claims `postgres: checkpointer`. Zero disk I/O despite high "checkpointer" CPU. → confirmed not legitimate postgres. |
| 2026-05-10 ~22:45 | `docker stop cloudtak-postgis-1` on `responder` + `tak-10`. `ufw deny 5433/tcp`, `ufw deny 9000/tcp`, `ufw deny 9002/tcp` applied on both. Public-facing attack surface eliminated. |
| 2026-05-10 ~23:00 | Forensic analysis of `responder`'s postgis data volume identifies `gcmanager-1.so` + matching `shared_preload_libraries` line in `postgresql.conf`. Identical scan on `tak-10` shows volume is clean — `tak-10` was vulnerable but never compromised. |
| 2026-05-10 ~23:15 | Sample preserved to `/tmp/responder-gcmanager-1.so.evidence` on `responder`. Forensic fingerprinting: file type, hashes, exported symbols, strings. Identified as PG_MEM/PGMiner family. |
| 2026-05-10 ~23:45 | v0.9.11-alpha development begins: override + UFW + password generation + compromise detection + quarantine. |
| 2026-05-11 | v0.9.11-alpha shipped. Upstream maintainer notified with IOCs + suggested fixes. |

---

## Attack chain

1. **Reconnaissance.** Internet-wide scanner probes TCP/5433 across IPv4. Common targeted port for misconfigured / default Postgres installs.
2. **Initial access.** Connect to `0.0.0.0:5433`, attempt `docker:docker`. Success (upstream default). Attacker now has superuser session on the `gis` database.
3. **Filesystem write.** Use Postgres' `COPY FROM PROGRAM` SQL feature to execute shell commands as the postgres process user. Drops `gcmanager-1.so` into the data directory (`/var/lib/postgresql/data/gcmanager-1.so` inside the container, which is the named volume mount on the host). Alternative paths used by this malware family per Unit 42: `pg_write_server_files` for direct binary writes, or large-object exports.
4. **Persistence.** Append to `postgresql.conf`:
   ```
   shared_preload_libraries = '/var/lib/postgresql/data/gcmanager-1.so'
   ```
   Persistence survives container restarts because both the `.so` and `postgresql.conf` live in the named volume, not the container image.
5. **Activation.** Restart postgres (or call `pg_reload_conf()` — actually `shared_preload_libraries` requires a real restart). The next postmaster spawn loads `gcmanager-1.so` alongside the database. Library spawns mining threads that masquerade as postgres workers (random `comm` field).
6. **Operation.** Monero mining at maximum CPU. C2 via SOCKS5 over Tor (`.onion` addresses per Unit 42). Periodic check-in for module updates; attacker can push new payloads.

---

## Malware identification

**Family:** PG_MEM / PGMiner (also: variants of SystemdMiner).

References:
- Aqua Nautilus, "PG_MEM: A Malware Hidden in the Postgres Processes": https://www.aquasec.com/blog/pg_mem-a-malware-hidden-in-the-postgres-processes/
- Palo Alto Unit 42, "PGMiner: New Cryptocurrency Mining Botnet Delivered via PostgreSQL": https://unit42.paloaltonetworks.com/pgminer-postgresql-cryptocurrency-mining-botnet/
- Hybrid Analysis sample of the same filename (different hash, same campaign): https://hybrid-analysis.com/sample/97109072c04bd4a4806bb7172a2a3128dfe10bf83fccea31217d5a9ad0b1b503

The hash of the sample on `responder` is different from the hybrid-analysis sample — the attacker recompiles `gcmanager-1.so` regularly to defeat hash-based AV. **Hash IOCs are unreliable for detection across deployments. The persistence technique (`.so` in data dir + `shared_preload_libraries` in `postgresql.conf`) is the reliable IOC.**

---

## Indicators of Compromise (IOCs)

### Sample observed on responder

| Field | Value |
|---|---|
| Filename | `gcmanager-1.so` |
| Size | 565,920 bytes |
| SHA256 | `715348a40250549100cbbeb2a8d68ffa323e671b55fc46e8df24c7016b11e10a` |
| MD5 | `914e0451b50c0c95c3a89fd5da419cb4` |
| File type | ELF 64-bit LSB shared object, x86-64, version 1 (SYSV), static-pie linked, stripped |
| libc | Statically-linked musl libc (`__block_all_sigs`, `__copy_tls`, `__clone`, `sem_open` symbols) — compiled for Alpine specifically |
| String tells | `cuRl` (mixed case — anti-string-scan technique), `cstr_*` (C string helper lib), `sem_open`, `kill`, `getenv` |
| Drop path | `/var/lib/postgresql/data/gcmanager-1.so` (inside postgis container, which maps to the named volume) |

### Generic IOCs (apply to all variants)

**Filesystem (postgis data volume, accessible on host via `docker inspect`):**
- Any `*.so` file in `/var/lib/postgresql/data/` — Postgres core never puts shared libs there
- `postgresql.conf` containing an uncommented `shared_preload_libraries = '...'` line pointing to a `.so` in the data directory

**Inside the running container:**
- Hidden files in `/var/tmp/` matching pattern `^\.[a-z]{10}$` (e.g. `.eygkhpofrf`, `.jomccgmkjg`)

**Process-level (host):**
- UID-70 postgres worker whose `comm` field is not `postgres` (e.g. a random 10-letter lowercase string) while `argv[0]` claims a legitimate postgres role (`postgres: checkpointer`, `postgres: background writer`, etc.)
- Sustained CPU near 100% × number_of_cores on `cloudtak-postgis-1` container with near-zero disk I/O (`docker stats`)

**Network:**
- Outbound connections to `.onion` addresses (Tor SOCKS5 C2 — per Unit 42 PGMiner writeup, attackers refresh the C2 list frequently so listing specific addresses is not useful)
- Egress to known Tor entry/guard nodes (covered by general Tor blocklists)

**Logs (postgis container):**
- Successful authentication attempts for `docker`, `postgres`, `tester` from unknown WAN IPs in postgis container logs prior to first compromise

---

## Host-by-host status

### `responder` (190.102.110.224)

- **Compromise confirmed.** `gcmanager-1.so` present in data volume. `postgresql.conf` had `shared_preload_libraries = '/var/lib/postgresql/data/gcmanager-1.so'`.
- **Containment:** `cloudtak-postgis-1` stopped. UFW deny rules applied for 5433/9000/9002. Sample preserved to `/tmp/responder-gcmanager-1.so.evidence`.
- **Forensic state:** All `.so` files quarantined to be moved into `<data>/quarantine-YYYYMMDD-HHMMSS/` once v0.9.11 update runs. `postgresql.conf` malicious line to be commented out.
- **Remediation:** Operator will run Update Now (v0.9.11) → automatic quarantine → then Remove + Reinstall to wipe potentially-tainted DB contents.

### `tak-10`

- **Not compromised.** No `.so` files in data volume. `postgresql.conf` clean.
- **Was vulnerable:** Same upstream defaults; only unaffected because attacker hadn't found the box yet.
- **Containment:** `cloudtak-postgis-1` stopped, UFW deny rules applied for 5433/9000/9002 (defensive).
- **Remediation:** Operator will run Update Now (v0.9.11) → clean install path applies override + UFW → optionally Remove + Reinstall to rotate to a strong password (recommended).

---

## Mitigation summary (v0.9.11)

| Layer | Mechanism | Why |
|---|---|---|
| Compose override | `postgis ports: !reset []` | Removes upstream `5433:5432` host mapping entirely. CloudTAK uses internal Docker network; host port was never needed. |
| Compose override | `store ports: !reset` → `127.0.0.1:9002:9002` | Drops public MinIO S3 API (9000); keeps console (9002) on loopback only for SSH-tunneled operator access. |
| Compose override | `postgis environment: POSTGRES_PASSWORD: "${POSTGRES_PASSWORD:-docker}"` | Fresh installs init with strong random password from `.env`; existing installs ignored harmlessly. |
| `.env` | `POSTGRES_PASSWORD={secrets.token_hex(24)}` | 48-char hex password, baked into `pg_authid` on first `initdb`. Connection string in same file uses same value. |
| `.env` | `~/CloudTAK/.postgres-password` (chmod 600) | Operator reference if they ever need the password. |
| UFW | `deny 5433/tcp`, `deny 9000/tcp`, `deny 9002/tcp` | Defense in depth — survives compose override regressions. |
| Detection | Scan postgis data volume for `*.so` files + non-empty `shared_preload_libraries` on every Update Now | Automated compromise check; can't be missed |
| Quarantine | Move `.so` files to dated subdir, comment out config line, stop containers, write `COMPROMISE-DETECTED.txt` | Stops the bleeding without destroying evidence; clear operator instruction |
| Operator action | "Remove + Reinstall CloudTAK" guidance in release notes + on-disk banner | Only way to fully clean attacker DB artifacts (potentially-created roles, pg_cron jobs, event triggers) is to wipe and restart |

---

## Upstream coordination

Reported to `dfpc-coe/CloudTAK` maintainer (Nick) on 2026-05-10 via direct Slack. Writeup included:
- Root cause analysis
- Live attack timeline
- IOCs (hashes, sample size, file type, persistence technique)
- Suggested upstream fixes: bind to `127.0.0.1:5433` or remove port mapping; generate `POSTGRES_PASSWORD` on install (similar to how `SigningSecret` is generated); document the risk in install docs

Awaiting upstream response. infra-TAK's downstream mitigation does not depend on upstream timing — operators are protected via v0.9.11.

---

## Lessons for infra-TAK going forward

1. **Audit all third-party compose files we deploy.** Any `0.0.0.0:` port binding or hardcoded credential in upstream compose files needs a downstream override. Future modules added to infra-TAK need this check before merge.
2. **Default-deny network posture for all data services.** Postgres, Redis, MinIO, etc., should never bind to `0.0.0.0` in infra-TAK deployments. The override file pattern used in v0.9.11 (`ports: !reset`) is the model for future module hardening.
3. **`shm_size` / `shared_preload_libraries` are signals.** Detecting `shared_preload_libraries` set to anything in any user-data-managed Postgres `.conf` is a generally useful malware signal beyond just CloudTAK. Consider extending the scan to Authentik's postgres and TAK Server's postgres in a future release as defense in depth.
4. **Symptom-level investigations need to checkpoint the assumptions.** The v0.9.5–v0.9.7 hotfix chain was chasing high CPU and ended up not addressing the actual cause (cryptominer) because the symptoms (high postgres CPU) matched a different (real) bug (Authentik task-log bloat). Adding a quick "what's actually running and what's its `comm` field" check early in any high-CPU investigation would have caught this faster. Adding to `.cursor/rules/` is being considered.

---

## Evidence preservation

The malware sample is preserved at:
- `/tmp/responder-gcmanager-1.so.evidence` on `responder` (survives until reboot — operator should `scp` to a permanent location)
- Hashes recorded above in this document

If forensic analysis is desired beyond what's documented here (e.g. reverse engineering to identify wallet addresses, C2 servers, or campaign attribution), the sample can be shared with researchers via private channel.
