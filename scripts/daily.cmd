@echo off
REM 내 PC 에서 매일 도는 자동 학습. 작업 스케줄러가 이걸 부른다.
REM Actions 쪽과 다른 점 하나 — 여기는 .env 가 있어 토스(국내주식)까지 학습한다.
cd /d "%~dp0.."
REM 저장소의 learning/ 은 Actions 가 쓴다. 여기는 옆자리에 쌓아 충돌을 만들지 않는다.
set MARKET_LENS_LEARNING=learning-local
if not exist "logs" mkdir "logs"
REM **언제 돌았고 어떻게 끝났는지를 남긴다.** 작업 스케줄러는 결과 코드 하나만
REM 알려 주는데 그 기록(TaskScheduler/Operational)이 기본으로 꺼져 있어, 죽은
REM 실행은 어디에도 안 남는다. 파이썬을 -u 로 돌리는 것도 같은 이유다 — 버퍼가
REM 차기 전에 끊기면 몇 분을 돌고도 로그가 통째로 비어, 아침에 결과가 없는데
REM 왜 없는지 볼 데가 없다. 실제로 그렇게 이틀을 몰랐다.
REM 표시 줄은 **영문으로 적는다.** cmd.exe 의 echo 는 OEM 코드페이지로 쓰는데
REM 로그 나머지는 파이썬이 UTF-8 로 써서, 한글을 넣으면 한 파일에 두 인코딩이
REM 섞여 깨진다. 이 줄의 일은 기계가 읽을 표시지 사람에게 하는 말이 아니다.
echo(>> "logs\daily.log"
echo ===== %DATE% %TIME% START >> "logs\daily.log"
".venv\Scripts\python.exe" -u scripts\daily.py --budget %1 >> "logs\daily.log" 2>&1
REM 추천 팩터도 같은 주기로 다시 잰다. 국내주식은 여기서만 잴 수 있다.
".venv\Scripts\python.exe" -u scripts\screen.py --horizons 1 2 3 --provider binance --provider yahoo --provider toss_kr >> "logs\screen.log" 2>&1
REM 예측하고 채점하고 왜 틀렸는지 판다. 판이 쌓일수록 기권 규칙이 단단해진다.
REM 예산이 1시간인 이유: 07:30 아침 추천과 겹치면 안 된다. 판은 이어서 쌓인다.
".venv\Scripts\python.exe" -u scripts\study.py --hours 1.0 --origins 24 >> "logs\study.log" 2>&1
echo ===== %DATE% %TIME% END code=%ERRORLEVEL% >> "logs\daily.log"
