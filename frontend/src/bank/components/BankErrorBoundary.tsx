// A render-error boundary for the bank pages. Without one, a single throw in any
// page (e.g. formatting a value the API typed as a number but sent as a string)
// unmounts the whole React tree and the entire window goes blank — which in a
// demo reads as a dead product, not one screen with a bug. This keeps the shell
// (sidebar, top bar) and shows the failure in place. BankLayout keys it on the
// location, so navigating to another screen clears it.
import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class BankErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Surfaced to the console for whoever is at the keyboard; no telemetry seam
    // on the bank side yet, so this is the only record.
    console.error("Bank page crashed:", error, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div role="alert" className="mx-auto max-w-lg rounded-card border border-border bg-card p-6 text-center">
          <h2 className="text-[15px] font-semibold text-foreground">This screen couldn’t be displayed</h2>
          <p className="mt-2 text-[12.5px] text-muted-foreground">
            Something went wrong rendering this page. Switching to another screen, or reloading, usually clears it.
            If it keeps happening, this is a bug worth reporting.
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="tap-target mt-4 inline-flex items-center rounded-md border border-border bg-background px-4 py-2 text-[12.5px] font-medium text-foreground hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Reload
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
