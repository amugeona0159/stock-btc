@echo off
REM Nightly self-learning on this PC. Task Scheduler runs this at 06:00 KST.
REM
REM ASCII ONLY. cmd.exe parses batch files in the OEM codepage (949) while this
REM repo writes UTF-8, so Korean comments are read as garbage -- and a garbled
REM fragment can end up being run as a command. It happened twice. The reasons
REM behind every line below are in CLAUDE.md, which is where they get read.
cd /d "%~dp0.."
set MARKET_LENS_LEARNING=learning-local
if not exist "logs" mkdir "logs"
REM Did the previous run finish? Must run BEFORE this run stamps its own START.
".venv\Scripts\python.exe" -u scripts\runcheck.py "logs\daily.log" market-lens-daily >> "logs\daily.log" 2>&1
echo(>> "logs\daily.log"
echo ===== %DATE% %TIME% START >> "logs\daily.log"
REM -u: a killed run must still leave what it managed to print.
".venv\Scripts\python.exe" -u scripts\daily.py --budget %1 >> "logs\daily.log" 2>&1
".venv\Scripts\python.exe" -u scripts\screen.py --horizons 1 2 3 --provider binance --provider yahoo --provider toss_kr >> "logs\screen.log" 2>&1
".venv\Scripts\python.exe" -u scripts\study.py --hours 1.0 --origins 24 >> "logs\study.log" 2>&1
echo ===== %DATE% %TIME% END code=%ERRORLEVEL% >> "logs\daily.log"
