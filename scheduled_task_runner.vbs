Option Explicit

Dim shell, fso, scriptDir, command, i

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
command = "powershell.exe -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File " & QuoteArg(fso.BuildPath(scriptDir, "scheduled_run.ps1"))

For i = 0 To WScript.Arguments.Count - 1
    command = command & " " & QuoteArg(WScript.Arguments.Item(i))
Next

shell.Run command, 0, False

Function QuoteArg(value)
    QuoteArg = """" & Replace(value, """", """""") & """"
End Function
