$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { $python = (Get-Command python).Source }
$outputDirectory = Join-Path $PSScriptRoot ("run-" + (Get-Date -Format "yyyyMMdd-HHmmss-fff"))
& $python (Join-Path $projectRoot "scripts\demo.py") --output $outputDirectory
if ($LASTEXITCODE -ne 0) { throw "The local proof demo failed; see its output above." }
Write-Host "DEMO_OUTPUT=$outputDirectory"
