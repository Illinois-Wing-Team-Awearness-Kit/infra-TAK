# v0.9.14-alpha — Authentik Admin Recovery Hotfix

**Date:** 2026-05-12
**Type:** Hotfix to v0.9.13. Drop-in update — no migrations, no operator pre-flight.

---

## What the operator does

Two clicks. No SSH. No copy-paste commands.

1. **Console → Update Now.**
2. **Authentik page → click `Reactivate` next to each `⚠ DEACTIVATED` row.**

That's it.

---

## What you'll see on the Authentik page

**Before this update** (the broken v0.9.13 panel):

```
PROTECTED ADMIN ACCOUNTS                              [ ↻ Refresh ]
akadmin       ? Authentik API 403
webadmin      ? Authentik API 403
```

No Reactivate buttons. Dead end.

**After this update** (same install, same disabled state):

```
PROTECTED ADMIN ACCOUNTS                              [ ↻ Refresh ]
akadmin       ⚠ DEACTIVATED                          [ Reactivate ]
webadmin      ⚠ DEACTIVATED                          [ Reactivate ]
status read via `ak shell` (Authentik API unavailable)
```

Click `Reactivate` on `akadmin` → row flips to `✓ Active · superuser`. Click `Reactivate` on `webadmin` → same. Done. Operator can now log in to Authentik at the normal URL.

The small caption at the bottom (`status read via ak shell …`) is just telling you the panel knows the Authentik API is wedged and is reading status through the container instead — it'll disappear on the next refresh once `akadmin` is reactivated.

---

## Why v0.9.13's panel didn't work in this case (short version)

The Authentik API token in `~/authentik/.env` is owned by `akadmin`. When the operator clicked **Disable** on `akadmin` in TAK Portal, that token's owner had no permissions, so every API call returned `403 Forbidden`. v0.9.13's status check only knew how to ask the API, so it got 403 and didn't draw the Reactivate button. v0.9.13's recover-button code *did* have a no-API fallback (it shells into the Authentik container and updates the database directly), but the read code didn't. **v0.9.14 gives the read path the same fallback.**

---

## Operator notes

- **Drop-in update from v0.9.12 or v0.9.13.** No migrations. The `main`/`dev` channel toggle from v0.9.12 is unchanged — leave it on `main` (green).
- **If you already recovered manually via SSH before updating** — no harm done. After Update Now the panel will simply render two green `✓ Active` rows and the bottom caption won't appear.
- **No changes to TAK Server, TAK Portal, Caddy, Guard Dog, or LDAP outpost behaviour.** Same URLs, same logins, same everything — except the panel works now.

---

## For maintainers (skip unless you're debugging)

<details>
<summary>Where the changes live in app.py</summary>

- `_read_authentik_admin_accounts_via_ak_shell()` — new helper. Reads `is_active` / `is_superuser` for the whitelisted admin users via one `docker exec authentik-server-1 ak shell` call. Base64-encoded snippet (same pattern as v0.9.13's recover ak-shell layer — zero shell/ssh/docker quoting concerns).
- `_get_authentik_admin_accounts_status()` — now layered. Try the Authentik API first; if any call errors (HTTPError, 401/403, network), switch the entire response to the ak-shell fallback so the panel renders from one consistent source. Response JSON gains a `source: 'api' | 'ak-shell'` field.
- `refreshAdminAccounts()` (inline JS) — the Reactivate button now also renders when `a.error` is set (previously only when `a.is_active === false`). Defense-in-depth in case both read paths fail.

No changes to: `_recover_authentik_user` (the write path was already correct in v0.9.13), `_AUTHENTIK_RECOVERABLE_USERS` whitelist, or the two HTTP endpoint URLs/verbs.

</details>

<details>
<summary>The lesson</summary>

A recovery feature has to survive the failure mode it's recovering from. v0.9.13 got that right for the *write* path and missed it for the *read* path because the bootstrap-token-owned-by-akadmin problem is symmetric: any read **or** write through the bootstrap token is a bootstrap problem when `akadmin.is_active = false`. Both directions need the same no-API escape hatch. Recorded in `memory-bank/techContext.md` so this doesn't repeat.

</details>
