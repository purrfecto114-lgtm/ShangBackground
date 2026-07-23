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
SetupIconFile={#PROJECT_ROOT}\src\img\LOGO.ico
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

[Files]
; Pull in the entire validated PyInstaller standalone layout. The trailing
; flag ``recursesubdirs`` keeps the ShangBackground/ and ShangBackground/_internal/
; layout intact, while ``createallsubdirs`` ensures empty dirs survive.
Source: "{#SOURCE_ROOT}\ShangBackground\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

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
; Remove per-user logs and config only when the user explicitly uninstalls.
; We deliberately do NOT touch %AppData%\ShangBackground\wallpapers (the
; user's actual wallpaper library) so an accidental uninstall does not wipe
; personal data.
Type: filesandordirs; Name: "{localappdata}\{#APP_NAME}"

[Code]
// InitializeSetup runs before any file operations. We use it only for
// environment checks that do NOT depend on installed files - the actual
// "is ShangBackground.exe present in the source bundle" check is done at
// build time by build_tools/buildlib/installer.py:_validate_source_layout,
// and ISCC itself fails compilation if the [Files] Source glob matches
// nothing. There is intentionally no PrepareToInstall check here because
// PrepareToInstall fires BEFORE [Files] copies anything, so checking
// {app}\ShangBackground.exe at that point would always fail.
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

// CurStepChanged runs AFTER file installation. Use it as a post-install
// sanity check: if the executable is missing at this point, something
// went wrong with the [Files] copy (disk full, AV interference, etc.)
// and we should abort with a clear message instead of letting the
// [Run] section try to launch a non-existent file.
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
