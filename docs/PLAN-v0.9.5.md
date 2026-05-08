# v0.9.5 — Feature Plan

> Not yet implemented. This is a planning document.

Non-root console migration was originally scoped for v0.9.3 but pulled to keep that release focused on MediaMTX Fail2ban, container hardening fixes, and the kernel patch banner.

---

## Non-root console migration (`takwerx` sudo user)

Move the infra-TAK console off root. The scaffolding is already in `app.py` from v0.9.2 (`_sudo_wrap`, `_write_priv`, `_read_priv` helpers), but the actual provisioning and service migration was pulled from v0.9.2 to keep that release stable.

### What needs to happen

**1. User provisioning (`provision_takwerx`)**
- Create system user `takwerx` with a home directory
- Add to `sudo` group with a targeted `NOPASSWD` sudoers entry covering only the commands the console needs (e.g. `systemctl`, `docker`, `pg_dump`, `apt`)
- Generate SSH key for the user if needed

**2. Service migration**
- Update the systemd unit (`/etc/systemd/system/takwerx-console.service`) to run as `User=takwerx`
- Set correct `WorkingDirectory` and `ExecStart` paths under `/home/takwerx/`
- Fix the Python venv shebang paths so they work under the new user

**3. File ownership / directory move**
- Move (or symlink) the infra-TAK install directory from `/root/infra-TAK` to `/home/takwerx/infra-TAK`
- Move all module directories (`/root/CloudTAK`, `/root/TAK-Portal`, etc.) or update settings to point to new paths
- Ensure Guard Dog scripts (`tak-post-start.sh`, `tak-boot-sequencer.sh`, etc.) have no hardcoded `/root/` paths — already cleaned in v0.9.2

**4. `start.sh` automation**
- `start.sh` should detect if running as root and automatically provision `takwerx`, migrate dirs, rewrite the service unit, and restart as the new user — all in one pass
- Must be idempotent — safe to re-run

**5. Update path**
- Existing operators on root need a clean migration path via "Update Now" or a one-time migration script
- The console should show a banner if still running as root post-v0.9.5, prompting the operator to run the migration

### Scaffolding already in place (v0.9.2)
- `_sudo_wrap(cmd)` — wraps a shell command with `sudo` when console is not root
- `_write_priv(path, content)` — writes to privileged paths via sudo
- `_read_priv(path)` — reads privileged paths via sudo
- `_find_settings()` — path-agnostic settings file discovery (works from any user's home)

### Key lessons from the failed v0.9.2 attempt
- The venv shebang (`/root/infra-TAK/venv/bin/python`) is hardcoded and breaks under a different user — must be rebuilt or symlinked
- `WorkingDirectory` in the systemd unit must match the actual install path exactly
- `cap_drop` on the Docker socket is irrelevant here — the issue was purely path and permission
- Do not `chown` the entire repo tree mid-process while the console is running from it
- Test the new service unit manually (`systemd-run --uid=takwerx ...`) before committing the unit file

---

## Out of scope for v0.9.5
- Split-server snapshot/rollback (→ v0.9.4)
- Per-feed Node-RED certs (→ future)
