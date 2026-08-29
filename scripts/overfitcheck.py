"""규칙 탐색이 잡음을 줍고 있는지 잰다.

`scripts/study.py` 는 가설을 수천 개 세우고 제일 좋은 것을 남긴다. 그 자체가 나쁜 건
아닌데, **문턱이 고정값 하나**였다(`PROMOTE_MARGIN = 0.01`). 점수는 `(적중−0.5)×√판수`
라 귀무 표준편차가 0.5 근처다 — 0.01 은 그 50분의 1이다. 즉 탐색 구간의 필터가
사실상 "조금이라도 나으면 통과" 였다.

여기서 하는 일은 하나다. **결과를 섞어 놓고 같은 탐색을 통째로 다시 돌린다.**
섞으면 조건과 결과 사이의 관계가 끊기므로 거기서 나오는 최고 점수는 전부 운이다.
그 분포와 진짜 점수를 견준다.

이 방식이 식보다 나은 이유: 가설 수도, 가설끼리의 상관도, 걸러 낸 순서도 전부
그 숫자 하나에 녹는다. 셀 필요가 없다.

돌리는 법:
    .venv/Scripts/python scripts/overfitcheck.py
    .venv/Scripts/python scripts/overfitcheck.py --rounds 400
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


def search_once(frame: pd.DataFrame) -> tuple[float, int, dict | None]:
    """`study.search_rules` 와 같은 절차. **최고 점수와 가설 수**를 돌려준다.

    study 쪽 함수를 그대로 부르지 않는 이유는, 여기서는 최종 구간의 점수 차이가
    필요한데 그쪽은 규칙이 살아남았을 때만 그 값을 만들기 때문이다. 절차는 같다 —
    탐색 60% · 확인 20% · 최종 20%.
    """
    called = frame[frame["direction_hit"].notna()].sort_values("origin_ts")
    if len(called) < study.MIN_BUCKET * 6:
        return float("-inf"), 0, None

    a, b = int(len(called) * 0.6), int(len(called) * 0.8)
    discover, confirm, holdout = called.iloc[:a], called.iloc[a:b], called.iloc[b:]

    base_discover = study.value_of(discover, None)
    candidates = study.hypotheses(discover, study.splits(discover))
    scored = []
    for rule in candidates:
        found = study.value_of(discover, rule)
        if found["score"] > base_discover["score"] + study.PROMOTE_MARGIN:
            scored.append((rule, found))
    scored.sort(key=lambda pair: -pair[1]["score"])

    base_confirm = study.value_of(confirm, None)
    survivors = []
    for rule, _found in scored[:20]:
        checked = study.value_of(confirm, rule)
        if checked["score"] > base_confirm["score"] + study.PROMOTE_MARGIN:
            survivors.append(rule)

    if not survivors:
        return float("-inf"), len(candidates), None

    best = survivors[0]
    base = study.value_of(holdout, None)
    with_rule = study.value_of(holdout, best)
    if base["n"] == 0 or with_rule["n"] < study.MIN_BUCKET:
        return float("-inf"), len(candidates), None

    # 최종 구간에서 얼마나 올랐나. 이게 '이 탐색이 건진 것' 이다.
    gain = with_rule["directionHit"] - base["directionHit"]
    return gain, len(candidates), {
        "label": best.label, "n": with_rule["n"],
        "base": base["directionHit"], "withRule": with_rule["directionHit"],
        "upShare": with_rule["upShare"], "baseUpShare": base["upShare"],
    }


OUTCOMES = ("direction_hit", "band_hit", "error_atr",
            "baseline_error_atr", "realised", "moved")


def shuffled(frame: pd.DataFrame, seed: int, block: int) -> pd.DataFrame:
    """결과만 덩어리째 섞는다. 규칙과 근거는 `overfit.block_shuffle`."""
    return overfit.block_shuffle(frame, OUTCOMES, block, seed)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=200)
    args = parser.parse_args()

    verdicts = study.load_verdicts()
    frame = study.frame_of(verdicts)
    called = frame[frame["direction_hit"].notna()]
    print(f"판 {len(frame):,}개 · 방향을 센 판 {len(called):,}개")

    real, tried, detail = search_once(frame)
    if detail is None:
        print("진짜 데이터에서 살아남은 규칙이 없다 — 잴 것이 없다")
        return

    print(f"\n진짜 탐색: 가설 {tried:,}개 → {detail['label']}")
    print(f"  최종 구간 {detail['n']:,}판 · {detail['base']:.1%} → {detail['withRule']:.1%}"
          f"  ({real * 100:+.2f}%p)")
    print(f"  남긴 판의 상승 예측 비율 {detail['upShare']:.1%} (전체 {detail['baseUpShare']:.1%})")

    # **덩어리 크기를 데이터가 정한다.** 손으로 200 을 골랐던 자리다 — 근거가 없었고,
    # 재 보니 실제 자기상관이 그보다 훨씬 길었다(적중은 시간에 뭉쳐 다닌다).
    block = overfit.pick_block(called["direction_hit"])
    print(f"\n결과를 {block}판씩 덩어리로 섞어 같은 탐색을 {args.rounds}번 돌린다...")
    null = []
    for i in range(args.rounds):
        gain, _, _ = search_once(shuffled(frame, i, block))
        if np.isfinite(gain):
            null.append(gain)
        if (i + 1) % 50 == 0:
            done = np.asarray(null, dtype="float64")
            if done.size == 0:
                # 섞은 데이터에서 규칙이 하나도 안 살아남는 것 자체가 결과다.
                print(f"  {i + 1}/{args.rounds} — 아직 살아남은 규칙 0개")
                continue
            print(f"  {i + 1}/{args.rounds} — 산 규칙 {done.size}개 · 평균 "
                  f"{done.mean() * 100:+.2f}%p · 최대 {done.max() * 100:+.2f}%p")

    null_arr = np.asarray(null, dtype="float64")
    if null_arr.size == 0:
        print("\n섞은 데이터에서는 규칙이 한 번도 안 살아남았다 — 탐색이 그만큼 빡빡하다")
        return

    p = overfit.p_value(real, null_arr)
    q95 = float(np.quantile(null_arr, 0.95))

    print(f"\n{'=' * 66}")
    print(f"진짜         {real * 100:+.2f}%p")
    print(f"귀무 평균    {null_arr.mean() * 100:+.2f}%p   "
          f"(섞어도 규칙이 산 횟수 {null_arr.size}/{args.rounds})")
    print(f"귀무 95%     {q95 * 100:+.2f}%p")
    print(f"p 값         {p:.4f}")
    print(f"{'=' * 66}")
    if p > 0.05:
        print("→ 이 정도 이득은 **섞은 데이터에서도 나온다.** 규칙을 쓰면 안 된다.")
    else:
        print("→ 섞은 데이터가 좀처럼 못 만드는 크기다. 다만 p 하나로 결정하지 말 것 —")
        print("  판이 한쪽으로 쏠렸는지(upShare)를 같이 봐야 한다.")


if __name__ == "__main__":
    main()
