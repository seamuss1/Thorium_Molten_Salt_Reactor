import ReactECharts from "echarts-for-react";

interface MetricChartProps {
  metrics: Record<string, unknown>;
  title?: string;
}

export function MetricChart({ metrics, title = "Metrics" }: MetricChartProps) {
  const rows = Object.entries(metrics)
    .filter(([, value]) => typeof value === "number" && Number.isFinite(value))
    .slice(0, 12) as Array<[string, number]>;

  if (!rows.length) {
    return <div className="empty-panel">No numeric metrics available.</div>;
  }

  const option = {
    title: { text: title, left: 0, textStyle: { fontSize: 13, fontWeight: 600 } },
    grid: { left: 118, right: 18, top: 42, bottom: 24 },
    xAxis: { type: "value", axisLabel: { color: "#51606f" }, splitLine: { lineStyle: { color: "#dfe5ea" } } },
    yAxis: {
      type: "category",
      data: rows.map(([key]) => key),
      axisLabel: { color: "#303944", width: 108, overflow: "truncate" }
    },
    tooltip: { trigger: "axis" },
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
