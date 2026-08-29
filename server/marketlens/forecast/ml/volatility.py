"""봉의 고가·저가로 재는 변동성. **재 봤고, 학습에는 안 넣었다.**

> **결론부터.** 이 여덟 축을 학습 표에 얹어 `scripts/sweep.py --daily` 로 쟀더니
> 일봉 10칸에서 **평균 +0.0003** 이었다(이긴 칸 7/10, 제일 큰 칸 +0.0012).
> 채택 기준선은 +0.002 다 — **못 넘었다.** 그래서 `dataset.FEATURE_COLUMNS` 에
> 넣지 않았다. 여기 남아 있는 건 재는 장치이지 도는 코드가 아니다.
>
> **왜 안 됐나.** 목표값을 이미 **ATR 로 나눠서** 학습한다
> (`model.volatility_scale` = ATR ÷ 종가). 그리고 ATR 의 재료인 True Range 가
> **고가·저가로 만들어진다.** 즉 이 저장소는 범위 기반 변동성을 이미 쓰고 있었고,
> 그것도 제일 센 자리인 정규화 상수로 쓰고 있었다. 추정량을 넷 더 얹어 봐야
> 같은 이야기를 다시 하는 셈이다.
>
> **그럼 자를 바꿔 보면? 그것도 재 봤고, 더 나빴다.** 목표를 나누는 자를 ATR 대신
> Garman–Klass·Parkinson 으로 갈아 봤다(`sweep.py --scale`). 일봉 8칸에서 ATR 이
> **7칸을 이겼다** — 범위 추정량 쪽이 0.1~0.6% 더 나쁜 손실이었다. 문헌의 효율
> 우위(4.9~7.4배)는 **σ 를 정확히 재는 문제**의 이야기고, 여기서 필요한 건
> "내일 수익률의 분위수" 라 같은 문제가 아니다. 게다가 ATR 은 14봉 RMA 라 이미
> 부드럽고, True Range 는 갭까지 삼킨다.
>
> **그래서 이 방향은 두 번 재고 두 번 다 졌다.** 다시 열려면 새 근거가 있어야 한다 —
> "이론상 더 효율적이니까" 는 이미 써 본 논거다. 코드는 재는 장치로 남겨 둔다
> (`sweep.py` 의 `+ 범위변동성` 과 `--scale`). 지우면 다음 사람이 같은 걸 처음부터
> 만들고 같은 결론에 다시 도착한다.

아래는 그 측정에서 실제로 확인된 것들이다.

지금 이 저장소가 변동성을 재는 방법은 둘뿐이다 — 종가끼리의 표준편차(`ret_vol_20`)와
ATR(`atr_pct`). **둘 다 봉 안에서 벌어진 일을 거의 안 본다.** 종가가 제자리로 돌아온
날은 종가 표준편차가 0 이라고 말하지만, 그 안에서 5% 를 오르내렸을 수 있다.

고가·저가를 같이 쓰면 같은 봉 수로 훨씬 정확한 변동성이 나온다. 이 저장소에서
**밴드(80% 구간)는 실제로 맞는 쪽이다**(27,664판에서 82.2%). 방향(55.0%)이 아니라
이쪽을 먼저 손보는 이유다.

## 추정량 넷 (출처)

- **Parkinson (1980)** — 고가·저가만. 종가 표준편차보다 이론상 **4.9배** 효율적이다
  (해석적으로 `2 / ((9ζ(3) − 16ln²2)/(16ln²2))` = 4.910. 흔히 도는 "5.2배" 는 1차
  출처를 못 찾았다). 대신 **시가 갭을 못 본다** — 장 열리기 전에 벌어진 일은 고저
  범위 밖이다. 거꾸로, 시가를 안 쓰므로 시가가 지저분한 시장에서는 이것만 멀쩡하다.
  Parkinson, M. "The Extreme Value Method for Estimating the Variance of the Rate
  of Return." *Journal of Business* 53(1), 1980, 61–65. doi:10.1086/296071
- **Garman–Klass (1980)** — 고저에 시가·종가를 더한다. 효율 **7.4배**인데 그 분모가
  종가끼리가 아니라 **`(종가−시가)²`** 다. GK 는 갭이 없다고 가정하므로 그 세계에서만
  둘이 같다 — **갭이 있는 주식에서 "종가 대비 7.4배" 는 성립하지 않는다.**
  추세가 있으면 위로 부푼다(재 보니 추세 6%/일에서 +45%).
  Garman, M. B., & Klass, M. J. "On the Estimation of Security Price Volatilities
  from Historical Data." *Journal of Business* 53(1), 1980, 67–78. doi:10.1086/296072
- **Rogers–Satchell (1991)** — **추세와 무관하다.** 위 둘이 못 하는 것이다.
  대신 시가 갭은 여전히 못 본다.
  Rogers, L. C. G., & Satchell, S. E. "Estimating Variance From High, Low and
  Closing Prices." *Annals of Applied Probability* 1(4), 1991, 504–512.
  doi:10.1214/aoap/1177005835
- **Yang–Zhang (2000)** — 갭 + 장중 + Rogers–Satchell 을 합친다. 추세와 무관하고
  갭도 본다. 전형적 효율은 **7.3배**다(흔히 인용되는 "14배" 는 `n=2` 라는 극단에서만
  나오는 값이다). 갭이 변동성을 지배하면 효율이 1 로 떨어져 종가 추정량과 같아진다.
  **혼자만 다기간 추정량이다** — 논문이 "추세와 갭 양쪽에 무관한 단일기간 추정량은
  존재할 수 없다" 를 증명해 두었다. 그래서 창(`window`)이 정의의 일부고, 창 길이가
  곧 `k` 를 정한다. 봉마다 나오는 P·GK·RS 와는 축이 다르다.
  Yang, D., & Zhang, Q. "Drift-Independent Volatility Estimation Based on High,
  Low, Open, and Close Prices." *Journal of Business* 73(3), 2000, 477–491.

**암호화폐에서는 갭 항이 0 이다.** 24시간 도는 시장이라 시가가 전 봉 종가와 같다.
`overnight_share` 가 그 사실을 축 하나로 들고 간다(재 보니 갭 1% 인 주식 0.27,
암호화폐 0.00). 모델이 시장을 가르는 데 쓸 수 있다.

**그렇다고 Yang–Zhang 이 못 쓰게 되는 게 아니다.** 갭이 0 이면 YZ 는
`k·(종가분산) + (1−k)·RS` 가 되는데, 논문이 `k` 는 갭 비중과 **무관하다**고 못박아
두었으므로 불편성도 최소분산성도 그대로다. 오히려 논문은 이 경우를 따로 다루면서
**"종가 추정량 단독도 RS 단독도 최소분산이 아니다"** 라고 쓴다 — 그러니
"암호화폐면 RS 만 쓰면 된다"는 흔한 조언은 논문과 정면으로 어긋난다.

암호화폐의 진짜 문제는 다른 데 있다. 거기엔 시가 단일가매매가 없어서 `O_t` 는
자정 직후에 찍힌 아무 체결가다. 즉 갭 항에 신호가 아니라 **호가 튐(마이크로구조 잡음)**
이 들어간다. 시가를 쓰는 GK·RS·YZ 가 다 같이 영향을 받고, Parkinson 만 면역이다
(식에서 시가가 소거된다). 진단은 간단하다 — `overnight_share` 를 찍어 보고
**0 근처가 아니면 추정량이 아니라 봉을 만드는 방식이 잘못된 것이다.**

## 직접 재 본 것 (`test_volatility.py` 가 고정한다)

봉 안을 실제로 걸어 다니는 모의 가격으로 재 봤다. 진짜 변동성 2%/일:

| 하루 추세 | 종가std | Parkinson | GK | RS | YZ |
|---|---|---|---|---|---|
| 0%  | 0.0191 | 0.0191 | 0.0189 | 0.0190 | 0.0191 |
| +6% | 0.0197 | 0.0408 | 0.0275 | 0.0175 | 0.0178 |

- **효율은 논문 그대로다.** 같은 20봉으로 잰 추정치의 분산이 종가 표준편차의 **0.27배**
  (0.0062 대 0.0226). 범위를 쓰면 같은 봉 수로 3~4배 정확하다는 게 이 하나다.
- **추세가 세면 Parkinson 은 두 배로 부푼다.** 쭉 오른 것을 흔들린 것으로 읽는다.
  GK 는 +45%, RS·YZ 는 −8% 로 거의 안 흔들린다 — 논문이 말한 그대로다.
- **다만 RS 는 봉이 성길 때 반대로 무너진다.** 봉 안을 24조각으로만 보면 추세 6% 에서
  0.0190 → 0.0040 으로 주저앉는다(390조각이면 0.0175). 고가·저가가 진짜 극단을 못 잡으면
  RS 의 두 항이 같이 0 으로 가기 때문이다. **거래가 뜸한 종목의 일봉이 정확히 이 자리다** —
  그래서 넷을 다 넣고 하나만 고르지 않는다. 어느 쪽으로 틀리는지가 서로 반대라
  모델이 둘을 같이 보면 상황을 가릴 수 있다.

## HAR (Corsi 2009)

변동성은 **어제·지난주·지난달이 각각 따로 남는다.** 하나의 창으로 재면 그 층이
뭉개진다. 세 창(1·5·22)의 비를 넣어 "지금 변동성이 오르는 중인가 내리는 중인가" 를
축으로 만든다.
  Corsi, F. "A Simple Approximate Long-Memory Model of Realized Volatility."
  *Journal of Financial Econometrics* 7(2), 2009, 174–196. doi:10.1093/jjfinec/nbp001

## 전부 **비율**로 낸다

모델은 BTC 와 삼성전자를 한 표에 놓고 배운다. 변동성의 절대 크기를 넣으면 그건
"어느 종목이냐" 를 말할 뿐 오늘에 대해 아무 말도 안 한다. 그래서 전부 종가 표준편차
대비 비나 창끼리의 비로 낸다 — `atr_pct` 가 이미 크기를 들고 있다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RANGE_COLUMNS: tuple[str, ...] = (
    "parkinson_ratio", "gk_ratio", "rs_ratio", "yz_ratio",
    "overnight_share", "har_short", "har_long", "range_pressure",
)

WINDOW = 20          # 추정 창. 20봉이면 일봉 한 달, 시간봉 하루 남짓이다.
_LN2 = float(np.log(2.0))
_EPS = 1e-12


def _log(a: pd.Series, b: pd.Series) -> pd.Series:
    """ln(a/b). 0 이나 음수 가격은 NaN 으로 흘린다 — 채우면 없던 값이 생긴다."""
    ratio = a.astype("float64") / b.astype("float64").replace(0.0, np.nan)
    return np.log(ratio.where(ratio > 0.0))


_FLOOR = float(np.exp(-6.0))     # 비가 이보다 작으면 여기서 멈춘다. e^-6 ≈ 0.0025


def _log_ratio(top: pd.Series, bottom: pd.Series) -> pd.Series:
    """ln(top/bottom). 양끝을 ±6 에서 자른다.

    분산의 비라 한쪽이 0 이면 무한대가 된다. 자르지 않으면 그 행이 학습에서 통째로
    빠지고, 트리는 살아남은 극단값 하나에 가지를 만든다.
    """
    ratio = (top / bottom.where(lambda s: s > 0.0)).clip(_FLOOR, 1.0 / _FLOOR)
    return np.log(ratio)


def parkinson(df: pd.DataFrame, window: int = WINDOW) -> pd.Series:
    """고가·저가만. Parkinson (1980)."""
    hl = _log(df["high"], df["low"]) ** 2
    return np.sqrt(hl.rolling(window).mean() / (4.0 * _LN2))


def garman_klass(df: pd.DataFrame, window: int = WINDOW) -> pd.Series:
    """고저 + 시종. Garman–Klass (1980).

    음수가 나올 수 있다 — 몸통이 범위보다 크게 잡히는 봉에서. 그때는 0 으로 자른다.
    분산이 음수라는 말은 뜻이 없고, 루트를 씌우면 NaN 이 되어 그 행이 학습에서 빠진다.
    """
    hl = _log(df["high"], df["low"]) ** 2
    co = _log(df["close"], df["open"]) ** 2
    var = 0.5 * hl - (2.0 * _LN2 - 1.0) * co
    return np.sqrt(var.rolling(window).mean().clip(lower=0.0))


def rogers_satchell(df: pd.DataFrame, window: int = WINDOW) -> pd.Series:
    """추세와 무관한 추정량. Rogers–Satchell (1991).

    추세 6%/일 에서 Parkinson 이 두 배로 부풀 때 이건 −8% 로 버틴다.

    **대신 봉이 성기면 이쪽이 무너진다.** 고가·저가가 진짜 극단을 못 잡으면
    `ln(H/C)`·`ln(L/O)` 가 같이 0 으로 가서 변동성을 0 이라고 말한다.
    거래가 뜸한 종목의 일봉이 그 자리다 — Parkinson 과 **같이** 넣는 이유다.
    """
    hc, ho = _log(df["high"], df["close"]), _log(df["high"], df["open"])
    lc, lo = _log(df["low"], df["close"]), _log(df["low"], df["open"])
    var = hc * ho + lc * lo
    return np.sqrt(var.rolling(window).mean().clip(lower=0.0))


def yang_zhang(df: pd.DataFrame, window: int = WINDOW) -> pd.Series:
    """갭 + 장중 + Rogers–Satchell. Yang–Zhang (2000).

    k 는 논문의 값이다 — 갭과 장중 분산의 비중을 창 길이로 정한다.
    """
    overnight = _log(df["open"], df["close"].shift(1))
    intraday = _log(df["close"], df["open"])

    var_o = overnight.rolling(window).var(ddof=1)
    var_c = intraday.rolling(window).var(ddof=1)
    var_rs = rogers_satchell(df, window) ** 2

    k = 0.34 / (1.34 + (window + 1.0) / (window - 1.0))
    return np.sqrt((var_o + k * var_c + (1.0 - k) * var_rs).clip(lower=0.0))


def range_features(df: pd.DataFrame, window: int = WINDOW) -> pd.DataFrame:
    """봉 범위로 잰 변동성 축들. 전부 무차원 비다.

    인덱스는 입력과 1:1 이고, 창이 안 찬 앞부분은 NaN 이다 — 채우지 않는다.
    없는 값을 0 으로 채우면 "변동성이 없다"는 거짓말이 되고, 학습 표는 그 행을
    버릴 줄 안다.
    """
    out = pd.DataFrame(index=df.index, dtype="float64")
    close = df["close"].astype("float64")

    # 기준자: 종가끼리의 표준편차. 지금 모델이 아는 유일한 변동성이다.
    # 새 추정량이 이보다 크면 "종가는 제자리인데 봉 안에서 크게 흔들렸다" 는 뜻이다.
    cc = np.log(close / close.shift(1)).rolling(window).std(ddof=1)
    base = cc.where(cc > _EPS)

    pk, gk = parkinson(df, window), garman_klass(df, window)
    rs, yz = rogers_satchell(df, window), yang_zhang(df, window)

    # 1 근처가 정상. 1 보다 크면 장중 흔들림이 종가 움직임보다 크다는 뜻이라
    # 되돌림(mean reversion)이 잦은 구간이다.
    out["parkinson_ratio"] = pk / base
    out["gk_ratio"] = gk / base
    out["rs_ratio"] = rs / base
    out["yz_ratio"] = yz / base

    # 갭이 전체 변동성에서 차지하는 몫. **암호화폐는 0 근처, 주식은 0.2~0.4.**
    # 모델이 시장 종류를 가르는 데 쓸 수 있는 값이라 일부러 남긴다.
    overnight = _log(df["open"], df["close"].shift(1))
    var_o = overnight.rolling(window).var(ddof=1)
    total = (yz ** 2).where(lambda s: s > _EPS)
    out["overnight_share"] = (var_o / total).clip(0.0, 1.0)

    # HAR (Corsi 2009): 어제 / 지난주 / 지난달. 비로 넣어야 종목을 안 탄다.
    # Rogers–Satchell 을 실현변동성 대용으로 쓴다 — 추세를 안 타는 게 여기서 중요하다.
    # 움직이지 않은 봉의 실현변동성은 **진짜로 0 이다.** 결측이 아니라 값이다 —
    # NaN 으로 바꿔 두면 그 한 봉이 뒤따르는 22행의 이동평균까지 지운다(실제로 그랬다).
    rv = rogers_satchell(df, 1) ** 2
    day = rv
    week = rv.rolling(5, min_periods=3).mean()
    month = rv.rolling(22, min_periods=11).mean()
    # 분산의 비라 폭이 크다. 로그로 눌러 이상치가 트리를 끌고 가지 않게 한다.
    #
    # **바닥을 깐다.** 고가에서 마감하고 저가에서 시작한 봉은 RS 가 분산을 정확히 0 이라고
    # 말한다(위 함수의 사각지대다. 재 보니 0.7% 쯤 된다). `log(0)` 은 -inf 라 그 행이
    # 학습에서 빠지는데, 그건 '모른다' 가 아니라 '아주 조용하다' 이므로 버릴 값이 아니다.
    out["har_short"] = _log_ratio(day, week)
    out["har_long"] = _log_ratio(week, month)

    # 오늘 봉의 범위가 최근 평균 범위의 몇 배인가. 위 넷은 창 평균이라
    # **오늘 하루가 유별났다는 사실이 20으로 나뉘어 묻힌다.**
    span = _log(df["high"], df["low"])
    out["range_pressure"] = np.log(
        (span / span.rolling(window).mean().where(lambda s: s > _EPS))
        .where(lambda s: s > 0.0)
    )

    return out.replace([np.inf, -np.inf], np.nan)
