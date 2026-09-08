@echo off
chcp 65001 >nul
title ChinaSide Welcome Bot
cd /d "%~dp0"
:loop
echo [%date% %time%] Pornesc botul...
"C:\Python312\python.exe" bot.py
if errorlevel 3 (
  echo [%date% %time%] Ruleaza deja o alta instanta. Nu repornesc.
  goto end
)
echo [%date% %time%] Botul s-a oprit. Repornesc in 15 secunde...
timeout /t 15 /nobreak >nul
goto loop
:end
