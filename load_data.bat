@echo off
if "%1"=="" (
    echo Usage: load_data.bat path\to\ookla_data.csv
    pause
    exit /b 1
)
call web\.venv\Scripts\activate.bat 2>nul || call web\.venv\Scripts\activate 2>nul
python load_data.py %1
pause
