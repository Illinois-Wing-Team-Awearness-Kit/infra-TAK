# Operator findings — April 2026 (Update Now, recovery, TAK Portal TLS, TAK logs)

Consolidated notes from a rough release window (**v0.4.0 → v0.4.3**): what broke, why, what we changed, and what to tell people. **Customer-facing detail** lives in [RELEASE-v0.4.3-alpha.md](RELEASE-v0.4.3-alpha.md) (latest) and [RELEASE-v0.4.2-alpha.md](RELEASE-v0.4.2-alpha.md); [README](../README.md) **Universal recovery (SSH)**.

---

## 1. Update Now / Git

### What people saw
- **`would clobber existing tag`** when clicking **Update Now**.
- Sometimes **`dev -> origin/dev`** (or other refs) updating even when they only wanted a release tag.

### Root cause
1. **Bulk `git fetch --tags`** (old behavior) tried to move every local tag to match GitHub; if a field box had an old **`v0.3.8-alpha`** (etc.) pointing at a different commit than GitHub, Git refused and the whole update failed.
2. **v0.4.0** fixed part of this by fetching **only** the latest tag from the GitHub API — but **explicit refspecs are additive** with **`remote.origin.fetch`**. So **`git fetch origin +refs/tags/foo:refs/tags/foo`** could **still** run the default **`+refs/heads/*:refs/remotes/origin/*`** and touch other tags → clobber could persist.
3. **v0.4.1** clears **`remote.origin.fetch`** for that fetch (**`git -c remote.origin.fetch=`**), so **only** the intended refspec runs.

### SSH / README “universal recovery” lesson
- **`git fetch origin main`** is **not** safe if **`origin`** is a **fork**, typo, or stale mirror — **`origin/main`** can sit on **v0.2.4** forever.
- **Fix:** fetch **canonical** **`main`** from **`https://github.com/takwerx/infra-TAK.git`** and **`git checkout --force -B main FETCH_HEAD`** (see README). Optionally **`git remote set-url origin https://github.com/takwerx/infra-TAK.git`**.

### Process
- Run **[TESTING-UPDATES.md](TESTING-UPDATES.md)** on a test VPS **before** pushing a release **tag** (the tag drives the “Update Available” banner).

---

## 2. TAK Portal `TAK_URL` — IP vs FQDN (v0.4.2)

### What people saw
- TAK Portal **not “connected”** to TAK / dashboard metrics dead.
- **QR / certificate enrollment**: **“TAK server’s identity could not be verified”** (or similar TLS errors).
- **`TAK_URL`** in portal settings showed **VPS IP** instead of **`takserver.<domain>`**.

### Root cause
- **`_takportal_build_settings_dict()`** preferred **`server_ip`** for container→host reachability (Docker), but TAK’s HTTPS cert is issued for a **hostname**, not the raw IP. **Node.js** verifies the name → mismatch → failures on strict TLS paths (enrollment, API calls).

### Fix (code)
- When **`fqdn`** is set, **`TAK_URL`** host prefers **`_get_takserver_host()`** (e.g. **`takserver.<fqdn>`**); IP remains fallback for no-domain installs.

### Operator action (critical for upgrades)
- **Upgrading the console does not rewrite `settings.json` inside the portal container.** After pulling **v0.4.2+**, operators must open **TAK Portal** in infra-TAK and click **Update config** (🔄). Fresh **deploy** of TAK Portal on a new install already runs the same builder — **Update config** is mainly for **existing** containers.
- If trust still fails: **Sync TAK Server CA** on the TAK Portal page.

### Slack-style TL;DR (v0.3.8 → v0.4.2)
- Update console to **v0.4.2**, then **Authentik → Update config** once and **TAK Portal → Update config** once. **Resync LDAP** on TAK page if **8446** is weird. Some **IN/OUT group / cert UI** weirdness may clear once Portal ↔ TAK and LDAP are consistent (not a separate marketed “IN/OUT fix”).

---

## 3. Authentik (v0.3.9+ track, still relevant for skip-upgraders)

Documented at length in [RELEASE-v0.3.9-alpha.md](RELEASE-v0.3.9-alpha.md) and summarized in [RELEASE-v0.4.2-alpha.md](RELEASE-v0.4.2-alpha.md): **webadmin** LDAP-only with Authentik, PostgreSQL tuning reset on **Update config**, final LDAP SA bind on local full deploy, no accidental full deploy when FQDN missing, **8446** / XML / SMTP+LDAP re-heal, etc.

**After a big jump:** run **Authentik → Update config** once (in addition to TAK Portal **Update config** per §2).

---

## 4. TAK Server — `ERROR` spam on port **8089**

### What people saw
- Many **red** lines: **`NotSslRecordException`**, **`PEER_DID_NOT_RETURN_A_CERTIFICATE`**, **`NO_SHARED_CIPHER`**, etc. **Remote** IPs in **185.247.x** / **87.236.x** (and similar), **local port 8089**.

### What it is
- **Public CoT/TLS port** on the internet. **Bots and scanners** open TCP, send **non-TLS** or wrong TLS → server logs **ERROR** and drops the connection. **Normal** for any exposed TAK streaming port.

### Does it break anything?
- **Usually no.** Legitimate clients show up as **INFO** (e.g. **DistributedSubscriptionManager**, ANDROID clients). Errors are **failed** handshakes, not corruption of the server.

### When to care
- **Disk** if logs never rotate and volume is extreme.
- **DDoS**-level connection floods (rare for typical noise).

### Guard Dog: `8089 unhealthy` restart loop (test10 pattern)

**Symptom:** `/var/log/takguard/restarts.log` lines like **`restart | 8089 unhealthy`** every ~15–20 minutes, often with **high load** and healthy free RAM.

**Cause:** **`tak-8089-watch.sh`** used to treat the TCP **accept queue** as “bad” when **`Recv-Q >= Send-Q - 5`**. Internet scanners partially fill the queue on **public 8089** all day → **false “unhealthy”** → **Guard Dog restarts TAK** → brief relief → repeat after grace period.

**Fix (repo):** Shipped **v0.4.3-alpha**: backlog must reach **≥95%** of the limit before tripping; **5** consecutive failures before restart (see `scripts/guarddog/tak-8089-watch.sh`). The same release also tightens **Authentik** checks (`/-/health/live/` + short retry) and improves **Auto-VACUUM** skip lines in **`restarts.log`**. **Update infra-TAK** then **↻ Update Guard Dog** so `/opt/tak-guarddog/` picks up the scripts.

---

## 5. On-prem fresh install — enrollment SSL failures (May 2026)

Both failures were seen on a fresh install on an on-prem server (home lab, dynamic IP, Ubuntu Desktop 22.04). The server had a valid FQDN and Caddy issued a Let's Encrypt cert. The install completed without obvious error but ATAK failed at two sequential stages.

---

### Failure A — "TAK server's identity could not be verified" (enrollment, port 8446)

**Symptom:** ATAK shows **"TAK server's identity could not be verified"** when scanning the enrollment QR code. Every device, every user. `openssl s_client -connect <fqdn>:8446` returns the **self-signed TAK PKI cert** instead of a Let's Encrypt cert.

**Root cause:** `install_le_cert_on_8446()` runs during the initial TAK Server deploy, but it is gated on the LE cert files already existing in Caddy's storage. On a fresh install Caddy may not have completed the ACME challenge yet — the function logs "Skipping 8446 cert install — DNS may not be propagated yet" and exits early. TAK Server therefore stays on its self-signed PKI cert on 8446. ATAK cannot verify a self-signed cert during enrollment → identity error.

**Diagnostic:**
```bash
openssl s_client -connect <fqdn>:8446 -servername <fqdn> </dev/null 2>&1 | grep -E "subject|issuer"
# BAD:  issuer=..., CN = INTERMEDIATE_CA_01   (self-signed TAK PKI)
# GOOD: issuer=C = US, O = Let's Encrypt, CN = E7
```

**Fix:** Click **"Patch CoreConfig"** on the TAK Server page in the console. This calls `install_le_cert_on_8446(wait_for_cert=False)` — by this point Caddy has the LE cert, the function converts it to a JKS and patches `CoreConfig.xml`, then restarts TAK Server. Wait ~2 minutes and re-verify with `openssl s_client` before re-testing enrollment.

**Why this doesn't happen on VPS installs:** VPS installs typically have DNS propagated before deploy runs; Caddy gets the cert quickly and `wait_for_cert=True` succeeds in its 120-second polling loop. Home lab / dynamic DNS installs can lag.

---

### Failure B — SSL error on ATAK after enrollment "succeeded" (CoT, port 8089)

**Symptom:** ATAK reports enrollment success and downloads a client cert, but then immediately shows an **SSL error** when trying to connect for CoT traffic. TAK Server messaging log (`/opt/tak/logs/takserver-messaging.log`) shows repeated:
```
SSLHandshakeException: General OpenSslEngine problem ... Certificate error: peer not verified
```
from the ATAK device's IP, hitting port 8089, every ~16 seconds (ATAK retry loop).

**What rules it out (confirmed via diagnostics):**
- CRL is empty (`No Revoked Certificates`) — cert is not revoked
- Truststore (`truststore-INTERMEDIATE_CA_01.jks`) has correct entries: `mykey` = INTERMEDIATE_CA_01, `root-ca` = ROOT_CA_01
- On-disk `INTERMEDIATE_CA_01.pem` fingerprint matches the truststore's `mykey` entry exactly
- `CoreConfig.xml` `<tls>` block points at the correct truststore file
- Downloaded client cert issuer = INTERMEDIATE_CA_01 (correct CA, issued today)
- Ubuntu Desktop vs Server OpenSSL: identical, not the cause

**Root cause:** Residual PKI state corruption from the rough first deploy (8446 served self-signed during first enrollment attempt). The JKS files are internally inconsistent in a way that SHA-256 fingerprint comparisons do not surface — OpenSSL reports "OK" but Java's JSSE/Netty rejects the chain during mutual TLS.

**Fix:** Use the **CA Rotation tool** in the TAK Server page of the console ("Rotate Intermediate CA"). The tool (lines ~27590–27836 in `app.py`) automatically:
1. Generates a new intermediate CA
2. Re-signs the server cert and all existing client certs
3. Rebuilds the truststore (new + old CA for transition)
4. Patches `CoreConfig.xml`
5. Syncs admin.p12 + CA chain to TAK Portal and restarts it
6. Restarts TAK Server

After rotation completes, verify 8446 is still on LE cert:
```bash
openssl s_client -connect <fqdn>:8446 -servername <fqdn> </dev/null 2>&1 | grep issuer
# Must show: issuer=..., O = Let's Encrypt
```
If it reverted to the TAK PKI cert, click "Patch CoreConfig" again to reinstall the LE cert on 8446.

Then: fully remove the server entry from ATAK (Settings → Network Connections → Remove + clear Device Certificate + Force Stop ATAK), generate a fresh QR code, and re-enroll.

**Why CA rotation fixes it:** rebuilds every JKS from scratch using the current OpenSSL binary and `_patch_openssl_string_mask()` settings, eliminating any encoding inconsistency baked in during the problematic first deploy.

---

| Topic | Doc / location |
|--------|----------------|
| Universal SSH recovery (canonical `main`) | [README — Universal recovery (SSH)](../README.md#universal-recovery-ssh) |
| Shallow clone / branch fetch | [PULL-AND-RESTART.md](PULL-AND-RESTART.md) |
| Pre-tag **Update Now** test | [TESTING-UPDATES.md](TESTING-UPDATES.md) |
| Release notes | [RELEASE-v0.4.3-alpha.md](RELEASE-v0.4.3-alpha.md), [RELEASE-v0.4.2-alpha.md](RELEASE-v0.4.2-alpha.md) |
| Selective `main` + tag | [COMMANDS.md](COMMANDS.md) |
| 8446 / CoreConfig patching | [WORKFLOW-8446-WEBADMIN.md](WORKFLOW-8446-WEBADMIN.md) |
| Deep LDAP / Authentik history | [HANDOFF-LDAP-AUTHENTIK.md](HANDOFF-LDAP-AUTHENTIK.md) (internal; may not be on `main`) |

---

## 6. Changelog pointer (infra-TAK versions)

- **v0.4.0** — API latest tag; single-tag fetch (clobber partial fix).
- **v0.4.1** — **`remote.origin.fetch=`** for Update Now fetches (clobber fix completed).
- **v0.4.2** — TAK Portal **`TAK_URL`** prefers FQDN; release notes stress **TAK Portal → Update config** after upgrade.
- **v0.4.3** — Guard Dog **8089** (95% backlog, 5 fails), **Authentik** health retry + **`/-/health/live/`**, **Auto-VACUUM** skip logs; **↻ Update Guard Dog** after console upgrade.
- **v0.4.4** — Guard Dog **8089** TCP connect probe replaces queue-depth (stops restart loops on scanner-heavy ports); **↻ Update Guard Dog** after console upgrade.

---

*This file is an operator/maintainer digest. It is not a substitute for running **TESTING-UPDATES.md** before tagging.*
