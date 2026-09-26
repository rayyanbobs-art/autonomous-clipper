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
python app.py

pause
