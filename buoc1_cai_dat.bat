@echo off
chcp 65001 >nul
REM BUOC 1: cai moi truong Python + tai InfluxDB 2.9.1 (ban chinh thuc tu download.influxdata.com)
cd /d %~dp0
if not exist logs mkdir logs
set LOG=logs\buoc1.log
echo ==== %date% %time% ==== > %LOG%

set PY=python
where python >nul 2>&1 || set PY=py -3
%PY% --version >> %LOG% 2>&1
if errorlevel 1 (echo [ERR] Khong tim thay Python >> %LOG% & echo Khong tim thay Python & pause & exit /b 1)

if not exist .venv\Scripts\python.exe (
  echo Tao .venv ... & %PY% -m venv .venv >> %LOG% 2>&1
)
echo Cai thu vien Python (vai phut) ...
.venv\Scripts\python.exe -m pip install --upgrade pip >> %LOG% 2>&1
.venv\Scripts\python.exe -m pip install -r requirements.txt >> %LOG% 2>&1
if errorlevel 1 (echo [ERR] pip install loi >> %LOG%) else (echo [OK] pip install >> %LOG%)
if not exist .env copy .env.example .env >nul

if not exist influxdb\influxd.exe (
  echo Tai InfluxDB ...
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue';" ^
    "Invoke-WebRequest -Uri 'https://download.influxdata.com/influxdb/releases/influxdb2-2.9.1-windows_amd64.zip' -OutFile 'influxdb.zip';" ^
    "Expand-Archive -Path 'influxdb.zip' -DestinationPath 'influxdb_tmp' -Force;" ^
    "New-Item -ItemType Directory -Force -Path 'influxdb' | Out-Null;" ^
    "Get-ChildItem 'influxdb_tmp' -Recurse -File | Move-Item -Destination 'influxdb' -Force;" ^
    "Remove-Item 'influxdb_tmp','influxdb.zip' -Recurse -Force" >> %LOG% 2>&1
)
if exist influxdb\influxd.exe (echo [OK] influxd.exe >> %LOG%) else (echo [ERR] Khong tai duoc InfluxDB >> %LOG%)
echo ==== XONG ==== >> %LOG%
echo Xong buoc 1. Xem logs\buoc1.log
timeout /t 5
