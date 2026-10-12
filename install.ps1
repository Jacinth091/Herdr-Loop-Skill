<#
.SYNOPSIS
  Install the Herdr Role Pack for Claude, Codex and Agy CLI on Windows.
.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Agents Claude,Codex -Roles Manager,Delegator -Force
.NOTES
  No Python/Node/network/admin required for core installation.
#>
[CmdletBinding()]
param(
  [ValidateSet('All','Claude','Codex','Agy')][string[]]$Agents = @('All'),
  [ValidateSet('All','Controller','Manager','Delegator','Worker','Verifier','Critic')][string[]]$Roles = @('All'),
  [switch]$Force,
  [switch]$DryRun,
  [switch]$Uninstall,
  [switch]$WithHerdr
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$skillNames = @{
  Controller = 'herdr-engineering-loop'
  Manager = 'herdr-manager'
  Delegator = 'herdr-delegator'
  Worker = 'herdr-worker'
  Verifier = 'herdr-verifier'
  Critic = 'herdr-critic'
}
$roleNames = if ($Roles -contains 'All') { @('Controller','Manager','Delegator','Worker','Verifier','Critic') } else { @($Roles | Select-Object -Unique) }
$agentNames = if ($Agents -contains 'All') { @('Claude','Codex','Agy') } else { @($Agents | Select-Object -Unique) }
$agentDirs = @{
  Claude = Join-Path $HOME '.claude\skills'
  Codex = Join-Path $HOME '.agents\skills'
  Agy = Join-Path $HOME '.gemini\antigravity-cli\skills'
}

function Install-PackSkill {
  param([string]$Source, [string]$Base, [string]$Name)
  $target = Join-Path $Base $Name
  $marker = Join-Path $target '.herdr-role-pack-managed'
  $exists = Test-Path -LiteralPath $target
  $managed = Test-Path -LiteralPath $marker
  if ($Uninstall) {
    if ($exists -and $managed) {
      Write-Output "REMOVE $target"
      if (-not $DryRun) { Remove-Item -LiteralPath $target -Recurse -Force }
    } else { Write-Output "SKIP removal (absent/unmanaged) $target" }
    return
  }
  if ($exists) {
    if (-not $managed) { throw "REFUSE unmanaged existing directory: $target" }
    if (-not $Force) { Write-Output "SKIP already installed: $target (use -Force to update)"; return }
  }
  if (-not (Test-Path -LiteralPath (Join-Path $Source 'SKILL.md') -PathType Leaf)) { throw "Missing source SKILL.md: $Source" }
  if ($DryRun) { Write-Output "INSTALL $Source -> $target"; return }
  if (-not (Test-Path -LiteralPath $Base)) { New-Item -ItemType Directory -Path $Base -Force | Out-Null }
  $stage = Join-Path $Base ('.herdr-stage-' + [guid]::NewGuid().ToString('N'))
  $stagedSkill = Join-Path $stage $Name
  $oldCopy = Join-Path $stage 'original'
  try {
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    Copy-Item -LiteralPath $Source -Destination $stagedSkill -Recurse -Force
    Set-Content -LiteralPath (Join-Path $stagedSkill '.herdr-role-pack-managed') -Value 'Herdr Role Pack v2.8' -Encoding UTF8
    if ($exists) { Move-Item -LiteralPath $target -Destination $oldCopy }
    try {
      Move-Item -LiteralPath $stagedSkill -Destination $target
    } catch {
      if (Test-Path -LiteralPath $oldCopy) { Move-Item -LiteralPath $oldCopy -Destination $target }
      throw
    }
    Write-Output "INSTALLED $target"
  } finally {
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
  }
}

foreach ($agent in $agentNames) {
  $base = $agentDirs[$agent]
  foreach ($role in $roleNames) {
    $name = $skillNames[$role]
    $source = Join-Path (Join-Path $root 'skills') $name
    Install-PackSkill -Source $source -Base $base -Name $name
  }
  if ($WithHerdr) {
    if ($Uninstall) {
      Install-PackSkill -Source (Join-Path $root 'skills\herdr') -Base $base -Name 'herdr'
    } elseif ($DryRun) {
      Write-Output "INSTALL official herdr skill from local 'herdr --skill' -> $base\herdr"
    } elseif (Get-Command herdr -ErrorAction SilentlyContinue) {
      $temporary = Join-Path ([System.IO.Path]::GetTempPath()) ('herdr-official-' + [guid]::NewGuid().ToString('N'))
      try {
        New-Item -ItemType Directory -Path (Join-Path $temporary 'herdr') -Force | Out-Null
        $text = (& herdr --skill) -join "`n"
        if ($LASTEXITCODE -ne 0 -or (-not $text.StartsWith('---'))) { throw 'herdr --skill returned invalid output' }
        [System.IO.File]::WriteAllText((Join-Path $temporary 'herdr\SKILL.md'), $text + "`n")
        Install-PackSkill -Source (Join-Path $temporary 'herdr') -Base $base -Name 'herdr'
      } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force }
      }
    } else {
      Write-Warning 'Herdr CLI is not installed. Optional official skill was skipped.'
    }
  }
}
Write-Output 'Done. Bind session roles with tools/role_bind.py or make role assignments manually from examples/roles.example.json.'
