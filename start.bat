@echo off
cd /d D:\NanTuPy

echo [1/3] Killing existing uvicorn on port 5000...
for /f "tokens=5" %%a in ('netstat -ano ^| find ":5000 "') do (
    echo   Found PID %%a, killing...
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 2 /nobreak >nul

echo [2/3] Activating venv...
call venv\Scripts\activate.bat

echo [3/3] Starting uvicorn...
uvicorn app.main:app --host 0.0.0.0 --port 5000

pause
