type Tone = "ok" | "warn" | "danger" | "info" | "neutral";

const TONE_BY_KEYWORD: Array<[RegExp, Tone]> = [
  [/(complete|completed|pass|passed|ready|ok|valid|success|ended)/i, "ok"],
  [/(running|queued|active|in[_-]?progress|started|live)/i, "info"],
  [/(fail|failed|error|blocked|missing|canceled|cancelled|reversal|high[_-]?risk)/i, "danger"],
  [/(pending|warn|warning|built|partial|medium|degraded|caution)/i, "warn"]
];

function toneFor(status: string): Tone {
  for (const [pattern, tone] of TONE_BY_KEYWORD) {
    if (pattern.test(status)) return tone;
  }
  return "neutral";
}

interface StatusBadgeProps {
  status: string;
  /** Show a leading dot indicator (default true). */
  dot?: boolean;
  className?: string;
}

/** Color-coded status pill used for run and output-section statuses. */
export function StatusBadge({ status, dot = true, className }: StatusBadgeProps) {
  const label = status.replaceAll("_", " ");
  return (
    <span className={["status-badge", `tone-${toneFor(status)}`, className].filter(Boolean).join(" ")} title={label}>
      {dot && <span className="status-dot" aria-hidden="true" />}
      <span className="status-label">{label}</span>
    </span>
  );
}
