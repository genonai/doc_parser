@echo off
REM Quick Start Script for Document Preprocessor API (Windows)

setlocal enabledelayedexpansion

echo.
echo 🚀 Document Preprocessor API - Quick Start
echo ==========================================
echo.

REM Check Python version
echo ✓ Checking Python version...
python --version >/dev/null 2>&1
if errorlevel 1 (
    echo ❌ Python not found. Please install Python 3.9 or later.
    pause
    exit /b 1
)

REM Create virtual environment
if not exist "venv" (
    echo ✓ Creating virtual environment...
    python -m venv venv
) else (
    echo ✓ Virtual environment already exists
)

REM Activate virtual environment
echo ✓ Activating virtual environment...
call venv\Scripts\activate

REM Upgrade pip
echo ✓ Upgrading pip...
python -m pip install --upgrade pip setuptools wheel -q

REM Install dependencies
echo ✓ Installing dependencies (this may take a few minutes)...
pip install -r requirements.txt -q

echo.
echo ==========================================
echo ✅ Setup complete!
echo ==========================================
echo.
echo 📝 Next steps:
echo 1. Start the server:
echo    cd genon\preprocessor\src
echo    python -m uvicorn intelligent_main:app --host 127.0.0.1 --port 7085 --reload
echo.
echo 2. In another terminal, test the API:
echo    curl http://127.0.0.1:7085/healthcheck
echo.
echo 3. Process a document:
echo    curl -X POST http://127.0.0.1:7085/upload/run ^
echo      -F "file=@document.pdf"
echo.
echo 📖 For more information, see README.md or USAGE.md
echo.
pause
