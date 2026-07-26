' Run this once to update your Desktop shortcut with the new icon.
' Double-click it or run: cscript update_shortcut.vbs

Dim ws, s, base, bat, ico, shortcut
base    = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
bat     = base & "\launcher.bat"
ico     = base & "\frontend\static\img\chevalier_events.ico"
shortcut = CreateObject("WScript.Shell").SpecialFolders("Desktop") & "\Chevalier Events.lnk"

Set ws = CreateObject("WScript.Shell")
Set s  = ws.CreateShortcut(shortcut)
s.TargetPath     = bat
s.WorkingDirectory = base
s.WindowStyle    = 1
s.Description    = "Launch Chevalier Events"
s.IconLocation   = ico
s.Save()

MsgBox "Desktop shortcut updated with new icon!", 64, "Chevalier Events"
