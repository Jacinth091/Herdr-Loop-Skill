# Release Guide for Herdr Role Pack

This document details the release process, version authority rules, packaging requirements, and verification gates for releasing new versions of the **Herdr Role Pack**.

---

## 1. Version Authority Rules

- **Git Tags are the Release Anchor:** A release is uniquely identified by an annotated SemVer Git tag formatted as `vX.Y.Z` (e.g. `v2.8.0`).
- **Single Canonical Source:** The version string in `manifest.json` is authoritative. `package.json` must mirror this exact version.
- **Tag-Manifest Synchronization:** The release pipeline validates that the pushed Git tag exactly matches `v` + the version declared in `manifest.json`. A mismatch immediately aborts the release without publishing any assets.
- **Ordinary Commits are Not Releases:** Routine commits on `main` carry conventional commit messages and do not bump the version or create releases.

---

## 2. Release Artifacts

Every published GitHub Release must include the following verified artifacts:
1. `herdr-role-pack-vX.Y.Z.zip`: Complete archive containing all six managed role skills, platform installers, tools, and license.
2. `herdr-controller-vX.Y.Z.zip`: Standalone controller skill package for environments supporting single-skill imports.
3. `SHA256SUMS`: Cryptographic SHA-256 digests for all generated archive assets.
4. Release Notes: Human-readable changelog detailing new capabilities, bug fixes, breaking changes, and known limitations.

---

## 3. Pre-Release Quality Gates

Before creating a release tag, maintainers must verify:
- [ ] All unit tests pass cleanly: `npm test` or `python -m unittest ...`.
- [ ] Installer dry-runs succeed across all supported platforms (`install.ps1 -DryRun`, `install.sh --dry-run`, `node bin/install.js --dry-run`).
- [ ] Packaging dry-run succeeds with zero path escapes, no duplicate archive members, and no unmanaged artifacts (`.herdr-loop/`, `__pycache__`, `.env`).
- [ ] `CHANGELOG.md` has a `## [X.Y.Z] - YYYY-MM-DD` section (the release workflow refuses the tag without `## [X.Y.Z]`).
- [ ] Working tree is clean and all commits are merged to `main`.

---

## 4. Step-by-Step Release Procedure

### Step 1: Update Version & Changelog
Update `manifest.json` and `package.json` to the target version `X.Y.Z`.
Move the notes in `CHANGELOG.md` from `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`.

Commit the changes:
```bash
git commit -am "chore(release): prepare vX.Y.Z"
git push origin main
```

### Step 2: Test Packaging Locally
Run the packaging utility to build and verify assets:
```bash
python tools/package.py --version X.Y.Z --output-dir dist/
```
Verify the contents of `dist/`:
- Check that `dist/herdr-role-pack-vX.Y.Z.zip` extracts cleanly.
- Verify `dist/SHA256SUMS` contains valid hashes.

### Step 3: Create and Push Annotated Tag
```bash
git tag -a "vX.Y.Z" -m "Release vX.Y.Z"
git push origin "vX.Y.Z"
```

### Step 4: Automated GitHub Actions Release
Pushing the `v*` tag triggers `.github/workflows/release.yml`.
The workflow:
1. Verifies the tag is strict `vX.Y.Z`, matches `manifest.json`, `package.json` and a `CHANGELOG.md` section, and points at a commit on `main` (otherwise nothing is published).
2. Runs the full test suite and installer dry-runs on Ubuntu and Windows at the tagged commit.
3. Runs `tools/package.py` to produce release ZIPs and `SHA256SUMS`.
4. Creates the GitHub Release with attached assets and release notes using `gh release create`.
