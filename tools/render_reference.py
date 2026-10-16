"""Regenerate the committed ledger command reference, deterministically.

Do not regenerate it with shell redirection: on Windows PowerShell `>` re-encodes the
stream as UTF-16LE with CRLF, which corrupts the file. Render through this tool so the
committed artifact is always UTF-8 without a BOM and LF-terminated on every platform,
and so the generated header can name a recipe that actually works.

    python tools/render_reference.py            # rewrite the committed reference
    python tools/render_reference.py --check    # exit 1 when the committed copy is stale
"""
import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "skills" / "herdr-engineering-loop" / "scripts" / "herdr_ledger.py"
TARGET = ROOT / "skills" / "herdr-engineering-loop" / "references" / "command-reference.md"


def _ledger_module():
    spec = importlib.util.spec_from_file_location("herdr_ledger_reference_render", CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rendered_bytes():
    """The exact bytes that belong in TARGET: generated text, UTF-8, LF, no BOM."""
    return _ledger_module().command_reference().encode("utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Regenerate the committed ledger command reference.")
    ap.add_argument("--check", action="store_true",
                    help="report whether the committed reference is current instead of writing it")
    args = ap.parse_args(argv)
    relative = TARGET.relative_to(ROOT).as_posix()
    generated = rendered_bytes()
    if args.check:
        if generated != (TARGET.read_bytes() if TARGET.exists() else b""):
            print(f"STALE: {relative} does not match the CLI parser")
            return 1
        print(f"OK: {relative} matches the CLI parser")
        return 0
    TARGET.write_bytes(generated)
    print(f"wrote {relative} ({len(generated)} bytes, UTF-8, LF)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
