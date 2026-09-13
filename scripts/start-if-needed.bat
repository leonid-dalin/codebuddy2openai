@echo off
rem Start the proxy only when it is not already answering.
rem
rem Intended for Task Scheduler at logon: running it while the proxy is up
rem exits without doing anything, so it is safe to trigger repeatedly.
rem
rem   scripts\start-if-needed.bat
curl -s -m 2 -o NUL http://127.0.0.1:8787/health
if %errorlevel%==0 exit /b 0
call "%~dp0start.bat" %*
