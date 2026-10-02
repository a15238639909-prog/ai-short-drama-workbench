Option Explicit
' AI Video Workbench V1.0 launcher. Double-click: starts server.py if not running, then opens the browser.
Dim sh, fso, ready, i, here, py
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
py = "python"

ready = IsUp()
If Not ready Then
    sh.Environment("PROCESS")("V41_NO_BROWSER") = "1"
    sh.Environment("PROCESS")("PYTHONIOENCODING") = "utf-8"
    sh.CurrentDirectory = here
    sh.Run py & " """ & here & "\server.py""", 0, False
    For i = 1 To 60
        WScript.Sleep 1000
        If IsUp() Then Exit For
    Next
End If
If IsUp() Then
    sh.Run "http://127.0.0.1:8853/", 1, False
Else
    MsgBox "Server did not start. Open a command prompt in " & here & " and run: python server.py", 48, "AI Video Workbench V1.0"
End If

Function IsUp()
    On Error Resume Next
    Dim http
    Set http = CreateObject("MSXML2.XMLHTTP")
    http.Open "GET", "http://127.0.0.1:8853/api/tasks", False
    http.Send
    IsUp = (Err.Number = 0)
    If IsUp Then IsUp = (http.Status = 200)
    Err.Clear
End Function
