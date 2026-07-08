import type { ReactNode } from "react";
import { AlertTriangle, Inbox, RotateCcw } from "lucide-react";

interface LoadingProps {
  label?: string;
  lines?: number;
  tall?: boolean;
}

/** Skeleton placeholder shown while a panel's data is loading. */
export function PanelLoading({ label = "Loading", lines = 3, tall = false }: LoadingProps) {
  return (
    <div className={`state-block loading${tall ? " tall" : ""}`} role="status" aria-live="polite">
      <span className="visually-hidden">{label}…</span>
      <div className="skeleton-lines" aria-hidden="true">
        {Array.from({ length: lines }).map((_, index) => (
          <span key={index} className="skeleton-line" style={{ width: `${90 - index * 14}%` }} />
        ))}
      </div>
    </div>
  );
}

interface ErrorProps {
  error?: unknown;
  label?: string;
  onRetry?: () => void;
  tall?: boolean;
}

/** Honest error surface with an optional retry action. */
export function PanelError({ error, label = "Could not load this data.", onRetry, tall = false }: ErrorProps) {
  const message = error instanceof Error ? error.message : typeof error === "string" ? error : undefined;
  return (
    <div className={`state-block error${tall ? " tall" : ""}`} role="alert">
      <AlertTriangle aria-hidden="true" />
      <div>
        <strong>{label}</strong>
        {message && <span className="state-detail">{message}</span>}
      </div>
      {onRetry && (
        <button type="button" className="secondary-action" onClick={onRetry}>
          <RotateCcw aria-hidden="true" />
          <span>Retry</span>
        </button>
      )}
    </div>
  );
}

interface EmptyProps {
  children: ReactNode;
  icon?: typeof Inbox;
  tall?: boolean;
}

/** Neutral empty state — distinct from loading and error. */
export function EmptyState({ children, icon: Icon = Inbox, tall = false }: EmptyProps) {
  return (
    <div className={`state-block empty${tall ? " tall" : ""}`}>
      <Icon aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}
