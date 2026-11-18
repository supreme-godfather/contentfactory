@echo off
setlocal
set "ROOT=%~dp0"
if not exist "%ROOT%logs" mkdir "%ROOT%logs"
cd /d "%ROOT%"
"%ROOT%.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 >> "%ROOT%logs\api.log" 2>&1
exit /b %ERRORLEVEL%
