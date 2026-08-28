import { useCallback, useEffect, useState } from "react";

import { screen } from "../api";
import type { ScreenResult } from "../types";

// 사용자가 물은 그대로 — 하루·이틀·사흘. 봉 단위가 일봉일 때의 이야기다.
const DAYS = [
  { label: "하루 뒤", value: 1 },
  { label: "이틀 뒤", value: 2 },
  { label: "사흘 뒤", value: 3 },
];

function signed(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined || !Number.isFinite(value)
    ? "—"
    : `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

interface Props {
  provider: string;
  timeframe: string;
  onPick: (symbol: string) => void;
}

export function ScreenPanel({ provider, timeframe, onPick }: Props) {
  const [horizon, setHorizon] = useState(1);
  const [result, setResult] = useState<ScreenResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(() => {
    setBusy(true);
    setError(null);
    screen
      .rank({ provider, timeframe, horizon, limit: 10 })
      .then(setResult)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false));
  }, [provider, timeframe, horizon]);

  // 종목·봉·지평이 바뀌면 다시 뽑는다. 이 화면은 목록이 전부라 빈 채로 두면 쓸모가 없다.
  useEffect(() => {
    setResult(null);
    run();
  }, [run]);

  const quality = result?.quality;

  return (
    <>
      <section className="card">
        <h2>관심있게 볼 종목</h2>
        <p className="formula" style={{ marginTop: 0 }}>
          같은 시각에 후보 종목을 팩터로 줄 세운 순위와, 그 뒤 실제로 간 결과의 순위가
          얼마나 맞았는지를 먼저 재고(<b>랭크 IC</b>), <b>폴드마다 부호가 일관된 축만</b>
          점수에 넣는다. 축의 부호도 미리 정하지 않고 잰 값을 따른다.
        </p>
        <div className="chips">
          {DAYS.map((item) => (
            <button
              key={item.value}
              className="chip"
              data-active={horizon === item.value}
              disabled={busy}
              onClick={() => setHorizon(item.value)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <button style={{ marginTop: 10, width: "100%" }} disabled={busy} onClick={run}>
          {busy ? "뽑는 중" : "다시 뽑기"}
        </button>
        {error && <p className="error" style={{ marginTop: 10 }}>{error}</p>}
        {result && !result.available && (
          <p className="note warn">{result.reason}</p>
        )}
      </section>

      {result?.available && (
        <section className="card">
          <h2>순위 · {horizon}일</h2>
          {/* 이 문장이 이 화면에서 제일 중요하다. 순위를 '오를 순서'로 읽으면 해롭다. */}
          <p className="note warn" style={{ marginTop: 0 }}>{result.note}</p>
          <div className="rows">
            {result.items.map((item, i) => (
              <div className="row" key={item.symbol}>
                <span>
                  <button
                    className="linky"
                    onClick={() => onPick(item.symbol)}
                    title="이 종목으로 차트를 옮긴다"
                  >
                    {i + 1}. {item.symbol}
                  </button>
                  {item.changePct !== null && item.changePct !== undefined && (
                    <span
                      style={{
                        color: item.changePct >= 0 ? "var(--up)" : "var(--down)",
                      }}
                    >
                      {" "}
                      {signed(item.changePct)}%
                    </span>
                  )}
                </span>
                <b>
                  변동 {signed(item.move)}
                  <span style={{ color: "var(--text-dim)" }}>
                    {" "}
                    · 방향 {signed(item.direction)}
                  </span>
                </b>
              </div>
            ))}
          </div>
          {result.items[0]?.why?.length > 0 && (
            <p className="formula" style={{ marginTop: 10 }}>
              1위 <b>{result.items[0].symbol}</b> 가 위에 있는 이유:{" "}
              {result.items[0].why
                .map((w) => `${w.label} ${signed(w.z, 1)}σ`)
                .join(" · ")}
            </p>
          )}
        </section>
      )}

      {quality && (
        <section className="card">
          <h2>이 순위가 과거에 맞았나</h2>
          <div className="rows">
            {(["move", "direction"] as const).map((kind) => {
              const q = quality[kind];
              const gap = q.topMinusBottomPct;
              return (
                <div className="row" key={kind}>
                  <span>{kind === "move" ? "변동 상위−하위" : "방향 상위−하위"}</span>
                  <b
                    style={{
                      color:
                        gap === null || gap === undefined
                          ? "var(--text-dim)"
                          : gap > 0
                            ? "var(--up)"
                            : "var(--down)",
                    }}
                  >
                    {gap === null || gap === undefined ? "—" : `${signed(gap)}%p`}
                    <span style={{ color: "var(--text-dim)" }}> · {q.factors}축</span>
                  </b>
                </div>
              );
            })}
            <div className="row">
              <span>후보 종목</span>
              <b>{result?.breadth ?? 0}개</b>
            </div>
            <div className="row">
              <span>마지막 측정</span>
              <b>{result?.measuredAt?.slice(0, 10) ?? "—"}</b>
            </div>
          </div>
          <p className="formula" style={{ marginTop: 10 }}>
            <b>변동</b>은 "얼마나 움직일까", <b>방향</b>은 "어느 쪽일까"다.{" "}
            <b>상위−하위</b>는 점수 상위 묶음의 평균 결과에서 하위 묶음의 평균을 뺀 값이고,
            수수료·슬리피지는 빼지 않았다. 변동 쪽은 크고 일관되지만 방향 쪽은 작다 —
            숫자를 그대로 읽을 것.
            <br />
            점수는 <b>자기 과거 대비</b> 축으로만 만든다. 원값(변동성·베타)은 IC 가 몇 배
            크지만 "이 종목은 원래 많이 움직인다"는 고정 순위라 매일 같은 답을 낸다.
          </p>
          {quality.move.used.length > 0 && (
            <>
              <div className="group-label">변동 점수에 쓰인 축</div>
              <div className="rows">
                {quality.move.used.slice(0, 6).map((f) => (
                  <div className="row" key={f.factor}>
                    <span>{f.label}</span>
                    <b>IC {signed(f.ic, 4)}</b>
                  </div>
                ))}
              </div>
            </>
          )}
        </section>
      )}
    </>
  );
}
