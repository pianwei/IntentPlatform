param([string[]]$TestPaths = @('tests/test_local_identity.py'), [switch]$ProcessFaults)
$ErrorActionPreference = 'Stop'
$backendRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $backendRoot '..'))
$dockerConfig = Join-Path $projectRoot '.local/docker'
$testContainer = if ($ProcessFaults) { 'intent-int021-process-test' } else { 'intent-int021-auth-test' }
$testDatabase = if ($ProcessFaults) { 'intent_process_faults_test' } else { 'intent_int021_identity_test' }
$reportDir = Join-Path $projectRoot ('output/INT-021/identity-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $reportDir | Out-Null
$created = $false
$networkCreated = $false
$testNetwork = "${testContainer}-net"
$resultCode = 1
Push-Location -LiteralPath $backendRoot
try {
    $existing = & docker --config $dockerConfig ps -a --filter "name=^/$testContainer`$" --format '{{.Names}}'
    if ($LASTEXITCODE -ne 0) { throw 'Docker inventory failed' }
    if ($existing) { throw 'Existing test container must not be reused or overwritten' }
    & docker --config $dockerConfig network create --internal --label 'intent.test.owner=INT-021-identity' $testNetwork | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Isolated network creation failed' }
    $networkCreated=$true
    & docker --config $dockerConfig run -d --network $testNetwork --name $testContainer --label 'intent.test.owner=INT-021-identity' -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_USER=intent_test_admin -e "POSTGRES_DB=$testDatabase" postgres:17.6-alpine | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Isolated PostgreSQL creation failed' }
    $created = $true
    $ready = $false
    for ($attempt=0; $attempt -lt 40; $attempt++) {
        # pg_isready can return success during the entrypoint's temporary
        # bootstrap server, before the requested POSTGRES_DB exists. Confirm
        # a real query against the target database before creating roles.
        & docker --config $dockerConfig exec $testContainer psql -h 127.0.0.1 -U intent_test_admin -d $testDatabase -tAc 'SELECT 1' *> $null
        if ($LASTEXITCODE -eq 0) { $ready=$true; break }
        Start-Sleep -Milliseconds 500
    }
    if (!$ready) { throw 'Isolated PostgreSQL did not become ready' }
    # Restricted owner/app test roles. Trust is confined to this disposable
    # internal-only network; no credentials from the principal service are read.
    & docker --config $dockerConfig exec $testContainer psql -U intent_test_admin -d $testDatabase -v ON_ERROR_STOP=1 -c "CREATE ROLE intent_owner LOGIN NOSUPERUSER NOBYPASSRLS; ALTER DATABASE $testDatabase OWNER TO intent_owner; ALTER SCHEMA public OWNER TO intent_owner; CREATE ROLE intent_app LOGIN NOSUPERUSER NOBYPASSRLS; CREATE EXTENSION IF NOT EXISTS pgcrypto; GRANT USAGE ON SCHEMA public TO intent_app; ALTER DEFAULT PRIVILEGES FOR ROLE intent_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO intent_app; ALTER DEFAULT PRIVILEGES FOR ROLE intent_owner IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO intent_app;" *> (Join-Path $reportDir 'database-setup.log')
    if ($LASTEXITCODE -ne 0) { throw 'Test database role setup failed' }
    if ($ProcessFaults) {
        $faultReports = Join-Path $reportDir 'process-faults'
        New-Item -ItemType Directory -Path $faultReports | Out-Null
        # Share ONLY this disposable PostgreSQL container's network namespace.
        # The process harness keeps its strict 127.0.0.1 database/HTTP guards.
        & docker --config $dockerConfig run --rm --network "container:$testContainer" --name "${testContainer}-runner" --label 'intent.test.owner=INT-021-identity' -v "${backendRoot}:/app" -v "${faultReports}:/output/process-faults" -w /app -e "INTENT_PROCESS_DATABASE_URL=postgresql+psycopg://intent_app@127.0.0.1:5432/$testDatabase" -e "INTENT_PROCESS_MIGRATION_DATABASE_URL=postgresql+psycopg://intent_owner@127.0.0.1:5432/$testDatabase" intent-backend:int021-test python scripts/verify_process_faults.py --run *> (Join-Path $reportDir 'pytest.log')
    } else {
        & docker --config $dockerConfig run --rm --network $testNetwork --name "${testContainer}-runner" --label 'intent.test.owner=INT-021-identity' -v "${backendRoot}:/app" -v "${reportDir}:/reports" -w /app -e INTENT_ENV=test -e INTENT_IDENTITY_MODE=legacy-test -e INTENT_ALLOW_DEV_AUTH=true -e INTENT_MODEL_MODE=fixture -e INTENT_ALLOW_PUBLIC_DEEPSEEK_MODEL_TEST=false -e "INTENT_DATABASE_URL=postgresql+psycopg://intent_app@${testContainer}:5432/$testDatabase" -e "INTENT_MIGRATION_DATABASE_URL=postgresql+psycopg://intent_owner@${testContainer}:5432/$testDatabase" intent-backend:int021-test python -m pytest @TestPaths -q --tb=short --junitxml=/reports/results.xml *> (Join-Path $reportDir 'pytest.log')
    }
    $resultCode = $LASTEXITCODE
} catch {
    $_.Exception.Message | Set-Content (Join-Path $reportDir 'runner-error.txt')
} finally {
    $runnerName = "${testContainer}-runner"
    $runnerExists = & docker --config $dockerConfig ps -a --filter "name=^/$runnerName`$" --format '{{.Names}}'
    if ($runnerExists) {
        $runnerLabel = & docker --config $dockerConfig inspect --format '{{ index .Config.Labels "intent.test.owner" }}' $runnerName
        if ($runnerLabel -eq 'INT-021-identity') {
            & docker --config $dockerConfig logs $runnerName *> (Join-Path $reportDir 'runner-container.log')
            & docker --config $dockerConfig rm -f -v $runnerName *> (Join-Path $reportDir 'runner-cleanup.log')
            if ($LASTEXITCODE -ne 0) { $resultCode=1 }
        } else { $resultCode=1 }
    }
    if ($created) {
        & docker --config $dockerConfig logs $testContainer *> (Join-Path $reportDir 'database.log')
        foreach ($file in Get-ChildItem -LiteralPath $reportDir -File -Recurse) {
            $content = Get-Content -LiteralPath $file.FullName -Raw
            $content = $content -replace '(?<![A-Za-z0-9_-])(?!test_)[A-Za-z0-9_-]{40,}(?![A-Za-z0-9_-])', '[redacted-value]'
            Set-Content -LiteralPath $file.FullName -Value $content
        }
        $label = & docker --config $dockerConfig inspect --format '{{ index .Config.Labels "intent.test.owner" }}' $testContainer
        if ($label -eq 'INT-021-identity') {
            & docker --config $dockerConfig rm -f -v $testContainer *> (Join-Path $reportDir 'cleanup.log')
            if ($LASTEXITCODE -ne 0) { $resultCode=1 }
        } else { $resultCode=1; 'Ownership verification failed' | Set-Content (Join-Path $reportDir 'cleanup.log') }
    }
    if ($networkCreated) {
        & docker --config $dockerConfig network rm $testNetwork *> (Join-Path $reportDir "network-cleanup.log")
        if ($LASTEXITCODE -ne 0) { $resultCode=1 }
    }
    Pop-Location
}
Write-Output "Identity test exit: $resultCode; report: $reportDir"
if (Test-Path (Join-Path $reportDir 'pytest.log')) { Get-Content (Join-Path $reportDir 'pytest.log') -Tail 12 }
exit $resultCode
