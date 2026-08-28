"""근거 표 — 이 프로그램이 기대고 있는 문헌 전부.

새 방법론을 넣을 때는 여기 항목을 먼저 쓴다. 항목 없이 들어간 방법론은
"어디선가 본 것" 이고, 그런 건 반년 뒤에 검증할 수 없다.

`limits` 를 성실하게 쓸 것. 예측 화면이 근거를 보여줄 때 한계까지 같이 나간다 —
그게 이 등록부의 존재 이유다.
"""
from __future__ import annotations

from .registry import Evidence, Source, add

# ---------------------------------------------------------------- 유사구간 예측

add(Evidence(
    key="analog_dtw",
    field="analog",
    claim="시계열의 모양이 비슷한 구간을 찾을 때, 시간축이 조금 밀리거나 늘어난 것을 "
          "허용하는 DTW 가 단순 유클리드 거리보다 같은 패턴을 잘 찾는다.",
    effect="1-NN + DTW 는 시계열 분류에서 오랫동안 이기기 어려운 기준선으로 쓰였다.",
    limits="느리다(창 길이의 제곱). 금융 데이터는 잡음이 커서 '모양이 비슷한 것'이 "
           "'이후가 비슷한 것'을 보장하지 않는다 — 거리만으로 고른 사례는 과신하면 안 된다.",
    confidence="strong",
    used_by=("analog/matcher.py: shape_distance",),
    sources=(
        Source("Using dynamic time warping to find patterns in time series",
               "Berndt, D. J. & Clifford, J.", 1994, "AAAI KDD Workshop"),
        Source("Matrix Profile I: All Pairs Similarity Joins for Time Series",
               "Yeh, C.-C. M. et al.", 2016, "IEEE ICDM"),
    ),
))

add(Evidence(
    key="analog_znorm",
    field="analog",
    claim="구간을 비교하기 전에 z-정규화해야 한다. 안 하면 가격 수준과 변동성이 다른 "
          "구간끼리는 모양이 같아도 멀게 나온다.",
    effect="모양 기반 검색의 사실상 표준 전처리다.",
    limits="정규화하면 '5만 달러에서의 3% 하락'과 '0.4달러에서의 3% 하락'이 같아진다. "
           "유동성이 다른 자산을 섞어 검색하면 그 차이가 지워진다.",
    confidence="strong",
    used_by=("analog/matcher.py: znorm",),
    sources=(
        Source("Matrix Profile I: All Pairs Similarity Joins for Time Series",
               "Yeh, C.-C. M. et al.", 2016, "IEEE ICDM"),
    ),
))

add(Evidence(
    key="analog_retrieval_forecast",
    field="analog",
    claim="과거의 유사 구간을 찾아 그 이후 경로를 예측에 쓰는 방식(검색 기반 예측)은 "
          "패턴이 실제로 반복되는 계열에서 통한다.",
    effect="검색으로 가져온 유사 사례를 예측 모형에 넣으면 성능이 개선된다는 보고가 있다.",
    limits="시장은 '패턴이 반복되는 계열'이라는 보장이 없다. 사례 수가 적으면 "
           "우연히 비슷한 구간을 뽑는다 — 이 프로그램이 사례 개수와 거리 분포를 "
           "같이 보여주는 이유다.",
    confidence="moderate",
    used_by=("analog/projection.py",),
    sources=(
        Source("Retrieval Augmented Time Series Forecasting",
               "Yang, K. et al.", 2024, "arXiv:2411.08249",
               "https://arxiv.org/abs/2411.08249"),
        Source("Forecasting Stock Time-Series using Data Approximation and "
               "Pattern Sequence Similarity",
               "Nguyen, T. & Nguyen, T.", 2013, "arXiv:1309.2517",
               "https://arxiv.org/abs/1309.2517"),
    ),
))

add(Evidence(
    key="technical_pattern_information",
    field="analog",
    claim="차트 패턴에 정보가 아예 없지는 않다 — 커널 회귀로 패턴을 자동 정의해 검정하면 "
          "일부 패턴에서 조건부 수익률 분포가 무조건부 분포와 통계적으로 다르다.",
    effect="정보 내용은 있으나, 그것이 곧 거래 비용을 넘는 수익으로 이어지지는 않는다.",
    limits="**이 주제는 논쟁적이다.** 같은 데이터로 반대 결론을 낸 연구도 많고, "
           "표본 기간을 바꾸면 결과가 자주 뒤집힌다. 이 프로그램의 시그널을 "
           "'검증된 수익 전략'으로 읽지 말 것.",
    confidence="contested",
    used_by=("signals/engine.py", "analog/matcher.py"),
    sources=(
        Source("Foundations of Technical Analysis: Computational Algorithms, "
               "Statistical Inference, and Empirical Implementation",
               "Lo, A. W., Mamaysky, H. & Wang, J.", 2000, "Journal of Finance 55(4)"),
    ),
))

# ------------------------------------------------------------- 이벤트 스터디

add(Evidence(
    key="event_study_car",
    field="event",
    claim="사건의 영향은 '초과수익률(AR)'과 그 누적(CAR)으로 잰다 — 사건 전 구간에서 "
          "정상 수익률 모형을 추정하고, 사건 창에서 실제와의 차이를 본다.",
    effect="금융 실증연구의 표준 절차다.",
    limits="정상 수익률 모형을 무엇으로 잡느냐에 결과가 민감하다. 사건 창이 겹치면 "
           "독립성이 깨진다. 사건일을 잘못 잡으면(정보가 미리 샜다면) 효과가 창 밖으로 나간다.",
    confidence="strong",
    used_by=("events/study.py: abnormal_returns",),
    sources=(
        Source("Event Studies in Economics and Finance",
               "MacKinlay, A. C.", 1997, "Journal of Economic Literature 35(1)"),
        Source("Using daily stock returns: The case of event studies",
               "Brown, S. J. & Warner, J. B.", 1985, "Journal of Financial Economics 14(1)"),
    ),
))

add(Evidence(
    key="crypto_announcement_reaction",
    field="event",
    claim="암호화폐는 주요 공시·뉴스가 나온 날 큰 초과수익률을 보이고, 반응이 "
          "여러 날에 걸쳐 이어진다 — 정보를 즉시 반영하지 않는다.",
    effect="사건일 초과수익률이 유의하고, (−3,+6)·(0,+6) 창의 CAR 이 계속 벌어진다는 보고.",
    limits="사건 표본을 어떻게 모으느냐에 크게 좌우된다. 큰 뉴스는 이미 가격에 반영된 "
           "뒤 보도되는 경우가 많아, 뉴스 시각과 가격 반응 시각이 어긋난다.",
    confidence="moderate",
    used_by=("events/study.py", "scenario/engine.py"),
    sources=(
        Source("Announcement effects in the cryptocurrency market",
               "Corbet, S., Larkin, C., Lucey, B., Meegan, A. & Yarovaya, L.",
               2020, "Applied Economics 52(44)",
               "https://www.tandfonline.com/doi/full/10.1080/00036846.2020.1745747"),
        Source("Bitcoin's sensitivity to external narratives: a study of abnormal "
               "returns in a transformative era",
               "Journal of Asset Management", 2026, "Journal of Asset Management",
               "https://link.springer.com/article/10.1057/s41260-026-00448-0"),
    ),
))

add(Evidence(
    key="scheduled_macro_event_risk",
    field="event",
    claim="FOMC 성명처럼 **예정된** 발표는 시각이 알려져 있어 그 전후로 변동성이 "
          "체계적으로 오르내린다. 암호화폐도 시간외가 없어 그대로 받는다.",
    effect="발표 시각 전후 시간 단위에서 변동성·거래량이 뚜렷하게 튀는 패턴이 보고됐다.",
    limits="예정된 사건과 돌발 사건은 성질이 다르다 — 예정된 것은 이미 상당 부분 "
           "가격에 들어가 있어, 반응은 '발표 내용과 예상의 차이'에 달렸다. "
           "이 프로그램은 예상치를 모르므로 방향이 아니라 변동성만 신뢰할 것.",
    confidence="moderate",
    used_by=("events/catalog.py", "scenario/engine.py"),
    sources=(
        Source("Scheduled FOMC statements and intraday macro event risk in "
               "cryptocurrency markets", "Finance Research Letters", 2026,
               "Finance Research Letters",
               "https://www.sciencedirect.com/science/article/abs/pii/S1544612326006021"),
    ),
))

add(Evidence(
    key="gdelt_news_volume",
    field="event",
    claim="GDELT 는 전세계 뉴스를 15분 단위로 색인해 공개한다 — 특정 주제의 보도량 급증을 "
          "'이슈가 터진 시점'의 대리 변수로 쓸 수 있다.",
    effect="보도량 급증 지표는 불확실성 대리 변수로 널리 쓰인다.",
    limits="보도량은 '중요도'가 아니라 '언론의 관심'이다. 같은 사건도 언어권마다 다르게 "
           "잡히고, 검색어를 어떻게 짜느냐가 결과를 좌우한다. DOC API 는 기본 3개월치만 준다.",
    confidence="moderate",
    used_by=("events/sources/gdelt.py",),
    sources=(
        Source("GDELT DOC 2.0 API", "The GDELT Project", 2018, "GDELT Blog",
               "https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/"),
    ),
))

# --------------------------------------------------------- 계절성·캘린더 효과

add(Evidence(
    key="calendar_effects",
    field="seasonality",
    claim="요일·월중 위치 같은 캘린더 축에서 수익률 평균이 균일하지 않다는 보고가 오래 있었다.",
    effect="주식에서 월요일 수익률이 낮고 월 전반부가 높다는 초기 보고가 있었다.",
    limits="**상당수가 발표 이후 사라졌다.** 데이터 스누핑(같은 데이터를 여러 번 훑어 "
           "우연한 규칙을 찾는 것)의 대표 사례로도 인용된다. 시장·기간이 바뀌면 부호까지 "
           "뒤집히므로, 이 프로그램은 캘린더 축을 **예측이 아니라 유사구간을 고르는 조건**으로만 쓴다.",
    confidence="contested",
    used_by=("context/calendar.py",),
    sources=(
        Source("Stock returns and the weekend effect",
               "French, K. R.", 1980, "Journal of Financial Economics 8(1)"),
        Source("A monthly effect in stock returns",
               "Ariel, R. A.", 1987, "Journal of Financial Economics 18(1)"),
    ),
))

# ------------------------------------------------------------------- 변동성

add(Evidence(
    key="volatility_clustering",
    field="volatility",
    claim="변동성은 뭉친다 — 큰 변동 뒤에 큰 변동이, 작은 변동 뒤에 작은 변동이 온다.",
    effect="거의 모든 금융 시계열에서 재현되는 성질이다. 수익률 제곱의 자기상관이 길게 남는다.",
    limits="'뭉친다'는 것은 크기를 말할 뿐 **방향은 말하지 않는다.** 변동성 예측이 잘 되는 것을 "
           "가격 예측이 잘 되는 것으로 착각하지 말 것.",
    confidence="strong",
    used_by=("forecast/stat.py", "context/regime.py"),
    sources=(
        Source("The Variation of Certain Speculative Prices",
               "Mandelbrot, B.", 1963, "Journal of Business 36(4)"),
        Source("Autoregressive Conditional Heteroscedasticity with Estimates of the "
               "Variance of United Kingdom Inflation",
               "Engle, R. F.", 1982, "Econometrica 50(4)"),
        Source("Generalized Autoregressive Conditional Heteroskedasticity",
               "Bollerslev, T.", 1986, "Journal of Econometrics 31(3)"),
    ),
))

add(Evidence(
    key="sqrt_time_scaling_is_optimistic",
    field="volatility",
    claim="구간 예측에서 표준편차를 √N 으로 늘리는 것은 수익률이 독립일 때만 옳다.",
    effect="변동성이 뭉치는 실제 시장에서는 이 방식이 꼬리 위험을 과소평가한다.",
    limits="그래서 이 프로그램은 √N 밴드를 유일한 답으로 내지 않고, 실제 수익률 분포를 "
           "부트스트랩한 결과와 ATR 도달범위를 **같이** 보여준다.",
    confidence="strong",
    used_by=("forecast/stat.py: project",),
    sources=(
        Source("The Variation of Certain Speculative Prices",
               "Mandelbrot, B.", 1963, "Journal of Business 36(4)"),
    ),
))

# ---------------------------------------------------------------- 구간 추정

add(Evidence(
    key="conformal_intervals",
    field="uncertainty",
    claim="컨포멀 예측은 모형의 과거 오차 분포로 구간을 만든다 — 분포 가정 없이 "
          "목표 커버리지에 가까운 구간을 준다.",
    effect="EnbPI 는 교환가능성 대신 '오차가 정상·강혼합'이라는 약한 가정만으로 "
           "시계열에서 근사적 커버리지를 얻는다.",
    limits="레짐이 바뀌는 순간(오차 분포가 통째로 이동)에는 커버리지가 깨진다. "
           "그때가 바로 예측이 제일 필요한 때라는 것이 이 방법의 근본적 한계다.",
    confidence="moderate",
    used_by=("analog/projection.py: calibrate",),
    sources=(
        Source("Conformal prediction interval for dynamic time-series",
               "Xu, C. & Xie, Y.", 2021, "ICML",
               "https://arxiv.org/abs/2010.09107"),
        Source("Conformal Prediction for Time Series",
               "Xu, C. & Xie, Y.", 2023, "IEEE TPAMI"),
    ),
))

# ------------------------------------------------------------ 모멘텀·평균회귀

add(Evidence(
    key="momentum",
    field="momentum",
    claim="3~12개월 상대 강도가 높았던 자산이 이후 3~12개월에도 더 오르는 경향이 보고됐다.",
    effect="원 논문은 미국 주식에서 월 약 1% 수준의 초과수익을 보고했다.",
    limits="폭락장 반전에서 크게 무너진다(모멘텀 크래시). 거래비용을 넣으면 상당 부분이 "
           "사라지고, 발표 이후 여러 시장에서 약해졌다.",
    confidence="moderate",
    used_by=("context/features.py: momentum axis",),
    sources=(
        Source("Returns to Buying Winners and Selling Losers: Implications for "
               "Stock Market Efficiency",
               "Jegadeesh, N. & Titman, S.", 1993, "Journal of Finance 48(1)"),
    ),
))

add(Evidence(
    key="overreaction_reversal",
    field="momentum",
    claim="크게 움직인 뒤에는 되돌리는 경향이 있다 — 시장이 극단적 뉴스에 과잉반응한다.",
    effect="3~5년 장기 구간에서 패자 포트폴리오가 승자를 앞선다는 보고.",
    limits="모멘텀과 정반대 방향이고, 어느 쪽이 이기는지는 **기간(horizon)이 정한다.** "
           "짧게는 모멘텀, 길게는 반전 쪽이라는 대략적 구분이 있을 뿐 경계가 뚜렷하지 않다.",
    confidence="moderate",
    used_by=("scenario/engine.py",),
    sources=(
        Source("Does the Stock Market Overreact?",
               "De Bondt, W. F. M. & Thaler, R.", 1985, "Journal of Finance 40(3)"),
    ),
))

# ---------------------------------------------------------------- 검증 방법

add(Evidence(
    key="triple_barrier_labeling",
    field="validation",
    claim="'다음 봉이 올랐나'로 라벨을 만들면 배우는 게 잡음이다. 익절·손절·시간만료 중 "
          "먼저 닿는 것을 정답으로 삼아야 실제 거래와 같은 질문이 된다.",
    effect="라벨의 경제적 의미가 생기고, 손절에 걸려 죽는 경로가 정답에서 빠진다.",
    limits="장벽 폭(ATR 배수)과 만료 기간을 어떻게 잡느냐가 곧 전략 설계다 — "
           "여러 조합을 돌려 제일 좋은 걸 고르면 그 자체가 과최적화다.",
    confidence="strong",
    used_by=("forecast/ml/labels.py: triple_barrier",),
    sources=(
        Source("Advances in Financial Machine Learning",
               "López de Prado, M.", 2018, "Wiley"),
    ),
))

add(Evidence(
    key="purged_walk_forward",
    field="validation",
    claim="시계열 교차검증에서 학습·검증 경계에 라벨이 겹치면(라벨이 미래를 본다) "
          "성적이 실제보다 좋게 나온다. 겹치는 구간을 버려야 한다.",
    effect="퍼징 없이 잰 성적은 체계적으로 부풀려진다.",
    limits="퍼징을 해도 같은 데이터로 여러 모형을 시험하면 다중검정 문제가 남는다. "
           "시험 횟수를 세지 않은 백테스트 성적은 그대로 믿을 수 없다.",
    confidence="strong",
    used_by=("forecast/ml/model.py: walk_forward",),
    sources=(
        Source("Advances in Financial Machine Learning",
               "López de Prado, M.", 2018, "Wiley"),
        Source("The Deflated Sharpe Ratio: Correcting for Selection Bias, "
               "Backtest Overfitting and Non-Normality",
               "Bailey, D. H. & López de Prado, M.", 2014,
               "Journal of Portfolio Management 40(5)"),
    ),
))

# ---------------------------------------------------------------- 지표 원전

add(Evidence(
    key="wilder_indicators",
    field="indicator",
    claim="RSI·ATR·ADX 의 평활은 전부 alpha=1/n 의 Wilder 방식이다.",
    effect="원전이 정한 계산 관례이고, 이 프로그램의 `_math.rma` 가 그것이다.",
    limits="구현체마다 시드값이 달라 초반 수십 봉의 값이 다르게 나온다 — "
           "다른 차트 도구와 소수점까지 맞으리라 기대하지 말 것.",
    confidence="strong",
    used_by=("indicators/_math.py: rma",),
    sources=(
        Source("New Concepts in Technical Trading Systems",
               "Wilder, J. W.", 1978, "Trend Research"),
    ),
))

add(Evidence(
    key="fisher_transform",
    field="indicator",
    claim="가격을 구간 안에서 −1..1 로 정규화한 뒤 피셔 변환을 걸면 분포가 정규분포에 "
          "가까워져 전환점이 뾰족해진다.",
    effect="극단값이 드물어지므로 임계선 돌파를 신호로 쓰기 쉬워진다.",
    limits="|x| 가 1에 닿으면 발산해 클램프가 필요하다. 정규화 창 안에서 가격이 "
           "움직이지 않으면 계산 자체가 성립하지 않는다.",
    confidence="moderate",
    used_by=("indicators/momentum.py: fisher",),
    sources=(
        Source("Using the Fisher Transform", "Ehlers, J. F.", 2002,
               "Technical Analysis of Stocks & Commodities"),
    ),
))

add(Evidence(
    key="ichimoku",
    field="indicator",
    claim="일목균형표는 전환·기준선의 관계, 구름대 대비 가격 위치, 후행스팬의 확인을 "
          "한 화면에서 같이 본다.",
    effect="여러 시간축의 중간값을 겹쳐 추세와 지지·저항을 동시에 표시한다.",
    limits="9·26·52 는 주 6일 거래를 전제로 나온 수다. 24시간 도는 암호화폐에 "
           "그대로 쓰는 것에 이론적 근거는 없다 — 관행일 뿐이다.",
    confidence="weak",
    used_by=("indicators/trend.py: ichimoku",),
    sources=(
        Source("一目均衡表", "細田悟一 (一目山人)", 1969, ""),
    ),
))
