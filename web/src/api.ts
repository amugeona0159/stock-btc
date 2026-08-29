import type { Forecast, IndicatorSpec, PatternHit, ProviderInfo, Requested } from "./types";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? res.statusText);
  return res.json();
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? res.statusText);
  return res.json();
}

export const api = {
  providers: () => get<{ providers: ProviderInfo[] }>("/api/providers"),

  /** 지표 목록·파라미터·수식이 전부 여기서 온다. 화면에 표를 다시 적지 않는다. */
  catalog: () =>
    get<{
      categories: Array<{ key: string; label: string }>;
      indicators: IndicatorSpec[];
      defaults: Requested[];
    }>("/api/indicators"),

  /** 그 시장의 전체 종목. 목록이 없는 시장도 200 으로 사유를 담아 온다. */
  symbols: (provider: string) =>
    get<import("./types").SymbolList>(
      `/api/symbols?provider=${encodeURIComponent(provider)}`),

  /** `provider` 를 안 주면 **전 시장을 한꺼번에** 찾는다. */
  search: (q: string, provider?: string) =>
    get<import("./types").SearchResult>(
      `/api/search?q=${encodeURIComponent(q)}` +
        (provider ? `&provider=${encodeURIComponent(provider)}` : ""),
    ),

  analyze: (body: {
    provider: string;
    symbol: string;
    timeframe: string;
    horizon: number;
    indicators: Requested[];
  }) =>
    post<{
      forecast: Forecast;
      patterns: PatternHit[];
      situation: import("./types").Situation;
    }>("/api/analyze", body),
};

export const research = {
  library: () => get<{ entries: import("./types").Evidence[] }>("/api/research"),
};

export const predict = {
  ask: (body: {
    provider: string;
    symbol: string;
    timeframe: string;
    question: string;
    window?: number;
    limit?: number;
    form?: import("./types").ScenarioForm | null;
    use_llm?: boolean;
  }) => post<import("./types").AskResult>("/api/ask", body),

  events: (body: {
    provider: string;
    symbol: string;
    timeframe: string;
    limit?: number;
    sources?: string[];
  }) =>
    post<{
      count: number;
      events: import("./types").EventMark[];
      sources: Record<string, { count: number; ok: boolean; error: string }>;
      available: string[];
    }>("/api/events", body),
};

export const learn = {
  models: () => get<{ models: import("./types").ModelInfo[] }>("/api/models"),

  /** 매일 도는 자동 학습이 남긴 기록. 서버는 읽기만 한다. */
  state: () => get<import("./types").LearningState>("/api/learning"),

  /** 자율 학습이 찾아낸 기권 규칙과 그 근거. */
  gate: () => get<import("./types").GateStatus>("/api/gate"),

  train: (body: {
    provider: string;
    symbol: string;
    timeframe: string;
    limit?: number;
    horizon?: number;
    window?: number;
    peers?: string[];
  }) => post<import("./types").TrainResult>("/api/train", body),

  predict: (body: { provider: string; symbol: string; timeframe: string }) =>
    post<import("./types").Learned>("/api/learned", body),
};

export const recommend = {
  /** 오늘 아침의 매수 추천. 서버는 얼린 파일을 읽을 뿐이라 같은 날 답이 안 바뀐다. */
  today: (provider: string, days: number) =>
    get<import("./types").Recommend>(
      `/api/recommend?provider=${encodeURIComponent(provider)}&days=${days}`),
};

export const screen = {
  /** 오늘 관심있게 볼 종목. 순위는 "크게 움직일 순서"지 "오를 순서"가 아니다. */
  rank: (body: { provider: string; timeframe: string; horizon: number; limit?: number }) =>
    post<import("./types").ScreenResult>("/api/screen", body),

  status: () => get<{ available: boolean; updated: string | null;
                      providers: Record<string, number[]> }>("/api/screen/status"),
};
