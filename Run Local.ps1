param([switch]$Stop, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'

Push-Location
try {
    & (Join-Path $PSScriptRoot 'scripts\local.ps1') @PSBoundParameters
} finally {
    Pop-Location
}
