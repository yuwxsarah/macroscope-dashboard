@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3.11 or newer first.
  pause
  exit /b 1
)
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -c "import pandas,numpy,plotly,akshare,yfinance,requests,yaml,tenacity,jinja2,bs4,openpyxl,xlrd,efinance" >nul 2>nul
if errorlevel 1 (
  echo Preparing the local data environment. This is only slow on the first run...
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
)
python -c "import pandas,numpy,plotly,akshare,yfinance,requests,yaml,tenacity,jinja2,bs4,openpyxl,xlrd,efinance" >nul 2>nul
if errorlevel 1 (
  echo Failed to prepare Python packages. Check the network and run this file again.
  pause
  exit /b 1
)
python scripts\local_server.py --open-browser
if errorlevel 1 (
  echo The local website could not start. Another program may already be using port 8000.
  pause
)
