param(
    [string]$SuiteRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\source')).Path
)

$ErrorActionPreference = 'Stop'
$skillSource = Join-Path $PSScriptRoot 'media-text-suite'
$skillTarget = Join-Path $HOME '.codex\skills\media-text-suite'

if (-not (Test-Path -LiteralPath (Join-Path $SuiteRoot 'desktop\agent_cli.py'))) {
    throw "Media Text Suite source not found under: $SuiteRoot"
}

New-Item -ItemType Directory -Path (Split-Path -Parent $skillTarget) -Force | Out-Null
if (Test-Path -LiteralPath $skillTarget) {
    Remove-Item -LiteralPath $skillTarget -Recurse -Force
}
Copy-Item -LiteralPath $skillSource -Destination $skillTarget -Recurse
[Environment]::SetEnvironmentVariable('MEDIA_TEXT_SUITE_HOME', $SuiteRoot, 'User')
$env:MEDIA_TEXT_SUITE_HOME = $SuiteRoot

Write-Host "Skill installed: $skillTarget"
Write-Host "Suite source: $SuiteRoot"
Write-Host 'Restart or refresh Codex to load the Skill.'
