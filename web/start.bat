@echo off
echo === Wren Data Intelligence ===

if not exist ".venv" (
    echo Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

:: Load .env
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        if not "%%A"=="" if not "%%A:~0,1%"=="#" set "%%A=%%B"
    )
)

if "%ANTHROPIC_API_KEY%"=="" (
    echo ERROR: ANTHROPIC_API_KEY not set. Edit web\.env and add your key.
    pause
    exit /b 1
)

echo Starting at http://localhost:8000
echo Press Ctrl+C to stop.

:: Open browser after short delay
start "" /b cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:8000"

cd /d "%~dp0"
uvicorn app:app --host 0.0.0.0 --port 8000
