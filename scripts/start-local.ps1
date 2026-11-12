param([switch]$Build)
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$frontendDir = Join-Path $projectDir 'frontend'
$pythonPath = Join-Path $projectDir '.venv\Scripts\python.exe'
$nodePath = (Get-Command node.exe).Source
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Create the project Python virtual environment first.' }
foreach ($port in @(8000, 3000)) {
    $probe = [Net.Sockets.TcpClient]::new()
    try {
        $connection = $probe.ConnectAsync('127.0.0.1', $port)
        try { $listening = $connection.Wait(500) -and $probe.Connected } catch { $listening = $false }
        if ($listening) { throw "Port $port is already in use. Stop the existing server before starting another instance." }
    } finally { $probe.Dispose() }
}
if ($Build) {
    Push-Location $frontendDir
    try {
        & $nodePath 'node_modules/next/dist/bin/next' build
        if ($LASTEXITCODE -ne 0) { throw 'The frontend build failed.' }
    } finally { Pop-Location }
}
if (-not (Test-Path -LiteralPath (Join-Path $frontendDir '.next\BUILD_ID'))) {
    throw 'Build the frontend first, or run this script with -Build.'
}
$stateDir = Join-Path $projectDir '.autobench'
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
$env:PYTHONPATH = Join-Path $projectDir 'src'
$apiProcess = Start-Process -FilePath $pythonPath -ArgumentList '-m','uvicorn','autobench.api.main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory $projectDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $stateDir 'api.stdout.log') -RedirectStandardError (Join-Path $stateDir 'api.stderr.log') -PassThru
$uiProcess = Start-Process -FilePath $nodePath -ArgumentList 'node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port','3000' -WorkingDirectory $frontendDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $stateDir 'ui.stdout.log') -RedirectStandardError (Join-Path $stateDir 'ui.stderr.log') -PassThru
$workerProcess = Start-Process -FilePath $pythonPath -ArgumentList '-m','autobench.cli.main','worker' -WorkingDirectory $projectDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $stateDir 'worker.stdout.log') -RedirectStandardError (Join-Path $stateDir 'worker.stderr.log') -PassThru
@{ api=$apiProcess.Id; ui=$uiProcess.Id; worker=$workerProcess.Id } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $stateDir 'local-processes.json')
Write-Output 'AutoBench starting at http://localhost:3000. Logs: .autobench/*.log'
