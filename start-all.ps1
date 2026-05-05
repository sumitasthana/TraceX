# TraceX — one-shot bootstrap
#
# Generates Layer 0 CSVs (if missing), builds the DuckDB, runs the staging+facts
# pipeline, ingests the resulting JSONL log into the Kuzu lineage graph, and
# starts the UI in this terminal.
#
# Usage:
#   .\start-all.ps1            # full sequence
#   .\start-all.ps1 -SkipGen   # assume CSVs + DuckDB already exist
#   .\start-all.ps1 -UiOnly    # just start the UI

param(
    [switch]$SkipGen,
    [switch]$UiOnly
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

function Step($label, [scriptblock]$body) {
    Write-Host ""
    Write-Host "== $label ==" -ForegroundColor Cyan
    & $body
}

if (-not $UiOnly) {
    if (-not $SkipGen) {
        Step "Layer 0 — generate synthetic CSVs" { python layer0\generate.py }
        Step "Layer 0 — load DuckDB"            { python layer0\load_duckdb.py }
    }
    Step "Pipeline — run all stages"            { python pipeline\run_pipeline.py }
    Step "Lineage — ingest latest run"          { python lineage\ingest.py --latest }
}

Step "UI — uvicorn on http://127.0.0.1:8765" { python ui\serve.py }
