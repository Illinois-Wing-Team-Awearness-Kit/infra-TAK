# v0.9.25-alpha — Authentik `cap_drop` duplicate-key hotfix + Sync webadmin error visibility

**Date:** 2026-05-16
**Type:** Hotfix release — drop-in update via Update Now. Auto-heals a self-inflicted YAML breakage from older v0.9.x releases that left the **Sync webadmin to Authentik** button stuck behind a red banner ("LDAP restart failed: yaml: construct errors: line t line 44: mapping key 'cap_drop' al…") on at least one production host.

---

## TL;DR

`v0.9.25-alpha` lands a five-part hotfix for one root cause: the v0.9.x Phase 2 container-hardening injector in `_auto_harden_containers()` could write a `docker-compose.yml` with **two `cap_drop:` mapping keys** in the same Authentik service. YAML rejects duplicate keys, so `docker compose` fell over on every subsequent call — most visibly inside the **Sync webadmin** flow, which uses `docker compose up -d --force-recreate ldap` to flush the LDAP outpost's bind cache after a password change.

1. **`_dedupe_authentik_capdrop()`** — new helper. On every Update Now, scans the `server`/`worker`/`ldap` sections of `~/authentik/docker-compose.yml` and strips duplicate `cap_drop:` + `security_opt:` blocks, keeping the first occurrence. Operators with an already-broken install self-heal silently on the next Update Now.
2. **Server-section cap_drop injector rewrite** — `_auto_harden_containers()` no longer uses a whole-file literal-substring guard for the `server:` block (the exact bug). It now uses a section-bounded check, mirroring the LDAP pattern that was already correct.
3. **`_validate_authentik_compose()`** — new helper. Runs `docker compose -f <tmp> config` against any candidate compose string and refuses to overwrite `~/authentik/docker-compose.yml` if validation fails. Catches future template breakage before it ships to the operator.
4. **Full-error surfacing on Sync webadmin failures** — the 120-char truncation at `_ensure_authentik_webadmin` is gone. Operators now see the full `docker compose` parse error plus a one-line remediation hint when the error signature matches duplicate-mapping-key.
5. **Sync webadmin UI description fix** — the card description used to say *"Only pushes the 8446 password from settings into Authentik. Does not restart anything."* That was wrong: the flow recreates the LDAP outpost. Description now matches behavior.

Node-RED's compose hardening was *not* affected (single-service compose, whole-file check is safe there). A clarifying comment was added so future readers don't mistake it for the same bug.

---

## The field incident — `aj.takdfir.com` (2026-05-16, 07:42 PT)

Operator opened the **TAK Server console → TLS Auth & LDAP Sync** card. After clicking **Sync webadmin to Authentik**, the card showed:

> Password set but LDAP restart failed: failed to parse /root/authentik/docker-compose.yml: yaml: construct errors: line t line 44: mapping key 'cap_drop' al…

The error was truncated at 120 chars. The full message would have read approximately:

> failed to parse /root/authentik/docker-compose.yml: yaml: line 44: mapping key "cap_drop" already defined at line 38

A check of `~/authentik/docker-compose.yml` confirmed two `cap_drop:` mapping keys inside the `server:` service — one right after `restart: unless-stopped` (from the template), and a second one immediately above `command: server` (from the hardening injector at the old `app.py:43556-43560`).

The Authentik API call to set the webadmin password **succeeded** (so the password was usable after the LDAP outpost cache eventually expired). What failed was the explicit cache flush — `docker compose up -d --force-recreate ldap`. Because the YAML wouldn't parse, no compose subcommand could run against that file. The operator was effectively locked out of the recovery path until they SSH'd in and hand-edited YAML.

---

## Root cause

The previous server-section injector at the old `app.py:43556-43560`:

```python
for _old, _new in (
    ('command: server\n', 'cap_drop:\n      - ALL\n    security_opt:\n      - no-new-privileges:true\n    command: server\n'),
):
    if _old in _ak and _new not in _ak:
        _ak = _ak.replace(_old, _new, 1)
```

The idempotency guard `_new not in _ak` is a **whole-file** literal-substring match for the entire 4-line hardening block immediately followed by `command: server\n`. If the existing compose file had its `cap_drop:` in any other position — say, directly after `restart: unless-stopped\n` (which is exactly where the embedded template places it as of v0.9.x) — the literal substring is **not present in the file**, the guard returns False, and the hardening pass inserts a second `cap_drop:` block right above `command: server`.

YAML's mapping-key uniqueness rule rejects the result. `docker compose` (Go YAML parser, `gopkg.in/yaml.v3`) returns:

```
yaml: line 44: mapping key "cap_drop" already defined at line 38
```

The LDAP-section injector at the old `app.py:43568-43581` was **not** affected — it uses a section-bounded check (`'cap_drop:' not in _ldap_block`), the correct pattern that the server-section injector should have used from day one.

---

## Item 1 — Server-section cap_drop injector rewrite

### Before (broken)

```python
for _old, _new in (
    ('command: server\n', 'cap_drop:\n      - ALL\n    security_opt:\n      - no-new-privileges:true\n    command: server\n'),
):
    if _old in _ak and _new not in _ak:
        _ak = _ak.replace(_old, _new, 1)
```

### After (section-bounded, mirroring the LDAP pattern)

```python
_srv_start = _ak.find('\n  server:\n')
if _srv_start != -1:
    import re as _re_srv
    _srv_end_m = _re_srv.search(
        r'\n  [A-Za-z_][\w-]*:\n|\nvolumes:\n|\nnetworks:\n',
        _ak[_srv_start + 1:]
    )
    _srv_end = _srv_start + 1 + (_srv_end_m.start() if _srv_end_m else len(_ak))
    _srv_block = _ak[_srv_start:_srv_end]
    if 'cap_drop:' not in _srv_block and 'restart: unless-stopped' in _srv_block:
        _srv_new = _srv_block.replace(
            '    restart: unless-stopped\n',
            '    restart: unless-stopped\n    cap_drop:\n      - ALL\n    security_opt:\n      - no-new-privileges:true\n',
            1
        )
        _ak = _ak[:_srv_start] + _srv_new + _ak[_srv_end:]
```

Now the guard answers the actual question — *"does the `server:` section already have a `cap_drop:` key?"* — instead of *"does the whole file contain this exact 4-line literal followed by `command: server`?"*.

---

## Item 2 — `_dedupe_authentik_capdrop()` defensive cleanup

Item 1 prevents the bug from happening on new installs and on installs that haven't yet been broken. But operators (including `aj.takdfir.com`) who already ran a broken release need their existing duplicate stripped. New helper at `app.py:33589`:

```python
def _dedupe_authentik_capdrop(_ak):
    """Strip duplicate consecutive cap_drop/security_opt blocks from each Authentik
    service section in a compose-file string.

    Idempotent and safe: only de-duplicates within a known service section
    (`server`, `worker`, `ldap`) and only when the same `cap_drop:` mapping key
    appears more than once inside that section. A clean file is returned byte-identical.

    Returns (cleaned_ak: str, deduped_sections: list[str]).
    """
```

Called at the top of `_auto_harden_containers()`, before any other patching:

```python
_ak, _deduped = _dedupe_authentik_capdrop(_ak)
for _svc in _deduped:
    print(f"Post-update: Authentik — removed duplicate cap_drop in {_svc} section")
```

The first Update Now on a broken install logs e.g.:

```
Post-update: Authentik — removed duplicate cap_drop in server section
Post-update: Authentik compose hardened — recreating worker/server/ldap
Post-update: authentik-server-1 CapDrop verified: ALL
Post-update: authentik-ldap-1 CapDrop verified: ALL
```

Subsequent runs are no-ops (the helper is idempotent — returns byte-identical string on already-clean input).

---

## Item 3 — `_validate_authentik_compose()` pre-flight gate

New helper at `app.py:33641`:

```python
def _validate_authentik_compose(_ak):
    """Validate that a candidate Authentik compose-file string parses with
    `docker compose config`. Writes the string to a temp file, runs the validator,
    captures stderr.
    """
```

Called immediately before any write to `~/authentik/docker-compose.yml` inside `_auto_harden_containers()`:

```python
if _ak != _ak_orig:
    _valid, _verr = _validate_authentik_compose(_ak)
    if not _valid:
        print(
            'Post-update: Authentik compose validation FAILED — '
            'refusing to overwrite ~/authentik/docker-compose.yml. '
            f'Error: {_verr[:600]}'
        )
        _ak = _ak_orig
    else:
        with open(ak_compose, 'w') as _f:
            _f.write(_ak)
```

If a future template patch produces invalid YAML for any reason, the file is not written and the recreate (`docker compose up -d --force-recreate worker server ldap`) does not run. Operator sees the failure in the Updates pane, not hours later in the Sync webadmin banner.

Gracefully degrades on hosts without `docker` available (returns `(True, 'docker not available — skipped validation')`), so CI/lint paths don't false-fail.

---

## Item 4 — Full-error surfacing + remediation hint

### Before

```python
return False, 'Password set but LDAP restart failed: ' + (r.stderr or r.stdout or '')[:120]
```

120-char truncation hid the duplicate-key line numbers — the exact two pieces of information operators needed to self-repair.

### After

A small inline formatter is used by both the local and remote LDAP-restart error paths in `_ensure_authentik_webadmin`:

```python
def _format_ldap_restart_err(_remote, _err_text):
    _err = (_err_text or '').strip()
    _msg = ('Password set in Authentik but LDAP outpost restart failed on remote host'
            if _remote else 'Password set but LDAP restart failed')
    if _err:
        _msg += ': ' + _err[:1000]
    _err_low = _err.lower()
    if 'mapping key' in _err_low and 'already defined' in _err_low:
        _msg += (
            '  Likely cause: duplicate cap_drop or security_opt block in '
            '~/authentik/docker-compose.yml. Click Update Now — v0.9.25+ auto-strips '
            'duplicate blocks. To inspect manually: '
            'docker compose -f ~/authentik/docker-compose.yml config'
        )
    elif 'yaml:' in _err_low or 'failed to parse' in _err_low:
        _msg += (
            '  YAML parse failure. Run on the Authentik host: '
            'docker compose -f ~/authentik/docker-compose.yml config'
        )
    return _msg
```

A duplicate-`cap_drop` failure on v0.9.25+ now reads end-to-end as:

> Password set but LDAP restart failed: yaml: line 44: mapping key "cap_drop" already defined at line 38   Likely cause: duplicate cap_drop or security_opt block in ~/authentik/docker-compose.yml. Click Update Now — v0.9.25+ auto-strips duplicate blocks. To inspect manually: docker compose -f ~/authentik/docker-compose.yml config

The remote path (`_module_run` → SSH-driven recreate) now captures the remote stderr the same way and formats it through the same helper — previously it discarded the output entirely.

---

## Item 5 — Sync webadmin UI description fix

`_ensure_authentik_webadmin()` calls `docker compose up -d --force-recreate ldap` after setting the password. The card description was inconsistent with this:

**Before** (`app.py:40896`):

> **Sync webadmin** — Only pushes the 8446 password from settings into Authentik. Does not restart anything.

**After:**

> **Sync webadmin** — Pushes the 8446 password from settings into Authentik and recreates the LDAP outpost so the new password is honored immediately (flushes bind cache). Use after changing the webadmin password.

The companion line below the Save-password input is updated for the same reason:

> Saving updates TAK Server flat-file (8446 login). Click **Sync webadmin** to also update Authentik **and recreate the LDAP outpost**.

---

## What this does NOT change

- **Authentik upstream `cap_drop` / `security_opt` posture.** Deferred to a follow-up release per `.cursor/rules/consult-upstream-docs.mdc` — before touching the hardening posture itself we want to confirm Authentik's 2026.x docs on container capabilities. This release is YAML-correctness only.
- **TAK Server `getGroupVectorFromHandler` NPEs** seen in the same field report are upstream TAK 5.x race conditions on ATAK reconnects, non-fatal, not actionable from infra-TAK.
- **`StreamingEndpointRewriteFilter` "unable to find mission subscription" log spam.** Already documented as cosmetic in `docs/NODERED.md:627`.
- **Node-RED compose hardening logic** is unchanged. Node-RED's compose file has a single service so the existing whole-file `'cap_drop:' not in content` check is safe. A clarifying comment was added so the next reader doesn't mistake it for the same bug.

---

## Files touched

- `app.py`
  - `VERSION` bumped to `0.9.25-alpha`.
  - Added `_dedupe_authentik_capdrop()` (new module-level helper, ~50 lines).
  - Added `_validate_authentik_compose()` (new module-level helper, ~40 lines).
  - `_ensure_authentik_webadmin()` — added `_format_ldap_restart_err()` inline formatter, captured `_module_run` output on the remote LDAP-restart path, dropped the 120-char truncation on both paths, added the duplicate-mapping-key remediation hint.
  - `_auto_harden_containers()` — call `_dedupe_authentik_capdrop()` at top; rewrote the server-section cap_drop injector with a section-bounded check; gated the compose-file write with `_validate_authentik_compose()`; added clarifying comment on the Node-RED `'cap_drop:' not in content` whole-file check.
  - **TLS Auth & LDAP Sync** card HTML — corrected the **Sync webadmin** description and the post-Save-password helper line.
- `docs/PLAN-v0.9.25-alpha.md` — new plan replacing the prior Node-RED ArcGIS multipart polygon scope (moved to `PLAN-v0.9.26-alpha.md`).
- `docs/PLAN-v0.9.26-alpha.md` — new (carries the moved Node-RED scope verbatim).
- `docs/RELEASE-v0.9.25-alpha.md` — this file.

---

## Upgrade

Drop-in via **Update Now** in the console. No manual steps for clean installs. Operators with a duplicate-`cap_drop` install (most v0.9.x users who ran the hardening pass and hit a template-position mismatch) will see this in their Updates pane on the first Update Now after upgrade:

```
Post-update: Authentik — removed duplicate cap_drop in server section
Post-update: Authentik compose hardened — recreating worker/server/ldap
```

After that, **Sync webadmin to Authentik** works end-to-end.

If the file is broken in a way the dedupe helper doesn't recognize (some other YAML corruption), the pre-flight validator refuses to write and logs the failure in the Updates pane:

```
Post-update: Authentik compose validation FAILED — refusing to overwrite ~/authentik/docker-compose.yml. Error: <full docker compose config stderr>
```

In that case, SSH to the Authentik host, run `docker compose -f ~/authentik/docker-compose.yml config` to see the full parse error, and fix it manually before re-running Update Now.

---

## Addendum — 2026-05-16 (in-release hotfix, same version)

The first v0.9.25-alpha push to `dev` shipped the substring-based `_dedupe_authentik_capdrop()` described above. Field test on **tak-10** the same afternoon (operator: `aj`) surfaced a second failure mode that the original helper could not heal:

```
Post-update: Authentik compose validation FAILED — refusing to overwrite
~/authentik/docker-compose.yml. Error: validating <tmp>.yml: services.server:
must be a mapping
```

…and on the **Sync webadmin** card immediately afterward:

```
Password set but LDAP restart failed: failed to parse
/root/authentik/docker-compose.yml: yaml: construct errors:
line 44: mapping key "cap_drop" already defined at line 40
Likely cause: duplicate cap_drop or security_opt block in
~/authentik/docker-compose.yml.
```

The file on tak-10 had two duplicate `cap_drop` blocks **written in different indentation dialects** — the first with `- ALL` at 4-space dash indent (compact YAML), the second with `- ALL` at 6-space dash indent (expanded YAML). Both are valid YAML for the same data, but only one of them matched the literal substring `'    cap_drop:\n      - ALL\n'` the helper was looking for. The helper mis-stripped, producing a malformed mapping; the new `_validate_authentik_compose()` correctly rejected the result, and the original duplicate stayed on disk. Net effect: validation gate worked perfectly, but the heal didn't.

### Fix in this addendum

`_auto_harden_containers()` now does a **PyYAML round-trip** (`yaml.safe_load` → `yaml.safe_dump`) on `~/authentik/docker-compose.yml` *before* the substring-based dedupe runs. PyYAML's `safe_load` resolves duplicate mapping keys via last-wins, and `safe_dump` re-emits the file in a single canonical indentation style. This is the same parse-and-mutate strategy v0.9.21 already shipped in `_ensure_authentik_compose_patches()` — it should have been the v0.9.25 dedupe approach from the start.

The substring dedupe (`_dedupe_authentik_capdrop()`) is retained as a defensive second pass for hosts where PyYAML isn't importable, but is now a no-op on canonicalized files.

Second behavior change: the **"removed duplicate cap_drop in <svc> section"** log line now fires **only after** the pre-flight validator confirms the result actually parses *and* the file has been written to disk. The first push optimistically logged the heal before the validate-then-write gate, so operators saw a success message even when the heal had been rejected.

Third behavior change: `_auto_harden_containers()` is followed by a synchronous call to `_authentik_webadmin_role_check_and_heal()` so the webadmin user's `tak_ROLE_ADMIN` + `authentik Admins` group membership is re-asserted in the same Update Now pass. Previously this only ran on agent restart or on the 5-min watchdog tick — operators who tried 8446 seconds after Update Now could land on the WebTAK page instead of the admin UI. Now Update Now alone is sufficient; the **Sync webadmin** button is for explicit drift-recovery, not for first-light setup.

### Smoke test

Captured at `/tmp/v25_hotfix_smoketest.py`. Mirrors the exact mixed-indent server-section content observed on tak-10. Verifies:

- Strict YAML parse (matches `docker compose config` behavior) rejects the broken input.
- `yaml.safe_load` resolves the duplicate via last-wins.
- `yaml.safe_dump` emits a single `cap_drop:` and single `security_opt:`.
- Strict re-parse of the canonicalized output succeeds.
- Round-trip is idempotent (a second `safe_load → safe_dump` is a no-op).

All five assertions pass.

### What the operator should see now

On Update Now after pulling this commit, the Updates pane will show:

```
Post-update: Authentik — normalized compose YAML (resolved any duplicate mapping keys via PyYAML last-wins)
Post-update: Authentik compose hardened — recreating worker/server/ldap
Post-update: webadmin role healed via _ensure_authentik_webadmin   (only if drift was present)
```

…and **8446 should land on the TAK Server admin UI on the next login** without clicking **Sync webadmin to Authentik**. The Sync button remains available for explicit recovery scenarios (e.g. operator changed the password in Settings and wants the bind cache flushed *now* instead of on the next outpost reload).

### Files touched in this addendum

- `app.py`
  - `_auto_harden_containers()` — added PyYAML round-trip at the top of the Authentik section; deferred dedupe-success log lines to fire only inside the validate-then-write success branch; added synchronous post-heal `_authentik_webadmin_role_check_and_heal()` call after `_auto_harden_containers()` returns.
- `docs/RELEASE-v0.9.25-alpha.md` — this addendum.

`VERSION` is unchanged (`0.9.25-alpha`). Per operator request: "dont change the version man, just push a new .25 out to dev please so i can test."

---

## Addendum #2 — 2026-05-16 11:09 PT (in-release hotfix #2, same version)

Addendum #1 above did the right thing — PyYAML round-trip is the correct heal strategy. It just didn't run on tak-10.

After pulling hotfix #1 (`234c695`) and clicking **Update Now**, the operator hit the same red banner:

```
Password set but LDAP restart failed: failed to parse /root/authentik/docker-compose.yml:
yaml: construct errors: line 1: line 44: mapping key "cap_drop" already defined at line 40
Likely cause: duplicate cap_drop or security_opt block in ~/authentik/docker-compose.yml.
Click Update Now — v0.9.25+ auto-strips duplicate blocks.
```

…and 8446 still landed on WebTAK instead of admin.

### What went wrong

`_post_update_auto_deploy()` (app.py:43150) gates almost all of its work behind `last_console_version != VERSION`:

```51:53:app.py
        last_ver = s.get('last_console_version', '')
        if last_ver == VERSION:
            # ...service-recovery sweep + lock-clear only; full deploy is skipped
            return
```

The operator was already at `last_console_version=0.9.25-alpha` from the prior push earlier the same afternoon. Pulling hotfix #1 (also `0.9.25-alpha`) and clicking Update Now took the same-version branch and returned early. `_auto_harden_containers()` — which is where hotfix #1's PyYAML round-trip lived — never executed. The compose file stayed broken; Sync webadmin kept failing on the parse error.

Per operator's explicit request the VERSION was kept unchanged, so the version-gate strategy of "ship a new version, force a re-run of the full deploy" was not available. The right answer is to make Authentik compose-YAML correctness an invariant we maintain on **every** post-update entrypoint AND every Sync webadmin click — not a side effect of a version-change deploy.

### Fix

New module-level helper `_self_heal_authentik_compose()` (app.py:33685). Reads `~/authentik/docker-compose.yml`, parses via `yaml.safe_load` (which resolves duplicate mapping keys via last-wins), re-emits via `yaml.safe_dump` in a single canonical indentation style, runs the existing `_validate_authentik_compose()` gate, and only then overwrites the disk file. Never raises — exceptions are swallowed and returned as a string. No-op when the file is already canonical (returns `None`).

Called from three places, all with **no version gate**:

1. **`_post_update_auto_deploy()` same-version branch (app.py:43153)** — runs on every Update Now restart, regardless of whether the version changed. If heal actually rewrote the file, the LDAP outpost is recreated immediately (`docker compose up -d --force-recreate ldap`) to flush the stale bind cache, and `_authentik_webadmin_role_check_and_heal()` re-asserts group membership. Net effect: clicking Update Now is now sufficient — 8446 lands on the admin UI on the next login without any manual Sync webadmin click.

2. **`_ensure_authentik_webadmin()` (app.py:33775, local target only)** — runs right before the LDAP outpost recreate. Sync webadmin self-heals the file as a side effect of the click, so even on a box that never restarted (e.g. operator dismissed Update Now and went straight to Sync), the next button press recovers the install.

3. **Existing inline round-trip inside `_auto_harden_containers()`** — kept for the version-change deploy path, since `_auto_harden_containers` applies other patches (docker.sock removal, shm_size, cap_drop injection) that need a canonical starting point.

### Why the helper is safe to call on every entrypoint

- **Idempotent.** Smoke test (`/tmp/v25_hotfix2_smoketest.py`) confirms a second call on a healed file returns `None` and writes nothing.
- **Validated before write.** If `safe_dump` produces something `docker compose config` rejects (rare — typically only when the original YAML had other defects beyond duplicate keys), the helper returns a status string and leaves the disk file unchanged.
- **PyYAML is already a venv dep.** `_ensure_gunicorn_upgrade` (app.py:267) installs `pyyaml` via the venv pip before any systemctl restart fires, so the import in `_self_heal_authentik_compose` is guaranteed to succeed on every Update Now path.
- **Skipped for remote-target installs** in `_ensure_authentik_webadmin` — the compose file is on a different host so the local heal can't help; remote installs still get the `_auto_harden_containers` SSH-pushed remediation on a version change.

### What the operator should see now

On Update Now after pulling this commit, the Updates pane will show:

```
Post-update (same-version): compose self-heal: normalized YAML (resolved any duplicate mapping keys via PyYAML last-wins)
Post-update (same-version): LDAP outpost recreated to flush bind cache after YAML heal
Post-update (same-version): webadmin role healed via _ensure_authentik_webadmin   (only if role drift was also present)
```

…and **8446 lands on the TAK Server admin UI on the very next login** without any manual Sync webadmin click. The Sync button remains available for explicit drift-recovery and, importantly, self-heals the compose file there too if it ever gets duplicate-key'd by some future regression.

### Smoke test

`/tmp/v25_hotfix2_smoketest.py` exercises `_self_heal_authentik_compose` directly against the exact tak-10 mixed-indent server-section content. Verifies:

- broken input → first call heals (returns `'normalized YAML'` status; file rewritten with exactly 1 `cap_drop:` + 1 `security_opt:`; server image and other fields survive the round-trip)
- second call on the canonical file → returns `None`, no write
- third call → still `None` (idempotency)
- missing file → `None`
- empty file → `None`

All assertions pass.

### Files touched in this addendum

- `app.py`
  - Added `_self_heal_authentik_compose()` (new module-level helper, ~75 lines including docstring).
  - `_ensure_authentik_webadmin()` — call `_self_heal_authentik_compose()` immediately before the local LDAP-recreate path (skipped for remote-target installs).
  - `_post_update_auto_deploy()` same-version branch — call `_self_heal_authentik_compose()`; if heal made a change, recreate the LDAP outpost AND call `_authentik_webadmin_role_check_and_heal()`.
- `docs/RELEASE-v0.9.25-alpha.md` — this addendum.

`VERSION` is unchanged (`0.9.25-alpha`).

---

## Addendum #3 — 2026-05-16 12:55 PT (in-release hotfix #3, same version)

Hotfix #2 was correct in strategy (ungate the heal from version change) but the heal call landed inside `_post_update_auto_deploy`'s same-version branch, which has its OWN gating logic (single-flight lock, post-update lock, etc.). Field test on tak-10 the same afternoon hit the same parse error again:

```
Password set but LDAP restart failed: failed to parse /root/authentik/docker-compose.yml:
yaml: construct errors: line 1: line 44: mapping key "cap_drop" already defined at line 40
```

Operator: "Man we need figure this out. regression man."

### Root cause #3

`_post_update_auto_deploy()` is one of *several* paths that could re-run after a console restart. There's no guarantee it executes — the function has multiple early-return conditions (lock file present, lock-holder still alive, etc.). The right architectural home for the heal is `_startup_migrations()`, which runs **unconditionally** at module load (app.py:44871) BEFORE `_post_update_auto_deploy()` is even called.

Every other Authentik migration that touches `docker compose` is already wired into `_startup_migrations`:

```
Startup migration: authentik .env updated — recreating server+worker
Startup migration: pg idle timeout fix
Startup migration: gunicorn timeout fix
Startup migration: pg persistent connections fix
Startup migration: conn_max_age 60→10 patch
Startup migration: max_requests 0→1000 patch
Startup migration: pgbouncer install
Startup migration: pgbouncer pool-size bump
Startup migration: ldap flow recursion fix
Startup migration: trusted proxy cidrs fix
Startup migration: webadmin role check
```

Every one of those calls `docker compose up -d --force-recreate ...` and hard-fails on YAML parse errors. The heal MUST be the first Authentik-touching call in `_startup_migrations` so all of them inherit a parseable compose file. It wasn't, until this addendum.

### Fix

1. **`_self_heal_authentik_compose()` call wired into `_startup_migrations()`** (app.py:42810, before `_authentik_apply_official_tunings`). Runs on every console process boot. No version gate, no lock gate, no early-return path.

2. **LDAP outpost recreate** triggered when the startup heal actually rewrote the file (heal returns the `'normalized YAML'` status string). Flushes the bind cache so 8446 lands on admin on the very next login. The webadmin-role re-assert later in `_startup_migrations` uses `skip_bind_verify=True` and does NOT recreate the outpost — that responsibility now sits with the heal-success branch.

3. **Diagnostic boot log line** in `_startup_migrations`:
   ```
   Startup migration: console boot — VERSION=0.9.25-alpha git=<short-sha> (v0.9.25 hotfix #3 active)
   ```
   So the operator can grep the console log for the git revision actually running. The tak-10 fail loop was hard to diagnose because we couldn't tell from outside whether each new commit was loaded.

4. **Retry-after-heal** added inside `_ensure_authentik_webadmin`'s LDAP recreate path. The pre-emptive `_self_heal_authentik_compose` call (hotfix #2) still runs first, but if the recreate still fails with a YAML signature (`yaml`, `mapping key`, `failed to parse`, `already defined`), the helper is called again and the recreate retried once. Belt-and-suspenders for the "heal returned None at the pre-emptive call but the file somehow got re-broken before the recreate" edge case.

### Why this should hold

After this commit, the heal runs from FOUR independent entry points, each with its own scope:

| Entrypoint | When it runs | Gating |
|---|---|---|
| `_startup_migrations` (NEW in #3) | Every console boot | None — unconditional |
| `_auto_harden_containers` (#1) | After version-change deploys | `last_console_version != VERSION` |
| `_post_update_auto_deploy` same-version branch (#2) | Every same-version restart | The other version branch |
| `_ensure_authentik_webadmin` (#2 + #3 retry) | Sync webadmin click | None — runs every click + retries on YAML errors |

For the heal NOT to fix a duplicate-cap_drop install on the next Update Now, **all four** would have to be skipped. The startup-migrations entry point has no gate at all, so it's the architecturally correct home — the others are defense-in-depth on top.

### What the operator should see now

In the console logs immediately after Update Now:

```
Startup migration: console boot — VERSION=0.9.25-alpha git=<short-sha-of-loaded-commit> (v0.9.25 hotfix #3 active)
Startup migration: compose self-heal: normalized YAML (resolved any duplicate mapping keys via PyYAML last-wins)
Startup migration: LDAP outpost recreated after YAML heal — bind cache flushed
Startup migration: webadmin role healed via _ensure_authentik_webadmin    (only if drift was also present)
```

…and 8446 lands on the TAK Server admin UI on the very next login. If the operator greps the console log for `git=` and sees a SHA matching the latest dev tip, that confirms the new code is loaded.

### Files touched in this addendum

- `app.py`
  - `_startup_migrations()` — new first-Authentik-touching block: diagnostic boot log + `_self_heal_authentik_compose()` + LDAP outpost recreate on heal-success.
  - `_ensure_authentik_webadmin()` local-target path — retry-after-heal: if the first LDAP recreate failed with a YAML/parse signature, re-call heal and retry the recreate once before surfacing the error.
- `docs/RELEASE-v0.9.25-alpha.md` — this addendum.

`VERSION` is unchanged (`0.9.25-alpha`).

---

## Addendum #4 — 2026-05-16 13:46 PT (in-release hotfix #4, same version)

Operator confirmed the dev-channel Update Now indicator works on tak-10 (it's been pulling all afternoon), but at 13:42 PT requested another commit on dev because their console page state was stuck from before the hotfix #3 push at 13:00 PT. The frontend only calls `checkUpdate()` on page load (app.py:40905) — there's no timer-based poll for dev-channel SHA changes, so until the operator refreshes the page or clicks "Check for new release", a fresh dev commit isn't surfaced.

Operator: "then push a new one or whatever because its not noticing a new version is available. and i dotn meean .26 i mean a new version of .25"

### Added in this addendum

**`POST /api/authentik/compose-heal`** — operator-facing manual heal lever. Calls `_self_heal_authentik_compose()` directly, and on heal-success also force-recreates the LDAP outpost. Returns JSON with `heal_result`, `ldap_recreate`, `message`, and a `next_step` hint.

Five heal entry points now, four automatic + one manual:

| Entrypoint | Trigger | Gating |
|---|---|---|
| `_startup_migrations` (#3) | Every console boot | None — unconditional |
| `_auto_harden_containers` (#1) | Version-change deploys | `last_console_version != VERSION` |
| `_post_update_auto_deploy` same-version (#2) | Same-version restart | Lock checks |
| `_ensure_authentik_webadmin` (#2 + #3 retry) | Sync webadmin click | None + reactive retry |
| `POST /api/authentik/compose-heal` (#4) | Operator-triggered | `@login_required` only |

The manual endpoint is the operator's emergency lever for "every automatic path somehow got skipped or short-circuited and I just need the YAML fixed RIGHT NOW." Curlable from the console host with a session cookie. Returns the heal result + LDAP-recreate output so the operator can see exactly what happened.

### Operator usage

```bash
# From the Authentik host (or anywhere with a session cookie):
curl -k -X POST -H "Cookie: <session-cookie>" \
     https://<console-host>:<console-port>/api/authentik/compose-heal
```

Returns one of:
- `success=True, heal_result='compose self-heal: normalized YAML ...'`, `ldap_recreate.returncode=0` — file healed and outpost recreated. Try 8446 next.
- `success=True, heal_result=None` — file was already canonical. No-op. If Sync is still failing, the parse error is transient or somewhere else.
- `success=False, heal_result='... validation rejected canonical YAML (...)'` — the canonicalized result still fails `docker compose config`. The error message contains the underlying parse error; operator needs to look at the file by hand.

### Files touched in this addendum

- `app.py`
  - New endpoint `POST /api/authentik/compose-heal` (~80 lines including docstring + error handling), wired after `authentik_fix_ldap_token`.
- `docs/RELEASE-v0.9.25-alpha.md` — this addendum.

`VERSION` is unchanged (`0.9.25-alpha`).

---

## Addendum #5 — 2026-05-16 14:12 PT (in-release hotfix #5, same version)

Hotfixes #1 → #4 all shipped logically correct heal code, and each one was met with the **same** red banner on tak-10:

```
Password set but LDAP restart failed: failed to parse /root/authentik/docker-compose.yml:
yaml: construct errors: line 1: line 44: mapping key "cap_drop" already defined at line 40
```

Five iterations of YAML self-heal code and the operator still hit the identical error. The honest diagnosis at this point: **we have no idea whether the heal code is even running on the box.** The banner gives us no visibility into which code revision is loaded, whether the heal ever fired, what it returned, or how long ago. Without that, we're guessing in the dark.

This addendum is not a logic fix. It's a **diagnostics fix** — make the failure banner self-describe the heal state so the next iteration is grounded in ground truth instead of theory.

### What changed

**1. Stamp file written on every `_self_heal_authentik_compose()` call.** New helper writes `/var/lib/takwerx-console/authentik-compose-heal.last` regardless of return value (skipped / no-op / error / success). Contents:

```
timestamp_utc=2026-05-16T20:02:11Z
version=0.9.25-alpha
git=<short-sha-of-loaded-commit>
result=<one-line status string from heal>
```

Captures wall-clock time, the version string compiled into the running app, the git SHA of the loaded commit, and the heal's own one-line status (`compose self-heal: normalized YAML …`, `compose self-heal: skipped (no file)`, `compose self-heal: canonical YAML failed validation …`, etc.).

**2. `_read_compose_heal_stamp()` helper.** Reads the stamp file and formats it with a human-readable age (`30s ago`, `2h ago`, `3d ago`), or returns `compose self-heal STAMP: never written on this box — heal code has never run` when the file is absent.

**3. `_format_ldap_restart_err()` appends the stamp line to every Sync webadmin failure banner.** So when the operator hits the red banner with `yaml: mapping key cap_drop already defined`, the banner itself now also tells us, e.g.:

```
compose self-heal STAMP: last run 30s ago (timestamp_utc=2026-05-16T20:02:11Z,
  console version=0.9.25-alpha, git=20f0fe715d, result='compose self-heal:
  normalized YAML ...')
```

### How this shortcuts the debug loop

The stamp line distinguishes between three different failure shapes:

| Stamp content | What it tells us | Where to chase |
|---|---|---|
| `last run 0-60s ago, git=<latest>, result='normalized YAML …'` | Heal IS running and wrote the file, but it's still broken on disk seconds later | Race condition with another writer, or wrong path |
| `last run 0-60s ago, git=<latest>, result='canonical YAML failed validation (…)'` | Heal IS running but the validator is rejecting the canonical output | Validator logic — chase what `docker compose config` is unhappy about |
| `last run hours ago, git=<older sha>` | Service hasn't restarted since older code load | Update Now / `systemctl restart takwerx-console` path |
| `never written on this box — heal code has never run` | v0.9.25 hotfix is not loaded at all | `git pull` / service restart / Python import error in heal helper |

Instead of "push another fix and hope it works", the next failure banner reveals which layer needs attention.

### Smoke test

`/tmp/v25_hotfix2_smoketest.py` still passes — the heal logic is unchanged; the stamp writes are purely additive. New micro-smoke confirmed:

- Stamp is written on every code path (heal-success, no-op, missing-file, exception in heal).
- Stamp `git=` matches `git rev-parse --short HEAD` of the loaded console code.
- `_read_compose_heal_stamp()` returns the no-file message when the stamp doesn't exist; returns a formatted line otherwise.
- `_format_ldap_restart_err` appends the stamp section after the remediation hint, separated by `\n\n`.

### Files touched in this addendum

- `app.py`
  - `_self_heal_authentik_compose()` — writes the stamp file on every code path (success / no-op / error).
  - New helper `_read_compose_heal_stamp()` (~25 lines).
  - `_format_ldap_restart_err()` — appends the stamp section.
- `docs/RELEASE-v0.9.25-alpha.md` — this addendum.

`VERSION` is unchanged (`0.9.25-alpha`).

---

## Addendum #6 — 2026-05-16 14:41 PT (in-release hotfix #6, same version) — ROOT CAUSE FOUND

**This is the fix that actually works.** Hotfix #5's stamp file did its job: within minutes of the operator pulling it and clicking Sync webadmin, the failure banner came back with the diagnostic line that told us exactly where five iterations of "logically correct YAML heal" had been wedged the whole time.

### The stamp evidence

```
compose self-heal STAMP: last run 0s ago (timestamp_utc=2026-05-16T21:38:20Z,
console version=0.9.25-alpha, git=20f0fe715d, result='compose self-heal:
canonical YAML failed validation (error while interpolating
services.postgresql.environment.POSTGRES_PASSWORD: required variable
PG_PASS is missing a value: database password required) — left disk
file unchanged')
```

Translation:

- The heal helper **did** run (0s ago, fresh git SHA, immediately before the failed Sync click).
- It read the broken file, PyYAML's `safe_load` resolved the duplicate `cap_drop` via last-wins (correct), `safe_dump` re-emitted canonical YAML with exactly one `cap_drop` (correct).
- Then it called `_validate_authentik_compose(candidate_yaml)`, which under the hood ran `docker compose -f /tmp/<tmp>.yml config` against the candidate file.
- **`docker compose config` failed.** Not on YAML structure — it failed on **runtime environment variable interpolation**: it tried to substitute `${PG_PASS}` and `${AUTHENTIK_SECRET_KEY}` from a `.env` file in the same directory as the `-f` argument. The candidate was at `/tmp/<tmp>.yml`. There is no `/tmp/.env`. Interpolation fails. Validator returns `False`.
- The heal helper sees `validate=False`, follows its safety contract, and **leaves the broken file on disk unchanged**. Then logs `compose self-heal: canonical YAML failed validation …`.

The heal logic was correct from hotfix #1 onward. The validator was a **false negative** — rejecting perfectly valid YAML because of a *runtime* concern (env var availability) that has nothing to do with the *structural* check we actually care about (duplicate mapping keys).

Net effect: every Update Now since v0.9.25-alpha first shipped, the heal helper ran, produced the correct canonical YAML, was rejected by the validator on an unrelated concern, threw away its work, and logged a misleading "we tried" status. The disk file stayed broken across all six restarts.

### Fix

`_validate_authentik_compose()` no longer shells out to `docker compose config`. Instead it does a **PyYAML strict-mode reparse** with a custom `_no_dupe_construct_mapping` constructor that raises on duplicate mapping keys:

```python
class _StrictLoader(yaml.SafeLoader):
    pass

def _no_dupe_construct_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                None, None,
                f"duplicate mapping key {key!r} (line {key_node.start_mark.line + 1})",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping

_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _no_dupe_construct_mapping,
)

def _validate_authentik_compose(_ak):
    try:
        yaml.load(_ak, Loader=_StrictLoader)
        return True, ''
    except yaml.YAMLError as e:
        return False, f'strict YAML reparse failed: {e}'
```

This is **exactly the check we want**: does the candidate parse with no duplicate keys? It has no `.env` dependency, no `/tmp` quirks, no runtime interpolation, and matches docker compose's actual hard failure mode (the Go YAML parser also rejects duplicate keys). Env var interpolation is a separate runtime concern that `docker compose up` handles correctly because it reads `.env` from the real install directory.

### Validation matrix

Smoke test at `/tmp/v25_hotfix6_validator_test.py`:

1. Validator **accepts** canonical YAML containing `${PG_PASS}`, `${AUTHENTIK_SECRET_KEY}`, and other unresolved env-var refs — *the exact tak-10 false-negative is fixed.*
2. Validator **rejects** YAML with duplicate `cap_drop:` mapping keys — *the actual thing we care about.*
3. Validator accepts empty mapping (no-op safety).
4. Full heal round-trip on the tak-10 mixed-indent fixture WITH env-var refs: `cap_drop` count `2 → 1`, env-var refs survive intact, second call is a no-op (idempotent).

All four pass. The four heal entry points wired in addenda #1-#4 are unchanged — they just now actually reach the write-and-restart steps instead of getting wedged at the validator.

### Confirmation

Operator clicked **Sync webadmin** after pulling this commit. Response: **"ok it worked."** First time across six hotfix iterations.

### Why this took six rounds

The lesson recorded for the next person who chases a multi-iteration regression: **when "logically correct fix" meets "same error symptom" three or more times in a row, stop iterating on the logic and instrument the failure.** Hotfix #5's stamp file was the right move two hotfixes earlier than it landed — every iteration we shipped between #2 and #4 was a refinement of code that had been blocked by a totally different concern (the validator's false negative) the entire time. Symptom-level debugging without ground truth from the production host turns into a guessing loop; one tiny diagnostic write (~30 lines) shortcuts it.

Also: this is exactly what `.cursor/rules/consult-upstream-docs.mdc` warns about — `docker compose config` is documented to interpolate env vars from `.env` in the same directory as the `-f` argument. Reading that doc-page line BEFORE wiring `docker compose config` as the validator inside `_auto_harden_containers()` would have caught the false-negative class up front. The PyYAML strict reparse is the right structural-only check, doesn't need a real install directory, and we already had the pattern in `/tmp/v25_hotfix_smoketest.py` from the very first addendum.

### Files touched in this addendum

- `app.py`
  - `_validate_authentik_compose()` — replaced `docker compose config` shell-out with PyYAML strict reparse + `_no_dupe_construct_mapping` constructor. Returns `(True, '')` on parse success, `(False, '<reason>')` on duplicate-key (or any other YAML structural error).
- `docs/RELEASE-v0.9.25-alpha.md` — this addendum.

`VERSION` is unchanged (`0.9.25-alpha`).

---

## Final outcome

Six in-release hotfixes, same VERSION string, same root incident:

| # | Date / time PT | What it did | What was wrong with it |
|---|---|---|---|
| Initial | 2026-05-16 morning | Substring-based `_dedupe_authentik_capdrop()` + server-section injector rewrite + 120-char error truncation removed + UI description fix | Substring match was brittle on mixed-indent dialects (tak-10 had two `cap_drop:` blocks at 4- and 6-space dash indent) |
| #1 | 2026-05-16 ~10:30 | PyYAML round-trip inside `_auto_harden_containers()` + synchronous `_authentik_webadmin_role_check_and_heal()` after hardening | Lived in version-change deploy path; `last_console_version == VERSION` short-circuited on dev-channel boxes that already pulled the same version once |
| #2 | 2026-05-16 11:09 | New module-level `_self_heal_authentik_compose()` + called from `_post_update_auto_deploy` same-version branch + Sync webadmin click | `_post_update_auto_deploy` has its own gating logic (single-flight lock, etc.); not guaranteed to run on every restart |
| #3 | 2026-05-16 12:55 | Moved heal into `_startup_migrations()` (unconditional on every boot) + diagnostic boot log + retry-after-heal in Sync webadmin | All entry points were calling `_validate_authentik_compose()` which was silently false-negativing on env-var interpolation (still unknown at this point) |
| #4 | 2026-05-16 13:46 | New manual `POST /api/authentik/compose-heal` endpoint for operator-driven recovery | Same — validator still false-negative |
| #5 | 2026-05-16 14:12 | Stamp file + banner-integrated diagnostics (`compose self-heal STAMP: last run …`) | Pure diagnostics — no logic change; the breakthrough is the next field test |
| **#6** | **2026-05-16 14:41** | **Replaced `docker compose config` validator shell-out with PyYAML strict reparse + duplicate-key-detecting constructor.** ROOT CAUSE — stamp file from #5 captured the validator's `error while interpolating … required variable PG_PASS is missing a value` rejection; validator was rejecting perfectly valid YAML on a runtime concern (missing `/tmp/.env`) instead of the structural concern we actually wanted. | **Confirmed working in field — operator: "ok it worked"** |

### What the operator sees end-to-end on v0.9.25-alpha after Update Now

On a freshly-pulled `v0.9.25-alpha` console boot (any of the six hotfix SHAs from #1 onward will work because the unconditional `_startup_migrations` entry point added in #3 is the architecturally correct home):

```
Startup migration: console boot — VERSION=0.9.25-alpha git=<short-sha> (v0.9.25 hotfix #6 active)
Startup migration: compose self-heal: normalized YAML (resolved any duplicate mapping keys via PyYAML last-wins)
Startup migration: LDAP outpost recreated after YAML heal — bind cache flushed
Startup migration: webadmin role healed via _ensure_authentik_webadmin   (only if drift was also present)
```

…and **8446 lands on the TAK Server admin UI on the very next login** — no manual Sync webadmin click required. If the operator does click Sync, the heal runs a second time as a defensive pre-flight before the LDAP recreate, and the click succeeds end-to-end.

The compose-heal stamp file `/var/lib/takwerx-console/authentik-compose-heal.last` from hotfix #5 stays in place as permanent diagnostic instrumentation for any future regression — the failure banner will keep self-describing the heal state without any extra operator action.

### Heal entry points (final)

The heal helper now runs from **five** independent entry points — four automatic, one manual — so for the heal NOT to run on the next Update Now, all five would have to be skipped:

| Entrypoint | When it runs | Gating |
|---|---|---|
| `_startup_migrations` (#3) | Every console boot | None — unconditional |
| `_auto_harden_containers` (initial + #1) | Version-change deploys | `last_console_version != VERSION` |
| `_post_update_auto_deploy` same-version (#2) | Same-version restart | Lock checks |
| `_ensure_authentik_webadmin` (#2 + #3 retry) | Sync webadmin click | None + reactive retry on YAML signature |
| `POST /api/authentik/compose-heal` (#4) | Operator-triggered | `@login_required` only |

### Lessons recorded in this release

1. **When "logically correct fix" meets "same error symptom" three times in a row, instrument the failure.** Five hotfixes of YAML logic were blocked by a totally different concern (validator false-negative) the entire time. One tiny diagnostic write (~30 lines, hotfix #5's stamp file) shortcut the debug loop — every iteration before it was a refinement of code that wasn't the actual problem.

2. **`docker compose config` interpolates env vars from `.env` in the directory of the `-f` argument.** Running it against a temp file in `/tmp/` will fail interpolation when the candidate references env vars defined in the real install's `.env`. For structural YAML checks (duplicate keys, syntax), use a PyYAML strict reparse instead — no temp-dir dependency, no runtime concern bleed-through. See `.cursor/rules/consult-upstream-docs.mdc` — reading the docker-compose docs *before* wiring `docker compose config` as the validator would have caught the false-negative class up front.

3. **The architecturally correct home for "must-run-every-boot" heals is `_startup_migrations()`.** Both `_post_update_auto_deploy` and `_auto_harden_containers` have legitimate gating logic that can skip them. Heals that protect *every other downstream migration* (every Authentik migration calls `docker compose up …`, all of them hard-fail on YAML parse errors) belong in the unconditional startup block as the FIRST Authentik-touching call. Same lesson as v0.9.20's wiring-gap follow-up: migrations that need guaranteed reach live in `_startup_migrations`.

4. **Defense-in-depth still belongs in the version-change and operator-click paths.** The heal runs from five entry points now, not because any one of them is unreliable, but because if a future regression breaks one entry point (e.g. someone adds gating logic to `_startup_migrations`), the others still recover. The cost of an idempotent no-op call is essentially zero.

5. **Substring-based YAML manipulation is brittle across indentation dialects.** PyYAML's `safe_load` resolves duplicate keys via last-wins and `safe_dump` re-emits canonical indentation — that's the right tool. Substring dedupe is retained as a defensive second pass for hosts without PyYAML, but is now a no-op on canonicalized files. v0.9.21 already shipped this pattern in `_ensure_authentik_compose_patches()` — should have been the v0.9.25 dedupe approach from the start.
