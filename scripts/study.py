"""자율 학습 — 예측하고, 맞았는지 보고, **왜 틀렸는지 분석해서** 고친다.

지금 있는 것으로는 이 고리가 안 닫힌다. `scripts/asof.py` 는 재기만 하고,
`scripts/daily.py` 는 설정을 흔들 뿐 왜 틀렸는지를 안 본다. 여기가 그 사이를 잇는다.

## 한 라운드

```
① 예측  과거 origin 에 서서 as-of 예측을 만든다 (그 시점 이후는 안 본다)
② 채점  지평이 지난 뒤 실제와 맞춘다 — 방향·밴드·오차
③ 분석  맞은 판과 틀린 판을 **조건별로 갈라** 본다
④ 가설  갈라진 조건을 '기권 규칙'으로 바꿔 재 본다
⑤ 확인  찾은 구간이 아니라 **뒤 구간**에서 확인한다
⑥ 기록  판 하나하나를 남긴다. 다음 라운드가 이어서 판다
```

## 왜 '기권'인가

짧은 지평의 방향 예측은 문헌에서도 잡음에 가깝고, 이 저장소가 잰 것도 그랬다
(암호화폐 일봉 방향 54%). 이런 판에서 정확도를 올리는 가장 확실한 길은 **더 잘
맞히는 것**이 아니라 **못 맞히는 자리에서 말을 안 하는 것**이다. 그래서 ④의 가설은
전부 "이 조건에서는 기준선만 쓴다" 꼴이다.

## 스스로를 속이지 않기 위한 장치

- 판을 시간순으로 **탐색 60% / 확인 20% / 최종 20%** 로 나눈다.
  규칙은 탐색에서 찾고, 확인에서 걸러내고, **최종 구간은 끝까지 안 본다.**
- 표본 30판 미만인 조건은 버린다.
- **세운 가설의 수를 남긴다.** 몇백 개를 세우면 그중 하나는 반드시 이긴다.

돌리는 법:
    .venv/Scripts/python scripts/study.py --hours 8
    .venv/Scripts/python scripts/study.py --hours 0.2 --origins 12   # 짧게 확인
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
import traceback
import warnings
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "scripts"))
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv                                    # noqa: E402

# 프로바이더가 키를 읽기 **전에** 올려야 한다. 스크립트는 `api/app.py` 를 안 거치므로
# 여기서 직접 부른다 — 안 그러면 토스가 조용히 "키가 비어 있다"로 빠진다.
load_dotenv(ROOT / ".env")

import asof                                                       # noqa: E402
from marketlens.forecast.ml import dataset                        # noqa: E402
from marketlens.forecast.ml import model as ml                    # noqa: E402

OUT = ROOT / "learning" / "study"
VERDICTS = OUT / "verdicts.jsonl"
STATE = OUT / "state.json"
SUMMARY = ROOT / "docs" / "STUDY.md"
# 최종 구간에서 확인된 규칙만 여기에 쓴다. 화면이 이걸 읽어 실제로 기권한다.
GATE = OUT / "gate.json"
NEWLINE = chr(10)

# 규칙이 챔피언을 갈아치우려면 이만큼은 이겨야 한다. `daily.py` 와 같은 정신.
PROMOTE_MARGIN = 0.01
# 조건 한 칸에 이만큼은 있어야 본다. 열 판으로 60% 를 봐도 그건 아무 말도 아니다.
MIN_BUCKET = 30
# 학습을 몇 origin 마다 다시 할지. 매번이면 못 끝내고, 한 번이면 미래를 본다.
RETRAIN_EVERY = 10


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- 무엇을 시험할까 ------------------------------------------------------

@dataclass(frozen=True)
class Target:
    provider: str
    market: str
    timeframe: str
    horizon: int
    symbols: tuple[str, ...]

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.timeframe}:{self.horizon}"


CRYPTO = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT")
STOCKS = ("AAPL", "MSFT", "NVDA", "AMZN", "META", "^GSPC")
KOREA = ("005930", "000660", "035720", "005380", "051910", "035420")


def targets() -> list[Target]:
    """되는 자리(주식 일봉)를 중심에 두되 안 되는 자리도 남긴다 — '안 된다'도
    매일 다시 확인해야 하는 사실이다."""
    out: list[Target] = []
    for horizon in (1, 2, 3, 5, 10):
        out.append(Target("yahoo", "us", "1d", horizon, STOCKS))
        out.append(Target("binance", "crypto", "1d", horizon, CRYPTO))
    for horizon in (3, 6, 12):
        out.append(Target("binance", "crypto", "1h", horizon, CRYPTO))
    for horizon in (1, 3, 5):
        out.append(Target("toss_kr", "kr", "1d", horizon, KOREA))
    return out


# --- 판 하나 -------------------------------------------------------------

# 갈라 볼 조건. 전부 그 시점까지의 정보로만 만들어지는 축이다
# (`dataset.build` 가 인과성을 보증하고 `tests/test_asof.py` 가 그걸 지킨다).
CONDITIONS = (
    "vol_percentile", "adx", "trend_state", "atr_pct", "bandwidth", "squeeze",
    "rsi", "percent_b", "range_position", "px_over_ema20", "px_over_ema60",
    "analog_corr_max", "analog_prob_up", "analog_spread",
    "event_recency", "event_severity", "attention_z",
    "volume_ratio", "cmf", "clv", "pressure",
)
# 예측 자체에서 나오는 조건. "모델이 자신 있다고 했나" 를 재는 축이라 따로 만든다.
FORECAST_CONDITIONS = ("band_atr", "move_atr", "prob_up")


@dataclass
class Verdict:
    """판 하나. 이 줄 하나가 '언제 무엇을 예측했고 실제로 어떻게 됐나' 전부다."""

    target: str
    symbol: str
    timeframe: str
    horizon: int
    origin_ts: int
    source: str                 # blend | volatility-baseline | analog
    predicted: float            # 지평에서의 예측 로그수익률
    realised: float
    low: float
    high: float
    atr: float
    conditions: dict = field(default_factory=dict)

    # --- 채점 ---
    @property
    def moved(self) -> bool:
        """실제가 뚜렷하게 움직였나. 0 근처에서는 부호가 동전던지기다."""
        return abs(self.realised) > 0.25 * self.atr

    @property
    def spoke(self) -> bool:
        return abs(self.predicted) > 1e-12

    @property
    def direction_hit(self) -> bool | None:
        if not (self.moved and self.spoke):
            return None
        return math.copysign(1, self.predicted) == math.copysign(1, self.realised)

    @property
    def band_hit(self) -> bool:
        return self.low <= self.realised <= self.high

    @property
    def error_atr(self) -> float:
        return abs(self.predicted - self.realised) / self.atr

    @property
    def baseline_error_atr(self) -> float:
        """아무 말도 안 했을 때의 오차. 모델은 이걸 이겨야 의미가 있다."""
        return abs(self.realised) / self.atr


def condition_row(view: asof.Slice, horizon: int) -> dict:
    """origin 시점의 조건. `dataset.build` 를 그대로 쓴다 — 축을 새로 만들지 않는다."""
    try:
        panel = dataset.build(view.closed, view.events, window=asof.WINDOW,
                              horizon=horizon, attention_frame=view.attention)
    except Exception:                                              # noqa: BLE001
        return {}
    if panel.empty:
        return {}
    row = panel.iloc[-1]
    out = {}
    for name in CONDITIONS:
        if name in panel.columns:
            value = row[name]
            if pd.notna(value) and np.isfinite(value):
                out[name] = round(float(value), 6)
    return out


async def play(target: Target, origins: int, budget_end: float) -> list[Verdict]:
    """한 대상에 대해 origin 들을 돌며 예측하고 채점한다."""
    bars = {"1d": 3000, "1h": 8000}.get(target.timeframe, 3000)
    pool: list[asof.Slice] = []
    for symbol in target.symbols:
        piece = await asof.load(symbol, target.provider, target.timeframe, bars,
                                target.market)
        if piece is not None and len(piece.closed) >= asof.MIN_HISTORY + target.horizon + 50:
            pool.append(piece)
        # 토스·업비트는 호출 한도가 있다. 몰아치지 않는다.
        if target.provider.startswith(("toss", "upbit")):
            await asyncio.sleep(1.2)
    if not pool:
        return []

    lead = pool[0]
    closed = lead.closed
    n = len(closed)
    lo, hi = asof.MIN_HISTORY, n - target.horizon - 1
    if hi <= lo:
        return []
    positions = np.unique(np.linspace(lo, hi, origins).astype(int))
    log_close = np.log(closed["close"].to_numpy(dtype="float64"))

    name = f"study-{target.key.replace(':', '-')}".lower()
    verdicts: list[Verdict] = []
    trained_at = -10**9

    for order, position in enumerate(positions):
        if time.time() > budget_end:
            break
        origin_ts = int(closed["ts"].iloc[position])
        view = asof.cut(lead, origin_ts)
        atr = float(ml.volatility_scale(view.closed).iloc[-1])
        if not np.isfinite(atr) or atr <= 0:
            continue
        realised = float(log_close[position + target.horizon] - log_close[position])

        # 학습은 **동료 종목까지 모아서** 한다. 한 종목만 쓰면 표본이 모자라
        # 실력을 실제보다 낮게 재게 된다 — 이 저장소가 이미 겪은 함정이다.
        if order - trained_at >= RETRAIN_EVERY:
            cuts = [asof.cut(p, origin_ts) for p in pool]
            data = [ml.SymbolData(c.symbol, c.closed, c.events, c.attention)
                    for c in cuts if len(c.closed) > asof.MIN_HISTORY]
            if data:
                try:
                    ml.train(data, name, horizon=target.horizon, window=asof.WINDOW,
                             folds=3, timeframe=target.timeframe)
                    trained_at = order
                except Exception:                                  # noqa: BLE001
                    pass

        if ml.load(name) is None:
            continue
        try:
            out = ml.predict(view.closed, name, view.events, target.timeframe,
                             view.attention)
        except Exception:                                          # noqa: BLE001
            continue
        if not out.get("available"):
            continue

        last = out["last"]
        predicted = float(np.log(out["bands"]["p50"][-1]["value"] / last))
        low = float(np.log(out["bands"]["p10"][-1]["value"] / last))
        high = float(np.log(out["bands"]["p90"][-1]["value"] / last))
        conditions = condition_row(view, target.horizon)
        # 예측 자체가 말해 주는 조건. 밴드가 넓다는 건 모델이 모르겠다는 뜻이다.
        conditions["band_atr"] = round((high - low) / atr, 6)
        conditions["move_atr"] = round(predicted / atr, 6)
        if out.get("probUp") is not None:
            conditions["prob_up"] = round(float(out["probUp"]), 6)

        verdicts.append(Verdict(
            target=target.key, symbol=lead.symbol, timeframe=target.timeframe,
            horizon=target.horizon, origin_ts=origin_ts,
            source=str(out.get("source", "?")), predicted=predicted,
            realised=realised, low=low, high=high, atr=atr, conditions=conditions,
        ))
    return verdicts


# --- ③ 분석: 맞은 판과 틀린 판을 조건별로 갈라 본다 -----------------------

def frame_of(verdicts: list[Verdict]) -> pd.DataFrame:
    rows = []
    for v in verdicts:
        row = {
            "target": v.target, "symbol": v.symbol, "horizon": v.horizon,
            "origin_ts": v.origin_ts, "source": v.source,
            "predicted": v.predicted, "realised": v.realised, "atr": v.atr,
            "moved": v.moved, "spoke": v.spoke,
            "direction_hit": v.direction_hit, "band_hit": v.band_hit,
            "error_atr": v.error_atr, "baseline_error_atr": v.baseline_error_atr,
        }
        row.update({f"c_{k}": val for k, val in v.conditions.items()})
        rows.append(row)
    if not rows:
        # 빈 표에도 열이 있어야 한다. 없으면 부르는 쪽마다 빈 검사를 다시 써야 한다.
        return pd.DataFrame(columns=["target", "symbol", "horizon", "origin_ts",
                                     "source", "predicted", "realised", "atr",
                                     "moved", "spoke", "direction_hit", "band_hit",
                                     "error_atr", "baseline_error_atr"])
    return pd.DataFrame(rows).sort_values("origin_ts").reset_index(drop=True)


def overall(frame: pd.DataFrame) -> dict:
    """전체 성적. 모든 비교의 기준점이다."""
    if frame.empty:
        return {"n": 0}
    called = frame[frame["direction_hit"].notna()]
    return {
        "n": int(len(frame)),
        "directionN": int(len(called)),
        "directionHit": float(called["direction_hit"].mean()) if len(called) else float("nan"),
        "bandHit": float(frame["band_hit"].mean()),
        "errorAtr": float(frame["error_atr"].mean()),
        "baselineErrorAtr": float(frame["baseline_error_atr"].mean()),
        "beatsBaseline": float((frame["baseline_error_atr"] - frame["error_atr"]).mean()),
    }


def splits(frame: pd.DataFrame) -> list[dict]:
    """조건마다 세 칸(낮음/중간/높음)으로 갈라 방향 적중을 본다.

    이게 "왜 맞았나 / 왜 틀렸나" 의 정직한 형태다 — 사후 서사가 아니라 갈라 본 숫자다.
    """
    called = frame[frame["direction_hit"].notna()]
    if len(called) < MIN_BUCKET * 3:
        return []
    base = float(called["direction_hit"].mean())
    found: list[dict] = []
    for column in [c for c in frame.columns if c.startswith("c_")]:
        values = called[column]
        if values.notna().sum() < MIN_BUCKET * 3 or values.nunique() < 6:
            continue
        try:
            bucket = pd.qcut(values.rank(method="first"), 3, labels=["낮음", "중간", "높음"])
        except ValueError:
            continue
        for name, group in called.groupby(bucket, observed=True):
            if len(group) < MIN_BUCKET:
                continue
            hit = float(group["direction_hit"].mean())
            found.append({
                "condition": column[2:], "bucket": str(name), "n": int(len(group)),
                "directionHit": round(hit, 4), "vsAll": round(hit - base, 4),
                "errorAtr": round(float(group["error_atr"].mean()), 4),
                "beatsBaseline": round(
                    float((group["baseline_error_atr"] - group["error_atr"]).mean()), 4),
                "low": round(float(values[group.index].min()), 6),
                "high": round(float(values[group.index].max()), 6),
            })
    found.sort(key=lambda s: -abs(s["vsAll"]))
    return found


# --- ④ 가설: 갈라진 조건을 '기권 규칙'으로 ------------------------------

@dataclass
class Rule:
    """'이 조건이면 말을 안 한다'. 기권한 판은 방향을 세지 않는다."""

    condition: str
    op: str                      # "<" | ">"
    threshold: float

    @property
    def label(self) -> str:
        return f"{self.condition} {self.op} {self.threshold:.4g} 이면 기권"

    def abstains(self, frame: pd.DataFrame) -> pd.Series:
        column = f"c_{self.condition}"
        if column not in frame.columns:
            return pd.Series(False, index=frame.index)
        values = frame[column]
        mask = values < self.threshold if self.op == "<" else values > self.threshold
        return mask.fillna(False)


def hypotheses(frame: pd.DataFrame, found: list[dict]) -> list[Rule]:
    """분석 결과를 **기계적으로** 규칙 후보로 바꾼다. 손으로 고르지 않는다."""
    out: list[Rule] = []
    seen: set[tuple] = set()
    for split in found:
        if split["vsAll"] >= 0:              # 평균보다 잘 맞은 칸은 기권 대상이 아니다
            continue
        column = f"c_{split['condition']}"
        if column not in frame.columns:
            continue
        # 못 맞힌 칸이 아래쪽이면 '그 위 경계보다 작으면 기권', 위쪽이면 그 반대.
        if split["bucket"] == "낮음":
            rule = Rule(split["condition"], "<", split["high"])
        elif split["bucket"] == "높음":
            rule = Rule(split["condition"], ">", split["low"])
        else:
            continue
        key = (rule.condition, rule.op, round(rule.threshold, 6))
        if key in seen:
            continue
        seen.add(key)
        out.append(rule)
    return out


def value_of(frame: pd.DataFrame, rule: Rule | None) -> dict:
    """규칙을 적용했을 때 남는 판의 성적.

    점수는 `(적중 - 0.5) × √남은판수`. 적중률만 보면 두 판만 남기고 100% 를 만드는
    규칙이 이긴다 — 표본 수로 눌러야 한다.
    """
    called = frame[frame["direction_hit"].notna()]
    if called.empty:
        return {"n": 0, "score": float("-inf")}
    kept = called if rule is None else called[~rule.abstains(called)]
    if len(kept) < MIN_BUCKET:
        return {"n": int(len(kept)), "score": float("-inf")}
    hit = float(kept["direction_hit"].mean())
    return {
        "n": int(len(kept)),
        "coverage": round(len(kept) / len(called), 4),
        "directionHit": round(hit, 4),
        "errorAtr": round(float(kept["error_atr"].mean()), 4),
        "score": round((hit - 0.5) * math.sqrt(len(kept)), 4),
    }


# 최종 구간을 몇 라운드마다 한 번만 열어 본다. 매 라운드 들여다보면 그건 더 이상
# 최종 구간이 아니다 — 보는 횟수만큼 그 숫자도 닳는다. 본 횟수를 기록에 남긴다.
HOLDOUT_EVERY = 10


def search_rules(frame: pd.DataFrame, peek: bool = False) -> dict:
    """탐색 구간에서 규칙을 찾고, **뒤 구간**에서 확인한다.

    같은 데이터에서 찾고 같은 데이터로 자랑하면 전부 거짓이다. 그래서 시간으로
    자른다 — 규칙이 앞 구간의 우연이면 뒤 구간에서 무너진다.
    """
    called = frame[frame["direction_hit"].notna()].sort_values("origin_ts")
    if len(called) < MIN_BUCKET * 6:
        return {"ready": False, "reason": f"판이 {len(called)}개뿐 — 아직 나눌 수 없다"}

    a = int(len(called) * 0.6)
    b = int(len(called) * 0.8)
    discover, confirm, holdout = called.iloc[:a], called.iloc[a:b], called.iloc[b:]

    base_discover = value_of(discover, None)
    candidates = hypotheses(discover, splits(discover))
    scored = []
    for rule in candidates:
        found = value_of(discover, rule)
        if found["score"] > base_discover["score"] + PROMOTE_MARGIN:
            scored.append((rule, found))
    scored.sort(key=lambda pair: -pair[1]["score"])

    # 탐색에서 이긴 것들을 **확인 구간**에 그대로 들이민다.
    survivors = []
    base_confirm = value_of(confirm, None)
    for rule, found in scored[:20]:
        checked = value_of(confirm, rule)
        if checked["score"] > base_confirm["score"] + PROMOTE_MARGIN:
            survivors.append({"rule": asdict(rule), "label": rule.label,
                              "discover": found, "confirm": checked})

    result = {
        "ready": True,
        "tried": len(candidates),            # 세운 가설의 수. 숨기지 않는다.
        "wonDiscover": len(scored),
        "survivedConfirm": len(survivors),
        "baseDiscover": base_discover,
        "baseConfirm": base_confirm,
        "survivors": survivors[:5],
    }
    # 최종 구간은 **살아남은 게 있고 열어 볼 차례일 때만**, 최고 하나만.
    if survivors and peek:
        best = Rule(**survivors[0]["rule"])
        # **최종 구간에서 실제로 잰 규칙을 같이 들고 다닌다.** 안 그러면 나중에
        # 확인 구간 1위(그때그때 바뀐다)를 내보내면서 성적은 다른 규칙의 것을 붙이게 된다.
        result["holdout"] = {"base": value_of(holdout, None),
                             "withRule": value_of(holdout, best),
                             "rule": asdict(best), "label": best.label}
    return result


# --- 기록 ----------------------------------------------------------------

def append_verdicts(verdicts: list[Verdict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with VERDICTS.open("a", encoding="utf-8") as handle:
        for v in verdicts:
            handle.write(json.dumps(asdict(v), ensure_ascii=False) + "\n")


def load_verdicts() -> list[Verdict]:
    if not VERDICTS.is_file():
        return []
    out = []
    for line in VERDICTS.read_text(encoding="utf-8").splitlines():
        try:
            out.append(Verdict(**json.loads(line)))
        except (ValueError, TypeError):
            continue
    return out


def load_state() -> dict:
    if STATE.is_file():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {"rounds": 0, "seen": {}, "history": []}


def save_state(state: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")


def write_gate(state: dict, analysis: dict) -> None:
    """찾은 것을 **실제 예측에 쓰는 곳**으로 넘긴다.

    최종 구간에서까지 확인된 규칙 하나만 쓴다. 확인 구간까지만 이긴 규칙은 안 쓴다 —
    거기서 이긴 것들은 수십 개고, 그중 하나는 우연히 이긴다.
    """
    holdout = state.get("lastHoldout")
    if not holdout:
        return
    base = holdout.get("base", {}).get("directionHit")
    ruled = holdout.get("withRule", {}).get("directionHit")
    if base is None or ruled is None or ruled <= base:
        # 최종 구간에서 못 이겼으면 아무것도 안 내보낸다. 빈 파일이 낫다.
        GATE.write_text(json.dumps({"updated": now(), "rule": None,
                                    "reason": "최종 구간에서 못 이겼다"},
                                   ensure_ascii=False, indent=2) + NEWLINE, encoding="utf-8")
        return
    # 최종 구간에서 잰 그 규칙만 내보낸다.
    rule = holdout.get("rule")
    if not rule:
        return
    GATE.write_text(json.dumps({
        "updated": now(),
        "rule": rule,
        "label": holdout.get("label"),
        "holdout": {"withoutRule": base, "withRule": ruled,
                    "n": holdout.get("withRule", {}).get("n"),
                    "coverage": holdout.get("withRule", {}).get("coverage")},
        "holdoutLooks": state.get("holdoutLooks", 0),
        "trials": analysis.get("tried"),
    }, ensure_ascii=False, indent=2) + NEWLINE, encoding="utf-8")


def write_summary(state: dict, frame: pd.DataFrame, analysis: dict,
                  found: list[dict]) -> None:
    total = overall(frame)
    lines = [
        "# 자율 학습 — 예측하고, 맞았는지 보고, 왜 틀렸는지 판다",
        "",
        "과거 시점에 서서 예측하고(그 시점 이후는 안 본다), 지평이 지난 뒤 실제와 맞춘 다음,",
        "**맞은 판과 틀린 판을 조건별로 갈라** 본다. 갈라진 조건은 '이 조건이면 말을 안 한다'는",
        "기권 규칙으로 바뀌고, 규칙은 **찾은 구간이 아니라 뒤 구간**에서 확인한다.",
        "",
        f"- 마지막 실행: `{now()}`",
        f"- 라운드 {state.get('rounds', 0)}회 · 쌓인 판 **{total.get('n', 0):,}개**",
        "",
        "## 전체 성적",
        "",
        "| | 값 |",
        "|---|---|",
        f"| 방향 적중 | {total.get('directionHit', float('nan')) * 100:.1f}% "
        f"({total.get('directionN', 0):,}판) |",
        f"| 80% 밴드 적중 | {total.get('bandHit', float('nan')) * 100:.1f}% |",
        f"| 오차 / ATR | {total.get('errorAtr', float('nan')):.3f} |",
        f"| 아무 말 안 했을 때 오차 | {total.get('baselineErrorAtr', float('nan')):.3f} |",
        "",
        "방향 적중은 **실제가 0.25 ATR 이상 움직인 판만** 센다. 0 근처에서는 부호가",
        "동전던지기라 그걸 넣으면 성적이 실제보다 좋아 보인다.",
        "",
    ]

    if found:
        lines += ["## 어디서 맞고 어디서 틀리나", "",
                  "| 조건 | 칸 | 판 | 방향적중 | 전체 대비 |", "|---|---|---|---|---|"]
        for s in found[:14]:
            lines.append(f"| {s['condition']} | {s['bucket']} | {s['n']} | "
                         f"{s['directionHit'] * 100:.1f}% | {s['vsAll'] * 100:+.1f}%p |")
        lines.append("")

    if analysis.get("ready"):
        lines += [
            "## 기권 규칙 찾기",
            "",
            f"- 세운 가설 **{analysis['tried']}개** · 탐색에서 이긴 것 {analysis['wonDiscover']}개",
            f"  · 확인 구간까지 살아남은 것 **{analysis['survivedConfirm']}개**",
            "",
            "가설 수를 같이 적는 이유: 수백 개를 세우면 그중 하나는 반드시 이긴다.",
            "",
        ]
        if analysis.get("survivors"):
            lines += ["| 규칙 | 탐색 적중(남긴 비율) | 확인 적중(남긴 비율) |",
                      "|---|---|---|"]
            for s in analysis["survivors"]:
                d, c = s["discover"], s["confirm"]
                lines.append(f"| {s['label']} | {d['directionHit'] * 100:.1f}% "
                             f"({d['coverage'] * 100:.0f}%) | {c['directionHit'] * 100:.1f}% "
                             f"({c['coverage'] * 100:.0f}%) |")
            lines.append("")
        if analysis.get("holdout"):
            h = analysis["holdout"]
            base, with_rule = h["base"], h["withRule"]
            lines += [
                "### 한 번도 안 본 구간에서",
                "",
                f"규칙: **{h['label']}**",
                "",
                "| | 방향 적중 | 판 |",
                "|---|---|---|",
                f"| 규칙 없이 | {base.get('directionHit', float('nan')) * 100:.1f}% | {base.get('n', 0)} |",
                f"| 규칙 적용 | {with_rule.get('directionHit', float('nan')) * 100:.1f}% "
                f"| {with_rule.get('n', 0)} |",
                "",
                f"이 구간을 지금까지 **{state.get('holdoutLooks', 0)}번** 열어 봤다. "
                "볼 때마다 이 숫자도 조금씩 닳는다 — 그래서 횟수를 같이 적는다.",
                "나머지 표는 전부 규칙을 찾는 데 쓴 구간이라 성적으로 읽으면 안 된다.",
                "",
            ]
    else:
        lines += ["## 기권 규칙 찾기", "", analysis.get("reason", "아직 판이 모자라다"), ""]

    if state.get("history"):
        lines += ["## 라운드 기록", "", "| 라운드 | 대상 | 판 | 방향적중 | 초 |",
                  "|---|---|---|---|---|"]
        for h in state["history"][-20:]:
            hit = h.get("directionHit")
            shown = "—" if hit is None or not np.isfinite(hit) else f"{hit * 100:.1f}%"
            lines.append(f"| {h['round']} | `{h['target']}` | {h['verdicts']} | "
                         f"{shown} | {h['seconds']:.0f} |")
        lines.append("")

    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- 돌리기 --------------------------------------------------------------

def pick(state: dict, pool: list[Target]) -> Target:
    """다음 대상. 덜 본 것부터 — 한 자리만 파면 그 자리의 우연을 배운다."""
    seen = state.get("seen", {})
    return min(pool, key=lambda t: (seen.get(t.key, 0), t.key))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=8.0)
    parser.add_argument("--origins", type=int, default=36)
    args = parser.parse_args()

    budget_end = time.time() + args.hours * 3600
    state = load_state()
    pool = targets()
    print(f"자율 학습 시작 · 예산 {args.hours}시간 · 대상 {len(pool)}종 · "
          f"origin {args.origins}개/라운드")

    while time.time() < budget_end:
        target = pick(state, pool)
        state["rounds"] = state.get("rounds", 0) + 1
        state.setdefault("seen", {})[target.key] = state["seen"].get(target.key, 0) + 1
        started = time.time()
        left = (budget_end - started) / 60
        print(f"\n[라운드 {state['rounds']}] {target.key} · 남은 {left:.0f}분", flush=True)

        try:
            verdicts = await play(target, args.origins, budget_end)
        except Exception:                                          # noqa: BLE001
            traceback.print_exc()
            verdicts = []

        if verdicts:
            append_verdicts(verdicts)
        seconds = time.time() - started

        everything = frame_of(load_verdicts())
        round_frame = frame_of(verdicts)
        round_score = overall(round_frame)
        state.setdefault("history", []).append({
            "round": state["rounds"], "target": target.key, "at": now(),
            "verdicts": len(verdicts), "seconds": round(seconds, 1),
            "directionHit": round_score.get("directionHit"),
        })
        print(f"  판 {len(verdicts)}개 · 방향 "
              f"{round_score.get('directionHit', float('nan')) * 100:.1f}% "
              f"· 누적 {len(everything):,}판 · {seconds:.0f}초", flush=True)

        found = splits(everything)
        peek = state["rounds"] % HOLDOUT_EVERY == 0
        analysis = search_rules(everything, peek=peek)
        if analysis.get("holdout"):
            state["holdoutLooks"] = state.get("holdoutLooks", 0) + 1
            # 마지막으로 본 결과를 들고 있는다. 안 그러면 요약이 열 라운드마다 비어 버린다.
            state["lastHoldout"] = analysis["holdout"]
        elif state.get("lastHoldout"):
            analysis["holdout"] = state["lastHoldout"]
        if analysis.get("ready"):
            print(f"  가설 {analysis['tried']}개 → 탐색 {analysis['wonDiscover']} "
                  f"→ 확인 {analysis['survivedConfirm']}", flush=True)
            if analysis.get("holdout"):
                h = analysis["holdout"]
                print(f"  최종구간: 규칙없이 "
                      f"{h['base'].get('directionHit', float('nan')) * 100:.1f}% → "
                      f"{h['withRule'].get('directionHit', float('nan')) * 100:.1f}% "
                      f"({h['label']})", flush=True)

        state["analysis"] = analysis
        state["overall"] = overall(everything)
        save_state(state)
        write_gate(state, analysis)
        write_summary(state, everything, analysis, found)

    print(f"\n예산 종료 · 라운드 {state.get('rounds', 0)}회 · "
          f"판 {state.get('overall', {}).get('n', 0):,}개")


if __name__ == "__main__":
    asyncio.run(main())
