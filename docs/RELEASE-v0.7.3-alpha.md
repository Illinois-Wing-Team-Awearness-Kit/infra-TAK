# v0.7.3-alpha — Immediate Password Propagation via LDAP Session Fix

## What's New

### Password Changes Now Take Effect Immediately

**Problem (pre-v0.7.3):** When a user or admin reset a password in TAK Portal (or directly in Authentik), the new password could take up to 24 hours to work on iTAK/ATAK. The old password continued to authenticate for the remainder of the cached session.

**Root cause:** Authentik's LDAP outpost runs with `bind_mode: cached`. A successful bind is cached for the lifetime of the user's authentication session. The `ldap-authentication-login` User Login stage had `session_duration: seconds=0`, which Authentik interprets as a full browser session — effectively ~24 hours for LDAP binds. Password changes made via TAK Portal (which directly call Authentik's `set_password` API) do not invalidate existing cached sessions.

**Fix:** Set `session_duration: seconds=120` on the `ldap-authentication-login` stage. Cached bind sessions now expire in 2 minutes. A user who changes their password will be able to authenticate with the new password within 2 minutes of the reset.

This is the correct and only reliable knob for this behavior. `token_validity` on the LDAP provider (a previous investigation path) is silently ignored by Authentik for LDAP providers — it only applies to OAuth/proxy providers.

### Why Not `bind_mode: direct`?

Direct mode re-authenticates against Authentik on every single LDAP bind — which TAK Server issues on a ~2-second polling cycle. At scale (hundreds of users, active sessions) this overwhelms the Authentik worker. Cached mode with a short session duration is the correct tradeoff: low resource usage, fast propagation.

### Self-Healing via Resync

The fix is enforced every time **Resync LDAP to TAK Server** runs — the console looks up `ldap-authentication-login` by name and patches `session_duration` regardless of its current value. Existing deployments that ran Resync after pulling this update are already fixed. Fresh deploys get the correct value baked into the blueprint.

## What Changed

| File | Change |
|------|--------|
| `app.py` | Blueprint YAML: `session_duration: seconds=0 → seconds=120` (two copies) |
| `app.py` | `_create_ldap_stage()` call: `seconds=0 → seconds=120` (fresh install path) |
| `app.py` | New unconditional PATCH in `_ensure_ldap_flow_authentication_none()`: looks up `ldap-authentication-login` and patches `session_duration=seconds=120` on every Resync run |
| `app.py` | Removed `token_validity: minutes=2` from all `providers/ldap` PATCH calls (wrong field, was silently ignored) |
| `app.py` | VERSION bumped to `0.7.3-alpha` |

## Operator Action Required

**For existing deployments:** After pulling this update, go to the TAK Server page → **Resync LDAP to TAK Server**. This patches the live Authentik stage immediately. Verify with:

```bash
TOKEN=$(grep AUTHENTIK_BOOTSTRAP_TOKEN ~/authentik/.env | cut -d= -f2)
curl -s -H "Authorization: Bearer $TOKEN" \
  'http://127.0.0.1:9090/api/v3/stages/user_login/?search=ldap' | \
  python3 -c "import sys,json; r=json.loads(sys.stdin.read())['results']; [print(f'name={s[\"name\"]} session_duration={s.get(\"session_duration\")}') for s in r]"
```

Expected output: `name=ldap-authentication-login session_duration=seconds=120`

**Fresh deployments:** No action needed — the blueprint sets the correct value at deploy time.

## Testing

1. Create a user in TAK Portal, set a password, log in on ATAK/iTAK — should work immediately.
2. Reset the password to a new value in TAK Portal.
3. Try the new password on the device — should authenticate immediately.
4. Wait ~2 minutes, then try the old password — should be rejected.

## Files Changed

- `app.py` — fix + VERSION bump
- `docs/RELEASE-v0.7.3-alpha.md` — this file
- `README.md` — latest release updated
- `STATUS.md` — session state updated
- `docs/HANDOFF-LDAP-AUTHENTIK.md` — LDAP cache behavior documented
