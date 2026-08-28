import { useEffect, useState } from "react";

import { learn } from "../api";
import type { GateStatus, Learned, LearningState, TrainReport } from "../types";

const HORIZONS = [
  { label: "5봉", value: 5 },
  { label: "10봉", value: 10 },
  { label: "20봉", value: 20 },
  { label: "60봉", value: 60 },
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
  note: string | null;
  skipped: Array<{ symbol: string; reason: string }>;
  onTrain: (horizon: number) => void;
}

export function LearnPanel({ learned, busy, error, note, skipped, onTrain }: Props) {
  const [horizon, setHorizon] = useState(10);
  const report = learned?.report;

  // 지평에서의 80% 밴드 반폭을 현재가 대비 %로. "이만큼 움직인다"를 한 숫자로.
  const low = learned?.bands?.p10?.at(-1)?.value;
  const high = learned?.bands?.p90?.at(-1)?.value;
  const band =
    low !== undefined && high !== undefined && learned?.last
      ? ((high - low) / 2 / learned.last) * 100
      : null;
  // 이 모델의 밴드가 검증에서 실제로 몇 %를 담았나. 목표는 80%.
  const coverage = report?.coverage?.[`${report?.horizon}:80`];

  return (
    <>
      <section className="card">
        <h2>학습</h2>
        <p className="formula" style={{ marginTop: 0 }}>
          상황 28축 + 유사구간 요약 + 사건 이력 + 관심도(위키백과 조회수)를 입력으로 넣고,
          실제로 무슨 일이 났는지를 학습한다. 사례를 답으로 쓰는 대신 <b>사례를 얼마나
          믿을지</b>까지 배우게 하는 것이다. 여러 종목을 모아 학습하고, 결과는 변동성
          기준선과 섞어 쓴다.
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
          {busy ? "학습 중 (1~2분 걸린다)" : "여러 종목을 모아 학습"}
        </button>
        {note && <p className="note warn">{note}</p>}
        {skipped.length > 0 && (
          <p className="note">
            빠진 종목: {skipped.map((item) => item.symbol).join(", ")}
          </p>
        )}
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
            {/* **폭이 먼저다.** 채점해 보면 밴드는 82% 로 맞고 방향은 55% 다.
                방향을 위에 두면 이 화면이 실제보다 잘하는 것처럼 읽힌다. */}
            {band !== null && (
              <div className="row">
                <span>{learned.horizon}봉 뒤 변동 폭 (80%)</span>
                <b style={{ fontSize: 15 }}>±{band.toFixed(2)}%</b>
              </div>
            )}
            {coverage !== undefined && (
              <div className="row">
                <span>이 모델의 밴드 실제 적중</span>
                <b style={{ color: coverage >= 0.75 ? "var(--up)" : "var(--warn)" }}>
                  {pct(coverage, 1)}
                  <span style={{ color: "var(--text-dim)" }}> · 목표 80%</span>
                </b>
              </div>
            )}
            <div className="row">
              <span>현재 변동성 (ATR)</span>
              <b>{learned.atrPct}%</b>
            </div>
            <div className="row">
              <span>쓰고 있는 것</span>
              <b style={{ color: learned.source === "blend" ? "var(--up)" : "var(--warn)" }}>
                {learned.sourceLabel}
              </b>
            </div>
          </div>

          <div className="group-label">방향 (참고 · 55%)</div>
          <div className="rows" style={{ color: "var(--text-dim)" }}>
            <div className="row">
              <span>{learned.horizon}봉 뒤 중앙</span>
              <b>
                {learned.expectedMovePct !== undefined && learned.expectedMovePct !== null
                  ? `${learned.expectedMovePct >= 0 ? "+" : ""}${learned.expectedMovePct.toFixed(2)}%`
                  : "—"}
              </b>
            </div>
            <div className="row">
              <span>상승 비중</span>
              <b>{pct(learned.probUp, 0)}</b>
            </div>
            {learned.direction !== undefined && !learned.abstain && (
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
          {learned.abstain && (
            <p className="note warn">
              <b>방향은 말하지 않는다.</b> {learned.abstainReason}
            </p>
          )}
          {learned.verdict && <p className="note warn">{learned.verdict}</p>}
        </section>
      )}

      <GateCard />

      <AutoLearnCard />

      {report && <ReportCard report={report} />}
    </>
  );
}

function AutoLearnCard() {
  const [state, setState] = useState<LearningState | null>(null);

  useEffect(() => {
    // 기록 파일 하나를 읽는 것뿐이라 실패해도 화면이 막힐 이유가 없다.
    learn.state().then(setState).catch(() => setState(null));
  }, []);

  if (!state?.available) return null;
  const top = state.champions.slice(0, 6);
  // 옆 띠는 좁다. 프로바이더 이름은 여섯 줄 내내 같으니 떼고 종목·봉만 남긴다.
  const short = (target: string) => target.split(":").slice(1).join(" ");

  return (
    <section className="card">
      <h2>매일 도는 자동 학습</h2>
      <p className="formula" style={{ marginTop: 0 }}>
        매일 한 번, 챔피언을 새 데이터로 다시 굽고 설정을 <b>하나만</b> 흔든 도전자와
        겨룬다. 도전자가 {signed(state.promoteMargin ?? 0.002)} 이상 이길 때만 갈아끼운다 —
        마진이 없으면 잡음으로 매일 모델이 바뀐다.
      </p>
      <div className="rows">
        <div className="row">
          <span>마지막 실행</span>
          <b>{state.updated?.slice(0, 16).replace("T", " ") ?? "—"}</b>
        </div>
        <div className="row">
          <span>추적 대상</span>
          <b>
            {state.tracked}개{" "}
            <span style={{ color: "var(--text-dim)" }}>· 기준선 넘음 {state.learned}개</span>
          </b>
        </div>
        <div className="row">
          <span>누적 시험 / 승격</span>
          <b>
            {state.trials}번 <span style={{ color: "var(--text-dim)" }}>/ {state.promotions}번</span>
          </b>
        </div>
      </div>

      <div className="group-label">지금의 챔피언</div>
      <div className="rows">
        {top.map((item) => (
          <div className="row" key={item.target}>
            <span>
              {short(item.target)}
              <span style={{ color: "var(--text-dim)" }}> 지평 {item.config.horizon}</span>
            </span>
            <b style={{ color: item.learned ? "var(--up)" : "var(--text-dim)" }}>
              {signed(item.skill ?? undefined, 4)}
              <span style={{ color: "var(--text-dim)" }}> · {item.trials}회</span>
            </b>
          </div>
        ))}
      </div>

      {state.recent.length > 0 && (
        <>
          <div className="group-label">최근 겨룬 기록</div>
          <div className="rows">
            {state.recent.slice(0, 6).map((trial, i) => (
              <div className="row" key={`${trial.at}-${i}`}>
                <span>
                  {trial.at.slice(5, 10)} {short(trial.target)}
                </span>
                <b style={{ color: trial.promoted ? "var(--up)" : "var(--text-dim)" }}>
                  {trial.promoted ? "승격" : "유지"}
                  <span style={{ color: "var(--text-dim)" }}> · {trial.change}</span>
                </b>
              </div>
            ))}
          </div>
        </>
      )}

      <p className="formula" style={{ marginTop: 10 }}>{state.note}</p>
    </section>
  );
}

function ReportCard({ report }: { report: TrainReport }) {
  const h = String(report.horizon);
  const skill = report.skill[h];
  const blendSkill = report.blendSkill?.[h];
  const volSkill = report.volSkill?.[h];
  const weight = report.weights?.[h];
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
          <span>모은 종목</span>
          <b>{report.symbols?.join(", ") ?? "—"}</b>
        </div>
        <div className="row">
          <span>변동성 스케일링이 준 것</span>
          <b style={{ color: (volSkill ?? 0) > 0 ? "var(--up)" : "var(--text-dim)" }}>
            {signed(volSkill)}
          </b>
        </div>
        <div className="row">
          <span>모델 단독</span>
          <b style={{ color: skill > 0 ? "var(--up)" : "var(--down)" }}>{signed(skill)}</b>
        </div>
        <div className="row">
          <span>기준선과 섞은 결과</span>
          <b style={{ color: (blendSkill ?? 0) > 0 ? "var(--up)" : "var(--down)" }}>
            {signed(blendSkill)}
            {weight !== undefined && (
              <span style={{ color: "var(--text-dim)" }}> (모델 {Math.round(weight * 100)}%)</span>
            )}
          </b>
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
        점수는 <b>기준선 대비 개선율</b>이다. 0이면 기준선과 같고, 음수면 더 나쁘다.
        모델 단독이 지더라도 기준선과 섞으면 이기는 일이 흔하다 — 섞는 비중은 앞 구간에서
        고른 뒤 다음 구간에 쓰므로, 이 점수는 실제로 배포 가능한 절차의 성적이다.
        <br />
        <b>수치는 작다.</b> +0.005 는 "변동성으로 폭을 잡는 것보다 0.5% 낫다" 는 뜻이지
        방향을 맞힌다는 뜻이 아니다. 짧은 지평의 방향 예측은 문헌에서도 잡음에 가깝다.
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

function GateCard() {
  const [state, setState] = useState<GateStatus | null>(null);

  useEffect(() => {
    learn.gate().then(setState).catch(() => setState(null));
  }, []);

  if (!state?.available || !state.holdout) return null;
  const { withoutRule, withRule, n, coverage } = state.holdout;

  return (
    <section className="card">
      <h2>못 맞히는 자리에서는 말을 안 한다</h2>
      <p className="formula" style={{ marginTop: 0 }}>
        과거 판을 쌓아 <b>맞은 판과 틀린 판을 조건별로 갈라</b> 보고, 거기서 나온 규칙을
        <b> 한 번도 안 본 구간</b>에서 확인한 것만 쓴다. 짧은 지평의 방향은 문헌에서도
        잡음에 가까워, 더 잘 맞히는 것보다 못 맞히는 자리에서 안 맞히는 쪽이 확실하다.
      </p>
      <div className="rows">
        <div className="row">
          <span>지금 쓰는 규칙</span>
          <b>{state.label ?? "—"}</b>
        </div>
        <div className="row">
          <span>규칙 없이 (안 본 구간)</span>
          <b>{pct(withoutRule, 1)}</b>
        </div>
        <div className="row">
          <span>규칙 적용</span>
          <b style={{ color: withRule > withoutRule ? "var(--up)" : "var(--down)" }}>
            {pct(withRule, 1)}
            <span style={{ color: "var(--text-dim)" }}>
              {" "}
              · {n}판 · {pct(coverage, 0)} 만 말함
            </span>
          </b>
        </div>
        <div className="row">
          <span>세운 가설 / 최종구간 열어 본 횟수</span>
          <b>
            {state.trials ?? "—"}개 / {state.holdoutLooks ?? "—"}번
          </b>
        </div>
      </div>
      <p className="formula" style={{ marginTop: 10 }}>
        가설 수와 최종 구간을 열어 본 횟수를 같이 적는다. 수백 개를 세우면 그중 하나는
        반드시 이기고, 최종 구간도 볼 때마다 조금씩 닳는다 — 그걸 숨기면 이 표가
        실제보다 좋아 보인다.
      </p>
    </section>
  );
}
