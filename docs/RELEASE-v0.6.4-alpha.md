# infra-TAK v0.6.4-alpha — patch (Update Now / VERSION alignment)

Release date: April 2026

---

## Summary

**Patch release.** The **`v0.6.3-alpha` Git tag** pointed at a **`main` commit whose `app.py` still had `VERSION = "0.6.2-alpha"`**, so **Update Now** could report success while the sidebar stayed on **v0.6.2-alpha**, **`last_console_version` matched `VERSION`**, and **post-update auto-deploy** (Guard Dog + Node-RED sync) did not run.

**v0.6.4-alpha** bumps **`VERSION` to `0.6.4-alpha`** only. **Feature content** is unchanged from the **v0.6.3-alpha** Node-RED / Configurator work already on the branch — operators who missed the post-update path should use **Update Now** again after this tag is published (or recover per [README.md](../README.md) universal recovery).

---

## Maintainer notes

- **Before pushing a release tag:** confirm `git show <tag>:app.py | grep '^VERSION = '` matches the tag (e.g. **`0.6.4-alpha`** for **`v0.6.4-alpha`**). The selective-merge checklist in [COMMANDS.md](COMMANDS.md) includes a Python guard for this.
- **Replacing a bad tag:** delete the remote tag and move **`v0.6.4-alpha`** to the commit that contains this **`VERSION`** line; do not leave **`v0.6.3-alpha`** as the “latest” if it still lacks a matching **`VERSION`**.

---

## Related

- Full Node-RED / Configurator notes: [RELEASE-v0.6.3-alpha.md](RELEASE-v0.6.3-alpha.md)
- Pre-release **Update Now** test: [TESTING-UPDATES.md](TESTING-UPDATES.md)
