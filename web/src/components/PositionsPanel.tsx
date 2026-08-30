/**
 * 보유 — 산 것을 팔 때까지 따라간다.
 *
 * 이 화면의 규칙 둘.
 *
 * **자동으로 팔지 않는다.** 값이 닿으면 "닿았다"까지만 뜨고, 「팔았다」·「안 팔았다」는
 * 사람이 누른다. 실제로 팔았는지는 프로그램이 모르고, 모르는 채로 장부를 움직이면
 * 그 순간 손익이 아무 뜻도 없는 숫자가 된다.
 *
 * **통화를 더하지 않는다.** 원화 판과 달러 판은 따로 센다. 환율로 맞추지도 않는다 —
 * 곱해서 나온 값에는 살 수도 팔 수도 없다.
 */
import { useCallback, useEffect, useState } from "react";

import { positions as api } from "../api";
import { ledgerWords, roomWords } from "../say";
import { Numbers } from "./Numbers";
import type { Position, PositionAdvice, PositionsView } from "../types";

const SIGN: Record<string, string> = { KRW: "₩", USD: "$" };

const REASON: Record<string, string> = {
  target: "목표",
  stop: "손절",
  manual: "직접",
};

function money(value: number | null | undefined, currency: string): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const digits = currency === "KRW" ? 0 : Math.abs(value) >= 100 ? 2 : 4;
  return `${SIGN[currency] ?? ""}${value.toLocaleString("ko-KR", {
    maximumFractionDigits: digits,
  })}`;
}

function signed(value: number | null | undefined, currency: string): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : "−"}${money(Math.abs(value), currency)}`;
}

function pct(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

/** 오르면 up, 내리면 down. 손익이라 여기서는 방향이 곧 좋고 나쁨이다. */
function tone(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "var(--text-dim)";
  }
  if (value > 0) return "var(--up)";
  if (value < 0) return "var(--down)";
  return "var(--text-dim)";
}

function num(value: string): number | null {
  const parsed = Number(value.replace(/,/g, ""));
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

interface Props {
  provider: string;
  symbol: string;
  onPick: (symbol: string) => void;
}

export function PositionsPanel({ provider, symbol, onPick }: Props) {
  const [view, setView] = useState<PositionsView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showClosed, setShowClosed] = useState(false);

  const load = useCallback(() => {
    api.list().then(setView).catch((e) => setError(String(e.message ?? e)));
  }, []);

  useEffect(() => {
    load();
    // 평가손익은 지금 값이라 따라 움직여야 한다. 30초면 충분하다.
    const timer = window.setInterval(load, 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const all = view?.positions ?? [];
  const open = all.filter((p) => p.status === "open");
  const closed = all.filter((p) => p.status === "closed");
  const shown = showClosed ? closed : open;

  return (
    <>
      <section className="card">
        <h2>
          보유
          {view && view.waiting > 0 && (
            <span className="scope">정할 것 {view.waiting}</span>
          )}
        </h2>

        <p className="formula" style={{ marginTop: 0 }}>
          값이 닿으면 알려 준다. <b>파는 것은 사람이 한다</b> — 닿았다고 판 것으로 치면
          장부가 실제 잔고와 갈라진다. 손절·목표는 그날 추천의 <b>80% 밴드</b>에서 나온다.
        </p>

        {error && <p className="error" style={{ marginTop: 8 }}>{error}</p>}

        <div className="chips">
          <button className="chip" data-active={!showClosed}
                  onClick={() => setShowClosed(false)}>
            들고 있는 것 {open.length}
          </button>
          <button className="chip" data-active={showClosed}
                  onClick={() => setShowClosed(true)}>
            닫은 판 {closed.length}
          </button>
        </div>

        <Record view={view} />
      </section>

      <OpenForm provider={provider} symbol={symbol} onDone={load} />

      {shown.length === 0 && (
        <section className="card">
          <p className="note" style={{ marginTop: 0 }}>
            {showClosed
              ? "아직 닫은 판이 없다."
              : "들고 있는 것이 없다. 추천에서 고르거나 아래에 직접 적는다."}
          </p>
        </section>
      )}

      {shown.map((item) => (
        <PositionCard key={item.id} item={item} onChange={load} onPick={onPick} />
      ))}
    </>
  );
}

function Record({ view }: { view: PositionsView | null }) {
  const record = view?.record;
  if (!record || record.n === 0) {
    return (
      <p className="plain" style={{ marginTop: 10 }}>
        아직 닫은 판이 없어 성적이 없습니다. <b>판을 닫아야 첫 줄이 쌓입니다</b> —
        여기 숫자는 이 장부의 성적이지 추천의 성적이 아닙니다.
      </p>
    );
  }
  return (
    <>
      {/* 말이 먼저. 승률에는 부호를 안 붙인다 — `+50%` 로 적으면 손익처럼 읽힌다. */}
      <p className="plain" style={{ marginTop: 10 }}>
        {ledgerWords(record.n, record.wins ?? 0)}
        {" ("}이긴 비율 {((record.winRate ?? 0) * 100).toFixed(0)}%{")"}
      </p>
      {/* **번 돈은 접지 않는다.** 여기선 숫자가 곧 답이다. */}
      <div className="rows" style={{ marginTop: 6 }}>
        {Object.entries(record.byCurrency).map(([currency, body]) => (
          <div className="row" key={currency}>
            <span>{currency} {body.n}판</span>
            <b style={{ color: tone(body.realized) }}>
              {signed(body.realized, currency)}
              {body.avgPct !== null && ` · 판당 ${pct(body.avgPct)}`}
            </b>
          </div>
        ))}
      </div>
      <Numbers>
        <div className="row">
          <span>어떻게 닫혔나</span>
          <b>
            {Object.entries(record.reasons)
              .map(([key, n]) => `${REASON[key] ?? key} ${n}`)
              .join(" · ")}
          </b>
        </div>
      </Numbers>
    </>
  );
}

function OpenForm({ provider, symbol, onDone }: {
  provider: string; symbol: string; onDone: () => void;
}) {
  const [entry, setEntry] = useState("");
  const [shares, setShares] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const submit = () => {
    const price = num(entry);
    const count = num(shares);
    if (price === null || count === null) {
      setNote("진입가와 주수를 숫자로 넣어라");
      return;
    }
    setBusy(true);
    api.open({ provider, symbol, entry: price, shares: count })
      .then((r) => {
        setEntry("");
        setShares("");
        // 밴드가 없으면 변동성으로 내려가고, 그것도 없으면 계획을 못 낸다.
        setNote(r.warning || "적었다. 손절·목표에 알림을 걸었다.");
        onDone();
      })
      .catch((e) => setNote(String(e.message ?? e)))
      .finally(() => setBusy(false));
  };

  return (
    <section className="card">
      <h2>산 것 적기</h2>
      <p className="formula" style={{ marginTop: 0 }}>
        <b>{symbol}</b> 을 얼마에 몇 주 샀는지. 손절·목표는 이 종목의 변동성(ATR)에서
        뽑는다 — 추천에서 열면 그날 밴드를 그대로 쓴다.
      </p>
      <div className="ask-form" style={{ marginTop: 8 }}>
        <input inputMode="decimal" placeholder="진입가"
               value={entry} onChange={(e) => setEntry(e.target.value)} />
        <input inputMode="decimal" placeholder="주수"
               value={shares} onChange={(e) => setShares(e.target.value)} />
        <button onClick={submit} disabled={busy}>적기</button>
      </div>
      {note && <p className="note" style={{ marginTop: 8 }}>{note}</p>}
    </section>
  );
}

function PositionCard({ item, onChange, onPick }: {
  item: Position; onChange: () => void; onPick: (symbol: string) => void;
}) {
  const [sellPrice, setSellPrice] = useState("");
  const [sellShares, setSellShares] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [advice, setAdvice] = useState<PositionAdvice | null>(null);

  const pending = item.pending ?? [];
  const total = (item.realized ?? 0) + (item.unrealized ?? 0);
  const totalPct = item.cost > 0 || item.realized
    ? (total / (item.entry * item.shares)) * 100
    : null;

  const run = (work: Promise<unknown>) => {
    setBusy(true);
    work.then(() => { setNote(null); onChange(); })
      .catch((e) => setNote(String(e.message ?? e)))
      .finally(() => setBusy(false));
  };

  const sell = (price: number, shares: number, reason: string) =>
    run(api.sold(item.id, { price, shares, reason }));

  return (
    <section className="card">
      <h2>
        <button className="linky" onClick={() => onPick(item.symbol)}>
          {item.label}
        </button>
        <span className="scope">
          {item.status === "closed"
            ? `${REASON[item.closeReason ?? "manual"] ?? ""} 로 닫힘`
            : `${item.sharesLeft.toLocaleString("ko-KR")}주`}
        </span>
      </h2>

      {/* --- 닿았다. 사람이 정할 자리 --- */}
      {pending.map((hit) => (
        <div className="note warn" key={hit.hitAt}>
          <b>{money(hit.price, item.currency)} 에 닿았다.</b> 실제로 팔았나?
          <div className="chips">
            <button className="chip" disabled={busy}
                    onClick={() => sell(hit.price,
                      item.sharesLeft * (hit.kind === "stop" ? 1 : 0.5),
                      hit.kind === "stop" ? "stop" : "target")}>
              팔았다
            </button>
            <button className="chip" disabled={busy}
                    onClick={() => run(api.held(item.id))}>
              안 팔았다 — 손절선 올리기
            </button>
          </div>
        </div>
      ))}

      {/* **합계가 답이다.** 여기선 숫자를 접지 않고 제일 크게 둔다 — "얼마 벌었나" 를
          말로 바꾸면 오히려 못 읽는다. 말이 필요한 건 "그래서 지금 어디쯤인가" 다. */}
      <div className="rows" style={{ marginTop: 8 }}>
        <div className="row">
          <span>지금까지</span>
          <b style={{ color: tone(total), fontSize: 15 }}>
            {signed(total, item.currency)} {totalPct !== null && `(${pct(totalPct)})`}
          </b>
        </div>
      </div>
      {roomWords(item.price, item.entry, item.stop, item.targets) && (
        <p className="plain">
          {roomWords(item.price, item.entry, item.stop, item.targets)}
        </p>
      )}
      <Numbers>
        <div className="rows">
          <div className="row">
            <span>진입</span>
            <b>{money(item.entry, item.currency)} × {item.shares.toLocaleString("ko-KR")}주</b>
          </div>
          <div className="row">
            <span>지금</span>
            <b>{item.price === null ? "시세 못 받음" : money(item.price, item.currency)}</b>
          </div>
          <div className="row">
            <span>평가손익</span>
            <b style={{ color: tone(item.unrealized) }}>
              {signed(item.unrealized, item.currency)}
            </b>
          </div>
          {item.realized !== 0 && (
            <div className="row">
              <span>실현손익</span>
              <b style={{ color: tone(item.realized) }}>
                {signed(item.realized, item.currency)}
              </b>
            </div>
          )}
        </div>
      </Numbers>

      {item.status === "open" && (
        <div className="rows" style={{ marginTop: 8 }}>
          <div className="row">
            <span>손절선</span>
            <b>
              {item.stop > 0 ? money(item.stop, item.currency) : "없음"}
              {item.stop > 0 && ` (${pct((item.stop / item.entry - 1) * 100)})`}
            </b>
          </div>
          {item.targets.map((target) => (
            <div className="row" key={target.price}>
              <span>
                {target.label} · {Math.round(target.portion * 100)}%
              </span>
              <b style={{ opacity: target.settledAt ? 0.5 : 1 }}>
                {money(target.price, item.currency)}
                {` (${pct((target.price / item.entry - 1) * 100)})`}
                {target.settledAt ? " · 정함" : target.hitAt ? " · 닿음" : ""}
              </b>
            </div>
          ))}
        </div>
      )}

      {item.band && (
        <p className="formula" style={{ marginTop: 8 }}>
          근거: {item.days}일 안에 {item.band[0].toFixed(1)}% ~ {item.band[1].toFixed(1)}%
          안에 있을 확률 80%. 손절선은 그 아래끝이다.
        </p>
      )}

      {item.status === "open" && (
        <>
          <div className="ask-form" style={{ marginTop: 8 }}>
            <input inputMode="decimal" placeholder="판 값"
                   value={sellPrice} onChange={(e) => setSellPrice(e.target.value)} />
            <input inputMode="decimal" placeholder={`주수 (최대 ${item.sharesLeft})`}
                   value={sellShares} onChange={(e) => setSellShares(e.target.value)} />
            <button disabled={busy} onClick={() => {
              const price = num(sellPrice);
              const count = num(sellShares);
              if (price === null || count === null) {
                setNote("판 값과 주수를 숫자로 넣어라");
                return;
              }
              setSellPrice("");
              setSellShares("");
              sell(price, count, "manual");
            }}>
              팔았다
            </button>
          </div>
          <div className="chips">
            <button className="chip" disabled={busy}
                    onClick={() => run(api.close(item.id, "manual"))}>
              계획 접기 (알림 거두기)
            </button>
            <button className="chip" disabled={busy}
                    onClick={() => run(api.remove(item.id))}>
              잘못 적었다 — 지우기
            </button>
          </div>
        </>
      )}

      {item.status === "closed" && (
        <div className="chips">
          <button className="chip" disabled={busy}
                  onClick={() => api.advice(item.id, item.days ?? 1)
                    .then(setAdvice)
                    .catch((e) => setNote(String(e.message ?? e)))}>
            다음에 할 일
          </button>
        </div>
      )}

      {advice && <Advice advice={advice} currency={item.currency} />}
      {note && <p className="note" style={{ marginTop: 8 }}>{note}</p>}

      <details style={{ marginTop: 8 }}>
        <summary className="formula">있었던 일 {item.events.length}</summary>
        <div className="rows" style={{ marginTop: 6 }}>
          {item.events.map((event, index) => (
            <div className="row" key={`${event.at}-${index}`}>
              <span>{event.at.slice(5, 16).replace("T", " ")}</span>
              <b style={{ fontWeight: 400 }}>{event.text}</b>
            </div>
          ))}
        </div>
      </details>
    </section>
  );
}

function Advice({ advice, currency }: { advice: PositionAdvice; currency: string }) {
  return (
    <div className="note" style={{ marginTop: 8 }}>
      <p style={{ margin: 0 }}>{advice.why}</p>
      <p style={{ margin: "6px 0 0" }}>
        <b>쉬는 기간: {advice.rest}</b> — {advice.restWhy}
      </p>
      {advice.reentry && (
        <p style={{ margin: "6px 0 0" }}>
          <b>재진입 값: {money(advice.reentry.price, currency)}</b> — {advice.reentry.how}
        </p>
      )}
      {advice.note && <p style={{ margin: "6px 0 0" }}>{advice.note}</p>}
    </div>
  );
}
