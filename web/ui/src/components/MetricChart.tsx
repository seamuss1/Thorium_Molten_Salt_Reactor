import { useEffect, useState } from "react";
import ReactECharts from "echarts-for-react";

interface MetricChartProps {
  metrics: Record<string, unknown>;
  title?: string;
}

export function MetricChart({ metrics, title = "Metrics" }: MetricChartProps) {
  const compact = useCompactChart();
  const rows = Object.entries(metrics)
    .filter(([, value]) => typeof value === "number" && Number.isFinite(value))
    .slice(0, 12) as Array<[string, number]>;

  if (!rows.length) {
    return <div className="empty-panel">No numeric metrics available.</div>;
  }

  const option = {
    title: { text: title, left: 0, textStyle: { fontSize: 13, fontWeight: 600 } },
    grid: { left: compact ? 82 : 118, right: compact ? 8 : 18, top: 42, bottom: 24 },
    xAxis: { type: "value", axisLabel: { color: "#51606f" }, splitLine: { lineStyle: { color: "#dfe5ea" } } },
    yAxis: {
      type: "category",
      data: rows.map(([key]) => key),
      axisLabel: { color: "#303944", width: compact ? 72 : 108, overflow: "truncate" }
    },
    tooltip: { trigger: "axis", confine: true },
    series: [
      {
        type: "bar",
        data: rows.map(([, value]) => value),
        itemStyle: { color: "#0f766e", borderRadius: [0, 3, 3, 0] }
      }
    ]
  };

  return <ReactECharts className="chart" option={option} notMerge lazyUpdate />;
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
