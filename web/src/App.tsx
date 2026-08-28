import { useEffect, useMemo, useState } from "react";

import { api } from "./api";
import { ChartStack } from "./components/Chart";
import {
  ForecastCard,
  FormulaCard,
  IndicatorPicker,
  PatternCard,
  SignalCard,
} from "./components/Panels";
import { useLive } from "./useLive";
import type { Forecast, IndicatorSpec, PatternHit, ProviderInfo, Requested } from "./types";

const HORIZON = 10;

export default function App() {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [catalog, setCatalog] = useState<IndicatorSpec[]>([]);
  const [categories, setCategories] = useState<Array<{ key: string; label: string }>>([]);
  const [selected, setSelected] = useState<Requested[]>([]);

  const [provider, setProvider] = useState("binance");
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [timeframe, setTimeframe] = useState("1h");
  const [draft, setDraft] = useState("BTCUSDT");

  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [patterns, setPatterns] = useState<PatternHit[]>([]);
  const [bootError, setBootError] = useState<string | null>(null);

  // 프로바이더·지표 카탈로그는 한 번만 받는다.
  useEffect(() => {
    Promise.all([api.providers(), api.catalog()])
      .then(([p, c]) => {
        setProviders(p.providers);
        setCatalog(c.indicators);
        setCategories(c.categories);
        setSelected(c.defaults);
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

  // 예측은 봉이 닫힐 때만 다시 받는다. 미확정 봉마다 부르면 숫자가 계속 떨린다.
  const lastClosedTs = useMemo(() => {
    for (let i = live.candles.length - 1; i >= 0; i -= 1) {
      if (live.candles[i].closed) return live.candles[i].time;
    }
    return null;
  }, [live.candles]);

  useEffect(() => {
    if (lastClosedTs === null) return;
    let cancelled = false;
    api
      .analyze({ provider, symbol, timeframe, horizon: HORIZON, indicators: selected })
      .then((res) => {
        if (cancelled) return;
        setForecast(res.forecast);
        setPatterns(res.patterns);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
    // 지표 선택은 예측을 바꾸지 않으므로 의존성에서 뺀다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, symbol, timeframe, lastClosedTs]);

  const submit = (event: React.FormEvent) => {
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
  const change = last && previous ? (last.close / previous.close - 1) * 100 : null;

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

        <form onSubmit={submit}>
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="종목"
            style={{ width: 130 }}
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
                style={{
                  marginLeft: 8,
                  color: change >= 0 ? "var(--up)" : "var(--down)",
                }}
              >
                {change >= 0 ? "+" : ""}
                {change.toFixed(2)}%
              </span>
            )}
          </span>
        )}

        <div className="spacer" />
        <div className="status">
          <i className="dot" data-state={live.status} />
          {live.status === "live"
            ? "실시간"
            : live.status === "connecting"
              ? "연결 중"
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
        <ChartStack candles={live.candles} indicators={live.indicators} />
      )}

      <aside className="side">
        <SignalCard signal={live.signal} />
        <ForecastCard forecast={forecast} />
        <PatternCard patterns={patterns} />
        <IndicatorPicker
          catalog={catalog}
          categories={categories}
          selected={selected}
          onChange={setSelected}
        />
        <FormulaCard specs={catalog} selected={selected} />
      </aside>
    </div>
  );
}
