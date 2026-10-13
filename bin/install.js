#!/usr/bin/env node
/**
 * Herdr Role Pack installer for npm / npx.
 * Zero external dependencies: Node.js standard library only.
 *
 * Usage:
 *   npx herdr-role-pack
 *   npx herdr-role-pack --dry-run
 *   npx herdr-role-pack --force
 *   npx herdr-role-pack --agents claude,codex,agy
 *   npx herdr-role-pack --roles manager,delegator,worker
 *   npx herdr-role-pack --uninstall
 */

const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { execFileSync } = require('node:child_process');

const ROOT = path.resolve(__dirname, '..');
const SKILL_NAMES = {
  controller: 'herdr-engineering-loop',
  'engineering-loop': 'herdr-engineering-loop',
  'herdr-engineering-loop': 'herdr-engineering-loop',
  manager: 'herdr-manager',
  'herdr-manager': 'herdr-manager',
  delegator: 'herdr-delegator',
  'herdr-delegator': 'herdr-delegator',
  worker: 'herdr-worker',
  'herdr-worker': 'herdr-worker',
  verifier: 'herdr-verifier',
  'herdr-verifier': 'herdr-verifier',
  critic: 'herdr-critic',
  'herdr-critic': 'herdr-critic',
};

const ALL_ROLES = [
  'herdr-engineering-loop',
  'herdr-manager',
  'herdr-delegator',
  'herdr-worker',
  'herdr-verifier',
  'herdr-critic',
];

function printHelp() {
  console.log(`
Herdr Role Pack - Easy Skill Installer (npx / npm)

Usage:
  npx herdr-role-pack [options]
  node bin/install.js [options]

Options:
  --agents <list>    Comma-separated agents to install for: claude, codex, agy, or all (default: all)
  --roles <list>     Comma-separated roles: manager, delegator, worker, verifier, critic, controller, or all (default: all)
  --force            Update already installed role-pack skills; refuse foreign folders
  --dry-run          Show actions without writing any files
  --uninstall        Remove only pack-managed skills
  --with-herdr       Also install official 'herdr' skill using local 'herdr --skill'
  -h, --help         Show this help message

Target Folders:
  Claude: ~/.claude/skills
  Codex:  ~/.agents/skills
  Agy:    ~/.gemini/antigravity-cli/skills
`);
}

function parseArgs(args) {
  const options = {
    agents: 'all',
    roles: 'all',
    force: false,
    dryRun: false,
    uninstall: false,
    withHerdr: false,
  };

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--help' || arg === '-h') {
      printHelp();
      process.exit(0);
    } else if (arg === '--force') {
      options.force = true;
    } else if (arg === '--dry-run') {
      options.dryRun = true;
    } else if (arg === '--uninstall') {
      options.uninstall = true;
    } else if (arg === '--with-herdr') {
      options.withHerdr = true;
    } else if (arg === '--agents' && i + 1 < args.length) {
      options.agents = args[++i];
    } else if (arg === '--roles' && i + 1 < args.length) {
      options.roles = args[++i];
    } else {
      console.error(`Unknown option: ${arg}`);
      printHelp();
      process.exit(2);
    }
  }

  return options;
}

function getAgentDirs() {
  const home = os.homedir();
  return {
    claude: path.join(home, '.claude', 'skills'),
    codex: path.join(home, '.agents', 'skills'),
    agy: path.join(home, '.gemini', 'antigravity-cli', 'skills'),
  };
}

function resolveAgents(selection, agentDirs) {
  if (selection.toLowerCase() === 'all') {
    return Object.keys(agentDirs);
  }
  const requested = selection.toLowerCase().split(',').map(s => s.trim()).filter(Boolean);
  for (const name of requested) {
    if (!agentDirs[name]) {
      console.error(`Error: Unknown agent '${name}'. Supported: claude, codex, agy, all`);
      process.exit(2);
    }
  }
  return [...new Set(requested)];
}

function resolveRoles(selection) {
  if (selection.toLowerCase() === 'all') {
    return ALL_ROLES;
  }
  const requested = selection.toLowerCase().split(',').map(s => s.trim()).filter(Boolean);
  const resolved = [];
  for (const r of requested) {
    const canonical = SKILL_NAMES[r];
    if (!canonical) {
      console.error(`Error: Unknown role '${r}'. Supported: manager, delegator, worker, verifier, critic, controller, all`);
      process.exit(2);
    }
    if (!resolved.includes(canonical)) resolved.push(canonical);
  }
  return resolved;
}

function copyDirRecursive(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const srcPath = path.join(src, entry.name);
    const destPath = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      copyDirRecursive(srcPath, destPath);
    } else {
      fs.copyFileSync(srcPath, destPath);
    }
  }
}

function installSkill({ source, baseDir, skillName, options }) {
  const target = path.join(baseDir, skillName);
  const marker = path.join(target, '.herdr-role-pack-managed');
  const exists = fs.existsSync(target);
  const managed = fs.existsSync(marker);

  if (options.uninstall) {
    if (exists && managed) {
      console.log(`REMOVE ${target}`);
      if (!options.dryRun) {
        fs.rmSync(target, { recursive: true, force: true });
      }
    } else {
      console.log(`SKIP removal (absent/unmanaged): ${target}`);
    }
    return;
  }

  if (exists) {
    if (!managed) {
      throw new Error(`REFUSE unmanaged existing directory: ${target}`);
    }
    if (!options.force) {
      console.log(`SKIP already installed: ${target} (use --force to update)`);
      return;
    }
  }

  const skillFile = path.join(source, 'SKILL.md');
  if (!fs.existsSync(skillFile)) {
    throw new Error(`Missing source SKILL.md in: ${source}`);
  }

  if (options.dryRun) {
    console.log(`INSTALL ${source} -> ${target}`);
    return;
  }

  fs.mkdirSync(baseDir, { recursive: true });

  const stageDir = path.join(baseDir, `.herdr-stage-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
  const stagedSkill = path.join(stageDir, skillName);
  const backupDir = path.join(stageDir, 'original');

  try {
    copyDirRecursive(source, stagedSkill);
    fs.writeFileSync(path.join(stagedSkill, '.herdr-role-pack-managed'), 'Herdr Role Pack v2.8\n', 'utf8');

    if (exists) {
      fs.renameSync(target, backupDir);
    }
    try {
      fs.renameSync(stagedSkill, target);
    } catch (err) {
      if (fs.existsSync(backupDir)) {
        fs.renameSync(backupDir, target);
      }
      throw err;
    }
    console.log(`INSTALLED ${target}`);
  } finally {
    if (fs.existsSync(stageDir)) {
      fs.rmSync(stageDir, { recursive: true, force: true });
    }
  }
}

function main() {
  const options = parseArgs(process.argv.slice(2));
  const agentDirs = getAgentDirs();
  const selectedAgents = resolveAgents(options.agents, agentDirs);
  const selectedRoles = resolveRoles(options.roles);

  // Preflight check: validate sources and check for unmanaged destination collisions
  const plan = [];
  for (const agent of selectedAgents) {
    const baseDir = agentDirs[agent];
    for (const skillName of selectedRoles) {
      const source = path.join(ROOT, 'skills', skillName);
      const target = path.join(baseDir, skillName);
      const marker = path.join(target, '.herdr-role-pack-managed');
      const exists = fs.existsSync(target);
      const managed = fs.existsSync(marker);

      if (!options.uninstall) {
        if (!fs.existsSync(path.join(source, 'SKILL.md'))) {
          console.error(`Error: Missing source SKILL.md in: ${source}`);
          process.exit(1);
        }
        if (exists && !managed) {
          console.error(`Error: REFUSE unmanaged existing directory: ${target}`);
          process.exit(1);
        }
      }
      plan.push({ agent, baseDir, skillName, source, target, exists, managed });
    }

    if (options.withHerdr) {
      const skillName = 'herdr';
      const source = path.join(ROOT, 'skills', skillName);
      const target = path.join(baseDir, skillName);
      const marker = path.join(target, '.herdr-role-pack-managed');
      const exists = fs.existsSync(target);
      const managed = fs.existsSync(marker);

      if (!options.uninstall && exists && !managed) {
        console.error(`Error: REFUSE unmanaged existing directory: ${target}`);
        process.exit(1);
      }
      plan.push({ agent, baseDir, skillName, source, target, exists, managed, isHerdr: true });
    }
  }

  // Pre-install backup for multi-skill rollback on failure
  let backupDir = null;
  if (!options.dryRun && !options.uninstall) {
    const backupRoot = path.join(os.homedir(), '.herdr-backups');
    backupDir = path.join(backupRoot, `install-backup-${Date.now()}`);
    for (const item of plan) {
      if (item.exists && item.managed) {
        const itemBackup = path.join(backupDir, item.agent, item.skillName);
        fs.mkdirSync(path.dirname(itemBackup), { recursive: true });
        copyDirRecursive(item.target, itemBackup);
      }
    }
  }

  try {
    for (const item of plan) {
      if (item.isHerdr) {
        if (options.uninstall) {
          installSkill({ source: item.source, baseDir: item.baseDir, skillName: 'herdr', options });
        } else if (options.dryRun) {
          console.log(`INSTALL official herdr skill from 'herdr --skill' -> ${item.target}`);
        } else {
          let herdrSource = item.source;
          let tempDir = null;
          try {
            const herdrOutput = execFileSync('herdr', ['--skill'], { encoding: 'utf8', timeout: 5000 });
            if (herdrOutput && herdrOutput.startsWith('---')) {
              tempDir = path.join(os.tmpdir(), `herdr-official-${Date.now()}`);
              const tempSkillDir = path.join(tempDir, 'herdr');
              fs.mkdirSync(tempSkillDir, { recursive: true });
              fs.writeFileSync(path.join(tempSkillDir, 'SKILL.md'), herdrOutput);
              herdrSource = tempSkillDir;
            }
          } catch {
            console.log(`SKIP official herdr skill: 'herdr --skill' unavailable`);
          }
          if (fs.existsSync(path.join(herdrSource, 'SKILL.md'))) {
            installSkill({ source: herdrSource, baseDir: item.baseDir, skillName: 'herdr', options });
          }
          if (tempDir && fs.existsSync(tempDir)) {
            fs.rmSync(tempDir, { recursive: true, force: true });
          }
        }
      } else {
        installSkill({ source: item.source, baseDir: item.baseDir, skillName: item.skillName, options });
      }
    }
  } catch (err) {
    console.error(`ERROR during installation: ${err.message}`);
    // backupDir is only created when something was backed up; a fresh install still needs its new slots removed.
    if (backupDir) {
      console.error('Rolling back to previous installation...');
      let failures = 0;
      for (const item of plan) {
        const itemBackup = path.join(backupDir, item.agent, item.skillName);
        try {
          if (item.exists && item.managed && fs.existsSync(itemBackup)) {
            if (fs.existsSync(item.target)) {
              fs.rmSync(item.target, { recursive: true, force: true });
            }
            copyDirRecursive(itemBackup, item.target);
          } else if (!item.exists && fs.existsSync(path.join(item.target, '.herdr-role-pack-managed'))) {
            fs.rmSync(item.target, { recursive: true, force: true });
            console.error(`Removed newly created slot: ${item.target}`);
          }
        } catch (rollbackErr) {
          failures++;
          console.error(`ROLLBACK FAILED for ${item.target}: ${rollbackErr.message}`);
        }
      }
      console.error(failures
        ? `Rollback INCOMPLETE (${failures} slot(s)); originals remain in ${backupDir}`
        : 'Rollback completed.');
    }
    process.exit(1);
  }

  console.log(`Done. Bind session roles with 'tools/role_bind.py' or make role assignments manually from examples/roles.example.json.`);
}

main();
