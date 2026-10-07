@echo off
chcp 65001 >nul
REM BUOC 3: collector + tien xu ly (lap 60 s) + app Streamlit. InfluxDB phai dang chay (buoc 2).
cd /d %~dp0
set PYTHONIOENCODING=utf-8
start "COLLECTOR (log: logs\collector.log)" /min cmd /c ".venv\Scripts\python.exe -u -m gateway.collector >> logs\collector.log 2>&1"
start "PREPROCESS (log: logs\preprocess.log)" /min cmd /c ".venv\Scripts\python.exe -u -m preprocessing.preprocess --start 1h --loop 60 >> logs\preprocess.log 2>&1"
start "APP" /min cmd /k ".venv\Scripts\python.exe -m streamlit run app/dashboard.py --server.headless true"
timeout /t 8 >nul
start http://localhost:8501
