import { useEffect, useState } from "react";
import ReactECharts from "echarts-for-react";
import { prefersReducedMotion, useChartTokens } from "../theme";
import type { NumericRow } from "../runData";

interface MetricChartProps {
  className?: string;
  limit?: number;
  metrics?: Record<string, unknown>;
  rows?: NumericRow[];
  title?: string;
}

export function MetricChart({ className, limit = 18, metrics, rows: explicitRows, title = "Metrics" }: MetricChartProps) {
  const compact = useCompactChart();
  const tokens = useChartTokens();
  const fallbackRows: NumericRow[] = Object.entries(metrics ?? {})
    .filter(([, value]) => typeof value === "number" && Number.isFinite(value))
    .map(([label, value]) => ({ label, value: value as number }));
  const rows: NumericRow[] = explicitRows ?? fallbackRows;
  const chartRows = rows.slice(0, limit);

  if (!chartRows.length) {
    return <div className="empty-panel">No numeric metrics available.</div>;
  }

  const height = Math.max(240, Math.min(520, 72 + chartRows.length * (compact ? 24 : 28)));
  const option = {
    animation: !prefersReducedMotion(),
    title: { text: title, left: 0, textStyle: { fontSize: 13, fontWeight: 600, color: tokens.label } },
    grid: { left: compact ? 88 : 132, right: compact ? 8 : 18, top: 42, bottom: 24 },
    xAxis: {
      type: "value",
      axisLabel: { color: tokens.axis },
      axisLine: { lineStyle: { color: tokens.grid } },
      splitLine: { lineStyle: { color: tokens.grid } }
    },
    yAxis: {
      type: "category",
      data: chartRows.map((row) => row.label),
      axisLine: { lineStyle: { color: tokens.grid } },
      axisLabel: { color: tokens.label, width: compact ? 78 : 122, overflow: "truncate" }
    },
    tooltip: {
      trigger: "axis",
      confine: true,
      valueFormatter: (value: number) => new Intl.NumberFormat(undefined, { maximumSignificantDigits: 6 }).format(value)
    },
    series: [
      {
        type: "bar",
        data: chartRows.map((row) => ({
          group: row.group,
          name: row.label,
          unit: row.unit,
          value: row.value
        })),
        itemStyle: { color: tokens.accent, borderRadius: [0, 3, 3, 0] }
      }
    ]
  };

  return <ReactECharts className={className ? `chart ${className}` : "chart"} option={option} style={{ height }} notMerge lazyUpdate />;
}

function useCompactChart() {
  const [compact, setCompact] = useState(() => (typeof window === "undefined" ? false : window.matchMedia("(max-width: 720px)").matches));

  useEffect(() => {
    const query = window.matchMedia("(max-width: 720px)");
    const update = () => setCompact(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  return compact;
}
