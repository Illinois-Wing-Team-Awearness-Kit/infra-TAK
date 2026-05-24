# Spike: Flat-File `nodered` User for DataSync (Phase 0)

> **Status:** PENDING — operator must run tests against a live TAK Server and fill in results below.
>
> **Plan reference:** `~/.cursor/plans/nodered_hardening_flatfile_*.plan.md`
>
> **Decision gate:** if **T1 and T3 both pass**, proceed to **Phase 1A** (flat-file migration). Otherwise, fall back to **Phase 1B** (admin-cert hardening only).

## Why this spike exists

infra-TAK's Node-RED currently uses `admin.pem` (the TAK Server bootstrap admin cert) for DataSync writes. We tested LDAP-based scoped certs in April 2026 (`nodered-global-datasyncfeed`) and they were blocked by an unfixed TAK Server bug: x509 certs that resolve groups via LDAP get OUT-only direction even when the LDAP base group says BOTH (see `docs/GIS-TAK-DATASYNC-HANDOFF.md` 2026-04-12 entry).

**Hypothesis:** A **flat-file** TAK Server user (defined in `UserAuthenticationFile.xml`, like `admin` itself) declares its groups directly in XML with explicit direction (`groupListIN` / `groupListOUT`). This sidesteps the LDAP-resolution path entirely. If true:

- Node-RED can present a least-privilege cert (`nodered.pem`) instead of admin
- Node-RED can be the **creator** of DataSync missions, making it the natural owner — no admin-only role-elevation hack needed
- Audit attribution becomes real (TAK Server logs show `nodered`, not `admin`)
- Cert leak blast radius shrinks from "full TAK admin authority" to "DataSync feeds in the configured group"

**Caveat:** this is unproven on TAK Server 5.x. The April 2026 finding said "x509 cert-only auth" gets OUT-only. Whether that bug is in the LDAP-resolution path specifically (so flat-file sidesteps it) or in cert auth generally (so flat-file also hits it) is exactly what these tests determine.

## Prerequisites

- SSH access to the TAK Server host as a user with `sudo` to `tak`
- TAK Server 5.x running (verify with `sudo systemctl status takserver`)
- A working `admin.pem`/`admin.key` (used to create the test mission and verify field-user access)
- `curl` and `openssl` on the test machine
- Optional: an ATAK device pre-enrolled with a non-admin LDAP user, for end-to-end verification

## Setup

### 1. Generate the `nodered` client cert

```bash
sudo -u tak bash -c 'cd /opt/tak/certs && ./makeCert.sh client nodered'
```

Expected output: `/opt/tak/certs/files/nodered.pem`, `nodered.key`, and `nodered.p12` created. Default password: `atakatak`.

### 2. Determine the flat-file path on this install

TAK Server 5.x uses one of these locations:

```bash
ls -la /opt/tak/UserAuthenticationFile.xml \
       /opt/tak/UserAuthentication.xml 2>/dev/null
```

Whichever exists is the file. (If neither exists, file auth is disabled in CoreConfig — see "Risks" at the bottom of this doc.)

### 3. Add the `nodered` user to the flat file

**BACKUP FIRST:**
```bash
sudo cp /opt/tak/UserAuthenticationFile.xml /opt/tak/UserAuthenticationFile.xml.pre-nodered-spike
```

Edit and add a `<User>` block inside `<UserAuthentication>`:

```xml
<User identifier="nodered" passwordHashed="false" role="ROLE_USER">
    <groupListIN>DATASYNC-FEEDS</groupListIN>
    <groupListOUT>DATASYNC-FEEDS</groupListOUT>
</User>
```

Notes:
- Replace `DATASYNC-FEEDS` with the actual group name your DataSync missions use
- `role="ROLE_USER"` is intentional — we do NOT want this user to have ROLE_ADMIN. The whole point is least-privilege
- If the schema validator rejects `groupListIN`/`groupListOUT` (older TAK Server versions used `<groupList>` only), try a single `<groupList>DATASYNC-FEEDS</groupList>` and document which schema this install accepts

### 4. Reload TAK Server

Quickest path:
```bash
sudo systemctl restart takserver
sleep 30
sudo systemctl status takserver
```

If you want to try `UserManager.jar` hot-reload instead (avoids downtime):
```bash
sudo -u tak java -jar /opt/tak/utils/UserManager.jar listusers | grep nodered
# If listed: hot-reload worked. If not: restart was required.
```

Document which mechanism worked in **Result T0** below.

## Tests

Run each curl from any host that can reach TAK Server's `:8443`. Examples assume you've copied `nodered.pem` and `nodered.key` to a working directory and decrypted the key:

```bash
# Decrypt the key once for ease of testing (do NOT commit the unencrypted key)
openssl rsa -in nodered.key -out nodered.nopass.key
# Password prompt: atakatak (default)

NODERED_CERT=./nodered.pem
NODERED_KEY=./nodered.nopass.key
TAK=https://<tak-server-host>:8443
GROUP=DATASYNC-FEEDS  # adjust to your install
```

### T0 — User loaded correctly (sanity check)

```bash
sudo -u tak java -jar /opt/tak/utils/UserManager.jar listusers
```

**Expected:** `nodered` appears in the list. If not, the flat-file edit didn't load. Re-check XML syntax.

**Result:** _to be filled in_

### T1 — Group resolution (CRITICAL — gates Phase 1A)

```bash
curl -k --cert "$NODERED_CERT" --key "$NODERED_KEY" \
  "$TAK/Marti/api/groups/all"
```

**Expected (PASS):** Response includes `DATASYNC-FEEDS` with `direction` showing both `IN` and `OUT` (or two entries, one per direction).

**Expected (FAIL):** Only `OUT` direction returned, or empty `data: []`. **If this fails, flat-file users hit the same x509 group bug as LDAP users — Phase 1A is dead, proceed to Phase 1B.**

**Result:** _to be filled in_

```text
<paste curl response here>
```

### T2 — Mission creation as `nodered`

```bash
curl -k --cert "$NODERED_CERT" --key "$NODERED_KEY" \
  -X PUT "$TAK/Marti/api/missions/spike-test?creatorUid=nodered&group=$GROUP&defaultRole=MISSION_READONLY_SUBSCRIBER" \
  -H "Content-Type: application/json"
```

**Expected (PASS):** HTTP 200 or 201, body contains the new mission with `creatorUid: "nodered"` and `defaultRole: "MISSION_READONLY_SUBSCRIBER"`.

**Verify in TAK Portal:** open `https://<your-fqdn>/` (TAK Portal), DataSync section, confirm `spike-test` mission is listed and shows `nodered` as creator/owner.

**Result:** _to be filled in_

### T3 — Stream + register UID (CRITICAL — gates Phase 1A)

This is the actual DataSync write path that admin currently performs.

**Step 1: Stream a CoT event** with `<marti><dest mission="spike-test"/></marti>`. Easiest path is a one-off Python or `openssl s_client` script:

```bash
cat > /tmp/spike-cot.xml <<'EOF'
<?xml version='1.0' encoding='UTF-8' standalone='yes'?>
<event version="2.0" uid="spike-test-uid-001" type="a-f-G-U-C" how="m-g" time="2026-05-05T00:00:00.000Z" start="2026-05-05T00:00:00.000Z" stale="2026-05-06T00:00:00.000Z">
  <point lat="34.05" lon="-118.25" hae="0" ce="9999999" le="9999999"/>
  <detail>
    <contact callsign="SPIKE-TEST"/>
    <__group name="Yellow" role="Team Member"/>
    <marti><dest mission="spike-test"/></marti>
  </detail>
</event>
EOF

# Stream via TLS to TAK Server port 8089 with nodered cert
{ cat /tmp/spike-cot.xml; sleep 2; } | \
  openssl s_client -connect <tak-host>:8089 \
    -cert "$NODERED_CERT" -key "$NODERED_KEY" -quiet 2>/dev/null
```

**Step 2: Wait 5 seconds for `CotCacheHelper` to register the UID, then PUT it into the mission:**

```bash
sleep 5

curl -k --cert "$NODERED_CERT" --key "$NODERED_KEY" \
  -X PUT "$TAK/Marti/api/missions/spike-test/contents?creatorUid=nodered" \
  -H "Content-Type: application/json" \
  -d '{"uids":["spike-test-uid-001"]}' \
  -v
```

**Expected (PASS):** HTTP 200, response body empty or contains the registered UID. UID appears in TAK Portal's `spike-test` mission as a Map Item (NOT as a File).

**Expected (FAIL):** HTTP 403 (group direction bug bites) or 500 (UID not in cache → streaming step didn't work or wrong cert presented). **If 403, Phase 1A is dead.**

**Result:** _to be filled in_

```text
<paste curl -v output here, especially the response status>
```

### T4 — Field-user read-only verification

Subscribe a separate (LDAP-based) field user cert to `spike-test`:

```bash
FIELD_CERT=./<some-field-user>.pem
FIELD_KEY=./<some-field-user>.key  # or .nopass.key

curl -k --cert "$FIELD_CERT" --key "$FIELD_KEY" \
  -X PUT "$TAK/Marti/api/missions/spike-test/subscription?uid=<field-user-cn>"
```

**Expected (PASS):** HTTP 200. Subscription role assigned = `MISSION_READONLY_SUBSCRIBER` (the mission's `defaultRole`).

**Verify on ATAK:** the field user's device shows the `spike-test` mission with `SPIKE-TEST` callsign as a Map Item. They can see it, can NOT delete it from the mission.

**Result:** _to be filled in_

### T5 — Delete by `nodered`

```bash
curl -k --cert "$NODERED_CERT" --key "$NODERED_KEY" \
  -X DELETE "$TAK/Marti/api/missions/spike-test/contents?uid=spike-test-uid-001&creatorUid=nodered" \
  -v
```

**Expected (PASS):** HTTP 200. UID disappears from `spike-test` mission. ATAK reflects the deletion.

**Result:** _to be filled in_

### T6 — Field user write attempt (should be denied)

Using the field user cert from T4:

```bash
curl -k --cert "$FIELD_CERT" --key "$FIELD_KEY" \
  -X PUT "$TAK/Marti/api/missions/spike-test/contents?creatorUid=<field-user-cn>" \
  -H "Content-Type: application/json" \
  -d '{"uids":["bogus"]}' \
  -v
```

**Expected (PASS):** HTTP 403 — confirms `MISSION_READONLY_SUBSCRIBER` enforcement works. If this returns 200, the read-only role isn't being enforced and we have a bigger problem to fix first.

**Result:** _to be filled in_

## Cleanup

After tests complete:

```bash
# Delete the test mission
curl -k --cert "$NODERED_CERT" --key "$NODERED_KEY" \
  -X DELETE "$TAK/Marti/api/missions/spike-test?creatorUid=nodered"

# Optional: leave the nodered user in place (Phase 1A will use it) OR remove if rolling back
# To remove:
sudo cp /opt/tak/UserAuthenticationFile.xml.pre-nodered-spike /opt/tak/UserAuthenticationFile.xml
sudo systemctl restart takserver
```

Securely delete the unencrypted key copy:
```bash
shred -u nodered.nopass.key
```

## Decision matrix

| T1 result | T3 result | Decision |
|-----------|-----------|----------|
| BOTH (IN+OUT) | 200 | **Phase 1A** — migrate Node-RED to flat-file `nodered`. The bug doesn't apply to flat-file users. |
| OUT only | 403 | **Phase 1B** — flat-file hits the same bug. Stay on admin, harden runtime. |
| BOTH | 403 | Investigate further: groups resolve correctly but writes still denied. May be CoreConfig auth chaining issue. Document and consult Justin/Josh. |
| OUT only | 200 | Unexpected: writes work despite OUT-only group? Could indicate the "owner is creator" rule overrides group direction for this mission. Document — may still enable Phase 1A. |

## Risks & open questions to record during testing

1. **CoreConfig file-auth chaining.** If the install was set up with `default="ldap"` and the `<file>` auth element is missing, flat-file users may not even be considered during cert lookup. Check `/opt/tak/CoreConfig.xml` for an `<auth>` block referencing `<file>`. Add it if missing — out of scope for the spike, but document if we have to.

2. **`UserManager.jar` reload.** Does adding a flat-file user require a TAK Server restart, or does `UserManager.jar usermod` (or any other `UserManager` command) hot-reload the file? Test and document. If restart is required, Phase 1A bootstrap needs a `systemctl restart takserver` call which has operator UX implications.

3. **Mission visibility in TAK Portal.** Does Portal's mission list show `spike-test` cleanly when the creator (`nodered`) is a flat-file user that doesn't exist in LDAP/Authentik? It should — Portal pulls missions from TAK Server's DB independent of user source — but verify and document.

4. **Schema variants.** Some TAK Server versions accept `<groupList>` only; others accept `<groupListIN>`/`<groupListOUT>`. Document which works on this install. If only `<groupList>` is supported, we may need to test whether direction inference works correctly for flat-file users (it should, since LDAP isn't involved).

5. **Pre-existing missions.** Phase 1A only fixes _new_ missions. Existing admin-owned missions stay admin-owned. After spike, decide: do we add a one-time bulk-transfer step (admin grants `nodered` MISSION_OWNER on each existing mission) or accept hybrid state?

## Once tests are complete

1. Fill in every "Result:" section above with actual output
2. Append a short "Conclusion" section: which decision-matrix row matched, what we're doing next
3. Commit this file (results and all) to `docs/SPIKE-flatfile-nodered.md` so the trail is permanent
4. Update the corresponding plan todo (`phase0-spike`) to completed in your tracker
5. Open the appropriate follow-up: Phase 1A migration PR or Phase 1B fallback PR
