# v0.9.15-alpha — TAK Portal Admin-Account Guardrail

**Date:** 2026-05-12
**Type:** Defense-in-depth release. Drop-in update — no migrations to run, no operator pre-flight.

---

## What the operator does

**Click Update Now in the Console.** That's it.

After the update:
- `akadmin` and `webadmin` no longer appear in TAK Portal's user list.
- Even if surfaced some other way, neither account can be modified from TAK Portal (Action-Lock).
- The existing v0.9.13 / v0.9.14 **Protected Admin Accounts** panel on `/authentik` stays in place as the last-resort recovery if any of this is ever bypassed.

No new clicks for the operator. No SSH. The guardrail is invisible until someone tries to do the wrong thing, at which point TAK Portal simply won't let them.

---

## Why this exists

v0.9.13 was triggered by an incident where a TAK Portal user clicked **Disable** on both `akadmin` and `webadmin` from the user-management UI, locking the operator out of Authentik entirely. v0.9.13 + v0.9.14 built the **detection + recovery** side (panel + one-click Reactivate, layered API → `ak shell`). v0.9.15 closes the **prevention** side: those two accounts can no longer be clicked at all from TAK Portal.

The TAK Portal author (Justin Davis) pointed out that TAK Portal already has two settings fields for exactly this:

- **Hidden User Prefixes** — usernames starting with these prefixes don't appear in the user list.
- **User Action-Lock Prefixes** — usernames starting with these prefixes appear (if visible) but cannot be modified.

infra-TAK is already authoritative for both fields in TAK Portal's `settings.json`. v0.9.15 just adds `akadmin` and `webadmin` to both lists by default and ships a tiny self-healing migration so existing installs get the change automatically on next Update Now.

---

## Three-layer defense

You now have three independent guardrails for the same incident class. Any one failing doesn't break the chain.

| Layer       | Where           | What it does                                                                               | Added in        |
| ----------- | --------------- | ------------------------------------------------------------------------------------------ | --------------- |
| **Prevent** | TAK Portal      | `akadmin` / `webadmin` are hidden + action-locked. Operator never sees a Disable button.   | v0.9.15         |
| **Detect**  | `/authentik`    | Protected Admin Accounts panel shows live `is_active` state. Reads survive Authentik 403s. | v0.9.13 + v0.9.14 |
| **Recover** | `/authentik`    | One-click **Reactivate** button. Layered API → `ak shell` so it works even when API is wedged. | v0.9.13 + v0.9.14 |

If a future TAK Portal release exposes a way past the hidden / action-lock pair, or the operator manages to clear those lists, or the accounts get disabled directly through Authentik's own admin UI — the panel and recover button are still there.

---

## What changed under the hood

(Skip this if you're an operator. This is for maintainers.)

<details>
<summary>Implementation</summary>

- **`_takportal_build_settings_dict()`** — defaults for two fields are bumped:
  - `USERS_HIDDEN_PREFIXES`: `"ak-,adm_,nodered-,ma-"` → `"akadmin,webadmin,ak-,adm_,nodered-,ma-"`
  - `USERS_ACTIONS_HIDDEN_PREFIXES`: `""` → `"akadmin,webadmin"`
  TAK Portal does **prefix** matching, so the literal strings `akadmin` and `webadmin` match those exact users (and any hypothetical future `akadminX` / `webadminX`). The pre-existing `ak-` prefix-with-dash is kept; it does not cover `akadmin` (no dash) which is why this is necessary.
- **`_auto_harden_takportal_settings()`** — new self-healing migration in `_run_post_update`, slotted between `_auto_harden_takportal` (port hardening) and `_auto_harden_mediamtx`. Logic:
  1. Skip if `~/TAK-Portal` doesn't exist (remote-Portal installs apply the new defaults on next "Update config & reconnect" click in the UI).
  2. Skip if the `tak-portal` container isn't running.
  3. Read the current `settings.json` from the container.
  4. If both `USERS_HIDDEN_PREFIXES` and `USERS_ACTIONS_HIDDEN_PREFIXES` already contain both `akadmin` and `webadmin`, no-op.
  5. Otherwise build merged settings via `_takportal_merged_settings_json()` (preserves `BRAND_LOGO_URL`, `TAK_SSH_ONBOARDED`, `TAK_SSH_LAST_HANDSHAKE_AT`), `docker cp` it into the container, `docker restart tak-portal`.
- **Recovery path unchanged.** `_recover_authentik_user` (write), `_get_authentik_admin_accounts_status` + `_read_authentik_admin_accounts_via_ak_shell` (read), the `/api/authentik/admin-accounts` and `/api/authentik/recover-admin` endpoints, and the Protected Admin Accounts UI panel are all untouched from v0.9.14.

No new dependencies, no new endpoints, no schema changes.

</details>

---

## Operator notes

- **Drop-in from v0.9.12 / v0.9.13 / v0.9.14.** No migrations to run, no channel toggle changes. Leave the toggle on `main` (green).
- **The hide + action-lock is reversible.** An operator who really needs to manage these accounts from TAK Portal can edit the prefix lists in TAK Portal's Settings → Authentik panel. infra-TAK will push the defaults back on the next "Update config & reconnect" / Update Now — same as it does today for any other infra-TAK-managed field. Use the Authentik native admin UI or `infratak /authentik`'s recovery panel for one-off admin work; don't fight the guardrail.
- **Remote-Portal installs** (TAK Portal not on the same VPS as the console) won't have the migration fire on Update Now — the post-update step skips when `~/TAK-Portal` is absent. Instead, click **TAK Portal → Update config & reconnect** in the console UI; the new defaults flow into the remote portal's `settings.json` via the same path the operator already uses for any other config change.
- **Already-customized prefix lists** will be overwritten by the new defaults the first time the migration fires (same as today — infra-TAK has always been authoritative for both fields; this isn't a new behaviour). Re-add any custom prefixes you need *after* the update; `akadmin` and `webadmin` will keep being pushed back automatically.

---

## Slack-able summary

> infra-TAK v0.9.15 ships the prevention side: `akadmin` and `webadmin` are now hidden + action-locked in TAK Portal so an operator can't click Disable / Delete on them from the user-management UI. Combined with the v0.9.13 + v0.9.14 detection + one-click recovery panel on /authentik, the original incident class is now closed from three independent directions. Update Now is drop-in.
