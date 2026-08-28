import { useCallback, useEffect, useMemo, useState } from "react";

import { api, learn, predict, research } from "./api";
import { AskPanel } from "./components/AskPanel";
import { ChartStack } from "./components/Chart";
import { LearnPanel } from "./components/LearnPanel";
import {
  EventsCard,
  EvidenceLibrary,
  ForecastCard,
  FormulaCard,
  IndicatorPicker,
  PatternCard,
  SignalCard,
  SituationCard,
} from "./components/Panels";
import { useLive } from "./useLive";
import type {
  AskResult,
  EventMark,
  Evidence,
  Forecast,
  IndicatorSpec,
  PatternHit,
  ProviderInfo,
  Learned,
  Requested,
  ScenarioForm,
  Situation,
} from "./types";

const HORIZON = 10;
// 실시간 구독이 받아오는 봉 수. 사건 조회도 같은 길이를 쓴다.
const CHART_BARS = 600;

type Tab = "signal" | "predict" | "learn" | "indicators" | "research";

const TABS: Array<{ key: Tab; label: string }> = [
  { key: "predict", label: "예측" },
  { key: "learn", label: "학습" },
  { key: "signal", label: "판단" },
  { key: "indicators", label: "지표" },
  { key: "research", label: "근거" },
];

export default function App() {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [catalog, setCatalog] = useState<IndicatorSpec[]>([]);
  const [categories, setCategories] = useState<Array<{ key: string; label: string }>>([]);
  const [selected, setSelected] = useState<Requested[]>([]);
  const [evidence, setEvidence] = useState<Evidence[]>([]);

  const [provider, setProvider] = useState("binance");
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [timeframe, setTimeframe] = useState("1h");
  const [draft, setDraft] = useState("BTCUSDT");
  const [tab, setTab] = useState<Tab>("predict");

  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [patterns, setPatterns] = useState<PatternHit[]>([]);
  const [situation, setSituation] = useState<Situation | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);

  const [ask, setAsk] = useState<AskResult | null>(null);
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);

  const [learned, setLearned] = useState<Learned | null>(null);
  const [training, setTraining] = useState(false);
  const [trainError, setTrainError] = useState<string | null>(null);
  const [trainNote, setTrainNote] = useState<string | null>(null);
  const [skipped, setSkipped] = useState<Array<{ symbol: string; reason: string }>>([]);

  const [events, setEvents] = useState<EventMark[]>([]);
  const [eventSources, setEventSources] = useState<
    Record<string, { count: number; ok: boolean; error: string }>
  >({});
  const [showEvents, setShowEvents] = useState(true);

  useEffect(() => {
    Promise.all([api.providers(), api.catalog(), research.library()])
      .then(([p, c, r]) => {
        setProviders(p.providers);
        setCatalog(c.indicators);
        setCategories(c.categories);
        setSelected(c.defaults);
        setEvidence(r.entries);
      })
      .catch((err) => setBootError(String(err.message ?? err)));
  }, []);

  const current = providers.find((p) => p.key === provider);
  const timeframes = current?.timeframes ?? ["1m", "5m", "15m", "1h", "4h", "1d"];

  const live = useLive({
    provider,
    symbol,
    timeframe,
    indicators: selected,
    enabled: catalog.length > 0,
  });

  const lastClosedTs = useMemo(() => {
    for (let i = live.candles.length - 1; i >= 0; i -= 1) {
      if (live.candles[i].closed) return live.candles[i].time;
    }
    return null;
  }, [live.candles]);

  // 예측·패턴은 봉이 닫힐 때만 다시 받는다. 미확정 봉마다 부르면 숫자가 계속 떨린다.
  useEffect(() => {
    if (lastClosedTs === null) return;
    let cancelled = false;
    api
      .analyze({ provider, symbol, timeframe, horizon: HORIZON, indicators: selected })
      .then((res) => {
        if (cancelled) return;
        setForecast(res.forecast);
        setPatterns(res.patterns);
        setSituation(res.situation);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, symbol, timeframe, lastClosedTs]);

  // 사건은 종목·봉이 바뀔 때만. 실시간마다 다시 긁으면 외부 API 한도를 먼저 태운다.
  useEffect(() => {
    let cancelled = false;
    setEvents([]);
    // 차트가 보여주는 구간과 같은 길이로 받는다. 더 넓게 받으면 "사건 106건" 이라 해놓고
    // 화면에는 세 개만 찍혀 숫자가 거짓말이 된다.
    predict
      .events({ provider, symbol, timeframe, limit: CHART_BARS })
      .then((res) => {
        if (cancelled) return;
        setEvents(res.events);
        setEventSources(res.sources);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [provider, symbol, timeframe]);

  const runAsk = useCallback(
    (question: string, form: ScenarioForm | null) => {
      setAsking(true);
      setAskError(null);
      predict
        .ask({ provider, symbol, timeframe, question, form, use_llm: true })
        .then(setAsk)
        .catch((err) => setAskError(String(err.message ?? err)))
        .finally(() => setAsking(false));
    },
    [provider, symbol, timeframe],
  );

  // 종목이나 봉이 바뀌면 예측을 지운다. 다른 종목의 경로가 남아 있으면 그게 제일 위험하다.
  useEffect(() => {
    setAsk(null);
    setAskError(null);
    setLearned(null);
    setTrainError(null);
    setTrainNote(null);
    setSkipped([]);
  }, [provider, symbol, timeframe]);

  // 이 종목·봉으로 학습된 모델이 이미 있으면 바로 불러온다.
  useEffect(() => {
    let cancelled = false;
    learn
      .predict({ provider, symbol, timeframe })
      .then((res) => {
        if (!cancelled) setLearned(res);
      })
      .catch(() => {
        if (!cancelled) setLearned(null);
      });
    return () => {
      cancelled = true;
    };
  }, [provider, symbol, timeframe]);

  const runTrain = useCallback(
    (horizon: number) => {
      setTraining(true);
      setTrainError(null);
      learn
        .train({ provider, symbol, timeframe, horizon, limit: 3000 })
        .then((res) => {
          setTrainNote(res.note);
          setSkipped(res.skipped ?? []);
          return learn.predict({ provider, symbol, timeframe });
        })
        .then(setLearned)
        .catch((err) => setTrainError(String(err.message ?? err)))
        .finally(() => setTraining(false));
    },
    [provider, symbol, timeframe],
  );

  const submitSymbol = (event: React.FormEvent) => {
    event.preventDefault();
    const next = draft.trim();
    if (next) setSymbol(next.toUpperCase());
  };

  const switchProvider = (key: string) => {
    const info = providers.find((p) => p.key === key);
    setProvider(key);
    const first = info?.defaultSymbols[0] ?? "";
    setSymbol(first);
    setDraft(first);
    if (info && !info.timeframes.includes(timeframe)) setTimeframe(info.timeframes[0]);
  };

  const last = live.candles.at(-1);
  const previous = live.candles.at(-2);
  const rawChange = last && previous ? (last.close / previous.close - 1) * 100 : null;
  // -0.00% 처럼 보이지 않게. 부호만 있고 값이 없는 표시는 읽는 사람을 헷갈리게 한다.
  const change = rawChange !== null && Math.abs(rawChange) < 0.005 ? 0 : rawChange;

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">market-lens</span>

        <select value={provider} onChange={(e) => switchProvider(e.target.value)}>
          {providers.map((p) => (
            <option key={p.key} value={p.key} disabled={!p.available}>
              {p.name}
              {p.available ? "" : " (키 필요)"}
            </option>
          ))}
        </select>

        <form onSubmit={submitSymbol}>
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="종목"
            style={{ width: 120 }}
          />
        </form>

        {timeframes.map((tf) => (
          <button key={tf} data-active={tf === timeframe} onClick={() => setTimeframe(tf)}>
            {tf}
          </button>
        ))}

        {last && (
          <span style={{ marginLeft: 4 }}>
            <b style={{ fontSize: 16 }}>
              {last.close.toLocaleString("ko-KR", { maximumFractionDigits: 6 })}
            </b>
            {change !== null && (
              <span
                style={{ marginLeft: 8, color: change >= 0 ? "var(--up)" : "var(--down)" }}
              >
                {change >= 0 ? "+" : ""}
                {change.toFixed(2)}%
              </span>
            )}
          </span>
        )}

        <div className="spacer" />

        <button data-active={showEvents} onClick={() => setShowEvents((v) => !v)}>
          사건 {events.length ? `(${events.length})` : ""}
        </button>

        <div className="status">
          <i className="dot" data-state={live.status} />
          {live.status === "live"
            ? "실시간"
            : live.status === "connecting"
              ? "연결 중"
              : live.status === "static"
                ? (live.error ?? "과거 데이터만")
                : live.status === "error"
                  ? (live.error ?? "오류")
                  : "대기"}
        </div>
      </header>

      {bootError ? (
        <div className="error" style={{ gridColumn: "1 / -1" }}>
          서버에 연결하지 못했다: {bootError}
        </div>
      ) : (
        <ChartStack
          candles={live.candles}
          indicators={live.indicators}
          projection={ask?.projection ?? null}
          learned={learned}
          eventPath={ask?.eventPath ?? null}
          events={showEvents ? events : []}
        />
      )}

      <aside className="side">
        <nav className="tabs">
          {TABS.map((item) => (
            <button
              key={item.key}
              data-active={tab === item.key}
              onClick={() => setTab(item.key)}
            >
              {item.label}
            </button>
          ))}
        </nav>

        {tab === "predict" && (
          <>
            <SituationCard situation={ask?.situation ?? situation} />
            <AskPanel
              result={ask}
              busy={asking}
              error={askError}
              onAsk={runAsk}
              onClear={() => setAsk(null)}
            />
          </>
        )}

        {tab === "learn" && (
          <LearnPanel
            learned={learned}
            busy={training}
            error={trainError}
            note={trainNote}
            skipped={skipped}
            onTrain={runTrain}
          />
        )}

        {tab === "signal" && (
          <>
            <SignalCard signal={live.signal} />
            <ForecastCard forecast={forecast} />
            <PatternCard patterns={patterns} />
          </>
        )}

        {tab === "indicators" && (
          <>
            <IndicatorPicker
              catalog={catalog}
              categories={categories}
              selected={selected}
              onChange={setSelected}
            />
            <FormulaCard specs={catalog} selected={selected} />
          </>
        )}

        {tab === "research" && (
          <>
            <EventsCard events={events} sources={eventSources} />
            <EvidenceLibrary entries={evidence} />
          </>
        )}
      </aside>
    </div>
  );
}
