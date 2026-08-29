"""메타 라벨링 — "방향을 맞힐 만한 자리인가" 를 따로 배운다.

이 도구의 약점은 하나다. 방향 적중 **55.0%**(21,089판). 밴드는 82.2% 로 쓸 만한데
방향은 동전던지기를 겨우 넘는다.

메타 라벨링은 그 자리를 노린 방법이다(López de Prado, 2017). 모델을 하나 더 쌓아
**"이번 예측이 맞을까"** 를 배우게 하고, 그 확률이 낮으면 말을 안 한다. 원래 모델은
많이 말하고(재현율), 두 번째 모델이 골라내(정밀도) F1 을 올린다는 생각이다.

## 이 저장소는 이걸 이미 한 번 해 봤다 — 손으로

`scripts/study.py` 의 기권 규칙 탐색이 그 축소판이다. 조건 하나에 문턱 하나를 걸어
"이러면 말을 안 한다" 를 찾았다. 8시간을 돌린 결과는 **못 찾았다** 였고, 제일 좋았던
`event_recency` 규칙의 +0.55%p 는 섞은 데이터의 95%(+3.84%p)에 한참 못 미쳤다.

그래서 여기서 묻는 것은 하나다: **문턱 하나가 아니라 모델을 쓰면 달라지나?**
축을 서른여섯 개 다 보고 비선형까지 쓰면 찾아질 수도 있다. 반대로 자유도가 커진
만큼 잡음을 더 잘 주울 수도 있다 — 그래서 **같은 귀무 검정을 그대로 통과시킨다.**

## 재는 방법

    ① 앞 70% 로 메타 모델 학습 (라벨 = 그때 방향을 맞혔나)
    ② 앞 구간에서만 문턱을 고른다        ← 뒤에서 고르면 자기 답을 본 성적이다
    ③ 뒤 30% 에서 그 문턱으로 기권시키고 방향 적중을 잰다
    ④ ①~③ 을 **결과를 덩어리째 섞어** 200번 다시 돌린다

④ 가 핵심이다. 섞으면 조건과 결과의 관계가 끊기므로 거기서 나온 이득은 전부 운이다.
**덩어리째** 섞는 이유는 적중이 시간에 뭉쳐 다니기 때문이다(`docs/STUDY.md`) —
한 줄씩 섞으면 귀무 세계가 실제보다 깨끗해져 문턱이 너무 낮게 잡힌다.

돌리는 법:
    .venv/Scripts/python scripts/metalabel.py
    .venv/Scripts/python scripts/metalabel.py --rounds 50
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "scripts"))

from marketlens.forecast import overfit  # noqa: E402

import study  # noqa: E402

TRAIN = 0.70
# 남길 판의 최소 비율. 이걸 안 두면 "제일 확신하는 두 판만 남기고 100%" 가 이긴다.
MIN_COVERAGE = 0.30
OUTCOMES = ("direction_hit", "band_hit", "error_atr",
            "baseline_error_atr", "realised", "moved")

TREE = dict(max_depth=3, max_iter=200, learning_rate=0.06,
            min_samples_leaf=200, l2_regularization=1.0, random_state=0)


def features(frame: pd.DataFrame) -> list[str]:
    """`c_` 로 시작하는 조건 축만. 결과 쪽 열이 섞이면 미래를 보게 된다."""
    return sorted(c for c in frame.columns
                  if c.startswith("c_") and frame[c].notna().sum() > len(frame) * 0.5)


def run_once(frame: pd.DataFrame, columns: list[str]) -> dict | None:
    """메타 모델을 앞에서 배우고 뒤에서 잰다. 돌려주는 것은 **뒤 구간의 이득**이다."""
    from sklearn.ensemble import HistGradientBoostingClassifier

    called = frame[frame["direction_hit"].notna()].sort_values("origin_ts")
    cut = int(len(called) * TRAIN)
    train, test = called.iloc[:cut], called.iloc[cut:]
    if len(test) < 500 or train["direction_hit"].nunique() < 2:
        return None

    x_train = train[columns].to_numpy(dtype="float64")
    y_train = train["direction_hit"].to_numpy(dtype="int")
    model = HistGradientBoostingClassifier(**TREE).fit(x_train, y_train)

    # **문턱은 앞 구간에서만 고른다.** 뒤에서 고르면 자기 답을 본 성적이 된다.
    p_train = model.predict_proba(x_train)[:, 1]
    best, best_score = None, -np.inf
    for q in np.arange(0.0, 0.71, 0.05):
        bar = float(np.quantile(p_train, q))
        kept = p_train >= bar
        if kept.mean() < MIN_COVERAGE:
            continue
        # 적중률만 보면 표본이 적을수록 이긴다. 남은 판 수로 눌러 준다.
        score = (y_train[kept].mean() - 0.5) * np.sqrt(kept.sum())
        if score > best_score:
            best, best_score = bar, score
    if best is None:
        return None

    p_test = model.predict_proba(test[columns].to_numpy(dtype="float64"))[:, 1]
    y_test = test["direction_hit"].to_numpy(dtype="int")
    kept = p_test >= best
    if kept.sum() < 200:
        return None

    return {
        "base": float(y_test.mean()),
        "gated": float(y_test[kept].mean()),
        "gain": float(y_test[kept].mean() - y_test.mean()),
        "coverage": float(kept.mean()),
        "n": int(kept.sum()),
        "testN": int(len(test)),
        # 남긴 판이 한쪽으로 쏠렸나. 상승장 편향을 다시 밟지 않으려면 봐야 한다.
        "upShare": float((test["predicted"].to_numpy()[kept] > 0).mean()),
        "baseUpShare": float((test["predicted"] > 0).mean()),
    }


def shuffled(frame: pd.DataFrame, seed: int, block: int) -> pd.DataFrame:
    """결과만 덩어리째 섞는다. 규칙과 근거는 `overfit.block_shuffle`."""
    return overfit.block_shuffle(frame, OUTCOMES, block, seed)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=200)
    args = parser.parse_args()

    frame = study.frame_of(study.load_verdicts())
    columns = features(frame)
    called = frame[frame["direction_hit"].notna()]
    print(f"판 {len(called):,}개 · 축 {len(columns)}개")

    real = run_once(frame, columns)
    if real is None:
        print("잴 수 있는 표본이 없다")
        return
    print(f"\n진짜: 뒤 구간 {real['testN']:,}판 중 {real['n']:,}판만 말한다"
          f" (커버리지 {real['coverage']:.0%})")
    print(f"  방향 적중 {real['base']:.1%} → {real['gated']:.1%}"
          f"  ({real['gain'] * 100:+.2f}%p)")
    print(f"  남긴 판의 상승 예측 비율 {real['upShare']:.1%}"
          f" (전체 {real['baseUpShare']:.1%})")

    # 덩어리 크기는 데이터가 정한다(Politis & White). 손으로 고른 200 이 아니다.
    block = overfit.pick_block(called["direction_hit"])
    print(f"\n결과를 {block}판씩 덩어리로 섞어 같은 절차를 {args.rounds}번 돌린다...")
    null = []
    for i in range(args.rounds):
        got = run_once(shuffled(frame, i, block), columns)
        if got:
            null.append(got["gain"])
        if (i + 1) % 25 == 0:
            done = np.asarray(null, dtype="float64")
            shown = (f"평균 {done.mean() * 100:+.2f}%p · 최대 {done.max() * 100:+.2f}%p"
                     if done.size else "아직 0개")
            print(f"  {i + 1}/{args.rounds} — {shown}")

    arr = np.asarray(null, dtype="float64")
    if arr.size == 0:
        print("\n섞은 데이터에서는 한 번도 문턱이 안 잡혔다")
        return

    p = overfit.p_value(real["gain"], arr)
    print(f"\n{'=' * 66}")
    print(f"진짜       {real['gain'] * 100:+.2f}%p")
    print(f"귀무 평균  {arr.mean() * 100:+.2f}%p   (산 횟수 {arr.size}/{args.rounds})")
    print(f"귀무 95%   {float(np.quantile(arr, 0.95)) * 100:+.2f}%p")
    print(f"p 값       {p:.4f}")
    print(f"{'=' * 66}")
    print("→ 잡음과 구별이 안 된다. 넣지 말 것." if p > 0.05 else
          "→ 섞은 데이터가 좀처럼 못 만드는 크기다. 다만 상승 예측 비율을 같이 볼 것 —\n"
          "  음수 예측만 버리는 규칙은 확신 게이트가 아니라 상승장 편향이다.")


if __name__ == "__main__":
    main()
