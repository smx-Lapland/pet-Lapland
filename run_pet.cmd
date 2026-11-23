@echo off
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%PY%" set "PY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe"
if not exist "%PY%" (
    where pythonw >nul 2>&1
    if errorlevel 1 (set "PY=python") else (set "PY=pythonw")
)
start "" "%PY%" -m pet.main
endlocal
