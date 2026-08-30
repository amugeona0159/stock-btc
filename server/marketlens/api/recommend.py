"""아침 추천을 읽어 내보낸다.

여기서 계산하지 않는다. `scripts/recommend.py` 가 07:30 에 얼려 둔 날짜 파일을 읽을
뿐이다. **POST 를 만들지 않는다** — 요청할 때 계산하는 게 없다는 것이 "하루 한 번,
그날은 안 바뀐다"의 전부고, 몇 분짜리 학습을 HTTP 뒤에 두면 안 된다.

## 두 폴더를 프로바이더 단위로 합친다

`api/screening.py` 와 같은 규칙이다. Actions 는 토스를 못 도니(IP 제한) 국내주식은
PC 쪽에만 있고, 하나만 읽으면 반쪽이 사라진다.

`api/learning.py` 의 챔피언은 일부러 **하나만** 고른다. 거기는 `store_data` 의 모델
파일과 짝이라 반쪽만 가져오면 설정과 모델이 어긋나기 때문이다. 추천은 다르다 —
날짜 파일이 자기 성적 스냅샷을 들고 다녀서 합쳐도 어긋날 수가 없다.
**둘을 "통일"하려다 챔피언 쪽을 망가뜨리지 말 것.**

## 시장마다 자기 날짜를 들고 온다

PC 가 일주일 꺼져 있었으면 국내주식은 8/22 것이 온다. 그때도 **그 날짜가 보이게**
내보낸다 — 사라지지도, 오늘 것인 척하지도 않는다.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ..screen import names
from .learning import DIRS

FOLDER = "recommend"
SCORES = "scores.jsonl"
# 되돌려 돌린 것. **실전과 다른 파일이다** — 섞으면 "과거에 맞았나" 가 못 믿을
# 숫자가 된다. 백필은 origin 을 고를 수 있고 몇 번이든 다시 돌릴 수 있는 반면
# 실전은 그날 한 번뿐이라, 성질이 다른 숫자다.
BACKFILL = "backfill.jsonl"
# 하루 한 건씩 쌓이므로 30건이면 한 달이다. 그 아래는 성적이라고 부르지 않는다.
ENOUGH = 30


# **사람이 사는 단위로 묶는다.** 화면은 시장 이름(바이낸스·토스)이 아니라
# 국내주식·해외주식·코인 셋으로 읽는다 — 어느 거래소를 거쳐 사는지는 그다음 문제고,
# 하나만 골라 보여주면 차트가 BTC 에 서 있는 동안에는 코인 추천만 보인다.
#
# 한 묶음에 프로바이더가 여럿인 건 같은 종목을 여러 곳에서 받아서다(미국주식은
# 토스·야후, 코인은 바이낸스·업비트). **앞에 적은 것이 이긴다.** 토스가 앞인 건
# 한글 이름이 붙어서고, 바이낸스가 앞인 건 추천 모델이 거기서 학습되고 원화 시세는
# 업비트에서 따로 붙기 때문이다(`screen/coins.py`).
GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("kr", "국내주식", ("toss_kr", "kis")),
    ("us", "해외주식", ("toss_us", "yahoo", "us_stock")),
    ("coin", "코인", ("binance", "upbit")),
)


def _dirs() -> list[Path]:
    return [d / FOLDER for d in DIRS]


def _latest(folder: Path) -> dict:
    """그 폴더의 가장 최근 날짜 파일. `latest.json` 같은 포인터는 안 만든다 —
    두 벌이 되는 순간 어긋난다."""
    if not folder.is_dir():
        return {}
    for path in sorted(folder.glob("20*.json"), reverse=True):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return {}


def _merged() -> dict:
    """프로바이더 단위로 합친다. 겹치면 로컬(이 PC)이 이긴다."""
    out: dict[str, dict] = {}
    # `DIRS` 는 로컬이 먼저다. 뒤에 읽은 것이 이기게 하려면 거꾸로 돈다.
    for folder in reversed(_dirs()):
        frozen = _latest(folder)
        for provider, body in (frozen.get("providers") or {}).items():
            out[provider] = {**body, "date": frozen.get("date"),
                             "generatedAt": frozen.get("generatedAt")}
    return out


def _scores(name: str = SCORES) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple] = set()
    for folder in reversed(_dirs()):
        path = folder / name
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            key = (row.get("date"), row.get("provider"), row.get("days"))
            if key in seen:
                continue                      # 두 폴더에 같은 채점이 있으면 한 번만
            seen.add(key)
            rows.append(row)
    return rows


def record(provider: str, days: int) -> dict:
    """지난 추천이 실제로 어땠나.

    **기준선은 후보 전체 평균이다.** 0 과 견주면 고르는 실력이 아니라 시장을 잰다 —
    시장이 다 오른 날 추천도 올랐다는 건 아무 말도 아니다.
    """
    part = [r for r in _scores()
            if r.get("provider") == provider and r.get("days") == days]
    if not part:
        return {"n": 0, "enough": False}
    return {
        "n": len(part),
        "enough": len(part) >= ENOUGH,
        "buyPct": round(float(np.mean([r["buyPct"] for r in part])), 3),
        "universePct": round(float(np.mean([r["universePct"] for r in part])), 3),
        "edgePct": round(float(np.mean([r["edgePct"] for r in part])), 3),
        "winRate": round(float(np.mean([r["edgePct"] > 0 for r in part])), 3),
        "bandHit": round(float(np.mean([r.get("bandHit") or 0 for r in part])), 3),
        "lastScored": max(r.get("scoredAt") or "" for r in part),
    }


def backfill(provider: str, days: int) -> dict:
    """되돌려 본 성적. **실전(`record`)과 절대 합치지 않는다.**

    `scripts/backfill.py` 가 과거 아침에 서서 같은 추천을 뽑고 지평이 지난 뒤 실제와
    맞춘 것이다. 실전 표가 하루 한 줄씩 쌓이는 동안 이쪽은 미리 채워지지만, 대신
    origin 을 내가 고를 수 있고 몇 번이든 다시 돌릴 수 있다 — **그래서 다른 숫자다.**

    `holdout` 은 규칙 튜닝에 안 쓴 마지막 구간이다. 튜닝에 쓴 구간에서 잰 성적은
    자기 답을 보고 만든 성적이라 못 믿으므로, **읽을 값은 holdout 쪽**이다.
    """
    found = [r for r in _scores(BACKFILL)
             if r.get("provider") == provider and r.get("days") == days]
    if not found:
        return {"n": 0}

    # **설정이 다른 줄을 섞지 않는다.** 손잡이가 바뀌면 옛 모델 성적과 새 모델 성적이
    # 한 숫자가 되는데, 그게 정확히 못 믿을 숫자다. 가장 최근에 잰 설정만 남기고
    # 나머지는 세어서 알린다 — 조용히 버리면 판 수가 왜 줄었는지 알 수가 없다.
    newest = max(found, key=lambda r: r.get("scoredAt") or "")
    part = [r for r in found if r.get("config") == newest.get("config")]
    stale = len(found) - len(part)

    def summarise(rows: list[dict]) -> dict:
        if not rows:
            return {"n": 0}
        return {
            "n": len(rows),
            "buyPct": round(float(np.mean([r["buyPct"] for r in rows])), 3),
            "universePct": round(float(np.mean([r["universePct"] for r in rows])), 3),
            "edgePct": round(float(np.mean([r["edgePct"] for r in rows])), 3),
            "winRate": round(float(np.mean([r["edgePct"] > 0 for r in rows])), 3),
            "bandHit": round(float(np.mean([r.get("bandHit") or 0 for r in rows])), 3),
        }

    dates = sorted(r.get("date") or "" for r in part)
    return {
        **summarise(part),
        "holdout": summarise([r for r in part if r.get("holdout")]),
        "from": dates[0], "to": dates[-1],
        # 설정이 바뀌어 안 센 줄. `scripts/backfill.py --restale` 로 다시 잰다.
        "staleRows": stale,
        # 어느 설정으로 잰 것인지. 설정이 바뀌면 옛 줄과 새 줄이 한 숫자에 섞이면
        # 안 되므로, 화면도 무엇을 보고 있는지 알 수 있어야 한다.
        "model": part[-1].get("model"),
    }


def measured() -> dict:
    """실측 성적. **화면에 숫자를 박지 않기 위해** 여기서 읽어 넘긴다.

    `scripts/study.py` 가 쌓은 것을 그대로 쓴다 — 그래야 화면 숫자가 학습 실행과
    절대 어긋나지 않는다.
    """
    for folder in DIRS:
        path = folder / "study" / "state.json"
        if not path.is_file():
            continue
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        overall = state.get("overall") or {}
        if overall:
            return {"directionHit": overall.get("directionHit"),
                    "bandHit": overall.get("bandHit"),
                    "n": overall.get("n"),
                    "directionN": overall.get("directionN")}
    return {}


def today(provider: str, days: int) -> dict:
    """오늘의 추천 한 시장 · 한 지평."""
    body = _merged().get(provider)
    if not body:
        known = sorted(_merged())
        return {"available": False, "providers": known,
                "reason": f"{provider} 의 추천이 없다 — "
                          f"`python scripts/recommend.py --provider {provider}` 로 뽑는다"}

    key = str(days)
    day = (body.get("byDay") or {}).get(key)
    if not day:
        return {"available": False, "date": body.get("date"),
                "reason": f"{days}일 지평은 그날 못 뽑았다"}

    rows = {r["symbol"]: r for r in body.get("candidates", [])}

    def shape(symbol: str) -> dict:
        row = rows.get(symbol, {})
        found = (row.get("byDay") or {}).get(key, {})
        return {
            "symbol": symbol, "last": row.get("last"),
            # 이름과 티커는 **읽을 때 표에서 붙인다**(`screen/names.py`). 얼린 파일에
            # 넣지 않는 이유는, 넣어 두면 표를 고쳐도 옛 파일이 옛 이름을 계속
            # 들고 나오기 때문이다. 표에 없으면 `None` — 지어내지 않는다.
            "name": names.of(symbol), "ticker": names.ticker(symbol),
            # 원화 시세는 **그날 값이라 얼려 둔 것만** 쓴다. `krw` 는 업비트
            # 실거래가지 환율 환산가가 아니다. 옛 파일이나 주식이면 없고, 그때
            # 화면은 티커만 낸다 — 없다고 행을 빼면 그날 추천을 통째로 못 보게 된다.
            "krw": row.get("krw"),
            "expected": found.get("expected"), "band": found.get("band"),
            # `probUp` 은 그 지평 모델의 값이라 지평별로 다르다. 그대로 넘긴다.
            "probUp": found.get("probUp"), "confidence": found.get("confidence"),
            "source": found.get("source"), "abstain": found.get("abstain"),
        }

    return {
        "available": True,
        "provider": provider, "days": days,
        "date": body.get("date"), "generatedAt": body.get("generatedAt"),
        "basedOn": body.get("basedOn"), "staleBars": body.get("staleBars"),
        "buy": [shape(s) for s in day.get("buy", [])],
        "avoid": [shape(s) for s in day.get("avoid", [])],
        # **모델을 하나도 안 썼으면 그 사실이 화면까지 가야 한다.** 그때 순위는
        # 사실상 변동성 순서지 "사라"가 아니다.
        "degenerate": bool(day.get("degenerate")),
        "allNegative": bool(day.get("allNegative")),
        "learned": bool(day.get("learned")),
        "skill": day.get("skill"),
        "modelStale": bool(day.get("modelStale")),
        "candidates": len(rows),
        "record": record(provider, days),
        # **실전과 나란히 두되 합치지 않는다.** 화면이 어느 쪽 숫자인지 밝힌다.
        "backfill": backfill(provider, days),
        "measured": measured(),
        "skipped": body.get("skipped") or [],
    }


def groups(days: int) -> dict:
    """국내주식·해외주식·코인 셋을 한 번에.

    **빠진 묶음을 조용히 지우지 않는다.** 국내주식은 토스가 IP 허용목록을 타서
    Actions 에서 못 돌고, 그래서 PC 가 며칠 꺼져 있으면 통째로 없다. 없는 것을
    안 보여주면 "국내주식은 살 게 없다"로 잘못 읽힌다 — 자리는 남기고 사유를 적는다.
    """
    found = _merged()
    out = []
    for key, name, order in GROUPS:
        provider = next((p for p in order if p in found), None)
        if provider is None:
            out.append({"key": key, "label": name, "available": False,
                        "reason": f"{name} 추천이 아직 없다 — 그 시장을 한 번 뽑아야 한다"})
            continue
        out.append({"key": key, "label": name, **today(provider, days)})
    return {"days": days, "groups": out}


def for_symbol(provider: str, symbol: str) -> dict:
    """이 종목을 **1·2·3일 각각** 어떻게 봤나.

    화면의 「판단」은 지금 선 봉 하나를 보고 답한다. 그런데 아침 추천은 일봉으로
    1~3일을 본 것이라, 둘이 어긋나 보일 때가 있다 — "추천은 사라는데 판단은 팔라네".
    **둘 다 맞고 질문이 다른 것**이고, 그 차이를 사람이 읽으려면 지평별로 어떻게
    달랐는지가 필요하다. 그게 얼린 파일 안에 이미 있으니 꺼내 주기만 하면 된다.

    **여기서 계산하지 않는다.** `today()` 와 같은 파일을 읽을 뿐이라 같은 날 답이
    안 바뀐다.
    """
    body = _merged().get(provider)
    if not body:
        return {"available": False}
    row = next((r for r in body.get("candidates", [])
                if str(r.get("symbol")) == symbol), None)
    if not row:
        return {"available": False}

    ranks = {}
    for key, day in (body.get("byDay") or {}).items():
        buy, avoid = day.get("buy") or [], day.get("avoid") or []
        ranks[key] = ("buy" if symbol in buy else "avoid" if symbol in avoid else "mid")

    days = {}
    for key, found in (row.get("byDay") or {}).items():
        days[key] = {
            "expected": found.get("expected"), "band": found.get("band"),
            "probUp": found.get("probUp"), "confidence": found.get("confidence"),
            # 그날 그 지평에서 사라/사지 말 것 어디에 있었나. 순위는 묶음 안에서만
            # 뜻이 있으므로 기대값보다 이쪽이 읽기 쉽다.
            "side": ranks.get(key, "mid"),
        }
    return {
        "available": bool(days), "provider": provider, "symbol": symbol,
        "date": body.get("date"), "basedOn": body.get("basedOn"),
        "name": names.of(symbol), "ticker": names.ticker(symbol),
        "days": days,
    }


def status() -> dict:
    """어느 시장의 추천이 언제 것으로 있는지."""
    return {name: {"date": body.get("date"), "days": sorted(
        int(d) for d in (body.get("byDay") or {}))}
        for name, body in _merged().items()}
