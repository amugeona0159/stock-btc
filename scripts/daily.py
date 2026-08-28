"""매일 스스로 학습.

같은 설정을 매일 다시 학습만 하면 "배운다"고 할 수 없다. 여기서는 **챔피언-도전자**로
설정을 스스로 탐색한다:

1. 오늘 돌릴 대상을 고른다        ← 선택과 집중
2. 챔피언을 새 데이터로 재학습     ← 시장이 변하니 매일
3. 도전자를 만들어 같이 학습       ← 설정을 하나씩 흔들어 본다
4. 같은 검증으로 재고, **마진 이상 이길 때만** 승격
5. 기록을 남긴다

**이 구조는 놔두면 과최적화 기계가 된다.** 같은 데이터로 수백 번 시험하면 그중 하나는
반드시 이긴다. 그래서 세 가지를 박아 뒀다:

- **승격에 마진을 둔다**(`PROMOTE_MARGIN`). 잡음으로 매일 갈아끼우지 않게.
- **시험 횟수를 로그에 남긴다.** 다중검정 문제를 숨기지 않는다.
- **승격 판정은 퍼징 워크포워드로만.** as-of 는 따로 재서 기록만 하고 승격에 안 쓴다.

돌리는 법:
    .venv/Scripts/python scripts/daily.py --budget 6
    .venv/Scripts/python scripts/daily.py --budget 2 --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

# 기록을 어디에 남길지. 저장소에 올라가는 `learning/` 은 **GitHub Actions 가 쓴다.**
# 내 PC 는 `MARKET_LENS_LEARNING=learning-local` 로 옮겨 쓴다(gitignore) — 둘이 같은
# 파일에 쓰면 매일 아침 pull 이 충돌한다. 대신 PC 쪽은 토스(국내주식)까지 학습한다.
LEARNING = Path(os.environ.get("MARKET_LENS_LEARNING") or ROOT / "learning")
if not LEARNING.is_absolute():
    LEARNING = ROOT / LEARNING
CHAMPIONS = LEARNING / "champions.json"
LOG = LEARNING / "log.jsonl"
# 저장소용 요약은 docs 에, PC 쪽 요약은 자기 폴더 안에 둔다.
SUMMARY = ROOT / "docs" / "LEARNING.md" if LEARNING.name == "learning" else LEARNING / "LEARNING.md"

# 도전자가 이만큼은 이겨야 승격한다. 0 으로 두면 잡음이 매일 챔피언을 갈아치운다.
PROMOTE_MARGIN = 0.002
# 마지막 실행이 오래됐을수록 순위를 올린다. 되는 자리에 시간이 몰리되, 시장이 변하면
# 지금 지는 자리도 언젠가 다시 보게 하려는 것. 완전히 버리지는 않는다.
STALE_BONUS_PER_DAY = 0.004
# 한 번에 흔드는 손잡이는 하나뿐이다. 여러 개를 같이 바꾸면 뭐가 이겼는지 모른다.
# `horizon` 은 봉마다 범위가 달라 아래에서 따로 만든다(LEARNABLE 안에서만 흔든다).
KNOBS = {
    "window": [24, 32, 48, 72, 96],
    "neighbours": [10, 20, 30],
    "folds": [3, 4, 5],
    "peer_count": [4, 6, 8, 11],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Config:
    horizon: int = 10
    window: int = 48
    neighbours: int = 20
    folds: int = 4
    # 스윕이 이긴 자리는 12종목 풀링이었다. 적게 시작하면 첫날부터 불리하다.
    peer_count: int = 11


@dataclass
class Target:
    provider: str
    symbol: str
    timeframe: str

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.symbol}:{self.timeframe}"

    @property
    def model(self) -> str:
        """API 가 실제로 읽는 이름(`routes.model_name`). 여기에 덮어써야 그날부터
        화면이 새 모델을 쓴다."""
        return f"{self.provider}-{self.symbol}-{self.timeframe}".lower()


def horizon_options(timeframe: str) -> list[int]:
    """그 봉에서 학습이 통하는 범위 안의 지평만. 밖으로 나가면 어차피 기준선이 이긴다."""
    from marketlens.api.routes import LEARNABLE

    lo, hi = LEARNABLE.get(timeframe, (5, 20))
    return sorted({lo, (lo + hi) // 2, hi})


def variant(config: Config, timeframe: str, rng: random.Random) -> tuple[Config, str]:
    """손잡이 하나만 흔든 도전자."""
    knobs = dict(KNOBS)
    knobs["horizon"] = horizon_options(timeframe)
    candidates = [k for k, values in knobs.items()
                  if [v for v in values if v != getattr(config, k)]]
    if not candidates:
        return Config(**asdict(config)), "변화 없음"
    knob = rng.choice(candidates)
    current = getattr(config, knob)
    value = rng.choice([v for v in knobs[knob] if v != current])
    return Config(**{**asdict(config), knob: value}), f"{knob} {current}→{value}"


@dataclass
class Record:
    """챔피언 하나의 현재 상태. `learning/champions.json` 에 그대로 들어간다."""

    config: dict = field(default_factory=lambda: asdict(Config()))
    skill: float | None = None
    learned: bool = False
    rows: int = 0
    symbols: list[str] = field(default_factory=list)
    updated: str = ""
    # 이 대상에 지금까지 몇 번 시험했나. 많을수록 '이긴' 결과를 의심해야 한다.
    trials: int = 0
    promotions: int = 0


def load_state() -> dict[str, Record]:
    if not CHAMPIONS.is_file():
        return {}
    raw = json.loads(CHAMPIONS.read_text(encoding="utf-8"))
    out: dict[str, Record] = {}
    for key, value in raw.get("champions", {}).items():
        fields = {k: v for k, v in value.items() if k in Record.__dataclass_fields__}
        # 손잡이가 늘거나 줄어도 옛 파일이 그대로 읽히게.
        fields["config"] = asdict(Config(**{
            k: v for k, v in (fields.get("config") or {}).items()
            if k in Config.__dataclass_fields__
        }))
        out[key] = Record(**fields)
    return out


def save_state(state: dict[str, Record]) -> None:
    LEARNING.mkdir(parents=True, exist_ok=True)
    CHAMPIONS.write_text(json.dumps({
        "updated": _now(),
        "promoteMargin": PROMOTE_MARGIN,
        "champions": {k: asdict(v) for k, v in sorted(state.items())},
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_log(entry: dict) -> None:
    LEARNING.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def targets() -> list[Target]:
    """추적 대상.

    되는 자리(주식·지수 일봉, 암호화폐 시간봉)를 중심에 두되 안 되는 자리도 남긴다 —
    "안 된다"도 매일 다시 확인해야 하는 사실이고, 시장이 변하면 뒤집힌다.
    """
    return [
        Target("binance", "BTCUSDT", "1h"),
        Target("binance", "BTCUSDT", "1d"),
        Target("binance", "ETHUSDT", "1h"),
        Target("binance", "ETHUSDT", "1d"),
        Target("binance", "SOLUSDT", "1h"),
        Target("binance", "BTCUSDT", "15m"),
        Target("yahoo", "AAPL", "1d"),
        Target("yahoo", "MSFT", "1d"),
        Target("yahoo", "NVDA", "1d"),
        Target("yahoo", "^GSPC", "1d"),
        Target("yahoo", "AAPL", "1h"),
    ]


def priority(target: Target, record: Record | None, today: float) -> float:
    """오늘 돌릴 순서. 잘 되는 자리 + 오래 안 본 자리가 앞으로 온다."""
    if record is None or not record.updated:
        return 10.0                       # 한 번도 안 본 것은 무조건 먼저
    try:
        last = datetime.fromisoformat(record.updated).timestamp()
    except ValueError:
        return 10.0
    stale_days = max(0.0, (today - last) / 86400.0)
    return (record.skill or 0.0) + STALE_BONUS_PER_DAY * stale_days


async def train_once(target: Target, config: Config, name: str) -> dict | None:
    """한 설정으로 학습하고 성적을 돌려준다. 실패하면 None."""
    from marketlens import events as event_layer
    from marketlens.api.routes import PEERS, TRAIN_BARS, TRAIN_MAX, _symbol_data
    from marketlens.forecast.ml import model as ml
    from marketlens.providers import get as get_provider

    info = get_provider(target.provider).info
    limit = min(TRAIN_MAX, TRAIN_BARS.get(target.timeframe, 3000))
    peers = [p for p in PEERS.get(target.provider, ())
             if p.upper() != target.symbol.upper()][: config.peer_count]

    datasets = []
    for symbol in [target.symbol] + peers:
        try:
            data, _ = await _symbol_data(
                target.provider, symbol, target.timeframe, limit,
                info.market, event_layer.DEFAULT_SOURCES, True,
            )
        except Exception as exc:  # noqa: BLE001 - 동료 하나가 빠져도 학습은 계속한다
            if symbol.upper() == target.symbol.upper():
                print(f"    {target.key}: 시세 실패 — {str(exc)[:60]}")
                return None
            continue
        datasets.append(data)
    if not datasets:
        return None

    try:
        return await asyncio.to_thread(
            ml.train, datasets, name, config.horizon, config.window,
            config.folds, target.timeframe, config.neighbours,
        )
    except Exception as exc:  # noqa: BLE001 - 표본 부족 등은 오늘 그냥 건너뛴다
        print(f"    {target.key}: 학습 실패 — {str(exc)[:60]}")
        return None


def skill_of(report: dict) -> float:
    """판정 숫자는 하나 — 기준선과 섞은 결과가 그 기준선을 넘는가."""
    return float(report.get("blendSkill", {}).get(str(report.get("horizon")), 0.0))


def adopt(trial: str, champion: str) -> None:
    """도전자가 구운 모델을 챔피언 자리로 옮긴다."""
    from marketlens.forecast.ml.model import MODEL_DIR

    for suffix in (".pkl", ".json"):
        source = MODEL_DIR / f"{trial}{suffix}"
        if source.is_file():
            shutil.move(str(source), str(MODEL_DIR / f"{champion}{suffix}"))


def discard(trial: str) -> None:
    from marketlens.forecast.ml.model import MODEL_DIR

    for suffix in (".pkl", ".json"):
        (MODEL_DIR / f"{trial}{suffix}").unlink(missing_ok=True)


async def run(budget: int, dry_run: bool, seed: int) -> list[dict]:
    rng = random.Random(seed)
    state = load_state()
    today = time.time()

    ordered = sorted(targets(), key=lambda t: -priority(t, state.get(t.key), today))
    entries: list[dict] = []

    for target in ordered[:budget]:
        record = state.get(target.key) or Record()
        champion = Config(**record.config)
        started = time.time()

        # 챔피언은 제자리에 덮어쓴다 — 오늘 데이터까지 반영된 게 늘 서빙된다.
        champion_report = await train_once(target, champion, target.model)
        if champion_report is None:
            continue
        champion_skill = skill_of(champion_report)

        # 도전자는 옆자리에서 굽는다. 져도 챔피언 파일을 건드리지 않게.
        trial_name = f"{target.model}-trial"
        challenger, change = variant(champion, target.timeframe, rng)
        challenger_report = await train_once(target, challenger, trial_name)
        challenger_skill = skill_of(challenger_report) if challenger_report else None

        promoted = (
            challenger_skill is not None
            and challenger_skill > champion_skill + PROMOTE_MARGIN
        )
        if promoted and not dry_run:
            adopt(trial_name, target.model)
        else:
            discard(trial_name)

        winner = challenger if promoted else champion
        winner_report = challenger_report if promoted else champion_report
        winner_skill = challenger_skill if promoted else champion_skill

        entry = {
            "at": _now(),
            "target": target.key,
            "championSkill": round(champion_skill, 5),
            "challengerSkill": None if challenger_skill is None else round(challenger_skill, 5),
            "change": change,
            "promoted": bool(promoted),
            "margin": PROMOTE_MARGIN,
            # 이 대상에 지금까지 몇 번 시험했나. 이긴 결과를 읽을 때 같이 봐야 한다.
            "trials": record.trials + (2 if challenger_report else 1),
            "config": asdict(winner),
            "rows": winner_report.get("rows"),
            "learned": bool(winner_report.get("learnedSomething")),
            "seconds": round(time.time() - started, 1),
            "dryRun": dry_run,
        }
        entries.append(entry)
        shown = "  없음  " if challenger_skill is None else f"{challenger_skill:+.4f}"
        print(f"  {target.key:26s} {champion_skill:+.4f} vs {shown}  "
              f"({change:20s}) → {'승격' if promoted else '유지'}  {entry['seconds']:.0f}s")

        if not dry_run:
            state[target.key] = Record(
                config=asdict(winner), skill=round(winner_skill, 5),
                learned=bool(winner_report.get("learnedSomething")),
                rows=int(winner_report.get("rows", 0)),
                symbols=list(winner_report.get("symbols", [])),
                updated=_now(), trials=entry["trials"],
                promotions=record.promotions + int(promoted),
            )
            append_log(entry)

    if not dry_run:
        save_state(state)
        write_summary(state, entries)
    return entries


def _row(key: str, record: Record) -> str:
    config = record.config
    setting = (f"지평{config['horizon']}·창{config['window']}·이웃{config['neighbours']}"
               f"·폴드{config['folds']}·동료{config['peer_count']}")
    skill = "—" if record.skill is None else f"{record.skill:+.4f}"
    return (f"| `{key}` | {skill} | {'O' if record.learned else 'X'} | {setting} | "
            f"{record.rows:,} | {record.trials} | {record.promotions} |")


def write_summary(state: dict[str, Record], entries: list[dict]) -> None:
    """사람이 읽는 요약. 저장소 첫 화면에서 진척이 바로 보이게."""
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    learned = [r for r in state.values() if r.learned]
    lines = [
        "# 자동 학습 기록",
        "",
        "매일 한 번, 챔피언을 새 데이터로 다시 학습하고 설정을 하나 흔든 도전자와 겨룬다.",
        f"도전자가 **{PROMOTE_MARGIN:+.3f} 이상** 이길 때만 승격한다 — 마진이 없으면",
        "잡음으로 매일 모델이 바뀐다.",
        "",
        f"- 마지막 실행: `{_now()}`",
        f"- 추적 중인 대상: {len(state)}개 · 그중 기준선을 넘은 것 **{len(learned)}개**",
        "",
        "## 지금의 챔피언",
        "",
        "| 대상 | skill | 기준선 넘음 | 설정 | 표본 | 시험 | 승격 |",
        "|---|---|---|---|---|---|---|",
    ]
    lines += [_row(key, record) for key, record in sorted(
        state.items(),
        key=lambda kv: -(kv[1].skill if kv[1].skill is not None else -9.0),
    )]

    if entries:
        lines += ["", "## 이번 실행", "", "| 대상 | 챔피언 | 도전자 | 바꾼 것 | 결과 |",
                  "|---|---|---|---|---|"]
        for e in entries:
            challenger = "없음" if e["challengerSkill"] is None else f"{e['challengerSkill']:+.4f}"
            lines.append(f"| `{e['target']}` | {e['championSkill']:+.4f} | {challenger} | "
                         f"{e['change']} | {'**승격**' if e['promoted'] else '유지'} |")

    lines += [
        "",
        "## 이 숫자를 읽을 때",
        "",
        "- `skill` 은 **변동성 기준선 대비 개선율**이다. 0 이면 기준선과 같고, 음수면 더 나쁘다.",
        "- 양수라고 방향을 맞힌다는 뜻이 아니다. 이득의 대부분은 밴드의 폭과 모양에서 나온다.",
        "- `시험` 횟수가 클수록 '이긴' 결과를 의심해야 한다. 같은 데이터로 수백 번 시험하면",
        "  그중 하나는 반드시 이긴다 — 그래서 이 숫자를 숨기지 않고 같이 적는다.",
        "- 승격 판정은 퍼징 워크포워드로만 한다. as-of 검증(`scripts/asof.py`)은 따로 재서",
        "  기록만 하고 승격에는 쓰지 않는다.",
        "- 국내주식(토스)은 IP 제한 때문에 GitHub Actions 에서 못 돈다. PC 쪽에서만 학습된다.",
    ]
    SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=int, default=6, help="오늘 돌릴 대상 수")
    parser.add_argument("--dry-run", action="store_true", help="승격·기록 없이 한 바퀴")
    parser.add_argument("--seed", type=int, default=0, help="0 이면 날짜로 정한다")
    args = parser.parse_args()

    seed = args.seed or int(datetime.now(timezone.utc).strftime("%Y%m%d"))
    print(f"자동 학습 · 예산 {args.budget}개 · seed {seed}"
          f"{' · 실행만(기록 안 함)' if args.dry_run else ''}")
    entries = await run(args.budget, args.dry_run, seed)
    promoted = sum(1 for e in entries if e["promoted"])
    print(f"\n돌린 대상 {len(entries)}개 · 승격 {promoted}개")


if __name__ == "__main__":
    asyncio.run(main())
