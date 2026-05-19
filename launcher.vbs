Option Explicit
Dim sh, fso, here, marker
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
marker = here & "\.deps_installed"
sh.CurrentDirectory = here

If Not fso.FileExists(marker) Then
    ' First run: visible window installs deps, then start.bat launches pet
    sh.Run """" & here & "\start.bat""", 1, True
Else
    ' Subsequent runs: silent launch via pythonw (no console)
    sh.Run "pythonw """ & here & "\claude_pet.py""", 0, False
End If
