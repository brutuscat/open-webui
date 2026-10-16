[CmdletBinding()]
param(
    [string]$Destination,
    [string]$PythonPath = 'C:\ProgramData\OpenWebUI\runtime\venv\Scripts\python.exe',
    [switch]$Build,
    [switch]$BuildNative,
    [string]$NodeDirectory,
    [string]$FrontendBaseline,
    [switch]$DependenciesOnly
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'Set -PythonPath to an installed Python 3.11 or 3.12 executable.'
}
$upgradeArguments = @((Join-Path $PSScriptRoot 'upgrade.py'))
if ($Destination) { $upgradeArguments += @('--destination', $Destination) }
if ($Build) { $upgradeArguments += '--build' }
if ($BuildNative) {
    $upgradeArguments += @('--build-native', '--node-directory', $NodeDirectory, '--frontend-baseline', $FrontendBaseline)
    if ($DependenciesOnly) { $upgradeArguments += '--dependencies-only' }
}
& $PythonPath @upgradeArguments
if ($LASTEXITCODE -ne 0) { throw 'Upgrade preparation failed. Inspect the error; source checkout and running services were not updated.' }
