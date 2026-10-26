' 工作交办挂件 —— 静默启动器（不弹黑窗）
' 用法：双击本文件即可；也可以给它在桌面建快捷方式。
' 依赖：pythonw.exe（本目录上一级有 venv\Scripts\pythonw.exe 就优先用它，否则用 PATH 里的 pythonw）

Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)

py = base & "\venv\Scripts\pythonw.exe"
If Not fso.FileExists(py) Then
    py = base & "\..\venv\Scripts\pythonw.exe"
End If
If Not fso.FileExists(py) Then
    py = "pythonw.exe"
End If

script = base & "\任务挂件.py"
If Not fso.FileExists(script) Then script = base & "\桌面挂件\任务挂件.py"

CreateObject("WScript.Shell").Run """" & py & """ """ & script & """", 0, False
