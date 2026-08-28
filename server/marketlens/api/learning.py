"""자동 학습이 남긴 것을 읽는다.

`scripts/daily.py` 가 매일 챔피언을 다시 굽고 `learning/champions.json` 에 그 설정과
성적을 적는다. 서버는 그걸 읽기만 한다 — 여기서 학습을 돌리지 않는다.

모델 자체는 `store_data/models` 에 있고 이름이 `model_name()` 과 같아서, 자동 학습이
돌면 그날부터 예측이 저절로 새 모델을 쓴다. 이 파일이 하는 일은 **화면에 근거를
보태는 것**이다: 이 종목·봉이 실제로 얼마나 학습됐는지, 마지막으로 언제 봤는지,
몇 번 시험한 끝에 나온 숫자인지.

`MODEL_DIR` 과 같이 저장소 뿌리 기준 상대 경로다. 서버는 뿌리에서 띄운다.
"""
from __future__ import annotations

import json
from pathlib import Path

# 이 PC 에서 돈 기록이 있으면 그쪽이 맞다 — 저장소의 `learning/` 은 GitHub Actions 가
# 쓴 것이고, 여기 모델은 PC 가 구운 것이다. 순서를 뒤집으면 화면의 성적이 실제로 서빙
# 중인 모델과 어긋난다.
# 저장소 뿌리 기준 **절대경로**. 상대경로로 두면 서버를 뿌리 밖에서 띄웠을 때
# 조용히 못 찾아 '아직 안 쟀다'가 뜬다 — 원인이 안 보이는 종류의 고장이다.
_ROOT = Path(__file__).resolve().parents[3]
DIRS = (_ROOT / "learning-local", _ROOT / "learning")


def _dir() -> Path | None:
    return next((d for d in DIRS if (d / "champions.json").is_file()), None)


def _read() -> dict:
    found = _dir()
    if found is None:
        return {}
    try:
        return json.loads((found / "champions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # 파일이 깨졌다고 화면이 막히면 안 된다. 자동 학습은 부가 정보다.
        return {}


def key(provider: str, symbol: str, timeframe: str) -> str:
    return f"{provider}:{symbol}:{timeframe}"


def champion(provider: str, symbol: str, timeframe: str) -> dict | None:
    """이 종목·봉에 대해 자동 학습이 찾아 둔 최선. 없으면 None."""
    return _read().get("champions", {}).get(key(provider, symbol, timeframe))


def note(provider: str, symbol: str, timeframe: str) -> str | None:
    """`learnable_note` 에 덧붙일 한 줄.

    일반적인 '이 봉은 이 범위에서 된다' 보다 **이 종목에서 실제로 잰 값**이 낫다.
    시험 횟수를 같이 적는다 — 많이 시험해서 얻은 양수는 그만큼 덜 믿을 값이다.
    """
    record = champion(provider, symbol, timeframe)
    if not record or record.get("skill") is None:
        return None
    config = record.get("config", {})
    verdict = "기준선을 넘었다" if record.get("learned") else "기준선을 못 넘었다"
    return (f"자동 학습이 이 종목·봉을 {record.get('trials', 0)}번 시험했고, "
            f"지금 최선은 지평 {config.get('horizon')}봉·창 {config.get('window')}"
            f"에서 skill {record['skill']:+.4f} — {verdict}"
            f" (마지막 학습 {str(record.get('updated', ''))[:10]}).")


def defaults(provider: str, symbol: str, timeframe: str) -> dict:
    """수동 학습의 출발점. 자동 학습이 이미 찾아 둔 설정에서 시작하는 게 낫다."""
    record = champion(provider, symbol, timeframe)
    return dict(record.get("config", {})) if record else {}


def recent(limit: int = 20) -> list[dict]:
    """최근 실행 기록. 승격이 언제 일어났는지 화면에서 보이게."""
    found = _dir()
    if found is None or not (found / "log.jsonl").is_file():
        return []
    try:
        lines = (found / "log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return list(reversed(out))


def summary() -> dict:
    """`GET /api/learning` 이 돌려주는 것 전부."""
    raw = _read()
    champions = raw.get("champions", {})
    return {
        "available": bool(champions),
        "updated": raw.get("updated"),
        "promoteMargin": raw.get("promoteMargin"),
        "tracked": len(champions),
        "learned": sum(1 for c in champions.values() if c.get("learned")),
        "trials": sum(int(c.get("trials", 0)) for c in champions.values()),
        "promotions": sum(int(c.get("promotions", 0)) for c in champions.values()),
        "champions": [
            {"target": target, **record}
            for target, record in sorted(
                champions.items(),
                key=lambda kv: -(kv[1].get("skill") if kv[1].get("skill") is not None else -9.0),
            )
        ],
        "recent": recent(),
        "note": "승격은 퍼징 워크포워드 blendSkill 로만 판정한다. 시험 횟수가 클수록 "
                "'이겼다'를 곧이곧대로 읽으면 안 된다 — 같은 데이터로 여러 번 시험하면 "
                "그중 하나는 반드시 이긴다.",
    }
