@echo off
REM Trading Bot Launcher for Windows
setlocal enabledelayedexpansion
set SCRIPT_DIR=%~dp0
set LOG_DIR=%SCRIPT_DIR%trading_logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
echo [%date% %time%] Starting Trading Bot >> "%LOG_DIR%\execution.log"
cd /d "%SCRIPT_DIR%"
python trading_bot_alpaca_integration.py >> "%LOG_DIR%\execution.log" 2>&1
echo [%date% %time%] Trading Bot Completed >> "%LOG_DIR%\execution.log"
endlocal
