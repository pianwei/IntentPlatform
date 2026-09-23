param([ValidateSet('backend','frontend','all')][string]$Scope = 'all')
$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$dockerArgs = @('--config', (Join-Path $projectRoot '.local\docker'))
function Check-Exit([string]$Step) { if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE" } }
Push-Location -LiteralPath $projectRoot
try {
    if ($Scope -in @('backend','all')) {
        & docker @dockerArgs compose up -d --wait postgres
        Check-Exit 'PostgreSQL startup'
        $testDatabase = & docker @dockerArgs compose exec -T postgres psql -U intent_admin -d postgres -tAc "SELECT datname FROM pg_database WHERE datname='intent_test'"
        Check-Exit 'Test database lookup'
        if (!$testDatabase) {
            & docker @dockerArgs compose exec -T postgres psql -U intent_admin -d postgres -v ON_ERROR_STOP=1 -c 'CREATE DATABASE intent_test OWNER intent_owner;'
            Check-Exit 'Test database creation'
        }
        & docker @dockerArgs compose exec -T postgres psql -U intent_admin -d intent_test -v ON_ERROR_STOP=1 -c 'ALTER SCHEMA public OWNER TO intent_owner; GRANT CONNECT ON DATABASE intent_test TO intent_app; GRANT USAGE ON SCHEMA public TO intent_app; ALTER DEFAULT PRIVILEGES FOR ROLE intent_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO intent_app; ALTER DEFAULT PRIVILEGES FOR ROLE intent_owner IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO intent_app;'
        Check-Exit 'Test database roles'
        $backendRoot = Join-Path $projectRoot 'backend'
        & docker @dockerArgs compose --profile app run --rm --no-deps -v "${backendRoot}:/app" -e INTENT_ENV=test -e INTENT_DATABASE_URL=postgresql+psycopg://intent_app:local-app-only@postgres:5432/intent_test -e INTENT_MIGRATION_DATABASE_URL=postgresql+psycopg://intent_owner:local-owner-only@postgres:5432/intent_test migrate python -m pytest -q
        Check-Exit 'Backend tests'
    }
    if ($Scope -in @('frontend','all')) {
        $frontendRoot = Join-Path $projectRoot 'frontend'
        & docker @dockerArgs run --rm -v "${frontendRoot}:/work" -w /work node:22.16.0-alpine npm ci --ignore-scripts --no-audit --no-fund
        Check-Exit 'Frontend dependency installation'
        & docker @dockerArgs run --rm -v "${frontendRoot}:/work" -w /work node:22.16.0-alpine npm test
        Check-Exit 'Frontend tests'
        & docker @dockerArgs run --rm -v "${frontendRoot}:/work" -w /work node:22.16.0-alpine npm run build
        Check-Exit 'Production frontend build'
    }
} finally { Pop-Location }
