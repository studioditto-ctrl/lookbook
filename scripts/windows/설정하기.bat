@echo off
rem  더블클릭으로 설정을 시작한다.
rem  로직은 install.ps1 에 있고, 여기서는 실행 정책만 풀어 부른다.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem '*.ps1' | Unblock-File; & '.\install.ps1'"
echo.
pause
