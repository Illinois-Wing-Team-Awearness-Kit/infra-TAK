# v[X.Y.Z]-alpha — Work Plan

**Headline:** [One sentence: what ships and why — e.g. "Fix X which causes Y. Auto-heals on next console restart."]

**Scope discipline:** This release ships [feature/fix] **only**. [Named parked features] are deferred to [v0.X.Y]. [Brief reason why they're parked.]

**Preconditions:** [e.g. "v0.8.8 soaked for ≥7 days on tak-10 and ssdnodes-validated before this is pulled by the fleet."]

---

## The bug / the need

[Full description. Include:]
- When and where it was observed
- Exact log line or error output (copy-paste, not paraphrase)
- Root cause (what config/code is wrong)
- Fleet-wide impact (is every install affected?)
- Why it matters operationally

```
[paste exact log line or error here]
```

---

## The fix

**Function/migration name:** `_function_name_here(plog)` in `app.py`

Steps the migration/fix must perform:
1. [Step 1 — include guard / idempotency check]
2. [Step 2]
3. [Step 3 — what it writes/changes]
4. Records `last_outcome: applied` to `settings.[key]`
5. [Any service restart triggered — name the exact containers]

**Call order** (if it's a migration in the startup chain):
1. `_existing_migration_1` (vX.Y.Z)
2. `_existing_migration_2` (vX.Y.Z)
3. **`_new_migration_here`** (vX.Y.Z — new)
4. `_authentik_verify_runtime_config` (updated to probe new key)

**Approximate location in app.py:** near line ~XXXX, after `[adjacent function name]`

---

## Acceptance test

After `Update Now` or manual pull + restart, run from the VPS:

```bash
# 1. Check settings.json records the outcome
sudo cat /root/infra-TAK/.config/settings.json | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('[check name]:', json.dumps(d.get('[settings_key]', {}), indent=2))
"

# 2. Verify runtime actually loaded the change (not just .env)
[runtime verification command — e.g. ak dump_config, docker exec, curl, etc.]

# 3. Real-world smoke test
[end-to-end action + what to look for in logs]
```

Expected result: `[exact string or value that confirms success]`

---

## What this does NOT ship

- **[Parked feature 1]** — parked to v[X.Y.Z] ([one-line reason])
- **[Parked feature 2]** — parked to v[X.Y.Z] ([one-line reason])
- **UI changes** — same scope discipline as previous releases
- **[Any tempting but out-of-scope change]** — deferred because [reason]
