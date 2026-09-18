param([switch]$Stop, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $projectRoot '.venv'
$pidFile = Join-Path $runtimeRoot 'local-processes.json'
$env:SECRET_KEY = 'local-development-only-not-for-deployment'
$env:DATABASE_URL = 'postgresql+asyncpg://media_tracker:local-development-only@127.0.0.1:55438/media_tracker'
$env:SERVER_URL = 'http://localhost:7340'
$env:BACKEND_PORT = '7341'
Set-Location -LiteralPath $projectRoot

if ($Stop) {
    if (Test-Path -LiteralPath $pidFile) {
        foreach ($record in (Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json)) {
            $process = Get-Process -Id $record.id -ErrorAction SilentlyContinue
            if ($process -and $process.StartTime.ToUniversalTime().ToString('o') -eq $record.started) {
                Stop-Process -Id $process.Id
            }
        }
        Remove-Item -LiteralPath $pidFile
    }
    Write-Host 'Local web servers stopped. The database and saved data are retained.'
    exit
}
foreach ($required in @('.venv\Scripts\python.exe','frontend\node_modules\astro\bin\astro.mjs')) {
    if (!(Test-Path -LiteralPath (Join-Path $projectRoot $required))) { throw "Missing $required. Install project dependencies first." }
}
& docker compose -p media-tracker-local -f compose.local.yaml up -d --wait
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop, wait until it is ready, then run this launcher again.' }
Push-Location backend
try {
    & ..\.venv\Scripts\python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
} finally { Pop-Location }
& .\.venv\Scripts\python scripts\prepare_local_login.py
if ($LASTEXITCODE -ne 0) { throw 'Local login setup failed.' }
$records = @()
if (Test-Path -LiteralPath $pidFile) { $records = (Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json) }
foreach ($service in @(
    @{ port=7341; name='backend'; exe=(Join-Path $runtimeRoot 'Scripts\python.exe'); args='-m uvicorn main:app --host 127.0.0.1 --port 7341'; cwd=(Join-Path $projectRoot 'backend') },
    @{ port=7340; name='frontend'; exe=(Get-Command node).Source; args='node_modules/astro/bin/astro.mjs dev --host 127.0.0.1 --port 7340'; cwd=(Join-Path $projectRoot 'frontend') }
)) {
    $listener = Get-NetTCPConnection -LocalPort $service.port -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        $owned = @($records | Where-Object { $_.id -in $listener.OwningProcess })
        if (!$owned) {
            foreach ($listenerId in $listener.OwningProcess) {
                $child = Get-CimInstance Win32_Process -Filter "ProcessId=$listenerId"
                $parentRecord = $records | Where-Object { $_.id -eq $child.ParentProcessId } | Select-Object -First 1
                $parent = Get-Process -Id $child.ParentProcessId -ErrorAction SilentlyContinue
                if ($parentRecord -and $parent -and $parent.StartTime.ToUniversalTime().ToString('o') -eq $parentRecord.started) { $owned=@($parentRecord) }
            }
        }
        if (!$owned) { throw "Port $($service.port) is occupied by another process. Stop it before starting Media Tracker." }
        continue
    }
    $process = Start-Process -FilePath $service.exe -ArgumentList $service.args -WorkingDirectory $service.cwd -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtimeRoot "$($service.name).log") -RedirectStandardError (Join-Path $runtimeRoot "$($service.name).error.log")
    $records += @{ id=$process.Id; started=$process.StartTime.ToUniversalTime().ToString('o') }
    $records | ConvertTo-Json | Set-Content -LiteralPath $pidFile
}
$ready = $false
for ($attempt=0; $attempt -lt 45; $attempt++) {
    try {
        $backend = Invoke-WebRequest -Uri 'http://127.0.0.1:7341/health' -UseBasicParsing -TimeoutSec 5
        $frontend = Invoke-WebRequest -Uri 'http://127.0.0.1:7340/login' -UseBasicParsing -TimeoutSec 5
        if ($backend.StatusCode -eq 200 -and $frontend.StatusCode -eq 200) { $ready=$true;break }
    } catch { Start-Sleep -Seconds 1 }
}
if (!$ready) { throw 'Servers did not become ready. Check .venv/backend.error.log and .venv/frontend.error.log.' }
# Windows virtualenv Python may launch a child interpreter. Record the actual
# listeners as well so Stop does not leave the child server behind.
foreach ($listener in @(Get-NetTCPConnection -LocalPort 7340,7341 -State Listen)) {
    if ($listener.OwningProcess -notin $records.id) {
        $process=Get-Process -Id $listener.OwningProcess
        $records=@(@{id=$process.Id;started=$process.StartTime.ToUniversalTime().ToString('o')})+$records
    }
}
$records | ConvertTo-Json | Set-Content -LiteralPath $pidFile
Write-Host 'Ready: http://localhost:7340'
Write-Host 'Login details: .venv\LOCAL-LOGIN.txt'
if (!$NoBrowser) { Start-Process 'http://localhost:7340/login' }




