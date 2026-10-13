# Updating and Rollback Guide for Herdr Role Pack

This guide explains how to check for, install, and safely roll back updates for the **Herdr Role Pack**.

---

## 1. Checking for Updates

To check if a newer published release is available on GitHub:

```bash
# Check installed vs. latest published version:
python tools/update.py --check
```

### Understanding Version Status
- **"Development version / no published releases found":** Indicates you are working from a development clone of the repository. No official release tag has been published yet.
- **"Update available: vX.Y.Z":** A newer published release exists than at least one installed managed skill.
- **"Up to date":** Every managed skill is already at the latest release (version read from each skill's `.herdr-role-pack-managed` marker). `python tools/update.py --force` reinstalls anyway.

> [!NOTE]
> Everyday Git commits on `main` do not represent published releases. Only annotated Git tags (`v*`) with corresponding GitHub Release assets and SHA-256 checksums represent releases.

---

## 2. Safe Explicit Update

To update your installed skills to the latest published release:

**Requirements:** Node.js on `PATH` (the updater runs the release's `bin/install.js`), and, because the repository is private, GitHub access: run `gh auth login` once, or set `GITHUB_TOKEN`/`GH_TOKEN`. Without access the updater stops with `AUTH_REQUIRED` or `ACCESS_DENIED`; it never reports "no releases" for an auth failure. The token is sent only to GitHub, never to the asset download host.

### Preview First (Dry-Run)
```bash
python tools/update.py --dry-run
```

### Apply Update
```bash
python tools/update.py
```

### What Happens During Update:
1. **Integrity Check:** The updater downloads the release archive and verifies its SHA-256 digest against the exact entry in `SHA256SUMS`. A release without `SHA256SUMS`, without an entry for the archive, or with a mismatch is refused before anything is extracted or backed up.
2. **Safety Scan:** Archive members are scanned to ensure no path traversal (`..`) or unsafe directories exist.
3. **Automated Backup:** All currently installed managed skills across Claude, Codex, and Agy are backed up to a timestamped folder under `~/.herdr-backups/backup-<timestamp>/`.
4. **Staged Replacement:** New skills are installed only for the agents (Claude, Codex, Agy) that already have the pack. Unmanaged user folders and unrelated skills are never touched.
5. **Fail-Closed Guarantee:** If any step fails during update, the previous installation is automatically restored from the backup.

---

## 3. Rollback on Demand

If an update causes unexpected issues or breaks your workflow, you can roll back immediately:

```bash
# List available local backups:
python tools/update.py --list-backups

# Restore previous installation:
python tools/update.py --rollback latest
```

Rollback restores the backed-up skills and removes the role slots that the update newly created. Before changing anything it saves the current state as a new backup, so `--rollback latest` run again undoes the rollback.

Rollback **refuses and changes nothing** when a destination folder is not pack-managed, when a managed skill was edited after the update, or when the backup no longer matches its manifest. Move your edits aside, then re-run.

You can also restore a specific backup directory by path:
```bash
python tools/update.py --rollback ~/.herdr-backups/backup-20261013-120000
```

---

## 4. In-Flight Session Safety

> [!WARNING]
> **Never update skills while a Herdr session is actively running.**
>
> Finish your ongoing task or halt the run before running `tools/update.py`. Updating skills will not mutate active `SESSION.md` or `ledger.sqlite` files, but running agents may cache previous role instructions.

---

## 5. Clean Uninstall

To remove pack-managed skills without deleting your project data, sessions, or custom skills:

```bash
# Cross-platform, no clone needed:
npx github:Jacinth091/Herdr-Loop-Skill --uninstall

# Cross-platform, from a local clone:
npx . --uninstall

# Windows:
powershell -File .\install.ps1 -Uninstall

# POSIX:
sh ./install.sh --uninstall
```
Only directories containing `.herdr-role-pack-managed` will be removed. All unmanaged directories and run evidence in `.herdr-loop/` remain preserved.
