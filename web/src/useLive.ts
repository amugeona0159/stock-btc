import { useCallback, useEffect, useRef, useState } from "react";

import type { Candle, IndicatorResult, Requested, Signal } from "./types";

export type Status = "idle" | "connecting" | "live" | "error";

interface Options {
  provider: string;
  symbol: string;
  timeframe: string;
  indicators: Requested[];
  enabled: boolean;
}

interface State {
  candles: Candle[];
  indicators: IndicatorResult[];
  signal: Signal | null;
  status: Status;
  error: string | null;
}

const EMPTY: State = {
  candles: [],
  indicators: [],
  signal: null,
  status: "idle",
  error: null,
};

/** 봉 하나를 덮어쓰거나 뒤에 붙인다. 서버의 upsert 와 같은 규칙이라야
 *  새로고침 전후로 화면이 같다. */
function applyCandle(list: Candle[], incoming: Candle): Candle[] {
  if (list.length === 0) return [incoming];
  const last = list[list.length - 1];
  if (incoming.time === last.time) {
    const copy = list.slice();
    copy[copy.length - 1] = incoming;
    return copy;
  }
  if (incoming.time > last.time) return [...list, incoming];
  return list; // 늦게 도착한 봉. 과거를 되살리지 않는다.
}

export function useLive({ provider, symbol, timeframe, indicators, enabled }: Options) {
  const [state, setState] = useState<State>(EMPTY);
  const socketRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<number | null>(null);
  // 지표 선택은 자주 바뀐다. 그때마다 소켓을 다시 열지 않으려고 최신값만 들고 있는다.
  const requestRef = useRef({ provider, symbol, timeframe, indicators });
  requestRef.current = { provider, symbol, timeframe, indicators };

  const connect = useCallback(() => {
    if (!enabled) return;
    const { protocol, host } = window.location;
    const scheme = protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${scheme}//${host}/ws`);
    socketRef.current = socket;
    setState((s) => ({ ...s, status: "connecting", error: null }));

    socket.onopen = () => {
      const { provider: p, symbol: s, timeframe: t, indicators: ind } = requestRef.current;
      socket.send(
        JSON.stringify({
          action: "subscribe",
          provider: p,
          symbol: s,
          timeframe: t,
          limit: 600,
          indicators: ind,
        }),
      );
    };

    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      switch (message.type) {
        case "snapshot":
          setState({
            candles: message.candles,
            indicators: message.indicators,
            signal: message.signal,
            status: "live",
            error: null,
          });
          break;
        case "candle":
          setState((s) => ({ ...s, candles: applyCandle(s.candles, message.candle) }));
          break;
        case "analysis":
          setState((s) => ({ ...s, indicators: message.indicators, signal: message.signal }));
          break;
        case "streamError":
          // 실시간만 끊긴 것이다. 이미 받은 차트는 그대로 두고 표시만 바꾼다.
          setState((s) => ({ ...s, status: "error", error: message.reason }));
          break;
        case "error":
          setState((s) => ({ ...s, status: "error", error: message.reason }));
          socket.close();
          break;
      }
    };

    socket.onerror = () => {
      setState((s) => ({ ...s, status: "error", error: s.error ?? "연결이 끊겼다" }));
    };

    socket.onclose = () => {
      socketRef.current = null;
      if (!enabled) return;
      // 조용히 멈추는 대신 다시 붙는다. 밤새 켜 둔 화면이 죽어 있으면 안 된다.
      retryRef.current = window.setTimeout(connect, 4000);
    };
  }, [enabled]);

  useEffect(() => {
    setState(EMPTY);
    connect();
    return () => {
      if (retryRef.current) window.clearTimeout(retryRef.current);
      const socket = socketRef.current;
      socketRef.current = null;
      if (socket) {
        socket.onclose = null;
        socket.close();
      }
    };
    // 지표는 일부러 뺐다 — 체크박스 하나 켤 때마다 거래소 연결을 다시 여는 건 과하다.
    // 대신 아래에서 선택만 따로 보낸다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, symbol, timeframe, enabled, connect]);

  // 지표 선택이 바뀌면 열려 있는 소켓으로 갱신만 보낸다.
  useEffect(() => {
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ action: "indicators", indicators }));
    }
  }, [indicators]);

  return state;
}
