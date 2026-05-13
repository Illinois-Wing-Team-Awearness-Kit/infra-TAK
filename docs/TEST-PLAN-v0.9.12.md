# v0.9.12-alpha — Test Plan

**Goal:** validate every security feature shipped in v0.9.12 across the three dev boxes before merging `dev` → `main` and tagging `v0.9.12-alpha`.

**Scope:** port hardening (Part A: CloudTAK, TAK Portal, MediaMTX, remote Authentik, Server One Postgres, Guard Dog 8080) + route-level patches (Part B: snapshot path traversal, external-DB SQL injection / RCE, webadmin password injection, hardcoded LDAP password fallback, SSH host/user injection) + the new `main`/`dev` update channel toggle.

**Reference docs:**
- [docs/RELEASE-v0.9.12-alpha.md](RELEASE-v0.9.12-alpha.md) — what shipped
- [docs/PLAN-v0.9.12.md](PLAN-v0.9.12.md) — design
- [docs/PORT-EXPOSURE-POLICY.md](PORT-EXPOSURE-POLICY.md) — Tier 1/3/4/5 classifications

---

## 0. Test boxes & roles

| Host | Role in this test |
|------|-------------------|
| `responder` (172.93.50.47) | "broken" box from the v0.9.5 saga; CloudTAK was previously compromised; clean reinstall expected |
| `tak-10` | development box — currently on `dev` branch via `git pull origin dev` |
| Third box (operator's choice) | clean two-server install if available, otherwise a single-server install |

For each box, capture **before/after** for ports + UFW rules. Don't trust "looks fine" — run the greps.

---

## 1. Pre-flight (all three boxes)

1. Confirm box is reachable: `ssh root@<host>`.
2. From the console UI: `Settings → Server IP` is populated. (Required for source-scoped UFW rules in A4/A5/A6.)
3. Trigger **Update Now** from the console (or `cd <repo> && git pull origin main` then restart for boxes on `main`; on `dev` boxes pull from `dev`).
4. After update completes (console restarts, banner clears), reload the console page.
5. Verify `VERSION` banner shows `v0.9.12-alpha` (or `0.9.12-alpha-rc*` if testing a release candidate).

```bash
# On host, ground truth:
cd /home/takwerx/infra-TAK && grep -E '^VERSION' app.py
```

If a box is on a `0.9.11-alpha` build that needs to test the auto-hardening on top of a known-bad state, run **Update Now** *once* on a vulnerable install before applying any hardening manually — the point of A1-A5 is that Update Now alone fixes everything.

---

## 2. Update channel toggle (smoke test — every box)

Already verified visually on tak-10 but re-run on each box once it's on v0.9.12:

- [ ] First load after update: `main` button renders **green** without any clicks.
- [ ] Click `dev` → password modal appears. Cancel → still on `main` (green).
- [ ] Click `dev` → enter wrong password → "incorrect password" error, still on `main`.
- [ ] Click `dev` → enter correct password → switches to yellow `dev` state, status text shows "Switched to dev — checking…", "Check for new release" then reports the dev branch HEAD commit.
- [ ] Reload the page → still yellow `dev` (persisted in `settings.json` as `update_channel: dev`).
- [ ] Click `main` (no password) → back to green. Reload → still green.
- [ ] Restart console process → channel survives (Flask secret key now persisted to `.config/secret_key`, session also survives).

Set the box **back to `main`** before moving on to Section 3 unless you specifically want it tracking `dev`.

---

## 3. Part A — Port hardening (per-box)

For each test below: SSH in as root, run the listed command(s), record output. The `0.0.0.0:<port>` greps should return **nothing** (because all the admin/loopback ports are now bound to `127.0.0.1` or removed). The `ufw status` greps should show **DENY** rules for the same ports.

### A1 — CloudTAK (any box with CloudTAK installed)

```bash
# Ports — all should be empty:
ss -tlnp | grep -E '0\.0\.0\.0:(5000|5002|5003|9997|18888)'
ss -tlnp | grep -E '0\.0\.0\.0:(5433|9000|9002)'   # v0.9.11 baseline, still must be empty

# UFW — should show DENY for each:
ufw status numbered | grep -E '(5000|5002|5003|9997|18888|5433|9000|9002).*DENY'

# Streaming ports should STAY public (Tier 1) — these should appear in ss output:
ss -tlnp | grep -E '0\.0\.0\.0:(18554|11935|18890)'
```

App-level smoke:
- [ ] Public CloudTAK URL (`https://cloudtak.<fqdn>/`) loads through Caddy.
- [ ] Map loads, login via Authentik works, an existing layer renders.

### A2 — TAK Portal (any box)

```bash
ss -tlnp | grep '0\.0\.0\.0:3000'                  # must be empty
ufw status numbered | grep '3000.*DENY'
```

App-level smoke:
- [ ] `https://portal.<fqdn>/` returns 200 through Caddy, login via Authentik works.
- [ ] Hitting `http://<box-public-ip>:3000` directly is **refused** (connection refused or filtered).

### A3 — MediaMTX (responder runs MediaMTX locally; tak-10 may not)

```bash
# All three admin/HLS/webedit ports must be loopback-only:
ss -tlnp | grep -E '0\.0\.0\.0:(5080|8888|9898)'
ufw status numbered | grep -E '(5080|8888|9898).*DENY'

# Streaming ports STAY public:
ss -tlnp | grep -E '0\.0\.0\.0:(8554|8322|8890|8000|8001)'

# Webedit Flask app — should be 127.0.0.1, not 0.0.0.0:
grep -E "host\s*=\s*'(0\.0\.0\.0|127\.0\.0\.1)'" /opt/mediamtx/mediamtx_config_editor.py 2>/dev/null \
  || grep -E "host\s*=\s*'(0\.0\.0\.0|127\.0\.0\.1)'" ~/mediamtx/mediamtx_config_editor.py
```

App-level smoke:
- [ ] HLS playback through Caddy `/hls-proxy/` works for an existing stream.
- [ ] MediaMTX webedit page loads through Caddy and saves config.
- [ ] `curl -sI http://<box-public-ip>:8888/` and `:9898/` and `:5080/` all fail (refused).
- [ ] RTSP push test still works on `<box-public-ip>:8554` (Tier 1).

### A4 — Remote Authentik (only on a box that has a remote Authentik configured)

```bash
# From the console host:
ssh root@<remote-authentik-host> 'ss -tlnp | grep -E "0\\.0\\.0\\.0:(9000|9443)"'   # must be empty
ssh root@<remote-authentik-host> 'ufw status numbered | grep -E "(9000|9443).*DENY"'

# LDAP must stay reachable but source-scoped to the console IP:
ssh root@<remote-authentik-host> 'ufw status numbered | grep -E "389|636"'
# Expect: ALLOW from <console_server_ip> + DENY catch-all.
```

App-level smoke:
- [ ] Authentik web UI (`https://auth.<fqdn>/`) reachable through Caddy.
- [ ] TAK Server on the console host successfully binds LDAP (`docker logs takserver-takserver | tail` shows no LDAP errors).
- [ ] From an unrelated public IP, `nc -zv <remote-auth-public-ip> 389` fails (timeout/refused).

### A5 — Server One Postgres (two-server install only)

```bash
# On Server One:
ufw status numbered | grep -E "(5432|5433).*DENY"            # catch-all DENY
ufw status numbered | grep -E "5432.*ALLOW.*<server-two-ip>"  # source-scoped ALLOW above it

# From a random box that is NOT Server Two:
nc -zv <server-one-public-ip> 5432    # expect timeout/refused

# From Server Two (or by ssh'ing to it):
nc -zv <server-one-internal-ip> 5432  # expect succeeded
```

App-level smoke:
- [ ] TAK Server on Server Two still talks to Postgres (no DB-down banners in console).

### A6 — Guard Dog 8080 (every box that has a Server One installed)

```bash
ufw status numbered | grep -E "8080.*DENY"                     # catch-all DENY
ufw status numbered | grep -E "8080.*ALLOW.*<console-source-ip>"  # source-scoped ALLOW

# From a random public box:
nc -zv <server-one-public-ip> 8080    # expect timeout/refused

# From console:
curl -sI http://<server-one-internal-ip>:8080/health    # expect 200
```

---

## 4. Part B — Route-level patches (one box is enough; pick tak-10)

These all hit `app.py` directly, no per-deploy variation.

### B1 — Snapshot path traversal

From a logged-in browser session (or `curl` with session cookie):

```bash
# All three should return 400 with "label must match [A-Za-z0-9._-]+" or similar:
curl -b cookies.txt -sS -o /dev/null -w '%{http_code}\n' \
  'https://<console>/api/takserver/snapshot/..%2F..%2Fetc/download'
curl -b cookies.txt -X DELETE -sS -o /dev/null -w '%{http_code}\n' \
  'https://<console>/api/takserver/snapshot/..%2F..%2Fetc'
curl -b cookies.txt -X POST -H 'Content-Type: application/json' \
     -d '{"label":"../../etc"}' \
     -sS -o /dev/null -w '%{http_code}\n' \
     'https://<console>/api/takserver/rollback'
```

- [ ] All three return **400**, response body contains the validation message.
- [ ] A legitimate snapshot label (e.g. one created via the UI) still downloads / rolls back successfully (200).
- [ ] `/srv/takserver/snapshots` (or wherever `SNAPSHOT_DIR` resolves) is untouched — `ls -la` looks identical before and after the traversal attempts.

### B2 — External-DB SQL injection (provision route)

From a logged-in session, POST a payload with shell-meta in identifiers:

```bash
curl -b cookies.txt -X POST -H 'Content-Type: application/json' \
     -d '{
           "db_host":"127.0.0.1",
           "db_port":5432,
           "admin_user":"postgres;DROP TABLE x;--",
           "admin_pass":"x",
           "app_user":"valid_app_user",
           "app_pass":"validpass1234567",
           "db_name":"valid_db_name"
         }' \
     -sS 'https://<console>/api/takserver/external-db/provision'
```

- [ ] Returns **400** with a "must match" / "invalid identifier" error for `admin_user`.
- [ ] Repeat with `app_user` and `db_name` containing the same garbage — also 400.
- [ ] A clean payload with valid identifiers + a reachable Postgres still provisions successfully.

### B3 — External-DB test-connection (RCE → socket)

```bash
curl -b cookies.txt -X POST -H 'Content-Type: application/json' \
     -d '{"db_host":"127.0.0.1; touch /tmp/pwned","db_port":5432}' \
     -sS 'https://<console>/api/takserver/external-db/test-connection'
```

- [ ] Returns **400** with a host-validation error.
- [ ] `ls /tmp/pwned` shows **no such file** — the shell payload never executed.
- [ ] Valid host + port still returns success.

### B4 — Webadmin password injection

From the console UI: Settings → Web Admin Password → try setting it to a value containing `;` or `$(...)`.

- [ ] UI rejects with the `_validate_cert_password` error.
- [ ] Setting a clean strong password succeeds and logging into TAK Server's webadmin with the new password works.

### B5 — Hardcoded LDAP password gone

```bash
grep -n 'B9wobRV8wlFJmnlEWB71gJjD3aoKOBBW' /home/takwerx/infra-TAK/app.py
```

- [ ] **No match** — the literal is gone.
- [ ] `~/authentik/.env` has a unique `AUTHENTIK_BOOTSTRAP_LDAPSERVICE_PASSWORD=...` value (NOT the old literal).
- [ ] If you nuke that line in `.env` and re-run the Authentik deploy step, a NEW random password is generated and persisted — and confirms in the Authentik UI's LDAP outpost config.

### B6 — SSH host/user injection

This one is harder to exercise from the UI; do a code-level smoke instead:

```bash
# Should fail validation if attempted:
python3 -c "
import sys; sys.path.insert(0, '/home/takwerx/infra-TAK')
import app
ok, msg = app._validate_ssh_target('-oProxyCommand=touch /tmp/x', 'root', 22)
print('host injection rejected:', not ok, msg)
ok, msg = app._validate_ssh_target('1.2.3.4', '-oProxyCommand=touch /tmp/x', 22)
print('user injection rejected:', not ok, msg)
ok, msg = app._validate_ssh_target('1.2.3.4', 'root', 999999)
print('port range rejected:', not ok, msg)
ok, msg = app._validate_ssh_target('1.2.3.4', 'root', 22)
print('happy path accepted:', ok)
"
```

- [ ] First three print `True`, last prints `True`. `/tmp/x` does **not** exist.

---

## 5. Functional regression sweep (every box)

After all the security tests, make sure nothing broke for real users:

- [ ] Console pages all load (Home, TAK Server, Authentik, CloudTAK, TAK Portal, MediaMTX, Settings, Snapshots).
- [ ] At least one ATAK / WinTAK / iTAK client successfully connects through the public TAK port and exchanges a CoT.
- [ ] CloudTAK web UI loads, an existing ArcGIS feed renders.
- [ ] Authentik login works for an existing user.
- [ ] Node-RED editor reachable through Caddy + auth-required.
- [ ] Snapshot create + download + rollback (legit label) all round-trip.
- [ ] CPU on each box is sane (`top` showing no postgres at 1000%+ — confirms the v0.9.5→v0.9.11 saga is fully behind us and v0.9.12 didn't re-open anything).

---

## 6. Sign-off

When every checkbox in sections 2-5 passes on at least two of the three boxes (responder + tak-10 mandatory, third box strongly preferred):

1. Tag the dev HEAD: `git tag v0.9.12-alpha && git push origin v0.9.12-alpha`
2. Merge `dev` → `main`: `git checkout main && git merge --no-ff dev && git push origin main`
3. Push the tag to main: already on the same commit so step 1's tag covers it.
4. Update the GitHub Release with the body from `docs/RELEASE-v0.9.12-alpha.md`.
5. Bump `memory-bank/techContext.md` "Release roadmap" — mark v0.9.12 **SHIPPED**, surface v1.0.0 as the new "next major".

If any test fails: open a follow-up todo, fix on `dev`, re-tag `v0.9.12-alpha-rc2`, re-run the failing section. Do **not** merge to `main` until everything in this doc is green — that's the v0.9.7-thru-v0.9.10 lesson we paid for already.

---

## Appendix — quick-copy commands

```bash
# One-shot port audit on any box:
echo "=== 0.0.0.0 binds that should be GONE ==="
ss -tlnp | grep -E '0\.0\.0\.0:(3000|5000|5002|5003|5080|5433|8888|9000|9002|9090|9443|9898|9997|18888)' || echo '(none — good)'
echo
echo "=== UFW DENY rules that should EXIST ==="
ufw status numbered | grep -E '(3000|5000|5002|5003|5080|5433|8888|9000|9002|9090|9443|9898|9997|18888|8080).*DENY' || echo '(MISSING — bad)'
echo
echo "=== Streaming ports that SHOULD stay public ==="
ss -tlnp | grep -E '0\.0\.0\.0:(8554|8322|11935|18554|18890|8000|8001|8890)' || echo '(none — verify if expected)'
```

Drop the above in a `/tmp/audit.sh` on each box and run it pre/post Update Now — the two outputs make the v0.9.12 effect obvious in 5 seconds.
