param([Parameter(Mandatory=$true)][string]$BackupPath)
# Native Docker diagnostics use stderr even on success (notably PostgreSQL
# logs). Check each exit code rather than treating stderr as a PS5 exception.
$ErrorActionPreference='Continue'
$projectRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$archive=(Resolve-Path -LiteralPath $BackupPath).Path
if (!(Test-Path -LiteralPath $archive -PathType Leaf) -or [IO.Path]::GetExtension($archive) -ne '.dump') { throw 'Use an existing local PostgreSQL backup' }
$dockerArgs=@('--config',(Join-Path $projectRoot '.local/docker'))
$run='intent-int021-upgrade-'+(Get-Date -Format 'yyyyMMdd-HHmmss')
$network="$run-net";$report=Join-Path $projectRoot "output/INT-021/$run";$dbName='intent_identity_upgrade_test'
New-Item -ItemType Directory -Path $report | Out-Null
$created=$false;$netCreated=$false;$code=1
function Check-Step([string]$name){if($LASTEXITCODE -ne 0){throw "$name failed"}}
try {
 & docker @dockerArgs network create --internal --label intent.test.owner=INT-021-upgrade $network | Out-Null;Check-Step 'Network';$netCreated=$true
 & docker @dockerArgs run -d --name $run --network $network --label intent.test.owner=INT-021-upgrade -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_USER=intent_test_admin -e "POSTGRES_DB=$dbName" postgres:17.6-alpine | Out-Null;Check-Step 'Database';$created=$true
 for($i=0;$i -lt 30;$i++){ & docker @dockerArgs exec $run pg_isready -U intent_test_admin -d $dbName *> $null;if($LASTEXITCODE -eq 0){break};Start-Sleep -Milliseconds 500 }
 & docker @dockerArgs exec $run psql -U intent_test_admin -d $dbName -v ON_ERROR_STOP=1 -c 'CREATE ROLE intent_admin; CREATE ROLE intent_owner LOGIN NOSUPERUSER NOBYPASSRLS; CREATE ROLE intent_app LOGIN NOSUPERUSER NOBYPASSRLS; ALTER DATABASE intent_identity_upgrade_test OWNER TO intent_owner; ALTER SCHEMA public OWNER TO intent_owner; GRANT USAGE ON SCHEMA public TO intent_app; ALTER DEFAULT PRIVILEGES FOR ROLE intent_owner IN SCHEMA public GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO intent_app; ALTER DEFAULT PRIVILEGES FOR ROLE intent_owner IN SCHEMA public GRANT USAGE,SELECT ON SEQUENCES TO intent_app;' *> (Join-Path $report 'roles.log');Check-Step 'Roles'
 & docker @dockerArgs cp $archive "${run}:/tmp/restoration.dump";Check-Step 'Backup copy'
 & docker @dockerArgs exec $run pg_restore -U intent_test_admin -d $dbName --exit-on-error /tmp/restoration.dump *> (Join-Path $report 'restore.log');Check-Step 'Restoration'
 $backend=Join-Path $projectRoot 'backend'
 & docker @dockerArgs run --rm --network $network --name "$run-check" --label intent.test.owner=INT-021-upgrade -v "${backend}:/app" -w /app -e INTENT_ENV=test -e INTENT_IDENTITY_MODE=local -e INTENT_ALLOW_DEV_AUTH=false -e "INTENT_DATABASE_URL=postgresql+psycopg://intent_app@${run}:5432/$dbName" -e "INTENT_MIGRATION_DATABASE_URL=postgresql+psycopg://intent_owner@${run}:5432/$dbName" -e "INTENT_LOCAL_ADMIN_DATABASE_URL=postgresql+psycopg://intent_test_admin@${run}:5432/$dbName" intent-backend:int021-test python scripts/verify_identity_upgrade.py *> (Join-Path $report 'verification.log');$code=$LASTEXITCODE
} catch { $_.Exception.Message | Set-Content (Join-Path $report 'error.txt');$code=1 }
finally {
 if($created){ & docker @dockerArgs logs $run *> (Join-Path $report 'database.log');$label=& docker @dockerArgs inspect --format '{{ index .Config.Labels "intent.test.owner" }}' $run
  if($label -eq 'INT-021-upgrade'){ & docker @dockerArgs rm -f -v $run *> (Join-Path $report 'cleanup.log');if($LASTEXITCODE -ne 0){$code=1} }else{$code=1} }
 if($netCreated){ & docker @dockerArgs network rm $network *> (Join-Path $report 'network-cleanup.log');if($LASTEXITCODE -ne 0){$code=1} }
}
Write-Output "Upgrade verification exit: $code; report: $report"
if(Test-Path (Join-Path $report 'verification.log')){Get-Content (Join-Path $report 'verification.log') -Tail 4}
exit $code
