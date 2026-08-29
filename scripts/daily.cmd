@echo off
REM 내 PC 에서 매일 도는 자동 학습. 작업 스케줄러가 이걸 부른다.
REM Actions 쪽과 다른 점 하나 — 여기는 .env 가 있어 토스(국내주식)까지 학습한다.
cd /d "%~dp0.."
REM 저장소의 learning/ 은 Actions 가 쓴다. 여기는 옆자리에 쌓아 충돌을 만들지 않는다.
set MARKET_LENS_LEARNING=learning-local
if not exist "logs" mkdir "logs"
".venv\Scripts\python.exe" scripts\daily.py --budget %1 >> "logs\daily.log" 2>&1
REM 추천 팩터도 같은 주기로 다시 잰다. 국내주식은 여기서만 잴 수 있다.
".venv\Scripts\python.exe" scripts\screen.py --horizons 1 2 3 --provider binance --provider yahoo --provider toss_kr >> "logs\screen.log" 2>&1
REM 예측하고 채점하고 왜 틀렸는지 판다. 판이 쌓일수록 기권 규칙이 단단해진다.
REM 예산이 1시간인 이유: 07:30 아침 추천과 겹치면 안 된다. 판은 이어서 쌓인다.
".venv\Scripts\python.exe" scripts\study.py --hours 1.0 --origins 24 >> "logs\study.log" 2>&1
