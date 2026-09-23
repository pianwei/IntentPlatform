param([string]$RunName = ('intent-offline-' + (Get-Date -Format 'yyyyMMdd-HHmmss')))
$ErrorActionPreference = 'Stop'
if ($RunName -notmatch '^intent-offline-[a-z0-9-]{3,48}$') { throw 'Use an intent-offline- name with lowercase letters, digits and hyphens.' }
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$reportRoot = Join-Path $projectRoot "output/offline/$RunName"
if (Test-Path -LiteralPath $reportRoot) { throw 'Existing evidence is preserved; choose a fresh run name.' }
$dockerArgs = @('--config', (Join-Path $projectRoot '.local/docker'))
$composeArgs = @('compose', '-p', $RunName, '-f', (Join-Path $projectRoot 'infra/offline.compose.yaml'))
function Check-Exit([string]$Step) { if ($LASTEXITCODE -ne 0) { throw "$Step failed: $LASTEXITCODE" } }
$existing = & docker @dockerArgs ps -aq --filter "label=com.docker.compose.project=$RunName"
Check-Exit 'Container lookup'
$volume = & docker @dockerArgs volume ls -q --filter "name=^${RunName}_data$"
Check-Exit 'Volume lookup'
if ($existing -or $volume) { throw 'Existing containers or data are preserved; choose a fresh run name.' }
New-Item -ItemType Directory -Path $reportRoot | Out-Null
$started = Get-Date
try {
    & docker @dockerArgs @composeArgs up -d --wait --pull never --no-build
    Check-Exit 'Offline cold startup'
    $internal = & docker @dockerArgs network inspect "${RunName}_default" --format '{{.Internal}}'
    Check-Exit 'Network isolation lookup'
    if ($internal.Trim() -ne 'true') { throw 'Network must be internal.' }
    $probe = Get-Content -Raw (Join-Path $PSScriptRoot 'offline_probe.py')
    $result = $probe | & docker @dockerArgs @composeArgs exec -T api python -
    Check-Exit 'Offline API and Worker probe'
    $report = $result | ConvertFrom-Json
    $report | Add-Member -NotePropertyName run_name -NotePropertyValue $RunName
    $report | Add-Member -NotePropertyName elapsed_seconds -NotePropertyValue ([Math]::Round(((Get-Date)-$started).TotalSeconds, 2))
    $report | ConvertTo-Json -Depth 10 | Set-Content -Encoding utf8 (Join-Path $reportRoot 'report.json')
    Write-Output "Local engineering report: $reportRoot/report.json"
} catch {
    @{status='failed'; run_name=$RunName; acceptance_evidence=$false; error=$_.Exception.Message} | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $reportRoot 'failure.json')
    throw
} finally {
    & docker @dockerArgs @composeArgs stop
    if ($LASTEXITCODE -ne 0) { Write-Warning 'Some verification containers did not stop; inspect the retained project.' }
}
# Volumes and containers remain stopped for inspection; this script never deletes data.
