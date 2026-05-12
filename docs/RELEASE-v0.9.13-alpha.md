# v0.9.13-alpha — Authentik Admin Recovery

**Date:** 2026-05-12
**Type:** Operator-recovery feature. No security regressions, no breaking changes.

---

## What ships

One new feature and one supporting bug fix, both addressing the same incident class.

### The incident

An operator clicked **Deactivate** on `webadmin` and `akadmin` from inside TAK Portal's user-management UI. Authentik's "Deactivate" is just `PATCH /api/v3/core/users/{pk}/` with `{"is_active": false}` — fully reversible, no data lost — but once **both** admin accounts are deactivated, the operator is locked out of the Authentik UI entirely. Today the only way back in is to SSH to the host and either:

- `docker exec authentik-server-1 ak shell` and run a Django ORM update, or
- `docker exec authentik-postgresql-1 psql -U authentik -c "UPDATE authentik_core_user SET is_active=true WHERE username IN (…);"`

Neither is operator-friendly.

### Feature A — Protected Admin Accounts panel on /authentik

A new status panel on the Authentik page (below the existing `Admin user: akadmin · Show Password` row inside the **Access** card) shows live state for the two protected admin accounts:

```
PROTECTED ADMIN ACCOUNTS                              [ ↻ Refresh ]
akadmin       ✓ Active · superuser
webadmin      ⚠ DEACTIVATED                          [ Reactivate ]
```

- Status pills render green (`✓ Active`) when normal, red (`⚠ DEACTIVATED`) when an operator has flipped `is_active=false` via TAK Portal or Authentik's own admin UI.
- A **Reactivate** button appears only on deactivated rows. One click flips the bit back via Authentik's API and the row re-renders green.
- `webadmin` may be missing if TAK Server hasn't been deployed yet — the panel shows a quiet `— not present in Authentik` in that case, no alarm.
- Refresh button re-polls without reloading the page. The panel also polls on page load.

Backed by two new endpoints:

- `GET /api/authentik/admin-accounts` → reads `is_active` / `is_superuser` for both accounts via the bootstrap token in `~/authentik/.env`.
- `POST /api/authentik/recover-admin` with `{"user": "akadmin"|"webadmin"}` → flips `is_active=true` (and `is_superuser=true` if it had been demoted).

### Layered recovery — works even when the API is wedged

`_recover_authentik_user()` tries two paths in order:

1. **Authentik API** with `AUTHENTIK_BOOTSTRAP_TOKEN` from `~/authentik/.env`. This is the normal-case path — fast, no container shell needed. Reports `[via api]` in the success banner.
2. **`docker exec authentik-server-1 ak shell`** running a Django ORM `User.objects.filter(username=…).update(is_active=True, is_superuser=True)`. Bypasses API auth, broken flows, broken policies — anything short of the `authentik-server-1` container being down. Reports `[via ak-shell]`.

The Python script is base64-encoded before being piped into `ak shell` so there's zero quoting/escaping at the shell, ssh, or docker layers. The function is whitelist-enforced (`_AUTHENTIK_RECOVERABLE_USERS = ('akadmin', 'webadmin')`) so the endpoint can't be used to re-enable arbitrary accounts, and the whitelist also guarantees the username interpolated into the ak-shell script is one of two literals.

### Bug Fix B — Sync webadmin now also re-activates

`_ensure_authentik_webadmin()` (the function behind the existing **Sync webadmin to Authentik** button on the TAK Server page) was patching `is_superuser`, `path`, and `groups` on an existing webadmin record but **never** flipping `is_active` back to `true`. Result: if an operator deactivated webadmin and then clicked "Sync webadmin" to recover, the API set the password but Authentik still rejected every 8446 login because `is_active=false` blocks LDAP bind.

v0.9.13 adds the missing line:

```python
if user_obj.get('is_active') is not True:
    patch_fields['is_active'] = True
```

This was already correctly handled for `adm_ldapservice` in `_ensure_authentik_ldap_service_account()` — the equivalent path for webadmin had just been overlooked.

---

## Where the recovery lives

- **Code path:**
  - `_AUTHENTIK_RECOVERABLE_USERS` whitelist
  - `_get_authentik_admin_accounts_status()` — read-only status probe
  - `_recover_authentik_user(username)` — layered (API → ak shell) recovery
  - `GET /api/authentik/admin-accounts`
  - `POST /api/authentik/recover-admin`
  - UI panel in `AUTHENTIK_TEMPLATE` directly under the existing akadmin password row
  - JS handlers `refreshAdminAccounts()` and `reactivateAdmin()` next to `showAkPassword()`
- **No new dependencies. No new packages. No migrations.**

---

## Out of scope (intentional)

- `adm_ldapservice` is not in this panel. TAK Portal cannot deactivate that account, and there's no plausible path for an operator to do it accidentally via the UIs infra-TAK is responsible for. (If you ever do want it surfaced, it's a one-line change: add `'adm_ldapservice'` to `_AUTHENTIK_RECOVERABLE_USERS`.)
- TAK Portal's own UI fix (refuse to deactivate the two protected accounts, or require a typed confirmation) is in the TAK Portal repo, not infra-TAK. infra-TAK's panel is the safety net for when that guardrail is bypassed or the operator uses Authentik's native UI to do the same thing.
- No automatic auto-heal. The panel surfaces the problem and offers one-click recovery; it does not silently re-enable accounts behind the operator's back.

---

## Operator notes

- After Update Now, the **Protected Admin Accounts** panel appears under the akadmin password row on `/authentik`. If both accounts are healthy you see two green `✓ Active` rows — nothing to do.
- If an account is shown as `⚠ DEACTIVATED`, click **Reactivate**. The banner reports which path was used (`[via api]` is the normal case; `[via ak-shell]` means the bootstrap token was rejected and the Django-ORM fallback was used — worth investigating the token state once the operator is unblocked).
- Existing **Sync webadmin to Authentik** button (TAK Server page) now also restores `is_active=true`. So a deactivated webadmin can be recovered from either page; the Authentik page is the one to use if both `webadmin` and `akadmin` are deactivated.
- No changes to TAK Server, TAK Portal, Caddy, Guard Dog, or LDAP outpost behaviour.
