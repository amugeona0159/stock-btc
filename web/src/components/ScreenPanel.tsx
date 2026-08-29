import { useCallback, useEffect, useState } from "react";

import { positions, recommend, screen } from "../api";
import type {
  ProviderInfo,
  Recommend,
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
  providers: ProviderInfo[];
  onPick: (symbol: string) => void;
  onProvider: (key: string) => void;
  /** 종목 기호 → 한글 이름. `App` 이 부팅 때 받아 둔 표를 그대로 넘긴다. */
  names: SymbolNames;
}

export function ScreenPanel({ provider, providers, onPick, onProvider, names }: Props) {
  const [days, setDays] = useState(1);
  const [result, setResult] = useState<Recommend | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setBusy(true);
    setError(null);
    setResult(null);
    recommend
      .today(provider, days)
      .then(setResult)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false));
  }, [provider, days]);

  return (
    <>
      <section className="card">
        <h2>오늘 살 만한 것</h2>
        <p className="formula" style={{ marginTop: 0 }}>
          모델이 <b>앞으로</b>를 보고 낸 기대 수익률 순서다. 지평마다 모델이 따로 있어
          칩을 바꾸면 그 지평 모델의 답이 나온다 — <b>다시 뽑는 게 아니다.</b>
        </p>
        <p className="note" style={{ marginTop: 0 }}>
          {result?.date
            ? `${result.date} 아침에 뽑았다 · 오늘은 다시 눌러도 안 바뀐다`
            : "아침에 한 번 뽑아 그날은 고정된다"}
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
        {busy && <p className="note">불러오는 중…</p>}
        {error && <p className="error" style={{ marginTop: 10 }}>{error}</p>}
        {result && !result.available && (
          <>
            <p className="note warn">{result.reason}</p>
            {(result.providers?.length ?? 0) > 0 && (
              <>
                <div className="group-label">추천이 있는 시장</div>
                <div className="chips">
                  {result.providers!.map((key) => (
                    <button key={key} className="chip" onClick={() => onProvider(key)}>
                      {providers.find((p) => p.key === key)?.name ?? key}
                    </button>
                  ))}
                </div>
              </>
            )}
          </>
        )}
      </section>

      {result?.available && (
        <section className="card">
          <h2>사라 · {days}일</h2>

          {/* 모델을 하나도 안 쓴 날이 있다. 그때 순위는 사실상 변동성 순서라
              "사라"가 아니다 — 목록보다 먼저 말해야 한다. */}
          {result.degenerate && (
            <p className="note warn" style={{ marginTop: 0 }}>
              <b>오늘 {days}일 모델은 기준선만 쓴다.</b> 이 순서는 사실상 변동성
              순서지 "사라"가 아니다.
              {result.skill !== null && result.skill !== undefined && (
                <> 학습 성적 {signed(result.skill, 4)} 로 기준선을 못 넘었다.</>
              )}
            </p>
          )}
          {result.allNegative && (
            <p className="note warn" style={{ marginTop: 0 }}>
              오늘은 후보 전부의 기대가 마이너스다. 그래도 순서는 낸다 —
              이건 <b>덜 나쁜 셋</b>이지 사라는 뜻이 아니다.
            </p>
          )}
          {(result.staleBars ?? 0) > 0 && (
            <p className="note" style={{ marginTop: 0 }}>
              장이 쉬어 {result.basedOn} 종가 기준이다.
            </p>
          )}
          {result.modelStale && (
            <p className="note warn" style={{ marginTop: 0 }}>
              오늘 학습이 실패해 어제 모델을 그대로 썼다.
            </p>
          )}

          <div className="rows">
            {result.buy.map((item, i) => (
              <Row key={item.symbol} item={item} rank={i + 1} days={days}
                   provider={provider} onPick={() => onPick(item.symbol)} />
            ))}
          </div>

          <div className="group-label">피하라</div>
          <div className="rows">
            {result.avoid.map((item) => (
              <Row key={item.symbol} item={item} down
                   onPick={() => onPick(item.symbol)} />
            ))}
          </div>
          <p className="note" style={{ marginTop: 6 }}>
            기대가 제일 낮은 둘이다. 공매도 하라는 말이 아니고, 값이 양수여도
            <b> 후보 중 꼴찌</b>라는 뜻이다.
          </p>
          <p className="formula" style={{ marginTop: 8, marginBottom: 0 }}>
            <b>확신</b>은 모델이 얼마나 크게 움직인다고 했는지다. 27,664판을 갈라 보니
            크게 본 구간에서는 방향을 <b>64.7%</b> 맞혔고, 거의 안 움직인다고 한 구간에서는{" "}
            <b>47.5%</b> — 동전던지기보다 못했다. 순위를 이걸로 다시 세우지는 않는다.
          </p>
        </section>
      )}

      {result?.available && <RecordCard result={result} days={days} />}

      {/* 예전의 '변동 순위'는 다른 질문에 답한다 — "오늘 뭘 지켜볼까".
          지우지 않고 접어 둔다. 재 놓은 결과가 있고, 사라는 뜻이 아닐 뿐이다. */}
      <WatchCard provider={provider} names={names} />
    </>
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

function RecordCard({ result, days }: { result: Recommend; days: number }) {
  const record = result.record;
  const measured = result.measured ?? {};
  return (
    <section className="card">
      <h2>이 추천이 과거에 맞았나</h2>
      {record && record.n > 0 ? (
        <div className="rows">
          <div className="row">
            <span>추천 3종 평균</span>
            <b>{signed(record.buyPct)}%</b>
          </div>
          <div className="row">
            <span>후보 전체 평균</span>
            <b>{signed(record.universePct)}%</b>
          </div>
          <div className="row">
            <span>차이 (이게 실력이다)</span>
            <b
              style={{
                fontSize: 15,
                color: (record.edgePct ?? 0) > 0 ? "var(--up)" : "var(--down)",
              }}
            >
              {signed(record.edgePct)}%p
              <span style={{ color: "var(--text-dim)" }}>
                {" "}
                · 이긴 비율 {pct(record.winRate, 0)}
              </span>
            </b>
          </div>
          <div className="row">
            <span>80% 밴드 적중</span>
            <b>{pct(record.bandHit, 1)}</b>
          </div>
          <div className="row">
            <span>채점한 날</span>
            <b>
              {record.n}일치
              {!record.enough && (
                <span style={{ color: "var(--warn)" }}> · 30일은 있어야 성적이다</span>
              )}
            </b>
          </div>
        </div>
      ) : (
        <p className="note">
          아직 채점할 판이 없다. 추천을 낸 뒤 {days}일이 지나야 첫 줄이 쌓인다 —
          그때부터 이 표가 이 기능의 진짜 답이다.
        </p>
      )}

      <p className="formula" style={{ marginTop: 10 }}>
        <b>기준선은 후보 전체 평균이다.</b> 0 과 견주면 고르는 실력이 아니라 시장을
        재게 된다 — 시장이 다 오른 날 추천도 올랐다는 건 아무 말도 아니다.
        <br />
        <b>기대</b>는 예측 분포의 <b>중앙값</b>이지 평균이 아니다. 수수료·슬리피지는
        빼지 않았다.
        {measured.directionHit !== undefined && (
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
