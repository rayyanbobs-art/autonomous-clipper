@echo off
title AutoShorts AI
echo ======================================================
echo           Starting AutoShorts AI Launcher...
echo ======================================================
echo.
echo Opening your web browser at http://127.0.0.1:5000 ...
echo Keep this window open while using the app.
echo.

cd /d "%~dp0"

:: Auto-setup Hyperframes dependencies on first run
if not exist "hyperframes_studio\node_modules\hyperframes" (
    echo [Setup] First-time setup detected. Pre-caching Hyperframes studio...
    where bun >nul 2>nul
    if %errorlevel% equ 0 (
        echo [Setup] Running bun install for Hyperframes...
        cd hyperframes_studio && call bun install && cd ..
    ) else (
        where npm >nul 2>nul
        if %errorlevel% equ 0 (
            echo [Setup] Running npm install for Hyperframes...
            cd hyperframes_studio && call npm install --no-audit --no-fund && cd ..
        )
    )
)

echo Opening your web browser at http://127.0.0.1:5000 ...
echo Keep this window open while using the app.
echo.

:: Locate Python executable with dependencies
set "PYTHON_EXE="
if exist "%LOCALAPPDATA%\Python\bin\python.exe" (
    set "PYTHON_EXE=%LOCALAPPDATA%\Python\bin\python.exe"
) else (
    where python >nul 2>nul
    if %errorlevel% equ 0 (
        set "PYTHON_EXE=python"
    ) else (
        where py >nul 2>nul
        if %errorlevel% equ 0 (
            set "PYTHON_EXE=py"
        )
    )
)

if "%PYTHON_EXE%"=="" (
    echo [Error] Python executable not found!
    echo Please ensure Python 3.10+ is installed and on system PATH.
    pause
    exit /b 1
)

"%PYTHON_EXE%" app.py

pause
