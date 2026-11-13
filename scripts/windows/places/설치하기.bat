@echo off
rem Installs the Instagram place archive bot. Logic lives in install.ps1.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem *.ps1 | Unblock-File; & .\install.ps1"
echo.
pause
