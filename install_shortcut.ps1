$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = Join-Path $desktop 'Foamo.lnk'

$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($lnk)
$s.TargetPath = Join-Path $here 'launcher.vbs'
$s.WorkingDirectory = $here
$s.IconLocation = (Join-Path $here 'foamo.ico') + ',0'
$s.Description = 'Foamo Desktop Pet'
$s.Save()

Write-Host "Shortcut created: $lnk"
