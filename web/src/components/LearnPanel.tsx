import { useState } from "react";

import type { Learned, TrainReport } from "../types";

const HORIZONS = [
  { label: "12봉", value: 12 },
  { label: "24봉", value: 24 },
  { label: "48봉", value: 48 },
];

function pct(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined || !Number.isFinite(value)
    ? "—"
    : `${(value * 100).toFixed(digits)}%`;
}

function signed(value: number | undefined, digits = 3): string {
  return value === undefined || !Number.isFinite(value)
    ? "—"
    : `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

interface Props {
  learned: Learned | null;
  busy: boolean;
  error: string | null;
  onTrain: (horizon: number) => void;
}

export function LearnPanel({ learned, busy, error, onTrain }: Props) {
  const [horizon, setHorizon] = useState(24);
  const report = learned?.report;

  return (
    <>
      <section className="card">
        <h2>학습</h2>
        <p className="formula" style={{ marginTop: 0 }}>
          상황 28축 + 유사구간 요약 + 사건 이력을 입력으로 넣고, 실제로 무슨 일이 났는지를
          학습한다. 사례를 답으로 쓰는 대신 <b>사례를 얼마나 믿을지</b>까지 배우게 하는 것이다.
        </p>
        <div className="chips">
          {HORIZONS.map((item) => (
            <button
              key={item.value}
              className="chip"
              data-active={horizon === item.value}
              disabled={busy}
              onClick={() => setHorizon(item.value)}
            >
              {item.label} 뒤
            </button>
          ))}
        </div>
        <button
          style={{ marginTop: 10, width: "100%" }}
          disabled={busy}
          onClick={() => onTrain(horizon)}
        >
          {busy ? "학습 중 (20초쯤 걸린다)" : "이 종목·봉으로 학습"}
        </button>
        {error && <p className="error" style={{ marginTop: 10 }}>{error}</p>}
      </section>

      {learned && !learned.available && (
        <section className="card">
          <h2>학습 예측</h2>
          <p className="formula" style={{ marginBottom: 0 }}>{learned.reason}</p>
        </section>
      )}

      {learned?.available && (
        <section className="card">
          <h2>학습 예측</h2>
          <div className="rows">
            <div className="row">
              <span>쓰고 있는 것</span>
              <b style={{ color: learned.source === "model" ? "var(--up)" : "var(--warn)" }}>
                {learned.sourceLabel}
              </b>
            </div>
            <div className="row">
              <span>{learned.horizon}봉 뒤 중앙</span>
              <b>
                {learned.expectedMovePct !== undefined
                  ? `${learned.expectedMovePct >= 0 ? "+" : ""}${learned.expectedMovePct.toFixed(2)}%`
                  : "—"}
              </b>
            </div>
            <div className="row">
              <span>상승 비중</span>
              <b>{pct(learned.probUp, 0)}</b>
            </div>
            <div className="row">
              <span>현재 변동성 (ATR)</span>
              <b>{learned.atrPct}%</b>
            </div>
            {learned.direction !== undefined && (
              <div className="row">
                <span>방향 분류</span>
                <b>
                  {learned.direction === 1 ? "상승" : learned.direction === -1 ? "하락" : "중립"}{" "}
                  {pct(learned.directionConfidence, 0)}
                  {learned.directionBeatsBaseline === false && (
                    <span style={{ color: "var(--down)" }}> · 기준선 이하</span>
                  )}
                </b>
              </div>
            )}
          </div>
          {learned.verdict && <p className="note warn">{learned.verdict}</p>}
        </section>
      )}

      {report && <ReportCard report={report} />}
    </>
  );
}

function ReportCard({ report }: { report: TrainReport }) {
  const h = String(report.horizon);
  const skill = report.skill[h];
  const volSkill = report.volSkill?.[h];
  const coverage = report.coverage[`${h}:80`];

  return (
    <section className="card">
      <h2>학습 성적표</h2>
      <div className="rows">
        <div className="row">
          <span>학습 표본</span>
          <b>
            {report.rows.toLocaleString("ko-KR")}행 · {report.folds}겹 검증
          </b>
        </div>
        <div className="row">
          <span>변동성 스케일링이 준 것</span>
          <b style={{ color: (volSkill ?? 0) > 0 ? "var(--up)" : "var(--text-dim)" }}>
            {signed(volSkill)}
          </b>
        </div>
        <div className="row">
          <span>학습이 그 위에 더한 것</span>
          <b style={{ color: skill > 0 ? "var(--up)" : "var(--down)" }}>{signed(skill)}</b>
        </div>
        <div className="row">
          <span>80% 밴드 실제 적중</span>
          <b>{pct(coverage, 1)}</b>
        </div>
        <div className="row">
          <span>방향 정확도</span>
          <b>
            {pct(report.directionAccuracy, 1)}
            <span style={{ color: "var(--text-dim)" }}>
              {" "}
              / 기준선 {pct(report.directionBaseline, 1)}
            </span>
          </b>
        </div>
      </div>

      <p className="formula" style={{ marginTop: 10 }}>
        점수는 <b>기준선 대비 개선율</b>이다. 0이면 기준선과 같고, 음수면 기준선보다 나쁘다.
        기준선이 둘인 이유는, 변동성으로 폭을 잡는 것만으로 이미 상당히 맞기 때문이다 —
        학습이 의미가 있으려면 <b>그 위에</b> 무언가를 더해야 한다.
      </p>

      {report.importance.length > 0 && (
        <>
          <div className="group-label">모델이 많이 본 입력</div>
          <div className="rows">
            {report.importance.slice(0, 8).map((item) => (
              <div className="row" key={item.feature}>
                <span>{item.feature}</span>
                <b>{item.score.toFixed(4)}</b>
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
