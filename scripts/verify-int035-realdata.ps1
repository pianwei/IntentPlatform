param([Parameter(Mandatory=$true)][string]$BackupPath,[switch]$PreflightOnly,[switch]$SmokeOnly,[switch]$FocusedOnly,[switch]$FullHybrid,[string[]]$CaseIds)
$ErrorActionPreference = 'Stop'

$modeCount = 0
foreach ($enabled in @($PreflightOnly.IsPresent,$SmokeOnly.IsPresent,$FocusedOnly.IsPresent,$FullHybrid.IsPresent,[bool]$CaseIds)) {
    if ($enabled) { $modeCount++ }
}
if ($modeCount -gt 1) { throw 'Choose only one INT-035 evaluation mode' }

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$backendRoot = Join-Path $projectRoot 'backend'
$dockerConfig = Join-Path $projectRoot '.local/docker'
$backupRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot '.local/backups'))
$archive = (Resolve-Path -LiteralPath $BackupPath).Path
$localData = Join-Path $backendRoot 'evaluation_data/local'
$snapshot = Join-Path $localData 'super_crm_snapshot.json'
$reportRoot = Join-Path $localData 'reports'

if (!(Test-Path -LiteralPath $archive -PathType Leaf) -or [IO.Path]::GetExtension($archive) -ne '.dump') { throw 'Use an existing local PostgreSQL .dump backup' }
if (!$archive.StartsWith($backupRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Backup must be inside .local/backups' }
if (!(Test-Path -LiteralPath $snapshot -PathType Leaf)) { throw 'The approved sanitized source snapshot is missing' }
Push-Location $projectRoot
try {
    & git check-ignore -q -- 'backend/evaluation_data/local/reports'
    if ($LASTEXITCODE -ne 0) { throw 'INT-035 evaluation evidence directory is not Git-ignored' }
} finally { Pop-Location }
if (!(Test-Path -LiteralPath (Join-Path $projectRoot '.env') -PathType Leaf) -or
    !(Test-Path -LiteralPath (Join-Path $projectRoot '.local/INT021-admin.json') -PathType Leaf)) {
    throw 'Local model configuration or evaluation administrator credentials are unavailable'
}

$runId = Get-Date -Format 'yyyyMMdd-HHmmss'
$resource = "intent-int035-realdata-$runId"
$databaseContainer = "$resource-pg"
$runnerContainer = "$resource-runner"
$network = "$resource-net"
$database = 'intent_int035_realdata_candidate'
$ownerLabel = 'INT-035-realdata-evaluation'
$reportDir = Join-Path $reportRoot $runId
$modelOutput = if ($PreflightOnly) { '/reports/preflight-contract' } elseif ($SmokeOnly) { '/reports/model-smoke' } elseif ($FullHybrid) { '/reports/model-hybrid-full' } elseif ($CaseIds) { '/reports/model-selected' } elseif ($FocusedOnly) { '/reports/model-focused' } else { '/reports/model-16' }
$resultCode = 1
$databaseCreated = $false
$networkCreated = $false
$mainBefore = $null
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null

function Invoke-Docker([string[]]$Arguments, [string]$Step) {
    & docker --config $dockerConfig @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE" }
}
function Get-Label([string]$Container) {
    & docker --config $dockerConfig inspect --format '{{ index .Config.Labels "intent.test.owner" }}' $Container 2>$null
}
function Get-MainContainerSnapshot {
    $rows = & docker --config $dockerConfig ps -a --filter 'name=^/intent-local-' --format '{{.ID}} {{.Names}} {{.State}}'
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect shared local service state' }
    return (@($rows | Sort-Object) -join "`n")
}

try {
    $mainBefore = Get-MainContainerSnapshot
    $existing = & docker --config $dockerConfig ps -a --filter "name=^/$databaseContainer`$" --format '{{.Names}}'
    if ($LASTEXITCODE -ne 0 -or $existing) { throw 'A conflicting INT-035 test database container exists' }
    $existing = & docker --config $dockerConfig ps -a --filter "name=^/$runnerContainer`$" --format '{{.Names}}'
    if ($LASTEXITCODE -ne 0 -or $existing) { throw 'A conflicting INT-035 test runner container exists' }
    $existing = & docker --config $dockerConfig network ls --filter "name=^$network`$" --format '{{.Name}}'
    if ($LASTEXITCODE -ne 0 -or $existing) { throw 'A conflicting INT-035 test network exists' }

    Invoke-Docker @('network','create','--label',"intent.test.owner=$ownerLabel",$network) 'Isolated evaluation network creation' | Out-Null
    $networkCreated = $true
    Invoke-Docker @('run','-d','--network',$network,'--name',$databaseContainer,'--label',"intent.test.owner=$ownerLabel",
        '--network-alias','postgres','--tmpfs','/var/lib/postgresql/data:rw,nosuid,nodev,size=1g',
        '-e','POSTGRES_HOST_AUTH_METHOD=trust','-e','POSTGRES_USER=intent_test_admin',
        '-e',"POSTGRES_DB=$database",'postgres:17.6-alpine') 'Disposable evaluation database creation' | Out-Null
    $databaseCreated = $true

    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        & docker --config $dockerConfig exec $databaseContainer psql -h 127.0.0.1 -U intent_test_admin -d $database -tAc 'SELECT 1' *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (!$ready) { throw 'Disposable evaluation database did not become ready' }

    $roles = @'
CREATE ROLE intent_owner LOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE intent_app LOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE intent_admin NOLOGIN;
ALTER DATABASE intent_int035_realdata_candidate OWNER TO intent_owner;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
'@
    Invoke-Docker @('exec',$databaseContainer,'psql','-U','intent_test_admin','-d',$database,'-v','ON_ERROR_STOP=1','-c',$roles) 'Isolated role setup' *> (Join-Path $reportDir 'roles.log')
    Invoke-Docker @('cp',$archive,"${databaseContainer}:/tmp/int035-source.dump") 'Approved backup copy'
    Invoke-Docker @('exec',$databaseContainer,'pg_restore','-U','intent_test_admin','-d',$database,'--exit-on-error','--no-owner','/tmp/int035-source.dump') 'Disposable backup restore' *> (Join-Path $reportDir 'restore.log')

    $ownership = @'
ALTER SCHEMA public OWNER TO intent_owner;
DO $body$
DECLARE item record;
BEGIN
  FOR item IN SELECT schemaname, tablename FROM pg_tables WHERE schemaname='public' LOOP
    EXECUTE format('ALTER TABLE %I.%I OWNER TO intent_owner', item.schemaname, item.tablename);
  END LOOP;
  FOR item IN SELECT schemaname, sequencename FROM pg_sequences WHERE schemaname='public' LOOP
    EXECUTE format('ALTER SEQUENCE %I.%I OWNER TO intent_owner', item.schemaname, item.sequencename);
  END LOOP;
  FOR item IN SELECT schemaname, viewname FROM pg_views WHERE schemaname='public' LOOP
    EXECUTE format('ALTER VIEW %I.%I OWNER TO intent_owner', item.schemaname, item.viewname);
  END LOOP;
  FOR item IN
    SELECT n.nspname AS schemaname, p.proname AS routine_name,
           pg_get_function_identity_arguments(p.oid) AS arguments,
           CASE p.prokind WHEN 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END AS routine_kind
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.prokind IN ('f','p')
  LOOP
    EXECUTE format('ALTER %s %I.%I(%s) OWNER TO intent_owner',
                   item.routine_kind, item.schemaname, item.routine_name, item.arguments);
  END LOOP;
END
$body$;
'@
    Invoke-Docker @('exec',$databaseContainer,'psql','-U','intent_test_admin','-d',$database,'-v','ON_ERROR_STOP=1','-c',$ownership) 'Restored schema ownership' *> (Join-Path $reportDir 'ownership.log')

    $image = 'intent-backend:local'
    Invoke-Docker @('image','inspect',$image) 'Cached backend runner image check' *> (Join-Path $reportDir 'runner-image.log')
    $modelArgs = @('--code-root','/app','--database',$database,'--database-host','postgres',
        '--data-snapshot','evaluation_data/local/super_crm_snapshot.json','--output',$modelOutput)
    $modelArgs += '--document-skills'
    if ($PreflightOnly) {
        $modelArgs += @('--source-contract-preflight','--ids','crm-profile-01')
    } elseif ($SmokeOnly) {
        $modelArgs += @('--contract-smoke-only','--fail-fast','--ids','crm-profile-01')
    } elseif ($FullHybrid) {
        # The approved full target is 384 cases. Run every currently frozen
        # hybrid candidate up to that cap and preserve per-origin denominators.
        $modelArgs += @('--limit','384')
    } elseif ($CaseIds) {
        $modelArgs += @('--fail-fast','--ids') + $CaseIds
    } elseif ($FocusedOnly) {
        $modelArgs += @('--fail-fast','--ids',
            'crm-customer-search-01','crm-product-search-01','crm-product-detail-01',
            'crm-segment-list-01','crm-marketing-list-01','crm-visit-list-01',
            'crm-skill-product-comparison-01','crm-autonomous-single-01','crm-autonomous-multi-01',
            'sim-fill-fallback-01','crm-permission-cross-manager-01')
    } else {
        $modelArgs += @('--fail-fast','--ids',
            'crm-profile-01','crm-profile-02','crm-profile-03','crm-profile-04','crm-profile-05',
            'crm-customer-search-01','crm-product-search-01','crm-product-detail-01',
            'crm-segment-list-01','crm-marketing-list-01','crm-visit-list-01',
            'crm-skill-product-comparison-01','crm-autonomous-single-01','crm-autonomous-multi-01',
            'sim-fill-fallback-01','crm-permission-cross-manager-01')
    }
    $envArgs = @('-e','INTENT_ENV=development','-e','INTENT_IDENTITY_MODE=local','-e','INTENT_ALLOW_DEV_AUTH=false',
        '-e','INTENT_MODEL_MODE=fixture','-e','INTENT_ALLOW_PUBLIC_DEEPSEEK_MODEL_TEST=true',
        '-e','INTENT_PLATFORM_ADMINS_JSON=','-e','INTENT_IDENTITY_DIRECTORY_JSON=',
        '-e',"INTENT_DATABASE_URL=postgresql+psycopg://intent_app:local-app-only@$databaseContainer`:5432/$database",
        '-e',"INTENT_MIGRATION_DATABASE_URL=postgresql+psycopg://intent_owner:local-owner-only@$databaseContainer`:5432/$database",
        '-e',"INTENT_LOCAL_ADMIN_DATABASE_URL=postgresql+psycopg://intent_test_admin@$databaseContainer`:5432/$database",
        '-e','PYTHONDONTWRITEBYTECODE=1')
    & docker --config $dockerConfig run --rm --network $network --name $runnerContainer --label "intent.test.owner=$ownerLabel" `
        -v "${projectRoot}:/work:ro" -v "${backendRoot}:/app:ro" -v "${reportDir}:/reports" `
        -w /work/backend @envArgs $image python scripts/verify_zhiyu_model.py @modelArgs *> (Join-Path $reportDir 'model-run.log')
    $resultCode = $LASTEXITCODE
    "exit=$resultCode" | Set-Content (Join-Path $reportDir 'model-run.exit')

    $contractFile = Join-Path $reportDir 'preflight-contract/pinned-release-contract-preflight.json'
    if (Test-Path -LiteralPath $contractFile -PathType Leaf) {
        $contract = Get-Content -LiteralPath $contractFile -Raw | ConvertFrom-Json
        [ordered]@{
            status = $contract.status
            sourceProcessorPreflight = (Get-Content -LiteralPath (Join-Path $reportDir 'preflight-contract/source-processor-preflight.json') -Raw | ConvertFrom-Json).status
            sourceCaseCandidates = (Get-Content -LiteralPath (Join-Path $reportDir 'preflight-contract/source-processor-preflight.json') -Raw | ConvertFrom-Json).source_case_count
            simulatorFillCandidates = (Get-Content -LiteralPath (Join-Path $reportDir 'preflight-contract/source-processor-preflight.json') -Raw | ConvertFrom-Json).simulator_fill_case_count
            capability = $contract.capability_key
            projectionErrorType = $contract.projection_error_type
            fieldFailures = $contract.field_failures
            externalModelCalls = $contract.external_model_calls
            sourceOriginVerified = $contract.source_production_origin_verified
        } | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $reportDir 'aggregate-summary.json')
    }
    $reportFolder = if ($SmokeOnly) { 'model-smoke' } elseif ($FullHybrid) { 'model-hybrid-full' } elseif ($CaseIds) { 'model-selected' } elseif ($FocusedOnly) { 'model-focused' } else { 'model-16' }
    $reportFile = Join-Path (Join-Path $reportDir $reportFolder) 'report.json'
    if (!$PreflightOnly -and (Test-Path -LiteralPath $reportFile -PathType Leaf)) {
        $run = Get-Content -LiteralPath $reportFile -Raw | ConvertFrom-Json
        [ordered]@{
            status = if ($resultCode -eq 0) { 'passed' } else { 'incomplete_or_failed' }
            cases = $run.cases
            passed = $run.passed
            failed = $run.cases - $run.passed
            unexecuted = $run.unexecuted_cases
            requestAttempts = $run.deepseek_request_attempts
            groups = $run.by_group
            dataMode = $run.data_mode
            sourceOriginCandidates = $run.available_source_cases
            simulatorFillCandidates = $run.available_simulator_fill_cases
            hybridCandidateCount = $run.available_hybrid_cases
            selectedCandidatesByOrigin = $run.selected_candidates_by_origin
            resultsByOrigin = $run.selected_results_by_origin
            sourceOriginVerified = $run.source_production_origin_verified
            bankAcceptance = $run.bank_acceptance
            stoppedOnFailure = $run.stopped_on_first_failed_case
            stoppedOnModelTransport = $run.stopped_for_model_budget_or_transport
        } | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $reportDir 'aggregate-summary.json')
    }
} catch {
    $_.Exception.Message | Set-Content (Join-Path $reportDir 'runner-error.txt')
    $resultCode = 1
} finally {
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
    foreach ($name in @($databaseContainer,$runnerContainer)) {
        $left = & docker --config $dockerConfig ps -a --filter "name=^/$name`$" --format '{{.Names}}' 2>$null
        if ($left) { $resultCode = 1; "Residual INT-035 container: $name" | Add-Content (Join-Path $reportDir 'cleanup-residuals.log') }
    }
    $networkLeft = & docker --config $dockerConfig network ls --filter "name=^$network`$" --format '{{.Name}}' 2>$null
    if ($networkLeft) { $resultCode = 1; "Residual INT-035 network: $network" | Add-Content (Join-Path $reportDir 'cleanup-residuals.log') }
    $mainAfter = Get-MainContainerSnapshot
    $mainAfter | Set-Content (Join-Path $reportDir 'shared-services-after.txt')
    $mainBefore | Set-Content (Join-Path $reportDir 'shared-services-before.txt')
    if ($mainBefore -ne $mainAfter) {
        $resultCode = 1
        'Shared local service container state changed during INT-035 evaluation' | Set-Content (Join-Path $reportDir 'shared-service-check.txt')
    } else { 'Shared local service container IDs and states unchanged' | Set-Content (Join-Path $reportDir 'shared-service-check.txt') }
    foreach ($path in @('restore.log','roles.log','ownership.log','model-run.log','database.log')) {
        $file = Join-Path $reportDir $path
        if (Test-Path -LiteralPath $file) {
            $content = if ((Get-Item -LiteralPath $file).Length -gt 0) { Get-Content -LiteralPath $file -Raw } else { '' }
            if ($null -ne $content) {
                $content = [regex]::Replace($content, 'sk-[A-Za-z0-9]{20,}', '[redacted-key]')
                $content = [regex]::Replace($content, '(?i)(Bearer\s+)[A-Za-z0-9._~-]{24,}', '$1[redacted-token]')
                Set-Content -LiteralPath $file -Value $content
            }
        }
    }
    "exit=$resultCode" | Set-Content (Join-Path $reportDir 'exit.txt')
}

Write-Output "INT-035 source-data replay exit: $resultCode; report: $reportDir"
if (Test-Path (Join-Path $reportDir 'aggregate-summary.json')) { Get-Content (Join-Path $reportDir 'aggregate-summary.json') }
if (Test-Path (Join-Path $reportDir 'model-run.log')) { Get-Content (Join-Path $reportDir 'model-run.log') -Tail 8 }
exit $resultCode
