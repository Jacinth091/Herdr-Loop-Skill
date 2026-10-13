#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/home"
export HOME="$TMP/home"
sh "$ROOT/install.sh" --agents claude,codex,agy --roles manager,worker,critic > "$TMP/installed.txt"
for dir in "$HOME/.claude/skills" "$HOME/.agents/skills" "$HOME/.gemini/antigravity-cli/skills"; do
  test -f "$dir/herdr-worker/SKILL.md"
  test -f "$dir/herdr-manager/SKILL.md"
  test -f "$dir/herdr-critic/SKILL.md"
  test ! -e "$dir/herdr-verifier"
  test -f "$dir/herdr-worker/.herdr-role-pack-managed"
  grep -qF "Herdr Role Pack v$(sed -n 's/^ *"version": *"\([^"]*\)".*/\1/p' "$ROOT/manifest.json" | head -n 1)" "$dir/herdr-worker/.herdr-role-pack-managed"
done
sh "$ROOT/install.sh" --agents claude,codex,agy --roles manager,worker,critic > "$TMP/second.txt"
grep -q 'SKIP already installed' "$TMP/second.txt"
# Refuse to overwrite a third-party folder, including with --force.
mkdir -p "$HOME/.agents/skills/herdr-verifier"
echo 'preserve' > "$HOME/.agents/skills/herdr-verifier/foreign.txt"
if sh "$ROOT/install.sh" --agents codex --roles verifier --force > "$TMP/collision.txt" 2>&1; then echo 'unexpected overwrite' >&2; exit 1; fi
test -f "$HOME/.agents/skills/herdr-verifier/foreign.txt"
# --force updates the installed managed role without touching the foreign folder.
sh "$ROOT/install.sh" --agents codex --roles manager --force > "$TMP/updated.txt"
grep -q 'INSTALLED' "$TMP/updated.txt"
# Dry run doesn't create new directories.
sh "$ROOT/install.sh" --agents claude --roles verifier --dry-run > "$TMP/dryrun.txt"
test ! -e "$HOME/.claude/skills/herdr-verifier"
sh "$ROOT/install.sh" --agents claude --roles manager,worker,critic --uninstall > "$TMP/uninstall.txt"
test ! -e "$HOME/.claude/skills/herdr-worker"
test ! -e "$HOME/.claude/skills/herdr-critic"
test -f "$HOME/.agents/skills/herdr-worker/SKILL.md"
sh "$ROOT/install.sh" --agents codex --roles verifier --uninstall > "$TMP/unsafeuninstall.txt"
test -f "$HOME/.agents/skills/herdr-verifier/foreign.txt"
# Installed official Herdr via a fake local binary, never fetched from internet.
mkdir -p "$TMP/bin"
cat > "$TMP/bin/herdr" <<'MOCK'
#!/bin/sh
printf '%s\n' '---' 'name: herdr' 'description: CLI skill' '---' 'Test Herdr'
MOCK
chmod +x "$TMP/bin/herdr"
PATH="$TMP/bin:$PATH" sh "$ROOT/install.sh" --agents claude --roles controller --with-herdr > "$TMP/official.txt"
test -f "$HOME/.claude/skills/herdr/SKILL.md"
test -f "$HOME/.claude/skills/herdr-engineering-loop/scripts/herdr_ledger.py"
echo 'PASS installer: selective installs, idempotency, unmanaged collision, force update, dry-run, uninstall, optional official skill'
