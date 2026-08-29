/** 서버가 내려주는 모양. 지표 목록을 여기 다시 적지 않는다 —
 *  화면의 지표 패널은 /api/indicators 가 준 카탈로그로 만들어진다. */

export type Direction = -1 | 0 | 1;

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  closed: boolean;
}

export interface Point {
  time: number;
  /** 값이 없는 자리(warm-up·결측)는 `time` 만 온다 — lightweight-charts 의
   *  whitespace 다. 버리지 않고 보내야 보조 패널의 시간축 인덱스가 메인과 맞는다. */
  value?: number;
}

export type Draw =
  | "line" | "area" | "histogram" | "band" | "cloud" | "marker" | "level" | "step";

export interface OutputSpec {
  key: string;
  label: string;
  draw: Draw;
  pane: "price" | "own";
  color: string;
  pair: string | null;
  optional: boolean;
  offset: number;
  offsetParam: string | null;
}

export interface ParamSpec {
  key: string;
  label: string;
  default: number | string | boolean;
  kind: "int" | "float" | "choice" | "bool";
  min: number | null;
  max: number | null;
  step: number | null;
  choices: string[];
}

export interface IndicatorSpec {
  key: string;
  name: string;
  category: string;
  formula: string;
  pane: "price" | "own";
  source: string | null;
  params: ParamSpec[];
  outputs: OutputSpec[];
}

export interface SeriesOutput extends Omit<OutputSpec, "offset" | "offsetParam"> {
  data: Point[];
}

export interface IndicatorResult {
  id: string;
  key: string;
  name?: string;
  category?: string;
  pane?: "price" | "own";
  params?: Record<string, unknown>;
  formula?: string;
  outputs?: SeriesOutput[];
  error?: string;
}

export interface Reason {
  key: string;
  label: string;
  direction: Direction;
  strength: number;
  weight: number;
  text: string;
}

export interface Signal {
  direction: Direction;
  label: string;
  confidence: number;
  score: number;
  barTs: number | null;
  reasons: Reason[];
}

export interface StatForecast {
  available: boolean;
  reason?: string;
  horizon?: number;
  last?: number;
  mid?: number;
  bands?: Record<string, { low: number; high: number }>;
  atrBand?: { low: number; high: number; atr: number } | null;
  expectedMovePct?: number;
  targetTs?: number;
}

export interface MonteCarlo {
  available: boolean;
  reason?: string;
  probUp?: number;
  percentiles?: Record<string, number>;
}

export interface Forecast {
  horizon: number;
  timeframe: string;
  layers: {
    rule: { available: boolean; direction: Direction; label: string; confidence: number };
    stat: StatForecast;
    monteCarlo: MonteCarlo;
    ml: { available: boolean; reason?: string; direction?: number; confidence?: number };
  };
}

export interface PatternHit {
  key: string;
  label: string;
  direction: Direction;
  ts: number;
  bars_ago: number;
}

export interface ProviderInfo {
  key: string;
  name: string;
  market: string;
  timeframes: string[];
  requiresKey: boolean;
  realtime: boolean;
  note: string;
  available: boolean;
  reason: string;
  defaultSymbols: string[];
  /** 전체 종목 목록을 줄 수 있나. false 면 '검색만 되는 시장'이다. */
  listsSymbols: boolean;
}

export interface Requested {
  key: string;
  params: Record<string, unknown>;
}

// --- 예측·이벤트·근거 -------------------------------------------------------

export interface PathPoint {
  time: number;
  value: number;
}

export interface AnalogPath {
  id: string;
  source: string;
  ts: number;
  distance: number;
  weight: number;
  outcome: number;
  windowStartTs: number;
  windowEndTs: number;
  points: PathPoint[];
}

export interface Projection {
  available: boolean;
  reason?: string;
  horizon?: number;
  last?: number;
  lastTs?: number;
  targetTs?: number;
  sampleCount: number;
  paths: AnalogPath[];
  bands: Record<string, PathPoint[]>;
  median?: number;
  expectedMovePct?: number;
  probUp?: number;
  diagnostics: {
    coverage: number | null;
    nominalCoverage: number;
    widenFactor: number;
    distanceMin: number;
    distanceMedian: number;
    distanceMax: number;
    reliable: boolean;
  };
  citations?: Evidence[];
}

export interface EventMark {
  id: string;
  ts: number;
  kind: string;
  kindLabel: string;
  title: string;
  source: string;
  sourceLabel: string;
  scope: string;
  severity: number;
  scheduled: boolean;
  url: string;
  tags: string[];
  note: string;
}

export interface EventStudy {
  available: boolean;
  reason?: string;
  label?: string;
  count?: number;
  before?: number;
  after?: number;
  offsets?: number[];
  meanCar?: number[];
  medianCar?: number[];
  carLow?: number[];
  carHigh?: number[];
  tStat?: number[];
  finalCarPct?: number;
  finalTStat?: number;
  hitRate?: number;
  significant?: boolean;
  overlapping?: boolean;
  events?: Array<EventMark & { carPct: number; immediatePct: number }>;
}

export interface Source {
  title: string;
  authors: string;
  year: number;
  venue: string;
  url: string;
}

export interface Evidence {
  key: string;
  field: string;
  fieldLabel: string;
  claim: string;
  effect: string;
  limits: string;
  confidence: "strong" | "moderate" | "weak" | "contested";
  confidenceLabel: string;
  usedBy: string[];
  sources: Source[];
}

export interface ScenarioForm {
  horizon_hours: number;
  horizon_text: string;
  event_kinds: string[];
  event_tags: string[];
  require_volatility: number | null;
  require_trend: number | null;
  emphasis: string[];
  context_weight: number;
  direction_hint: number | null;
  interpretation: string;
}

export interface ScenarioView {
  question: string;
  timeframe: string;
  horizon: number;
  horizonHours: number;
  horizonText: string;
  eventKinds: string[];
  eventTags: string[];
  requireVolatility: number | null;
  requireVolatilityLabel: string | null;
  requireTrend: number | null;
  requireTrendLabel: string | null;
  emphasis: string[];
  contextWeight: number;
  directionHint: number | null;
  interpretation: string;
  parsedBy: "rule" | "llm" | "form";
  notes: string[];
}

export interface Situation {
  available: boolean;
  calendar?: { iso: string; text: string };
  regime?: {
    volatility: number;
    volatilityLabel: string;
    trend: number;
    trendLabel: string;
    volPercentile: number;
    adx: number | null;
  };
}

export interface AskResult {
  scenario: ScenarioView;
  situation: Situation;
  projection: Projection;
  eventStudy: EventStudy;
  eventPath: PathPoint[] | null;
  matchedEvents: EventMark[];
  answer: string;
  citations: Evidence[];
  eventSources: Record<string, { count: number; ok: boolean; error: string }>;
}

// --- 학습층 ---------------------------------------------------------------

export interface TrainReport {
  rows: number;
  symbols: string[];
  horizon: number;
  window: number;
  horizons: number[];
  folds: number;
  /** 모델 단독이 변동성 기준선 대비. */
  skill: Record<string, number>;
  /** 기준선과 섞은 결과가 변동성 기준선 대비. 실제로 쓰는 건 이쪽이다. */
  blendSkill: Record<string, number>;
  /** 변동성 기준선이 단순 기준선 대비. 변동성 스케일링만으로 여기까지 간다. */
  volSkill: Record<string, number>;
  /** 지평별로 모델을 얼마나 섞는지. */
  weights: Record<string, number>;
  coverage: Record<string, number>;
  directionAccuracy: number | null;
  directionBaseline: number | null;
  importance: Array<{ feature: string; score: number }>;
  learnedSomething: boolean;
  verdict: string;
  citations: Evidence[];
}

export interface Learned {
  /** 학습이 '이 조건에서는 틀린다'고 잰 자리. 방향을 말하지 않는다. */
  abstain?: boolean;
  abstainReason?: string;
  available: boolean;
  reason?: string;
  model?: string;
  source?: "blend" | "volatility-baseline";
  sourceLabel?: string;
  weight?: number;
  horizon?: number;
  last?: number;
  atrPct?: number;
  bands?: Record<string, PathPoint[]>;
  median?: number;
  expectedMovePct?: number;
  probUp?: number;
  direction?: number;
  directionConfidence?: number;
  directionBeatsBaseline?: boolean;
  report?: TrainReport;
  verdict?: string;
}

export interface ModelInfo {
  name: string;
  horizon: number;
  rows: number;
  symbols: string[];
  learnedSomething: boolean;
  skill: number | null;
}

export interface TrainResult {
  model: string;
  report: TrainReport;
  skipped: Array<{ symbol: string; reason: string }>;
  /** 이 봉·지평에서 학습이 통한 적이 있는지에 대한 사전 안내. */
  note: string | null;
}

/** `scripts/daily.py` 가 매일 남기는 기록. `GET /api/learning`. */
export interface Champion {
  target: string;
  config: { horizon: number; window: number; neighbours: number; folds: number; peer_count: number };
  skill: number | null;
  learned: boolean;
  rows: number;
  symbols: string[];
  updated: string;
  /** 이 대상에 지금까지 시험한 횟수. 클수록 '이겼다'를 덜 믿어야 한다. */
  trials: number;
  promotions: number;
}

export interface LearningTrial {
  at: string;
  target: string;
  championSkill: number;
  challengerSkill: number | null;
  change: string;
  promoted: boolean;
  trials: number;
}

export interface LearningState {
  available: boolean;
  updated: string | null;
  promoteMargin: number | null;
  tracked: number;
  learned: number;
  trials: number;
  promotions: number;
  champions: Champion[];
  recent: LearningTrial[];
  note: string;
}

/** 오늘의 추천. `POST /api/screen`. */
export interface ScreenItem {
  symbol: string;
  move: number | null;
  direction: number | null;
  why: Array<{ factor: string; label: string; z: number }>;
  last: number | null;
}

export interface ScreenQuality {
  factors: number;
  meanIc: number;
  /** 점수 상위 묶음 평균 − 하위 묶음 평균. 수수료 전. */
  topMinusBottomPct: number | null;
  buckets: number[] | null;
  used: Array<{ factor: string; label: string; ic: number }>;
}

export interface ScreenResult {
  available: boolean;
  reason?: string;
  horizon?: number;
  sortedBy?: "move" | "direction";
  family?: string;
  quality?: { move: ScreenQuality; direction: ScreenQuality };
  items: ScreenItem[];
  breadth?: number;
  note?: string;
  provider?: string;
  timeframe?: string;
  measuredAt?: string | null;
  /** 안 잰 시장일 때, 재 둔 시장 목록. 화면이 그리로 넘어갈 수 있게. */
  measuredProviders?: string[];
  measuredTimeframe?: string;
  skipped?: Array<{ symbol: string; reason: string }>;
}

/** 종목 한 벌. 목록과 검색이 같은 모양을 쓴다. */
export interface SymbolItem {
  symbol: string;
  label: string;
  name: string;
  market: string;
  kind: string;
  /** 검색 결과에만 붙는다. */
  provider?: string;
  providerName?: string;
  /** 같은 종목을 주는 다른 시장. 한 줄로 접은 뒤 여기 남긴다. */
  also?: string[];
}

export interface SymbolList {
  provider: string;
  listed: boolean;
  count: number;
  items: SymbolItem[];
  reason: string;
}

export interface SearchResult {
  q: string;
  groups: Array<{ provider: string; name: string; market: string; items: SymbolItem[] }>;
  /** 왜 빠졌는지. 조용히 빠지면 "그 시장엔 없다"고 잘못 배우게 된다. */
  sources: Record<string, { ok: boolean; count: number; error: string }>;
}

/** 기권 규칙과 그 근거. `GET /api/gate`. */
export interface GateStatus {
  available: boolean;
  label: string | null;
  holdout: { withoutRule: number; withRule: number; n: number;
             coverage: number } | null;
  /** 최종 구간을 몇 번 열어 봤나. 볼수록 그 숫자가 닳는다. */
  holdoutLooks: number | null;
  trials: number | null;
  updated: string | null;
}

/** 오늘 아침의 매수 추천. `GET /api/recommend`. 07:30 에 얼린 것을 읽을 뿐이다. */
/**
 * 종목 기호 → 한글 이름. 열쇠는 `SOLUSDT`·`KRW-SOL`·`005930` 처럼 **심볼 그대로**다.
 * 표에 없는 심볼은 이름이 없는 것이고, 그때 화면은 티커를 그대로 쓴다 —
 * **이름을 지어내지 않는다.**
 */
export type SymbolNames = Record<string, { name: string; ticker: string }>;

export interface RecommendItem {
  symbol: string;
  last: number | null;
  /** 한글 이름(솔라나). 표에 없는 코인이면 없다 — 지어내지 않는다. */
  name?: string | null;
  /** 거래쌍을 뗀 코인 기호(SOL). 주식이면 심볼 그대로다. */
  ticker?: string | null;
  /**
   * 원화 실거래가. **환산가가 아니라 업비트 원화 마켓의 실제 종가**다 —
   * 환율을 곱한 값은 김치 프리미엄만큼 어긋나 그 값에 살 수가 없다.
   * 주식이거나 옛 파일이면 없다.
   */
  krw?: { symbol: string; last: number; ts: number } | null;
  /** 그 지평의 기대 수익률(%). 예측 분포의 중앙값이지 평균이 아니다. */
  expected: number | null;
  /** 80% 구간 [아래, 위] (%). */
  band: [number, number] | null;
  probUp: number | null;
  confidence: "low" | "mid" | "high" | null;
  source: string | null;
  abstain: boolean | null;
}

export interface RecommendRecord {
  n: number;
  enough: boolean;
  buyPct?: number;
  universePct?: number;
  /** 추천 − 후보 평균. **이게 실력이다.** */
  edgePct?: number;
  winRate?: number;
  bandHit?: number;
  lastScored?: string;
}

export interface Recommend {
  available: boolean;
  reason?: string;
  providers?: string[];
  provider?: string;
  days?: number;
  date?: string;
  generatedAt?: string;
  basedOn?: string;
  /** 마지막 확정봉이 어제가 아니면 0보다 크다 — 장이 쉰 날. */
  staleBars?: number;
  buy: RecommendItem[];
  avoid: RecommendItem[];
  /** 모델을 하나도 안 썼다 — 그때 순위는 사실상 변동성 순서다. */
  degenerate?: boolean;
  allNegative?: boolean;
  learned?: boolean;
  skill?: number | null;
  modelStale?: boolean;
  candidates?: number;
  record?: RecommendRecord;
  measured?: { directionHit?: number; bandHit?: number; n?: number; directionN?: number };
  skipped?: Array<{ symbol: string; reason: string }>;
}
