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
