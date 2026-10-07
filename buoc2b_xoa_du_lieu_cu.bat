@echo off
REM Xoa du lieu thu nghiem cu trong InfluxDB (giu bucket). InfluxDB phai dang chay.
cd /d %~dp0
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe -m scripts.reset_data > logs\reset.log 2>&1
del /q logs\collector.log logs\preprocess.log logs\buffer.jsonl logs\rejected.jsonl 2>nul
type logs\reset.log
timeout /t 3
