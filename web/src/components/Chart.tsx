import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, HeatmapChart, LineChart, ScatterChart, TreemapChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { Mode } from "../lib/palette";

echarts.use([
  BarChart, LineChart, HeatmapChart, ScatterChart, TreemapChart, GridComponent, LegendComponent,
  TooltipComponent, VisualMapComponent, MarkLineComponent, MarkPointComponent, CanvasRenderer,
]);

/** Chart chrome & ink from the palette (recessive axes, hairline grid, text tokens). */
export function chrome(mode: Mode) {
  return mode === "dark"
    ? { surface: "#1a1a19", text: "#f0efec", secondary: "#c3c2b7", muted: "#898781", grid: "#2c2c2a", axis: "#383835" }
    : { surface: "#fcfcfb", text: "#0b0b0b", secondary: "#52514e", muted: "#898781", grid: "#e1e0d9", axis: "#c3c2b7" };
}

export function baseOption(mode: Mode): echarts.EChartsCoreOption {
  const c = chrome(mode);
  return {
    backgroundColor: "transparent",
    textStyle: { fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif", color: c.secondary },
    animationDuration: 250,
    tooltip: {
      backgroundColor: c.surface,
      borderColor: c.axis,
      textStyle: { color: c.text, fontSize: 12 },
      extraCssText: "box-shadow: 0 4px 16px rgba(0,0,0,.12); border-radius: 6px;",
    },
  };
}

export function axisStyle(mode: Mode) {
  const c = chrome(mode);
  return {
    axisLine: { lineStyle: { color: c.axis } },
    axisTick: { show: false },
    axisLabel: { color: c.muted, fontSize: 11 },
    splitLine: { lineStyle: { color: c.grid, type: "solid" as const } },
    nameTextStyle: { color: c.muted, fontSize: 11 },
  };
}

interface Props {
  option: echarts.EChartsCoreOption;
  height?: number | string;
  onClick?: (p: echarts.ECElementEvent) => void;
  loading?: boolean;
  ariaLabel: string;
}

export function Chart({ option, height = 260, onClick, loading, ariaLabel }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const inst = useRef<echarts.ECharts | null>(null);
  const clickRef = useRef(onClick);
  clickRef.current = onClick;

  useEffect(() => {
    if (!el.current) return;
    const chart = echarts.init(el.current, undefined, { renderer: "canvas" });
    inst.current = chart;
    chart.on("click", (p) => clickRef.current?.(p as echarts.ECElementEvent));
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el.current);
    return () => {
      ro.disconnect();
      chart.dispose();
      inst.current = null;
    };
  }, []);

  useEffect(() => {
    inst.current?.setOption(option, { notMerge: true });
  }, [option]);

  return (
    <div
      ref={el}
      role="img"
      aria-label={ariaLabel}
      className={loading ? "chart refetching" : "chart"}
      style={{ height, width: "100%" }}
    />
  );
}
