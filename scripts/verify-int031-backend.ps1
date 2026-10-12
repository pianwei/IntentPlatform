param([switch]$Full, [string[]]$TestPath = @())
$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$backendRoot = Join-Path $projectRoot 'backend'
$dockerConfig = Join-Path $projectRoot '.local/docker'
$runId = Get-Date -Format 'yyyyMMdd-HHmmss'
$resource = "intent-int031-$runId"
$databaseContainer = "$resource-pg"
$runnerContainer = "$resource-runner"
$network = "$resource-net"
$database = 'intent_int031_test'
$ownerLabel = 'INT-031-isolated-backend'
$reportDir = Join-Path $projectRoot "output/INT-031/backend-$runId"
$resultCode = 1
$networkCreated = $false
$databaseCreated = $false
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null

function Invoke-Docker([string[]]$Arguments, [string]$Step) {
    & docker --config $dockerConfig @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE" }
}
function Get-Label([string]$Container) {
    & docker --config $dockerConfig inspect --format '{{ index .Config.Labels "intent.test.owner" }}' $Container 2>$null
}

try {
    $existingContainers = & docker --config $dockerConfig ps -a --filter "name=^/$databaseContainer`$" --format '{{.Names}}'
    if ($LASTEXITCODE -ne 0 -or $existingContainers) { throw 'A conflicting PostgreSQL test container exists; refusing to reuse it' }
    $existingNetwork = & docker --config $dockerConfig network ls --filter "name=^$network`$" --format '{{.Name}}'
    if ($LASTEXITCODE -ne 0 -or $existingNetwork) { throw 'A conflicting test network exists; refusing to reuse it' }

    Invoke-Docker @('network','create','--internal','--label',"intent.test.owner=$ownerLabel",$network) 'Isolated network creation' | Out-Null
    $networkCreated = $true
    Invoke-Docker @('run','-d','--network',$network,'--name',$databaseContainer,'--label',"intent.test.owner=$ownerLabel",
        '--tmpfs','/var/lib/postgresql/data:rw,nosuid,nodev,size=1g','-e','POSTGRES_HOST_AUTH_METHOD=trust',
        '-e','POSTGRES_USER=intent_test_admin','-e',"POSTGRES_DB=$database",'postgres:17.6-alpine') 'Disposable PostgreSQL creation' | Out-Null
    $databaseCreated = $true

    $ready = $false
    for ($attempt = 0; $attempt -lt 50; $attempt++) {
        # pg_isready can succeed against the image's temporary initdb server,
        # which shuts down before the final server starts. Require a real TCP query.
        & docker --config $dockerConfig exec $databaseContainer psql -h 127.0.0.1 -U intent_test_admin -d $database -tAc 'SELECT 1' *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (!$ready) { throw 'Disposable PostgreSQL did not become ready' }

    $roleSql = "CREATE ROLE intent_owner LOGIN NOSUPERUSER NOBYPASSRLS; ALTER DATABASE $database OWNER TO intent_owner; ALTER SCHEMA public OWNER TO intent_owner; CREATE ROLE intent_app LOGIN NOSUPERUSER NOBYPASSRLS; CREATE EXTENSION IF NOT EXISTS pgcrypto; GRANT CONNECT ON DATABASE $database TO intent_app; GRANT USAGE ON SCHEMA public TO intent_app; ALTER DEFAULT PRIVILEGES FOR ROLE intent_owner IN SCHEMA public GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO intent_app; ALTER DEFAULT PRIVILEGES FOR ROLE intent_owner IN SCHEMA public GRANT USAGE,SELECT ON SEQUENCES TO intent_app;"
    Invoke-Docker @('exec',$databaseContainer,'psql','-U','intent_test_admin','-d',$database,'-v','ON_ERROR_STOP=1','-c',$roleSql) 'Restricted database role setup' *> (Join-Path $reportDir 'database-setup.log')

    $image = 'intent-backend:int029'
    & docker --config $dockerConfig image inspect $image *> (Join-Path $reportDir 'runner-image.log')
    if ($LASTEXITCODE -ne 0) { throw "Required cached test image is unavailable: $image" }
    $pytestArgs = @('python','-m','pytest','-q','--tb=short','--junitxml=/reports/results.xml')
    if (!$Full) {
        if ($TestPath.Count) {
            $selectedTests = @($TestPath | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
            $pytestArgs += $selectedTests
        }
        else { $pytestArgs += @('tests/test_business_catalog.py','tests/test_output_contract.py','tests/test_connector_transport.py') }
    }
    $envArgs = @('-e','INTENT_ENV=test','-e','INTENT_IDENTITY_MODE=legacy-test','-e','INTENT_ALLOW_DEV_AUTH=true',
        '-e','INTENT_MODEL_MODE=fixture','-e','INTENT_ALLOW_PUBLIC_DEEPSEEK_MODEL_TEST=false','-e','INTENT_PLATFORM_ADMINS_JSON=',
        '-e','INTENT_IDENTITY_DIRECTORY_JSON=',
        '-e',"INTENT_DATABASE_URL=postgresql+psycopg://intent_app@$databaseContainer`:5432/$database",
        '-e',"INTENT_MIGRATION_DATABASE_URL=postgresql+psycopg://intent_owner@$databaseContainer`:5432/$database")
    & docker --config $dockerConfig run --rm --network $network --name $runnerContainer --label "intent.test.owner=$ownerLabel" `
        -v "${backendRoot}:/app" -v "${reportDir}:/reports" -w /app @envArgs $image @pytestArgs *> (Join-Path $reportDir 'pytest.log')
    $resultCode = $LASTEXITCODE
} catch {
    $_.Exception.Message | Set-Content (Join-Path $reportDir 'runner-error.txt')
    $resultCode = 1
} finally {
    $runnerExists = & docker --config $dockerConfig ps -a --filter "name=^/$runnerContainer`$" --format '{{.Names}}' 2>$null
    if ($runnerExists) {
        if ((Get-Label $runnerContainer) -eq $ownerLabel) {
            & docker --config $dockerConfig logs $runnerContainer *> (Join-Path $reportDir 'runner-container.log')
            & docker --config $dockerConfig rm -f -v $runnerContainer *> (Join-Path $reportDir 'runner-cleanup.log')
            if ($LASTEXITCODE -ne 0) { $resultCode = 1 }
        } else { $resultCode = 1; 'Runner ownership verification failed' | Set-Content (Join-Path $reportDir 'runner-cleanup.log') }
    }
    if ($databaseCreated) {
        & docker --config $dockerConfig logs $databaseContainer *> (Join-Path $reportDir 'database.log')
        if ((Get-Label $databaseContainer) -eq $ownerLabel) {
            & docker --config $dockerConfig rm -f -v $databaseContainer *> (Join-Path $reportDir 'database-cleanup.log')
            if ($LASTEXITCODE -ne 0) { $resultCode = 1 }
        } else { $resultCode = 1; 'Database ownership verification failed' | Set-Content (Join-Path $reportDir 'database-cleanup.log') }
    }
    if ($networkCreated) {
        $members = & docker --config $dockerConfig network inspect --format '{{len .Containers}}' $network 2>$null
        $label = & docker --config $dockerConfig network inspect --format '{{ index .Labels "intent.test.owner" }}' $network 2>$null
        if ($label -eq $ownerLabel -and $members -eq '0') {
            & docker --config $dockerConfig network rm $network *> (Join-Path $reportDir 'network-cleanup.log')
            if ($LASTEXITCODE -ne 0) { $resultCode = 1 }
        } else { $resultCode = 1; "Network cleanup refused; owner=$label members=$members" | Set-Content (Join-Path $reportDir 'network-cleanup.log') }
    }
    foreach ($path in @('pytest.log','database.log','runner-container.log')) {
        $file = Join-Path $reportDir $path
        if (Test-Path $file) {
            $content = Get-Content -LiteralPath $file -Raw
            $content = [regex]::Replace($content, 'sk-[A-Za-z0-9]{20,}', '[redacted-key]')
            $content = [regex]::Replace($content, '(?i)(Bearer\s+)[A-Za-z0-9._~-]{24,}', '$1[redacted-token]')
            Set-Content -LiteralPath $file -Value $content
        }
    }
    "exit=$resultCode" | Set-Content (Join-Path $reportDir 'exit.txt')
}

Write-Output "INT-031 backend exit: $resultCode; report: $reportDir; full=$Full"
if (Test-Path (Join-Path $reportDir 'pytest.log')) { Get-Content (Join-Path $reportDir 'pytest.log') -Tail 25 }
exit $resultCode
