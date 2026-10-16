[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$candidate = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$evidence = Join-Path (Split-Path $candidate -Parent) ('v0.12.3-native-build-evidence-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $evidence | Out-Null
$statePath = Join-Path $evidence 'state.json'
$state = @{ stage='starting'; started=(Get-Date).ToUniversalTime().ToString('o'); success=$false }
function Save-State {
    try { $state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding UTF8 }
    catch { Write-Warning 'Could not save build state; service restoration still proceeds.' }
}
function Wait-Model {
    $key = [IO.File]::ReadAllText('C:\Users\mauro\llamacpp\gptoss20b-api-key.txt').Trim()
    $deadline = (Get-Date).AddSeconds(180)
    do {
        try {
            $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8080/health' -Headers @{Authorization="Bearer $key"} -TimeoutSec 5
            if ($response.StatusCode -eq 200) { return }
        } catch { }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw 'GPTOSS20B health did not recover within 180 seconds.'
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator access required.' }
if ((Get-Service OpenWebUI).Status -ne 'Running' -or (Get-Service GPTOSS20B).Status -ne 'Running') { throw 'Expected original services running.' }
$caddyHash = (Get-FileHash -LiteralPath 'C:\ProgramData\Caddy\Caddyfile' -Algorithm SHA256).Hash
$buildProcess = $null
& 'C:\ProgramData\OpenWebUI\runtime\venv-0.12.0-harmony-20261014\Scripts\python.exe' (Join-Path $PSScriptRoot 'drain_tasks.py')
if ($LASTEXITCODE -ne 0) { throw 'Generation drain failed; leaving services running.' }
$outageStarted = Get-Date
Save-State
try {
    $state.stage='stopping-services'; Save-State
    Stop-Service OpenWebUI
    (Get-Service OpenWebUI).WaitForStatus('Stopped',[TimeSpan]::FromSeconds(45))
    Stop-Service GPTOSS20B
    (Get-Service GPTOSS20B).WaitForStatus('Stopped',[TimeSpan]::FromSeconds(45))
    Start-Sleep -Seconds 3
    $memory = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
    $state.available_commit_bytes = [long]$memory.CommitLimit - [long]$memory.CommittedBytes
    $state.required_commit_bytes = 10GB
    $state.stage='memory-gate'; Save-State
    if ($state.available_commit_bytes -lt 10GB) { throw ('Insufficient available commit: {0:N2} GiB, require 10 GiB.' -f ($state.available_commit_bytes/1GB)) }
    $state.stage='building'; Save-State
    $python = Join-Path (Split-Path $candidate -Parent) 'v0.12.3-build-env\Scripts\python.exe'
    $arguments = @('"'+(Join-Path $PSScriptRoot 'upgrade.py')+'"','--build-native','--node-directory','"'+(Join-Path (Split-Path $candidate -Parent) 'build-tools\node-v22.23.3-win-x64')+'"','--frontend-baseline','C:\ProgramData\OpenWebUI\runtime\venv-0.12.0-harmony-20261014\Lib\site-packages\open_webui\frontend')
    $buildProcess = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $candidate -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $evidence 'build.stdout.log') -RedirectStandardError (Join-Path $evidence 'build.stderr.log')
    # Reserve six minutes of the 15-minute window for model readiness and OWU restoration.
    $remaining = [math]::Max(0, (9*60 - ((Get-Date)-$outageStarted).TotalSeconds)*1000)
    if (-not $buildProcess.WaitForExit([int]$remaining)) { throw 'Native build timed out; restoring original services.' }
    if ($buildProcess.ExitCode -ne 0) { throw "Native build failed with exit code $($buildProcess.ExitCode)." }
    $state.build_complete=$true
} catch {
    $state.error=$_.Exception.Message
} finally {
    if ($buildProcess -and -not $buildProcess.HasExited) { & taskkill.exe /PID $buildProcess.Id /T /F | Out-Null }
    $state.stage='restoring-original-services'; Save-State
    try {
        Start-Service GPTOSS20B
        Wait-Model
        Start-Service OpenWebUI
        (Get-Service OpenWebUI).WaitForStatus('Running',[TimeSpan]::FromSeconds(45))
        $version=$null
        $deadline=(Get-Date).AddSeconds(90)
        do {
            try { $version=(Invoke-RestMethod 'http://127.0.0.1:3000/api/version' -TimeoutSec 5).version; if ($version -eq '0.12.0') { break } } catch { }
            Start-Sleep -Seconds 2
        } while ((Get-Date) -lt $deadline)
        if ($version -ne '0.12.0') { throw 'Original OWU did not recover at version 0.12.0.' }
        if ((Get-FileHash -LiteralPath 'C:\ProgramData\Caddy\Caddyfile' -Algorithm SHA256).Hash -ne $caddyHash) { throw 'Caddy configuration changed unexpectedly.' }
        $state.original_services_restored=$true
        $state.success=$state.ContainsKey('build_complete') -and $state.build_complete
        $state.stage=if ($state.success) {'build-complete-original-restored'} else {'build-held-original-restored'}
    } catch { $state.success=$false; $state.restoration_error=$_.Exception.Message; $state.stage='restoration-failed' }
    $state.finished=(Get-Date).ToUniversalTime().ToString('o'); Save-State
}
