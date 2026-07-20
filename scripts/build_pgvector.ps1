# Build and install pgvector for PG17.
# Run in a PowerShell terminal that has access to the VS install.
$ErrorActionPreference = 'Stop'

$vsPs1 = 'C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\Tools\Launch-VsDevShell.ps1'
if (-not (Test-Path $vsPs1)) { throw "VS dev shell script not found: $vsPs1" }

Write-Host "=== Initializing VS Dev Shell (amd64) ===" -ForegroundColor Cyan
& $vsPs1 -Arch amd64 -SkipAutomaticLocation

Write-Host "=== Setting PGROOT ===" -ForegroundColor Cyan
$env:PGROOT = 'C:\pgsql17\pgsql'
# pgvector Makefile.win expects PGROOT to point to the PG install root
# (the dir containing lib/, share/, include/). Verify.
if (-not (Test-Path "$env:PGROOT\lib")) { throw "PGROOT\lib not found: $env:PGROOT\lib" }
if (-not (Test-Path "$env:PGROOT\share\extension")) { throw "PGROOT\share\extension not found" }
"PGROOT OK: $env:PGROOT"

Write-Host "=== nmake build ===" -ForegroundColor Cyan
Set-Location C:\dev\pgvector
& nmake /F Makefile.win 2>&1
if ($LASTEXITCODE -ne 0) { throw "nmake build failed (exit $LASTEXITCODE)" }

Write-Host "=== nmake install ===" -ForegroundColor Cyan
& nmake /F Makefile.win install 2>&1
if ($LASTEXITCODE -ne 0) { throw "nmake install failed (exit $LASTEXITCODE)" }

Write-Host "=== Verifying install ===" -ForegroundColor Cyan
$vectorDll = "$env:PGROOT\lib\vector.dll"
$ctl = "$env:PGROOT\share\extension\vector.control"
$sql = "$env:PGROOT\share\extension\vector--0.8.1.sql"
"dll: $(if (Test-Path $vectorDll) { (Get-Item $vectorDll).Length + ' bytes' } else { 'MISSING' })"
"ctl: $(if (Test-Path $ctl) { 'OK' } else { 'MISSING' })"
"sql: $(if (Test-Path $sql) { 'OK' } else { 'MISSING' })"

Write-Host "=== DONE ===" -ForegroundColor Green
