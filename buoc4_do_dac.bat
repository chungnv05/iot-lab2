@echo off
chcp 65001 >nul
REM BUOC 4: lay so lieu cho bao cao (chay sau khi he thong da chay >= 20-30 phut)
cd /d %~dp0
set LOG=logs\buoc4.log
echo ==== %date% %time% ==== > %LOG%
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe -m preprocessing.preprocess --start 2h >> %LOG% 2>&1
.venv\Scripts\python.exe -m tools.latency_report --minutes 120 >> %LOG% 2>&1
.venv\Scripts\python.exe -m tools.benchmark_storage --points 5000 >> %LOG% 2>&1
.venv\Scripts\python.exe -m pytest -q >> %LOG% 2>&1
echo ==== XONG ==== >> %LOG%
type %LOG%
timeout /t 10
