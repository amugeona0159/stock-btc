"""여러 번 시험하고 제일 좋은 걸 고르면, 잡음만으로도 좋은 게 나온다.

이 저장소는 두 곳에서 **골라 뽑는다.**

- `scripts/daily.py` — 매일 손잡이를 하나 흔들어 이기면 챔피언을 갈아끼운다.
- `scripts/study.py` — 가설 수천 개를 세우고 제일 좋은 규칙을 남긴다.

둘 다 시험 횟수를 로그에 적어 두고는 **판정에는 안 썼다.** 고정된 마진 하나로만
잘랐다. 그래서 한 번 속았다 — `move_atr < 0.038 이면 기권` 이 최종 구간에서
61.8% → 75.9% 로 크게 이겼는데, 알고 보니 음수 예측을 통째로 버리는 상승장
편향이었다(`docs/STUDY.md`). 이 파일은 그 자리를 숫자로 막으려는 것이다.

## 두 가지 도구

**1. 귀무 최고값** — "N번 시험하면 잡음만으로 최고가 얼마까지 나오나."
독립인 N번의 시험에서 최고값의 기댓값은 시험 수의 **로그 정도로만** 자란다:

    E[max] ≈ σ · [ (1−γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e)) ]     γ = 오일러 상수

가설 1,000개면 σ 의 3.26배다. `study.py` 의 점수는 귀무 표준편차가 0.5 근처이므로
**1,000개를 세운 날의 1등은 1.6점쯤을 그냥 받는다.** 지금 문턱은 0.01 이다.

**2. PBO (과최적화 확률)** — "고르는 절차 자체가 과최적화인가."
구간을 여러 조각으로 잘라 절반으로 고르고 나머지 절반에서 채점하기를 모든 조합으로
반복한다. 앞에서 1등이던 것이 뒤에서 **중앙값 아래로 떨어지는 비율**이 PBO 다.
0.5 면 고르는 행위에 정보가 하나도 없다는 뜻이다.

출처:
- Bailey, D. H., & López de Prado, M. "The Deflated Sharpe Ratio: Correcting for
  Selection Bias, Backtest Overfitting, and Non-Normality." *Journal of Portfolio
  Management* 40(5), 2014, 94–107. doi:10.3905/jpm.2014.40.5.094
- Bailey, D. H., Borwein, J., López de Prado, M., & Zhu, Q. J. "The Probability of
  Backtest Overfitting." *Journal of Computational Finance* 20(4), 2017, 39–69.
  doi:10.21314/JCF.2016.322

## 이 파일이 하지 않는 것

**정규분포를 가정한 식은 근사다.** 가설들이 서로 상관돼 있으면(대개 그렇다) 실제
독립 시험 수는 N 보다 적고, 위 식은 문턱을 **필요 이상으로 높게** 잡는다. 안전한
쪽으로 틀리는 것이지만 공짜는 아니다 — 진짜 신호도 같이 막는다.

그래서 `null_best()` 를 같이 둔다. **결과를 섞어서 탐색을 통째로 다시 돌리는** 방식이라
가설 수도 상관도 탐색 절차도 전부 자동으로 반영된다. 느린 대신 가정이 없다.
쓸 수 있으면 이쪽을 쓸 것.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from itertools import combinations

import numpy as np

EULER = 0.5772156649015329


def _ppf(p: float) -> float:
    """표준정규 분위수. scipy 를 부르지 않으려고 직접 둔다.

    이 파일은 학습이 아니라 **판정**에 쓰이고, 판정은 서버에서도 돌 수 있어야 한다.
    """
    if not 0.0 < p < 1.0:
        raise ValueError(f"분위수는 0과 1 사이여야 한다: {p}")
    # 역오차함수로 바로 간다. math.erf 의 역함수를 뉴턴법으로 몇 번 돌린다.
    x = 0.0
    target = 2.0 * p - 1.0
    for _ in range(60):
        err = math.erf(x) - target
        if abs(err) < 1e-14:
            break
        x -= err / (2.0 / math.sqrt(math.pi) * math.exp(-x * x))
    return x * math.sqrt(2.0)


def expected_max(trials: int, null_std: float = 1.0) -> float:
    """N번 시험했을 때 **잡음만으로** 기대되는 최고값.

    시험을 한 번만 했으면 0 이다(고를 게 없으니 선택 편향도 없다).
    """
    if trials <= 1:
        return 0.0
    n = float(trials)
    first = (1.0 - EULER) * _ppf(1.0 - 1.0 / n)
    second = EULER * _ppf(1.0 - 1.0 / (n * math.e))
    return null_std * (first + second)


def survives(observed: float, *, trials: int, null_std: float,
             cushion: float = 1.0) -> bool:
    """관측한 최고값이 시험 횟수를 감안해도 살아남는가.

    `cushion` 은 귀무 최고값에 더 곱하는 여유다. 1.0 이면 "평균적인 운" 만 넘으면
    통과인데, 그건 절반은 통과시킨다는 뜻이라 대개 부족하다.
    """
    return observed > expected_max(trials, null_std) * cushion


def null_best(search: Callable[[int], float], rounds: int = 200) -> np.ndarray:
    """**귀무 상태에서 탐색을 통째로 다시 돌린다.**

    `search(seed)` 는 결과를 섞은 뒤 탐색 전체를 수행하고 **그때의 최고 점수**를
    돌려줘야 한다. 가설을 몇 개 세웠는지, 서로 얼마나 겹치는지, 어떤 순서로
    걸렀는지가 전부 그 숫자에 녹아 있으므로 따로 셀 필요가 없다.

    돌려주는 것은 그 최고 점수 `rounds` 개다. 실제 점수를 이 분포의 어디에
    놓느냐가 곧 p 값이다.
    """
    out = np.empty(rounds, dtype="float64")
    for i in range(rounds):
        out[i] = search(i)
    return out


def p_value(observed: float, null: Sequence[float]) -> float:
    """귀무 분포에서 관측값 이상이 나올 비율.

    **+1 을 한다.** 200번 돌려 한 번도 안 넘었다고 p=0 이라고 쓰면 "절대 우연이
    아니다" 가 되는데, 실제로 말할 수 있는 건 "1/201 보다 작다" 까지다.
    """
    arr = np.asarray(null, dtype="float64")
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return 1.0
    return float((np.sum(arr >= observed) + 1) / (arr.size + 1))


def pbo(matrix: np.ndarray, splits: int = 10) -> dict:
    """과최적화 확률 (CSCV).

    `matrix` 는 (구간 × 후보) 성적표다. 행이 시간 조각, 열이 고를 대상.

    구간을 `splits` 조각으로 자르고, 절반을 골라 **앞**으로 삼아 1등을 뽑은 뒤
    나머지 **뒤**에서 그 1등의 순위를 본다. 모든 조합에 대해 반복한다.
    앞에서 1등이던 것이 뒤에서 중앙값 아래로 가는 비율이 PBO 다.

    0.5 는 고르는 행위에 정보가 없다는 뜻이고, 0 에 가까울수록 고르기가 실제로
    통한다는 뜻이다. **후보가 2개면 의미가 없다** — 순위가 둘뿐이라 늘 0 이나 1 이다.
    """
    data = np.asarray(matrix, dtype="float64")
    if data.ndim != 2 or data.shape[1] < 4:
        return {"pbo": float("nan"), "reason": "후보가 4개는 있어야 순위가 뜻을 가진다"}
    if splits % 2 or splits < 4:
        raise ValueError("조각 수는 4 이상의 짝수여야 한다")

    rows = data.shape[0]
    if rows < splits:
        return {"pbo": float("nan"), "reason": f"구간이 {rows}개뿐 — {splits}조각으로 못 자른다"}

    blocks = np.array_split(np.arange(rows), splits)
    half = splits // 2
    n = data.shape[1]

    logits = []
    for pick in combinations(range(splits), half):
        front = np.concatenate([blocks[i] for i in pick])
        back = np.concatenate([blocks[i] for i in range(splits) if i not in pick])

        best = int(np.nanargmax(np.nanmean(data[front], axis=0)))
        outside = np.nanmean(data[back], axis=0)
        # 뒤 구간에서 그 후보의 상대 순위. 1 이면 꼴찌, n 이면 1등.
        rank = float(np.sum(outside <= outside[best]))
        share = rank / (n + 1.0)
        share = min(max(share, 1e-6), 1.0 - 1e-6)
        logits.append(math.log(share / (1.0 - share)))

    array = np.asarray(logits, dtype="float64")
    return {
        "pbo": float(np.mean(array < 0.0)),
        "combinations": int(array.size),
        "candidates": n,
        "medianLogit": float(np.median(array)),
    }


def effective_n(rows: int, horizon: int) -> int:
    """겹치는 라벨을 감안한 **실질 표본 수.**

    지평이 10봉이면 오늘의 라벨과 내일의 라벨이 9봉을 공유한다. 행이 30,000개라도
    독립인 관측은 3,000개 남짓이고, 표본 수로 뭔가를 주장할 때는 이쪽을 써야 한다.

    López de Prado 의 '평균 고유도' 합이 곧 이 값이다. **이 저장소에서는 그 계산을
    할 필요가 없다** — 지평이 고정이고 봉마다 라벨이 하나씩이라 고유도가 안쪽에서
    정확히 1/지평 로 일정하기 때문이다. 재 봤다: 암호화폐 일봉 6종목에서 봉이
    하나도 안 빠져 가중치 최대/최소가 **정확히 1.000** 이었다.

    그래서 **표본 가중치는 넣지 않았다.** 전부 같은 값이라 학습이 달라질 수가 없다.
    빠진 봉이 많은 계열(거래 정지, 신규 상장 직후)에서는 달라지므로, 유니버스에
    그런 종목을 들이면 그때 다시 볼 것.

    출처: López de Prado, M. *Advances in Financial Machine Learning*, Wiley 2018,
    4장 Sample Weights (동시성 · 평균 고유도 · 순차 부트스트랩).
    """
    if rows <= 0 or horizon <= 1:
        return max(rows, 0)
    return max(1, int(rows / horizon))
