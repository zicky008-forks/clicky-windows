; ────────────────────────────────────────────────────────────────────
;  Clicky for Windows — Inno Setup script
;
;  Builds a single Setup-Clicky.exe from the PyInstaller dist folder.
;
;  Prerequisites:
;    1. Run  build.bat  first (produces dist\Clicky\)
;    2. Install Inno Setup 6 from https://jrsoftware.org/isdl.php
;    3. Run:  iscc installer.iss
;
;  Output:  dist\Setup-Clicky.exe   (single-file installer, ~200-400 MB)
; ────────────────────────────────────────────────────────────────────

#define MyAppName        "Clicky"
#define MyAppVersion     "1.6.0-preview"
#ifndef BuildRoot
  #define BuildRoot "dist\Clicky"
#endif
#define MyAppPublisher   "Shashank Singh"
#define MyAppURL         "https://github.com/zicky008-forks/clicky-windows"
#define MyAppExeName     "Clicky.exe"

[Setup]
AppId={{9A4E3F2C-7B1D-4A8F-9C6E-3D7F1B5E9A0C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
VersionInfoVersion=1.6.0.0
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
; One-click install (spec 7, v5-1): the user double-clicks Setup and only sees the progress bar.
; Per-user folder (%LOCALAPPDATA%\Programs\Clicky), never Program Files, so no admin rights and no
; "install for all users?" question. No licence, folder, tasks, ready or finished page; no account,
; no API key, no Ollama and no AI-provider choice anywhere.
DefaultDirName={userpf}\{#MyAppName}
UsePreviousAppDir=no
DefaultGroupName={#MyAppName}
DisableWelcomePage=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableFinishedPage=yes
OutputDir=dist
OutputBaseFilename=Setup-Clicky
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
#if FileExists("assets\icon.ico")
  SetupIconFile=assets\icon.ico
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
; Inno Setup has no official Vietnamese translation, so the few lines a user actually sees are
; overridden here. This file is saved as UTF-8 with a BOM so these strings compile correctly.
SetupAppTitle=Cài đặt Clicky
SetupWindowTitle=Cài đặt Clicky
WizardInstalling=Đang cài Clicky
InstallingLabel=Chị đợi một chút, không cần làm gì thêm.
StatusExtractFiles=Đang chép Clicky vào máy...
StatusCreateDirs=Đang chuẩn bị...
StatusCreateIcons=Đang tạo biểu tượng trên màn hình...
StatusCreateIniEntries=Đang hoàn tất...
StatusCreateRegistryEntries=Đang hoàn tất...
StatusRegisterFiles=Đang hoàn tất...
StatusSavingUninstall=Đang hoàn tất...
StatusRunProgram=Đang mở Clicky...
StatusRollback=Đang hoàn tác, máy chị được giữ như cũ...
ButtonCancel=Hủy
ExitSetupTitle=Dừng cài đặt
ExitSetupMessage=Clicky chưa cài xong. Chị có muốn dừng không?%n%nLúc khác chị mở lại file cài là được.
UninstallAppTitle=Gỡ Clicky
UninstallAppFullTitle=Gỡ Clicky
ConfirmUninstall=Gỡ Clicky khỏi máy? File Excel của chị không bị đụng tới.
UninstallStatusLabel=Đang gỡ Clicky...
UninstalledAll=Đã gỡ Clicky khỏi máy.

[InstallDelete]
; Older previews accidentally bundled a foreign ICU that shadows Windows ICU.
Type: files; Name: "{app}\_internal\icuuc.dll"

[UninstallDelete]
Type: files; Name: "{app}\install-stamp.txt"

[Files]
; Everything PyInstaller produced
Source: "{#BuildRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}";                    Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Gỡ {#MyAppName}";                 Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}";              Filename: "{app}\{#MyAppExeName}"

[Run]
; Open Clicky as soon as the files are in place: the welcome card is the next thing the user sees.
; Not "postinstall" (there is no finished page to tick it on); skipped for silent updates.
Filename: "{app}\{#MyAppExeName}"; Flags: nowait skipifsilent

[Code]
{ Reinstalling must greet the user again even though setup_complete.flag survives in
  %LOCALAPPDATA%\Clicky. Every install a person clicks through writes a new stamp next to
  Clicky.exe; the app shows the welcome when the flag does not hold this stamp (first_run.py).
  Silent installs (in-app updates) keep the old stamp, so an update never re-shows the welcome.
  Written at ssInstall so it exists before [Run] starts Clicky. }
procedure CurStepChanged(CurStep: TSetupStep);
var
  Folder: String;
begin
  if (CurStep = ssInstall) and (not WizardSilent) then
  begin
    Folder := ExpandConstant('{app}');
    ForceDirectories(Folder);
    SaveStringToFile(Folder + '\install-stamp.txt',
      '{#MyAppVersion} ' + GetDateTimeString('yyyy/mm/dd hh:nn:ss', '-', ':'), False);
  end;
end;
