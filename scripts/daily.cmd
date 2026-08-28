@echo off
REM 내 PC 에서 매일 도는 자동 학습. 작업 스케줄러가 이걸 부른다.
REM Actions 쪽과 다른 점 하나 — 여기는 .env 가 있어 토스(국내주식)까지 학습한다.
cd /d "%~dp0.."
REM 저장소의 learning/ 은 Actions 가 쓴다. 여기는 옆자리에 쌓아 충돌을 만들지 않는다.
set MARKET_LENS_LEARNING=learning-local
if not exist "logs" mkdir "logs"
".venv\Scripts\python.exe" scripts\daily.py --budget %1 >> "logs\daily.log" 2>&1
