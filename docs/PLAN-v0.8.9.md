# v0.8.9-alpha — Work Plan

**Headline:** Fix Authentik recording the Docker bridge gateway IP (`172.18.0.1`) instead of the real client IP for every login event. Fleet-wide latent bug since the Caddy→Authentik wiring went in. Auto-heals on next console restart.

**Scope discipline:** This release ships the trusted-proxy CIDR fix **only**. The full fail2ban host-hardening feature and Authentik Reputation policy are parked to later releases. The trusted-proxy fix is a prerequisite for both — fail2ban that reads `client_ip: "172.18.0.1"` will ban the Docker gateway and DoS the stack; Reputation policy that records the gateway IP is useless.

**Precondition:** v0.8.8 soaked for ≥7 days on tak-10, responder, ssdnodes-validated, and Alex's R3930 before v0.8.9 is pulled by the fleet.

---

## The bug

Confirmed May 1 2026 on takserver2: a deliberate failed login from a cellular phone was recorded in Authentik's audit log as:

```json
{"action": "login_failed", "client_ip": "172.18.0.1", ...}
```

`172.18.0.1` is the Docker bridge gateway — not the phone's real public IP. Caddy correctly forwards `X-Forwarded-For`, but Authentik ignores it because `AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS` defaults to "trust nothing."

Per the [Authentik docs](https://docs.goauthentik.io/install-config/configuration/) — **same reference that surfaced the v0.8.7 `AUTHENTIK_WEB__WORKERS` bug** — `AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS` must be set to the CIDR of any trusted upstream proxy. We never set it.

This is fleet-wide: every infra-TAK install since the Caddy→Authentik wiring shipped has logged the wrong IP on every login event, every logout, every failed-login attempt.

**Impact without fix:**
- Audit log permanently wrong about where login attempts came from.
- Authentik Reputation policy (v0.9.0) would score all attempts as if from one IP — useless.
- Any fail2ban jail consuming `client_ip` would ban `172.18.0.1` (the Docker gateway) — DoSes the entire stack.

---

## The fix

**New idempotent migration `_authentik_fix_trusted_proxy_cidrs(plog)`** in `app.py`:

1. Reads `~/authentik/.env`. If missing, skip (Authentik not installed).
2. If `AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS` is already present in `.env`, record `idempotent-noop` and return.
3. Append `AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS=172.16.0.0/12,127.0.0.1/32,::1/128`:
   - `172.16.0.0/12` — covers all observed Docker bridge subnets fleet-wide (172.17 default, 172.18 authentik, 172.19 tak-portal, 172.20 infratak, 172.21 cloudtak).
   - `127.0.0.1/32`, `::1/128` — loopback (Guard Dog health probes).
4. Records `last_outcome: applied` to `settings.authentik_trusted_proxy_cidrs_fix`.
5. Caller triggers `_recreate_authentik_server_worker(reason='trusted-proxy-cidrs-migration')` — server+worker only, **LDAP outpost untouched**, cardinal rule upheld.

**Migration call order** (startup + post-update):

1. `_authentik_apply_official_tunings` (v0.8.7)
2. `_authentik_fix_pg_idle_timeout` (v0.8.8)
3. `_authentik_fix_ldap_flow_recursion` (v0.8.8)
4. **`_authentik_fix_trusted_proxy_cidrs`** (v0.8.9 — new, runs AFTER recursion fix to batch server restarts on old boxes)
5. `_authentik_verify_runtime_config` (v0.8.7+v0.8.8+v0.8.9)

**Verifier extension** — `_authentik_verify_runtime_config` gains a fourth probe:

```python
get_path(cfg, 'listen.trusted_proxy_cidrs')  # from ak dump_config
```

Asserts the result contains `172.16.0.0/12`. Surfaces in `settings.authentik_runtime_config_check.last_results.listen.trusted_proxy_cidrs`.

---

## Acceptance test

After `Update Now` or `git pull origin main` + console restart, from Alex's box or takserver2:

```bash
# 1. Audit check
sudo cat /root/infra-TAK/.config/settings.json | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('Trusted proxy:', json.dumps(d.get('authentik_trusted_proxy_cidrs_fix', {}), indent=2))
print()
print('Runtime verify:', d.get('authentik_runtime_config_check', {}).get('last_outcome'))
"

# 2. Verify runtime loaded the CIDR (not just .env)
docker exec authentik-worker-1 ak dump_config 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
cidrs=d.get('listen',{}).get('trusted_proxy_cidrs','NOT SET')
print('trusted_proxy_cidrs:', cidrs)
"

# 3. Real-world smoke test: fail one login from a phone on cellular, then check
docker logs authentik-server-1 --since 2m 2>&1 | grep 'login_failed' | tail -3
# client_ip must be the phone's real WAN IP, NOT 172.18.0.1
```

---

## What this does NOT ship

- **fail2ban host hardening** — parked to v0.9.x (needs this fix as a prerequisite, plus a 30-day soak).
- **Authentik Reputation policy** — parked to v0.9.0 (headline feature; blueprint diff + healing migration).
- **UI changes** — same scope discipline as v0.8.7/v0.8.8.
- **Configurable CIDR list via env var** — `172.16.0.0/12` is correct for every box we know. Operators who need a different CIDR can set `AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS` manually in `~/authentik/.env`; the migration's idempotent-noop path preserves operator-set values.
