@echo off
REM 아침 매수 추천. 작업 스케줄러가 07:30 에 이걸 부른다.
REM 06:00 학습 작업과 **따로 둔다** — 거기 붙이면 실제 실행이 08시 언저리에서
REM 매일 흔들리고, 앞 단계가 넘치면 아예 안 돈다. "07:30 고정"은 벽시계 보장이다.
cd /d "%~dp0.."
REM 저장소의 learning/ 은 Actions 가 쓴다. 여기는 옆자리에 쌓아 충돌을 만들지 않는다.
set MARKET_LENS_LEARNING=learning-local
if not exist "logs" mkdir "logs"
REM 여기는 .env 가 있어 토스(국내·미국주식)까지 낸다. Actions 는 IP 제한으로 못 한다.
".venv\Scripts\python.exe" scripts\recommend.py --provider binance --provider upbit --provider yahoo --provider toss_kr --provider toss_us >> "logs\recommend.log" 2>&1
