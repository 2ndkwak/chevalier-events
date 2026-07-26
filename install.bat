@echo off
title Chevalier Events - Installation
echo.
echo  ============================================
echo   Chevalier Events - Installing...
echo  ============================================
echo.

REM Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: Python is not installed.
    echo.
    echo  Please install Python 3.10 or later from:
    echo  https://www.python.org/downloads/
    echo.
    echo  Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo  [1/3] Python found. Installing dependencies...
echo.
pip install flask flask-login flask-mail flask-sqlalchemy anthropic requests python-docx Pillow reportlab --quiet --disable-pip-version-check

if %errorlevel% neq 0 (
    echo.
    echo  ERROR: Failed to install dependencies.
    echo  Please check your internet connection and try again.
    pause
    exit /b 1
)

echo  [2/3] Dependencies installed.
echo.
echo  [3/3] Launching setup wizard...
echo.

cd /d "%~dp0"
python setup_wizard.py

echo.
echo  ============================================
echo   Setup complete!
echo   Use the Desktop shortcut to launch the app.
echo  ============================================
echo.
pause
