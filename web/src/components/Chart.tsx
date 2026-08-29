import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import {
  ColorType,
  LineStyle,
  LineType,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LogicalRange,
  type MouseEventParams,
  type Time,
} from "lightweight-charts";

import type {
  Candle,
  EventMark,
  IndicatorResult,
  Learned,
  Projection,
  SeriesOutput,
} from "../types";

/** 지표 스펙은 색을 이름으로 넘긴다. 그 이름이 실제 색이 되는 곳은 여기 하나다. */
function token(name: string): string {
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(`--${name}`)
    .trim();
  return value || "#8b98a5";
}

function chartOptions(height: number, attribution = false) {
  return {
    height,
    layout: {
      background: { type: ColorType.Solid, color: "transparent" },
      textColor: token("text-dim"),
      fontSize: 11,
      // lightweight-charts 표기는 메인 패널에만 남긴다. 패널마다 붙으면 로고가 넷이 된다.
      attributionLogo: attribution,
    },
    grid: {
      vertLines: { color: token("line"), style: LineStyle.Dotted },
      horzLines: { color: token("line"), style: LineStyle.Dotted },
    },
    // 패널마다 가격축 폭이 다르면 같은 구간을 보여 줘도 세로선이 어긋난다.
    rightPriceScale: { borderColor: token("line"), minimumWidth: 64 },
    timeScale: { borderColor: token("line"), timeVisible: true, secondsVisible: false },
    crosshair: { mode: 0 as const },
    localization: { locale: "ko-KR" },
  };
}

type SeriesMap = Map<string, ISeriesApi<"Line" | "Histogram">>;

/** 예측 오버레이. 밴드는 얇고 흐리게, 중앙값은 점선, 사례 경로는 더 흐리게 —
 *  한 화면에 스무 줄이 들어오므로 굵기와 진하기로 층을 나누지 않으면 아무것도 안 읽힌다. */
const MAX_MARKERS = 20;

const BAND_STYLE: Record<string, { width: 1 | 2; opacity: string; dashed: boolean }> = {
  p10: { width: 1, opacity: "88", dashed: false },
  p25: { width: 1, opacity: "55", dashed: false },
  p50: { width: 2, opacity: "ff", dashed: true },
  p75: { width: 1, opacity: "55", dashed: false },
  p90: { width: 1, opacity: "88", dashed: false },
};

/** 한 출력 시리즈를 차트에 붙인다. draw 종류가 곧 그리는 방법이다. */
function addOutput(chart: IChartApi, output: SeriesOutput): ISeriesApi<"Line" | "Histogram"> {
  const color = token(output.color);
  if (output.draw === "histogram") {
    return chart.addHistogramSeries({
      color,
      priceLineVisible: false,
      lastValueVisible: false,
    });
  }
  return chart.addLineSeries({
    color,
    lineWidth: output.draw === "cloud" || output.draw === "band" ? 1 : 2,
    lineType: output.draw === "step" ? LineType.WithSteps : LineType.Simple,
    // 수평 레벨(피보나치·피벗)은 점선으로. 지표선과 같은 굵기면 어느 게 계산값인지 안 보인다.
    lineStyle: output.draw === "level" ? LineStyle.Dashed : LineStyle.Solid,
    lineVisible: output.draw !== "marker",
    pointMarkersVisible: output.draw === "marker",
    pointMarkersRadius: output.draw === "marker" ? 3 : undefined,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
  });
}

/** 지표 결과에서 이 패널이 그릴 출력만. 껐다 켰는지는 시리즈 키 집합으로 판단한다. */
function outputsFor(results: IndicatorResult[], pane: "price" | "own", id?: string) {
  const picked: Array<{ id: string; output: SeriesOutput }> = [];
  for (const result of results) {
    if (result.error || !result.outputs) continue;
    if (id && result.id !== id) continue;
    for (const output of result.outputs) {
      const target = output.pane === "own" || result.pane === "own" ? "own" : "price";
      if (target !== pane) continue;
      // optional 은 "기본으로는 숨기는 보조선" 이다. 이걸 켜서 그리면 볼린저의 %B 같은
      // 보조 출력이 제멋대로 서브패널을 만든다.
      if (output.optional || output.data.length === 0) continue;
      picked.push({ id: `${result.id}.${output.key}`, output });
    }
  }
  return picked;
}

function syncSeries(chart: IChartApi, map: SeriesMap, items: Array<{ id: string; output: SeriesOutput }>) {
  const wanted = new Set(items.map((i) => i.id));
  for (const [key, series] of map) {
    if (!wanted.has(key)) {
      chart.removeSeries(series);
      map.delete(key);
    }
  }
  for (const { id, output } of items) {
    let series = map.get(id);
    if (!series) {
      series = addOutput(chart, output);
      map.set(id, series);
    }
    // 값이 없는 점은 `{time}` 만 온다(whitespace). 그대로 넘겨야 인덱스가 맞는다.
    series.setData(output.data as Array<{ time: Time; value?: number }>);
  }
}

interface Props {
  candles: Candle[];
  indicators: IndicatorResult[];
  projection?: Projection | null;
  learned?: Learned | null;
  eventPath?: Array<{ time: number; value: number }> | null;
  events?: EventMark[];
  onEventClick?: (mark: EventMark) => void;
}

export function ChartStack({
  candles,
  indicators,
  projection,
  learned,
  eventPath,
  events,
}: Props) {
  const mainRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const priceRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const overlayRef = useRef<SeriesMap>(new Map());
  const forecastRef = useRef<SeriesMap>(new Map());

  const subRefs = useRef<Map<string, { chart: IChartApi; series: SeriesMap; node: HTMLDivElement }>>(
    new Map(),
  );
  // 자기 패널을 쓰는 지표만 아래에 쌓는다.
  const subPanes = useMemo(
    () =>
      indicators
        .filter((r) => !r.error && outputsFor([r], "own").length > 0)
        .map((r) => ({ id: r.id, name: r.name ?? r.key })),
    [indicators],
  );
  // **패널 목록의 정체성은 id 문자열이다.** `subPanes` 배열은 실시간 틱마다 새로
  // 만들어지므로 그걸 deps 에 쓰면 매 틱 구독을 끊었다 다시 걸고, 그때마다 범위를
  // 강제로 맞춰 사용자가 방금 스크롤한 자리가 되돌아간다.
  const paneKey = subPanes.map((p) => p.id).join(",");

  // --- 메인 차트는 한 번만 만든다 ---
  useLayoutEffect(() => {
    if (!mainRef.current) return;
    const chart = createChart(mainRef.current, {
      ...chartOptions(mainRef.current.clientHeight || 360, true),
      width: mainRef.current.clientWidth,
    });
    chartRef.current = chart;

    priceRef.current = chart.addCandlestickSeries({
      upColor: token("up"),
      downColor: token("down"),
      borderUpColor: token("up"),
      borderDownColor: token("down"),
      wickUpColor: token("up"),
      wickDownColor: token("down"),
    });
    volumeRef.current = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
      color: token("neutral"),
      priceLineVisible: false,
      lastValueVisible: false,
    });
    // 거래량은 아래 20% 에만. 가격축과 같은 스케일에 두면 캔들이 납작해진다.
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

    const resize = () => {
      if (!mainRef.current) return;
      chart.applyOptions({
        width: mainRef.current.clientWidth,
        height: mainRef.current.clientHeight,
      });
    };
    const observer = new ResizeObserver(resize);
    observer.observe(mainRef.current);

    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
      overlayRef.current.clear();
      forecastRef.current.clear();
    };
  }, []);

  // --- 캔들 ---
  useEffect(() => {
    if (!priceRef.current || !volumeRef.current) return;
    priceRef.current.setData(
      candles.map((c) => ({
        time: c.time as Time,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      })),
    );
    volumeRef.current.setData(
      candles.map((c) => ({
        time: c.time as Time,
        value: c.volume,
        color: c.close >= c.open ? `${token("up")}55` : `${token("down")}55`,
      })),
    );
  }, [candles]);

  // --- 가격축 위 지표 ---
  useEffect(() => {
    if (!chartRef.current) return;
    syncSeries(chartRef.current, overlayRef.current, outputsFor(indicators, "price"));
  }, [indicators]);

  // --- 예측 오버레이 ---
  // 마지막 봉 이후 시각에 점을 찍는다. lightweight-charts 는 미래 시각도 그냥 그린다 —
  // 캔들이 없는 구간이라 축이 알아서 늘어난다.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const map = forecastRef.current;
    const wanted = new Set<string>();

    const draw = (
      id: string,
      points: Array<{ time: number; value: number }>,
      color: string,
      width: 1 | 2 | 3,
      dashed: boolean,
    ) => {
      if (points.length < 2) return;
      wanted.add(id);
      let series = map.get(id);
      if (!series) {
        series = chart.addLineSeries({
          color,
          lineWidth: width,
          lineStyle: dashed ? LineStyle.Dashed : LineStyle.Solid,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          // 예측선은 세로축 계산에 끼지 않는다. 끼면 사례 하나가 +20% 로 뻗은 순간
          // 축이 통째로 늘어나 정작 봐야 할 캔들이 아래에 눌린다.
          autoscaleInfoProvider: () => null,
        });
        map.set(id, series);
      }
      series.applyOptions({ color, lineWidth: width });
      series.setData(points as Array<{ time: Time; value?: number }>);
    };

    if (projection?.available) {
      // 사례 경로 먼저(제일 흐리게) → 밴드 → 중앙값 순으로 얹어야 위층이 보인다.
      for (const path of projection.paths) {
        draw(`path-${path.id}`, path.points, `${token("neutral")}38`, 1, false);
      }
      for (const [key, points] of Object.entries(projection.bands)) {
        const style = BAND_STYLE[key];
        if (!style) continue;
        draw(`band-${key}`, points, `${token("accent")}${style.opacity}`,
             style.width, style.dashed);
      }
    }
    if (eventPath && eventPath.length > 1) {
      draw("event-path", eventPath, token("warn"), 2, true);
    }
    // 학습층 밴드. 사례 밴드와 색을 달리해 둘이 다른 말을 할 때 그게 보이게 한다.
    if (learned?.available && learned.bands) {
      for (const [key, points] of Object.entries(learned.bands)) {
        const style = BAND_STYLE[key];
        if (!style) continue;
        draw(`learned-${key}`, points, `${token("up")}${style.opacity}`,
             style.width, style.dashed);
      }
    }

    for (const [key, series] of map) {
      if (!wanted.has(key)) {
        chart.removeSeries(series);
        map.delete(key);
      }
    }
  }, [projection, learned, eventPath]);

  // --- 사건 마커 ---
  useEffect(() => {
    const series = priceRef.current;
    if (!series) return;
    if (!events || events.length === 0) {
      series.setMarkers([]);
      return;
    }
    // 캔들 범위 밖의 사건은 마커를 못 붙인다. 붙이면 축이 통째로 늘어나 차트가 찌그러진다.
    const first = candles[0]?.time ?? 0;
    const last = candles.at(-1)?.time ?? 0;
    const visible = events.filter((e) => {
      const t = Math.floor(e.ts / 1000);
      return t >= first && t <= last;
    });
    // 화면에 마커가 스무 개를 넘으면 캔들이 안 보인다. 굵직한 것부터 자른다.
    const shown = visible
      .sort((a, b) => b.severity - a.severity)
      .slice(0, MAX_MARKERS)
      .sort((a, b) => a.ts - b.ts);

    // 같은 봉에 여러 사건이 있으면 하나로 합친다. 시각이 겹친 마커는
    // lightweight-charts 가 하나만 그리고 나머지를 조용히 버린다.
    const byBar = new Map<number, EventMark[]>();
    for (const e of shown) {
      const t = Math.floor(e.ts / 1000);
      const bucket = byBar.get(t);
      if (bucket) bucket.push(e);
      else byBar.set(t, [e]);
    }

    series.setMarkers(
      Array.from(byBar.entries())
        .sort((a, b) => a[0] - b[0])
        .map(([time, group]) => {
          const lead = group.reduce((a, b) => (b.severity > a.severity ? b : a));
          const extra = group.length > 1 ? ` +${group.length - 1}` : "";
          return {
            time: time as Time,
            position: "aboveBar" as const,
            color: lead.severity >= 0.7 ? token("down") : token("warn"),
            shape: lead.scheduled ? ("square" as const) : ("circle" as const),
            // 라벨은 굵직한 것에만. 전부 붙이면 글자끼리 겹쳐 아무것도 못 읽는다.
            text: lead.severity >= 0.6
              ? `${lead.title.length > 16 ? `${lead.title.slice(0, 16)}…` : lead.title}${extra}`
              : "",
          };
        }),
    );
  }, [events, candles]);

  // --- 서브 패널 ---
  useEffect(() => {
    const alive = new Set(subPanes.map((p) => p.id));
    for (const [id, entry] of subRefs.current) {
      if (!alive.has(id)) {
        entry.chart.remove();
        subRefs.current.delete(id);
      }
    }
    for (const pane of subPanes) {
      const node = document.getElementById(`pane-${pane.id}`) as HTMLDivElement | null;
      if (!node) continue;
      let entry = subRefs.current.get(pane.id);
      if (!entry || entry.node !== node) {
        entry?.chart.remove();
        const chart = createChart(node, {
          ...chartOptions(node.clientHeight || 118),
          width: node.clientWidth,
        });
        entry = { chart, series: new Map(), node };
        subRefs.current.set(pane.id, entry);
      }
      syncSeries(entry.chart, entry.series, outputsFor(indicators, "own", pane.id));
    }
  }, [indicators, subPanes]);

  // --- 시간축·크로스헤어 맞물림 ---
  //
  // 패널마다 따로 움직이면 RSI 를 볼 때 가격이 다른 구간을 보고 있게 된다.
  // 여기가 되려면 셋이 다 맞아야 한다:
  //
  // 1. **인덱스 원점이 같아야 한다.** 서버가 warm-up 자리를 whitespace 로 채워
  //    보낸다(`core/series.py: _points`). 버리면 RSI 의 0번이 캔들 14번째가 된다.
  // 2. **가드가 프레임을 넘어야 한다.** v4 의 `setVisibleLogicalRange` 는 그 자리에서
  //    적용되지 않고 다음 페인트에 적용되며 거기서 이벤트가 난다. 같은 틱에서 켰다
  //    끄는 플래그로는 되쏘기를 못 막아 핑퐁이 된다 — **마지막으로 넘긴 범위를
  //    기억해 두고 같은 범위면 무시**한다.
  // 3. **구독을 매 틱 다시 걸면 안 된다.** deps 는 `paneKey`(id 문자열)다.
  useEffect(() => {
    const charts = () => [
      chartRef.current,
      ...Array.from(subRefs.current.values(), (e) => e.chart),
    ].filter(Boolean) as IChartApi[];

    const applied = new WeakMap<IChartApi, string>();
    const same = (chart: IChartApi, range: LogicalRange) => {
      const key = `${range.from.toFixed(4)}:${range.to.toFixed(4)}`;
      if (applied.get(chart) === key) return true;
      applied.set(chart, key);
      return false;
    };

    const spread = (source: IChartApi | null, range: LogicalRange | null) => {
      if (!range) return;
      for (const chart of charts()) {
        if (chart === source || same(chart, range)) continue;
        chart.timeScale().setVisibleLogicalRange(range);
      }
    };

    const rangeHandlers = charts().map((chart) => {
      const handler = (range: LogicalRange | null) => spread(chart, range);
      chart.timeScale().subscribeVisibleLogicalRangeChange(handler);
      return { chart, handler };
    });

    // 크로스헤어를 세우려면 그 차트의 시리즈가 하나 필요하다. 세로선만 쓸 것이라
    // 어느 것이든 상관없다.
    const anySeries = (chart: IChartApi) => {
      if (chart === chartRef.current) return priceRef.current;
      for (const entry of subRefs.current.values()) {
        if (entry.chart === chart) return entry.series.values().next().value ?? null;
      }
      return null;
    };

    // 크로스헤어도 같이 움직인다. 가격 위에 올린 시각의 세로선이 RSI·MACD 에도
    // 서야 "같은 26일"을 같은 선상에서 읽을 수 있다.
    const crossHandlers = charts().map((chart) => {
      const handler = (param: MouseEventParams) => {
        for (const other of charts()) {
          if (other === chart) continue;
          const series = anySeries(other);
          if (!series) continue;
          if (param.time === undefined) other.clearCrosshairPosition();
          // 값은 안 쓴다 — 세로선만 세우면 되고, 가격축은 패널마다 다르다.
          else other.setCrosshairPosition(Number.NaN, param.time, series);
        }
      };
      chart.subscribeCrosshairMove(handler);
      return { chart, handler };
    });

    // 새로 생긴 서브 패널을 메인의 현재 구간에 맞춘다.
    spread(null, chartRef.current?.timeScale().getVisibleLogicalRange() ?? null);

    return () => {
      for (const { chart, handler } of rangeHandlers) {
        chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler);
      }
      for (const { chart, handler } of crossHandlers) {
        chart.unsubscribeCrosshairMove(handler);
      }
    };
  }, [paneKey]);

  // 서브 패널의 크기. **`window.resize` 만으로는 모자라다** — 탭을 바꾸거나 패널이
  // 늘어 레이아웃만 변할 때는 창 크기가 안 바뀌어서 서브만 옛 폭을 유지하고,
  // 그러면 같은 구간을 보여 줘도 봉 간격이 달라 그림이 어긋난다.
  useEffect(() => {
    const fit = () => {
      for (const entry of subRefs.current.values()) {
        entry.chart.applyOptions({
          width: entry.node.clientWidth,
          height: entry.node.clientHeight,
        });
      }
    };
    const observer = new ResizeObserver(fit);
    for (const entry of subRefs.current.values()) observer.observe(entry.node);
    window.addEventListener("resize", fit);
    fit();
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", fit);
    };
  }, [paneKey]);

  const showLegend =
    Boolean(projection?.available) || Boolean(eventPath?.length) || Boolean(learned?.available);

  return (
    <div className="charts">
      <div className="pane main" ref={mainRef}>
        {showLegend && (
          <div className="chart-legend">
            <span>
              <i style={{ background: token("neutral") }} /> 과거 사례 경로
            </span>
            <span>
              <i className="dashed" style={{ background: token("accent") }} /> 사례 중앙값
            </span>
            <span>
              <i style={{ background: token("accent"), opacity: 0.55 }} /> 10~90% 구간
            </span>
            {eventPath?.length ? (
              <span>
                <i className="dashed" style={{ background: token("warn") }} /> 같은 사건 이후 평균
              </span>
            ) : null}
            {learned?.available ? (
              <span>
                <i style={{ background: token("up") }} /> {learned.sourceLabel} 밴드
              </span>
            ) : null}
          </div>
        )}
      </div>
      {subPanes.map((pane) => (
        <div className="pane sub" key={pane.id}>
          <span className="pane-label">{pane.name}</span>
          <div id={`pane-${pane.id}`} style={{ position: "absolute", inset: 0 }} />
        </div>
      ))}
    </div>
  );
}
