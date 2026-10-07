@echo off
chcp 65001 >nul
REM Mo lai app (neu da tat) va lay so lieu cho bao cao tren toan bo du lieu 3 gio gan nhat
cd /d %~dp0
set PYTHONIOENCODING=utf-8
start "APP" /min cmd /k ".venv\Scripts\python.exe -m streamlit run app/dashboard.py --server.headless true"
set LOG=logs\buoc5.log
echo ==== %date% %time% ==== > %LOG%
.venv\Scripts\python.exe -W ignore -m preprocessing.preprocess --start 3h >> %LOG% 2>&1
.venv\Scripts\python.exe -W ignore -m tools.latency_report --minutes 180 >> %LOG% 2>&1
echo ==== XONG ==== >> %LOG%
timeout /t 5
