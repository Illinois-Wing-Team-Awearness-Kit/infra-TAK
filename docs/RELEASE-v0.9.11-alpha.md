# Release Notes — v0.9.11-alpha

## ⚠ SECURITY HOTFIX — read this before updating

This release patches a critical vulnerability in **upstream CloudTAK** (`dfpc-coe/CloudTAK`) that has been **actively exploited in the wild** as of May 2026. **Every infra-TAK install running CloudTAK is at risk.** Update Now applies network-level mitigation automatically; full remediation requires a one-click **Remove + Reinstall** of CloudTAK after the update completes.

If you have **never enabled CloudTAK** on your infra-TAK install, you are not affected. Update Now is still safe to run — `_auto_harden_cloudtak()` no-ops if `~/CloudTAK` is not present.

---

## What was wrong

Upstream `dfpc-coe/CloudTAK`'s `docker-compose.yml` ships two security-relevant defaults that combine into remote code execution on any public-IP host:

| Upstream default | What it means |
|---|---|
| `postgis` service: `ports: ["5433:5432"]` | postgres listening on `0.0.0.0:5433` — reachable from the internet |
| `postgis` service: `POSTGRES_PASSWORD=docker` (literal) | superuser password is the well-known string `docker` |
| `store` service: `ports: ["9000:9000", "9002:9002"]` | MinIO S3 API + console on `0.0.0.0` |

Out of the box, any CloudTAK install on a VPS with a public IP is an open PostgreSQL with `docker:docker` superuser credentials. Internet-wide scanners hit port 5433 within hours.

The attack chain we observed on infra-TAK `responder` May 8 – 10 2026:

1. Scanner brute-forces the port → `docker:docker` succeeds (logged login attempts for `docker`, `postgres`, `tester` accounts)
2. Attacker uses Postgres' `COPY FROM PROGRAM` (legit feature) to drop `gcmanager-1.so` into `/var/lib/postgresql/data/`
3. Attacker appends `shared_preload_libraries = '/var/lib/postgresql/data/gcmanager-1.so'` to `postgresql.conf`
4. On next postgres restart (or via `pg_reload_conf()`), Postgres loads the malicious shared library at startup
5. Library is **PG_MEM / PGMiner** family (Aqua Nautilus, Palo Alto Unit 42) — a Monero cryptominer that disguises itself as a postgres process (random 10-letter `comm` field like `epggfmfdnr` while `argv[0]` still claims `postgres: checkpointer`)
6. CPU pegged at ~1100%. C2 over Tor SOCKS5. Persistence survives container restarts because the `.so` and modified `postgresql.conf` live in the named volume, not the image.

Full IOC list and forensic timeline: **[docs/SECURITY-INCIDENT-2026-05-10-PGMINER.md](SECURITY-INCIDENT-2026-05-10-PGMINER.md)**.

---

## What v0.9.11 ships

### 1. Hardened compose override — `_cloudtak_build_override_yml()`

```yaml
services:
  postgis:
    ports: !reset []                              # remove upstream 5433:5432 entirely
    environment:
      POSTGRES_PASSWORD: "${POSTGRES_PASSWORD:-docker}"  # substituted from .env
  store:
    ports: !reset
      - "127.0.0.1:9002:9002"                     # MinIO console only, loopback only
                                                  # (drops public 9000 S3 API)
```

- Postgis host port is **gone entirely.** CloudTAK app containers reach postgis over the internal Docker network (`postgis:5432`); the host port mapping was never required, only existed for upstream-developer convenience. Operators who want `psql` access: `docker exec -it cloudtak-postgis-1 psql -U docker gis`.
- MinIO **S3 API (9000)** removed — only used by internal containers.
- MinIO **console (9002)** still available on `127.0.0.1` for SSH-tunneled access: `ssh -L 9002:localhost:9002 root@your-vps` then open `http://localhost:9002` in your laptop browser.
- `POSTGRES_PASSWORD` is now substituted from `.env`. On a fresh data volume Postgres reads this on `initdb` and bakes it into `pg_authid`. On existing volumes the env var is ignored (Postgres only honors it on first init) so update-only flows don't break running installs.

### 2. Strong password generation — `_cloudtak_build_env_content()`

New `postgres_pass` parameter. Fresh-install call sites (local + remote) generate `secrets.token_hex(24)` (48 hex chars). Saved to `~/CloudTAK/.postgres-password` (`chmod 600`) for operator reference.

Reconfig call sites preserve the existing value:
- **Local reconfig**: reads `POSTGRES_PASSWORD=` from existing `~/CloudTAK/.env`
- **Remote reconfig**: SSH-greps the value from the remote `.env` before writing the new config
- If no value is found (pre-v0.9.11 install): falls back to `docker` so the existing volume's baked password keeps working

### 3. Compromise detection + quarantine — `_auto_harden_cloudtak()`

Runs every Update Now after CloudTAK and Authentik settle. Three phases:

**Phase 1 — Scan for compromise indicators in the postgis data volume:**
- Any `*.so` file in the data root (Postgres core never puts shared libs there)
- Uncommented `shared_preload_libraries = '...'` line in `postgresql.conf` pointing to a `.so`

**Phase 2 — If compromised:**
- Stop all CloudTAK containers
- Move every `.so` to `<data_path>/quarantine-YYYYMMDD-HHMMSS/` (preserves forensics, **does not delete**)
- Comment out the malicious `shared_preload_libraries` line with `#INFRATAK_DISABLED# ` prefix
- Write `~/CloudTAK/COMPROMISE-DETECTED.txt` with a full forensic banner
- Print the same banner to the update log (visible in browser + journal)
- **Leave CloudTAK STOPPED** — operator must Remove + Reinstall to fully clean the DB

**Phase 3 — Always (idempotent):**
- Write/refresh `docker-compose.override.yml`
- Apply UFW deny rules: `5433/tcp`, `9000/tcp`, `9002/tcp` (defense in depth — survives override regressions)
- If clean: `docker compose up -d --force-recreate` to apply the new port bindings
- If compromised: skip recreate (containers stay stopped)

---

## Operator options — pick the one that fits

### Option A — Recommended: Update Now + Remove + Reinstall

**Best for: 95% of operators. Especially everyone running CloudTAK in any production-adjacent capacity.**

1. Console → **Update Now** (applies all v0.9.11 hardening: override, UFW, password generation logic, compromise scan)
2. If `_auto_harden_cloudtak()` detects compromise, your CloudTAK will be stopped automatically. Either way, continue:
3. Console → **CloudTAK** module → **Remove** (`docker compose down -v` — wipes data volume including any attacker artifacts)
4. Console → **CloudTAK** module → **Install** (fresh `git clone`, fresh data volume, **strong random POSTGRES_PASSWORD generated and applied during `initdb`**, hardened override and UFW rules in place from the start)
5. Reconnect CloudTAK to your TAK Server through the bootstrap wizard (same as the original install)

End state: hardened from scratch with all three walls active (port removal, UFW, strong password).

**Data loss:** Anything stored only in CloudTAK's `gis` Postgres DB (mission overlays, layer configs, asset metadata, basemaps cache). Users, certificates, missions on TAK Server itself, Authentik identities — all unaffected, because those live elsewhere.

### Option B — Update Now only (no reinstall)

**Best for: operators who can't reinstall CloudTAK right now and need it running. Acceptable as a temporary measure — schedule the reinstall within 7 days.**

1. Console → **Update Now**
2. Done.

What you get:
- Public port `5433` no longer exposed (override + UFW)
- Public MinIO ports `9000`/`9002` no longer exposed (override + UFW)
- If compromise was detected, malware is **quarantined and disabled** but the DB contents are **not cleaned** — attacker may have created additional postgres roles, pg_cron jobs, or event triggers that survive

What you don't get:
- Strong random POSTGRES_PASSWORD (your existing `docker:docker` stays in place, but it's now unreachable from the internet, so functionally equivalent until a future port regression)
- Verified-clean DB contents (compromised installs only — clean installs have nothing to clean)

**Schedule a Remove + Reinstall window within a week** if you took Option B and you were on a compromised host. Watch for: unexpected high CPU returning, unfamiliar postgres users in `\du`, suspicious pg_cron entries (`SELECT * FROM cron.job`).

### Option C — Manual one-liner for stuck operators

If **Update Now** is broken or you can't get to the console, run this on the host via SSH to apply the network-level lockdown immediately:

```bash
# 1. Lock down the firewall (idempotent — safe to run anywhere)
sudo ufw deny 5433/tcp 2>/dev/null
sudo ufw deny 9000/tcp 2>/dev/null
sudo ufw deny 9002/tcp 2>/dev/null

# 2. If CloudTAK is installed, write an emergency override and recreate
if [ -d ~/CloudTAK ]; then
  cat > ~/CloudTAK/docker-compose.override.yml <<'OVERRIDE'
# Emergency v0.9.11 security override (manual install)
services:
  postgis:
    ports: !reset []
  store:
    ports: !reset
      - "127.0.0.1:9002:9002"
OVERRIDE
  cd ~/CloudTAK && docker compose up -d --force-recreate
fi

# 3. Check for the IOC (manual compromise check)
VOL=$(docker inspect cloudtak-postgis-1 -f '{{ range .Mounts }}{{ if eq .Destination "/var/lib/postgresql/data" }}{{ .Source }}{{ end }}{{ end }}' 2>/dev/null)
if [ -n "$VOL" ]; then
  echo "Postgis data volume: $VOL"
  ls "$VOL"/*.so 2>/dev/null && echo "⚠ COMPROMISE: .so files found in data volume — Remove + Reinstall CloudTAK"
  grep -E '^[^#]*shared_preload_libraries' "$VOL/postgresql.conf" 2>/dev/null && echo "⚠ COMPROMISE: shared_preload_libraries set — Remove + Reinstall CloudTAK"
fi
```

After running this, still update the console to v0.9.11 + do Remove + Reinstall when you can — the manual override doesn't include the strong-password logic, just the network lockdown.

### Option D — Take CloudTAK offline (extreme)

**For operators who don't actually use CloudTAK on this host and want the smallest attack surface possible.**

1. Console → **CloudTAK** module → **Stop** (or **Remove** to fully delete it)
2. Update Now
3. Don't reinstall CloudTAK

You lose CloudTAK. You gain peace of mind. If you have no users of CloudTAK on this VPS, this is a perfectly reasonable choice.

---

## How to check if you were compromised

Run this on the host:

```bash
# Identify the postgis data volume host path
VOL=$(docker inspect cloudtak-postgis-1 -f '{{ range .Mounts }}{{ if eq .Destination "/var/lib/postgresql/data" }}{{ .Source }}{{ end }}{{ end }}')
echo "Postgis data volume mounted at: $VOL"

# IOC 1: any .so file in data root (clean postgres never puts shared libs here)
ls -la "$VOL"/*.so 2>/dev/null

# IOC 2: shared_preload_libraries set to anything in postgresql.conf
grep -nE '^[^#]*shared_preload_libraries' "$VOL/postgresql.conf"

# IOC 3: a UID-70 postgres process whose `comm` is NOT "postgres"
ps -eo uid,pid,comm,args | awk '$1==70 && $3!~/^postgres/ {print}'

# IOC 4: sustained 100% × NCPU CPU usage with near-zero disk I/O on postgis container
docker stats --no-stream cloudtak-postgis-1 2>/dev/null
```

If **any** of those return something suspicious, treat the host as compromised and use Option A (Remove + Reinstall).

---

## Known IOCs from the live infection (responder, May 10 2026)

- File path: `/var/lib/postgresql/data/gcmanager-1.so` (inside postgis container)
- Size: 565,920 bytes
- SHA256: `715348a40250549100cbbeb2a8d68ffa323e671b55fc46e8df24c7016b11e10a`
- MD5: `914e0451b50c0c95c3a89fd5da419cb4`
- File type: ELF 64-bit LSB shared object, x86-64, static-pie, stripped, statically-linked musl libc (compiled for Alpine specifically)
- Process tell: postgres worker with `comm` = random 10-letter lowercase string (e.g. `epggfmfdnr`) while `argv[0]` still says `postgres: ...`
- Container artifacts: hidden 10-char-name files in `/var/tmp/` (e.g. `.eygkhpofrf`, `.jomccgmkjg`)
- Network: outbound SOCKS5 over Tor (`.onion` C2 addresses per Unit 42 PGMiner writeup)

Hashes will differ across deployments — attackers recompile the `.so` to evade signature detection. **The persistence mechanism (`.so` in data dir + `shared_preload_libraries` in `postgresql.conf`) is the reliable IOC, not the hash.**

---

## Upstream report

The root cause is `dfpc-coe/CloudTAK`'s upstream `docker-compose.yml`, not infra-TAK. We've reported the findings + IOCs + suggested fixes (bind to `127.0.0.1`, generate `POSTGRES_PASSWORD` on install, document the risk) to the CloudTAK maintainer for an upstream fix. infra-TAK's mitigation lands today; the upstream fix will protect every CloudTAK install regardless of downstream.

---

## What's NOT in this release

The original v0.9.11 plan (non-root console migration) is **pushed to v0.9.12** so this security fix could ship the same day the vulnerability was confirmed. No functional infra-TAK behavior changes outside the CloudTAK module.

---

## Files changed

- `app.py` — `_cloudtak_build_env_content()`, `_cloudtak_build_override_yml()`, `_auto_harden_cloudtak()` (new), 4 caller call sites
- `docs/RELEASE-v0.9.11-alpha.md` (this file)
- `docs/SECURITY-INCIDENT-2026-05-10-PGMINER.md` (new)
- `README.md`, `memory-bank/techContext.md`
