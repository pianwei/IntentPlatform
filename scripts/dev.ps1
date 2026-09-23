param(
    [ValidateSet('database','up','stop','status','logs','test-frontend')]
    [string]$Action = 'status'
)
$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Push-Location -LiteralPath $projectRoot
try {
    $dockerConfig = Join-Path $projectRoot '.local\docker'
    $dockerArgs = @('--config', $dockerConfig)
    switch ($Action) {
        'database' { & docker @dockerArgs compose up -d --wait postgres }
        'up' { & docker @dockerArgs compose --profile app up --build -d --wait }
        'stop' { & docker @dockerArgs compose --profile app stop }
        'status' { & docker @dockerArgs compose --profile app ps -a }
        'logs' { & docker @dockerArgs compose --profile app logs --tail 100 }
        'test-frontend' {
            $frontendRoot = Join-Path $projectRoot 'frontend'
            & docker @dockerArgs run --rm -v "${frontendRoot}:/work" -w /work node:22.16.0-alpine npm test
        }
    }
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }
