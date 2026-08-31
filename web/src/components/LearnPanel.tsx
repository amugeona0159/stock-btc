import { useEffect, useState } from "react";

import { learn } from "../api";
import type { GateStatus, Learned, LearningState, TrainReport } from "../types";
import { bandWords, directionWords } from "../say";
import { Numbers } from "./Numbers";
import { CardTabs, useCardTab } from "./Tabs";

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

  // **여기서 한 번에 받아 둔다.** 예전에는 카드가 각자 받아 갔는데, 그러면 그 카드가
  // 빈지를 밖에서 알 수 없다 — 눌러도 아무것도 안 나오는 탭이 생긴다는 뜻이다.
  // 둘 다 기록 파일 하나를 읽는 것뿐이라 실패해도 화면이 막힐 이유가 없다.
  const [auto, setAuto] = useState<LearningState | null>(null);
  const [gate, setGate] = useState<GateStatus | null>(null);
  useEffect(() => {
    learn.state().then(setAuto).catch(() => setAuto(null));
    learn.gate().then(setGate).catch(() => setGate(null));
  }, []);

  // 지평에서의 80% 밴드 반폭을 현재가 대비 %로. "이만큼 움직인다"를 한 숫자로.
  const low = learned?.bands?.p10?.at(-1)?.value;
  const high = learned?.bands?.p90?.at(-1)?.value;
  const band =
    low !== undefined && high !== undefined && learned?.last
      ? ((high - low) / 2 / learned.last) * 100
      : null;
  // 이 모델의 밴드가 검증에서 실제로 몇 %를 담았나. 목표는 80%.
  const coverage = report?.coverage?.[`${report?.horizon}:80`];

  // 카드 넷이 세로로 쌓여 있었다 — 320px 짜리 띠에서 성적표는 스크롤 네 번 아래다.
  // **첫 칸은 「학습 예측」이다**: 이 화면에서 지금 답을 하는 것은 그것뿐이고
  // 나머지 셋은 "그 답을 얼마나 믿을 만한가" 쪽이다.
  const cards = [
    ...(learned ? [{ key: "forecast", label: "학습 예측" }] : []),
    ...(auto?.available ? [{ key: "auto", label: "자동 학습" }] : []),
    ...(report ? [{ key: "report", label: "성적표" }] : []),
    ...(gate?.available && gate.holdout ? [{ key: "gate", label: "기권 규칙" }] : []),
  ];
  const [showing, setShowing] = useCardTab(cards.map((c) => c.key));

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

        {cards.length > 1 && <div className="group-label">무엇을 볼지</div>}
        <CardTabs label="학습 화면에서 무엇을 볼지" items={cards}
                  active={showing} onPick={setShowing} />
      </section>

      {showing === "forecast" && learned && !learned.available && (
        <section className="card">
          <h2>학습 예측</h2>
          <p className="formula" style={{ marginBottom: 0 }}>{learned.reason}</p>
        </section>
      )}

      {showing === "forecast" && learned?.available && (
        <section className="card">
          <h2>학습 예측</h2>
          {/* **폭이 먼저다.** 채점해 보면 밴드는 잘 맞고 방향은 반반에 가깝다.
              방향을 위에 두면 이 화면이 실제보다 잘하는 것처럼 읽힌다. */}
          {band !== null && (
            <p className="plain" style={{ marginTop: 0 }}>
              배운 대로라면 {learned.horizon}봉 뒤에 <b>지금 값에서 ±{band.toFixed(1)}%</b>
              {" "}안에 있을 가능성이 큽니다.
              {coverage !== undefined && bandWords(coverage) && (
                <>
                  {" "}이 모델은 지금까지 {bandWords(coverage)}
                  {coverage >= 0.75
                    ? " — 목표만큼 맞고 있습니다."
                    : " — 목표보다 덜 맞고 있어 그만큼 덜 믿을 값입니다."}
                </>
              )}
            </p>
          )}
          {/* 방향은 이 도구가 약한 쪽이다. **말로 한 줄만 내고 숫자는 접는다** —
              표로 늘어놓으면 폭과 같은 무게로 읽힌다. */}
          <p className="plain">
            {directionWords(learned.probUp)}.
            {learned.abstain
              ? " 그래서 방향은 말하지 않습니다."
              : learned.directionBeatsBaseline === false
                ? " 다만 이 모델의 방향은 기준선을 못 넘어 참고만 할 값입니다."
                : ""}
          </p>
          <Numbers>
            <div className="rows">
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
          </Numbers>
          {learned.abstain && (
            <p className="note warn">
              <b>방향은 말하지 않는다.</b> {learned.abstainReason}
            </p>
          )}
          {learned.verdict && <p className="note warn">{learned.verdict}</p>}
        </section>
      )}

      {showing === "gate" && <GateCard state={gate} />}

      {showing === "auto" && <AutoLearnCard state={auto} />}

      {showing === "report" && report && <ReportCard report={report} />}
    </>
  );
}

function AutoLearnCard({ state }: { state: LearningState | null }) {
  if (!state?.available) return null;
  const top = state.champions.slice(0, 6);
  // 옆 띠는 좁다. 프로바이더 이름은 여섯 줄 내내 같으니 떼고 종목·봉만 남긴다.
  const short = (target: string) => target.split(":").slice(1).join(" ");

  return (
    <section className="card">
      <h2>매일 도는 자동 학습</h2>
      {/* **말이 먼저.** 이 카드는 원래 skill 숫자 표였는데, 그 값이 무슨 뜻인지
          아는 사람에게만 읽히는 화면이었다. 무슨 일이 있었는지를 문장으로 먼저 낸다. */}
      <p className="plain" style={{ marginTop: 0 }}>
        매일 한 번, 지금 쓰는 모델을 새 자료로 다시 굽고 설정을 <b>하나만</b> 바꾼
        모델과 겨룹니다. 뚜렷하게 이겼을 때만 갈아끼웁니다 — 아니면 잡음만으로 매일
        모델이 바뀝니다.
      </p>
      <p className="plain">
        지켜보는 {state.tracked}자리 가운데 <b>{state.learned}자리</b>에서 배운 것이
        쓸모가 있었습니다.
        {/* 남는 자리가 없을 때 "나머지는" 이라고 쓰면 없는 것을 있다고 말하게 된다. */}
        {state.tracked > state.learned
          ? <> 나머지는 <b>배운 걸 쓰지 않고</b> 기본값으로 답합니다 — 못 이긴 모델을
              쓰면 더 나쁜 답이 나오기 때문입니다.</>
          : <> 못 이긴 자리가 생기면 그 자리는 배운 걸 쓰지 않고 기본값으로 답합니다.</>}
        {state.updated && <> 마지막으로 돈 것은 {state.updated.slice(0, 10)} 입니다.</>}
      </p>
      <Numbers>
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
              {state.trials}번{" "}
              <span style={{ color: "var(--text-dim)" }}>/ {state.promotions}번</span>
            </b>
          </div>
          <div className="row">
            <span>승격 마진</span>
            <b>{signed(state.promoteMargin ?? 0.002)}</b>
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
      </Numbers>

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

function GateCard({ state }: { state: GateStatus | null }) {
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
