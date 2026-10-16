#!/bin/sh
# Herdr Role Pack installer (POSIX shell; no Node, Python or network required).
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VERSION=$(sed -n 's/^ *"version": *"\([^"]*\)".*/\1/p' "$ROOT/manifest.json" | head -n 1)
AGENTS=all
ROLES=all
FORCE=0
DRY_RUN=0
UNINSTALL=0
WITH_HERDR=0
SKILLS="herdr-engineering-loop herdr-manager herdr-delegator herdr-lead herdr-worker herdr-verifier herdr-critic"

usage() {
  cat <<'USAGE'
Install independent Herdr roles into user-level agent skill folders.
Usage: sh install.sh [--agents claude,codex,agy,opencode|all] [--roles manager,delegator,lead,worker,verifier,critic,controller|all] [--force] [--dry-run] [--uninstall] [--with-herdr]
  --agents      Agent CLIs receiving skills (default: all)
  --roles       Role skills to install (default: all)
  --force       Update already pack-managed skills; refuse foreign folders
  --dry-run     Show changes without writing
  --uninstall   Remove ONLY pack-managed directories
  --with-herdr  Also install official CLI skill from local `herdr --skill` (requires installed herdr)
No network, npm, elevated permissions or agent auto-launch.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --agents) [ "$#" -ge 2 ] || { echo 'Missing --agents value' >&2; exit 2; }; AGENTS=$2; shift 2 ;;
    --roles) [ "$#" -ge 2 ] || { echo 'Missing --roles value' >&2; exit 2; }; ROLES=$2; shift 2 ;;
    --force) FORCE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    --with-herdr) WITH_HERDR=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

contains_csv() { case ",$1," in *",$2,"*) return 0;; *) return 1;; esac; }
expand_roles() {
  case "$ROLES" in all) echo "$SKILLS"; return;; esac
  out=''
  rest=$(printf '%s' "$ROLES" | tr ',' ' ')
  for role in $rest; do
    case "$role" in
      controller|engineering-loop|herdr-engineering-loop) name=herdr-engineering-loop ;;
      manager|herdr-manager) name=herdr-manager ;;
      delegator|herdr-delegator) name=herdr-delegator ;;
      # Lead is built on the Manager and Delegator contracts, so it always brings them along.
      lead|herdr-lead) name='herdr-lead herdr-manager herdr-delegator' ;;
      worker|herdr-worker) name=herdr-worker ;;
      verifier|herdr-verifier) name=herdr-verifier ;;
      critic|herdr-critic) name=herdr-critic ;;
      *) echo "Unknown role: $role" >&2; exit 2 ;;
    esac
    for n in $name; do
      case " $out " in *" $n "*) ;; *) out="$out $n" ;; esac
    done
  done
  printf '%s\n' "$out"
}

case "$AGENTS" in
  all) selected_agents='claude codex agy opencode' ;;
  *) selected_agents=$(printf '%s' "$AGENTS" | tr ',' ' ')
    for a in $selected_agents; do
      case "$a" in claude|codex|agy|opencode) : ;; *) echo "Unknown agent: $a" >&2; exit 2 ;; esac
    done ;;
esac
roles_selected=$(expand_roles)
[ -n "$roles_selected" ] || { echo 'No roles selected' >&2; exit 2; }
[ -n "$selected_agents" ] || { echo 'No agents selected' >&2; exit 2; }

install_one() {
  targetdir=$1
  name=$2
  src=$3
  managed_marker=$targetdir/$name/.herdr-role-pack-managed
  if [ "$UNINSTALL" -eq 1 ]; then
    if [ -e "$targetdir/$name" ] && [ -f "$managed_marker" ]; then
      if [ "$DRY_RUN" -eq 0 ]; then rm -rf -- "$targetdir/$name"; fi
      echo "REMOVE $targetdir/$name"
    else echo "SKIP removal (absent/unmanaged) $targetdir/$name"; fi
    return
  fi
  if [ -e "$targetdir/$name" ]; then
    if [ ! -f "$managed_marker" ]; then echo "REFUSE existing unmanaged folder: $targetdir/$name" >&2; return 1; fi
    if [ "$FORCE" -ne 1 ]; then echo "SKIP already installed: $targetdir/$name (use --force to update)"; return; fi
  fi
  [ -d "$src" ] || { echo "Missing bundled skill: $src" >&2; return 1; }
  [ -f "$src/SKILL.md" ] || { echo "Missing SKILL.md: $src" >&2; return 1; }
  if [ "$DRY_RUN" -eq 1 ]; then echo "INSTALL $src -> $targetdir/$name"; return; fi
  mkdir -p -- "$targetdir"
  temp=$(mktemp -d "$targetdir/.herdr-stage.XXXXXXXX")
  cp -R -- "$src" "$temp/$name"
  printf 'Herdr Role Pack v%s\n' "$VERSION" >"$temp/$name/.herdr-role-pack-managed"
  if [ -e "$targetdir/$name" ]; then
    mv -- "$targetdir/$name" "$temp/original"
  fi
  if mv -- "$temp/$name" "$targetdir/$name"; then
    rm -rf -- "$temp"
    echo "INSTALLED $targetdir/$name"
  else
    [ ! -d "$temp/original" ] || mv -- "$temp/original" "$targetdir/$name"
    rm -rf -- "$temp"
    return 1
  fi
}

for agent in $selected_agents; do
  case "$agent" in
    claude) base="$HOME/.claude/skills" ;;
    codex) base="$HOME/.agents/skills" ;;
    agy) base="$HOME/.gemini/antigravity-cli/skills" ;;
    opencode) base="$HOME/.config/opencode/skills" ;;
  esac
  for skill in $roles_selected; do
    install_one "$base" "$skill" "$ROOT/skills/$skill"
  done
  if [ "$WITH_HERDR" -eq 1 ]; then
    if [ "$UNINSTALL" -eq 1 ]; then
      install_one "$base" "herdr" "$ROOT/skills/herdr"
    elif command -v herdr >/dev/null 2>&1; then
      if [ "$DRY_RUN" -eq 1 ]; then
        echo "INSTALL official herdr skill via local herdr --skill -> $base/herdr"
      else
        tmp_official=$(mktemp -d)
        mkdir -p "$tmp_official/herdr"
        if herdr --skill > "$tmp_official/herdr/SKILL.md" && grep -q '^---' "$tmp_official/herdr/SKILL.md"; then
          install_one "$base" "herdr" "$tmp_official/herdr"
        else
          echo 'WARN official `herdr --skill` failed or returned invalid text; skipped' >&2
        fi
        rm -rf "$tmp_official"
      fi
    else
      echo 'WARN herdr binary not found; official skill skipped (install Herdr first)' >&2
    fi
  fi
done
printf 'Role skills are installed. Assign agents explicitly using tools/role_bind.py and ACTIVATE.md.\n'
