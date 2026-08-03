@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 exit /b 1
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -c "import pandas,numpy,plotly,akshare,yfinance,requests,yaml,tenacity,jinja2,bs4,openpyxl,xlrd,efinance" >nul 2>nul
if errorlevel 1 python -m pip install -r requirements.txt
python scripts\local_server.py --refresh-only
exit /b %errorlevel%
