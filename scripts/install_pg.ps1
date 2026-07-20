$ErrorActionPreference = 'Stop'
$installer = "$env:TEMP\pg17-installer.exe"
if (-not (Test-Path $installer)) {
    Write-Host "Installer not found at $installer, please re-download."
    exit 1
}

$installArgs = @(
    "--mode", "unattended",
    "--unattendedmodeui", "none",
    "--superpassword", "postgres",
    "--servicename", "postgresql-17",
    "--serverport", "5432",
    "--enable-components", "server,commandlinetools",
    "--disable-components", "stackbuilder",
    "--prefix", "C:\Program Files\PostgreSQL\17",
    "--datadir", "C:\Program Files\PostgreSQL\17\data"
)

Write-Host "Launching PostgreSQL 17 installer with admin privileges..."
Write-Host "UAC prompt will appear - please click YES to authorize."
Write-Host "Args: $($installArgs -join ' ')"

$proc = Start-Process -FilePath $installer -ArgumentList $installArgs -Verb RunAs -Wait -PassThru
Write-Host "Installer exit code: $($proc.ExitCode)"
Write-Host "----------------------------------------"
Write-Host "Verifying installation..."
$psqlPath = "C:\Program Files\PostgreSQL\17\bin\psql.exe"
if (Test-Path $psqlPath) {
    Write-Host "[OK] psql.exe found at $psqlPath"
} else {
    Write-Host "[FAIL] psql.exe NOT found at $psqlPath"
}
$svc = Get-Service -Name "postgresql*" -ErrorAction SilentlyContinue
if ($svc) {
    $svc | Select-Object Name, Status, StartType | Format-Table
} else {
    Write-Host "[FAIL] No postgresql service registered"
}
