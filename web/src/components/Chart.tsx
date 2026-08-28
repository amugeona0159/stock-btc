import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import {
  ColorType,
  LineStyle,
  LineType,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LogicalRange,
  type Time,
} from "lightweight-charts";

import type { Candle, IndicatorResult, SeriesOutput } from "../types";

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
    rightPriceScale: { borderColor: token("line") },
    timeScale: { borderColor: token("line"), timeVisible: true, secondsVisible: false },
    crosshair: { mode: 0 as const },
    localization: { locale: "ko-KR" },
  };
}

type SeriesMap = Map<string, ISeriesApi<"Line" | "Histogram">>;

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
    series.setData(output.data as Array<{ time: Time; value: number }>);
  }
}

interface Props {
  candles: Candle[];
  indicators: IndicatorResult[];
}

export function ChartStack({ candles, indicators }: Props) {
  const mainRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const priceRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const overlayRef = useRef<SeriesMap>(new Map());

  const subRefs = useRef<Map<string, { chart: IChartApi; series: SeriesMap; node: HTMLDivElement }>>(
    new Map(),
  );
  const syncing = useRef(false);

  // 자기 패널을 쓰는 지표만 아래에 쌓는다.
  const subPanes = useMemo(
    () =>
      indicators
        .filter((r) => !r.error && outputsFor([r], "own").length > 0)
        .map((r) => ({ id: r.id, name: r.name ?? r.key })),
    [indicators],
  );

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

  // --- 시간축 동기화 ---
  // 패널마다 따로 움직이면 RSI 를 볼 때 가격이 다른 구간을 보고 있게 된다.
  useEffect(() => {
    const charts = () => [
      chartRef.current,
      ...Array.from(subRefs.current.values(), (e) => e.chart),
    ].filter(Boolean) as IChartApi[];

    const apply = (source: IChartApi, range: LogicalRange | null) => {
      if (!range || syncing.current) return;
      syncing.current = true;
      for (const chart of charts()) {
        if (chart !== source) chart.timeScale().setVisibleLogicalRange(range);
      }
      syncing.current = false;
    };

    const handlers = charts().map((chart) => {
      const handler = (range: LogicalRange | null) => apply(chart, range);
      chart.timeScale().subscribeVisibleLogicalRangeChange(handler);
      return { chart, handler };
    });

    // 새로 생긴 서브 패널을 메인의 현재 구간에 맞춘다.
    const current = chartRef.current?.timeScale().getVisibleLogicalRange();
    if (current && chartRef.current) apply(chartRef.current, current);

    return () => {
      for (const { chart, handler } of handlers) {
        chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler);
      }
    };
  }, [subPanes]);

  // 서브 패널의 폭도 창을 따라간다.
  useEffect(() => {
    const resize = () => {
      for (const entry of subRefs.current.values()) {
        entry.chart.applyOptions({
          width: entry.node.clientWidth,
          height: entry.node.clientHeight,
        });
      }
    };
    window.addEventListener("resize", resize);
    resize();
    return () => window.removeEventListener("resize", resize);
  }, [subPanes]);

  return (
    <div className="charts">
      <div className="pane main" ref={mainRef} />
      {subPanes.map((pane) => (
        <div className="pane sub" key={pane.id}>
          <span className="pane-label">{pane.name}</span>
          <div id={`pane-${pane.id}`} style={{ position: "absolute", inset: 0 }} />
        </div>
      ))}
    </div>
  );
}
