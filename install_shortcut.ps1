$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = Join-Path $desktop 'Claude Pet.lnk'

$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($lnk)
$s.TargetPath = Join-Path $here 'launcher.vbs'
$s.WorkingDirectory = $here
$s.IconLocation = (Join-Path $here 'foamo.ico') + ',0'
$s.Description = 'Claude Pet · Desktop Companion'
$s.Save()

Write-Host "Shortcut created: $lnk"
