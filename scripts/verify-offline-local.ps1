param(
    [string]$RunName = ('intent-offline-' + (Get-Date -Format 'yyyyMMdd-HHmmss')),
    [ValidatePattern('^[a-zA-Z0-9./:_-]+$')][string]$BackendImage = 'intent-backend:local',
    [ValidatePattern('^[a-zA-Z0-9./:_-]+$')][string]$WebImage = 'intent-web:local'
)
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
$existingNetwork = & docker @dockerArgs network ls -q --filter "name=^${RunName}_default$"
Check-Exit 'Network lookup'
if ($existing -or $volume -or $existingNetwork) { throw 'Existing containers, networks or data are preserved; choose a fresh run name.' }
New-Item -ItemType Directory -Path $reportRoot | Out-Null
$imageOverride = Join-Path $reportRoot 'candidate-images.yaml'
@"
services:
  migrate:
    image: $BackendImage
  api:
    image: $BackendImage
  worker:
    image: $BackendImage
  ingest-worker:
    image: $BackendImage
  web:
    image: $WebImage
"@ | Set-Content -Encoding utf8 $imageOverride
$composeArgs += @('-f', $imageOverride)
$started = Get-Date
$verificationError = $null
$cleanupError = $null
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
    $verificationError = $_
    @{status='failed'; run_name=$RunName; acceptance_evidence=$false; error=$_.Exception.Message} | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $reportRoot 'failure.json')
} finally {
    if ($verificationError) {
        # This environment contains only synthetic fixtures. Keep bounded failure
        # diagnostics outside Docker before removing the disposable environment.
        try {
            $diagnostics = & docker @dockerArgs @composeArgs logs --no-color --tail 200 2>&1
            $diagnostics | Set-Content -Encoding utf8 (Join-Path $reportRoot 'failure-containers.log')
            if ($LASTEXITCODE -ne 0) { Write-Warning 'Some failure logs were unavailable; cleanup will still run.' }
        } catch { Write-Warning 'Could not save container logs; cleanup will still run.' }
    }
    try {
        # RunName is constrained and preflight rejected existing resources. Never
        # prune Docker globally or remove images shared with the main application.
        & docker @dockerArgs @composeArgs down --volumes --timeout 5
        Check-Exit 'Offline resource cleanup'
        $remainingContainers = & docker @dockerArgs ps -aq --filter "label=com.docker.compose.project=$RunName"
        Check-Exit 'Cleanup container check'
        $remainingVolume = & docker @dockerArgs volume ls -q --filter "name=^${RunName}_data$"
        Check-Exit 'Cleanup volume check'
        $remainingNetwork = & docker @dockerArgs network ls -q --filter "name=^${RunName}_default$"
        Check-Exit 'Cleanup network check'
        if ($remainingContainers -or $remainingVolume -or $remainingNetwork) { throw 'Offline resources remain after cleanup.' }
        @{status='passed'; run_name=$RunName; containers_removed=$true; volume_removed=$true; network_removed=$true; reports_preserved=$true} |
            ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $reportRoot 'cleanup.json')
    } catch {
        $cleanupError = $_
        @{status='failed'; run_name=$RunName; error=$_.Exception.Message; reports_preserved=$true} |
            ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $reportRoot 'cleanup.json')
    }
}
if ($verificationError) {
    if ($cleanupError) { Write-Warning "Cleanup also failed: $($cleanupError.Exception.Message)" }
    throw $verificationError
}
if ($cleanupError) { throw $cleanupError }
