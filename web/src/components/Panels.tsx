import type {
  EventMark,
  Evidence,
  Forecast,
  IndicatorSpec,
  PatternHit,
  Requested,
  Signal,
  Situation,
} from "../types";
import { directionWords, verdictWords } from "../say";
import { Numbers } from "./Numbers";

function price(value: number | undefined): string {
  if (value === undefined || !Number.isFinite(value)) return "—";
  // 5만짜리 BTC 와 0.4짜리 알트가 같은 화면에 온다. 자릿수를 크기에서 정한다.
  const digits = Math.abs(value) >= 1000 ? 0 : Math.abs(value) >= 1 ? 2 : 6;
  return value.toLocaleString("ko-KR", { maximumFractionDigits: digits });
}

function percent(value: number | undefined, digits = 1): string {
  return value === undefined || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(digits)}%`;
}

export function SignalCard({ signal, timeframe, basis }: {
  signal: Signal | null;
  /** 이 판단이 선 봉. 안 적으면 어느 질문에 대한 답인지 알 수 없다. */
  timeframe?: string;
  /** 아침 추천이 선 봉. 이것과 다르면 둘은 다른 이야기를 하고 있는 것이다. */
  basis?: string;
}) {
  if (!signal) return null;
  // **봉이 다르면 판단도 다르다.** SOLUSDT 가 같은 날 1시간봉에서 '매도'(신뢰 0.26),
  // 일봉에서 '매수'(신뢰 0.92) 였다. 추천은 일봉으로 계산한 것이라 1시간봉을 보면
  // "추천은 사라는데 판단은 팔라네" 로 읽힌다. 둘 다 맞고 질문이 다른 것이다.
  const mismatched = Boolean(timeframe && basis && timeframe !== basis);
  return (
    <section className="card">
      <h2>
        종합 판단
        {timeframe && <span className="scope">{timeframe} 기준</span>}
      </h2>
      <div className="verdict" data-dir={signal.direction}>
        <strong>{signal.label}</strong>
      </div>
      {/* **신뢰도 % 를 그대로 내보내지 않는다.** `signals/engine.py` 의 주석대로
          관망일 때의 신뢰도는 "관망이라는 판단"의 신뢰도가 아니라 **쏠림의 크기**다.
          그래서 `관망 · 신뢰도 5%` 는 "규칙들이 거의 완전히 상쇄됐다" 는 뜻인데
          숫자로만 내놓으면 "관망을 5%만 확신한다" 로 정반대로 읽힌다. 실제로 그렇게
          읽혔다. 말로 옮기고 숫자는 아래 접어 둔다. */}
      <p className="plain">{verdictWords(signal.direction, signal.confidence)}.</p>
      {mismatched && (
        <p className="mismatch">
          이건 <b>{timeframe} 봉</b>을 보고 한 판단이다. 아침 추천은 <b>{basis} 봉</b>으로
          1~3일을 본 것이라 <b>서로 다른 질문의 답</b>이다 — 어긋나 보여도 둘 중 하나가
          틀린 게 아니다. 추천과 맞춰 보려면 위에서 <b>{basis}</b> 를 고를 것.
        </p>
      )}
      <div className="meter">
        <i style={{ width: `${Math.round(signal.confidence * 100)}%` }} />
      </div>
      <Numbers label="숫자 보기 (쏠림의 크기)">
        <div className="row">
          <span>규칙 쏠림</span>
          <b>{percent(signal.confidence, 0)}</b>
        </div>
        <p style={{ margin: "4px 0 0" }}>
          매수·매도로 넘어가는 문턱은 이 눈금의 24% 다. 그 아래면 관망이고,
          <b> 낮을수록 "약한 관망" 이 아니라 "신호가 없다"</b> 에 가깝다.
        </p>
      </Numbers>
      <div className="group-label">그렇게 본 까닭</div>
      <ul className="reasons">
        {signal.reasons.slice(0, 8).map((reason) => (
          <li key={reason.key} data-dir={reason.direction}>
            <b />
            <span>{reason.text}</span>
          </li>
        ))}
      </ul>
      <p className="formula" style={{ marginTop: 10, marginBottom: 0 }}>
        이건 <b>어느 쪽인가</b>에 대한 규칙들의 투표다. 그런데 이 도구가 방향을 맞히는
        비율은 동전던지기를 조금 넘는 정도다 — 위 문장들을 <b>왜 그렇게 봤는지 읽는
        용도</b>지 이대로 따르라는 뜻이 아니다.
        <b> 이 화면에서 믿을 만한 쪽은 위 칸의 '얼마나 움직일까' 다.</b>
      </p>
    </section>
  );
}

export function ForecastCard({ forecast }: { forecast: Forecast | null }) {
  if (!forecast) return null;
  const { stat, monteCarlo, ml } = forecast.layers;
  // 80% 구간의 반폭을 현재가 대비 %로. "앞으로 이만큼 움직인다"를 한 숫자로 읽게.
  const wide = stat.bands?.["80"];
  const band80 =
    wide && stat.mid ? ((wide.high - wide.low) / 2 / stat.mid) * 100 : null;

  return (
    <section className="card">
      <h2>{forecast.horizon}봉 뒤 예측</h2>
      {!stat.available ? (
        <p className="formula">{stat.reason}</p>
      ) : (
        <>
          {/* **폭을 맨 위에, 말로.** 이 도구가 실제로 맞히는 건 여기다. 방향은
              훨씬 덜 맞으므로 아래로 내리고 숫자와 함께 접는다. */}
          {band80 !== null && (
            <p className="plain" style={{ marginTop: 0 }}>
              앞으로 {forecast.horizon}봉 동안 <b>지금 값에서 ±{band80.toFixed(1)}%</b>
              {" "}안에서 움직일 가능성이 큽니다. <b>이 도구가 실제로 맞히는 건 이
              폭입니다</b> — 어느 쪽으로 갈지는 훨씬 덜 맞습니다.
            </p>
          )}
          <p className="plain">
            {directionWords(monteCarlo.available ? monteCarlo.probUp : null)}.
            {ml.available
              ? " 학습 모델도 같이 봤습니다."
              : " 이 종목·봉은 아직 학습을 안 했습니다."}
          </p>
          <Numbers>
            <div className="rows">
              {stat.bands &&
                Object.entries(stat.bands).map(([level, band]) => (
                  <div className="row" key={level}>
                    <span>{level}% 구간</span>
                    <b>{price(band.low)} ~ {price(band.high)}</b>
                  </div>
                ))}
              {stat.atrBand && (
                <div className="row">
                  <span>ATR 도달범위</span>
                  <b>{price(stat.atrBand.low)} ~ {price(stat.atrBand.high)}</b>
                </div>
              )}
              <div className="row">
                <span>중심 (방향)</span>
                <b>
                  {price(stat.mid)} ({stat.expectedMovePct !== undefined
                    ? `${stat.expectedMovePct >= 0 ? "+" : ""}${stat.expectedMovePct.toFixed(2)}%`
                    : "—"})
                </b>
              </div>
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
            <p style={{ margin: "6px 0 0" }}>
              구간은 로그수익률 표준편차를 √N 으로 늘린 것이다. 변동성이 뭉치는 실제
              시장에서는 낙관적인 하한이라 ATR 범위와 같이 본다.
            </p>
          </Numbers>
        </>
      )}
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

export function SituationCard({ situation }: { situation: Situation | null }) {
  if (!situation?.available || !situation.regime) return null;
  const r = situation.regime;
  return (
    <section className="card">
      <h2>지금 상황</h2>
      <div className="rows">
        <div className="row">
          <span>레짐</span>
          <b>
            {r.volatilityLabel} · {r.trendLabel}
          </b>
        </div>
        <div className="row">
          <span>변동성 백분위</span>
          <b>{(r.volPercentile * 100).toFixed(0)}%</b>
        </div>
        <div className="row">
          <span>ADX</span>
          <b>{r.adx ?? "—"}</b>
        </div>
        <div className="row">
          <span>시점</span>
          <b>{situation.calendar?.text}</b>
        </div>
      </div>
      <p className="formula" style={{ marginBottom: 0 }}>
        유사구간을 찾을 때 이 축들을 조건으로 쓴다. 요일·시간대가 수익률을 만든다는 뜻이
        아니라, 비슷한 자리를 고르는 기준이라는 뜻이다.
      </p>
    </section>
  );
}

export function EventsCard({
  events,
  sources,
}: {
  events: EventMark[];
  sources: Record<string, { count: number; ok: boolean; error: string }>;
}) {
  const failed = Object.entries(sources).filter(([, v]) => !v.ok);
  return (
    <section className="card">
      <h2>사건 ({events.length})</h2>
      <div className="chips" style={{ marginBottom: 8 }}>
        {Object.entries(sources).map(([key, value]) => (
          <span
            className="tag"
            key={key}
            title={value.error || undefined}
            data-dir={value.ok ? undefined : -1}
          >
            {key} {value.ok ? value.count : "실패"}
          </span>
        ))}
      </div>
      {failed.length > 0 && (
        <p className="note warn">
          {failed.map(([key]) => key).join(", ")} 소스를 못 읽었다. 나머지 소스로만
          계산한 결과다.
        </p>
      )}
      <div className="indicator-list">
        {events.slice(0, 60).map((event) => (
          <div className="event-row" key={event.id}>
            <b style={{ opacity: 0.4 + event.severity * 0.6 }}>
              {new Date(event.ts).toISOString().slice(0, 10)}
            </b>
            <span>{event.title}</span>
            <em>{event.sourceLabel}</em>
          </div>
        ))}
      </div>
    </section>
  );
}

const CONFIDENCE_COLOR: Record<Evidence["confidence"], string> = {
  strong: "var(--up)",
  moderate: "var(--accent)",
  weak: "var(--warn)",
  contested: "var(--down)",
};

export function EvidenceLibrary({ entries }: { entries: Evidence[] }) {
  const fields = Array.from(new Set(entries.map((e) => e.field)));
  return (
    <section className="card">
      <h2>근거 등록부 ({entries.length})</h2>
      <p className="formula" style={{ marginTop: 0 }}>
        이 프로그램이 쓰는 방법론과 그 출처다. '논쟁 중' 표시는 실제로 반대 결론을 낸
        연구가 있다는 뜻이다 — 감추지 않는다.
      </p>
      {fields.map((field) => (
        <div key={field}>
          <div className="group-label">
            {entries.find((e) => e.field === field)?.fieldLabel}
          </div>
          {entries
            .filter((e) => e.field === field)
            .map((item) => (
              <details key={item.key} className="evidence">
                <summary>
                  <span
                    className="badge"
                    style={{
                      borderColor: CONFIDENCE_COLOR[item.confidence],
                      color: CONFIDENCE_COLOR[item.confidence],
                    }}
                  >
                    {item.confidenceLabel}
                  </span>
                  {item.claim}
                </summary>
                <p className="formula">
                  <b>효과</b> {item.effect}
                </p>
                <p className="formula">
                  <b>한계</b> {item.limits}
                </p>
                <ul className="sources">
                  {item.sources.map((source) => (
                    <li key={source.title}>
                      {source.url ? (
                        <a href={source.url} target="_blank" rel="noreferrer">
                          {source.title}
                        </a>
                      ) : (
                        source.title
                      )}
                      <em>
                        {" "}
                        — {source.authors} ({source.year}
                        {source.venue ? `, ${source.venue}` : ""})
                      </em>
                    </li>
                  ))}
                </ul>
              </details>
            ))}
        </div>
      ))}
    </section>
  );
}
