param([switch]$Install, [int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$nodeRuntime = Join-Path $taskRoot '.tools/node/node_modules/node/bin/node.exe'
if (-not (Test-Path -LiteralPath $nodeRuntime)) {
    & npm install --prefix .tools/node --no-audit --no-fund node@24.22.3
    if ($LASTEXITCODE -ne 0) { throw 'Node.js 24 installation failed.' }
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'Install uv from https://docs.astral.sh/uv/getting-started/installation/ first.' }
& uv sync --locked --python 3.14.7
if ($LASTEXITCODE -ne 0) { throw 'Python environment installation failed.' }
$env:PATH = "$(Split-Path -Parent $nodeRuntime);$env:PATH"
if ($Install -or -not (Test-Path -LiteralPath 'node_modules/.package-lock.json')) {
    $npmFolder = Split-Path -Parent (Get-Command npm).Source
    $npmEntry = Join-Path $npmFolder 'node_modules/npm/bin/npm-cli.js'
    if (-not (Test-Path -LiteralPath $npmEntry)) { throw 'Cannot locate npm. Install Node.js/npm from the official distribution.' }
    & $nodeRuntime $npmEntry ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
}
& $nodeRuntime node_modules/typescript/bin/tsc -b
if ($LASTEXITCODE -ne 0) { throw 'Type check failed.' }
& $nodeRuntime node_modules/vite/bin/vite.js build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
$env:AINOVEL_PORT = "$Port"
Write-Host "ainovel: http://127.0.0.1:$Port (keep this window open)"
& .venv/Scripts/python.exe -m ainovel
