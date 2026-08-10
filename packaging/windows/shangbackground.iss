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
; Show the Select Start Menu Folder page so the user can rename the group
; or opt out of start-menu shortcuts entirely. Previous builds used
; DisableProgramGroupPage=yes which silently forced the group; users had no
; way to skip start-menu shortcut creation.
DisableProgramGroupPage=no
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
; "startmenu" mirrors the built-in {group} behavior but exposes it as an
; explicit task so the wizard's "Additional shortcuts" page lists it. This
; gives users a visible option to skip Start Menu shortcuts. When unchecked,
; the [Icons] entries guarded by Tasks: startmenu are skipped entirely.
Name: "startmenu"; Description: "创建开始菜单快捷方式"; GroupDescription: "{cm:AdditionalIcons}"
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
; Shortcut Comment (tooltip shown on hover) uses APP_NAME, not PRODUCT_NAME.
; Previous builds set Comment: "{#PRODUCT_NAME}" which made the Windows shell
; tooltip show "Previous Desktop Background" — confusing because the Start
; Menu / Desktop label is "ShangBackground" and users expect the tooltip to
; match the label they clicked.
Name: "{group}\{#APP_NAME}"; Filename: "{app}\ShangBackground.exe"; WorkingDir: "{app}"; Comment: "{#APP_NAME} — {#PRODUCT_NAME}"; Tasks: startmenu
Name: "{group}\卸载 {#APP_NAME}"; Filename: "{uninstallexe}"; Comment: "卸载 {#APP_NAME}"; Tasks: startmenu
Name: "{group}\{#APP_NAME} 官方仓库"; Filename: "https://github.com/purrfecto114-lgtm/ShangBackground"; Tasks: startmenu
Name: "{autodesktop}\{#APP_NAME}"; Filename: "{app}\ShangBackground.exe"; WorkingDir: "{app}"; Tasks: desktopicon; Comment: "{#APP_NAME} — {#PRODUCT_NAME}"

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
; Use both 'filesandordirs' (recursive delete of files + empty subdirs) and an
; explicit 'dirifempty' fallback so the directory itself is removed even when
; only subdirectories existed.
Type: filesandordirs; Name: "{localappdata}\{#APP_NAME}"; Check: ShouldDeleteConfig
Type: dirifempty; Name: "{localappdata}\{#APP_NAME}"; Check: ShouldDeleteConfig
; The single-instance lock lives in a per-user hashed directory next to the
; main data dir (see src/core/single_instance.py:_runtime_dir). The hash
; suffix is derived from the user name, so we cannot hard-code the full name
; here. Instead, the [Code] section's CurUninstallStepChanged procedure
; scans %LOCALAPPDATA% for 'ShangBackground-*' directories and removes them.
; This entry handles the legacy non-hashed lock file location as a fallback.
Type: files; Name: "{localappdata}\{#APP_NAME}\single_instance.lock"; Check: ShouldDeleteConfig
; Legacy session wallpaper file written to %TEMP% by older releases.
Type: files; Name: "{tmp}\ShangBackground_session_wallpaper.json"

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

// CurUninstallStepChanged: mandatory VBS/registry/lock cleanup.
// Runs during usUninstall BEFORE [UninstallDelete] evaluates its Check functions,
// so the single-instance lock file is released and can be deleted.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  StartupFolder: String;
  VbsPath: String;
  LocalAppData: String;
  FindResult: Boolean;
  FindRec: TFindRec;
  LockDir: String;
  LegacySessionFile: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    // MANDATORY: Remove legacy VBS startup files (v1.4.4 and earlier).
    StartupFolder := ExpandConstant('{userstartup}');
    VbsPath := StartupFolder + '\ShangBackground.vbs';
    if FileExists(VbsPath) then
      DeleteFile(VbsPath);

    // MANDATORY: Remove the per-user single-instance lock directory.
    // src/core/single_instance.py creates %LOCALAPPDATA%\ShangBackground-<hash>\
    // (the hash is derived from the user name) to hold single_instance.lock.
    // The [Registry] section's uninsdeletekey flags cannot clean this because
    // it is a file-system directory, not a registry key, and the hash suffix
    // prevents hard-coding the path. Scan %LOCALAPPDATA% for matching prefixes.
    LocalAppData := ExpandConstant('{localappdata}');
    FindResult := FindFirst(LocalAppData + '\ShangBackground-*', FindRec);
    try
      while FindResult do
      begin
        if FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0 then
        begin
          LockDir := LocalAppData + '\' + FindRec.Name;
          // Delete the lock file first so the directory can be removed.
          if FileExists(LockDir + '\single_instance.lock') then
            DeleteFile(LockDir + '\single_instance.lock');
          if DirExists(LockDir) then
            RemoveDir(LockDir);
        end;
        FindResult := FindNext(FindRec);
      end;
    finally
      FindClose(FindRec);
    end;

    // MANDATORY: Remove the legacy session wallpaper file written to %TEMP%
    // by v1.4.x and earlier. The new path lives under the data dir and is
    // cleaned by [UninstallDelete] when the user confirms config deletion.
    LegacySessionFile := ExpandConstant('{tmp}\ShangBackground_session_wallpaper.json');
    if FileExists(LegacySessionFile) then
      DeleteFile(LegacySessionFile);
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
