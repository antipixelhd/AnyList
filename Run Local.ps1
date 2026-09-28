param([switch]$Stop, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'

$projectRoot = $PSScriptRoot
$runtimeRoot = Join-Path $projectRoot '.venv'
$pidFile = Join-Path $runtimeRoot 'local-web-processes.json'
$python = Join-Path $runtimeRoot 'Scripts\python.exe'
$frontendEntry = Join-Path $projectRoot 'frontend\node_modules\astro\bin\astro.mjs'
$env:SECRET_KEY = 'local-development-only-not-for-deployment'
$env:DATABASE_URL = 'postgresql+asyncpg://media_tracker:local-development-only@127.0.0.1:55438/media_tracker'
$env:SERVER_URL = 'http://localhost:7340'
$env:BACKEND_PORT = '7341'

function Get-LocalWebListener([int]$Port) {
    @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Test-ExpectedProcess($ProcessInfo, [int]$Port) {
    if (!$ProcessInfo) { return $false }
    if ($Port -eq 7341) {
        return $ProcessInfo.CommandLine -like "*$projectRoot*uvicorn main:app*--port 7341*"
    }
    return $ProcessInfo.CommandLine -like "*node_modules/astro/bin/astro.mjs dev*--port 7340*"
}

if ($Stop) {
    $knownProcesses = @{}
    if (Test-Path -LiteralPath $pidFile) {
        foreach ($record in (Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json)) {
            $knownProcesses[[int]$record.id] = [string]$record.started
        }
    }

    foreach ($port in @(7340, 7341)) {
        foreach ($listener in (Get-LocalWebListener $port)) {
            $processId = [int]$listener.OwningProcess
            $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
            if (!$process) { continue }
            try { $started = $process.StartTime.ToUniversalTime().ToString('o') } catch { continue }

            $expected = Test-ExpectedProcess (Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue) $port
            $recorded = $knownProcesses.ContainsKey($processId) -and $knownProcesses[$processId] -eq $started
            if ($expected -and $recorded) { Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue }
        }
    }

    if (Test-Path -LiteralPath $pidFile) { Remove-Item -LiteralPath $pidFile }
    Write-Host 'Local web servers stopped. The database and saved data are retained.'
    exit
}

foreach ($required in @($python, $frontendEntry)) {
    if (!(Test-Path -LiteralPath $required)) { throw "Missing dependency: $required" }
}

# Running the migration also confirms the already-started local database is reachable.
Push-Location (Join-Path $projectRoot 'backend')
try {
    & $python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Database migration failed. Confirm the local database container is running.' }
} finally { Pop-Location }
& $python (Join-Path $projectRoot 'scripts\prepare_local_login.py')
if ($LASTEXITCODE -ne 0) { throw 'Local login setup failed.' }

$node = (Get-Command node -ErrorAction Stop).Source
$services = @(
    @{ port=7341; name='backend'; exe=$python; args='-m uvicorn main:app --host 127.0.0.1 --port 7341'; cwd=(Join-Path $projectRoot 'backend') },
    @{ port=7340; name='frontend'; exe=$node; args='node_modules/astro/bin/astro.mjs dev --host 127.0.0.1 --port 7340'; cwd=(Join-Path $projectRoot 'frontend') }
)

$records = @()
if (Test-Path -LiteralPath $pidFile) {
    foreach ($record in (Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json)) {
        $process = Get-Process -Id $record.id -ErrorAction SilentlyContinue
        if (!$process) { continue }
        try {
            if ($process.StartTime.ToUniversalTime().ToString('o') -eq $record.started) { $records += $record }
        } catch { continue }
    }
}

foreach ($service in $services) {
    $listeners = Get-LocalWebListener $service.port
    if ($listeners.Count -gt 0) {
        foreach ($listener in $listeners) {
            $processId = [int]$listener.OwningProcess
            $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
            $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
            if (!$process -or !(Test-ExpectedProcess $processInfo $service.port)) {
                throw "Port $($service.port) is occupied by another process. Stop it before starting media-tracker."
            }
            try { $started = $process.StartTime.ToUniversalTime().ToString('o') } catch { continue }
            if ($processId -notin @($records | ForEach-Object { [int]$_.id })) {
                $records += @{ id=$processId; started=$started }
            }
        }
        continue
    }

    $process = Start-Process -FilePath $service.exe -ArgumentList $service.args -WorkingDirectory $service.cwd -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtimeRoot "$($service.name).log") -RedirectStandardError (Join-Path $runtimeRoot "$($service.name).error.log")
    $records += @{ id=$process.Id; started=$process.StartTime.ToUniversalTime().ToString('o') }
    $records | ConvertTo-Json | Set-Content -LiteralPath $pidFile
}

$ready = $false
for ($attempt = 0; $attempt -lt 45; $attempt++) {
    try {
        $backend = Invoke-WebRequest -Uri 'http://127.0.0.1:7341/health' -UseBasicParsing -TimeoutSec 5
        $frontend = Invoke-WebRequest -Uri 'http://127.0.0.1:7340/login' -UseBasicParsing -TimeoutSec 5
        if ($backend.StatusCode -eq 200 -and $frontend.StatusCode -eq 200) { $ready = $true; break }
    } catch { Start-Sleep -Seconds 1 }
}
if (!$ready) { throw 'Servers did not become ready. Check .venv/backend.error.log and .venv/frontend.error.log.' }

# Capture the actual listener PIDs, since the Windows Python launcher may exit after spawning the interpreter.
foreach ($service in $services) {
    foreach ($listener in (Get-LocalWebListener $service.port)) {
        $processId = [int]$listener.OwningProcess
        if ($processId -notin @($records | ForEach-Object { [int]$_.id })) {
            $process = Get-Process -Id $processId
            $records += @{ id=$processId; started=$process.StartTime.ToUniversalTime().ToString('o') }
        }
    }
}
$records | ConvertTo-Json | Set-Content -LiteralPath $pidFile
Write-Host 'Ready: http://localhost:7340'
Write-Host 'Login details: .venv\LOCAL-LOGIN.txt'
if (!$NoBrowser) { Start-Process 'http://localhost:7340/login' }
