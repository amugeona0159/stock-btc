import type {
  Forecast,
  IndicatorSpec,
  PatternHit,
  ProviderInfo,
  Requested,
  SymbolNames,
} from "./types";

/**
 * 접속 토큰. 배포판은 공개 주소라 이게 없으면 서버가 401 을 준다.
 *
 * 집에서 개발할 때는 서버가 토큰을 안 걸어 두므로(`MARKET_LENS_TOKEN` 미설정)
 * 여기가 비어 있어도 그대로 돈다.
 */
const TOKEN_KEY = "market-lens-token";

export function token(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";                       // 사파리 프라이빗에서 localStorage 가 막힌다
  }
}

export function setToken(value: string): void {
  try {
    if (value) localStorage.setItem(TOKEN_KEY, value);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* 못 저장해도 이번 세션은 돈다 */
  }
}

export function authHeaders(): Record<string, string> {
  const value = token();
  return value ? { Authorization: `Bearer ${value}` } : {};
}

/** 웹소켓은 **헤더를 못 붙인다.** 토큰을 쿼리로 실어야 한다. */
export function withToken(url: string): string {
  const value = token();
  if (!value) return url;
  return url + (url.includes("?") ? "&" : "?") + `token=${encodeURIComponent(value)}`;
}

async function fail(res: Response): Promise<never> {
  const detail = (await res.json().catch(() => null))?.detail;
  if (res.status === 401) {
    throw new Error(detail ?? "토큰이 필요하다 — 설정에서 넣을 것");
  }
  throw new Error(detail ?? res.statusText);
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: authHeaders() });
  if (!res.ok) return fail(res);
  return res.json();
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  if (!res.ok) return fail(res);
  return res.json();
}

async function send<T>(path: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) return fail(res);
  return res.json();
}

export const alerts = {
  list: () => get<import("./types").AlertsView>("/api/alerts"),
  create: (body: {
    provider: string; symbol: string; kind: string; price: number; note?: string;
  }) => post<import("./types").AlertRule>("/api/alerts", body),
  patch: (id: string, body: Record<string, unknown>) =>
    send<import("./types").AlertRule>(`/api/alerts/${id}`, "PATCH", body),
  remove: (id: string) => send<{ ok: boolean }>(`/api/alerts/${id}`, "DELETE"),
  read: (id: string) => post<{ ok: boolean }>(`/api/alerts/fired/${id}/read`, {}),
  archive: (id: string) => post<{ ok: boolean }>(`/api/alerts/fired/${id}/archive`, {}),
  fromRecommendation: (provider: string, days: number) =>
    post<{ made: import("./types").AlertRule[]; date?: string }>(
      `/api/alerts/from-recommendation?provider=${provider}&days=${days}`, {}),
  test: () => post<{ sent: number; subscriptions: number; push: boolean }>(
    "/api/alerts/test", {}),

  /** 기록. 알림함과 달리 **보관한 것도 기본으로 들어온다.** `days: 0` 이 전체다. */
  log: (query: { days: number; symbol?: string; kind?: string; archived?: boolean }) => {
    const params = new URLSearchParams({ days: String(query.days) });
    if (query.symbol) params.set("symbol", query.symbol);
    if (query.kind) params.set("kind", query.kind);
    if (query.archived === false) params.set("archived", "false");
    return get<import("./types").AlertLogView>(`/api/alerts/log?${params}`);
  },
  /** 보고 있는 기록의 뒷값만 물어본다. 느리고 실패도 하는 조회라 따로 부른다. */
  outcome: (ids: string[]) =>
    post<import("./types").AlertOutcomes>("/api/alerts/log/outcome", { ids }),
};

export const positions = {
  list: () => get<import("./types").PositionsView>("/api/positions"),
  open: (body: {
    provider: string; symbol: string; entry: number; shares: number;
    band?: [number, number] | null; expected?: number | null;
    days?: number | null; source?: string; note?: string;
  }) => post<{ position: import("./types").Position; warning: string }>(
    "/api/positions", body),
  /** 실제로 판 만큼 덜어낸다. 다 팔면 닫힌다. */
  sold: (id: string, body: { price: number; shares: number; reason?: string }) =>
    post<{ position: import("./types").Position }>(`/api/positions/${id}/sold`, body),
  /** 닿았는데 안 팔았다 — 손절선을 올려 다시 건다. */
  held: (id: string) =>
    post<{ position: import("./types").Position }>(`/api/positions/${id}/held`, {}),
  close: (id: string, reason = "manual") =>
    post<{ position: import("./types").Position }>(
      `/api/positions/${id}/close?reason=${reason}`, {}),
  retarget: (id: string, body: { stop?: number; targets?: unknown[] }) =>
    send<{ position: import("./types").Position }>(`/api/positions/${id}`, "PATCH", body),
  remove: (id: string) => send<{ ok: boolean }>(`/api/positions/${id}`, "DELETE"),
  advice: (id: string, days = 1) =>
    get<import("./types").PositionAdvice>(`/api/positions/${id}/advice?days=${days}`),
};

export const api = {
  providers: () => get<{ providers: ProviderInfo[] }>("/api/providers"),

  /** 아침 추천이 이 종목을 1·2·3일 각각 어디에 뒀나. 「판단」이 "언제" 에 답하는 데 쓴다.
   *  `recommend.symbol` 과 같은 것을 가리킨다 — `App` 이 `api` 만 들고 있어 여기 둔다. */
  recommendSymbol: (provider: string, symbol: string) =>
    get<import("./say").SymbolPlan>(
      `/api/recommend/symbol?provider=${encodeURIComponent(provider)}`
      + `&symbol=${encodeURIComponent(symbol)}`),

  /**
   * 종목 기호 → 한글 이름. 부팅할 때 한 번만 받는다.
   *
   * **응답마다 이름을 실어 나르지 않는다.** 표는 서버(`screen/names.py`) 한 벌이고
   * 화면은 그걸 통째로 받아 찾아 쓴다. 열쇠가 `SOLUSDT` 처럼 **심볼 그대로**라
   * 거래쌍을 떼는 규칙을 이쪽에 또 만들 필요가 없다.
   */
  names: () => get<{ names: SymbolNames }>("/api/names"),

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
  /** 국내주식·해외주식·코인 셋을 한 번에. **화면이 시장을 고르지 않게 하려는 것이다** —
   *  차트가 BTC 에 서 있다고 코인 추천만 보이면 안 된다.
   *
   *  시장 하나만 묻는 `GET /api/recommend` 도 서버에 남아 있다(알림의
   *  `from-recommendation` 이 같은 함수를 쓴다). 화면에서는 안 부른다 —
   *  부르는 데가 없는 클라이언트 함수를 남겨 두면 다음 사람이 그게 쓰이는 길인 줄 안다. */
  groups: (days: number) =>
    get<{ days: number; groups: import("./types").RecommendGroup[] }>(
      `/api/recommend/groups?days=${days}`),

  /** 이 종목을 1·2·3일 각각 어떻게 봤나. 「판단」이 추천과 어긋나 보일 때 그게
   *  **지평이 다른 두 질문**이라는 걸 읽을 수 있게 하는 자료다. */
  symbol: (provider: string, symbol: string) =>
    get<import("./say").SymbolPlan>(
      `/api/recommend/symbol?provider=${encodeURIComponent(provider)}`
      + `&symbol=${encodeURIComponent(symbol)}`),
};

export const screen = {
  /** 오늘 관심있게 볼 종목. 순위는 "크게 움직일 순서"지 "오를 순서"가 아니다. */
  rank: (body: { provider: string; timeframe: string; horizon: number; limit?: number }) =>
    post<import("./types").ScreenResult>("/api/screen", body),

  status: () => get<{ available: boolean; updated: string | null;
                      providers: Record<string, number[]> }>("/api/screen/status"),
};
