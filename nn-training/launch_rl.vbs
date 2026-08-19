Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
Set args = WScript.Arguments

Dim cmdArgs
cmdArgs = ""
For i = 0 To args.Count - 1
    If i > 0 Then cmdArgs = cmdArgs & " "
    cmdArgs = cmdArgs & """" & args(i) & """"
Next

pythonExe = scriptDir & "\.venv\Scripts\python.exe"
trainScript = scriptDir & "\run_rl.py"

WshShell.Run """" & pythonExe & """ -u """ & trainScript & """ " & cmdArgs, 0, False
