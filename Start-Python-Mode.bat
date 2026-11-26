@echo off
setlocal
set "APP=%~dp0novel_reader.pyw"

if not exist "%APP%" goto no_app

where pythonw >nul 2>&1
if not errorlevel 1 goto run

where pyw >nul 2>&1
if not errorlevel 1 goto run_launcher

goto no_python

:no_app
echo.
echo [ERROR] novel_reader.pyw not found next to this file.
echo Please keep this bat file in the same folder as the app.
echo.
pause
exit /b 1

:run
start "" pythonw "%APP%"
exit /b 0

:run_launcher
start "" pyw "%APP%"
exit /b 0

:no_python
echo.
echo Python was not found on this computer.
echo Opening the install guide (Install-Python.txt) ...
echo.
echo Tip: if you do not want to install Python, use the
echo exe version in this folder instead (no install needed).
echo.
start "" notepad "%~dp0Install-Python.txt"
pause
exit /b 1
