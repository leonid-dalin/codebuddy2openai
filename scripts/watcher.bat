@echo off
rem Launch the proxy watcher on Windows.
rem
rem Requires Git for Windows for bash. The watcher exits immediately when one
rem is already running, so this is safe to place in the startup folder or in a
rem Task Scheduler trigger at logon.
rem
rem   scripts\watcher.bat
cd /d "%~dp0"
set "BASH="
if exist "%ProgramFiles%\Git\bin\bash.exe" set "BASH=%ProgramFiles%\Git\bin\bash.exe"
if not defined BASH if exist "%ProgramFiles(x86)%\Git\bin\bash.exe" set "BASH=%ProgramFiles(x86)%\Git\bin\bash.exe"
if not defined BASH (
  echo error: bash.exe not found. Install Git for Windows, or run the watcher from WSL. 1>&2
  exit /b 1
)
"%BASH%" --noprofile --norc "%~dp0watcher.sh"
