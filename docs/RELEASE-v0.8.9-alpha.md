# v0.8.9-alpha Release Notes

## What this fixes

One fleet-wide latent bug in the Authentik integration. Present on every infra-TAK install since the Caddy→Authentik wiring shipped. Auto-heals on next console restart after upgrade.

### Bug — Authentik recording Docker bridge IP instead of real client IP

Every Authentik login event (successful, failed, or timed-out) on every infra-TAK box has been recorded with `client_ip: "172.18.0.1"` — the Docker bridge gateway — instead of the real public IP of the user's device.

Root cause: Authentik defaults to trusting **no** upstream proxy headers. Caddy correctly forwards `X-Forwarded-For` on every request, but Authentik discards it because `AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS` was never configured. Authentik records the immediate-upstream connection source (the Docker bridge gateway) instead.

Per [the Authentik configuration docs](https://docs.goauthentik.io/install-config/configuration/) — the same reference that surfaced the v0.8.7 `AUTHENTIK_WEB__WORKERS` silent-ignore bug — `AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS` must be explicitly set to the CIDR of any trusted upstream proxy. We never set it. The default is "trust nothing," which is correct security posture in general but wrong for our Caddy-in-front-of-Authentik deployment shape.

**Why it matters:**
- Audit logs have been wrong since the project began. `admin` → Authentik login events show `client_ip: "172.18.0.1"` for every operator from every location.
- Authentik Reputation policy (planned for v0.9.0) works per-IP. With the gateway IP logged, every failed attempt looks like it came from the same internal IP — the policy would be completely ineffective.
- Any fail2ban jail consuming `client_ip` from Authentik logs would immediately ban `172.18.0.1`, taking down the entire Caddy→Authentik path and DoS-ing the stack.

**Fix:** Set `AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS=172.16.0.0/12,127.0.0.1/32,::1/128` in `~/authentik/.env`:
- `172.16.0.0/12` covers all observed Docker bridge subnets fleet-wide (172.17 default, 172.18 authentik, 172.19 tak-portal, 172.20 infratak, 172.21 cloudtak).
- `127.0.0.1/32` and `::1/128` cover loopback (Guard Dog health probes).

After the fix, Authentik reads `X-Forwarded-For` from Caddy and records the real client IP. Audit logs are accurate going forward.

---

## Changes shipped

### `app.py`

- **VERSION**: `0.8.8-alpha` → `0.8.9-alpha`.
- **New idempotent migration `_authentik_fix_trusted_proxy_cidrs(plog)`** — checks `~/authentik/.env` for `AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS`. If missing, appends the fleet-default CIDR list and returns `True`. Wired into `_startup_migrations` AND `_post_update_auto_deploy`, runs **after** the v0.8.8 recursion fix (to batch server restarts on old boxes — one restart serves both fixes). If changed, triggers `_recreate_authentik_server_worker(reason='trusted-proxy-cidrs-migration')` — server + worker only, LDAP outpost untouched. Persists `last_outcome` (`applied` or `idempotent-noop`) to `settings.authentik_trusted_proxy_cidrs_fix`.
- **`_authentik_verify_runtime_config` extension** — adds a fourth probe: reads `listen.trusted_proxy_cidrs` from `ak dump_config` output, asserts it contains `172.16.0.0/12`. Surfaces in `settings.authentik_runtime_config_check.last_results`. Success log line updated to include `trusted_proxy_cidrs=172.16.0.0/12`.

---

## Operator acceptance check

After `Update Now` or console restart on a v0.8.8 box:

```bash
sudo cat /home/takwerx/infra-TAK/.config/settings.json | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('Trusted proxy fix:', json.dumps(d.get('authentik_trusted_proxy_cidrs_fix', {}), indent=2))
print()
print('Runtime verify last_outcome:', d.get('authentik_runtime_config_check', {}).get('last_outcome'))
"
```

Expected on first v0.8.9 startup:
- `authentik_trusted_proxy_cidrs_fix.last_outcome`: `"applied"`, `last_value: "AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS=172.16.0.0/12,127.0.0.1/32,::1/128"`
- `authentik_runtime_config_check.last_outcome`: `"pass"`

Subsequent restarts:
- `authentik_trusted_proxy_cidrs_fix.last_outcome`: `"idempotent-noop"` (env var already present)
- `authentik_runtime_config_check.last_outcome`: `"pass"`

Runtime confirmation (optional):

```bash
docker exec authentik-worker-1 ak dump_config 2>/dev/null | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('trusted_proxy_cidrs:', d.get('listen', {}).get('trusted_proxy_cidrs', 'NOT SET'))
"
```

Should return `['172.16.0.0/12', '127.0.0.1/32', '::1/128']` or equivalent.

---

## Migration window

First console restart after upgrade:

| Step | Time |
|---|---|
| `_authentik_fix_trusted_proxy_cidrs`: write `.env` + server+worker recreate | ~15-30s |
| Wait for server ready | ~10-30s |
| Verifier run | ~10s |

**Total user-visible Authentik unavailability: ~35-60 seconds.** Existing browser sessions resume automatically. LDAP outpost stays up the whole time — TAK Server clients and field users are unaffected.

Subsequent restarts: sub-second no-op.

---

## What was explicitly NOT shipped

- **fail2ban host hardening** — parked. The trusted-proxy fix is a prerequisite; fail2ban without it would DoS the stack on first ban. A 30-day soak of this fix is required before fail2ban ships.
- **Authentik Reputation policy** — parked to v0.9.0. The upstream-recommended primary brute-force control; requires blueprint diff + healing migration.
- **UI changes** — same scope discipline as v0.8.7 and v0.8.8.

---

## Cardinal rules upheld

- **Server+worker restart only.** `docker compose up -d --force-recreate --no-deps server worker`. LDAP outpost (`authentik-ldap-1`) stays up. Bind cache preserved.
- **Idempotent.** Running the migration repeatedly on an already-fixed box is a sub-second no-op (env var already present → early return).
- **Audit trail in `settings.json`.** `authentik_trusted_proxy_cidrs_fix` key records outcome, timestamp, and applied value for every box.
- **Operator override preserved.** If an operator has already manually set `AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS` to a custom value, the migration detects it (any value, not just ours) and records `idempotent-noop` — never overwrites operator config.
