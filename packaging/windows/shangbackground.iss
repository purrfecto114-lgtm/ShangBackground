; ShangBackground Windows installer definition for Inno Setup.
;
; This script is consumed by ``build_tools/build.py installer``. It expects a
; validated PyInstaller standalone layout under ``SourceRoot`` and emits a
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

; Modern look, LZMA2 ultra compression, solid archive for smallest setup.exe.
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes

; 64-bit only (matches the PyInstaller x86_64 standalone layout).
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Always create uninstaller + registry entry so Add/Remove Programs works.
Uninstallable=yes
CreateUninstallRegKey=yes

; Sensible defaults.
PrivilegesRequired=admin
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
; ChineseSimplified.isl ships with Inno Setup 6.5.0+ only. To stay compatible
; with older Inno Setup 6.x installs (e.g. the Chocolatey innosetup package
; currently ships 6.4.x), we bundle the official language file from
; https://raw.githubusercontent.com/jrsoftware/issrc/main/Files/Languages/ChineseSimplified.isl
; and reference it via a relative path rooted at PROJECT_ROOT.
Name: "chinesesimp"; MessagesFile: "{#PROJECT_ROOT}\packaging\windows\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startup"; Description: "开机自启动 {#APP_NAME}"; GroupDescription: "其他选项:"

[Registry]
; v1.4.5: Register HKCU Run key for autostart when the "startup" task is selected.
; This is cleaner than VBS in the Startup folder: no extra wscript.exe process hop,
; auto-cleaned by Inno Setup on uninstall (uninsdeletevalue flag), and visible in
; Task Manager > Startup tab for user transparency.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ShangBackground"; ValueData: """{app}\ShangBackground.exe"" --hide"; Flags: uninsdeletevalue; Tasks: startup

[Messages]
; v1.4.5: Custom welcome page text.
WelcomeLabel1=欢迎使用 ShangBackground 安装向导
WelcomeLabel2=这将安装 ShangBackground {#APP_VERSION} 到您的计算机。%n%nShangBackground 是一个跨平台桌面壁纸管理器，支持静态壁纸、幻灯片、Bing 每日壁纸、视频壁纸和交互式 HTML 壁纸。%n%n建议在继续前关闭所有其他应用程序。%n%n点击"下一步"继续。

[Files]
; Pull in the entire validated standalone layout. The build pipeline produces
; either a PyInstaller layout (ShangBackground\ShangBackground.exe +
; ShangBackground\_internal\) or a Nuitka layout (ShangBackground.dist\*).
; Both are emitted under {#SOURCE_ROOT}. We use skipifsourcedoesntexist so
; ISCC does not abort when one of the two globs matches nothing (only one
; layout is ever present at a time).
Source: "{#SOURCE_ROOT}\ShangBackground\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "{#SOURCE_ROOT}\ShangBackground.dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Icons]
Name: "{group}\{#APP_NAME}"; Filename: "{app}\ShangBackground.exe"; WorkingDir: "{app}"; Comment: "{#PRODUCT_NAME}"
Name: "{group}\卸载 {#APP_NAME}"; Filename: "{uninstallexe}"; Comment: "卸载 {#APP_NAME}"
Name: "{group}\{#APP_NAME} 官方仓库"; Filename: "https://github.com/purrfecto114-lgtm/ShangBackground"
Name: "{autodesktop}\{#APP_NAME}"; Filename: "{app}\ShangBackground.exe"; WorkingDir: "{app}"; Tasks: desktopicon; Comment: "{#PRODUCT_NAME}"

; Auto-launch entry: written only when the user keeps the "startup" task
; selected. The checkbox defaults to checked so a normal install enables
; autostart; power users can opt out explicitly.
Name: "{commonstartup}\{#APP_NAME}"; Filename: "{app}\ShangBackground.exe"; WorkingDir: "{app}"; Tasks: startup; Comment: "开机自启动 {#APP_NAME}"

[Run]
; Offer to launch the app after a successful install. ``postinstall`` keeps
; the wizard open until the user closes the app, ``nowait`` lets the wizard
; finish without waiting for the app to exit.
Filename: "{app}\ShangBackground.exe"; Description: "{cm:LaunchProgram,{#APP_NAME}}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Make sure we do not leave a stale tray process holding files we are about
; to delete. ``--quit`` is the silent-quit IPC shipped by the application's
; single-instance guard.
Filename: "{app}\ShangBackground.exe"; Parameters: "--quit"; RunOnceId: "StopAppBeforeUninstall"; Flags: runhidden

[UninstallDelete]
; v1.4.4: Remove per-user logs and config (same as before).
; We deliberately do NOT touch %AppData%\ShangBackground\wallpapers (the
; user's actual wallpaper library) so an accidental uninstall does not wipe
; personal data.
Type: filesandordirs; Name: "{localappdata}\{#APP_NAME}"

[Code]
// InitializeSetup runs before any file operations.
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

// v1.4.5: Custom UNINSTALL-only page with optional "Delete user config" checkbox.
// This page is created lazily during uninstallation (not during install) so it
// does not interfere with the install wizard flow.
var
  DeleteConfigPage: TOutputMsgWizardPage;
  DeleteConfigCheckbox: TNewCheckbox;
  UninstallPageCreated: Boolean;

procedure CreateUninstallConfigPage();
begin
  if UninstallPageCreated then Exit;
  UninstallPageCreated := True;
  
  DeleteConfigPage := CreateOutputMsgPage(wpSelectProgramGroup,
    '卸载选项',
    '选择卸载时要清理的项目',
    '卸载程序将自动移除开机启动项（包括注册表条目和 VBS 脚本）。' #13#10 #13#10 +
    '您可以选择是否同时删除用户配置文件和日志。壁纸库不会被删除。');
  
  DeleteConfigCheckbox := TNewCheckbox.Create(DeleteConfigPage);
  DeleteConfigCheckbox.Parent := DeleteConfigPage.Surface;
  DeleteConfigCheckbox.Caption := '同时删除用户配置和日志（%LOCALAPPDATA%\ShangBackground）';
  DeleteConfigCheckbox.Checked := False;
  DeleteConfigCheckbox.Top := 40;
  DeleteConfigCheckbox.Width := DeleteConfigPage.SurfaceWidth;
end;

function ShouldDeleteConfig(): Boolean;
begin
  Result := False;
  if UninstallPageCreated and (DeleteConfigCheckbox <> nil) then
    Result := DeleteConfigCheckbox.Checked;
end;

// CurUninstallStepChanged: mandatory VBS/registry cleanup + optional config deletion
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  StartupFolder: String;
  VbsPath: String;
  LegacyVbsPath: String;
  LocalAppData: String;
  ConfigDir: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    // MANDATORY: Remove legacy VBS startup files
    StartupFolder := ExpandConstant('{userstartup}');
    VbsPath := StartupFolder + '\ShangBackground.vbs';
    LegacyVbsPath := StartupFolder + '\PowerOn.vbs';
    
    if FileExists(VbsPath) then
      DeleteFile(VbsPath);
    if FileExists(LegacyVbsPath) then
      DeleteFile(LegacyVbsPath);
    
    // OPTIONAL: Delete user config if checkbox was checked
    if ShouldDeleteConfig() then
    begin
      LocalAppData := ExpandConstant('{localappdata}');
      ConfigDir := LocalAppData + '\ShangBackground';
      if DirExists(ConfigDir) then
        DelTree(ConfigDir, True, True, True);
    end;
  end;
end;

// InitializeUninstallProgressForm: create the config-deletion page during uninstall
procedure InitializeUninstallProgressForm();
begin
  CreateUninstallConfigPage();
end;

// CurStepChanged: post-install sanity check
procedure CurStepChanged(CurStep: TSetupStep);
var
  ExecutablePath: String;
begin
  if CurStep = ssPostInstall then
  begin
    ExecutablePath := ExpandConstant('{app}\ShangBackground.exe');
    if not FileExists(ExecutablePath) then
    begin
      RaiseException('打包产物缺失：安装后未找到 ' + ExecutablePath + '。可能是磁盘空间不足或杀毒软件拦截，请关闭杀毒软件后重试或联系作者。');
    end;
  end;
end;
