@echo off
REM Dung collector, tien xu ly, app va InfluxDB
taskkill /fi "WINDOWTITLE eq COLLECTOR*" /t /f >nul 2>&1
taskkill /fi "WINDOWTITLE eq PREPROCESS*" /t /f >nul 2>&1
taskkill /fi "WINDOWTITLE eq APP*" /t /f >nul 2>&1
taskkill /im influxd.exe /f >nul 2>&1
echo Da dung.
timeout /t 3
