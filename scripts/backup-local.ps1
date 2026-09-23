param([string]$Label = 'manual')
$ErrorActionPreference = 'Stop'
if ($Label -notmatch '^[a-zA-Z0-9_-]{1,40}$') { throw 'Label must contain 1-40 letters, digits, underscore or hyphen.' }
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$backupRoot = Join-Path $projectRoot '.local\backups'
New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
$archiveName = 'intent-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + $Label + '.dump'
$archivePath = Join-Path $backupRoot $archiveName
$containerPath = '/tmp/' + $archiveName
$dockerArgs = @('--config', (Join-Path $projectRoot '.local\docker'))
Push-Location -LiteralPath $projectRoot
try {
    # Local backup is an administrative operation. The migration owner cannot export
    # FORCE RLS tables; never weaken tenant policies or elevate the API/Worker role.
    & docker @dockerArgs compose exec -T postgres pg_dump -U intent_admin -d intent -Fc -f $containerPath
    if ($LASTEXITCODE -ne 0) { throw 'Database dump failed; no usable backup was reported.' }
    & docker @dockerArgs compose cp "postgres:${containerPath}" $archivePath
    if ($LASTEXITCODE -ne 0) { throw 'Copying the database archive failed.' }
    if (!(Test-Path -LiteralPath $archivePath) -or (Get-Item -LiteralPath $archivePath).Length -eq 0) { throw 'Database archive is missing or empty.' }
    Write-Output $archivePath
} finally { Pop-Location }
