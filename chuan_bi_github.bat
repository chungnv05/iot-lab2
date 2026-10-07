@echo off
chcp 65001 >nul
REM Tao kho git cuc bo va commit toan bo ma nguon (bo qua cac file trong .gitignore)
cd /d %~dp0
> logs\git_prepare.log 2>&1 (
  if not exist .git git init -b main
  for /f "delims=" %%a in ('git config user.name') do set GN=%%a
  if not defined GN git config user.name "Nguyen Duy Tu"
  for /f "delims=" %%a in ('git config user.email') do set GE=%%a
  if not defined GE git config user.email "b23dcat316@users.noreply.github.com"
  git add -A
  git commit -m "Bai thuc hanh 2 IoT: thu thap MQTT, luu tru InfluxDB, tien xu ly, dashboard Streamlit"
  echo ===== FILES IN COMMIT =====
  git ls-files
  echo ===== STATUS =====
  git status --short
  git log --oneline -3
)
