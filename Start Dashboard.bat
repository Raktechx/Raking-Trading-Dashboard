@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
where py >nul 2>nul
if %errorlevel%==0 set "PY=py -3"
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY goto nopython

if exist ".venv\Scripts\python.exe" goto havevenv
echo First-time setup - this takes 2-3 minutes, please keep this window open...
%PY% -m venv .venv
if errorlevel 1 goto nopython
:havevenv

fc /b requirements.txt ".venv\req.done" >nul 2>nul
if not errorlevel 1 goto depsok
echo Installing required packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt -q
if errorlevel 1 goto installfail
copy /y requirements.txt ".venv\req.done" >nul
:depsok

if not exist ".env" copy ".env.example" ".env" >nul
if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
  echo [general]> "%USERPROFILE%\.streamlit\credentials.toml"
  echo email = "">> "%USERPROFILE%\.streamlit\credentials.toml"
)

title Raking the Trader
echo.
echo  Starting Raking the Trader... your browser will open automatically.
echo  To stop the dashboard, close this window.
echo.
".venv\Scripts\python.exe" -m streamlit run app.py
pause

goto end

:nopython
echo.
echo Python not found. Install Python 3.11 or newer from python.org/downloads.
echo During install tick "Add python.exe to PATH". Then run this file again.
pause
exit /b 1

:installfail
echo.
echo Package install failed. Check your internet connection and run this file again.
pause
exit /b 1

:end
