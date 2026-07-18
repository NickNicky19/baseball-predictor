[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$ServiceRoot = "",
    [string]$PythonExe = "python",
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
}
if (-not $ServiceRoot) {
    $ServiceRoot = Join-Path $RepoRoot "data\learning\shadow\operational_smoke_v1"
}
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$ServiceRoot = (Resolve-Path -LiteralPath $ServiceRoot).Path
$scopePath = Join-Path $ServiceRoot "evidence_scope.json"

if (-not (Test-Path -LiteralPath $scopePath -PathType Leaf)) {
    throw "Operational-smoke evidence scope is missing: $scopePath"
}
$scope = Get-Content -Raw -LiteralPath $scopePath | ConvertFrom-Json
if ($scope.schema_version -ne "forward-shadow-evidence-scope-v2") {
    throw "Evidence scope schema is not the locked forward-shadow schema"
}
if ($scope.mode -ne "operational_smoke" -or $scope.operational_smoke -ne $true) {
    throw "This runner accepts only the permanently excluded operational-smoke scope"
}
if ($scope.economic_evidence_eligible -ne $false -or $scope.betting_authorized -ne $false) {
    throw "Operational smoke cannot be economic evidence or authorize betting"
}

$python = Get-Command $PythonExe -ErrorAction Stop
Push-Location $RepoRoot
try {
    & $python.Source "scripts\prepare_forward_evidence_scope.py" `
        --mode operational_smoke `
        --era-id $scope.era_id `
        --service-root $ServiceRoot `
        --readiness $scope.readiness_report.path `
        --boundary $scope.forward_evidence_boundary.path `
        --protocol $scope.deployment_protocol.path `
        --products $scope.execution_product_contracts.path
    $scopeValidationExit = $LASTEXITCODE
}
finally {
    Pop-Location
}
if ($scopeValidationExit -ne 0) {
    throw "Operational-smoke evidence scope or runtime fingerprint validation failed"
}

if ($ValidateOnly) {
    Write-Host "LOCAL OPERATIONAL-SMOKE RUNNER VALID"
    Write-Host "  scope: $($scope.scope_sha256)"
    Write-Host "  economic evidence eligible: FALSE"
    exit 0
}

$secureKey = Read-Host "Enter the ROTATED odds-provider key (input is hidden; never paste it into source or chat)" -AsSecureString
$secretPtr = [IntPtr]::Zero

try {
    $secretPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    $env:ODDS_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPtr)
    if ([string]::IsNullOrWhiteSpace($env:ODDS_API_KEY)) {
        throw "The rotated odds-provider key cannot be blank"
    }

    Write-Host "Starting the permanently excluded local operational smoke."
    Write-Host "Keep this PowerShell window open and the computer awake until the lifecycle is settled."
    Write-Host "This runner never places a wager and is not the durable forward-evidence primary."

    while ($true) {
        $tickStarted = Get-Date
        Push-Location $RepoRoot
        try {
            & $python.Source "run_shadow_collector_tick.py" --service-root $ServiceRoot
            $tickExit = $LASTEXITCODE
        }
        finally {
            Pop-Location
        }
        if ($tickExit -ne 0) {
            Write-Warning "Collector tick failed with exit $tickExit. Evidence remains fail-closed; no backfill will be attempted."
        }
        $elapsed = ((Get-Date) - $tickStarted).TotalSeconds
        $sleepSeconds = [Math]::Max(1, [Math]::Ceiling(60 - $elapsed))
        Start-Sleep -Seconds $sleepSeconds
    }
}
finally {
    Remove-Item Env:ODDS_API_KEY -ErrorAction SilentlyContinue
    if ($secretPtr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPtr)
    }
}
