@echo off
REM Chạy thử KHÔNG cần InfluxDB và KHÔNG cần Wokwi: thiết bị giả lập + collector dry-run + app demo.
cd /d %~dp0
start "SIMULATOR" cmd /k ".venv\Scripts\activate && python -m tools.simulator --devices 2 --interval 2"
start "COLLECTOR (dry-run)" cmd /k ".venv\Scripts\activate && python -m gateway.collector --dry-run"
start "PREPROCESS (demo)" cmd /k ".venv\Scripts\activate && timeout /t 60 && python -m preprocessing.preprocess --demo --start 1h --rule 10s --loop 30"
start "APP (demo)" cmd /k ".venv\Scripts\activate && streamlit run app/dashboard.py -- --demo"
