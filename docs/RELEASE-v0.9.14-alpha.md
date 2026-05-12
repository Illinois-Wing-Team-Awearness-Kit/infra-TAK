# v0.9.14-alpha — Authentik Admin Recovery: status read self-heals when akadmin is locked out

**Date:** 2026-05-12
**Type:** Hotfix to v0.9.13. No new features, no migrations, no operator pre-flight. Drop-in update from v0.9.13.

---

## TL;DR

v0.9.13 shipped the **Protected Admin Accounts** panel on `/authentik` so operators could one-click reactivate `akadmin` / `webadmin` after a TAK Portal "Disable" mishap. The panel worked in the lab. In production it surfaced as:

```
PROTECTED ADMIN ACCOUNTS                              [ ↻ Refresh ]
akadmin       ? Authentik API 403
webadmin      ? Authentik API 403
```

— no `⚠ DEACTIVATED` banner, no **Reactivate** button. The exact failure the panel was built to fix.

**v0.9.14 fixes it.** The status read now has the same `ak shell` fallback the recover action already had. After Update Now, the panel renders correctly even when both admin accounts are deactivated.

---

## What was wrong with v0.9.13

The bug is interesting enough to record so it doesn't repeat.

`AUTHENTIK_BOOTSTRAP_TOKEN` (the token infra-TAK pulls out of `~/authentik/.env`) is owned by `akadmin`. Authentik's permission model checks the *token owner's* permissions on every API call. If `akadmin.is_active = false` (the exact state that triggers the need for this panel), Authentik authenticates the token successfully but returns **HTTP 403 Forbidden** on every protected endpoint — including `GET /api/v3/core/users/?search=akadmin`. The token is valid, the user behind it just has no rights.

v0.9.13 anticipated this for the **write** path:

```python
# _recover_authentik_user (v0.9.13, unchanged in v0.9.14)
# Layer 1: API with bootstrap token
# Layer 2: docker exec authentik-server-1 ak shell  →  Django ORM .save()
```

…but the **read** path (`_get_authentik_admin_accounts_status`) only knew how to call the API. So on installs where the bug had already bitten — both admin accounts disabled — the panel couldn't see the state it was supposed to expose, never reached the `is_active === false` branch, and never drew the Reactivate button. Operator saw `? Authentik API 403` and nothing actionable.

The pre-v0.9.14 recovery in that state still required SSH:

```bash
docker exec authentik-server-1 sh -c 'echo "from authentik.core.models import User
for n in (\"akadmin\",\"webadmin\"):
    u=User.objects.filter(username=n).first()
    if u: u.is_active=True; u.is_superuser=True; u.save(); print(\"OK\", n)
" | ak shell'
```

That manual one-liner is exactly what v0.9.14 makes unnecessary.

---

## What ships in v0.9.14

### Fix A — `_read_authentik_admin_accounts_via_ak_shell()` (new helper)

Reads both protected admin users in a **single** `docker exec authentik-server-1 ak shell` call:

```python
from authentik.core.models import User
for _n in ('akadmin', 'webadmin'):
    _u = User.objects.filter(username=_n).first()
    if _u is None:
        print(f'AK-STATUS|{_n}|MISSING|0|0')
    else:
        print(f'AK-STATUS|{_n}|EXISTS|{int(bool(_u.is_active))}|{int(bool(_u.is_superuser))}')
```

- Python snippet is **base64-encoded** before being piped into `ak shell` (same pattern as v0.9.13's `_recover_authentik_user` ak-shell layer) — no quoting/escaping concerns at the shell / SSH / docker layers.
- Output is parsed line-by-line; missing lines come back as `error: 'ak shell did not report this user'` for that account.
- Only the whitelisted usernames (`_AUTHENTIK_RECOVERABLE_USERS = ('akadmin', 'webadmin')`) are ever interpolated into the snippet — same defense-in-depth as the recover path.
- One `docker exec` per panel render is the same cost the recover path already pays on click, so the panel's user-perceived latency goes from ~50ms (API) to ~300–500ms (ak-shell) **only when the API fails** — happy path stays fast.

### Fix B — `_get_authentik_admin_accounts_status()` layered read

```python
# Layered read (mirrors _recover_authentik_user):
#   1. Authentik REST API using the bootstrap token (normal case, fastest).
#   2. `ak shell` Django ORM inside authentik-server-1 (fallback for the
#      very scenario the panel exists to fix).
```

Logic:

1. Try the API for both users (the original v0.9.13 code path).
2. If **any** API call errored — `HTTPError` with status 401/403, network error, anything — switch to the `ak shell` fallback for *all* users so the entire panel renders from a consistent source.
3. If the ak-shell fallback also fails (container down, etc.), surface whatever the API gave us with a top-level `fallback_error` so the operator at least sees something instead of "Status unavailable."

The response JSON now carries a `source` field: `'api'` (normal case) or `'ak-shell'` (fallback hit). The UI shows a small dim caption — `status read via ak shell (Authentik API unavailable)` — under the rows when the fallback was used, so the operator knows the panel is healing itself.

### Fix C — UI escape hatch

Even with the read-path fallback in place, the JS now also renders a **Reactivate** button when `a.error` is set (it previously only rendered the button when `a.is_active === false`). The recover endpoint has its own independent ak-shell fallback that does not depend on the read working at all, so the operator always has a manual lever even if the status read fails for some reason we haven't anticipated.

---

## What it looks like in the original failure scenario

Before v0.9.14 (the screenshot that prompted this hotfix):

```
PROTECTED ADMIN ACCOUNTS                              [ ↻ Refresh ]
akadmin       ? Authentik API 403
webadmin      ? Authentik API 403
```

After v0.9.14 (same install, same disabled state):

```
PROTECTED ADMIN ACCOUNTS                              [ ↻ Refresh ]
akadmin       ⚠ DEACTIVATED                          [ Reactivate ]
webadmin      ⚠ DEACTIVATED                          [ Reactivate ]
status read via `ak shell` (Authentik API unavailable)
```

Click **Reactivate** on `akadmin` → recovers via ak-shell layer (still — because the API is still 403 until akadmin is healthy again) → `Reactivated akadmin via ak shell (API path: API 403). [via ak-shell]` → panel refreshes → `akadmin` flips to `✓ Active · superuser`, **and now the API is unwedged**. Reactivate `webadmin` → ideally goes `[via api]` this time since akadmin is back, but ak-shell still works either way.

---

## Where the changes live

- `app.py:30551` — `_read_authentik_admin_accounts_via_ak_shell()` (new helper).
- `app.py:30617` — `_get_authentik_admin_accounts_status()` layered read.
- `app.py:29072` — `refreshAdminAccounts()` JS escape-hatch render path + source-tag caption.

No changes to:

- The recover path (`_recover_authentik_user`) — it already had the right architecture in v0.9.13.
- The `_AUTHENTIK_RECOVERABLE_USERS` whitelist.
- The two HTTP endpoints (`/api/authentik/admin-accounts`, `/api/authentik/recover-admin`) — they keep the same URLs and verbs; the response JSON gains a `source` field but the v0.9.13 fields are unchanged.

---

## Operator notes

- **Update Now is drop-in.** No migrations, no channel toggle changes from v0.9.12 (`main` stays green), no operator pre-flight. The fix is in `app.py` and the inline JS template; the existing v0.9.13 panel is replaced by the v0.9.14 one on next page load.
- **If you were stuck on `? Authentik API 403` before this update:** after Update Now, hit `/authentik`, the panel should render with `⚠ DEACTIVATED` rows and **Reactivate** buttons. The dim caption `status read via ak shell` at the bottom tells you the API path is still wedged (expected — until you reactivate `akadmin` it stays that way).
- **If you already recovered manually via SSH** (the `ak shell` one-liner above): no harm done. The panel will simply render via the API path on next refresh and you'll see two green `✓ Active` rows.

---

## Why this matters for the next dev

A recovery feature has to survive the failure mode it's recovering from. v0.9.13 nailed it for the *write* path and missed it for the *read* path. The lesson recorded in `memory-bank/techContext.md`: when adding a feature that lets an operator out of a self-inflicted-foot-bullet state, every code path the panel touches — read **and** write — needs the no-API fallback. If you can express it as "the bootstrap token works *iff* akadmin is active," then any read or write through the bootstrap token is a bootstrap problem and needs the same ak-shell escape hatch.
