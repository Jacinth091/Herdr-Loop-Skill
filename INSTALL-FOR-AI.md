# Installing Herdr Loop Skill: instructions for AI agents

**You are an AI coding agent and your user asked you to install Herdr Loop Skill for yourself.** Follow these steps in order. They work for Claude Code, Codex, Agy and OpenCode, and for any other CLI that loads skills from `SKILL.md` folders.

Herdr Loop Skill is a set of role skills (Controller, Manager, Delegator, Lead, Worker, Verifier, Critic) that coordinate several coding agents inside [Herdr](https://herdr.dev). Installing only copies Markdown skill folders into a skills directory. It does not start agents, change code or need admin rights.

## Ground rules

- **Ask before you write.** Tell the user which folder you will install into and which roles, and wait for their OK. Show them the `--dry-run` output first.
- **Never overwrite folders you didn't install.** The installer refuses to replace a skill folder that lacks the `.herdr-role-pack-managed` marker. If it stops, report the folder; do not delete or rename it to get past the check.
- **Use native permissions.** For an authorized in-scope operation, request approval only through your own CLI's supported tool and wait for its decision. Never self-grant, use `sudo`/Administrator, change ACLs or provider policy, or disable a sandbox. If an install folder is not writable, report it.
- **Report honestly.** If a step fails or you skipped one, say so.

## Step 1: Find your skills folder

| You are | Your user-level skills folder | Installer option |
|---|---|---|
| Claude Code | `~/.claude/skills` | `--agents claude` |
| Codex CLI | `~/.agents/skills` | `--agents codex` |
| Agy CLI | `~/.gemini/antigravity-cli/skills` | `--agents agy` |
| OpenCode | `~/.config/opencode/skills` | `--agents opencode` |
| Any other CLI | The folder your tool loads user-level `SKILL.md` skill folders from. Check your own documentation or configuration; if you are not sure, ask the user. | `--target-dir "<absolute path>"` |

Use an absolute path with `--target-dir` (`~` is not expanded inside quotes). If your tool has no skills feature at all, skip to [Step 5](#step-5-if-your-tool-has-no-skills-feature).

## Step 2: Choose roles with the user

| The user wants | `--roles` |
|---|---|
| Everything (default) | *(omit)* |
| One agent to plan and coordinate on a budget (Lead) | `controller,lead` |
| This agent only as a Worker | `controller,worker` |
| This agent only as Verifier or Critic | `controller,verifier` / `controller,critic` |

Keep `controller` in any subset. `lead` also installs `manager` and `delegator`, which it builds on.

## Step 3: Preview

Requires Node.js 16 or newer (`node --version`). If Node is missing, use [Step 4b](#step-4b-without-nodejs).

```bash
npx github:Jacinth091/Herdr-Loop-Skill#v2.8.1 --agents claude --dry-run
# or, for any other CLI:
npx github:Jacinth091/Herdr-Loop-Skill#v2.8.1 --target-dir "/absolute/path/to/your/skills" --dry-run
```

Add the user's `--roles` choice. Show the user the `INSTALL ...` lines and confirm.

If `npx` cannot fetch the repository, the repository may be private to the user: ask them to run `gh auth setup-git` (or sign in through Git Credential Manager) and try again.

## Step 4a: Install

Run the same command without `--dry-run`. If the skills are already installed, add `--force` to update them; the installer backs up the old copies to `~/.herdr-backups/` first and rolls back if a copy fails part-way.

## Step 4b: Without Node.js

1. Download `herdr-role-pack-v2.8.1.zip` and `SHA256SUMS` from the [v2.8.1 release](https://github.com/Jacinth091/Herdr-Loop-Skill/releases/tag/v2.8.1) (`gh release download v2.8.1 -R Jacinth091/Herdr-Loop-Skill -p "herdr-role-pack-v2.8.1.zip" -p SHA256SUMS`).
2. Verify the archive: its SHA-256 (`sha256sum` on Linux, `shasum -a 256` on macOS, `Get-FileHash` on Windows) must equal the `herdr-role-pack-v2.8.1.zip` line in `SHA256SUMS`. Stop if it differs.
3. Extract it. For each chosen skill (`skills/herdr-engineering-loop`, `skills/herdr-lead`, ...): if the destination folder already exists without a `.herdr-role-pack-managed` file, stop and report it. Otherwise copy the folder into your skills folder.
4. In each copied folder, create `.herdr-role-pack-managed` containing `Herdr Role Pack v2.8.1`. This marks it as pack-managed so later updates and uninstalls recognize it.

## Step 5: If your tool has no skills feature

1. Install into a plain folder instead, for example `--target-dir "<home>/.herdr-skills"`.
2. Ask the user before editing your persistent instruction file (for example `AGENTS.md`, `GEMINI.md` or your tool's rules file). With their OK, add: *"When assigned a Herdr role, read and follow `<home>/.herdr-skills/<role-skill>/SKILL.md` and its `references/` files before acting."*

## Step 6: Verify

- List the skills folder: each installed role folder contains `SKILL.md` and `.herdr-role-pack-managed`.
- Tell the user to restart your CLI (or start a new session) so it loads the new skills, and, after restart, check that your tool lists them.

## Step 7: Report back

Tell the user, in plain words: where you installed, which roles, the version (from the marker file), where backups went if you used `--force`, and anything you skipped or that failed.

## Updating and removing

```bash
npx github:Jacinth091/Herdr-Loop-Skill#<newer tag> --force [--agents ... | --target-dir ...]
npx github:Jacinth091/Herdr-Loop-Skill --uninstall [--agents ... | --target-dir ...]
```

Uninstall removes only folders with the `.herdr-role-pack-managed` marker.

## Good to know

- **Governed sessions need Herdr.** A direct standalone Worker task can run in an existing CLI without Herdr; installing skills does not start agents or install Herdr.
- **Any CLI can hold a role.** `tools/role_bind.py` recognizes `claude`, `codex`, `agy` and `opencode` in every role. For any other CLI, set `"kind"` to its name (for example `"kind": "gemini"`); the binder accepts it with a warning, because the pack has only been tested with `claude`, `codex`, `agy` and `opencode`; confirm that `herdr agent start --kind <your-kind>` works before relying on Herdr integration. Approval support remains unverified until that CLI itself offers a native request for the exact action.
- **Model names in the skills are defaults**, chosen for Claude, Codex, Agy and OpenCode. Rules that name Codex (such as never using `spawn_agent`, or the model-override launch table) apply only when you are Codex. In governed runs, you operate as a named Herdr agent in your own pane, never as an in-process subagent, and Workers do not run tests. A direct standalone Worker assignment can use bounded self-checks, labeled as self-verification. In every mode, stay inside the assigned role and use only your CLI's real permission flow.
