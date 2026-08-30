import { useEffect, useState } from "react";

import type { AskResult, EventStudy, Evidence, Projection, ScenarioForm } from "../types";
import { analogWords, coverageWords, eventStudyWords } from "../say";
import { Numbers } from "./Numbers";

const PRESETS: Array<{ label: string; hours: number }> = [
  { label: "1일", hours: 24 },
  { label: "3일", hours: 72 },
  { label: "1주일", hours: 168 },
  { label: "2주일", hours: 336 },
  { label: "1개월", hours: 720 },
];

const EXAMPLES = [
  "일주일 이내 차트 어떻게 될까?",
  "급락 나온 뒤 3일 동안 어떻게 움직였어?",
  "금리 발표 뒤 한 달은?",
  "고변동 구간에서 이틀 뒤",
];

const EMPHASIS = [
  { key: "trend", label: "추세" },
  { key: "momentum", label: "모멘텀" },
  { key: "position", label: "위치" },
  { key: "volatility", label: "변동성" },
  { key: "volume", label: "거래량" },
  { key: "regime", label: "레짐" },
  { key: "calendar", label: "캘린더" },
];

const CONFIDENCE_COLOR: Record<Evidence["confidence"], string> = {
  strong: "var(--up)",
  moderate: "var(--accent)",
  weak: "var(--warn)",
  contested: "var(--down)",
};

/** 거리는 0에 가까울수록 비슷하다. 숫자만 던지면 0.33 이 좋은 건지 나쁜 건지 모른다. */
function closeness(distance: number): string {
  if (distance < 0.2) return "매우 비슷";
  if (distance < 0.4) return "비슷";
  if (distance < 0.7) return "느슨하게 비슷";
  return "먼 편";
}

function pct(value: number | undefined, digits = 1): string {
  return value === undefined || !Number.isFinite(value) ? "—" : `${value.toFixed(digits)}%`;
}

function toForm(result: AskResult): ScenarioForm {
  const s = result.scenario;
  return {
    horizon_hours: s.horizonHours,
    horizon_text: s.horizonText,
    event_kinds: s.eventKinds,
    event_tags: s.eventTags,
    require_volatility: s.requireVolatility,
    require_trend: s.requireTrend,
    emphasis: s.emphasis,
    context_weight: s.contextWeight,
    direction_hint: s.directionHint,
    interpretation: s.interpretation,
  };
}

interface Props {
  result: AskResult | null;
  busy: boolean;
  error: string | null;
  onAsk: (question: string, form: ScenarioForm | null) => void;
  onClear: () => void;
}

export function AskPanel({ result, busy, error, onAsk, onClear }: Props) {
  const [question, setQuestion] = useState("");
  const [form, setForm] = useState<ScenarioForm | null>(null);
  const [editing, setEditing] = useState(false);

  // 서버가 해석한 조건을 폼의 출발점으로 삼는다. 사람이 그 위에서 고친다.
  useEffect(() => {
    if (result) setForm(toForm(result));
  }, [result]);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!question.trim() || busy) return;
    onAsk(question, null);
  };

  const rerun = () => {
    if (!form || busy) return;
    onAsk(question || result?.scenario.question || "", form);
  };

  const patch = (next: Partial<ScenarioForm>) =>
    setForm((current) => (current ? { ...current, ...next } : current));

  return (
    <>
      <section className="card">
        <h2>질문</h2>
        <form onSubmit={submit} className="ask-form">
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="예: 급락 나온 뒤 3일 동안 어떻게 움직였어?"
            disabled={busy}
          />
          <button type="submit" disabled={busy || !question.trim()}>
            {busy ? "찾는 중" : "예측"}
          </button>
        </form>
        <div className="chips">
          {EXAMPLES.map((example) => (
            <button
              key={example}
              className="chip"
              disabled={busy}
              onClick={() => {
                setQuestion(example);
                onAsk(example, null);
              }}
            >
              {example}
            </button>
          ))}
        </div>
        {error && <p className="error" style={{ marginTop: 10 }}>{error}</p>}
      </section>

      {result && (
        <>
          <section className="card">
            <h2>
              답
              <button className="link" onClick={onClear}>
                지우기
              </button>
            </h2>
            <p className="answer">{result.answer}</p>

            <div className="interpretation">
              <span>
                이렇게 읽었다 ({result.scenario.parsedBy === "llm" ? "Claude"
                  : result.scenario.parsedBy === "form" ? "직접 지정" : "규칙"})
              </span>
              <b>{result.scenario.interpretation || "조건 없음"}</b>
            </div>

            {result.scenario.notes.map((note) => (
              <p className="note" key={note}>
                {note}
              </p>
            ))}

            <button className="link" onClick={() => setEditing((v) => !v)}>
              {editing ? "조건 닫기" : "조건 고치기"}
            </button>

            {editing && form && (
              <div className="form-grid">
                <label>
                  <span>기간</span>
                  <div className="chips">
                    {PRESETS.map((preset) => (
                      <button
                        key={preset.label}
                        className="chip"
                        data-active={form.horizon_hours === preset.hours}
                        onClick={() =>
                          patch({ horizon_hours: preset.hours, horizon_text: preset.label })
                        }
                      >
                        {preset.label}
                      </button>
                    ))}
                  </div>
                </label>

                <label>
                  <span>사건 태그 (쉼표로 구분)</span>
                  <input
                    value={form.event_tags.join(", ")}
                    onChange={(e) =>
                      patch({
                        event_tags: e.target.value
                          .split(",")
                          .map((t) => t.trim())
                          .filter(Boolean),
                      })
                    }
                    placeholder="rate, etf, crash …"
                  />
                </label>

                <label>
                  <span>변동성 레짐</span>
                  <select
                    value={form.require_volatility ?? ""}
                    onChange={(e) =>
                      patch({
                        require_volatility: e.target.value === "" ? null : Number(e.target.value),
                      })
                    }
                  >
                    <option value="">조건 없음</option>
                    <option value="0">저변동</option>
                    <option value="1">보통</option>
                    <option value="2">고변동</option>
                  </select>
                </label>

                <label>
                  <span>추세 레짐</span>
                  <select
                    value={form.require_trend ?? ""}
                    onChange={(e) =>
                      patch({
                        require_trend: e.target.value === "" ? null : Number(e.target.value),
                      })
                    }
                  >
                    <option value="">조건 없음</option>
                    <option value="1">상승추세</option>
                    <option value="0">횡보</option>
                    <option value="-1">하락추세</option>
                  </select>
                </label>

                <label>
                  <span>모양 ↔ 상황 ({form.context_weight.toFixed(2)})</span>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.05}
                    value={form.context_weight}
                    onChange={(e) => patch({ context_weight: Number(e.target.value) })}
                  />
                </label>

                <label>
                  <span>무겁게 볼 축</span>
                  <div className="chips">
                    {EMPHASIS.map((item) => {
                      const on = form.emphasis.includes(item.key);
                      return (
                        <button
                          key={item.key}
                          className="chip"
                          data-active={on}
                          onClick={() =>
                            patch({
                              emphasis: on
                                ? form.emphasis.filter((k) => k !== item.key)
                                : [...form.emphasis, item.key],
                            })
                          }
                        >
                          {item.label}
                        </button>
                      );
                    })}
                  </div>
                </label>

                <button onClick={rerun} disabled={busy}>
                  이 조건으로 다시
                </button>
              </div>
            )}
          </section>

          <ProjectionCard projection={result.projection} />
          <CasesCard projection={result.projection} />
          <EventStudyCard study={result.eventStudy} />
          <CitationsCard citations={result.citations} />
        </>
      )}
    </>
  );
}

function ProjectionCard({ projection }: { projection: Projection }) {
  if (!projection.available) {
    return (
      <section className="card">
        <h2>예측</h2>
        <p className="formula">{projection.reason}</p>
      </section>
    );
  }
  const d = projection.diagnostics;
  return (
    <section className="card">
      <h2>예측</h2>
      {/* **몇 건인지가 답의 무게다.** 사례 셋짜리 60%와 스물짜리 60%는 다른 말인데,
          `사례 수 12건` 이라고만 적혀 있으면 그 무게가 안 읽힌다. */}
      {analogWords(projection.sampleCount, d.distanceMin,
                   d.reliable).map((line, i) => (
        <p className="plain" key={i} style={{ marginTop: i === 0 ? 0 : 4 }}>
          {line.split("**").map((part, j) => (j % 2 === 1 ? <b key={j}>{part}</b> : part))}
        </p>
      ))}
      {coverageWords(d.coverage, d.nominalCoverage, d.widenFactor) && (
        <p className="plain">{coverageWords(d.coverage, d.nominalCoverage, d.widenFactor)}</p>
      )}
      <Numbers>
        <div className="rows">
          <div className="row">
            <span>사례 수</span>
            <b>{projection.sampleCount}건</b>
          </div>
          <div className="row">
            <span>중앙 경로</span>
            <b>{pct(projection.expectedMovePct, 2)}</b>
          </div>
          <div className="row">
            <span>상승 비중</span>
            <b>{pct((projection.probUp ?? 0) * 100, 0)}</b>
          </div>
          <div className="row">
            <span>가장 비슷한 정도</span>
            <b>
              {closeness(d.distanceMin)}
              <span style={{ color: "var(--text-dim)" }}> ({d.distanceMin.toFixed(2)})</span>
            </b>
          </div>
          <div className="row">
            <span>밴드 적중률</span>
            <b>
              {d.coverage === null ? "—" : `${(d.coverage * 100).toFixed(0)}%`}
              <span style={{ color: "var(--text-dim)" }}>
                {" "}/ 목표 {(d.nominalCoverage * 100).toFixed(0)}%
              </span>
            </b>
          </div>
          {d.widenFactor > 1.01 && (
            <div className="row">
              <span>밴드 보정</span>
              <b>×{d.widenFactor.toFixed(2)}</b>
            </div>
          )}
        </div>
      </Numbers>
      <p className="formula" style={{ marginBottom: 0 }}>
        차트의 회색 얇은 선이 과거 사례의 실제 경로, 파란 점선이 그 중앙값, 옅은 파란
        선이 10·25·75·90% 구간이다. 노란 점선은 같은 종류의 사건 이후 평균 경로.
      </p>
    </section>
  );
}

function CasesCard({ projection }: { projection: Projection }) {
  if (!projection.available || projection.paths.length === 0) return null;
  return (
    <section className="card">
      <h2>이 예측이 본 과거</h2>
      <div className="rows">
        {projection.paths.map((path) => (
          <div className="row" key={path.id}>
            <span>
              {new Date(path.windowStartTs).toISOString().slice(0, 10)} ~{" "}
              {new Date(path.windowEndTs).toISOString().slice(5, 10)}
            </span>
            <b style={{ color: path.outcome >= 0 ? "var(--up)" : "var(--down)" }}>
              {(path.outcome * 100).toFixed(2)}%
            </b>
          </div>
        ))}
      </div>
      <p className="formula" style={{ marginBottom: 0 }}>
        각 구간이 끝난 뒤 실제로 얼마나 움직였는지다. 날짜를 차트에서 직접 찾아보면
        이 예측이 무엇을 근거로 하는지 눈으로 확인할 수 있다.
      </p>
    </section>
  );
}

function EventStudyCard({ study }: { study: EventStudy }) {
  if (!study.available) return null;
  return (
    <section className="card">
      <h2>사건 이후</h2>
      {/* **`t 값 2.01 유의` 를 그대로 내보내지 않는다.** 그 표시가 뜻하는 것은
          "우연으로 보기 어렵다" 인데, t값을 모르면 못 읽고 알면 굳이 문장으로 안 읽는다.
          뜻을 풀고 숫자는 아래 접어 둔다. */}
      {eventStudyWords(study.count ?? 0, study.after ?? 0, study.finalCarPct ?? null,
                       Boolean(study.significant), study.hitRate ?? null,
                       Boolean(study.overlapping)).map((line, i) => (
        <p className="plain" key={i} style={{ marginTop: i === 0 ? 0 : 4 }}>
          {line.split("**").map((part, j) => (j % 2 === 1 ? <b key={j}>{part}</b> : part))}
        </p>
      ))}
      <Numbers>
        <div className="rows">
          <div className="row">
            <span>사건 수</span>
            <b>{study.count}건</b>
          </div>
          <div className="row">
            <span>{study.after}봉 뒤 누적 초과수익 (CAR)</span>
            <b style={{ color: (study.finalCarPct ?? 0) >= 0 ? "var(--up)" : "var(--down)" }}>
              {pct(study.finalCarPct, 2)}
            </b>
          </div>
          <div className="row">
            <span>t 값</span>
            <b>
              {study.finalTStat?.toFixed(2)}{" "}
              <span style={{ color: study.significant ? "var(--up)" : "var(--text-dim)" }}>
                {study.significant ? "유의" : "유의하지 않음"}
              </span>
            </b>
          </div>
          <div className="row">
            <span>방향 일치율</span>
            <b>{pct((study.hitRate ?? 0) * 100, 0)}</b>
          </div>
        </div>
      </Numbers>
    </section>
  );
}

function CitationsCard({ citations }: { citations: Evidence[] }) {
  if (!citations || citations.length === 0) return null;
  return (
    <section className="card">
      <h2>이 예측이 기댄 근거</h2>
      <div className="rows">
        {citations.map((item) => (
          <details key={item.key} className="evidence">
            <summary>
              <span
                className="badge"
                style={{ borderColor: CONFIDENCE_COLOR[item.confidence],
                         color: CONFIDENCE_COLOR[item.confidence] }}
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
    </section>
  );
}
