import { useCallback, useEffect, useState } from "react";

import { positions, recommend, screen } from "../api";
import {
  avoidWords, bandWords, edgeWords, fewHandsWords, groupWords, moveWords, trustWords,
} from "../say";
import { Numbers } from "./Numbers";
import type {
  RecommendGroup,
  RecommendItem,
  ScreenResult,
  SymbolNames,
} from "../types";

// 사용자가 물은 그대로 — 하루·이틀·사흘. 지평마다 **모델이 따로** 있어서 칩을 바꾸면
// 그 지평 모델의 답이 나온다. 다시 뽑는 게 아니라 이미 뽑아 둔 다른 답을 보는 것이다.
const DAYS = [
  { label: "하루 뒤", value: 1 },
  { label: "이틀 뒤", value: 2 },
  { label: "사흘 뒤", value: 3 },
];

/**
 * **아침 추천이 선 봉.** `scripts/recommend.py` 가 `recommend-<시장>-1d-<일수>` 로
 * 굽는다 — 여기를 바꾸려면 그쪽도 같이 바꿔야 한다.
 *
 * 추천 화면이 이 값의 주인이고, `App` 이 가져다 쓴다(종목을 열 때 이 봉으로 맞추고,
 * 종합 판단이 다른 봉을 보고 있으면 그렇다고 알린다). 두 벌로 두면 갈라진다.
 */
export const RECOMMEND_TIMEFRAME = "1d";

/** `이름 티커` 로 읽히게. 표에 없으면 심볼 그대로 — **이름을 지어내지 않는다.** */
function label(symbol: string, names: SymbolNames | undefined): string {
  const found = names?.[symbol];
  return found ? `${found.name} ${found.ticker}` : symbol;
}

function signed(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined || !Number.isFinite(value)
    ? "—"
    : `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

/** 원화. 코인은 값의 자릿수가 천차만별이라(비트코인 1억, 도지 300원) 자리를 고정하면
 *  한쪽은 잘리고 한쪽은 0 만 늘어난다. 1,000원 위로는 소수점을 버린다. */
function won(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "";
  const digits = value >= 1000 ? 0 : value >= 1 ? 1 : 4;
  return `₩${value.toLocaleString("ko-KR", { maximumFractionDigits: digits })}`;
}

/** 그 시장의 돈으로 적은 값. 코인은 업비트 원화 실거래가, 국내주식은 원, 미국주식은 달러.
 *  **환율로 환산하지 않는다** — 곱해서 나온 값에는 살 수도 팔 수도 없다. */
function price(item: RecommendItem, money?: "kr" | "us" | "coin"): string {
  if (money === "coin") return item.krw ? won(item.krw.last) : "";
  if (item.last === null || item.last === undefined || !Number.isFinite(item.last)) return "";
  if (money === "kr") return won(item.last);
  return `$${item.last.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}

function pct(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined || !Number.isFinite(value)
    ? "—"
    : `${(value * 100).toFixed(digits)}%`;
}

// 확신도는 `move_atr` 3분위다 — 모델이 "얼마나 크게 움직인다" 고 했는지의 3등분.
// **숫자를 여기 박지 않는다.** 실측값(그 구간의 방향 적중)은 학습을 다시 돌리면
// 바뀌는데 화면만 옛말을 하게 된다. 뜻은 `say.moveWords` 가 문장으로 말한다.
const CONFIDENCE: Record<string, string> = {
  high: "크게 움직인다고 봤다",
  mid: "평소만큼",
  low: "거의 안 움직인다고 봤다",
};

interface Props {
  provider: string;
  onPick: (provider: string, symbol: string) => void;
  /** 종목 기호 → 한글 이름. `App` 이 부팅 때 받아 둔 표를 그대로 넘긴다. */
  names: SymbolNames;
}

/**
 * **시장을 고르게 하지 않는다.** 예전에는 차트가 선 시장 하나만 보여줬는데, 차트는
 * 기본이 BTCUSDT 라 열면 늘 코인 추천만 나왔다 — 국내주식과 해외주식은 시장을 바꿔야
 * 볼 수 있었고, 그러려면 그런 게 있다는 걸 먼저 알아야 했다.
 *
 * 지금은 국내주식·해외주식·코인 셋을 한 번에 낸다. 묶음마다 **사라 셋 · 사지 말 것
 * 셋**이라 아홉씩 열여덟 줄이고, 각 묶음이 자기 날짜를 들고 온다 — PC 가 꺼져 있어
 * 국내주식이 며칠 전 것이면 그 날짜가 그대로 보인다.
 */
export function ScreenPanel({ provider, onPick, names }: Props) {
  const [days, setDays] = useState(1);
  const [groups, setGroups] = useState<RecommendGroup[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setBusy(true);
    setError(null);
    recommend
      .groups(days)
      .then((r) => setGroups(r.groups))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false));
  }, [days]);

  return (
    <>
      <section className="card">
        <h2>오늘 살 만한 것</h2>
        <p className="formula" style={{ marginTop: 0 }}>
          모델이 <b>앞으로</b>를 보고 낸 기대 수익률 순서다. 지평마다 모델이 따로 있어
          칩을 바꾸면 그 지평 모델의 답이 나온다 — <b>다시 뽑는 게 아니다.</b>
        </p>
        <p className="note" style={{ marginTop: 0 }}>
          국내주식·해외주식·코인을 따로 낸다 · 아침에 한 번 뽑아 그날은 고정된다
        </p>
        <div className="chips">
          {DAYS.map((item) => (
            <button
              key={item.value}
              className="chip"
              data-active={days === item.value}
              disabled={busy}
              onClick={() => setDays(item.value)}
            >
              {item.label}
            </button>
          ))}
        </div>
        {busy && !groups && <p className="note">불러오는 중…</p>}
        {error && <p className="error" style={{ marginTop: 10 }}>{error}</p>}
      </section>

      {groups?.map((group) => (
        <GroupCard key={group.key} group={group} days={days} onPick={onPick} />
      ))}

      {/* 긴 설명은 **묶음마다 되풀이하지 않는다.** 세 번 적으면 목록보다 문장이
          길어져 정작 순위가 안 읽힌다. 셋 밑에 한 번만 둔다.
          `사지 말 것` 설명은 그 목록 바로 위로 옮겼다 — 설명이 목록에서 멀면
          그 목록을 잘못 읽은 뒤에야 읽게 된다. */}
      {groups?.some((g) => g.available) && (
        <section className="card">
          <p className="plain" style={{ marginTop: 0 }}>
            <b>순위는 그 묶음 안에서만</b> 매겨집니다. 코인의 1위와 국내주식의 1위를
            견주는 것은 뜻이 없습니다 — 움직이는 크기가 자릿수로 다릅니다.
          </p>
          <p className="plain">
            <b>"크게 움직인다" 는 "오른다" 가 아닙니다.</b> 크게 움직인다고 본 종목에서는
            방향도 제법 맞혔지만, 거의 안 움직인다고 본 종목에서는 동전던지기보다
            못했습니다. 그래도 순위를 그걸로 다시 세우지는 않습니다.
          </p>
        </section>
      )}

      {groups && <RecordCard groups={groups} days={days} />}

      {/* 예전의 '변동 순위'는 다른 질문에 답한다 — "오늘 뭘 지켜볼까".
          지우지 않고 접어 둔다. 재 놓은 결과가 있고, 사라는 뜻이 아닐 뿐이다. */}
      <WatchCard provider={provider} names={names} />
    </>
  );
}

/** 한 묶음 — 국내주식 · 해외주식 · 코인 중 하나. **빠진 묶음도 자리를 지킨다.**
 *  없는 것을 안 보여주면 "국내주식은 살 게 없다"로 잘못 읽힌다. */
function GroupCard({ group, days, onPick }: {
  group: RecommendGroup; days: number;
  onPick: (provider: string, symbol: string) => void;
}) {
  if (!group.available) {
    return (
      <section className="card">
        <h2>{group.label}</h2>
        <p className="note warn" style={{ marginTop: 0, marginBottom: 0 }}>
          {group.reason}
        </p>
      </section>
    );
  }
  const market = group.provider ?? "";
  // 1위와 꼴찌의 기대 차이. **"1위" 가 얼마나 뜻이 있는지가 여기 달렸다** — 열 종목이
  // 0.5%p 안에 붙어 있으면 순위는 줄 세우기지 고른 것이 아니다.
  const values = [...group.buy, ...group.avoid]
    .map((r) => r.expected)
    .filter((v): v is number => v !== null && v !== undefined && Number.isFinite(v));
  const spread = values.length > 1 ? Math.max(...values) - Math.min(...values) : null;
  // **값을 그 시장의 돈으로 적는다.** 얼마짜리인지는 % 를 몰라도 바로 읽히는
  // 유일한 숫자라 접지 않는다. 코인만 업비트 원화 실거래가를 따로 들고 온다.
  const money = group.key;
  return (
    <section className="card">
      <div className="row" style={{ marginBottom: 6 }}>
        <h2 style={{ margin: 0 }}>{group.label} · 사라</h2>
        {/* **묶음마다 자기 날짜를 들고 온다.** PC 가 꺼져 있으면 국내주식만 며칠
            전 것인데, 오늘 것인 척하면 그게 제일 위험한 거짓말이다. */}
        <span style={{ color: "var(--text-dim)", fontSize: 11 }}>{group.date}</span>
      </div>

      {/* 모델을 하나도 안 쓴 날이 있다. 그때 순위는 사실상 변동성 순서라
          "사라"가 아니다 — 목록보다 먼저 말해야 한다. */}
      {group.degenerate && (
        <p className="note warn" style={{ marginTop: 0 }}>
          <b>오늘 {days}일 모델은 기준선만 쓴다.</b> 이 순서는 사실상 변동성
          순서지 "사라"가 아니다.
          {group.skill !== null && group.skill !== undefined && (
            <> 학습 성적 {signed(group.skill, 4)} 로 기준선을 못 넘었다.</>
          )}
        </p>
      )}
      {group.allNegative && (
        <p className="note warn" style={{ marginTop: 0 }}>
          이 묶음은 후보 전부의 기대가 마이너스다. 그래도 순서는 낸다 —
          이건 <b>덜 나쁜 셋</b>이지 사라는 뜻이 아니다.
        </p>
      )}
      {(group.staleBars ?? 0) > 0 && (
        <p className="note" style={{ marginTop: 0 }}>
          장이 쉬어 {group.basedOn} 종가 기준이다.
        </p>
      )}
      {group.modelStale && (
        <p className="note warn" style={{ marginTop: 0 }}>
          그날 학습이 실패해 하루 전 모델을 그대로 썼다.
        </p>
      )}

      {/* **묶음 공통 이야기는 여기 한 번만.** 줄마다 되풀이하면 여섯 줄이 똑같아져
          정작 줄마다 다른 것(얼마나 움직일까)이 안 읽힌다. */}
      <p className="plain" style={{ marginBottom: 8 }}>
        {groupWords(spread, [...group.buy, ...group.avoid].map((r) => r.probUp))}
      </p>

      <div className="rows">
        {group.buy.map((item, i) => (
          <Row key={item.symbol} item={item} rank={i + 1} days={days} money={money}
               provider={market} onPick={() => onPick(market, item.symbol)} />
        ))}
      </div>

      <div className="group-label">사지 말 것</div>
      <p className="plain" style={{ marginTop: 0, marginBottom: 6 }}>{avoidWords}</p>
      <div className="rows">
        {group.avoid.map((item) => (
          <Row key={item.symbol} item={item} down days={days} money={money}
               onPick={() => onPick(market, item.symbol)} />
        ))}
      </div>
    </section>
  );
}

/**
 * 추천 한 줄. **말이 먼저, 숫자는 접어 둔다.**
 *
 * 예전에는 `+0.17% · −3.7%~+4.3% · 오를 확률 52%` 만 있었는데, 분위수를 공부하지
 * 않은 사람에게 그건 좋다는 뜻인지 나쁘다는 뜻인지 알 수 없는 줄이다. 문장은
 * `say.ts` 한 곳에서 만든다 — 화면마다 적으면 같은 상황이 다르게 읽힌다.
 */
function Row({
  item,
  rank,
  down,
  days,
  provider,
  money,
  onPick,
}: {
  item: RecommendItem;
  rank?: number;
  down?: boolean;
  days?: number;
  provider?: string;
  /** 어느 묶음인가. 값을 그 시장의 돈으로 적는 데만 쓴다. */
  money?: "kr" | "us" | "coin";
  onPick: () => void;
}) {
  const good = (item.expected ?? 0) >= 0 && !down;
  const [buying, setBuying] = useState(false);
  // 행과 매수 칸은 **형제**다. 행 안에 넣으면 세 번째 flex 칸이 되어 종목 이름을
  // 짓누른다 — 실제로 "솔라/나 SOL" 로 쪼개졌다. `.rows` 가 격자라 형제가 제 줄을 갖는다.
  return (
    <>
    <div className="row" style={{ alignItems: "flex-start" }}>
      <span style={{ flex: 1, minWidth: 0 }}>
        <button className="linky" onClick={onPick} title="이 종목으로 차트를 옮긴다">
          {rank ? `${rank}. ` : "· "}
          {/* **거래쌍 이름(SOLUSDT)을 쓰지 않는다.** 원화로 사는 사람에게 달러 쌍
              이름은 자기가 치를 값과 무관하다. 이름이 표에 없으면 티커만 나간다 —
              없는 이름을 지어내면 다른 코인과 헷갈린다. */}
          {item.name ? (
            <>
              {item.name} <em className="ticker">{item.ticker ?? item.symbol}</em>
            </>
          ) : (
            item.ticker ?? item.symbol
          )}
        </button>
        {/* 값은 접지 않는다. 얼마짜리인지는 % 를 몰라도 바로 읽히는 유일한 숫자다. */}
        {price(item, money) && (
          <span style={{ display: "block", color: "var(--text-dim)", fontSize: 11 }}>
            {price(item, money)}
          </span>
        )}
        {/* **줄에는 그 종목만의 것.** 묶음 공통(순위 차이·방향)은 카드 위에 한 번 있다. */}
        <p className="plain">{moveWords(item.confidence, item.band)}.</p>
        <Numbers>
          <div className="row">
            <span>기대</span>
            <b style={{ color: good ? "var(--up)" : "var(--down)" }}>
              {signed(item.expected)}%
            </b>
          </div>
          {item.band && (
            <div className="row">
              <span>{days ?? 1}일 뒤 범위 (80%)</span>
              <b>{signed(item.band[0], 1)}% ~ {signed(item.band[1], 1)}%</b>
            </div>
          )}
          {item.probUp !== null && item.probUp !== undefined && (
            <div className="row">
              <span>오를 확률</span>
              <b>{pct(item.probUp, 0)}</b>
            </div>
          )}
          {item.confidence && (
            <div className="row">
              <span>확신</span>
              <b>{CONFIDENCE[item.confidence] ?? item.confidence}</b>
            </div>
          )}
        </Numbers>
      </span>
      {!down && provider && (
        <button className="chip" style={{ flex: "none", alignSelf: "flex-start" }}
                onClick={() => setBuying((v) => !v)}>
          {buying ? "접기" : "샀다"}
        </button>
      )}
    </div>
    {buying && provider && (
      <BuyForm item={item} days={days} provider={provider}
               onDone={() => setBuying(false)} />
    )}
    </>
  );
}

/**
 * 산 것을 장부에 적는다.
 *
 * **원화 마켓이 있으면 그쪽으로 연다.** 추천은 바이낸스 모델로 나오지만 사람이 실제로
 * 치른 값은 업비트 원화다. 거기서 열어야 손익이 자기가 낸 돈으로 난다 — 환율로 환산한
 * 달러 값은 살 수도 팔 수도 없는 숫자다(`screen/coins.py`).
 *
 * 밴드는 **비율**이라 어느 통화에 붙여도 뜻이 같다. 그래서 근거는 그대로 넘어간다.
 */
function BuyForm({ item, days, provider, onDone }: {
  item: RecommendItem; days?: number; provider: string; onDone: () => void;
}) {
  const krw = item.krw;
  const suggested = krw?.last ?? item.last;
  const [entry, setEntry] = useState(suggested ? String(suggested) : "");
  const [shares, setShares] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const submit = () => {
    const price = Number(entry.replace(/,/g, ""));
    const count = Number(shares.replace(/,/g, ""));
    if (!(price > 0) || !(count > 0)) {
      setNote("진입가와 주수를 숫자로 넣어라");
      return;
    }
    setBusy(true);
    positions.open({
      provider: krw ? "upbit" : provider,
      symbol: krw ? krw.symbol : item.symbol,
      entry: price, shares: count,
      band: item.band ?? null, expected: item.expected ?? null,
      days: days ?? null, source: "recommend",
    })
      .then((r) => {
        setNote(r.warning || "보유 탭에 적었다. 손절·목표에 알림을 걸었다.");
        setShares("");
        window.setTimeout(onDone, 1500);
      })
      .catch((e) => setNote(String(e.message ?? e)))
      .finally(() => setBusy(false));
  };

  return (
    <div style={{ minWidth: 0 }}>
      <div className="ask-form">
        <input inputMode="decimal" placeholder="진입가"
               value={entry} onChange={(e) => setEntry(e.target.value)} />
        <input inputMode="decimal" placeholder="주수"
               value={shares} onChange={(e) => setShares(e.target.value)} />
        <button onClick={submit} disabled={busy}>적기</button>
      </div>
      <p className="formula" style={{ marginTop: 4, marginBottom: 0 }}>
        {krw
          ? `업비트 ${krw.symbol} 로 적는다 — 실제로 치른 값이 그쪽이다.`
          : "손절·목표는 이 추천의 80% 밴드에서 나온다."}
      </p>
      {note && <p className="note" style={{ marginTop: 6 }}>{note}</p>}
    </div>
  );
}

/**
 * 이 추천이 과거에 맞았나 — **묶음마다 따로 센다.**
 *
 * 국내주식·해외주식·코인은 변동성이 자릿수로 달라 합치면 평균이 아무 뜻이 없다.
 * 한 표에 나란히 놓되 한 숫자로 접지 않는다.
 *
 * 칸이 둘인 이유: 실전은 그날 한 번뿐이라 되돌릴 수 없고, 백필은 origin 을 고를 수
 * 있고 몇 번이든 다시 돌릴 수 있다. 한 숫자로 합치면 "몇 번 다시 돌렸는지" 가
 * 성적에 스며든다. 그래서 나누고, 백필은 읽을 값(holdout)을 앞에 둔다.
 */
function RecordCard({ groups, days }: { groups: RecommendGroup[]; days: number }) {
  const live = groups.filter((g) => (g.record?.n ?? 0) > 0);
  const back = groups.filter((g) => (g.backfill?.n ?? 0) > 0);
  const measured = groups.find((g) => g.measured?.directionHit !== undefined)?.measured;

  return (
    <section className="card">
      <h2>이 추천이 과거에 맞았나</h2>

      <div className="group-label">아침에 뽑아 그대로 채점한 것</div>
      {live.length ? (
        groups.map((g) => {
          const r = g.record;
          if (!r || r.n === 0) return null;
          return (
            <p className="plain" key={g.key} style={{ marginTop: 6 }}>
              <b>{g.label}</b> — {edgeWords(r.edgePct)}.
              {bandWords(r.bandHit) && <> {bandWords(r.bandHit)}.</>}
            </p>
          );
        })
      ) : (
        <p className="plain" style={{ marginTop: 6 }}>
          아직 채점할 판이 없습니다. 추천을 낸 뒤 {days}일이 지나야 첫 줄이 쌓입니다 —
          그때부터 이 칸이 이 기능의 진짜 답입니다.
        </p>
      )}
      {live.length > 0 && fewHandsWords(groups.map((g) => g.record?.n)) && (
        <p className="formula" style={{ marginTop: 6, marginBottom: 0 }}>
          {fewHandsWords(groups.map((g) => g.record?.n))}
        </p>
      )}
      {live.length > 0 && (
        <Numbers>
          {groups.map((g) => {
            const r = g.record;
            if (!r || r.n === 0) return null;
            return (
              <div className="row" key={g.key}>
                <span style={{ whiteSpace: "nowrap" }}>{g.label}</span>
                <b style={{ color: (r.edgePct ?? 0) > 0 ? "var(--up)" : "var(--down)" }}>
                  {signed(r.edgePct)}%p · 밴드 {pct(r.bandHit, 1)} · {r.n}판
                </b>
              </div>
            );
          })}
        </Numbers>
      )}

      {back.length > 0 && (
        <div style={{ marginTop: 14, borderTop: "1px solid var(--line)", paddingTop: 10 }}>
          <div className="group-label" style={{ marginTop: 0 }}>
            과거로 되돌려 재 본 것 (실전 아님)
          </div>
          {back.map((g) => {
            const b = g.backfill!;
            const read = b.holdout?.n ? b.holdout : b;
            return (
              <p className="plain" key={g.key} style={{ marginTop: 6 }}>
                <b>{g.label}</b> — {edgeWords(read.edgePct)}.
                {bandWords(read.bandHit) && <> {bandWords(read.bandHit)}.</>}
              </p>
            );
          })}
          {fewHandsWords(back.map((g) => (g.backfill?.holdout?.n ?? g.backfill?.n))) && (
            <p className="formula" style={{ marginTop: 6, marginBottom: 0 }}>
              {fewHandsWords(back.map((g) => (g.backfill?.holdout?.n ?? g.backfill?.n)))}
            </p>
          )}
          <Numbers>
            {back.map((g) => {
              const b = g.backfill!;
              const held = b.holdout?.n ? b.holdout : null;
              const read = held ?? b;
              return (
                <div className="row" key={g.key}>
                  <span style={{ whiteSpace: "nowrap" }}>{g.label}</span>
                  <b style={{ color: (read.edgePct ?? 0) > 0 ? "var(--up)" : "var(--down)" }}>
                    {signed(read.edgePct)}%p · 밴드 {pct(read.bandHit, 1)}
                    {" "}· {read.n}판{held ? " (안 본 구간)" : ""}
                  </b>
                </div>
              );
            })}
          </Numbers>
          <p className="formula" style={{ marginTop: 8, marginBottom: 0 }}>
            <b>이건 실전 성적이 아닙니다.</b> 과거 아침에 서서 같은 추천을 뽑고 지평이
            지난 뒤 실제와 맞춘 것입니다 — 그날의 자료만 보고 뽑았지만, 실전과 달리
            어느 날들을 볼지 고를 수 있고 몇 번이든 다시 돌릴 수 있습니다. 그래서 위
            문장은 <b>규칙을 만질 때 안 본 구간</b>에서 잰 것만 읽습니다.
          </p>
        </div>
      )}

      <p className="formula" style={{ marginTop: 10, marginBottom: 0 }}>
        견주는 상대는 <b>그 묶음 후보 전체를 그냥 다 산 경우</b>입니다. 그냥 "올랐나" 로
        보면 고르는 실력이 아니라 시장을 재게 됩니다 — 시장이 다 오른 날 추천도 올랐다는
        건 아무 말도 아닙니다. <b>묶음끼리 견주는 것도 안 됩니다</b>: 코인과 국내주식은
        움직이는 크기가 자릿수로 다릅니다.
        {trustWords(measured) && (
          <>
            <br />
            <b>{trustWords(measured)}</b> 이 성적은 추천 자체가 아니라 그 밑에 깔린
            예측 모델을 잰 것입니다.
          </>
        )}
      </p>
    </section>
  );
}

/** 예전의 변동 순위. 다른 질문("오늘 뭘 지켜볼까")에 답하고 그쪽이 훨씬 세다 —
 *  변동 상위−하위 +0.94%p vs 방향 +0.65%p. 지우지 않고 접어 둔다. */
function WatchCard({ provider, names }: { provider: string; names: SymbolNames }) {
  const [open, setOpen] = useState(false);
  const [found, setFound] = useState<ScreenResult | null>(null);

  useEffect(() => setFound(null), [provider]);

  const load = useCallback(() => {
    screen
      .rank({ provider, timeframe: RECOMMEND_TIMEFRAME, horizon: 3, limit: 6 })
      .then(setFound)
      .catch(() => setFound(null));
  }, [provider]);

  return (
    <section className="card">
      <details
        onToggle={(e) => {
          const isOpen = (e.currentTarget as HTMLDetailsElement).open;
          setOpen(isOpen);
          if (isOpen && !found) load();
        }}
      >
        <summary style={{ cursor: "pointer", color: "var(--text-dim)", fontSize: 12 }}>
          관심있게 볼 종목 (변동 순 — 사라는 뜻이 아니다)
        </summary>
        {open && !found && <p className="note">불러오는 중…</p>}
        {found?.available && (
          <>
            <div className="rows" style={{ marginTop: 8 }}>
              {found.items.map((item, i) => (
                <div className="row" key={item.symbol}>
                  <span>
                    {i + 1}. {label(item.symbol, names)}
                  </span>
                  <b>{signed(item.move)}</b>
                </div>
              ))}
            </div>
            <p className="formula" style={{ marginTop: 8 }}>
              "앞으로 크게 움직일 것 같은 순서"다. 실제로는 이쪽이 더 세다 —
              상위−하위 {signed(found.quality?.move.topMinusBottomPct)}%p 로 방향
              쪽({signed(found.quality?.direction.topMinusBottomPct)}%p)의 몇 배다.
            </p>
          </>
        )}
        {found && !found.available && <p className="note">{found.reason}</p>}
      </details>
    </section>
  );
}
