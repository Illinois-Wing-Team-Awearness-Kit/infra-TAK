# Test & Evaluation Procedure (T&E) — release validation playbook

**Purpose.** One canonical, repeatable procedure for validating a candidate release on the dev fleet before any merge to `main` / tag / push. When the operator says **"perform the test and evaluation procedure"** (or "run T&E", "soak it", "validate dev"), the agent follows this doc top to bottom — no improvisation.

**Why a single doc.** Recent releases (v0.9.27, v0.9.28, v0.9.29, v0.9.30, v0.9.31) all converged on the same pattern: pull dev on 3 boxes, restart, soak ≥60 min, check the same metric matrix. Each release re-derived it from scratch — and v0.9.26 shipped to `main` validated only on a single operator-tuned box (`tak-10`) which fractured the fleet. This doc codifies the pattern so no one re-derives it and no one validates on the wrong box.

**Authority.** This procedure is the operationalization of two cursor rules that already exist:

- `.cursor/rules/fleet-uniform-config.mdc` — validation MUST run on boxes whose runtime config matches what the codebase computes. No operator overrides.
- `.cursor/rules/no-main-merge-no-tag-without-permission.mdc` — even after T&E passes, the agent MUST stop and ask before merging / tagging / pushing.

Read both. This doc presumes you have.

---

## Roles — who does what

T&E is a **two-actor protocol**. Splitting these roles is deliberate and not optional:

| Step | Operator | Agent |
|---|---|---|
| Pick candidate (SHA + version) | — | ✓ |
| Pre-flight each box (override check, baseline health) | — | ✓ (reports findings) |
| **Pull `dev` + restart console on each box** | **✓ (manually, per-box)** | **NEVER** — the agent does not pull or restart on test boxes. See § Step 2. |
| Verify pull took (SHA match, console up, no migration tracebacks) | — | ✓ |
| Run soak window + collect metrics at T+60 min | — | ✓ |
| Release-specific operator-action checks (click Remove buttons, click Patch now, etc.) | ✓ | flags them for the operator |
| PASS/FAIL gate + ship prompt | — | ✓ (asks; does not ship) |
| Authorize `main` merge / tag / push | ✓ | — |

**Why the operator does the pull.** The pull is the customer-facing operator action — the same code path a real customer hits with Update Now or a manual `git pull` + restart. Having the operator run it on the dev fleet:

1. **Validates the pull itself** as part of T&E. If `git fetch origin dev` clobbers, prints a merge conflict, or hits a shallow-clone error, the operator sees it directly — not buried in an agent SSH session.
2. **Locks in human-eyes-on-deploy** for every box. The agent can't accidentally pull the wrong SHA on the wrong box or skip a box and quietly soak only 2 of 3.
3. **Mirrors the real upgrade flow.** Customers don't have an SSH agent SSHing into their box and running `git checkout -B dev`. They run a command (or click a button) and watch it work. So should the dev fleet.
4. **Forces a per-box pause.** If box 1's pull throws a warning the operator wasn't expecting, they can stop, investigate, and call the candidate bad before boxes 2 and 3 are even touched.

The agent's job after the pull is to **verify** it took (Step 2 checks), **soak** for ≥60 min, and **report** the health-check matrix at T+60 min — not to do the deploy itself.

---

## TL;DR — the procedure in one screen

1. **Agent: pick the candidate.** Identify the dev branch HEAD SHA and the release version string. Confirm the release notes file exists (`docs/RELEASE-vX.Y.Z-alpha.md`) with a "Validation plan" section.
2. **Agent: pre-flight each box.** No operator overrides intersecting the change. Console healthy at baseline. Note each box's pre-pull SHA.
3. **Operator: pull dev + restart console on each box, manually.** Canonical command in § Step 2. Agent does NOT do this. Operator reports back which boxes pulled cleanly and at what time.
4. **Agent: verify the pull took on each box.** Same SHA as candidate, console active, `/healthz` 200, no migration tracebacks in last 2 min of journal. Soak clock starts here.
5. **Wait ≥60 minutes.** No exceptions. If a new commit lands during the soak, operator pulls again and the clock resets on every box that pulled it.
6. **Agent: run the health-check matrix on each box.** § Step 4. All metrics must hit green on all boxes.
7. **Agent: run the release-specific checks** from the release notes' Validation plan. Operator-action paths flagged for the operator to confirm.
8. **Agent: PASS/FAIL the gate.** § Step 6 checklist — all items checked = green; any item red = stop and triage on dev.
9. **Agent: if green, present the ship prompt** per `no-main-merge-no-tag-without-permission.mdc` and **wait for explicit operator authorization** before any `main` / tag / push.

---

## Test fleet (as of 2026-05-19)

These are the boxes the agent validates against. Update this list when boxes are added/retired.

| Box | Role | Tracks | Notes |
|---|---|---|---|
| `test6` | Primary SSDNodes dev box | `dev` | Used in v0.9.28–v0.9.31 validations. |
| `test8` | Primary SSDNodes dev box | `dev` | Used in v0.9.27–v0.9.31. Surfaced v0.9.26 fleet-fracture (the `max(cur,target)` cautionary tale). |
| `test12` | Primary SSDNodes dev box | `dev` | Used in v0.9.28–v0.9.31. |
| `tak-10` (`172.93.50.47`) | Maintainer's longest-running box | `dev` | **WARNING: historically operator-tuned (pgbouncer 250/50, watchdog thresholds, etc.). Do NOT validate on tak-10 alone.** Use only as a 4th data point alongside the 3 above, and verify its `.config/settings.json` against what `app.py` would compute on first boot. |
| `responder` | Busier (Mission API / DataSync) | `dev` | Use when the change touches Authentik bind volume, spiral detection, or LDAP load. |

**Minimum quorum: 3 boxes from rows 1–3.** Add row 4/5 when the change calls for it.

**Fresh-install box rule.** If the release touches deploy / install paths (Bugs 1 + 3 in v0.9.31, e.g.), validate on **at least one fresh-installed box** in addition to the soak fleet. A box that's been pulling `dev` for months has run every prior migration; a fresh `start.sh` box has run none. Different code paths.

---

## Step 0 — confirm the candidate

The agent does this without operator help:

```bash
# Where are we
git -C /Users/andreasjohansson/GitHub/infra-TAK status
git -C /Users/andreasjohansson/GitHub/infra-TAK rev-parse dev

# What does VERSION say
grep '^VERSION' /Users/andreasjohansson/GitHub/infra-TAK/app.py

# Is there a release notes file with a Validation plan?
ls /Users/andreasjohansson/GitHub/infra-TAK/docs/RELEASE-v$(grep '^VERSION' /Users/andreasjohansson/GitHub/infra-TAK/app.py | cut -d'"' -f2).md
```

**Required state before T&E starts:**

- [ ] Working tree clean (`git status` shows nothing to commit).
- [ ] On `dev` branch.
- [ ] `dev` is pushed to `origin/dev` (`git status` says "up to date" or "ahead of origin/dev by 0" — push first if ahead).
- [ ] `app.py`'s `VERSION = "X.Y.Z-alpha"` matches the release notes filename `docs/RELEASE-vX.Y.Z-alpha.md`.
- [ ] Release notes file has a **"## Validation plan"** section with explicit checks for the change.

Record the candidate SHA. T&E validates **that exact SHA**.

---

## Step 1 — pre-flight each test box (agent)

Agent runs these read-only probes per box. Pre-flight runs **before** asking the operator to pull, so override / baseline state is captured at the pre-pull SHA (otherwise we can't distinguish "this box has always been this way" from "the candidate broke it").

```bash
# 1. Note the SHA the box is on right now (baseline)
ssh <box> "cd \$(grep -oP 'WorkingDirectory=\K.*' /etc/systemd/system/takwerx-console.service) && git rev-parse HEAD && git log -1 --format='%s'"

# 2. Confirm no operator overrides intersect with the change
#    The override-detection command depends on the change. Generic check:
ssh <box> "cd \$(grep -oP 'WorkingDirectory=\K.*' /etc/systemd/system/takwerx-console.service) && \
  python3 -c 'import json; s=json.load(open(\".config/settings.json\")); \
  print(json.dumps({k:v for k,v in s.items() if \"override\" in k.lower() or \"autotune\" in k.lower() or \"pool_size\" in k.lower() or \"watchdog\" in k.lower()}, indent=2))'"

# 3. Console healthy at baseline (so we know it was green BEFORE the candidate, not just AFTER)
ssh <box> "systemctl is-active takwerx-console && curl -kfsS https://localhost:5001/healthz >/dev/null && echo CONSOLE_OK"

# 4. Watchdog / Authentik baseline (used as the BEFORE for the AFTER comparison)
ssh <box> "tail -n 1000 /var/log/takguard/watchdog.log 2>/dev/null | grep -c ALERT; \
           docker logs authentik-server-1 --since 30m 2>&1 | grep -cE 'WORKER TIMEOUT|SIGABRT'; \
           docker exec authentik-postgresql-1 psql -U authentik -d authentik -tAc \
             \"SELECT count(*) FROM pg_stat_activity WHERE state='idle in transaction' AND application_name LIKE '%authentik%';\""
```

**Per-box gate before asking the operator to pull:**

- [ ] Baseline SHA recorded (used to verify the operator's pull actually took).
- [ ] Override-grep returned `{}` or only orthogonal keys (operator confirmed they're orthogonal to this release).
- [ ] Baseline `systemctl is-active takwerx-console` = `active`, `/healthz` = 200.
- [ ] Baseline watchdog ALERT count and Authentik SIGABRT count noted (we expect them to **not increase** during the soak — not necessarily to be zero, especially on `responder`).

**If any box fails pre-flight: tell the operator NOT to pull on it.** Either the operator fixes the override (per `fleet-uniform-config.mdc`: clear the override or restore the codebase default before validating) or that box drops out of the quorum and a replacement is added.

Agent's output at the end of Step 1: a per-box pre-flight summary + the **operator's pull instructions for Step 2** (below). Then the agent waits for the operator to do the pulls.

---

## Step 2 — operator pulls dev + restarts console (operator action)

**The operator runs this command — not the agent.** This is the customer-facing deploy path and the human-eyes-on-deploy gate. The agent's job here is to print the command, wait, then verify the result.

Resolve the install dir dynamically — **never hardcode `/home/takwerx/infra-TAK` or `/root/infra-TAK`**, per `memory-bank/techContext.md`. The agent emits this command per box, the operator pastes it into their SSH session for that box:

```bash
cd $(grep -oP 'WorkingDirectory=\K.*' /etc/systemd/system/takwerx-console.service)
git fetch origin dev
git checkout -B dev origin/dev
echo "now on: $(git rev-parse HEAD)  $(git log -1 --format='%s')"
sudo systemctl restart takwerx-console
sleep 5
systemctl is-active takwerx-console
curl -kfsS https://localhost:5001/healthz >/dev/null && echo CONSOLE_OK_AFTER_PULL
```

**What the operator watches for and reports back to the agent:**

1. `git fetch origin dev` — no clobber/shallow/refusal errors. If it complains, STOP and triage; do not paste the next line. (`docs/TESTING-UPDATES.md` covers the common failure modes — tag clobber, shallow clone, wrong `origin`.)
2. `git checkout -B dev origin/dev` — no merge/rebase prompts, no detached-HEAD warning beyond the normal "Switched to a new branch 'dev'".
3. `now on: <sha>` line printed — operator notes the SHA and confirms it matches the agent's candidate SHA (Step 0).
4. `systemctl is-active takwerx-console` prints `active`.
5. `CONSOLE_OK_AFTER_PULL` is printed.

The operator then tells the agent something like: **"test6 / test8 / test12 are all on `<sha>`, restarted at HH:MM UTC, console OK"** — or names any box that didn't make it. (Free-form is fine; the agent extracts what it needs.)

### Why operator-driven and not agent-driven

Detailed rationale in § "Roles" at the top. The short version: the pull is the customer code path. Having the operator run it manually:

- Catches pull-path failures (tag clobber, merge conflicts, shallow clones) at the same point a customer would catch them — under operator eyes, not buried in an agent SSH session.
- Prevents the agent from silently soaking the wrong SHA, the wrong box, or 2-of-3 boxes.
- Forces a per-box pause where the operator can call the candidate bad before more boxes are touched.

### Why `dev` pull + `systemctl restart`, not Update Now

- **`dev` pull + restart** is what every test box already uses to track the dev branch. It exercises `_startup_migrations()` on the new code without needing a tag to exist.
- **Update Now** is a separate code path (single-tag fetch via the GitHub API, `git checkout --force <tag>`) and is validated separately per `docs/TESTING-UPDATES.md` before any tag push. Do NOT skip TESTING-UPDATES.md — it catches things this procedure doesn't (tag clobbers, shallow clones, wrong refs).
- If the release adds new migrations that should only run during an Update Now, also run the Update Now path on at least one box per `docs/TESTING-UPDATES.md`.

### Agent: verify the pull took (per box, read-only)

After the operator reports back, the agent verifies each box. Read-only — does NOT pull, restart, or modify state.

```bash
ssh <box> bash <<'EOF'
INSTALL_DIR=$(grep -oP 'WorkingDirectory=\K.*' /etc/systemd/system/takwerx-console.service)
cd "$INSTALL_DIR"
echo "=== SHA ==="
git rev-parse HEAD
git log -1 --format='%s'
echo "=== console ==="
systemctl is-active takwerx-console
curl -kfsS https://localhost:5001/healthz >/dev/null && echo CONSOLE_OK || echo CONSOLE_FAIL
echo "=== boot log (migrations) ==="
sudo journalctl -u takwerx-console --since '5 min ago' | grep -E 'VERSION|Startup migration|Traceback|failed:' | head -80
EOF
```

**Per-box gate after the operator's pull (agent verifies):**

- [ ] `git rev-parse HEAD` on the box equals the candidate SHA from Step 0. If it doesn't: operator pulled the wrong commit, or a newer commit landed mid-pull. Tell the operator before starting the soak clock.
- [ ] `systemctl is-active takwerx-console` = `active`.
- [ ] `CONSOLE_OK` printed (i.e. `/healthz` returned 200).
- [ ] Console boot log shows `VERSION = "X.Y.Z-alpha"` and every migration the release adds, each followed by either `✓` (applied), `already correct — skipping`, or a no-op early-return line — no `Traceback`, no bare `Exception`, no `failed:*`.

If a migration logged `failed:*` — STOP. Triage on dev. Do not start the soak clock with a failed migration; that's the bug to find, not a metric to wait out.

**Soak clock starts only when all boxes pass this gate.** Note the timestamp; this is T+0.

---

## Step 3 — the soak window (≥60 min)

Start the soak clock **only after all boxes are on the candidate SHA and all migrations completed cleanly**.

**Why 60 min.** Multiple failure classes only surface on the 5–90-min window:

- Authentik Channels-pool exhaustion (v0.9.26 / v0.9.27): unhealthy at ~5 min on a fresh recreate, sometimes returns to ~5 min stable then re-degrades at 30–60 min.
- PgBouncer `query_wait_timeout` storms: cluster at 60–90 min post-recreate when the Channels groupchannel SELECTs accumulate.
- `takauthentiktasklogpurge.service` Sunday timer (v0.9.31 Bug 5): only fires on Sunday — can't be soaked into, must be reasoned about. Note in release notes if this applies.
- Watchdog ALERT thresholds: scaled to 60-min windows by design; shorter soak won't surface threshold-tuning bugs.

**If a commit lands mid-soak** (operator pushed a hotfix during validation): the operator re-runs Step 2 on every box that should pick up the new commit, and the soak clock resets on each box at the moment it pulled. **Do NOT validate v0.X on commit A on one box and commit B on another.** The agent's job here is to (a) tell the operator the soak clock has reset, (b) re-verify every box landed on the same new SHA, (c) restart the 60-min timer.

**During the soak:** the agent does not need to actively watch. Set a reminder, work on something else. The metric matrix below is captured at end-of-soak. The agent does NOT re-pull, restart, or otherwise touch the test boxes during the soak — read-only probes only if any are run mid-soak.

---

## Step 4 — the health-check matrix (run at T+60 min on every box)

Every metric must be green on every box. **No "well box X looks a little off but box Y is fine" reasoning** — that's how v0.9.26 shipped. Fleet means all boxes.

### 4a. Console + watchdog (every box)

```bash
ssh <box> bash <<'EOF'
echo "=== takwerx-console ==="
systemctl is-active takwerx-console
curl -kfsS https://localhost:5001/healthz >/dev/null && echo CONSOLE_OK || echo CONSOLE_FAIL
echo
echo "=== systemd --failed ==="
systemctl --failed --no-legend
echo
echo "=== takwerx-console journal: errors/tracebacks in last 65 min ==="
sudo journalctl -u takwerx-console --since "65 min ago" 2>&1 | \
  grep -cE "Traceback|ERROR|CRITICAL|failed:" || true
echo
echo "=== watchdog ALERTs in last 65 min ==="
tail -n 5000 /var/log/takguard/watchdog.log 2>/dev/null | \
  awk -v d="$(date -u --date='65 min ago' '+%Y-%m-%d %H:%M')" '$0 >= d' | \
  grep -c ALERT || true
EOF
```

**Targets:**

| Metric | Target | Investigate | Stop |
|---|---|---|---|
| `systemctl is-active takwerx-console` | `active` | — | anything else |
| `/healthz` | `CONSOLE_OK` | — | timeout / 5xx |
| `systemctl --failed` | empty | one unit listed → check it | ≥2 units, or any unit related to the change |
| Console journal errors (65 min) | 0 | 1–2 (transient retry that succeeded) | 3+ or any `Traceback` |
| Watchdog ALERTs (65 min) | 0 NEW above baseline | 1–2 NEW (transient burst) | 3+ NEW or any sustained ALERT class |

### 4b. Authentik (every box that runs Authentik)

This is the most-frequent failure class. Run the full TESTING-AUTHENTIK.md "30-minute soak test" block on every box. Summary of the targets that MUST be green:

```bash
ssh <box> bash <<'EOF'
echo "=== Authentik idle-in-transaction ==="
docker exec authentik-postgresql-1 psql -U authentik -d authentik -tAc \
  "SELECT count(*) FROM pg_stat_activity WHERE state='idle in transaction' AND application_name LIKE '%authentik%';"

echo "=== SIGABRT / worker timeout (60 min) ==="
docker logs authentik-server-1 --since 60m 2>&1 | grep -cE "WORKER TIMEOUT|SIGABRT" || true

echo "=== Outpost spiral markers (60 min) ==="
docker logs authentik-ldap-1 --since 60m 2>&1 | grep -cE "exceeded stage recursion|502 bad gateway|503 service|nil pointer|result code 50" || true

echo "=== PgBouncer query_wait_timeout (60 min) ==="
docker logs authentik-pgbouncer-1 --since 60m 2>&1 | grep -c query_wait_timeout || true

echo "=== Container health (every Authentik container) ==="
docker ps --filter 'name=authentik' --format '{{.Names}}: {{.Status}}'

echo "=== Authentik runtime config (verify env vars actually loaded) ==="
docker exec authentik-server-1 ak dump_config 2>/dev/null | \
  grep -E "^(web|postgresql|outposts|listen)" | head -30

echo "=== LDAP routing (FQDN on any box with TAK Server) ==="
grep 'AUTHENTIK_HOST:' ~/authentik/docker-compose.yml 2>/dev/null
EOF
```

**Targets (from TESTING-AUTHENTIK.md + v0.9.20+ field reference):**

| Metric | Target | Investigate | Spiraling |
|---|---|---|---|
| Postgres idle-in-tx | 0–3 | 10–29 | ≥30 |
| SIGABRT count (60 min) | 0 | 1–2 (transient) | 3+ |
| Outpost spiral markers (60 min) | 0 | 1 (transient) | 2+ |
| PgBouncer `query_wait_timeout` (60 min) | 0 | 1–2 (transient) | 3+ |
| Container status | every Authentik container `(healthy)` | one transient `(unhealthy)` that recovered | sustained unhealthy |
| `ak dump_config` env vars | match release notes' expected values (DOUBLE underscore where applicable, per `consult-upstream-docs.mdc`) | — | mismatch |
| LDAP routing | `https://<fqdn>` (on TAK boxes) | — | `http://authentik-server-1:9000` |

**`ak dump_config` is not optional.** Per `.cursor/rules/consult-upstream-docs.mdc`, never trust the input config to mean the runtime is using it. Every release that changes Authentik env vars MUST verify them via `ak dump_config` on every box. v0.8.2 → v0.8.6 shipped `AUTHENTIK_WEB_WORKERS=4` (SINGLE underscore — silently ignored) for 5 releases because no one ran this check.

### 4c. TAK Server (every box that runs TAK Server)

```bash
ssh <box> bash <<'EOF'
echo "=== TAK Server processes ==="
sudo systemctl is-active takserver
ss -tlnp 2>/dev/null | grep -E ':8089|:8443|:8446' | wc -l

echo "=== CoT DB size ==="
sudo -u postgres psql -d cot -tAc "SELECT pg_size_pretty(pg_database_size('cot'));"

echo "=== Guard Dog last run ==="
sudo journalctl -u tak-guarddog --since "10 min ago" 2>&1 | tail -10

echo "=== 8089 connections (live clients) ==="
ss -tn '( sport = :8089 )' state established 2>/dev/null | wc -l
EOF
```

**Targets:** systemd active; 3 listening ports (`8089`, `8443`, `8446`); CoT DB size not unexpectedly grown vs baseline; Guard Dog last run = success; 8089 connection count not zero on busy boxes (zero on `responder` mid-soak = symptom).

### 4d. Container health (general — for releases that touch MediaMTX / CloudTAK / Node-RED / Fed Hub)

```bash
ssh <box> "docker ps --format '{{.Names}}: {{.Status}}' | grep -v healthy"
```

**Target:** empty output. Any container not `(healthy)` is a fail unless explicitly noted in the release notes (e.g. some containers don't have healthchecks defined).

### 4e. Cross-box convergence (the fleet-uniform gate)

This is what v0.9.26 missed. After every box passes 4a–4d individually, verify they all converged to the SAME config:

```bash
# Run on EVERY box, compare outputs
ssh <box> "cd \$(grep -oP 'WorkingDirectory=\K.*' /etc/systemd/system/takwerx-console.service) && \
  git rev-parse HEAD && \
  grep '^VERSION' app.py && \
  python3 -c 'import json; s=json.load(open(\".config/settings.json\")); \
    print(\"pool_size:\", s.get(\"authentik_pgbouncer_pool_size\")); \
    print(\"watchdog:\", s.get(\"watchdog_thresholds_autotune\")); \
    print(\"channels:\", s.get(\"authentik_channels_pool_baseline\"));'"
```

**Target:** every box reports the same SHA, same VERSION, same autotune outputs (or same explicit fleet constants). **If two boxes have different pool_size values or different watchdog thresholds, ONE OF THEM has an operator override or the autotune saw different signals — investigate before shipping. Don't ship a release that runs differently on different boxes "in normal operation."**

---

## Step 5 — release-specific verification

Open `docs/RELEASE-vX.Y.Z-alpha.md`. Find its **"## Validation plan"** section (every release after v0.9.26 has one; if it doesn't, the release isn't ready). Run every checkbox in that section on the appropriate boxes.

For v0.9.31-alpha as a worked example, the validation plan includes:

- Bug 1: trigger `Remove TAK Server` → re-deploy → expect `Detected half-removed takserver ... purging before reinstall...` log line and successful deploy.
- Bug 2: trigger `Remove` on each per-service → visit subdomain → expect clean Caddy 404 (not Authentik "Not Found").
- Bug 3: restart console → expect `✓ takwerx heal complete: mediamtx + mediamtx-webeditor both active` log line and `systemctl is-active mediamtx mediamtx-webeditor` both `active`.
- Bug 4: restart console → expect `Startup migration: ✓ TAK Portal Proxy: provider ...` and visit `takportal.<fqdn>` → expect Authentik login (not "Not Found").
- Bug 5: on any box with the stale failed unit → restart console → expect `✓ tasklog-purge: stale failed-state cleared` log line; `systemctl --failed` no longer lists the unit.
- Bug 6: click `[Patch now]` in banner → verify `infratak-kernel-patch.service` runs in its own cgroup, survives `takwerx-console.service` restart, completes with `Result=success`.

**Every bullet must be checked on at least one box for which it applies.** Operator-action-triggered paths (Bug 1, Bug 2, Bug 6) need explicit operator action — the agent CAN'T validate those on its own and must ask the operator to trigger them, or flag them as "code-only review" in the ship prompt.

---

## Step 6 — the PASS/FAIL gate

Copy this checklist into the operator-facing ship-prompt message. Every item must be ticked before the agent presents the ship prompt.

```
T&E results for v<X.Y.Z>-alpha @ <SHA>:

Boxes validated:
  - test6   on <SHA>   pulled <ISO timestamp>   soak <minutes> min
  - test8   on <SHA>   pulled <ISO timestamp>   soak <minutes> min
  - test12  on <SHA>   pulled <ISO timestamp>   soak <minutes> min
  (+ tak-10 / responder / fresh-install box, if applicable, with the same fields)

Pre-flight:
  [ ] All boxes had no operator overrides intersecting this change
  [ ] All boxes were healthy at baseline

Step 4 health matrix (ALL boxes ALL green):
  [ ] systemctl --failed empty
  [ ] takwerx-console active + /healthz 200
  [ ] No new Tracebacks / migration failures in console journal
  [ ] No new watchdog ALERTs above baseline
  [ ] Authentik idle-in-tx 0–3, SIGABRT 0, spiral markers 0, PgBouncer query_wait_timeout 0
  [ ] All Authentik containers (healthy)
  [ ] ak dump_config confirms env vars match release notes (DOUBLE underscore checked)
  [ ] LDAP routing on FQDN (on TAK boxes)
  [ ] All non-Authentik containers (healthy)

Cross-box convergence:
  [ ] All boxes same SHA, same VERSION, same autotune outputs (no fleet fracture)

Release-specific validation (from release notes):
  [ ] Every checkbox in docs/RELEASE-v<X.Y.Z>-alpha.md "Validation plan" is ticked
  [ ] Operator-action-triggered paths flagged for operator to confirm or marked code-only

Update Now path (if release adds migrations that should fire on Update Now):
  [ ] docs/TESTING-UPDATES.md completed on at least one box (fake low VERSION, click Update Now, restore)

Failure handling: no item above is red, and no item is "looks a little off."
```

**If any item is red:** STOP. Triage on dev. Push the fix to `dev`. Restart T&E from Step 2 on every box (60-min clock resets per box that pulled the new commit). Do not ship a release where one box is "close enough."

**If every item is green:** present the ship prompt per `.cursor/rules/no-main-merge-no-tag-without-permission.mdc`:

> Ready to ship v<X.Y.Z>-alpha to `main`:
> - dev branch tip: `<SHA>` (`<commit subject>`)
> - field validation: T&E green across <N> boxes, ≥60 min soak on each (see above)
> - this will: squash-merge dev → main, tag `v<X.Y.Z>-alpha`, push both
>
> **Ship it?**

Then **stop and wait**. Do not run `git merge`, `git tag`, `git push origin main`, or `git push origin <tag>` until the operator replies with explicit authorization. Phrases like "do the best thing" / "go for it" / "send it" / "lets ship it" are NOT authorization — ask for the unambiguous "ship to main and tag" or equivalent. See the cursor rule for the full list.

---

## Failure handling — what to do when a box goes red

### Single-box failure

1. Agent captures the failing state IMMEDIATELY (read-only, before anything restarts):
   ```bash
   ssh <box> bash <<'EOF'
   ts=$(date -u +%Y-%m-%dT%H-%M-%SZ)
   mkdir -p /tmp/tae-fail-$ts
   journalctl -u takwerx-console --since "65 min ago" > /tmp/tae-fail-$ts/console.log
   docker ps --format '{{.Names}}: {{.Status}}' > /tmp/tae-fail-$ts/docker-ps.txt
   for c in $(docker ps --format '{{.Names}}' | grep authentik); do
     docker logs "$c" --since 65m > /tmp/tae-fail-$ts/$c.log 2>&1
   done
   docker exec authentik-postgresql-1 psql -U authentik -d authentik -c \
     "SELECT state, count(*) FROM pg_stat_activity WHERE application_name LIKE '%authentik%' GROUP BY state;" \
     > /tmp/tae-fail-$ts/pg_state.txt
   tail -n 5000 /var/log/takguard/watchdog.log > /tmp/tae-fail-$ts/watchdog.log 2>/dev/null
   tar -czf /tmp/tae-fail-$ts.tgz -C /tmp tae-fail-$ts
   ls -la /tmp/tae-fail-$ts.tgz
   EOF
   ```
2. Operator (or agent) pulls the tarball off the box (`scp` to maintainer's machine).
3. Triage on dev. The failing box stays on the candidate SHA — do not roll back yet. Live state may be needed for triage.
4. Agent pushes the fix to `dev`. **Operator** re-runs Step 2 on every box (operator pulls — agent verifies). Do NOT exempt the previously-passing boxes — they pull the new commit too, and the soak clock resets on each.

### Cross-box divergence (different boxes show different config)

This is the v0.9.26 fleet-fracture pattern. **Stop everything.** Do not ship. Per `.cursor/rules/fleet-uniform-config.mdc`, the answer is a code fix that makes the codebase produce the same config on every box from the same observable inputs — not a per-box override. Find which box has the override, clear it, re-validate.

### Soak window too short (<60 min, operator wants to ship NOW)

Do not yield. Per the v0.9.26 cautionary tale: "validated for 65 min" was the lesson learned. <60 min validation is what shipped a broken release to `main`. If the operator pushes back, point at this doc and the rule, and re-prompt with the full T&E checklist. The agent does not have authority to waive the soak window; the operator does, but they have to know what they're waiving and say so explicitly.

---

## See also

- `.cursor/rules/fleet-uniform-config.mdc` — why operator-tuned boxes don't count as validation.
- `.cursor/rules/no-main-merge-no-tag-without-permission.mdc` — the prompt and the gated commands.
- `.cursor/rules/consult-upstream-docs.mdc` — why `ak dump_config` is mandatory.
- `docs/TESTING-AUTHENTIK.md` — the Authentik-specific probes referenced from Step 4b.
- `docs/TESTING-UPDATES.md` — the separate Update Now / tag-fetch validation path.
- `docs/TESTING-NODERED-DEPLOYS.md` — Node-RED Configurator-only validation (out of scope for general T&E unless the release touches it).
- `memory-bank/techContext.md` § "Test/maintainer infrastructure" — the canonical box list.
- The "Validation plan" section in every recent `docs/RELEASE-vX.Y.Z-alpha.md` — release-specific checks plugged into Step 5.

---

## Maintenance — when to update this doc

- **Test fleet changed?** Update § "Test fleet" with the new box list (and retire dead ones).
- **New failure class found?** Add the probe to Step 4. If it has to soak for >60 min to surface, add a note to Step 3 explaining why.
- **New release pattern emerges?** Update the worked example in Step 5 with the latest release's validation-plan shape.

Do not weaken any gate (soak time, override-clearance requirement, cross-box convergence) without an explicit operator decision recorded in the release notes for the release in which the weakening lands. Strengthening gates is fine — push it through whenever a new failure class teaches us something.
