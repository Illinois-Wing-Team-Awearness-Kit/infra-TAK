# Authentik health testing — field validation playbook

Internal doc — dev branch only. Do not merge to main.

These are the exact commands used during the v0.8.5 fleet validation (tak-10, ssdnodes, responder — 30+ min of sustained real bind load) and the v0.8.6 Azure single-box test (tak-test-3). Run them in order after any Authentik deploy, Update Now, or suspected spiral.

All commands run **on the server** (SSH in first — see `docs/AZURE-SSH.md`).

---

## Quick pass — 60 seconds, tells you if you're healthy

Run these five back-to-back right after deploy or update. Everything should hit its target value.

```bash
# 1. Postgres idle-in-transaction — the single best spiral signal
#    Target: 0–3.  ≥30 = spiraling right now.
docker exec authentik-postgresql-1 psql -U authentik -d authentik -tAc \
  "SELECT count(*) FROM pg_stat_activity WHERE state='idle in transaction' AND application_name LIKE '%authentik%';"

# 2. SIGABRT / worker timeout — gunicorn killing workers under load
#    Target: 0 over last 30 min.
docker logs authentik-server-1 --since 30m 2>&1 | grep -cE "WORKER TIMEOUT|SIGABRT"

# 3. Outpost spiral markers — real errors, not noise (recursion, 502, nil pointer, result code 50)
#    Target: 0.  Any number here means active spiral or recent spiral.
docker logs authentik-ldap-1 --since 30m 2>&1 | grep -cE "exceeded stage recursion|502 bad gateway|503 service|nil pointer|result code 50"

# 4. LDAP routing — should be FQDN on any box with TAK Server installed
#    Target: https://<your-fqdn>  (NOT http://authentik-server-1:9000)
grep 'AUTHENTIK_HOST:' ~/authentik/docker-compose.yml

# 5. Gunicorn timeout — must be 120 on all v0.8.5+ boxes
#    Target: --timeout=120
docker exec authentik-server-1 printenv GUNICORN_CMD_ARGS
```

**All-green means:** idle-in-trans=0, SIGABRT=0, spiral markers=0, routing=FQDN, timeout=120.

---

## 30-minute soak test — sustained bind load validation

Used for post-release fleet validation. Leave these running in a watch loop while real TAK clients connect (or trigger manual binds). Each metric should hold steady at target values for the full window.

```bash
# Bind volume — how many binds in the last 5 minutes
# On a healthy busy box (Mission API / DataSync): 400–700 binds / 5 min (~1.5–2.4/sec) is normal
docker logs authentik-ldap-1 --since 5m 2>&1 | grep -c "Bind request"

# Postgres connection breakdown — full activity snapshot
docker exec authentik-postgresql-1 psql -U authentik -d authentik -tAc \
  "SELECT state, count(*) FROM pg_stat_activity WHERE application_name LIKE '%authentik%' GROUP BY state ORDER BY count DESC;"

# Postgres idle-in-trans count (the key number from above, easy to repeat)
docker exec authentik-postgresql-1 psql -U authentik -d authentik -tAc \
  "SELECT count(*) FROM pg_stat_activity WHERE state='idle in transaction' AND application_name LIKE '%authentik%';"

# Server CPU + memory snapshot (run a few times 30-60s apart to see burst-and-idle pattern)
docker stats authentik-server-1 authentik-worker-1 authentik-postgresql-1 --no-stream

# SIGABRT count — rolling 30-minute window (re-run periodically)
docker logs authentik-server-1 --since 30m 2>&1 | grep -cE "WORKER TIMEOUT|SIGABRT"

# Outpost spiral markers — rolling 30-minute window
docker logs authentik-ldap-1 --since 30m 2>&1 | grep -cE "exceeded stage recursion|502 bad gateway|503 service|nil pointer|result code 50"

# General outpost noise (these appear on healthy boxes too — for context only, not alarm signals)
docker logs authentik-ldap-1 --since 30m 2>&1 | grep -cE "failed to execute flow|EOF"

# Spiral monitor heartbeat — should fire once per 10 min from a single PID
sudo journalctl -u takwerx-console --since "35 min ago" | grep "spiral monitor"
```

**What healthy looks like during soak (tak-10 v0.8.5 reference):**

| Metric | Value |
|--------|-------|
| Postgres idle-in-trans | 0 (sustained) |
| SIGABRT count (30 min) | 0 |
| Spiral markers (30 min) | 0 |
| Bind volume (5 min) | 706 binds (~2.4/sec) |
| Server CPU | Burst 104% → 7% → 1% over 60s — this is NORMAL on DataSync boxes |
| Postgres CPU | Burst 51% → 2% → 0.3% over 60s — also NORMAL |
| Server mem | 575 MiB stable (no growth = no leak) |
| Spiral monitor | "outpost already on FQDN — skipping (already correct)" every 10 min |

**CPU spikes are not a problem** on Mission API / DataSync / Node-RED boxes. Clock-aligned DataSync polls fire 50+ binds in 1-2 seconds. Authentik crunches them, CPU spikes, drops. With `--timeout=120` the bursts complete cleanly. The metric that matters is SIGABRT count = 0 over 30+ min.

---

## Migration status — confirm all three ran correctly

After any Update Now or fresh deploy, check that the three key migrations fired:

```bash
SETTINGS=$(cat /root/infra-TAK/.config/settings.json 2>/dev/null || cat ~/.config/settings.json 2>/dev/null)

# Gunicorn timeout migration — should show value:120
echo "$SETTINGS" | python3 -c "import json,sys; s=json.load(sys.stdin); print('gunicorn_timeout:', s.get('authentik_gunicorn_timeout_migration','NOT RECORDED'))"

# Proactive routing migration — "not recorded" on already-FQDN boxes is OK (see note below)
echo "$SETTINGS" | python3 -c "import json,sys; s=json.load(sys.stdin); print('proactive_routing:', s.get('authentik_proactive_routing_migration','NOT RECORDED'))"

# Last spiral repair (should be "not recorded" on healthy boxes)
echo "$SETTINGS" | python3 -c "import json,sys; s=json.load(sys.stdin); print('spiral_last_repair:', s.get('authentik_spiral_last_repair','NOT RECORDED'))"
```

**"NOT RECORDED" on proactive_routing is a SUCCESS** if the box was already on FQDN routing before v0.8.5 — the migration found nothing to do and didn't write. Confirm via journalctl:

```bash
sudo journalctl -u takwerx-console --since "1 hour ago" | grep "proactive routing"
# Expected: "proactive routing: outpost already on FQDN — skipping (already correct)"
```

---

## LDAP SA bind — manual verification

After Authentik deploy, or any time you suspect LDAP is broken:

```bash
# Install ldapsearch if missing
apt-get install -y ldap-utils 2>/dev/null || yum install -y openldap-clients 2>/dev/null

# Read SA password from .env
SA_PASS=$(grep AUTHENTIK_BOOTSTRAP_TOKEN ~/authentik/.env | cut -d= -f2-)

# Test bind + search (should return the adm_ldapservice user entry)
ldapsearch -x -H ldap://localhost:389 \
  -D "cn=adm_ldapservice,ou=users,dc=takldap" \
  -w "$SA_PASS" \
  -b "ou=users,dc=takldap" \
  -s one "(cn=adm_ldapservice)" cn

# Check LDAP container log for the bind result
docker logs authentik-ldap-1 --since 30s 2>&1 | grep -E "authenticated|bind failed|adm_ldapservice"
```

**Healthy result:** ldapsearch returns `dn: cn=adm_ldapservice,ou=users,dc=takldap` and Docker logs show `"authenticated from session"`.

**Never search `dc=takldap` with base scope** — Authentik doesn't expose a root object there and ldapsearch returns error 32 even on a healthy bind. Always search `ou=users,dc=takldap` or `ou=groups,dc=takldap`.

---

## Spiral diagnosis — if something looks wrong

```bash
# Full outpost log — last 1000 lines (enough to catch markers even on busy boxes)
docker logs authentik-ldap-1 --tail 1000 2>&1 | grep -E "result code 50|nil pointer|exceeded stage recursion|502 bad gateway|503 service"

# Active Postgres queries (spiral = 200+ on policybindingmodel)
docker exec authentik-postgresql-1 psql -U authentik -d authentik -tAc \
  "SELECT query, count(*) FROM pg_stat_activity WHERE state='active' AND application_name LIKE '%authentik%' GROUP BY query ORDER BY count DESC LIMIT 5;"

# Server error log — gunicorn crashes
docker logs authentik-server-1 --since 1h 2>&1 | grep -E "CRITICAL|WORKER TIMEOUT|SIGABRT|Worker.*was sent"

# LDAP routing (confirm FQDN, not direct internal)
grep 'AUTHENTIK_HOST:' ~/authentik/docker-compose.yml
# Should be: AUTHENTIK_HOST: https://<fqdn>
# If it shows http://authentik-server-1:9000 AND TAK Server is installed → proactive migration didn't run
# Fix: trigger Update Now, or run from console → Authentik → Update

# Caddy reachable from LDAP container (precondition for routing migration)
docker exec authentik-ldap-1 wget -qO- --timeout=5 https://$(grep AUTHENTIK_HOST ~/authentik/.env | cut -d= -f2- | sed 's|https://||')/-/health/live/ && echo "caddy ok"
```

---

## Post-deploy verification block (run once after fresh deploy)

From `docs/HANDOFF-LDAP-AUTHENTIK.md` — the definitive post-deploy checklist:

```bash
# 1. Version
grep '^VERSION' ~/infra-TAK/app.py

# 2. Routing — should be FQDN
grep 'AUTHENTIK_HOST:' ~/authentik/docker-compose.yml

# 3. Gunicorn timeout — should be 120
docker exec authentik-server-1 printenv GUNICORN_CMD_ARGS

# 4. Health signals — all zero
docker exec authentik-postgresql-1 psql -U authentik -d authentik -tAc \
  "SELECT count(*) FROM pg_stat_activity WHERE state='idle in transaction' AND application_name LIKE '%authentik%';"
docker logs authentik-server-1 --since 10m 2>&1 | grep -cE "WORKER TIMEOUT|SIGABRT"
docker logs authentik-ldap-1 --since 10m 2>&1 | grep -cE "exceeded stage recursion|502 bad gateway|nil pointer"

# 5. Migration records
python3 -c "
import json
s = json.load(open('/root/infra-TAK/.config/settings.json'))
for k in ['authentik_proactive_routing_migration', 'authentik_gunicorn_timeout_migration']:
    print(f'{k}: {s.get(k) or \"(not recorded)\"}')"

# 6. Spiral monitor alive
sudo journalctl -u takwerx-console --since "15 min ago" | grep "spiral monitor"
```

---

## What the numbers mean — reference

| Metric | Healthy | Investigate | Spiraling |
|--------|---------|-------------|-----------|
| Postgres idle-in-trans | 0–3 | 10–29 | ≥30 |
| SIGABRT count (30 min) | 0 | 1–2 (transient restart) | 3+ (recurring) |
| Outpost recursion/502/nil-ptr (30 min) | 0 | 1 (transient) | 2+ |
| Bind volume (5 min) | Any | — | — (volume alone isn't a problem) |
| Gunicorn timeout | 120 | <120 on TAK box | 30 (default — will SIGABRT under load) |
| LDAP routing | `https://<fqdn>` | — | `http://authentik-server-1:9000` on TAK box |

See `docs/HANDOFF-LDAP-AUTHENTIK.md` for full incident history and rules.
