import { useCallback, useEffect, useState } from "react";

import { positions, recommend, screen } from "../api";
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

function pct(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined || !Number.isFinite(value)
    ? "—"
    : `${(value * 100).toFixed(digits)}%`;
}

// 확신도는 `move_atr` 3분위다. **줄마다 긴 문장을 반복하지 않는다** — 짧은 라벨에
// 숫자만 붙이고, 그 숫자가 무슨 뜻인지는 카드 아래에 한 번만 적는다.
const CONFIDENCE: Record<string, string> = {
  high: "확신 높음 · 64.7%",
  mid: "보통",
  low: "확신 낮음 · 47.5%",
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
          길어져 정작 순위가 안 읽힌다. 셋 밑에 한 번만 둔다. */}
      {groups?.some((g) => g.available) && (
        <section className="card">
          <p className="note" style={{ marginTop: 0 }}>
            <b>사지 말 것</b>은 기대가 제일 낮은 셋이다. 공매도 하라는 말이 아니고,
            값이 양수여도 <b>그 묶음 안에서 꼴찌</b>라는 뜻이다.
          </p>
          <p className="formula" style={{ marginTop: 8, marginBottom: 0 }}>
            <b>확신</b>은 모델이 얼마나 크게 움직인다고 했는지다. 27,664판을 갈라 보니
            크게 본 구간에서는 방향을 <b>64.7%</b> 맞혔고, 거의 안 움직인다고 한 구간에서는{" "}
            <b>47.5%</b> — 동전던지기보다 못했다. 순위를 이걸로 다시 세우지는 않는다.
            <br />
            <b>묶음끼리 견주지 말 것.</b> 순위는 그 묶음의 후보 안에서만 매겨진다 —
            코인 +2% 와 국내주식 +0.5% 는 변동성이 달라 같은 자로 잰 값이 아니다.
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

      <div className="rows">
        {group.buy.map((item, i) => (
          <Row key={item.symbol} item={item} rank={i + 1} days={days}
               provider={market} onPick={() => onPick(market, item.symbol)} />
        ))}
      </div>

      <div className="group-label">사지 말 것</div>
      <div className="rows">
        {group.avoid.map((item) => (
          <Row key={item.symbol} item={item} down
               onPick={() => onPick(market, item.symbol)} />
        ))}
      </div>
    </section>
  );
}

function Row({
  item,
  rank,
  down,
  days,
  provider,
  onPick,
}: {
  item: RecommendItem;
  rank?: number;
  down?: boolean;
  days?: number;
  provider?: string;
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
        <span style={{ display: "block", color: "var(--text-dim)", fontSize: 11 }}>
          {/* 원화 실거래가(업비트) + 확신도. **가운뎃점으로 끊는다** — 붙여 두면
              "₩144,400 확신 높음" 이 한 덩어리로 읽히고 줄이 바뀔 때 라벨 가운데가
              잘린다. 피하라 목록에는 확신도를 안 적는다(거기서는 읽을 이유가 없다). */}
          {[
            item.krw ? won(item.krw.last) : null,
            item.confidence && !down
              ? CONFIDENCE[item.confidence] ?? item.confidence
              : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </span>
      </span>
      <b style={{ textAlign: "right", flex: "none" }}>
        <span style={{ color: good ? "var(--up)" : "var(--down)" }}>
          {signed(item.expected)}%
        </span>
        {item.band && (
          <span style={{ display: "block", color: "var(--text-dim)", fontSize: 11 }}>
            {signed(item.band[0], 1)}% ~ {signed(item.band[1], 1)}%
            {/* `probUp` 은 그 지평 모델의 값이라 지평마다 다르다. 그대로 적는다. */}
            {item.probUp !== null && item.probUp !== undefined && (
              <> · 오를 확률 {pct(item.probUp, 0)}</>
            )}
          </span>
        )}
      </b>
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

      <div className="group-label">실전 — 아침에 뽑아 그대로 채점한 것</div>
      {live.length ? (
        <div className="rows">
          {groups.map((g) => {
            const r = g.record;
            return (
              <div className="row" key={g.key}>
                <span>{g.label}</span>
                {r && r.n > 0 ? (
                  <b style={{ color: (r.edgePct ?? 0) > 0 ? "var(--up)" : "var(--down)" }}>
                    {signed(r.edgePct)}%p
                    <span style={{ color: "var(--text-dim)" }}>
                      {" "}· 밴드 {pct(r.bandHit, 1)} · {r.n}판
                      {!r.enough && " (30일은 있어야 성적이다)"}
                    </span>
                  </b>
                ) : (
                  <b style={{ color: "var(--text-dim)" }}>아직 없음</b>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <p className="note" style={{ marginTop: 0 }}>
          아직 채점할 판이 없다. 추천을 낸 뒤 {days}일이 지나야 첫 줄이 쌓인다 —
          그때부터 이 표가 이 기능의 진짜 답이다.
        </p>
      )}

      {back.length > 0 && (
        <div style={{ marginTop: 14, borderTop: "1px solid var(--line)", paddingTop: 10 }}>
          <div className="group-label" style={{ marginTop: 0 }}>
            되돌려 본 성적 — 실전이 아니다
          </div>
          <div className="rows">
            {back.map((g) => {
              const b = g.backfill!;
              const held = b.holdout?.n ? b.holdout : null;
              const read = held ?? b;
              return (
                <div className="row" key={g.key}>
                  <span>
                    {g.label}
                    <span style={{ display: "block", color: "var(--text-dim)", fontSize: 11 }}>
                      {b.from} ~ {b.to}
                      {held ? ` · 안 본 구간 ${held.n}판` : ` · ${b.n}판`}
                    </span>
                  </span>
                  <b style={{ color: (read.edgePct ?? 0) > 0 ? "var(--up)" : "var(--down)" }}>
                    {signed(read.edgePct)}%p
                    <span style={{ display: "block", color: "var(--text-dim)", fontSize: 11 }}>
                      밴드 {pct(read.bandHit, 1)} · 이긴 비율 {pct(read.winRate, 0)}
                    </span>
                  </b>
                </div>
              );
            })}
          </div>
          <p className="formula" style={{ marginTop: 8, marginBottom: 0 }}>
            <b>이건 실전 성적이 아니다.</b> 과거 아침에 서서 같은 추천을 뽑고 지평이
            지난 뒤 실제와 맞춘 것이다 — 그날의 시세·사건·관심도만 보고 뽑았지만,
            실전과 달리 어느 날들을 볼지 고를 수 있고 몇 번이든 다시 돌릴 수 있다.
            {back.some((g) => g.backfill?.holdout?.n) && (
              <>
                {" "}그래서 위 숫자는 <b>규칙 튜닝에 쓰지 않은 마지막 구간</b>에서 잰
                것만 읽는다. 튜닝에 쓴 구간의 성적은 자기 답을 보고 만든 값이다.
              </>
            )}
            {back.some((g) => g.backfill?.staleRows) && (
              <>
                {" "}설정이 바뀐 판은 안 셌다 — 옛 모델과 새 모델 성적을 한 숫자에
                섞을 수 없어서다.
              </>
            )}
          </p>
        </div>
      )}

      <p className="formula" style={{ marginTop: 10 }}>
        <b>기준선은 그 묶음 후보 전체의 평균이다.</b> 0 과 견주면 고르는 실력이 아니라
        시장을 재게 된다 — 시장이 다 오른 날 추천도 올랐다는 건 아무 말도 아니다.
        <b> 묶음끼리 견주는 것도 안 된다</b> — 코인과 국내주식은 변동성이 자릿수로
        다르다.
        <br />
        <b>기대</b>는 예측 분포의 <b>중앙값</b>이지 평균이 아니다. 수수료·슬리피지는
        빼지 않았다.
        {measured?.directionHit !== undefined && (
          <>
            <br />
            <b>아래 숫자는 이 추천의 성적이 아니다.</b> 추천 밑에 깔린 예측 모델을
            {" "}{measured.n?.toLocaleString("ko-KR")}판 채점한 결과다 — 방향{" "}
            <b>{pct(measured.directionHit, 1)}</b>, 80% 밴드{" "}
            <b>{pct(measured.bandHit, 1)}</b>. 추천 자체의 성적은 위 칸에 쌓인다.
            <br />
            <b>이 도구가 잘하는 건 폭이지 방향이 아니다</b> — 추천은 그 약한 쪽을 쓰는
            화면이라 숫자를 그대로 읽을 것.
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
