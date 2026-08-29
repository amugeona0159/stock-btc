@echo off
REM 아침 매수 추천. 작업 스케줄러가 07:30 에 이걸 부른다.
REM 06:00 학습 작업과 **따로 둔다** — 거기 붙이면 실제 실행이 08시 언저리에서
REM 매일 흔들리고, 앞 단계가 넘치면 아예 안 돈다. "07:30 고정"은 벽시계 보장이다.
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
echo(>> "logs\recommend.log"
echo ===== %DATE% %TIME% START >> "logs\recommend.log"
REM 여기는 .env 가 있어 토스(국내·미국주식)까지 낸다. Actions 는 IP 제한으로 못 한다.
".venv\Scripts\python.exe" -u scripts\recommend.py --provider binance --provider upbit --provider yahoo --provider toss_kr --provider toss_us >> "logs\recommend.log" 2>&1
echo ===== %DATE% %TIME% END code=%ERRORLEVEL% >> "logs\recommend.log"
