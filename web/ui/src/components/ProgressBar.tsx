interface ProgressBarProps {
  /** Progress fraction in [0, 1]. When omitted, renders an indeterminate bar. */
  value?: number | null;
  label?: string;
}

/** Determinate/indeterminate progress bar driven by real run progress. */
export function ProgressBar({ value, label }: ProgressBarProps) {
  const indeterminate = value === null || value === undefined || !Number.isFinite(value);
  const clamped = indeterminate ? 0 : Math.max(0, Math.min(1, value));
  const percent = Math.round(clamped * 100);
  return (
    <div
      className={`progress-bar${indeterminate ? " indeterminate" : ""}`}
      role="progressbar"
      aria-label={label ?? "Run progress"}
      aria-valuenow={indeterminate ? undefined : percent}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <span className="progress-fill" style={indeterminate ? undefined : { width: `${percent}%` }} />
    </div>
  );
}
