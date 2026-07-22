<#
Fail-closed continuation for the canonical June hits baseline.

It is intentionally specific to the two already-started reconstruction workers.
It never promotes a model or authorizes betting.  It continues only when fresh,
atomic frozen/candidate artifacts pass the full-universe validator.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [int[]]$ReconstructionPid,

    [string]$Root = "C:\Projects\baseball_predictor"
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $Root
$startedAt = Get-Date

function Require-FreshFile {
    param([string]$Path, [datetime]$NotBefore)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required artifact is missing: $Path"
    }
    $item = Get-Item -LiteralPath $Path
    if ($item.LastWriteTime -lt $NotBefore) {
        throw "Artifact predates this continuation and may be stale: $Path ($($item.LastWriteTime))"
    }
}

try {
    Write-Host "[wait] waiting for reconstruction workers: $($ReconstructionPid -join ', ')"
    foreach ($workerPid in $ReconstructionPid) {
        if (Get-Process -Id $workerPid -ErrorAction SilentlyContinue) {
            Wait-Process -Id $workerPid
        }
    }

    $required = @(
        "data\analysis\market_hits_june_base\gate_frozen.csv",
        "data\analysis\market_hits_june_base\gate_candidate.csv",
        "data\analysis\market_hits_june_base\outcomes_frozen.csv",
        "data\analysis\market_hits_june_base\outcomes_candidate.csv",
        "data\analysis\market_hits_june_base\gate_frozen.manifest.json",
        "data\analysis\market_hits_june_base\gate_candidate.manifest.json"
    )
    foreach ($artifact in $required) {
        Require-FreshFile -Path $artifact -NotBefore $startedAt
    }

    Write-Host "[validate] full canonical reconstruction universe"
    & python scripts\validate_market_reconstruct_smoke.py `
        --strict-manifest data\market\v3\base_rule_strict_hits_manifest.json `
        --frozen data\analysis\market_hits_june_base\gate_frozen.csv `
        --candidate data\analysis\market_hits_june_base\gate_candidate.csv `
        --frozen-outcomes data\analysis\market_hits_june_base\outcomes_frozen.csv `
        --candidate-outcomes data\analysis\market_hits_june_base\outcomes_candidate.csv `
        --frozen-provenance data\analysis\market_hits_june_base\gate_frozen.manifest.json `
        --candidate-provenance data\analysis\market_hits_june_base\gate_candidate.manifest.json
    if ($LASTEXITCODE -ne 0) {
        throw "Full-universe reconstruction validation failed (exit $LASTEXITCODE)."
    }

    Write-Host "[score] strict MLB-truth market A/B (research-only)"
    & python scripts\run_market_ab.py `
        --frozen data\analysis\market_hits_june_base\gate_frozen.csv `
        --candidate data\analysis\market_hits_june_base\gate_candidate.csv `
        --official data\market\v3\official_hits_actuals.csv `
        --strict-market-manifest data\market\v3\base_rule_strict_hits_manifest.json `
        --policy config\ab_policy.json `
        --months 2026-06 `
        --b 4000 `
        --seed 7 `
        --out data\analysis\market_hits_june_base\market_ab
    if ($LASTEXITCODE -ne 0) {
        throw "Strict market A/B failed (exit $LASTEXITCODE)."
    }

    Write-Host "[complete] validation and research A/B both completed."
}
catch {
    Write-Error $_
    exit 1
}
