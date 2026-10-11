@echo off
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
  python -m venv venv
  if errorlevel 1 (
    pause
    exit /b 1
  )
)

venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
  pause
  exit /b 1
)

if not exist ".env" (
  copy /Y ".env.example" ".env" >nul
  echo Add your GEMINI_API_KEY to .env, then run this file again.
  notepad .env
  pause
  exit /b 0
)

venv\Scripts\python.exe -m streamlit run app.py
endlocal
