#!/usr/bin/env python3
"""
Explicit updater and rollback utility for Herdr Role Pack.
Queries published GitHub releases, verifies asset checksums and archive safety,
backs up managed installations, and provides safe rollback.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


REPO_OWNER = "Jacinth091"
REPO_NAME = "Herdr-Loop-Skill"
GITHUB_API_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases"
GITHUB_RELEASES_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases"

MANAGED_MARKER = ".herdr-role-pack-managed"
BACKUP_ROOT = Path.home() / ".herdr-backups"

MANAGED_SKILLS = [
    "herdr-engineering-loop",
    "herdr-manager",
    "herdr-delegator",
    "herdr-lead",
    "herdr-worker",
    "herdr-verifier",
    "herdr-critic",
]


def get_agent_skill_dirs() -> dict[str, Path]:
    home = Path.home()
    return {
        "claude": home / ".claude" / "skills",
        "codex": home / ".agents" / "skills",
        "agy": home / ".gemini" / "antigravity-cli" / "skills",
    }


def get_installed_versions() -> dict[str, dict[str, str | None]]:
    """Returns status of installed skills across all target agents."""
    results: dict[str, dict[str, str | None]] = {}
    for agent, base_dir in get_agent_skill_dirs().items():
        results[agent] = {}
        for skill in MANAGED_SKILLS:
            skill_dir = base_dir / skill
            marker = skill_dir / MANAGED_MARKER
            if skill_dir.exists():
                if marker.exists():
                    try:
                        content = marker.read_text(encoding="utf-8-sig").strip()
                    except Exception:
                        content = "MANAGED"
                    results[agent][skill] = content
                else:
                    results[agent][skill] = "UNMANAGED"
            else:
                results[agent][skill] = None
    return results


def parse_version(text: str | None) -> tuple[int, int, int] | None:
    """'Herdr Role Pack v2.8.0' / 'v2.8.0' -> (2, 8, 0). The 2.8.0 installers wrote 'v2.8', read as 2.8.0."""
    m = re.search(r"v(\d+)\.(\d+)(?:\.(\d+))?\b", text or "")
    return (int(m[1]), int(m[2]), int(m[3] or 0)) if m else None


def get_github_token() -> str | None:
    """Retrieves GitHub token from environment or active gh CLI authentication."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and token.strip():
        return token.strip()
    gh = shutil.which("gh")
    if gh:
        try:
            res = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=5, check=False)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass
    return None


class _DropAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """urllib forwards every header on redirect; never hand the GitHub token to the asset CDN."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            new.remove_header("Authorization")
        return new


_OPENER = urllib.request.build_opener(_DropAuthOnRedirect)


def fetch_latest_release(token: str | None = None, allow_prerelease: bool = False) -> dict | None:
    """Queries GitHub Releases API for the latest compatible release."""
    token = token if token is not None else get_github_token()
    headers = {
        "User-Agent": "Herdr-Role-Pack-Updater",
        "Accept": "application/vnd.github.v3+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(GITHUB_API_URL, headers=headers)
    try:
        with _OPENER.open(req, timeout=10) as response:
            if response.status != 200:
                return None
            data = json.loads(response.read().decode("utf-8"))
            if not isinstance(data, list) or not data:
                return None
            for release in data:
                if not allow_prerelease and release.get("prerelease", False):
                    continue
                if not release.get("draft", False):
                    return release
            return None
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise RuntimeError(
                "AUTH_REQUIRED: GitHub authentication required to access repository releases. "
                "Set GITHUB_TOKEN or authenticate via 'gh auth login'."
            )
        if e.code == 403:
            raise RuntimeError(
                "ACCESS_DENIED: Access denied to repository releases. Verify token permissions."
            )
        if e.code == 404:
            # A visible repo with no releases answers 200 []; 404 means the repo is not visible to us.
            if not token:
                raise RuntimeError(
                    "AUTH_REQUIRED: Received 404 from repository API. If this is a private repository, "
                    "authentication is required. Set GITHUB_TOKEN or authenticate via 'gh auth login'."
                )
            raise RuntimeError(
                "ACCESS_DENIED: Repository not visible with current credentials (404). "
                "Verify the token has access to this private repository."
            )
        raise


def calculate_sha256(file_path: Path) -> str:
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def parse_checksums_file(sums_text: str) -> dict[str, str]:
    """Strictly parses SHA256SUMS: '<64 hex sha>  [*]<filename>'."""
    entries: dict[str, str] = {}
    pattern = re.compile(r"^([0-9a-fA-F]{64})\s+[*]?(.+)$")
    for line in sums_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = pattern.match(line)
        if m:
            hash_val, fname = m.groups()
            name = Path(fname.strip()).name
            if entries.get(name, hash_val.lower()) != hash_val.lower():
                raise ValueError(f"INTEGRITY_ERROR: conflicting SHA256SUMS entries for '{name}'")
            entries[name] = hash_val.lower()
    return entries


def validate_archive_safety(zip_path: Path) -> list[str]:
    errors: list[str] = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        seen = set()
        for info in zf.infolist():
            name = info.filename
            if name in seen:
                errors.append(f"Duplicate entry: {name}")
            seen.add(name)
            if name.startswith("/") or name.startswith("\\") or ".." in Path(name).parts:
                errors.append(f"Unsafe path traversal: {name}")
    return errors


def download_asset(asset: dict, dest_path: Path, token: str | None = None) -> None:
    """Downloads a release asset, handling authenticated private repository downloads."""
    token = token if token is not None else get_github_token()

    # Try authenticated GitHub API asset download if token is present
    if token and asset.get("url"):
        req = urllib.request.Request(
            asset["url"],
            headers={
                "User-Agent": "Herdr-Role-Pack-Updater",
                "Authorization": f"Bearer {token}",
                "Accept": "application/octet-stream",
            },
        )
        try:
            with _OPENER.open(req, timeout=30) as resp, open(dest_path, "wb") as f:
                while chunk := resp.read(65536):
                    f.write(chunk)
            return
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise RuntimeError("ACCESS_DENIED: Failed to download release asset with current credentials.")

    # Fall back to browser download URL
    url = asset.get("browser_download_url")
    if not url:
        raise ValueError(f"No download URL available for asset {asset.get('name')}")
    req = urllib.request.Request(url, headers={"User-Agent": "Herdr-Role-Pack-Updater"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with _OPENER.open(req, timeout=30) as resp, open(dest_path, "wb") as f:
            while chunk := resp.read(65536):
                f.write(chunk)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise RuntimeError("AUTH_REQUIRED: GitHub authentication required to download release asset.")
        if e.code == 403:
            raise RuntimeError("ACCESS_DENIED: Access denied when downloading release asset.")
        if e.code == 404:
            raise RuntimeError(f"ASSET_NOT_FOUND: Release asset {asset.get('name')} not found (404).")
        raise


def hash_tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): calculate_sha256(p)
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def managed_slots() -> dict[str, Path]:
    """'agent/skill' -> path for every currently installed pack-managed skill."""
    return {
        f"{agent}/{skill}": base_dir / skill
        for agent, base_dir in get_agent_skill_dirs().items()
        for skill in MANAGED_SKILLS
        if (base_dir / skill / MANAGED_MARKER).exists()
    }


def backup_managed_skills() -> Path:
    """Copies every managed skill into a new backup dir with a per-file hash manifest."""
    from datetime import datetime
    ts_str = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_dir = BACKUP_ROOT / f"backup-{ts_str}"
    backup_dir.mkdir(parents=True)

    items = {}
    for key, target in managed_slots().items():
        shutil.copytree(target, backup_dir / key)
        items[key] = {"target": str(target.resolve()), "files": hash_tree(target)}

    write_manifest(backup_dir, {"timestamp": ts_str, "items": items})
    return backup_dir


def read_manifest(backup_dir: Path) -> dict:
    path = backup_dir / "backup_manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def write_manifest(backup_dir: Path, manifest: dict) -> None:
    (backup_dir / "backup_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def restore_backup(backup_dir: Path, snapshot_first: bool = False) -> None:
    """Restores a backup, removing slots the following update created.

    Every check runs before anything is touched. Refuses when a destination is unmanaged,
    when a slot changed after the update recorded it ("installed" hashes), or when the
    backup copy no longer matches its own manifest.
    """
    if not backup_dir.exists():
        raise FileNotFoundError(f"Backup directory not found: {backup_dir}")
    manifest = read_manifest(backup_dir)
    items, installed = manifest.get("items", {}), manifest.get("installed", {})

    restores, removals = [], []
    for agent, base_dir in get_agent_skill_dirs().items():
        for skill in MANAGED_SKILLS:
            key = f"{agent}/{skill}"
            src, target = backup_dir / agent / skill, base_dir / skill
            if not src.exists() and key not in installed:
                continue
            if target.exists():
                if not (target / MANAGED_MARKER).exists():
                    raise RuntimeError(
                        f"REFUSE: destination '{target}' is unmanaged; rollback blocked to preserve user files."
                    )
                if key in installed and hash_tree(target) != installed[key]:
                    raise RuntimeError(
                        f"REFUSE: '{target}' changed after the update; rollback blocked. Reconcile manually."
                    )
            if src.exists():
                if key in items and hash_tree(src) != items[key]["files"]:
                    raise RuntimeError(f"REFUSE: backup copy '{src}' does not match its manifest.")
                restores.append((src, target))
            elif target.exists():
                removals.append(target)

    if snapshot_first:
        snapshot = backup_managed_skills()
        print(f"Saved current state first; undo this rollback with: --rollback {snapshot}")
    for target in removals:
        shutil.rmtree(target)
        print(f"Removed slot created by the update: {target}")
    for src, target in restores:
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(src, target)
        print(f"Restored {target}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Herdr Role Pack Updater and Rollback Tool.")
    parser.add_argument("--check", action="store_true", help="Check for available releases without updating")
    parser.add_argument("--dry-run", action="store_true", help="Preview update actions without making changes")
    parser.add_argument("--rollback", help="Restore skills from a specific backup directory or 'latest'")
    parser.add_argument("--list-backups", action="store_true", help="List available local backups")
    parser.add_argument("--prerelease", action="store_true", help="Allow updating to prereleases")
    parser.add_argument("--force", action="store_true", help="Force reinstall even if version matches")

    args = parser.parse_args()

    if args.list_backups:
        if not BACKUP_ROOT.exists():
            print("No local backups found.")
            return 0
        backups = sorted([p for p in BACKUP_ROOT.iterdir() if p.is_dir()], reverse=True)
        if not backups:
            print("No local backups found.")
            return 0
        print("Available local backups:")
        for b in backups:
            print(f"  {b.name} -> {b}")
        return 0

    if args.rollback:
        if args.rollback == "latest":
            if not BACKUP_ROOT.exists():
                print("ERROR: No backups available to restore.", file=sys.stderr)
                return 1
            backups = sorted([p for p in BACKUP_ROOT.iterdir() if p.is_dir()], reverse=True)
            if not backups:
                print("ERROR: No backups available to restore.", file=sys.stderr)
                return 1
            target_backup = backups[0]
        else:
            target_backup = Path(args.rollback)
        print(f"Restoring from: {target_backup}...")
        try:
            restore_backup(target_backup, snapshot_first=True)
        except Exception as e:
            print(f"ERROR during rollback: {e}", file=sys.stderr)
            return 1
        print("Rollback completed successfully.")
        return 0

    # Version inspection
    installed = get_installed_versions()
    print("Installed skills inspection:")
    any_installed = False
    for agent, skills in installed.items():
        for skill, ver in skills.items():
            if ver:
                any_installed = True
                print(f"  [{agent}] {skill}: {ver}")
    if not any_installed:
        print("  No pack-managed skills currently detected.")

    print("\nChecking for published GitHub releases...")
    try:
        latest = fetch_latest_release(allow_prerelease=args.prerelease)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if not latest:
        print(f"Status: Development version. No published releases found on GitHub ({GITHUB_RELEASES_URL}).")
        print("To install or update from current repository source, run:")
        print("  npx . --force")
        print("  or: .\\install.ps1 -Force")
        return 0

    tag_name = latest.get("tag_name", "unknown")
    print(f"Latest published release on GitHub: {tag_name}")
    assets = latest.get("assets", [])
    pack_asset = next((a for a in assets if a["name"].startswith("herdr-role-pack-") and a["name"].endswith(".zip")), None)
    sums_asset = next((a for a in assets if a["name"] == "SHA256SUMS"), None)

    if not pack_asset:
        print(f"Warning: Release {tag_name} has no role-pack archive asset attached.")
        return 0

    latest_version = parse_version(tag_name)
    managed_versions = [parse_version(v) for skills in installed.values() for v in skills.values() if v and v != "UNMANAGED"]
    # Unreadable markers count as outdated, so an unknown install is always offered the update.
    up_to_date = bool(managed_versions) and latest_version is not None and all(
        v is not None and v >= latest_version for v in managed_versions
    )
    if up_to_date and not args.force:
        print(f"\nUp to date: every managed skill is at {tag_name} or newer. Use --force to reinstall.")
        return 0

    if args.check:
        print(f"\nUpdate available: {tag_name}")
        print(f"Release URL: {latest.get('html_url')}")
        return 0

    if args.dry_run:
        print(f"\n[DRY-RUN] Would download: {pack_asset['name']}")
        print(f"[DRY-RUN] Would verify checksum from: {sums_asset['name'] if sums_asset else 'MISSING'}")
        print(f"[DRY-RUN] Would back up current skills to: {BACKUP_ROOT}")
        print("[DRY-RUN] Would extract and invoke installer with --force for agents that already have the pack")
        return 0

    # Fail fast, before downloading or backing up anything
    if not sums_asset:
        print("ERROR: INTEGRITY_ERROR: Release is missing required SHA256SUMS asset.", file=sys.stderr)
        return 1
    node_cmd = shutil.which("node")
    if not node_cmd:
        print("ERROR: Node.js runtime is required for updating. Please install Node.js.", file=sys.stderr)
        return 1
    # Update only the agents that already have the pack; never spread into agent dirs the user never chose.
    agents = sorted({key.split("/")[0] for key in managed_slots()})
    if not agents:
        print("ERROR: No pack-managed installation found. Install first (see README).", file=sys.stderr)
        return 1

    temp_dir = Path(tempfile.mkdtemp(prefix="herdr-update-"))
    backup_dir = None
    try:
        archive_path = temp_dir / pack_asset["name"]
        print(f"Downloading {pack_asset['name']}...")
        download_asset(pack_asset, archive_path)

        sums_path = temp_dir / "SHA256SUMS"
        download_asset(sums_asset, sums_path)
        sums_text = sums_path.read_text(encoding="utf-8")
        parsed_sums = parse_checksums_file(sums_text)

        expected_hash = parsed_sums.get(pack_asset["name"])
        if not expected_hash:
            raise ValueError(f"INTEGRITY_ERROR: No checksum entry found for '{pack_asset['name']}' in SHA256SUMS.")

        actual_hash = calculate_sha256(archive_path)
        if not hmac.compare_digest(actual_hash.lower(), expected_hash.lower()):
            raise ValueError(f"INTEGRITY_ERROR: Checksum mismatch for {pack_asset['name']}! Expected {expected_hash}, got {actual_hash}")
        print("Checksum verified successfully.")

        # Validate archive layout safety
        safety_errors = validate_archive_safety(archive_path)
        if safety_errors:
            raise ValueError(f"Archive safety check failed: {safety_errors}")

        extract_dir = temp_dir / "extracted"
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(extract_dir)

        # Pre-update backup created right before execution
        print(f"\nInitiating safe update to {tag_name}...")
        backup_dir = backup_managed_skills()
        print(f"Saved pre-update backup: {backup_dir}")

        # Run extracted node installer
        install_js = extract_dir / "bin" / "install.js"
        if not install_js.exists():
            raise FileNotFoundError(f"Missing installer inside archive: {install_js}")

        subprocess.run([node_cmd, str(install_js), "--force", "--agents", ",".join(agents)], check=True)
        # Record what the update installed so a later rollback can detect user edits and new slots.
        manifest = read_manifest(backup_dir)
        manifest["installed"] = {key: hash_tree(path) for key, path in managed_slots().items()}
        write_manifest(backup_dir, manifest)
        print(f"Update to {tag_name} completed successfully.")
    except Exception as e:
        print(f"ERROR: Update failed: {e}", file=sys.stderr)
        if backup_dir and backup_dir.exists():
            print("Restoring from backup...", file=sys.stderr)
            try:
                restore_backup(backup_dir)
                print("Original installation restored cleanly.", file=sys.stderr)
            except Exception as re:
                print(f"CRITICAL: Rollback encountered an error: {re}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
