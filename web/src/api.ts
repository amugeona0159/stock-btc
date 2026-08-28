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

  search: (provider: string, q: string) =>
    get<{ results: Array<{ symbol: string; label: string }> }>(
      `/api/search?provider=${encodeURIComponent(provider)}&q=${encodeURIComponent(q)}`,
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

  train: (body: {
    provider: string;
    symbol: string;
    timeframe: string;
    limit?: number;
    horizon?: number;
    window?: number;
  }) =>
    post<{ model: string; report: import("./types").TrainReport }>("/api/train", body),

  predict: (body: { provider: string; symbol: string; timeframe: string }) =>
    post<import("./types").Learned>("/api/learned", body),
};
