' Start Voice Typing without opening a Command Prompt window.
Option Explicit

Dim shell, fso, root, pythonw, appFile
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = root & "\.venv\Scripts\pythonw.exe"
appFile = root & "\desktop_app.py"

If Not fso.FileExists(pythonw) Then
    MsgBox "The app is not installed yet." & vbCrLf & _
           "Please run Install_Voice_Typing.bat first.", _
           vbExclamation, "Voice Typing Offline"
    WScript.Quit 1
End If

If Not fso.FileExists(appFile) Then
    MsgBox "desktop_app.py was not found in the program folder.", _
           vbCritical, "Voice Typing Offline"
    WScript.Quit 2
End If

shell.CurrentDirectory = root
shell.Run Chr(34) & pythonw & Chr(34) & " " & Chr(34) & appFile & Chr(34), 0, False
