import type { Forecast, IndicatorSpec, PatternHit, Requested, Signal } from "../types";

function price(value: number | undefined): string {
  if (value === undefined || !Number.isFinite(value)) return "—";
  // 5만짜리 BTC 와 0.4짜리 알트가 같은 화면에 온다. 자릿수를 크기에서 정한다.
  const digits = Math.abs(value) >= 1000 ? 0 : Math.abs(value) >= 1 ? 2 : 6;
  return value.toLocaleString("ko-KR", { maximumFractionDigits: digits });
}

function percent(value: number | undefined, digits = 1): string {
  return value === undefined || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(digits)}%`;
}

export function SignalCard({ signal }: { signal: Signal | null }) {
  if (!signal) return null;
  return (
    <section className="card">
      <h2>종합 판단</h2>
      <div className="verdict" data-dir={signal.direction}>
        <strong>{signal.label}</strong>
        <span>신뢰도 {percent(signal.confidence, 0)}</span>
      </div>
      <div className="meter">
        <i style={{ width: `${Math.round(signal.confidence * 100)}%` }} />
      </div>
      <ul className="reasons">
        {signal.reasons.slice(0, 8).map((reason) => (
          <li key={reason.key} data-dir={reason.direction}>
            <b />
            <span>{reason.text}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function ForecastCard({ forecast }: { forecast: Forecast | null }) {
  if (!forecast) return null;
  const { stat, monteCarlo, ml } = forecast.layers;

  return (
    <section className="card">
      <h2>{forecast.horizon}봉 뒤 예측</h2>
      {!stat.available ? (
        <p className="formula">{stat.reason}</p>
      ) : (
        <div className="rows">
          <div className="row">
            <span>중심</span>
            <b>
              {price(stat.mid)} ({stat.expectedMovePct !== undefined
                ? `${stat.expectedMovePct >= 0 ? "+" : ""}${stat.expectedMovePct.toFixed(2)}%`
                : "—"})
            </b>
          </div>
          {stat.bands &&
            Object.entries(stat.bands).map(([level, band]) => (
              <div className="row" key={level}>
                <span>{level}% 구간</span>
                <b>
                  {price(band.low)} ~ {price(band.high)}
                </b>
              </div>
            ))}
          {stat.atrBand && (
            <div className="row">
              <span>ATR 도달범위</span>
              <b>
                {price(stat.atrBand.low)} ~ {price(stat.atrBand.high)}
              </b>
            </div>
          )}
          {monteCarlo.available && (
            <div className="row">
              <span>상승 확률 (부트스트랩)</span>
              <b>{percent(monteCarlo.probUp, 1)}</b>
            </div>
          )}
          <div className="row">
            <span>학습 모델</span>
            <b>
              {ml.available
                ? `${ml.direction === 1 ? "상승" : ml.direction === -1 ? "하락" : "중립"} ${percent(ml.confidence, 0)}`
                : "미학습"}
            </b>
          </div>
        </div>
      )}
      <p className="formula" style={{ marginTop: 10, marginBottom: 0 }}>
        구간은 로그수익률 표준편차를 √N 으로 늘린 것이다. 변동성이 뭉치는 실제 시장에서는
        낙관적인 하한이라 ATR 범위와 같이 본다.
      </p>
    </section>
  );
}

export function PatternCard({ patterns }: { patterns: PatternHit[] }) {
  if (patterns.length === 0) return null;
  return (
    <section className="card">
      <h2>최근 캔들 패턴</h2>
      <div className="pattern-tags">
        {patterns.slice(0, 8).map((hit, index) => (
          <span className="tag" data-dir={hit.direction} key={`${hit.key}-${index}`}>
            {hit.label}
            {hit.bars_ago > 0 ? ` · ${hit.bars_ago}봉 전` : ""}
          </span>
        ))}
      </div>
    </section>
  );
}

interface PickerProps {
  catalog: IndicatorSpec[];
  categories: Array<{ key: string; label: string }>;
  selected: Requested[];
  onChange: (next: Requested[]) => void;
}

export function IndicatorPicker({ catalog, categories, selected, onChange }: PickerProps) {
  const isOn = (key: string) => selected.some((s) => s.key === key);

  const toggle = (key: string) => {
    onChange(
      isOn(key) ? selected.filter((s) => s.key !== key) : [...selected, { key, params: {} }],
    );
  };

  return (
    <section className="card">
      <h2>지표 ({selected.length})</h2>
      <div className="indicator-list">
        {categories.map((category) => {
          const group = catalog.filter((spec) => spec.category === category.key);
          if (group.length === 0) return null;
          return (
            <div key={category.key}>
              <div className="group-label">{category.label}</div>
              {group.map((spec) => (
                <label className="indicator-row" key={spec.key} title={spec.formula}>
                  <input type="checkbox" checked={isOn(spec.key)} onChange={() => toggle(spec.key)} />
                  <span>{spec.name}</span>
                  <em>{spec.pane === "own" ? "별도" : "가격"}</em>
                </label>
              ))}
            </div>
          );
        })}
      </div>
    </section>
  );
}

export function FormulaCard({ specs, selected }: { specs: IndicatorSpec[]; selected: Requested[] }) {
  const shown = specs.filter((spec) => selected.some((s) => s.key === spec.key)).slice(0, 4);
  if (shown.length === 0) return null;
  return (
    <section className="card">
      <h2>공식</h2>
      <div className="rows">
        {shown.map((spec) => (
          <div key={spec.key} style={{ marginBottom: 6 }}>
            <b>{spec.name}</b>
            <p className="formula" style={{ margin: "2px 0 0" }}>
              {spec.formula}
            </p>
            {spec.source && (
              <p className="formula" style={{ margin: "2px 0 0", opacity: 0.7 }}>
                {spec.source}
              </p>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
