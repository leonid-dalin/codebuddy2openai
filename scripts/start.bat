@echo off
rem Start the WorkBuddy2OpenAI proxy on Windows.
rem
rem   scripts\start.bat
rem   scripts\start.bat --port 9000 --log converter.log
rem
rem Loads .env from the repository root, picks the project virtual
rem environment, and installs the runtime dependencies when they are missing.
setlocal enabledelayedexpansion
cd /d "%~dp0.."

if not exist ".env" goto resolve_python
for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
  set "key=%%A"
  if not "!key!"=="" if not "!key:~0,1!"=="#" set "!key!=%%B"
)

:resolve_python
set "PY="
for %%C in (".venv\Scripts\python.exe" "venv\Scripts\python.exe") do (
  if exist %%C set "PY=%%~C"
)
if not defined PY (
  echo warning: no virtual environment found; falling back to the system interpreter 1>&2
  echo   create one with: py -3 -m venv .venv ^&^& .venv\Scripts\python -m pip install -r requirements.txt 1>&2
  set "PY=python"
)

"%PY%" -c "import httpx, fastapi, uvicorn" 2>nul
if errorlevel 1 "%PY%" -m pip install -q -r requirements.txt

"%PY%" -m workbuddy2openai.converter %*
endlocal
