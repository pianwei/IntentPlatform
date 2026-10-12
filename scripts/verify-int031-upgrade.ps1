param([Parameter(Mandatory=$true)][string]$BackupPath, [switch]$ModelSuite)
$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$backendRoot = Join-Path $projectRoot 'backend'
$dockerConfig = Join-Path $projectRoot '.local/docker'
$archive = (Resolve-Path -LiteralPath $BackupPath).Path
$backupRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot '.local/backups'))
if (!(Test-Path -LiteralPath $archive -PathType Leaf) -or [IO.Path]::GetExtension($archive) -ne '.dump') { throw 'Use an existing local PostgreSQL .dump backup' }
if (!$archive.StartsWith($backupRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Backup must be inside .local/backups' }

$runId = Get-Date -Format 'yyyyMMdd-HHmmss'
$resource = "intent-int031-upgrade-$runId"
$databaseContainer = "$resource-pg"
$runnerContainer = "$resource-runner"
$modelRunnerContainer = "$resource-model-runner"
$network = "$resource-net"
$database = 'intent_int031_upgrade_test'
$ownerLabel = 'INT-031-isolated-upgrade'
$reportDir = Join-Path $projectRoot "output/INT-031/upgrade-$runId"
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
    $existing = & docker --config $dockerConfig ps -a --filter "name=^/$databaseContainer`$" --format '{{.Names}}'
    if ($LASTEXITCODE -ne 0 -or $existing) { throw 'A conflicting test database container exists' }
    $existing = & docker --config $dockerConfig network ls --filter "name=^$network`$" --format '{{.Name}}'
    if ($LASTEXITCODE -ne 0 -or $existing) { throw 'A conflicting test network exists' }

    $networkArgs = @('network','create')
    if (!$ModelSuite) { $networkArgs += '--internal' }
    $networkArgs += @('--label',"intent.test.owner=$ownerLabel",$network)
    Invoke-Docker $networkArgs 'Test network creation' | Out-Null
    $networkCreated = $true
    Invoke-Docker @('run','-d','--network',$network,'--name',$databaseContainer,'--label',"intent.test.owner=$ownerLabel",
        '--network-alias','postgres',
        '--tmpfs','/var/lib/postgresql/data:rw,nosuid,nodev,size=1g','-e','POSTGRES_HOST_AUTH_METHOD=trust',
        '-e','POSTGRES_USER=intent_test_admin','-e',"POSTGRES_DB=$database",'postgres:17.6-alpine') 'Disposable database creation' | Out-Null
    $databaseCreated = $true

    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        & docker --config $dockerConfig exec $databaseContainer psql -h 127.0.0.1 -U intent_test_admin -d $database -tAc 'SELECT 1' *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (!$ready) { throw 'Disposable restore database did not become ready' }

    $roles = @'
CREATE ROLE intent_owner LOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE intent_app LOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE intent_admin NOLOGIN;
ALTER DATABASE intent_int031_upgrade_test OWNER TO intent_owner;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
'@
    Invoke-Docker @('exec',$databaseContainer,'psql','-U','intent_test_admin','-d',$database,'-v','ON_ERROR_STOP=1','-c',$roles) 'Test roles and extension setup' *> (Join-Path $reportDir 'roles.log')

    Invoke-Docker @('cp',$archive,"${databaseContainer}:/tmp/int031-backup.dump") 'Backup copy to isolated database'
    Invoke-Docker @('exec',$databaseContainer,'pg_restore','-U','intent_test_admin','-d',$database,'--exit-on-error','--no-owner','/tmp/int031-backup.dump') 'Database restore' *> (Join-Path $reportDir 'restore.log')

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
    Invoke-Docker @('exec',$databaseContainer,'psql','-U','intent_test_admin','-d',$database,'-v','ON_ERROR_STOP=1','-c',$ownership) 'Restored schema ownership'

    $image = 'intent-backend:int029'
    Invoke-Docker @('image','inspect',$image) 'Cached backend runner image check' *> (Join-Path $reportDir 'runner-image.log')
    $envArgs = @('-e','INTENT_ENV=test','-e','INTENT_IDENTITY_MODE=local','-e','INTENT_ALLOW_DEV_AUTH=false',
        '-e','INTENT_MODEL_MODE=fixture','-e','INTENT_ALLOW_PUBLIC_DEEPSEEK_MODEL_TEST=false','-e','INTENT_PLATFORM_ADMINS_JSON=',
        '-e','INTENT_IDENTITY_DIRECTORY_JSON=',
        '-e',"INTENT_DATABASE_URL=postgresql+psycopg://intent_app@$databaseContainer`:5432/$database",
        '-e',"INTENT_MIGRATION_DATABASE_URL=postgresql+psycopg://intent_owner@$databaseContainer`:5432/$database",
        '-e',"INTENT_LOCAL_ADMIN_DATABASE_URL=postgresql+psycopg://intent_test_admin@$databaseContainer`:5432/$database",
        '-e','PYTHONDONTWRITEBYTECODE=1')
    & docker --config $dockerConfig run --rm --network $network --name $runnerContainer --label "intent.test.owner=$ownerLabel" `
        -v "${backendRoot}:/app" -v "${reportDir}:/reports" -w /app @envArgs $image python scripts/verify_int031_upgrade.py *> (Join-Path $reportDir 'verification.log')
    $resultCode = $LASTEXITCODE

    if ($ModelSuite -and $resultCode -eq 0) {
        $modelEnvPath = Join-Path $projectRoot '.env'
        $adminFile = Join-Path $projectRoot '.local/INT021-admin.json'
        if (!(Test-Path -LiteralPath $modelEnvPath -PathType Leaf) -or !(Test-Path -LiteralPath $adminFile -PathType Leaf)) {
            throw 'The approved local model configuration or test administrator credential file is unavailable'
        }
        Invoke-Docker @('exec',$databaseContainer,'psql','-U','intent_test_admin','-d','postgres','-v','ON_ERROR_STOP=1',
            '-c',"CREATE DATABASE intent_int029_int031_candidate TEMPLATE $database") 'Isolated model-evaluation database clone'
        $modelArgs = @('--code-root','/app','--database','intent_int029_int031_candidate','--database-host','postgres',
            '--output','/reports/model-106')
        $modelExit = 1
        & docker --config $dockerConfig run --rm --network $network --name $modelRunnerContainer --label "intent.test.owner=$ownerLabel" `
            -v "${projectRoot}:/work:ro" -v "${backendRoot}:/app:ro" -v "${reportDir}:/reports" -w /work/backend `
            -e 'PYTHONDONTWRITEBYTECODE=1' $image python scripts/verify_zhiyu_model.py @modelArgs *> (Join-Path $reportDir 'model-106.log')
        $modelExit = $LASTEXITCODE
        "exit=$modelExit" | Set-Content (Join-Path $reportDir 'model-106.exit')
        if ($modelExit -ne 0) { $resultCode = 1 }
    }
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
    foreach ($name in @($databaseContainer,$runnerContainer,$modelRunnerContainer)) {
        $left = & docker --config $dockerConfig ps -a --filter "name=^/$name`$" --format '{{.Names}}' 2>$null
        if ($left) { $resultCode = 1; "Residual owned test container: $name" | Add-Content (Join-Path $reportDir 'cleanup-residuals.log') }
    }
    $networkLeft = & docker --config $dockerConfig network ls --filter "name=^$network`$" --format '{{.Name}}' 2>$null
    if ($networkLeft) { $resultCode = 1; "Residual test network: $network" | Add-Content (Join-Path $reportDir 'cleanup-residuals.log') }
    foreach ($path in @('restore.log','verification.log','runner-container.log','database.log','model-106.log')) {
        $file = Join-Path $reportDir $path
        if (Test-Path $file) {
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

Write-Output "INT-031 upgrade verification exit: $resultCode; model-suite=$ModelSuite; report: $reportDir"
if (Test-Path (Join-Path $reportDir 'verification.log')) { Get-Content (Join-Path $reportDir 'verification.log') -Tail 5 }
if (Test-Path (Join-Path $reportDir 'model-106.log')) { Get-Content (Join-Path $reportDir 'model-106.log') -Tail 12 }
exit $resultCode
