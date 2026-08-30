@echo off
REM Morning buy recommendation. Task Scheduler runs this at 07:30 KST.
REM
REM ASCII ONLY. cmd.exe parses batch files in the OEM codepage (949) while this
REM repo writes UTF-8, so Korean comments are read as garbage -- and a garbled
REM fragment can end up being run as a command. It happened twice. The reasons
REM behind every line below are in CLAUDE.md, which is where they get read.
cd /d "%~dp0.."
set MARKET_LENS_LEARNING=learning-local
if not exist "logs" mkdir "logs"
REM Did the previous run finish? Must run BEFORE this run stamps its own START.
".venv\Scripts\python.exe" -u scripts\runcheck.py "logs\recommend.log" market-lens-recommend >> "logs\recommend.log" 2>&1
echo(>> "logs\recommend.log"
echo ===== %DATE% %TIME% START >> "logs\recommend.log"
REM -u: a killed run must still leave what it managed to print.
".venv\Scripts\python.exe" -u scripts\recommend.py --provider binance --provider upbit --provider yahoo --provider toss_kr --provider toss_us >> "logs\recommend.log" 2>&1
echo ===== %DATE% %TIME% END code=%ERRORLEVEL% >> "logs\recommend.log"
