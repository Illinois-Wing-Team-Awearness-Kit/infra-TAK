# v0.9.32-alpha — Hotfix: dashboard JS broken in v0.9.31 + "Reboot now" banner loop after kernel patch

**Date:** 2026-05-19
**Type:** Hotfix release — drop-in update via Update Now.
**Status:** RELEASED 2026-05-19 to `main`. Field-validated on test6 / test8 / test12 — all three boxes pulled `9154c55` from `dev`, restarted console, confirmed `_kernel_patch_unit_state` returns the new 5-tuple, `_kernel_patch_job_state` returns `done=False / error=False / running=False` from the `LoadState=not-found` short-circuit (Layer 1), and the dashboard banner clears post-reboot. Operator click-through (full Patch-now → Reboot-now → confirm banner stays clear) deferred to natural next kernel-upgrade cycle since no kernel update was pending on any test box at validation time.

---

## TL;DR

Two bugs, both surfaced during v0.9.32 field testing on test6 / test8 / test12:

**Bug 1 — Console dashboard `<script>` block aborted at parse time on v0.9.31.** Three escape-sequence mismatches inside `CONSOLE_TEMPLATE = '''…'''` (a Python triple-single-quoted string) caused the new "Patch now" JavaScript to render with raw newlines and a stray apostrophe inside a `'…'` JS string literal. Browser threw `Uncaught SyntaxError` on the first offending line, aborted the entire script block, and every `onclick` on the Console page silently threw `Uncaught ReferenceError`. Operator-reported case was **"What's using CPU/RAM?"** but the resource-breakdown button, the kernel-patch buttons themselves, the host-card unattended-upgrades toggles, and every other dashboard handler were all broken the same way.

**Bug 2 — `[Reboot now]` banner re-fires forever after every reboot.** After operator clicks "Patch now" → apt-get full-upgrade completes → operator clicks "Reboot now" → box reboots → comes back up → banner is **back in the "Kernel patch complete — Reboot now required"** state. Cause: the transient systemd unit `infratak-kernel-patch.service` is gone after the reboot (transient units do not survive reboot). But `systemctl show -p Result` on a non-existent unit returns `Result=success` as the **default property value** — not as a real outcome. `_kernel_patch_job_state` was reading that default as "the patch job completed successfully" and returning `done=true` to the dashboard JS forever. Click Reboot, reboot, banner returns, infinite loop. Field-reproduced on all three dev boxes 2026-05-19 right after pulling v0.9.32-alpha.

---

## Bug 1: Dashboard `<script>` aborted at parse time

v0.9.31's new **"Patch now"** kernel-upgrade JavaScript (commit `eacdafd`, Bug 6) shipped with two escape-sequence bugs that aborted the entire `<script>` block at parse time on the **Console (home) page**. Symptom from the operator's browser DevTools:

```
console:602  Uncaught SyntaxError: Invalid or unexpected token
console:215  Uncaught ReferenceError: toggleResourceBreakdown is not defined
              at HTMLButtonElement.onclick (console:215:327)
```

Since the syntax error fires before any function in the dashboard `<script>` block gets defined, **every onclick on the Console page** is broken in v0.9.31 — not just **"What's using CPU/RAM?"**. The kernel-patch button itself, the resource-breakdown button, and any other dashboard-level JS (toggle handlers, refresh handlers, etc. defined in that same block) all throw `ReferenceError` because nothing got defined.

v0.9.32 fixes the two escape-sequence bugs. No other behavior change.

---

## Root cause

The Console page template (`CONSOLE_TEMPLATE = '''…'''` starting at `app.py:44202`) is a Python triple-single-quoted string. Inside that quoting context, Python parses backslash escapes **before** the bytes ever reach the browser:

- `\n` → literal newline (`0x0A`)
- `\'` → literal apostrophe (`'`)

The new kernel-patch JS in v0.9.31 wrote three confirm/log strings that assumed those escapes were JS-level, not Python-level. After Python's pass, the rendered JS the browser received looked like:

```js
if(!confirm('Start kernel patch?
[real newline]
[real newline]
… that's a separate explicit click.
[real newline]
Continue?'))return;
```

Two distinct JS syntax errors in one string:

1. **Multi-line single-quoted string literal.** JavaScript requires `\n` (backslash + `n`) inside a `'…'` string — a raw newline is a syntax error.
2. **Unescaped apostrophe.** The `'` in `that's` terminates the JS string literal early; everything after it (`s a separate explicit click. … Continue?'`) is parsed as broken JS expression syntax.

Same trap on the **Reboot now** confirm and the `[browser] reboot requested...` log-append line — same one-line cause.

When the browser hits the first `SyntaxError` while parsing the inline `<script>`, it aborts the entire script. Every function below the error never gets defined. `toggleResourceBreakdown`, `refreshModuleCards`, `loadTakCertExpiry`, the kernel-patch state machine itself, all the dashboard polling handlers — all of them undefined. Then every onclick on the Console page throws `ReferenceError`.

## What v0.9.32 changes

Three minimal one-line edits to `app.py` inside `CONSOLE_TEMPLATE`:

| File:Line | Before (broken) | After (fixed) |
|---|---|---|
| `app.py:44619` (startKernelPatch confirm) | `'Start kernel patch?\n\n… that\'s …'` | `"Start kernel patch?\\n\\n… that\\u2019s …"` |
| `app.py:44679` (rebootForKernelPatch confirm) | `'Reboot now?\n\n…'` | `'Reboot now?\\n\\n…'` |
| `app.py:44681` (kpatch-log-done append) | `'\n[browser] reboot requested...'` | `'\\n[browser] reboot requested...'` |

Two patterns at play:

- **`\n` → `\\n`.** Python's `\\n` is two characters: a literal backslash followed by `n`. After Python parses the template, the rendered HTML contains `\n` (two chars), which JavaScript correctly parses as a newline escape sequence inside a string literal. (The previous `\n` was parsed by Python as a real newline, then sent to the browser as a real newline, then choked the JS parser.)
- **Apostrophe in `that's` → `\\u2019` (typographic apostrophe).** This eliminates the close-quote conflict regardless of which outer quote style the JS string uses, and removes the chance of a future quoting-context drift re-introducing the bug. The kernel-patch confirm also switches its outer JS quotes from `'…'` to `"…"` as defense in depth.

That's it. **No other changes to the codebase.** All v0.9.31 fixes ship as-is.

## Net behavior

After Update Now to v0.9.32:

1. **Console page loads.** No `SyntaxError` in DevTools console. The dashboard `<script>` block fully evaluates.
2. **"What's using CPU/RAM?" button** opens the resource-breakdown panel (process list, CPU/RAM-top tables) — restored.
3. **All other dashboard onclick handlers work** — module-status refresh, host-card toggles, update-channel switcher, "Check for new release," etc.
4. **Kernel-patch banner** — "Patch now" / "Reboot now" buttons now actually fire their confirm dialogs and call their endpoints. (The endpoints themselves were always healthy in v0.9.31 — the bug only blocked the JS click handlers from reaching them.)

## Verification before commit

The rendered `<script>` block was extracted from the parsed `CONSOLE_TEMPLATE` and validated with `node --check`:

```
$ node --check /tmp/console_script_0.js
$ echo $?
0
```

No syntax errors. All functions definable. Same procedure repeatable from the repo root:

```python
import re, ast
src = open('app.py').read()
tree = ast.parse(src)
for node in ast.walk(tree):
    if isinstance(node, ast.Assign):
        for tgt in node.targets:
            if isinstance(tgt, ast.Name) and tgt.id == 'CONSOLE_TEMPLATE':
                val = eval(ast.unparse(node.value), {'BASE_CSS': ''})
                scripts = re.findall(r'<script[^>]*>(.*?)</script>', val, re.DOTALL)
                open('/tmp/console_script_0.js', 'w').write(scripts[0])
# then: node --check /tmp/console_script_0.js
```

This validator was used to confirm the fix, and is worth adding as a startup-migration smoke test in a future release so a repeat of this class of bug surfaces at console-restart time, not "next time someone clicks a button."

---

## Bug 2: "Reboot now" banner re-fires forever after every reboot

### Symptom (field-reproduced 2026-05-19 on test6 / test8 / test12)

Operator flow on each box:

1. Pulled v0.9.32-alpha (commit `46350be`), restarted `takwerx-console.service`. Dashboard loaded clean (Bug 1 fix verified — no `SyntaxError`, "What's using CPU/RAM?" worked, kernel-update banner rendered correctly).
2. Clicked **"Patch now"**. The detached transient systemd unit ran `apt-get update && apt-get full-upgrade` cleanly. Banner switched through `running` → `done` states. `[Reboot now]` button appeared.
3. Clicked **"Reboot now"**. Confirm dialog rendered cleanly (no Bug 1 syntax breakage). Box rebooted.
4. Box came back up. New kernel running. `apt list --upgradable | grep linux-image | wc -l` returns 0.
5. **Reloaded the dashboard. Banner is back, still showing "Kernel patch complete — reboot required to boot the new kernel" with `[Reboot now]` button.** Operator clicks Reboot → box reboots → comes back → banner is back. Infinite loop.

### Root cause

The dashboard JS in `checkKernelPatch()` (`app.py:44574–44610`) checks `/api/system/kernel-patch/job-status` **first** and only falls through to the apt-list "is a kernel update pending?" probe if job-status reports `running=false, done=false, error=false`. So if `job-status` lies about `done`, the apt-list cross-check is never consulted.

`_kernel_patch_unit_state()` (pre-fix at `app.py:43058–43089`) ran:

```python
['systemctl', 'show', '-p', 'ActiveState,SubState,Result,MainPID',
 _KERNEL_PATCH_UNIT]
```

After a reboot, the transient unit `infratak-kernel-patch.service` does **not** exist (transient units don't survive reboot — they have no on-disk unit file and systemd's in-memory state is wiped). But `systemctl show -p Result` on a non-existent unit does NOT return "unit not found." It returns the **default values** of each property:

```
ActiveState=inactive
SubState=dead
Result=success     ← THIS IS THE DEFAULT — NOT A REAL OUTCOME
MainPID=0
```

`_kernel_patch_job_state` then computed:

```python
done = (not running) and (result == 'success')   # → True
err  = (not running) and (active == 'failed' or (result not in ('', 'success')))   # → False
```

`done=True` → JS jumps straight to the "done" branch and shows the `[Reboot now]` banner. Reboot → reboot → identical state → identical banner. The comment in the prior code even said this should be safe (`# Use systemd's own Result= field as the authoritative success/fail signal`) — but the comment assumed `systemctl show` distinguished "completed successfully" from "doesn't exist." It does not.

### Fix

Two layers in `app.py` (defense in depth — either alone closes the bug):

**Layer 1 — Hard gate on `LoadState=loaded`.** `_kernel_patch_unit_state()` now also queries `LoadState`:

```python
['systemctl', 'show', '-p', 'LoadState,ActiveState,SubState,Result,MainPID',
 _KERNEL_PATCH_UNIT]
```

When the unit doesn't exist, `LoadState=not-found`. `_kernel_patch_job_state()` short-circuits in that case and returns `running=False, done=False, error=False`. The dashboard JS then falls through to the apt-list probe at `/api/system/kernel-patch-status`, which returns `patched=true` (no kernel update pending), and the banner clears.

**Layer 2 — Cross-check `done` against the apt-list probe.** Even on a future code path where `LoadState=loaded` but the kernel is already current (e.g. operator clicked Patch now but no kernel update was pending), `_kernel_patch_job_state()` now downgrades `done=True` → `done=False` if `apt list --upgradable | grep linux-image` returns 0. The "done" state's only purpose is to surface "you should reboot to boot the new kernel" — if there's no new kernel staged, the banner has nothing to assert.

The cross-check reuses the 60-second cache in `_kernel_patch_cache` when fresh (avoids spamming apt). On apt probe failure, it falls back silently to Layer 1 (best-effort defense in depth — Layer 1 already covers the field-observed bug).

### Net behavior

After v0.9.32 with the Bug 2 fix:

| Box state | `LoadState` | `done` | Banner shows |
|---|---|---|---|
| Patch never started | `not-found` | `False` | apt-list probe decides (idle banner if upgrade pending, hidden if patched) |
| Patch running | `loaded` | `False` (running=true) | live log tail with spinner |
| Patch just finished, kernel pending boot | `loaded` | `True` (apt-list still shows linux-image upgradable until reboot) | `[Reboot now]` + `[I'll reboot later]` |
| Patch finished, box rebooted, new kernel running | `not-found` | `False` (Layer 1 fires) | hidden (apt-list cross-check confirms patched) |
| Operator runs apt-get full-upgrade externally then clicks Patch now | `loaded`, `Result=success`, apt-list clean | `False` (Layer 2 fires) | hidden — no false reboot prompt |

### Why this wasn't caught by code review of v0.9.31 Bug 6

The transient-unit lifecycle was the right answer to v0.9.31's actual problem (cgroup escape from `takwerx-console.service`'s sphere of influence so `needrestart` couldn't kill the upgrade mid-flight). The systemd defaults gotcha is a separate subtlety — `systemctl show` returning property defaults for unknown units is documented in `systemd.exec(5)` but isn't something most developers hit until they reboot through a transient unit's lifecycle. The v0.9.31 smoke test on test8 (with `apt-get -s full-upgrade` simulate-mode substituted) terminated **before** the reboot leg, so this codepath was never exercised.

Lesson recorded below: smoke tests for `systemd-run --no-block` transient units MUST include a reboot leg to validate post-reboot state inference.

---

## Validation plan (dev fleet)

Per [docs/TEST-AND-EVALUATION-PROCEDURE.md](TEST-AND-EVALUATION-PROCEDURE.md):

1. **Operator pulls `dev` + restarts `takwerx-console.service`** on each of `test6`, `test8`, `test12`. (Agent does not do the pull.)
2. **Agent verifies** each box reports `VERSION = 0.9.32-alpha` in the sidebar, console `/healthz` returns 200, no migration tracebacks in the last 2 min of `journalctl -u takwerx-console`.
3. **Soak ≥60 min** per box. Standard health-check matrix (containers `(healthy)`, no `query_wait_timeout` in pgbouncer, no real watchdog ALERTs, no `idle_in_transaction_session_timeout` events).
4. **Console-page click-through — Bug 1** on each box (this is the original bug; no agent-side substitute is possible):
   - [ ] Open `https://<console>/` (Console page).
   - [ ] DevTools console shows **zero** `Uncaught SyntaxError` and **zero** `Uncaught ReferenceError`.
   - [ ] Click **"What's using CPU/RAM?"** under any host card → resource-breakdown panel opens, process list renders.
   - [ ] Click **"Refresh"** inside the panel → tables update without error.
   - [ ] Click the toggle switch on a host card (unattended-upgrades toggle) → label flips between "Enabled" / "Disabled" and the PATCH lands.
   - [ ] If the kernel-update banner is visible on any box: click **"Patch now"** → confirm dialog appears (multi-line text rendered correctly). Cancel without confirming (we're testing the JS, not the upgrade itself).
5. **Reboot-loop verification — Bug 2** on at least one box (preferably the one that already showed the looping banner during the v0.9.32 first-cut test):
   - [ ] On a box where the dashboard is currently showing "Kernel patch complete — reboot required" *after* a real reboot (i.e. you're already in the broken state): pull this fix and restart `takwerx-console.service`.
   - [ ] Reload the dashboard. Banner must clear within ~5s (the JS calls `/api/system/kernel-patch/job-status`, gets `done=false` because `LoadState=not-found`, falls through to the apt-list probe, gets `patched=true`, hides the banner).
   - [ ] On a box where a real kernel upgrade IS pending: click **"Patch now"** → wait for `done` state → click **"Reboot now"** → after the box comes back, the banner must NOT re-appear. (This is the end-to-end click-through.)
   - [ ] Inspect `systemctl show -p LoadState,ActiveState,Result infratak-kernel-patch.service` post-reboot. Must report `LoadState=not-found`. (Confirms the system-level reason the prior code was confused.)
6. **Negative-control** on a box still on v0.9.31 (if you have one available): same Console-page click-through reproduces the original `SyntaxError` + `ReferenceError` chain in DevTools (Bug 1), and a reboot-post-Patch-now reproduces the looping banner (Bug 2). Confirms we have the right diagnoses.

## Files changed

**Bug 1 — dashboard `<script>` parse fix:**
- `app.py:369` — `VERSION = "0.9.32-alpha"`
- `app.py:44619` — startKernelPatch confirm string fixed (outer `"…"`, `\\n`, `\\u2019`)
- `app.py:44679` — rebootForKernelPatch confirm string fixed (`\\n`)
- `app.py:44681` — kpatch-log-done append fixed (`\\n`)

**Bug 2 — `[Reboot now]` banner re-fire fix:**
- `app.py:43058–43108` — `_kernel_patch_unit_state()` now also queries `LoadState`; returns a 5-tuple `(load_state, active_state, sub_state, result, main_pid)`.
- `app.py:43180–43290` — `_kernel_patch_job_state()` short-circuits with `done=false, error=false, running=false` when `LoadState != 'loaded'` (Layer 1); and downgrades `done=true` → `false` when the apt-list probe says no kernel upgrade is pending (Layer 2).
- `app.py:43125, 43187, 43231` — three call sites updated to unpack the new 5-tuple from `_kernel_patch_unit_state()`.

**Docs:**
- `README.md` — Latest release + changelog entry (covers both fixes)
- `memory-bank/techContext.md` — version-history entry (covers both fixes)
- `docs/RELEASE-v0.9.32-alpha.md` (this file)

No schema changes. No new settings keys. No new dependencies. No new endpoints.

## Fleet-uniform compliance

Per `.cursor/rules/fleet-uniform-config.mdc`: both fixes are pure code-path bug fixes. Same code on every box → same rendered JS, same systemctl query, same apt-list cross-check on every box. Zero per-box state, zero operator-override surface, zero autotune interaction.

## Lessons recorded

1. **Triple-quoted Python string templates with inline JavaScript are a long-tail rendering hazard.** Anytime you write `\n` or `\'` inside `CONSOLE_TEMPLATE = '''…'''` (or any other `'''…'''` / `"""…"""` template in `app.py`), Python parses the escape, not the browser. Use `\\n` and `\\u2019` (or just switch the outer JS quote style) for anything that needs to survive Python's pass.
2. **A single broken inline `<script>` aborts the whole block.** A one-character syntax error 600 lines into the dashboard JS takes out every onclick handler on the page. The error message points at the line of the breakage but the visible symptom is "everything that should be clickable does nothing." Look for the first SyntaxError in DevTools, not the most-clicked button that's broken.
3. **`node --check` on the extracted rendered script is the right pre-merge guard.** A 5-line validator caught Bug 1 in 600ms. Worth wiring into a release-prep make target before any future template-touching commit ships.
4. **v0.9.31's other five bug fixes were not affected by Bug 1.** All five passively-firing migrations (Bug 3 takwerx user, Bug 4 chain healer, Bug 5 tasklog cleanup) run server-side at `_startup_migrations` time, not from the dashboard JS. Bug 1 (TAK Server purge) and Bug 2 (Caddyfile regen) are operator-action paths that route through `/api/...` Flask endpoints, not through the broken JS block. The console dashboard `<script>` block specifically is what got nuked.
5. **`systemctl show -p Result` on a non-existent unit returns the DEFAULT (`success`), not an error.** This is the v0.9.32 Bug 2 root cause. When you build a state machine on top of `systemctl show` for a transient unit lifecycle, you MUST query `LoadState` and short-circuit if it's anything other than `loaded` — otherwise you'll interpret systemd's property defaults as real outcomes. Documented in `systemd.exec(5)` but easy to miss until you reboot through a transient unit's full lifecycle.
6. **Smoke tests for `systemd-run --no-block` transient units MUST include a reboot leg.** The v0.9.31 Bug 6 smoke test on test8 substituted `apt-get -s full-upgrade` (simulate mode) — it validated the cgroup escape and the unit-spawn invariants, but terminated before the reboot leg. The post-reboot state-inference codepath was never exercised. Any future smoke test for a transient-unit feature must include "reboot the box, then re-query the unit's state and verify the dashboard's behavior" as an explicit step.
