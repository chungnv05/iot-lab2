@echo off
chcp 65001 >nul
REM BUOC 2: chay InfluxDB (chi nghe tren 127.0.0.1) + tao org/token/bucket/retention
cd /d %~dp0
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
set LOG=logs\buoc2.log
echo ==== %date% %time% ==== > %LOG%
tasklist /fi "imagename eq influxd.exe" | find /i "influxd.exe" >nul || start "INFLUXDB - giu cua so nay mo" /min influxdb\influxd.exe --http-bind-address 127.0.0.1:8086
echo Doi InfluxDB khoi dong ...
timeout /t 10 >nul
.venv\Scripts\python.exe -m scripts.setup_influxdb >> %LOG% 2>&1
type %LOG%
timeout /t 5
