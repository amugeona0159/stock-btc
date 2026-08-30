/**
 * 알림 화면 — 받은 알림과 걸어 둔 규칙.
 *
 * 이 화면의 규칙 하나: **"사세요" 라고 쓰지 않는다.** 서버가 보내는 문구도 그렇고
 * 여기 라벨도 그렇다. 이 도구가 아는 건 "설정한 값에 닿았다" 까지고, 방향 적중은
 * 55% 다. 명령형으로 쓰면 그 55% 를 숨기는 화면이 된다.
 */
import { useCallback, useEffect, useState } from "react";

import { alerts } from "../api";
import { ruleBandWords } from "../say";
import { installed, isIos, register, subscribe } from "../push";
import type { AlertFired, AlertRule, AlertsView } from "../types";

/** 종류 이름표. **기록 화면도 이걸 쓴다** — 두 벌이 되면 같은 알림이 화면마다 달리 읽힌다. */
export const KIND: Record<string, string> = {
  buy_below: "내려와 닿으면",
  stop_below: "손절선 이탈",
  sell_above: "올라가 닿으면",
  target_above: "목표가 도달",
};

function won(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const digits = value >= 1000 ? 0 : value >= 1 ? 1 : 4;
  return value.toLocaleString("ko-KR", { maximumFractionDigits: digits });
}

function ago(iso: string): string {
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return "";
  const minutes = Math.floor((Date.now() - then) / 60000);
  if (minutes < 1) return "방금";
  if (minutes < 60) return `${minutes}분 전`;
  if (minutes < 60 * 24) return `${Math.floor(minutes / 60)}시간 전`;
  return `${Math.floor(minutes / 1440)}일 전`;
}

interface Props {
  provider: string;
  symbol: string;
  onPick: (symbol: string) => void;
}

export function AlertsPanel({ provider, symbol, onPick }: Props) {
  const [view, setView] = useState<AlertsView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pushNote, setPushNote] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);

  const load = useCallback(() => {
    alerts.list().then(setView).catch((e) => setError(String(e.message ?? e)));
  }, []);

  useEffect(() => {
    load();
    register();
    // 알림이 오면 화면도 따라 바뀌어야 한다. 30초면 충분하다.
    const timer = window.setInterval(load, 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const turnOnPush = async () => {
    if (!view) return;
    const got = await subscribe(view.push.publicKey);
    if (got.ok) {
      setPushNote("알림을 켰다. 아래 '시험 알림' 으로 확인해 보라.");
      return;
    }
    setPushNote(got.reason);
  };

  const fired = (view?.fired ?? []).filter((f) => showArchived || !f.archived);
  const unread = (view?.fired ?? []).filter((f) => !f.read && !f.archived).length;

  return (
    <>
      {/* --- 홈 화면 추가 안내: iOS 는 이걸 해야만 푸시가 온다 --- */}
      {isIos() && !installed() && (
        <section className="card">
          <h2>알림을 받으려면</h2>
          <p className="note warn" style={{ marginTop: 0 }}>
            <b>홈 화면에 추가해야 알림이 옵니다.</b> 사파리 아래 <b>공유</b> 버튼 →{" "}
            <b>홈 화면에 추가</b>. 아이폰은 이 방법으로 설치한 경우에만 웹 알림을
            허용합니다 — 사파리 탭으로 열어 두면 권한 요청조차 뜨지 않습니다.
          </p>
        </section>
      )}

      <section className="card">
        <h2>
          알림함
          {unread > 0 && <span className="scope">안 읽음 {unread}</span>}
        </h2>

        {error && <p className="error" style={{ marginTop: 0 }}>{error}</p>}
        {view?.quiet && (
          <p className="note" style={{ marginTop: 0 }}>
            지금은 조용한 시간이다(23~07시). 손절 말고는 모아 뒀다 아침에 한 번 보낸다.
          </p>
        )}

        <div className="chips">
          <button className="chip" onClick={turnOnPush}
                  disabled={!view?.push.available}>
            알림 켜기
          </button>
          <button className="chip" onClick={() => alerts.test().then((r) =>
            setPushNote(r.sent > 0
              ? `${r.sent}곳에 보냈다. 폰을 확인하라.`
              : `보낼 곳이 없다 (구독 ${r.subscriptions}개, 서버 푸시 ${r.push ? "켜짐" : "꺼짐"})`))}>
            시험 알림
          </button>
          <button className="chip" data-active={showArchived}
                  onClick={() => setShowArchived((v) => !v)}>
            보관함
          </button>
        </div>
        {pushNote && <p className="note" style={{ marginTop: 8 }}>{pushNote}</p>}
        {view && !view.push.available && (
          <p className="note warn" style={{ marginTop: 8 }}>
            서버에 푸시 키(VAPID)가 없어 알림이 안 나간다. 기록은 여기 그대로 쌓인다.
          </p>
        )}

        <div className="rows" style={{ marginTop: 10 }}>
          {fired.length === 0 && (
            <p className="note" style={{ marginTop: 0 }}>
              아직 온 알림이 없다. 아래에서 규칙을 걸면 그 값에 닿을 때 온다.
            </p>
          )}
          {fired.map((item, index) => (
            <FiredRow key={`${item.id}-${index}`} item={item} onChange={load} onPick={onPick} />
          ))}
        </div>
      </section>

      <RulesCard view={view} provider={provider} symbol={symbol}
                 onChange={load} onPick={onPick} />
    </>
  );
}

function FiredRow({ item, onChange, onPick }: {
  item: AlertFired; onChange: () => void; onPick: (s: string) => void;
}) {
  return (
    <div className="row" style={{ alignItems: "flex-start", opacity: item.read ? 0.6 : 1 }}>
      <span style={{ flex: 1 }}>
        <button className="linky" onClick={() => item.symbol && onPick(item.symbol)}>
          {item.title}
        </button>
        <span style={{ display: "block", color: "var(--text-dim)", fontSize: 11 }}>
          {item.body}
        </span>
        <span style={{ display: "block", color: "var(--text-dim)", fontSize: 11 }}>
          {ago(item.at)}
        </span>
      </span>
      <span style={{ display: "flex", gap: 4, flexDirection: "column" }}>
        {!item.read && (
          <button className="chip" onClick={() => alerts.read(item.id).then(onChange)}>
            읽음
          </button>
        )}
        {!item.archived && (
          <button className="chip" onClick={() => alerts.archive(item.id).then(onChange)}>
            보관
          </button>
        )}
      </span>
    </div>
  );
}

function RulesCard({ view, provider, symbol, onChange, onPick }: {
  view: AlertsView | null; provider: string; symbol: string;
  onChange: () => void; onPick: (s: string) => void;
}) {
  const [price, setPrice] = useState("");
  const [kind, setKind] = useState("buy_below");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const add = () => {
    const value = Number(price);
    if (!Number.isFinite(value) || value <= 0) {
      setNote("가격을 숫자로 넣어라");
      return;
    }
    setBusy(true);
    alerts.create({ provider, symbol, kind, price: value })
      .then(() => { setPrice(""); setNote(null); onChange(); })
      .catch((e) => setNote(String(e.message ?? e)))
      .finally(() => setBusy(false));
  };

  const fromToday = () => {
    setBusy(true);
    alerts.fromRecommendation(provider, 1)
      .then((r) => { setNote(`오늘 추천에서 ${r.made.length}개 만들었다`); onChange(); })
      .catch((e) => setNote(String(e.message ?? e)))
      .finally(() => setBusy(false));
  };

  const rules = view?.rules ?? [];
  return (
    <section className="card">
      <h2>규칙</h2>
      <p className="formula" style={{ marginTop: 0 }}>
        걸어 둔 값에 닿으면 알린다. <b>한 규칙은 한 번만 울린다</b> — 경계에서 가격이
        떨 때 알림이 쏟아지지 않게. 다시 받으려면 <b>되살리기</b>를 누른다.
      </p>

      <div className="ask-form" style={{ marginTop: 8 }}>
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          {Object.entries(KIND).map(([key, label]) => (
            <option key={key} value={key}>{label}</option>
          ))}
        </select>
        <input inputMode="decimal" placeholder={`${symbol} 가격`}
               value={price} onChange={(e) => setPrice(e.target.value)} />
        <button onClick={add} disabled={busy}>추가</button>
      </div>
      <div className="chips">
        <button className="chip" onClick={fromToday} disabled={busy}>
          오늘 추천에서 만들기
        </button>
      </div>
      {note && <p className="note" style={{ marginTop: 8 }}>{note}</p>}

      <div className="rows" style={{ marginTop: 10 }}>
        {rules.length === 0 && (
          <p className="note" style={{ marginTop: 0 }}>걸어 둔 규칙이 없다.</p>
        )}
        {rules.map((rule) => (
          <RuleRow key={rule.id} rule={rule} onChange={onChange} onPick={onPick} />
        ))}
      </div>
    </section>
  );
}

function RuleRow({ rule, onChange, onPick }: {
  rule: AlertRule; onChange: () => void; onPick: (s: string) => void;
}) {
  const done = Boolean(rule.fired_at);
  return (
    <div className="row" style={{ alignItems: "flex-start",
                                  opacity: rule.active && !done ? 1 : 0.55 }}>
      <span style={{ flex: 1 }}>
        <button className="linky" onClick={() => onPick(rule.symbol)}>
          {rule.label}
        </button>
        <span style={{ display: "block", color: "var(--text-dim)", fontSize: 11 }}>
          {KIND[rule.kind] ?? rule.kind} {won(rule.price)}
          {rule.source === "recommend" && " · 추천에서"}
          {done && " · 울렸음"}
          {!rule.active && " · 꺼짐"}
        </span>
        {/* **이 값이 어디서 나왔는지**를 말한다. `3일 안에 −5.5% ~ +5.6% 안에 있을
            확률 80%` 만 적혀 있으면 그게 이 알림값과 무슨 상관인지 알 수가 없다. */}
        {rule.band && rule.days && (
          <span style={{ display: "block", color: "var(--text-dim)", fontSize: 11 }}>
            {ruleBandWords(rule.days, rule.band)}
          </span>
        )}
      </span>
      <span style={{ display: "flex", gap: 4, flexDirection: "column" }}>
        {done && (
          <button className="chip"
                  onClick={() => alerts.patch(rule.id, { rearm: true }).then(onChange)}>
            되살리기
          </button>
        )}
        {!done && (
          <button className="chip"
                  onClick={() => alerts.patch(rule.id, { active: !rule.active })
                    .then(onChange)}>
            {rule.active ? "끄기" : "켜기"}
          </button>
        )}
        <button className="chip"
                onClick={() => alerts.remove(rule.id).then(onChange)}>
          삭제
        </button>
      </span>
    </div>
  );
}
