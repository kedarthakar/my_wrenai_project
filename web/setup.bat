@echo off
echo === Wren Data Intelligence Setup ===

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.11+ from https://python.org
    pause
    exit /b 1
)

:: Create venv
if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
)

:: Activate and install
echo Installing dependencies...
call .venv\Scripts\activate.bat
pip install --upgrade pip -q
pip install -r requirements.txt -q

:: Check ANTHROPIC_API_KEY
if not exist ".env" (
    echo.
    echo IMPORTANT: Create web\.env with your API key:
    echo   ANTHROPIC_API_KEY=sk-ant-...
    echo.
    echo Copying .env.example to .env ...
    copy .env.example .env >nul 2>&1
)

echo.
echo Setup complete. Run start.bat to launch.
pause
