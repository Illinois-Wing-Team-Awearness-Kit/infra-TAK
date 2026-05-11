# v1.0.0 — Feature Plan (Major Release)

> Not yet implemented. This is a planning document. v1.0.0 is reserved for the **non-root console migration** — a substantial behavioral change that warrants the major version bump.

The non-root migration was originally scoped for v0.9.3, deferred to v0.9.8, then displaced by the v0.9.5–v0.9.11 PostgreSQL CPU / cryptominer crisis. With the security-hardening work consolidated into v0.9.12, the remaining v0.9.x cycle is bug-fix only. v1.0.0 carries the big-disruption migration so operators get a clear semver signal that something fundamental changes about how the console runs.

---

## Non-root console migration (`takwerx` sudo user)

Move the infra-TAK console off root. Scaffolding has been in `app.py` since v0.9.2 (`_sudo_wrap`, `_write_priv`, `_read_priv` helpers); the actual provisioning and service migration was pulled to keep early v0.9.x releases stable.

### What needs to happen

**1. User provisioning (`provision_takwerx`)**
- Create system user `takwerx` with a home directory.
- Add to `sudo` group with a targeted `NOPASSWD` sudoers entry covering only the commands the console needs (e.g. `systemctl`, `docker`, `pg_dump`, `apt`, `ufw`).
- Generate SSH key for the user if needed.

**2. Service migration**
- Update the systemd unit (`/etc/systemd/system/takwerx-console.service`) to run as `User=takwerx`.
- Set correct `WorkingDirectory` and `ExecStart` paths under `/home/takwerx/`.
- Fix the Python venv shebang paths so they work under the new user.

**3. File ownership / directory move**
- Move (or symlink) the infra-TAK install directory from `/root/infra-TAK` to `/home/takwerx/infra-TAK`.
- Move all module directories (`/root/CloudTAK`, `/root/TAK-Portal`, `/root/authentik`, etc.) or update settings to point to new paths.
- Ensure Guard Dog scripts (`tak-post-start.sh`, `tak-boot-sequencer.sh`, etc.) have no hardcoded `/root/` paths — already cleaned in v0.9.2.

**4. `start.sh` automation**
- `start.sh` should detect if running as root and automatically provision `takwerx`, migrate dirs, rewrite the service unit, and restart as the new user — all in one pass.
- Must be idempotent — safe to re-run.

**5. Update path for existing operators**
- Existing operators on root need a clean migration path via "Update Now" or a one-time migration button.
- The console should show a banner if still running as root in v1.0.0+, prompting the operator to run the migration.
- Detect-and-warn lands first in v0.9.12; the actual migrator lands in v1.0.0.

### Scaffolding already in place (v0.9.2)
- `_sudo_wrap(cmd)` — wraps a shell command with `sudo` when console is not root.
- `_write_priv(path, content)` — writes to privileged paths via sudo.
- `_read_priv(path)` — reads privileged paths via sudo.
- `_find_settings()` — path-agnostic settings file discovery (works from any user's home).

### Key lessons from the failed v0.9.2 attempt
- The venv shebang (`/root/infra-TAK/venv/bin/python`) is hardcoded and breaks under a different user — must be rebuilt or symlinked.
- `WorkingDirectory` in the systemd unit must match the actual install path exactly.
- `cap_drop` on the Docker socket is irrelevant here — the issue was purely path and permission.
- Do not `chown` the entire repo tree mid-process while the console is running from it.
- Test the new service unit manually (`systemd-run --uid=takwerx ...`) before committing the unit file.

---

## Other items earmarked for v1.0.0

- **Console "Service Exposure" panel** — operator-facing visual of every port + actual bind + UFW status (green/yellow/red), grounded in the Tier 1/3/4/5 classification adopted in v0.9.12.
- **Documentation overhaul** — `docs/PORT-EXPOSURE-POLICY.md` becomes the canonical reference; README ports table rebuilt from it.
- **Ubuntu 24.04 LTS support** (currently 22.04 only). The non-root migration is a natural time to broaden supported OS targets.
- **First-class two-server / federation HA story** consolidated into a single "Topology" page.

These are stretch goals — the core v1.0.0 deliverable is the non-root migration. Anything else slips to v1.0.1 if it isn't ready.

---

## Out of scope for v1.0.0
- Anything still on the v0.9.x bug-fix cadence (port-binding regressions, route-level patches).
- Edge bridge module (→ tracked separately in `EDGE-BRIDGE-MODULE-PLAN.md`).
- mbtileserver module (→ tracked separately in `PLAN-mbtileserver-module.md`).
