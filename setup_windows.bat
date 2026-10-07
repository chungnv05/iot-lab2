@echo off
REM Cài môi trường Python lần đầu
cd /d %~dp0
python -m venv .venv
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
if not exist .env copy .env.example .env
echo.
echo Xong. Mo file .env de sua MQTT_GROUP neu can.
pause
