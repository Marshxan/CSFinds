@echo off
chcp 65001 >nul
echo Opresc botul de welcome...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*start-bot.bat*' -or $_.CommandLine -like '*launch-hidden.vbs*' -or ($_.Name -in 'python.exe','pythonw.exe' -and $_.CommandLine -like '*bot.py*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo Gata. Toate instantele au fost oprite.
timeout /t 3 /nobreak >nul
