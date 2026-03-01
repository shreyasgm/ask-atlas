import { AlertCircle } from 'lucide-react';
import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  static displayName = 'ErrorBoundary';
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console -- error boundary logging
    console.error('ErrorBoundary caught:', error, info.componentStack);
  }

  override render() {
    if (this.state.error) {
      return (
        <div className="flex flex-col items-center gap-3 px-6 py-12 text-center" role="alert">
          <AlertCircle className="h-8 w-8 text-destructive" />
          <p className="text-sm font-medium text-foreground">Something went wrong</p>
          <p className="max-w-md text-xs text-muted-foreground">{this.state.error.message}</p>
          <button
            className="mt-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            onClick={() => this.setState({ error: null })}
            type="button"
          >
            Try again
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
