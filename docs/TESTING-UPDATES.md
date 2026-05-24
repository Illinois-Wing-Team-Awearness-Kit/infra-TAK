# Testing the Update Now button before a release

**Rule:** Do **not** push a Git **tag** until you have run this protocol on a test VPS and **Update Now** succeeds end-to-end. Pushing a tag is what shows customers the banner; mistakes are public.

The Update Now button uses a **different code path** from `git pull origin dev`. Pulling dev and restarting does **not** validate the updater.

## How Update Now works (current code)

1. Console asks the GitHub **API** for tags and picks the **highest** release tag (e.g. `v0.4.5-alpha`).
2. If that version is newer than running `VERSION`, it shows **Update Available**.
3. On **Update Now**, it runs **`git -c remote.origin.fetch= fetch origin +refs/tags/<that-tag>:refs/tags/<that-tag>`** (default fetch disabled so Git does not also update every remote-tracking ref / tag), then **`git checkout --force`** that tag, then restarts the service. If tag resolution fails, it falls back to an isolated fetch of **`main`** as `origin/main`.

Tags drive the banner. Pushing to **`dev`** or **`main`** alone does **not** trigger customer updates. Pushing a **tag** does.

## Why “it worked for months” then broke

Older updaters used **`git fetch --tags`**. That updates **every** tag. If a field box has a **local** `v0.3.8-alpha` (or similar) pointing at a **different commit** than GitHub, Git refuses with **`would clobber existing tag`** and the whole update fails. Clones where local tags always matched GitHub never saw it.

**v0.4.0+** resolved the latest tag via the API and passed a single-tag refspec. **v0.4.1+** also clears **`remote.origin.fetch`** for that fetch, because explicit refspecs are **additive** with the default — without that, some installs still hit **`would clobber existing tag`**.

**Chicken-and-egg:** A box still running **pre-v0.4.0** code cannot get that fix via **Update Now** if fetch already fails there. **One-time** recovery: [PULL-AND-RESTART.md](PULL-AND-RESTART.md) (or SSH: `git fetch` + `git checkout --force v0.4.5-alpha` + restart console). After that, **Update Now** uses the isolated-fetch path.

---

## Pre-release test (zero customer exposure)

### Prerequisites

- Test VPS with the console installed (same style as production).
- SSH access.
- New updater logic is **committed and pushed to `dev`** (no new **tag** yet).

### Steps

**1. Sync test VPS to `dev` (no mass tag fetch)**

Use branch sync only so you don’t simulate a broken “fetch all tags” on the tester.

```bash
cd "$(grep -oP 'WorkingDirectory=\K.*' /etc/systemd/system/takwerx-console.service)"
git fetch origin
git checkout -B dev origin/dev
sudo systemctl restart takwerx-console
```

*(If your clone is shallow/single-branch, follow [PULL-AND-RESTART.md](PULL-AND-RESTART.md) for `remote set-branches` / fetch first.)*

**2. Fake the version down**

```bash
sed -i 's/VERSION = "[^"]*"/VERSION = "0.0.1"/' app.py
sudo systemctl restart takwerx-console
```

The console now reports `0.0.1` and treats the **current highest tag on GitHub** as an update target.

**3. Open the console in a browser**

Confirm **Update Available** (refresh or wait for cache; use **Check for new release** if present).

**4. Click Update Now**

This is the **exact** customer path. Check:

- Clean restart (brief 502, then UI loads).
- Sidebar **VERSION** matches the tag you expected.
- No red error banner.

**5. If it passed — restore the test VPS to real `dev`**

```bash
cd "$(grep -oP 'WorkingDirectory=\K.*' /etc/systemd/system/takwerx-console.service)"
git checkout -- app.py
git fetch origin
git checkout -B dev origin/dev
sudo systemctl restart takwerx-console
```

**6. Cut the release (selective `main` + tag)**

Use **[COMMANDS.md](COMMANDS.md) → “Merge dev → main (selective — release only)”** — full path list, Python `VERSION` check, commit, **`git push origin main`**, then **`git tag`** / **`git push origin <tag>`**.

**7. If step 4 failed — fix on `dev`, repeat from step 1**

No tag was pushed; customers were not prompted.

### Optional: Guard Dog updates email (v0.2.7-alpha+)

After **Update Now** succeeds, **↻ Update Guard Dog** once. To exercise the updates email path:

```bash
sudo rm -f /var/lib/takguard/updates_notified
sudo /opt/tak-guarddog/tak-updates-watch.sh
tail -5 /var/log/takguard/updates.log
```

Use **Guard Dog → Send test email** first if Email Relay is not verified.

---

## What this catches

- Updater `git` failures (tag clobber, shallow clone, wrong ref).
- Detached HEAD / stale merge-rebase state (`update_apply` aborts those first).
- Wrong **VERSION** vs tag after restart.
- Service restart failures.

## See also

- **Node-RED / Configurator only** (checkout `dev`, `nodered/deploy.sh --no-pull`, curl + UI smoke tests): **[TESTING-NODERED-DEPLOYS.md](TESTING-NODERED-DEPLOYS.md)**

## Quick reference

| Action | Who sees “Update Available”? |
|--------|-------------------------------|
| Push `dev` | Nobody |
| Push `main` | Nobody |
| Push a **tag** | Everyone whose `VERSION` is older than that release |

**Release safety:** Before `git tag`, `app.py` **`VERSION`** must equal the tag without the `v` (e.g. tag `v0.4.3-alpha` → `VERSION = "0.4.5-alpha"`). Copy-paste check: [COMMANDS.md](COMMANDS.md) release block.

---

## What went wrong if you skipped this

Rushing **tag → customers** without step 4 can ship a broken **Update Now**. That is not a “you’re stupid” problem — it is a **process** problem. This doc **is** the process; follow it every time.
