/**
 * 알림 기록 — 그때 뭐가 왔었나.
 *
 * **알림함과 따로 있는 이유.** 알림함은 지금 봐야 할 것을 보여주는 화면이라, 읽고
 * 보관하면 눈앞에서 사라진다. 그런데 나중에 세어 볼 것은 바로 그 사라진 것들이다.
 * 그래서 이 화면은 보관한 것도 기본으로 보여주고, 지우는 버튼을 두지 않는다.
 *
 * **여기서 맞았다·틀렸다를 매기지 않는다.** 뒷값은 변화율까지다. `buy_below` 는
 * 뒤가 오르면 반가운 알림이고 `target_above` 는 이미 목표에 닿아 나간 알림이라,
 * 한 잣대로 점수를 매기면 없는 성적이 생긴다. 부호를 어떻게 읽을지는 보는 쪽이 정한다.
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { alerts } from "../api";
import type { AlertFired, AlertLogView, AlertOutcomes } from "../types";
import { KIND } from "./AlertsPanel";

const KST = "Asia/Seoul";

const SPANS: Array<{ days: number; label: string }> = [
  { days: 7, label: "7일" },
  { days: 30, label: "30일" },
  { days: 90, label: "90일" },
  { days: 0, label: "전체" },
];

function day(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "언젠가";
  return at.toLocaleDateString("ko-KR", {
    timeZone: KST, year: "numeric", month: "2-digit", day: "2-digit", weekday: "short",
  });
}

/** 뒷값 옆에 붙는 짧은 날짜. 줄이 길어지면 종목 이름이 밀린다. */
function shortDay(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const parts = at.toLocaleDateString("en-CA", {
    timeZone: KST, month: "2-digit", day: "2-digit",
  });
  return parts.replace("-", ".");
}

function clock(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  return at.toLocaleTimeString("ko-KR", {
    timeZone: KST, hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

function num(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const digits = value >= 1000 ? 0 : value >= 1 ? 2 : 4;
  return value.toLocaleString("ko-KR", { maximumFractionDigits: digits });
}

function pct(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

/** 오르면 up, 내리면 down. **좋다·나쁘다가 아니라 방향이다.** */
function tone(value: number): string {
  if (value > 0.05) return "var(--up)";
  if (value < -0.05) return "var(--down)";
  return "var(--text-dim)";
}

function csv(entries: AlertFired[], outcomes: AlertOutcomes | null,
             horizons: number[]): string {
  const cell = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const head = [
    "시각(UTC)", "날짜(KST)", "시각(KST)", "프로바이더", "종목", "종류",
    "설정값", "닿은값", "제목", "내용", "읽음", "보관",
    ...horizons.map((h) => `+${h}일 변화율`), "최근 변화율",
  ];
  const rows = entries.map((item) => {
    const got = outcomes?.outcomes[item.id];
    return [
      item.at, day(item.at), clock(item.at), item.provider, item.symbol,
      KIND[item.kind ?? ""] ?? item.kind, item.setPrice, item.price,
      item.title, item.body, item.read ? "읽음" : "", item.archived ? "보관" : "",
      ...horizons.map((h) => {
        const point = got?.after[String(h)];
        return point ? point.changePct.toFixed(2) : "";
      }),
      got?.latest ? got.latest.changePct.toFixed(2) : "",
    ].map(cell).join(",");
  });
  // 엑셀이 UTF-8 을 알아보게 BOM 을 붙인다. 없으면 한글이 전부 깨져서 열린다.
  return `﻿${head.map(cell).join(",")}\n${rows.join("\n")}\n`;
}

interface Props {
  onPick: (symbol: string) => void;
}

export function AlertLog({ onPick }: Props) {
  const [view, setView] = useState<AlertLogView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState(30);
  const [symbol, setSymbol] = useState("");
  const [kind, setKind] = useState("");
  const [archived, setArchived] = useState(true);
  const [outcomes, setOutcomes] = useState<AlertOutcomes | null>(null);
  const [measuring, setMeasuring] = useState(false);

  const load = useCallback(() => {
    setError(null);
    alerts.log({ days, symbol: symbol || undefined, kind: kind || undefined, archived })
      .then(setView)
      .catch((e) => setError(String(e.message ?? e)));
  }, [days, symbol, kind, archived]);

  useEffect(() => {
    // 필터가 바뀌면 뒷값은 다른 목록의 것이 된다. 남겨 두면 엉뚱한 줄에 붙는다.
    setOutcomes(null);
    load();
  }, [load]);

  const entries = view?.entries ?? [];
  const summary = view?.summary;
  const horizons = outcomes?.horizons ?? [1, 3, 7];

  const grouped = useMemo(() => {
    const out: Array<{ key: string; items: AlertFired[] }> = [];
    for (const item of entries) {
      const key = day(item.at);
      const last = out[out.length - 1];
      if (last && last.key === key) last.items.push(item);
      else out.push({ key, items: [item] });
    }
    return out;
  }, [entries]);

  const measure = () => {
    if (entries.length === 0) return;
    setMeasuring(true);
    // 서버가 200건에서 끊는다. 그보다 긴 목록은 최근 것부터 잰다.
    alerts.outcome(entries.slice(0, 200).map((e) => e.id))
      .then(setOutcomes)
      .catch((e) => setError(String(e.message ?? e)))
      .finally(() => setMeasuring(false));
  };

  const save = () => {
    const blob = new Blob([csv(entries, outcomes, horizons)], {
      type: "text/csv;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `알림기록-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const failed = Object.entries(outcomes?.failed ?? {});

  return (
    <>
      <section className="card">
        <h2>
          기록
          {summary && (
            <span className="scope">
              {summary.total}건
              {summary.stored > summary.total && ` · 전체 ${summary.stored}건`}
            </span>
          )}
        </h2>

        <p className="formula" style={{ marginTop: 0 }}>
          나간 알림은 <b>지우지 않는다.</b> 알림함에서 보관해도 여기에는 그대로 남는다 —
          "그때 알림이 왔었나" 를 나중에 확인할 수 있어야 하기 때문이다.
        </p>

        {error && <p className="error" style={{ marginTop: 8 }}>{error}</p>}

        <div className="chips">
          {SPANS.map((span) => (
            <button key={span.days} className="chip" data-active={days === span.days}
                    onClick={() => setDays(span.days)}>
              {span.label}
            </button>
          ))}
          <button className="chip" data-active={archived}
                  onClick={() => setArchived((v) => !v)}>
            보관한 것 포함
          </button>
        </div>

        <div className="ask-form" style={{ marginTop: 8 }}>
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            <option value="">모든 종목</option>
            {(summary?.symbols ?? []).map((item) => (
              <option key={item.symbol} value={item.symbol}>
                {item.label} ({item.count})
              </option>
            ))}
          </select>
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">모든 종류</option>
            {Object.entries(KIND).map(([key, text]) => (
              <option key={key} value={key}>{text}</option>
            ))}
          </select>
        </div>

        <div className="chips">
          <button className="chip" onClick={measure}
                  disabled={measuring || entries.length === 0}>
            {measuring ? "재는 중" : "그 뒤 값 보기"}
          </button>
          <button className="chip" onClick={save} disabled={entries.length === 0}>
            CSV 로 내보내기
          </button>
        </div>

        {summary && summary.total > 0 && (
          <p className="formula" style={{ marginTop: 8 }}>
            {day(summary.first ?? "")} ~ {day(summary.last ?? "")}
            {" · "}
            {Object.entries(summary.kinds)
              .map(([key, n]) => `${KIND[key] ?? key} ${n}`)
              .join(" · ")}
            {summary.unread > 0 && ` · 안 읽음 ${summary.unread}`}
          </p>
        )}

        {outcomes && (
          <p className="note" style={{ marginTop: 8 }}>
            뒷값은 <b>알림 뒤 값이 어디로 갔는지</b>지 알림의 성적이 아니다. 내려와 닿은
            알림과 목표가 알림은 부호를 반대로 읽어야 해서, 한 잣대로 더하면 없는 성적이
            생긴다. 기준은 <b>닿은 값</b>이고 <b>확정된 일봉 종가</b>로 센다 — 주식은
            거래일이라 장 쉬는 날은 안 센다.
          </p>
        )}
        {failed.length > 0 && (
          <p className="note warn" style={{ marginTop: 8 }}>
            {failed.map(([key, reason]) => `${key}: ${reason}`).join(" / ")}
          </p>
        )}
      </section>

      {grouped.length === 0 && !error && (
        <section className="card">
          <p className="note" style={{ marginTop: 0 }}>
            {summary && summary.stored > 0
              ? "이 조건에 맞는 기록이 없다. 기간을 넓혀 보라."
              : "아직 나간 알림이 없다. 알림 탭에서 규칙을 걸면 그 값에 닿을 때 여기 쌓인다."}
          </p>
        </section>
      )}

      {grouped.map((group) => (
        <section className="card" key={group.key}>
          <h2>
            {group.key}
            <span className="scope">{group.items.length}건</span>
          </h2>
          <div className="rows" style={{ marginTop: 6 }}>
            {group.items.map((item) => (
              <LogRow key={item.id} item={item}
                      outcome={outcomes?.outcomes[item.id] ?? null}
                      horizons={horizons} onPick={onPick} />
            ))}
          </div>
        </section>
      ))}
    </>
  );
}

function LogRow({ item, outcome, horizons, onPick }: {
  item: AlertFired;
  outcome: AlertOutcomes["outcomes"][string] | null;
  horizons: number[];
  onPick: (symbol: string) => void;
}) {
  return (
    <div className="log-entry">
      <span className="log-when">{clock(item.at)}</span>
      <span style={{ flex: 1, minWidth: 0 }}>
        <button className="linky" disabled={!item.symbol}
                onClick={() => item.symbol && onPick(item.symbol)}>
          {item.title}
        </button>
        <span className="log-line">{item.body}</span>
        <span className="log-line">
          {item.kind && (KIND[item.kind] ?? item.kind)}
          {item.setPrice !== undefined && ` 설정 ${num(item.setPrice)}`}
          {item.price !== undefined && ` · 닿은 값 ${num(item.price)}`}
          {item.batch && " · 밤사이 모아 보낸 것"}
          {item.archived && " · 보관"}
          {!item.read && " · 안 읽음"}
        </span>
        {outcome && (
          <span className="log-line">
            {horizons.map((h) => {
              const point = outcome.after[String(h)];
              if (!point) return null;
              return (
                <b key={h} style={{ color: tone(point.changePct), marginRight: 8 }}>
                  +{h}일 {pct(point.changePct)}
                </b>
              );
            })}
            {outcome.latest && (
              /* 날짜를 같이 적는다. 그냥 "최근" 이면 지금 값으로 읽히는데, 실제로는
                 마지막 **확정된** 일봉이라 오늘 것이 아닐 때가 많다. */
              <b style={{ color: tone(outcome.latest.changePct) }}>
                최근({shortDay(outcome.latest.at)}) {pct(outcome.latest.changePct)}
              </b>
            )}
            {Object.keys(outcome.after).length === 0 && !outcome.latest && (
              <span>아직 뒤가 없다 — 방금 나간 알림이다</span>
            )}
          </span>
        )}
      </span>
    </div>
  );
}
