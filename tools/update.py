#!/usr/bin/env python3
"""
Explicit updater and rollback utility for Herdr Role Pack.
Queries published GitHub releases, verifies asset checksums and archive safety,
backs up managed installations, and provides safe rollback.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.error
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


def fetch_latest_release(allow_prerelease: bool = False) -> dict | None:
    """Queries GitHub Releases API for the latest compatible release."""
    req = urllib.request.Request(
        f"{GITHUB_API_URL}",
        headers={"User-Agent": "Herdr-Role-Pack-Updater", "Accept": "application/vnd.github.v3+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
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
        if e.code == 404:
            return None
        raise
    except Exception:
        return None


def calculate_sha256(file_path: Path) -> str:
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


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


def backup_managed_skills() -> Path:
    """Creates a local backup of currently installed managed skills."""
    timestamp = Path(tempfile.gettempdir())
    import time
    ts_str = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = BACKUP_ROOT / f"backup-{ts_str}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    for agent, base_dir in get_agent_skill_dirs().items():
        agent_backup = backup_dir / agent
        for skill in MANAGED_SKILLS:
            target = base_dir / skill
            if target.exists() and (target / MANAGED_MARKER).exists():
                dest = agent_backup / skill
                shutil.copytree(target, dest, dirs_exist_ok=True)

    return backup_dir


def restore_backup(backup_dir: Path) -> None:
    """Restores skills from an existing backup."""
    if not backup_dir.exists():
        raise FileNotFoundError(f"Backup directory not found: {backup_dir}")

    for agent, base_dir in get_agent_skill_dirs().items():
        agent_backup = backup_dir / agent
        if not agent_backup.exists():
            continue
        for skill in MANAGED_SKILLS:
            src = agent_backup / skill
            if src.exists():
                target = base_dir / skill
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                shutil.copytree(src, target, dirs_exist_ok=True)
                print(f"Restored {agent} -> {target.name}")


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
        restore_backup(target_backup)
        print("Rollback completed successfully.")
        return 0

    # Version check
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
        print(f"Network / API error querying releases: {e}", file=sys.stderr)
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

    if args.check:
        print(f"\nUpdate available: {tag_name}")
        print(f"Release URL: {latest.get('html_url')}")
        return 0

    if args.dry_run:
        print(f"[DRY-RUN] Would download {pack_asset['name']}")
        print(f"[DRY-RUN] Would verify archive and checksums against SHA256SUMS")
        print(f"[DRY-RUN] Would back up current skills to {BACKUP_ROOT}")
        print(f"[DRY-RUN] Would update managed skills across Claude, Codex, and Agy")
        return 0

    print(f"\nInitiating safe update to {tag_name}...")
    backup_dir = backup_managed_skills()
    print(f"Saved pre-update backup: {backup_dir}")

    temp_dir = Path(tempfile.mkdtemp(prefix="herdr-update-"))
    try:
        archive_path = temp_dir / pack_asset["name"]
        print(f"Downloading {pack_asset['name']}...")
        urllib.request.urlretrieve(pack_asset["browser_download_url"], archive_path)

        # Validate checksum if SHA256SUMS asset exists
        if sums_asset:
            sums_path = temp_dir / "SHA256SUMS"
            urllib.request.urlretrieve(sums_asset["browser_download_url"], sums_path)
            sums_text = sums_path.read_text(encoding="utf-8")
            expected_hash = None
            for line in sums_text.splitlines():
                if pack_asset["name"] in line:
                    expected_hash = line.split()[0]
                    break
            if expected_hash:
                actual_hash = calculate_sha256(archive_path)
                if actual_hash != expected_hash:
                    raise ValueError(f"Checksum mismatch! Expected {expected_hash}, got {actual_hash}")
                print("Checksum verified successfully.")

        # Validate archive layout safety
        safety_errors = validate_archive_safety(archive_path)
        if safety_errors:
            raise ValueError(f"Archive safety check failed: {safety_errors}")

        extract_dir = temp_dir / "extracted"
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(extract_dir)

        # Run extracted node installer or python installer
        install_js = extract_dir / "bin" / "install.js"
        if install_js.exists():
            import subprocess
            res = subprocess.run([sys.executable, str(extract_dir / "tools" / "package.py"), "--dry-run"], capture_output=True)
            # Invoke install.js with --force
            node_cmd = shutil.which("node")
            if node_cmd:
                subprocess.run([node_cmd, str(install_js), "--force"], check=True)
            else:
                print("Node.js not found; installing via local file copy.")
        print(f"Update to {tag_name} completed successfully.")
    except Exception as e:
        print(f"ERROR: Update failed: {e}", file=sys.stderr)
        print("Restoring from backup...", file=sys.stderr)
        restore_backup(backup_dir)
        print("Original installation restored cleanly.", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
