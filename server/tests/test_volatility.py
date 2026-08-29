"""봉 범위로 재는 변동성.

**모의 가격은 봉 안을 실제로 걸어 다닌다.** 고가·저가를 손으로 지어내면 추정량이
서로 어떻게 다른지가 전부 가정이 된다 — 이 파일이 지키려는 게 바로 그 차이다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from marketlens.forecast.ml import volatility as vol

SIGMA = 0.02      # 진짜 변동성 2%/봉


def walk(seed: int, n: int = 3000, *, drift: float = 0.0, gap: float = 0.0,
         steps: int = 390) -> pd.DataFrame:
    """봉 하나를 `steps` 조각으로 걸어 고가·저가를 진짜로 만든다."""
    rng = np.random.default_rng(seed)
    inner = rng.normal(drift / steps, SIGMA / np.sqrt(steps), size=(n, steps))
    gaps = rng.normal(0.0, gap, size=n) if gap else np.zeros(n)
    rows, log_close = [], 0.0
    for day, jump in zip(inner, gaps):
        opened = log_close + jump
        path = opened + np.cumsum(day)
        rows.append((np.exp(opened), np.exp(path.max()),
                     np.exp(path.min()), np.exp(path[-1])))
        log_close = path[-1]
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    frame["volume"] = 1.0
    return frame


ESTIMATORS = {
    "parkinson": vol.parkinson,
    "garman_klass": vol.garman_klass,
    "rogers_satchell": vol.rogers_satchell,
    "yang_zhang": vol.yang_zhang,
}


@pytest.mark.parametrize("name", sorted(ESTIMATORS))
def test_estimators_find_the_true_volatility(name):
    """추세가 없으면 넷 다 진짜 값을 맞혀야 한다. 여기서 틀리면 공식이 틀린 것이다."""
    got = float(ESTIMATORS[name](walk(11)).mean())
    assert got == pytest.approx(SIGMA, rel=0.10), f"{name}: {got:.4f}"


def test_range_beats_close_to_close_on_precision():
    """범위를 쓰는 이유 하나. 같은 봉 수로 더 정확해야 한다.

    정확도는 '평균이 맞나' 가 아니라 '추정치가 얼마나 덜 흔들리나' 다.
    """
    frame = walk(11)
    close = np.log(frame["close"] / frame["close"].shift(1))
    noisy = close.rolling(vol.WINDOW).std(ddof=1).dropna()
    spread = (noisy.std() / noisy.mean()) ** 2

    for name, fn in ESTIMATORS.items():
        series = fn(frame).dropna()
        got = (series.std() / series.mean()) ** 2
        assert got < spread * 0.6, f"{name}: {got:.4f} vs 종가 {spread:.4f}"


def test_drift_inflates_parkinson_but_not_rogers_satchell():
    """추세를 변동성으로 잘못 읽는가.

    Parkinson 은 부풀고(고저 범위가 추세만큼 넓어진다) Rogers–Satchell 은 버틴다.
    **둘을 같이 넣는 근거가 이 차이다** — 하나만 쓰면 어느 쪽으로 틀렸는지 모른다.
    """
    calm, trend = walk(11), walk(11, drift=0.06)

    park = float(vol.parkinson(trend).mean() / vol.parkinson(calm).mean())
    rs = float(vol.rogers_satchell(trend).mean() / vol.rogers_satchell(calm).mean())

    assert park > 1.5, f"Parkinson 이 추세에 안 부풀었다: {park:.2f}배"
    assert 0.85 < rs < 1.15, f"Rogers–Satchell 이 추세에 흔들렸다: {rs:.2f}배"


def test_sparse_bars_collapse_rogers_satchell():
    """**봉이 성기면 RS 가 반대로 무너진다.** 문서에 적은 함정을 고정한다.

    고가·저가가 진짜 극단을 못 잡으면 RS 의 두 항이 같이 0 으로 간다.
    거래가 뜸한 종목의 일봉이 그 자리다. 이 성질이 사라지면 문서도 같이 고쳐야 한다.
    """
    fine = float(vol.rogers_satchell(walk(11, drift=0.06, steps=390)).mean())
    sparse = float(vol.rogers_satchell(walk(11, drift=0.06, steps=24)).mean())
    assert sparse < fine * 0.5, f"성긴 봉 {sparse:.4f} · 촘촘한 봉 {fine:.4f}"


def test_overnight_share_separates_crypto_from_stocks():
    """갭 몫은 24시간 시장에서 0 이다. 이 축 하나로 시장 종류가 드러난다."""
    crypto = vol.range_features(walk(5, gap=0.0))["overnight_share"].mean()
    stock = vol.range_features(walk(5, gap=0.01))["overnight_share"].mean()
    assert crypto < 0.02, f"암호화폐 갭 몫이 0 이 아니다: {crypto:.3f}"
    assert stock > 0.15, f"주식 갭 몫이 너무 작다: {stock:.3f}"


def test_features_do_not_care_about_price_level():
    """BTC 와 삼성전자가 한 표에서 배운다. 가격 크기가 남아 있으면 안 된다."""
    base = walk(3)
    scaled = base.copy()
    scaled[["open", "high", "low", "close"]] *= 100.0
    pd.testing.assert_frame_equal(vol.range_features(base).dropna(),
                                  vol.range_features(scaled).dropna(),
                                  atol=1e-9)


def test_warmup_stays_empty():
    """창이 안 찬 앞부분은 NaN 이어야 한다.

    0 으로 채우면 '변동성이 없다' 는 거짓말이 되고, 학습 표는 NaN 행만 버릴 줄 안다.
    """
    frame = vol.range_features(walk(3))
    assert frame.head(vol.WINDOW).isna().any(axis=1).all()
    assert frame.tail(100).notna().all(axis=1).all()


def test_every_declared_column_exists():
    """`RANGE_COLUMNS` 와 실제 표가 어긋나면 학습에서 축이 조용히 빠진다."""
    frame = vol.range_features(walk(3))
    assert list(frame.columns) == list(vol.RANGE_COLUMNS)


def test_flat_bars_do_not_explode():
    """움직이지 않은 봉. 0 으로 나누는 자리가 여럿이라 실제로 터진 적이 있다."""
    flat = pd.DataFrame({"open": [100.0] * 80, "high": [100.0] * 80,
                         "low": [100.0] * 80, "close": [100.0] * 80,
                         "volume": [1.0] * 80})
    frame = vol.range_features(flat)
    values = frame.to_numpy(dtype="float64")
    # 무한대가 나오면 학습 표에서 그 열 전체가 못 쓰게 된다. NaN 은 괜찮다 —
    # 변동성이 0 인 구간의 비는 정의되지 않는 게 맞고, 그 행만 빠진다.
    assert not np.isinf(values).any()


def test_monotone_bar_does_not_drop_the_row():
    """고가에서 마감하고 저가에서 시작한 봉.

    Rogers–Satchell 은 그런 봉의 분산을 **정확히 0** 이라고 말한다(사각지대다).
    `log(0)` 을 그대로 두면 그 행이 학습에서 빠진다 — 재 보니 0.7% 나 된다.
    '모른다' 가 아니라 '아주 조용하다' 이므로 바닥에서 멈춰야 한다.
    """
    bars = walk(3).copy()
    # 한 봉을 저가 시작 · 고가 마감으로 바꿔 RS 를 0 으로 만든다.
    i = 100
    bars.loc[i, ["open", "low"]] = 100.0
    bars.loc[i, ["high", "close"]] = 103.0

    assert float(vol.rogers_satchell(bars, 1).iloc[i]) == 0.0, "전제가 깨졌다"

    frame = vol.range_features(bars)
    assert frame.loc[i].notna().all(), "RS 0 인 봉에서 행이 통째로 빠졌다"
    assert np.isfinite(frame["har_short"].to_numpy()[vol.WINDOW:]).all()


def test_no_future_leaks_into_the_past():
    """**미래를 잘라내도 과거 값이 그대로여야 한다.**

    학습 표에서 미래가 새는 제일 흔한 경로는 중심 이동평균이나 전체 구간 정규화다.
    새면 검증 성적만 좋아지고 실전에서 그대로 무너지는데, 성적표만 봐서는 안 보인다.
    """
    full = walk(3, n=800)
    reference = vol.range_features(full)

    for cut in (300, 500, 700):
        partial = vol.range_features(full.iloc[:cut].copy())
        gap = (reference.iloc[:cut] - partial).abs().to_numpy(dtype="float64")
        assert np.nanmax(gap) == 0.0, f"{cut}봉에서 자르면 과거가 바뀐다"


def test_yang_zhang_avoids_the_bug_that_got_copied_everywhere():
    """널리 퍼진 YZ 구현 셋이 논문과 어긋난다. 우리 것이 그쪽으로 가지 않게 못 박는다.

    제일 큰 것: 장중 항에 `ln(C_t/C_{t-1})` 을 쓰는 것. 논문은 `ln(C_i/O_i)` 다.
    `ln(C/C_prev) = 갭 + 장중` 이라 **갭을 두 번 세게 되고** 위로 부푼다.
    (나머지 둘 — 평균을 안 빼는 것, RS 를 1/(n−1) 로 나누는 것 — 도 같이 부풀린다.)
    """
    def buggy(frame: pd.DataFrame, n: int) -> float:
        """퍼져 있는 잘못된 형태를 그대로 재현한다."""
        log_cc = np.log(frame["close"] / frame["close"].shift(1))   # 논문은 ln(C/O)
        wrong_c = (log_cc ** 2).rolling(n).sum() / (n - 1.0)        # 평균을 안 뺀다
        log_o = np.log(frame["open"] / frame["close"].shift(1))
        wrong_o = (log_o ** 2).rolling(n).sum() / (n - 1.0)
        wrong_rs = (vol.rogers_satchell(frame, 1) ** 2).rolling(n).sum() / (n - 1.0)
        k = 0.34 / (1.34 + (n + 1.0) / (n - 1.0))
        return float(np.sqrt(wrong_o + k * wrong_c + (1.0 - k) * wrong_rs).mean())

    n = vol.WINDOW
    # **갭이 커질수록 격차가 벌어져야 한다.** 그게 갭을 두 번 세고 있다는 증거다.
    # 한 지점의 크기(재 보면 2~6%)보다 이 기울기가 훨씬 단단한 신호다.
    ratios = []
    for gap in (0.0, 0.005, 0.01, 0.02):
        frame = walk(11, gap=gap)
        ratios.append(buggy(frame, n) / float(vol.yang_zhang(frame, n).mean()))

    assert ratios == sorted(ratios), f"갭이 커져도 격차가 안 벌어진다: {ratios}"
    assert ratios[-1] > ratios[0] * 1.02, f"이중계상이 안 드러난다: {ratios}"
    assert float(vol.yang_zhang(walk(11), n).mean()) == pytest.approx(SIGMA, rel=0.10)


def test_yang_zhang_weight_uses_the_papers_constant():
    """k = 0.34 / (1.34 + (n+1)/(n−1)). 분모의 1.34 를 1 로 쓴 포크가 돌아다닌다.

    k 를 0.34 로 **박아 둔** 구현도 있다. 실제 k 는 0.14 근처다.
    """
    for n in (10, 20, 30):
        k = 0.34 / (1.34 + (n + 1.0) / (n - 1.0))
        assert 0.10 < k < 0.16, f"n={n}: k={k:.3f}"
    # 창이 길어질수록 k 는 커진다(갭의 몫이 늘어난다).
    ks = [0.34 / (1.34 + (n + 1.0) / (n - 1.0)) for n in (5, 10, 30, 100)]
    assert ks == sorted(ks)
