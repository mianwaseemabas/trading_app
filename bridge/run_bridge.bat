@echo off
REM Starts the MT5 bridge. Use Task Scheduler ("At log on", restart on failure) to keep it running.
cd /d %~dp0
:loop
python mt5_bridge.py
echo Bridge stopped, restarting in 10 seconds...
timeout /t 10 /nobreak >nul
goto loop
