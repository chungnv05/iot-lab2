@echo off
REM Commit va day cac thay doi moi len GitHub
cd /d %~dp0
> logs\git_push.log 2>&1 (
  git add -A
  git commit -m "Cap nhat bao cao: them link GitHub"
  git push
  git log --oneline -3
)
