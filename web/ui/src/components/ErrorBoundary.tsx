import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertOctagon, RotateCcw } from "lucide-react";

interface Props {
  children: ReactNode;
  /** Optional custom fallback; receives the error and a reset callback. */
  fallback?: (error: Error, reset: () => void) => ReactNode;
  title?: string;
  /** When this value changes, a caught error is cleared (e.g. on route change). */
  resetKey?: unknown;
}

interface State {
  error: Error | null;
}

/** Catches render-time throws so a single failing subtree never blanks the app. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidUpdate(prevProps: Props): void {
    // Recover automatically when the caller signals a context change (e.g. the
    // user navigated to a different route), so the fallback isn't pinned forever.
    if (this.state.error && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Surface for local debugging without crashing the shell.
    console.error("UI error boundary caught:", error, info.componentStack);
  }

  reset = () => this.setState({ error: null });

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.fallback) return this.props.fallback(error, this.reset);
    return (
      <div className="state-block error tall" role="alert">
        <AlertOctagon aria-hidden="true" />
        <div>
          <strong>{this.props.title ?? "Something went wrong rendering this view."}</strong>
          <span className="state-detail">{error.message}</span>
        </div>
        <button type="button" className="secondary-action" onClick={this.reset}>
          <RotateCcw aria-hidden="true" />
          <span>Try again</span>
        </button>
      </div>
    );
  }
}
