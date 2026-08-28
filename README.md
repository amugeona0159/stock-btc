# market-lens

차트를 읽고, 지표로 분석하고, 이후 양상을 예측하는 도구. 암호화폐·미국주식·국내주식을
같은 화면에서 본다.

![차트](screenshots/chart-wide.png)

## 빠르게 띄우기

키가 없어도 바로 돈다 — 기본값인 Binance 는 인증이 필요 없다.

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"        # Linux/macOS 는 .venv/bin/pip
.venv/Scripts/python -m uvicorn marketlens.api.app:app --app-dir server --port 8000

cd web && npm install && npm run dev          # http://localhost:5173
```

`.env.example` 를 `.env` 로 복사해 키를 넣으면 미국주식·국내주식이 켜진다. 키가 없는
프로바이더는 목록에 '비활성'으로 남고 앱은 정상 기동한다.

## 무엇이 들어 있나

- **지표 35종** — 일목균형표, 피셔 변환, 피보나치 되돌림, MACD, ADX/DMI, 슈퍼트렌드,
  파라볼릭 SAR, 볼린저·켈트너·돈치안, TTM 스퀴즈, RSI·스토캐스틱·CCI·MFI·TSI,
  OBV·VWAP·CMF·거래량 프로파일, 피벗 4종, 지지·저항 군집, 캔들 패턴 15종
- **시그널 엔진** — 14개 규칙의 가중 투표. 판단마다 근거 문장이 붙는다
- **예측 3층** — 규칙 / 통계적 구간(ATR·부트스트랩) / ML 방향성 분류
- **백테스트** — 확정봉 이벤트 기반, 수수료·슬리피지, 승률·PF·MDD·샤프
- **실시간** — 웹소켓 팬아웃. 브라우저 탭이 열 개여도 거래소 연결은 하나

## 데이터 소스

| 시장 | 실시간 | 과거 캔들 | 키 |
|---|---|---|---|
| 암호화폐 | Binance kline WS · Upbit trade WS | Binance `/klines` · Upbit `/candles` | 불필요 |
| 미국주식 | Finnhub trade WS | Stooq CSV (일·주봉) | Finnhub 무료 가입 |
| 국내주식 | KIS `H0STCNT0` | KIS 일봉 TR + 분봉 TR | 증권계좌 + 앱키 |

미국주식은 **히스토리와 실시간의 출처가 다르다** — Finnhub 무료 티어가 과거 캔들을
403 으로 막기 때문이다. 그 이음매는 `providers/composite.py` 한 곳에만 있다.

## 설계 규칙

이 프로젝트를 고칠 때 지켜야 하는 것들은 [CLAUDE.md](CLAUDE.md) 에 있다. 요약하면:

1. 공식은 코드가 아니라 데이터다 — 지표 목록은 `indicators/catalog.py` 한 벌
2. 캔들 스키마도 한 벌 — 거래소별 차이는 프로바이더 안에서 끝난다
3. 미확정 봉으로 시그널을 확정하지 않는다 (리페인팅)
4. 지표 계산은 서버에서만
5. 프로바이더는 `history` / `stream` 두 메서드뿐
6. 예측 3층은 출력 형태가 같다

## 검증

```bash
.venv/Scripts/python -m pytest server/tests -q      # 147개
cd web && npx tsc -b                                # 타입체크
cd web && npm run shot                              # 실제 화면 PNG (서버 2개가 떠 있어야 함)
```

`screenshots/` 의 PNG 를 눈으로 확인할 것. 일목 구름이 마지막 봉보다 26봉 앞까지
뻗는지, 원화 위의 글자가 읽히는지는 테스트로 안 잡힌다.

## 아직 없는 것

- 자동매매·주문 (범위 밖. KIS 는 시세 조회 TR 만 쓴다)
- 미국주식 분봉 히스토리 — 무료로 주는 데가 없다
- 구름대의 면적 채우기 — lightweight-charts v4 는 두 선 사이를 칠하지 못해
  선 두 개로 그린다
- ML 층은 `pip install -e ".[ml]"` 로 scikit-learn 을 깔아야 켜진다

## 라이선스와 출처

차트는 [lightweight-charts](https://github.com/tradingview/lightweight-charts) (Apache-2.0,
TradingView). 지표 원전은 각 스펙의 `source` 필드에 적혀 있고 `/api/indicators` 로 나간다.

**투자 판단에 쓰지 말 것.** 여기 나오는 예측은 과거 가격의 통계일 뿐이다.
