; ShangBackground Windows installer definition for Inno Setup.
;
; This script is consumed by ``build_tools/build.py installer``. It expects a
; validated Nuitka or PyInstaller standalone layout under ``SourceRoot`` and emits a
; single self-contained ``setup.exe`` that bundles the application, start-menu
; shortcuts, uninstaller, and the user licence agreement.
;
; The licence agreement is shown as the *first* wizard page after the welcome
; screen. ``LicenseFile`` plus the Inno Setup licence dialog make acceptance
; mandatory: the Next button stays disabled until the user selects
; "I accept the agreement".
;
; Rebuild from source after editing this file:
;   python build_tools/build.py installer --target windows --arch x86_64 --profile lite
;
; Render-time placeholders (overridden by ISCC.exe /D flags from
; build_tools/buildlib/installer.py). The defaults below let the .iss open in
; the Inno Setup IDE for visual inspection without a Python driver.
;   /DAPP_NAME             (default: ShangBackground)
;   /DAPP_VERSION          (default: 0.0.0)
;   /DAPP_VERSION_PUB      (default: 0.0.0.0)
;   /DCOMPANY_NAME         (default: XXDZ Studio)
;   /DPRODUCT_NAME         (default: Previous Desktop Background)
;   /DARCH                 (default: x86_64)
;   /DSOURCE_ROOT          (default: .)
;   /DBUNDLE_SUBDIR        (default: ShangBackground.dist)
;   /DMANIFEST_RELATIVE    (default: build-features.json)
;   /DOUTPUT_DIR           (default: .)
;   /DOUTPUT_BASENAME      (default: ShangBackground-setup)
;   /DPROJECT_ROOT         (default: .)

#ifndef APP_NAME
  #define APP_NAME "ShangBackground"
#endif
#ifndef APP_VERSION
  #define APP_VERSION "0.0.0"
#endif
#ifndef APP_VERSION_PUB
  #define APP_VERSION_PUB "0.0.0.0"
#endif
#ifndef COMPANY_NAME
  #define COMPANY_NAME "XXDZ Studio"
#endif
#ifndef PRODUCT_NAME
  #define PRODUCT_NAME "Previous Desktop Background"
#endif
#ifndef ARCH
  #define ARCH "x86_64"
#endif
#ifndef SOURCE_ROOT
  #define SOURCE_ROOT "."
#endif
#ifndef BUNDLE_SUBDIR
  #define BUNDLE_SUBDIR "ShangBackground.dist"
#endif
#ifndef MANIFEST_RELATIVE
  #define MANIFEST_RELATIVE "build-features.json"
#endif
#ifndef OUTPUT_DIR
  #define OUTPUT_DIR "."
#endif
#ifndef OUTPUT_BASENAME
  #define OUTPUT_BASENAME "ShangBackground-setup"
#endif
#ifndef PROJECT_ROOT
  #define PROJECT_ROOT "."
#endif

[Setup]
AppId={{XXDZ-ShangBackground-PreviousDesktopBackground}
AppName={#APP_NAME}
AppVersion={#APP_VERSION}
AppVerName={#APP_NAME} {#APP_VERSION}
AppPublisher={#COMPANY_NAME}
AppPublisherURL=https://github.com/purrfecto114-lgtm/ShangBackground
AppSupportURL=https://github.com/purrfecto114-lgtm/ShangBackground/issues
AppUpdatesURL=https://github.com/purrfecto114-lgtm/ShangBackground/releases
AppContact=https://github.com/purrfecto114-lgtm/ShangBackground
AppCopyright=Copyright (C) {#COMPANY_NAME}
VersionInfoVersion={#APP_VERSION_PUB}
VersionInfoCompany={#COMPANY_NAME}
VersionInfoProductName={#PRODUCT_NAME}
VersionInfoProductVersion={#APP_VERSION_PUB}

; License agreement shown right after the welcome page. The Next button is
; disabled until the user explicitly accepts. This satisfies the requirement
; that the agreement must be accepted before installation can proceed.
LicenseFile={#PROJECT_ROOT}\packaging\windows\license.rtf

; Single self-extracting setup.exe, no external payload.
OutputDir={#OUTPUT_DIR}
OutputBaseFilename={#OUTPUT_BASENAME}
SetupIconFile={#PROJECT_ROOT}\src\img\installer_icon.ico
UninstallDisplayIcon={app}\ShangBackground.exe
UninstallDisplayName={#APP_NAME}

; Inno Setup 7: emit a native x64 installer for this x64-only application.
; Either the 32-bit or 64-bit Inno Setup 7 compiler can build it.
SetupArchitecture=x64

; Modern look, LZMA2 ultra compression, solid archive for smallest setup.exe.
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes

; 64-bit only. Qt/PySide 6.11 supports Windows 10 1809+ on x86_64,
; so do not let Setup install successfully on an OS where the app cannot run.
MinVersion=10.0.17763
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Always create uninstaller + registry entry so Add/Remove Programs works.
Uninstallable=yes
CreateUninstallRegKey=yes

; Sensible defaults.
PrivilegesRequired=admin
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
DisableProgramGroupPage=yes
DisableDirPage=no
DefaultDirName={autopf}\{#APP_NAME}
DefaultGroupName={#APP_NAME}

; Friendly installer metadata in file properties.
InternalCompressLevel=ultra64
ShowLanguageDialog=no
LanguageDetectionMethod=none
DisableWelcomePage=no

[Languages]
; Keep the Simplified Chinese language file in-repo for reproducible builds
; instead of depending on a compiler installation's optional language payload.
Name: "chinesesimp"; MessagesFile: "{#PROJECT_ROOT}\packaging\windows\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startup"; Description: "开机自启动 {#APP_NAME}"; GroupDescription: "其他选项:"

[Registry]
; v1.4.5: Register HKCU Run key for autostart when the "startup" task is selected.
; This is cleaner than VBS in the Startup folder: no extra wscript.exe process hop,
; cleaned by the unconditional uninstall entry below, and visible in
; Task Manager > Startup tab for user transparency.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ShangBackground"; ValueData: """{app}\ShangBackground.exe"" --hide"; Tasks: startup
; Always remove startup values, including values enabled later from inside the
; application and the product-specific value used by older releases.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "ShangBackground"; Flags: dontcreatekey uninsdeletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "xxdz_WallpaperController"; Flags: dontcreatekey uninsdeletevalue
; The application creates these per-user shell entries at runtime. Record
; uninstall-only cleanup without creating disabled entries during installation.
Root: HKCU; Subkey: "Software\Classes\DesktopBackground\Shell\LastWallpaper"; Flags: dontcreatekey uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\DesktopBackground\Shell\NextWallpaper"; Flags: dontcreatekey uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\DesktopBackground\Shell\RandomWallpaper"; Flags: dontcreatekey uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\DesktopBackground\Shell\ZJumpToWallpaper"; Flags: dontcreatekey uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\DesktopBackground\Shell\JumpToWallpaper"; Flags: dontcreatekey uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\DesktopBackground\Shell\~~PersonalizeBackground"; Flags: dontcreatekey uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\image\shell\ShangBackgroundSetWallpaper"; Flags: dontcreatekey uninsdeletekey
; Older elevated releases wrote the same product-owned keys through HKCR,
; which resolves to the machine Classes hive when no per-user override exists.
Root: HKLM; Subkey: "Software\Classes\DesktopBackground\Shell\LastWallpaper"; Flags: dontcreatekey uninsdeletekey
Root: HKLM; Subkey: "Software\Classes\DesktopBackground\Shell\NextWallpaper"; Flags: dontcreatekey uninsdeletekey
Root: HKLM; Subkey: "Software\Classes\DesktopBackground\Shell\RandomWallpaper"; Flags: dontcreatekey uninsdeletekey
Root: HKLM; Subkey: "Software\Classes\DesktopBackground\Shell\ZJumpToWallpaper"; Flags: dontcreatekey uninsdeletekey
Root: HKLM; Subkey: "Software\Classes\DesktopBackground\Shell\JumpToWallpaper"; Flags: dontcreatekey uninsdeletekey
Root: HKLM; Subkey: "Software\Classes\DesktopBackground\Shell\~~PersonalizeBackground"; Flags: dontcreatekey uninsdeletekey
Root: HKLM; Subkey: "Software\Classes\SystemFileAssociations\image\shell\ShangBackgroundSetWallpaper"; Flags: dontcreatekey uninsdeletekey

[Messages]
; v1.4.5: Custom welcome page text.
WelcomeLabel1=欢迎使用 ShangBackground 安装向导
WelcomeLabel2=这将安装 ShangBackground {#APP_VERSION} 到您的计算机。%n%nShangBackground 是一个跨平台桌面壁纸管理器，支持静态壁纸、幻灯片、Bing 每日壁纸、视频壁纸和交互式 HTML 壁纸。%n%n建议在继续前关闭所有其他应用程序。%n%n点击"下一步"继续。

[Files]
; The Python build driver resolves exactly one validated freezer layout and
; passes its subdirectory explicitly. Do not use dual skipifsourcedoesntexist
; globs here: a wrong/empty build must fail compilation rather than silently
; producing a setup.exe with no application payload.
Source: "{#SOURCE_ROOT}\{#BUNDLE_SUBDIR}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#APP_NAME}"; Filename: "{app}\ShangBackground.exe"; WorkingDir: "{app}"; Comment: "{#PRODUCT_NAME}"
Name: "{group}\卸载 {#APP_NAME}"; Filename: "{uninstallexe}"; Comment: "卸载 {#APP_NAME}"
Name: "{group}\{#APP_NAME} 官方仓库"; Filename: "https://github.com/purrfecto114-lgtm/ShangBackground"
Name: "{autodesktop}\{#APP_NAME}"; Filename: "{app}\ShangBackground.exe"; WorkingDir: "{app}"; Tasks: desktopicon; Comment: "{#PRODUCT_NAME}"

; Autostart is provided only by the HKCU Run value above. A second common
; Startup shortcut would launch another process and cannot be disabled from
; the application's per-user startup setting.

[InstallDelete]
; Remove the duplicate all-users Startup shortcut created by v1.4.5.
Type: files; Name: "{commonstartup}\{#APP_NAME}.lnk"
; _internal is an application-owned PyInstaller payload directory. Clearing it
; avoids stale DLL/PYD files surviving a PyInstaller upgrade and also cleans it
; when migrating a previous PyInstaller installation to the release Nuitka build.
; We deliberately do NOT wildcard-delete {app}: users may choose a custom path.
Type: filesandordirs; Name: "{app}\_internal"
; MPV is a native dependency bundle whose filenames change across builds.
; Leaving obsolete codec/runtime DLLs beside a newer mpv.exe can produce
; loader failures that are very hard to diagnose. Replace this product-owned
; subtree deterministically on every upgrade instead of layering new files over it.
Type: filesandordirs; Name: "{app}\bin\mpv"
; Remove the old root manifest before the selected bundle layout is copied.
Type: files; Name: "{app}\build-features.json"

[Run]
; Offer to launch the app after a successful install. ``postinstall`` adds the
; final-page checkbox, while ``nowait`` lets the wizard finish immediately.
Filename: "{app}\ShangBackground.exe"; Description: "{cm:LaunchProgram,{#APP_NAME}}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Delete settings and logs only after explicit confirmation. This directory
; can also contain user-managed runtime files, so the default is to preserve it.
Type: filesandordirs; Name: "{localappdata}\{#APP_NAME}"; Check: ShouldDeleteConfig

[Code]
// InitializeSetup runs before any file operations.
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

// Upgrade lifecycle: ask the currently logged-in user's existing instance to
// perform its normal exit transaction before Restart Manager scans for locked
// payload files.  Setup itself is elevated, so use ExecAsOriginalUser to keep
// the helper process in the same user/session/security context as the tray app.
// Failure is deliberately non-fatal: a damaged older executable or unavailable
// IPC must not make the package un-upgradeable; CloseApplications=yes remains
// the fallback immediately after this hook.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExecutablePath: String;
  ResultCode: Integer;
  ExistingVersion: Int64;
  MinimumGracefulQuitVersion: Int64;
begin
  Result := '';
  ExecutablePath := ExpandConstant('{app}\ShangBackground.exe');
  if not FileExists(ExecutablePath) then
    Exit;

  // Older releases did not understand --quit. Starting one merely to ask it
  // to exit could instead open a fresh GUI and make ewWaitUntilTerminated hang.
  // 1.4.2 is the first verified release in this upgrade line with --quit.
  MinimumGracefulQuitVersion := PackVersionComponents(1, 4, 2, 0);
  if (not GetPackedVersion(ExecutablePath, ExistingVersion)) or
     (ComparePackedVersion(ExistingVersion, MinimumGracefulQuitVersion) < 0) then
  begin
    Log('{#APP_NAME}: installed version predates the verified graceful-quit protocol; Restart Manager will handle remaining locks');
    Exit;
  end;

  try
    if ExecAsOriginalUser(ExecutablePath, '--quit --wait-for-exit',
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    begin
      if ResultCode = 0 then
        Log('{#APP_NAME}: existing instance exited cleanly before upgrade')
      else
        Log(Format('{#APP_NAME}: graceful pre-upgrade exit returned code %d; Restart Manager will handle remaining locks', [ResultCode]));
    end
    else
      Log(Format('{#APP_NAME}: could not launch graceful pre-upgrade exit helper (%d: %s); Restart Manager will handle remaining locks', [ResultCode, SysErrorMessage(ResultCode)]));
  except
    Log('{#APP_NAME}: graceful pre-upgrade exit helper raised an exception; Restart Manager will handle remaining locks: ' + GetExceptionMessage);
  end;
end;

function InitializeUninstall(): Boolean;
var
  ExecutablePath: String;
  ResultCode: Integer;
begin
  Result := True;
  ExecutablePath := ExpandConstant('{app}\ShangBackground.exe');
  if not FileExists(ExecutablePath) then
    Exit;

  if (not Exec(ExecutablePath, '--quit --wait-for-exit', ExpandConstant('{app}'),
    SW_HIDE, ewWaitUntilTerminated, ResultCode)) or (ResultCode <> 0) then
  begin
    Result := SuppressibleMsgBox(
      '无法通过应用内退出命令确认 {#APP_NAME} 已停止。' #13#10 #13#10 +
      '这也可能发生在安装文件已经损坏、原生依赖无法加载时。' #13#10 +
      '建议先从托盘退出程序；如果程序已经无法启动，可以继续卸载。' #13#10 #13#10 +
      '是否仍然继续卸载？',
      mbConfirmation, MB_YESNO, IDYES) = IDYES;
  end;
end;

var
  DeleteConfigSelected: Boolean;

function ShouldDeleteConfig(): Boolean;
begin
  Result := DeleteConfigSelected;
end;

// CurUninstallStepChanged: mandatory VBS/registry cleanup + optional config deletion
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  StartupFolder: String;
  VbsPath: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    // MANDATORY: Remove legacy VBS startup files
    StartupFolder := ExpandConstant('{userstartup}');
    VbsPath := StartupFolder + '\ShangBackground.vbs';
    
    if FileExists(VbsPath) then
      DeleteFile(VbsPath);
  end;
end;

// Custom setup wizard pages cannot be created by the separate uninstaller.
// SuppressibleMsgBox is supported there and safely defaults to preserving data
// for /SILENT and /VERYSILENT runs.
procedure InitializeUninstallProgressForm();
begin
  DeleteConfigSelected := SuppressibleMsgBox(
    '是否同时删除用户配置和日志？' #13#10 #13#10 +
    '位置：' + ExpandConstant('{localappdata}\{#APP_NAME}') + #13#10 +
    '选择“否”将保留这些数据，便于以后重新安装。',
    mbConfirmation, MB_YESNO, IDNO) = IDYES;
end;

// CurStepChanged: post-install sanity check
procedure CurStepChanged(CurStep: TSetupStep);
var
  ExecutablePath: String;
  ManifestPath: String;
begin
  if CurStep = ssPostInstall then
  begin
    ExecutablePath := ExpandConstant('{app}\ShangBackground.exe');
    ManifestPath := ExpandConstant('{app}\{#MANIFEST_RELATIVE}');
    if not FileExists(ExecutablePath) then
      RaiseException('打包产物缺失：安装后未找到 ' + ExecutablePath + '。可能是磁盘空间不足或安全软件拦截，请检查安装日志。');
    if not FileExists(ManifestPath) then
      RaiseException('打包产物不完整：安装后未找到构建清单 ' + ManifestPath + '。请检查安装日志。');
  end;
end;
