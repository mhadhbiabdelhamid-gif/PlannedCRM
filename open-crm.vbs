' Opens the CRM. Starts the server first if it is not already running.
' This is what the desktop shortcut points at.
Option Explicit

Dim fso, shell, here, url, running, procs, p
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = here
url = "http://localhost:5000"

' Is the CRM already up? Look for a python process running serve_office.py.
running = False
On Error Resume Next
Set procs = GetObject("winmgmts:\\.\root\cimv2").ExecQuery( _
    "SELECT CommandLine FROM Win32_Process WHERE Name = 'python.exe'")
If Err.Number = 0 Then
    For Each p In procs
        If Not IsNull(p.CommandLine) Then
            If InStr(LCase(p.CommandLine), "serve_office.py") > 0 Then running = True
        End If
    Next
End If
On Error GoTo 0

If Not running Then
    shell.Run """" & fso.BuildPath(here, "keep-running.bat") & """", 0, False
    ' Give the server a moment to bind the port before the browser asks for it.
    WScript.Sleep 7000
End If

shell.Run url, 1, False
