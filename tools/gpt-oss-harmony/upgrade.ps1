[CmdletBinding()]
param(
    [string]$Destination,
    [string]$PythonPath = 'C:\ProgramData\OpenWebUI\runtime\venv\Scripts\python.exe',
    [switch]$Build
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'Set -PythonPath to an installed Python 3.11 or 3.12 executable.'
}
$upgradeArguments = @((Join-Path $PSScriptRoot 'upgrade.py'))
if ($Destination) { $upgradeArguments += @('--destination', $Destination) }
if ($Build) { $upgradeArguments += '--build' }
& $PythonPath @upgradeArguments
if ($LASTEXITCODE -ne 0) { throw 'Upgrade preparation failed. Inspect the error; source checkout and running services were not updated.' }
