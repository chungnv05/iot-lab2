@echo off
cd /d %~dp0
(where git ^& git --version ^& echo name=^& git config --global user.name ^& echo email=^& git config --global user.email) > logs\git_check.log 2>&1
