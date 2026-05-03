# Release Notes — v0.9.2-alpha

## What ships

### Feature A — Authentik Reputation Policy

Adds a flow-level brute-force check inside Authentik, complementary to Fail2ban.

- Every failed login decrements the source-IP score by 1
- When the score drops below the threshold (default -5), Authentik blocks the login at the flow level — before the password stage even runs
- Scores recover automatically over time
- Applied automatically to `ldap-authentication-flow` on first startup via `_authentik_setup_reputation_policy()`
- **UI card** on the Authentik page (visible when Authentik is running): enable/disable toggle, configurable threshold, live table of top flagged IPs, "Clear All Scores" button
- **API routes**:
  - `GET /api/authentik/reputation/status`
  - `POST /api/authentik/reputation/config`
  - `POST /api/authentik/reputation/scores/clear`

### Feature B — SSH Fail2ban Jail

Extends the existing Fail2ban module with an SSH jail for host-level brute-force protection.

- Uses the built-in `sshd` filter; monitors `/var/log/auth.log`
- Default thresholds: maxretry=3, findtime=10 min, bantime=60 min (stricter than Authentik — SSH brute-force is more severe)
- Opt-in via the Fail2ban page — does not require Authentik to be installed
- Guard Dog email alert fires on ban (reuses `infratak-guarddog.conf`)
- **New UI card** on the Fail2ban page: enable toggle, stats, config, whitelist (ignoreip), ban list, unban button
- **API routes**:
  - `GET /api/fail2ban/ssh/status`
  - `POST /api/fail2ban/ssh/config`
  - `POST /api/fail2ban/ssh/unban`

## Version

`0.9.1-alpha` → `0.9.2-alpha`
