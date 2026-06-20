@echo off
set "SHANG_NO_UPX=1"
call "%~dp0build_windows_onedir.bat"
exit /b %ERRORLEVEL%
