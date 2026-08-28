import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api";
import type { ProviderInfo, SearchResult, SymbolItem, SymbolList } from "../types";

// 한 번에 그리는 최대 줄 수. 국내주식은 4,300종목이라 전부 그리면 브라우저가 멎는다.
// 가상 스크롤 라이브러리를 넣는 대신 잘라 놓고 **몇 개를 잘랐는지 적는다**.
const VISIBLE = 200;

interface Props {
  providers: ProviderInfo[];
  provider: string;
  onPick: (provider: string, symbol: string) => void;
  onClose: () => void;
}

export function SymbolPicker({ providers, provider, onPick, onClose }: Props) {
  const [market, setMarket] = useState(provider);
  const [query, setQuery] = useState("");
  const [lists, setLists] = useState<Record<string, SymbolList>>({});
  const [found, setFound] = useState<SearchResult | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  // ESC 로 닫는다. 오버레이가 이 저장소에 하나뿐이라 전역 리스너로 충분하다.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    input.current?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const loadList = useCallback((key: string) => {
    setLists((have) => (have[key] ? have : have));
    api
      .symbols(key)
      .then((list) => setLists((have) => ({ ...have, [key]: list })))
      .catch(() =>
        setLists((have) => ({
          ...have,
          [key]: { provider: key, listed: false, count: 0, items: [],
                   reason: "목록을 못 받았다" },
        })),
      );
  }, []);

  // 고른 시장을 먼저 받고, 나머지 시장은 뒤이어 받는다. 왼쪽에 종목 수가 "…" 로
  // 남아 있으면 고장처럼 보이고, 시장을 옮길 때마다 기다리게 된다.
  // 서버가 12시간 캐시하므로 두 번째부터는 즉시 온다.
  useEffect(() => {
    if (!lists[market]) loadList(market);
  }, [market, lists, loadList]);

  useEffect(() => {
    const rest = providers.filter((p) => p.listsSymbols && p.available && !lists[p.key]);
    if (!rest.length) return;
    const timer = window.setTimeout(() => rest.forEach((p) => loadList(p.key)), 400);
    return () => window.clearTimeout(timer);
    // `lists` 를 넣으면 한 개 받을 때마다 다시 돈다. 받은 개수만 본다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providers, Object.keys(lists).length, loadList]);

  // 검색은 서버로 나간다(전 시장 통합). 목록 안 필터는 로컬에서 한다.
  useEffect(() => {
    const needle = query.trim();
    if (!needle) {
      setFound(null);
      return;
    }
    setBusy(true);
    const timer = window.setTimeout(() => {
      api
        .search(needle)
        .then(setFound)
        .catch(() => setFound(null))
        .finally(() => setBusy(false));
    }, 200);
    return () => window.clearTimeout(timer);
  }, [query]);

  const list = lists[market];
  const needle = query.trim();

  // 검색어가 있으면 **고른 시장은 로컬에서** 거른다. 목록이 이미 브라우저에 있으므로
  // 서버의 30건 상한을 안 타고, "삼성" 40여 종목이 다 나온다.
  const rows = useMemo(() => {
    const items = list?.items ?? [];
    if (!needle) return items.slice(0, VISIBLE);
    const upper = needle.toUpperCase();
    return items
      .filter((x) => x.symbol.toUpperCase().includes(upper) || x.name.includes(needle)
                     || x.name.toUpperCase().includes(upper))
      .slice(0, VISIBLE);
  }, [list, needle]);
  const total = needle ? rows.length : list?.count ?? 0;
  const hidden = total - rows.length;

  const pick = (key: string, item: SymbolItem) => onPick(key, item.symbol);

  return (
    <div
      className="overlay"
      role="dialog"
      aria-modal="true"
      // mousedown 으로 본다. click 이면 목록 안에서 드래그를 시작해 밖에서 놓을 때
      // 닫혀 버린다.
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="card picker">
        <div className="picker-search">
          <input
            ref={input}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="종목 이름이나 코드 — 삼성전자 · 005930 · BTC"
          />
          <button onClick={onClose} title="닫기 (ESC)">
            닫기
          </button>
        </div>

        <div className="picker-markets">
          <div className="group-label">시장</div>
          {providers.map((p) => {
            const known = lists[p.key];
            const count = !p.available
              ? "키 필요"
              : !p.listsSymbols
                ? "검색만"
                : known
                  ? known.count.toLocaleString("ko-KR")
                  : "…";
            return (
              <div
                key={p.key}
                className="picker-row"
                data-active={!query && p.key === market}
                onClick={() => {
                  setQuery("");
                  setMarket(p.key);
                }}
                style={{ cursor: "pointer", justifyContent: "space-between" }}
              >
                <span style={{ color: p.available ? "var(--text)" : "var(--text-dim)" }}>
                  {p.name}
                </span>
                <span style={{ color: "var(--text-dim)", fontSize: 11 }}>{count}</span>
              </div>
            );
          })}
        </div>

        <div className="picker-list">
          {needle ? (
            <>
              {rows.length > 0 && (
                <div>
                  <div className="group-label">
                    {providers.find((p) => p.key === market)?.name ?? market} ·{" "}
                    {rows.length}건
                  </div>
                  {rows.map((item) => (
                    <Row key={item.symbol} item={item}
                         onPick={() => pick(market, item)} />
                  ))}
                </div>
              )}
              <SearchResults found={found} busy={busy} query={needle}
                             skip={market} hasLocal={rows.length > 0} onPick={pick} />
            </>
          ) : (
            <>
              {list && !list.listed && (
                <p className="note warn" style={{ marginTop: 0 }}>
                  {list.reason} 위 칸에 이름을 넣으면 찾을 수 있다.
                </p>
              )}
              {rows.map((item) => (
                <Row key={item.symbol} item={item} onPick={() => pick(market, item)} />
              ))}
              {hidden > 0 && (
                <p className="note">
                  {list?.count.toLocaleString("ko-KR")}개 중 {VISIBLE}개만 보여준다 —
                  위에 검색어를 넣어 좁힐 것.
                </p>
              )}
              {!list && <p className="note">목록을 받는 중…</p>}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Row({ item, onPick }: { item: SymbolItem; onPick: () => void }) {
  return (
    <div className="picker-row" onClick={onPick} style={{ cursor: "pointer" }}>
      <b style={{ minWidth: 92, fontWeight: 500 }}>{item.symbol}</b>
      <span style={{ flex: 1, color: "var(--text)" }}>{item.name}</span>
      {/* 보통주가 아니면 종류를 적는다. ETF·리츠가 종목인 척하면 안 된다. */}
      {item.kind && item.kind !== "STOCK" && item.kind !== "spot" && (
        <span className="chip">{item.kind}</span>
      )}
      {item.market && <span className="chip">{item.market}</span>}
    </div>
  );
}

function SearchResults({
  found,
  busy,
  query,
  skip,
  hasLocal,
  onPick,
}: {
  found: SearchResult | null;
  busy: boolean;
  query: string;
  /** 고른 시장은 위에서 로컬로 이미 그렸다. 두 번 그리지 않는다. */
  skip: string;
  hasLocal: boolean;
  onPick: (provider: string, item: SymbolItem) => void;
}) {
  if (!found) return <p className="note">다른 시장도 찾는 중…</p>;
  // 빠진 시장은 조용히 넘기지 않는다. 안 그러면 "그 시장엔 없다"고 잘못 배운다.
  const missing = Object.entries(found.sources).filter(([, s]) => !s.ok);
  const groups = found.groups.filter((g) => g.provider !== skip);
  const empty = groups.length === 0 && !hasLocal;

  return (
    <>
      {groups.map((group) => (
        <div key={group.provider}>
          <div className="group-label">
            {group.name} · {group.items.length}건
          </div>
          {group.items.slice(0, VISIBLE).map((item) => (
            <Row
              key={`${group.provider}:${item.symbol}`}
              item={item}
              onPick={() => onPick(group.provider, item)}
            />
          ))}
        </div>
      ))}
      {empty && !busy && (
        <p className="note warn">
          "{query}" 로 찾은 게 없다.
        </p>
      )}
      {missing.length > 0 && (
        <p className="note">
          {missing.map(([key, s]) => `${key}: ${s.error}`).join(" · ")}
        </p>
      )}
    </>
  );
}
