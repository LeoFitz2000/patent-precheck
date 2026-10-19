$ErrorActionPreference = 'Stop'
$intakePythonCommand = Get-Command pythonw.exe -ErrorAction SilentlyContinue
$intakeArguments = @()
if (-not $intakePythonCommand) {
    $intakePythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
}
if (-not $intakePythonCommand) {
    $intakePythonCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    $intakeArguments += '-3'
}
if (-not $intakePythonCommand) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show('Python 3 is required. Install Python and add it to PATH, then reopen this launcher.', 'Patent precheck') | Out-Null
    exit 1
}
$intakeScript = Join-Path $PSScriptRoot 'app.py'
$intakeArguments += @(('"' + $intakeScript + '"'), '--open')
Start-Process -FilePath $intakePythonCommand.Source -ArgumentList $intakeArguments -WindowStyle Hidden -WorkingDirectory $PSScriptRoot
