' Launches keep-running.bat with no visible window, so nobody can close
' it by accident. Used by install-autostart.bat.
' To stop the CRM: Task Manager, Details tab, end python.exe
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.Run "keep-running.bat", 0, False
