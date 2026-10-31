# install.ps1: Windows equivalent of install.sh (directory junctions, so no admin rights needed).
#   powershell -ExecutionPolicy Bypass -File install.ps1 [-Venv] [-Optional] [-Npm] [-Mcp]
# Starts/stops no processes, so it is safe while other agents are running.
param([switch]$Venv, [switch]$Optional, [switch]$Npm, [switch]$Mcp)
$ErrorActionPreference = 'Stop'
$Repo = $PSScriptRoot
$VpHome = if ($env:VP_HOME) { $env:VP_HOME } else { Join-Path $HOME '.config\global-agent-skills\video-production' }
$Skills = if ($env:CLAUDE_SKILLS_DIR) { $env:CLAUDE_SKILLS_DIR } else { Join-Path $HOME '.claude\skills' }

function Link($Target, $Path) {
  if (Test-Path $Path) {
    $item = Get-Item $Path -Force
    if ($item.LinkType) { Remove-Item $Path -Force } else { Write-Warning "SKIP $Path exists (not a link)"; return }
  }
  New-Item -ItemType Junction -Path $Path -Target $Target | Out-Null
  Write-Host "link $Path -> $Target"
}

New-Item -ItemType Directory -Force -Path (Split-Path $VpHome), $Skills | Out-Null
if ((Resolve-Path $Repo).Path -ne $VpHome) { Link $Repo $VpHome }
Get-ChildItem $Repo -Recurse -Filter SKILL.md | Where-Object { $_.FullName -notmatch 'node_modules' } | ForEach-Object {
  $name = (Select-String -Path $_.FullName -Pattern '^name: (.+)$' | Select-Object -First 1).Matches[0].Groups[1].Value
  Link $_.DirectoryName (Join-Path $Skills $name)
}
if ($Venv) {
  py -3 -m venv (Join-Path $Repo '.venv')
  $pip = Join-Path $Repo '.venv\Scripts\pip.exe'
  & $pip install -q -r (Join-Path $Repo 'requirements.txt')
  if ($Optional) { & $pip install -q -r (Join-Path $Repo 'requirements-optional.txt') }
}
if ($Npm) { Push-Location $Repo; npm ci --no-audit --no-fund; Pop-Location }
if ($Mcp) {
  $py = Join-Path $VpHome '.venv\Scripts\python.exe'
  claude mcp add --scope user ffmpeg-bridge -- $py (Join-Path $VpHome 'mcp-extensions\ffmpeg-bridge\server.py')
}
