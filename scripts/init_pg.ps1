$ErrorActionPreference = 'Stop'
$pgBin = "C:\pgsql17\pgsql\bin"
$pgData = "C:\pgsql17\data"
$pgLog = "C:\pgsql17\pg.log"

# Add PG bin to PATH for this session
$env:Path = "$pgBin;" + $env:Path

# 1. Initialize data directory if not already done
if (-not (Test-Path "$pgData\PG_VERSION")) {
    Write-Host "[1/4] Initializing PostgreSQL data directory at $pgData ..."
    # Create temp password file
    $passFile = "$env:TEMP\pg_init_pass.txt"
    "postgres" | Out-File -FilePath $passFile -Encoding ascii -NoNewline
    & "$pgBin\initdb.exe" -D $pgData -U postgres --pwfile=$passFile --auth-local=scram-sha-256 --auth-host=scram-sha-256 --encoding=UTF8 --locale=C 2>&1 | ForEach-Object { Write-Host $_ }
    Remove-Item $passFile -Force
    if (-not (Test-Path "$pgData\PG_VERSION")) {
        Write-Host "[FAIL] initdb did not create PG_VERSION"
        exit 1
    }
    Write-Host "[OK] initdb complete"
} else {
    Write-Host "[1/4] Data directory already initialized"
}

# 2. Start PostgreSQL (background, no service registration)
Write-Host "[2/4] Starting PostgreSQL server..."
$pgCtlStatus = & "$pgBin\pg_ctl.exe" -D $pgData status 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "[OK] PostgreSQL already running"
} else {
    & "$pgBin\pg_ctl.exe" -D $pgData -l $pgLog start 2>&1 | ForEach-Object { Write-Host $_ }
    Start-Sleep -Seconds 3
    $pgCtlStatus = & "$pgBin\pg_ctl.exe" -D $pgData status 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] PostgreSQL did not start. Log:"
        if (Test-Path $pgLog) { Get-Content $pgLog -Tail 30 }
        exit 1
    }
    Write-Host "[OK] PostgreSQL started"
}

# 3. Verify connection and create ecommerce database
Write-Host "[3/4] Verifying connection and creating ecommerce database..."
$env:PGPASSWORD = "postgres"
& "$pgBin\psql.exe" -U postgres -h 127.0.0.1 -p 5432 -c "SELECT version();" 2>&1 | ForEach-Object { Write-Host $_ }

# Create database if not exists (psql doesn't have IF NOT EXISTS for CREATE DATABASE, use shell check)
$dbExists = & "$pgBin\psql.exe" -U postgres -h 127.0.0.1 -p 5432 -tAc "SELECT 1 FROM pg_database WHERE datname='ecommerce';" 2>&1
if ($dbExists.Trim() -eq "1") {
    Write-Host "[OK] Database 'ecommerce' already exists"
} else {
    & "$pgBin\psql.exe" -U postgres -h 127.0.0.1 -p 5432 -c "CREATE DATABASE ecommerce OWNER postgres ENCODING 'UTF8';" 2>&1 | ForEach-Object { Write-Host $_ }
    Write-Host "[OK] Database 'ecommerce' created"
}

# 4. Final verification
Write-Host "[4/4] Final verification..."
& "$pgBin\psql.exe" -U postgres -h 127.0.0.1 -p 5432 -d ecommerce -c "SELECT current_database(), current_user, version();" 2>&1 | ForEach-Object { Write-Host $_ }
Write-Host ""
Write-Host "========================================"
Write-Host "PostgreSQL 17 is ready:"
Write-Host "  Host:     127.0.0.1"
Write-Host "  Port:     5432"
Write-Host "  User:     postgres"
Write-Host "  Password: postgres"
Write-Host "  Database: ecommerce"
Write-Host "  Bin:      $pgBin"
Write-Host "  Data:     $pgData"
Write-Host "  Log:      $pgLog"
Write-Host "========================================"
