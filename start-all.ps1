# TraceX bootstrap — thin wrapper around `python cli.py up`.
# Forwards every argument so you can do:
#   .\start-all.ps1
#   .\start-all.ps1 -- --force
#   .\start-all.ps1 -- --port 9000
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)
python cli.py up @args
