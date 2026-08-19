Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

Set WshShell = CreateObject("WScript.Shell")
Set args = WScript.Arguments
Dim cmdArgs
cmdArgs = ""
For i = 0 To args.Count - 1
    If i > 0 Then cmdArgs = cmdArgs & " "
    cmdArgs = cmdArgs & """" & args(i) & """"
Next

pythonExe = scriptDir & "\.venv\Scripts\python.exe"
trainLoop = scriptDir & "\train_loop.py"

WshShell.Run """" & pythonExe & """ -u """ & trainLoop & """ " & cmdArgs, 0, False
