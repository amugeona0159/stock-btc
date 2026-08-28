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
  value: number;
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
