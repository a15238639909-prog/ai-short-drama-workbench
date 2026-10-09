Option Explicit
Dim shell, fs, root, pythonPath, candidates, item
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(WScript.ScriptFullName)
pythonPath = ""
candidates = Array(shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Python\pythoncore-3.14-64\pythonw.exe", shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python313\pythonw.exe", shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Microsoft\WindowsApps\pyw.exe")
For Each item In candidates
  If fs.FileExists(item) Then
    pythonPath = item
    Exit For
  End If
Next
If pythonPath = "" Then
  MsgBox "Python 3.10+ is required. Run launch.pyw with Python.", 48, "Douyin Downloader"
  WScript.Quit 1
End If
shell.Run """" & pythonPath & """ -B """ & root & "\launch.pyw""", 0, False
