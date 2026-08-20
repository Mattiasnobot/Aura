@echo off
setlocal
cd /d "%~dp0"
set "AURA_PYTHONW="
call :candidate "%~dp0.venv\Scripts\pythonw.exe"
call :candidate "%LocalAppData%\Programs\Python\Python313\pythonw.exe"
call :candidate "%LocalAppData%\Programs\Python\Python312\pythonw.exe"
call :candidate "%LocalAppData%\Programs\Python\Python311\pythonw.exe"
call :candidate "%LocalAppData%\Programs\Python\Python310\pythonw.exe"
if defined AURA_PYTHONW goto :launch
where py >nul 2>nul
if %errorlevel% equ 0 (
    start "Aura" pyw -3 aura_app.py
    exit /b 0
)
where python >nul 2>nul
if %errorlevel% equ 0 (
    start "Aura" pythonw aura_app.py
    exit /b 0
)
echo Aura needs Python 3.10 or newer.
echo Install Python from python.org and select "Add Python to PATH".
pause
exit /b 1

:candidate
if not defined AURA_PYTHONW if exist "%~1" set "AURA_PYTHONW=%~1"
exit /b 0

:launch
start "Aura" "%AURA_PYTHONW%" "%~dp0aura_app.py"
exit /b 0
