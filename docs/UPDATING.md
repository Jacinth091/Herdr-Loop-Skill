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
- **"Update available: vX.Y.Z":** Indicates an official, verified release tag and assets exist on GitHub.

> [!NOTE]
> Everyday Git commits on `main` do not represent published releases. Only annotated Git tags (`v*`) with corresponding GitHub Release assets and SHA-256 checksums represent releases.

---

## 2. Safe Explicit Update

To update your installed skills to the latest published release:

### Preview First (Dry-Run)
```bash
python tools/update.py --dry-run
```

### Apply Update
```bash
python tools/update.py
```

### What Happens During Update:
1. **Integrity Check:** The updater downloads the release archive and verifies its SHA-256 digest against `SHA256SUMS`.
2. **Safety Scan:** Archive members are scanned to ensure no path traversal (`..`) or unsafe directories exist.
3. **Automated Backup:** All currently installed managed skills across Claude, Codex, and Agy are backed up to a timestamped folder under `~/.herdr-backups/backup-<timestamp>/`.
4. **Staged Replacement:** New skills are installed. Unmanaged user folders and unrelated skills are never touched.
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
# Cross-platform:
npx . --uninstall

# Windows:
powershell -File .\install.ps1 -Uninstall

# POSIX:
sh ./install.sh --uninstall
```
Only directories containing `.herdr-role-pack-managed` will be removed. All unmanaged directories and run evidence in `.herdr-loop/` remain preserved.
