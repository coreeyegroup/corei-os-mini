/* A failing screen shows what went wrong and a way back; the rest of the workstation
 * (rail, status, other groups) keeps working. */
import { Component, type ReactNode } from 'react';

export default class ErrorBoundary extends Component<{ name: string; children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error) { console.error(`[COREI] ${this.props.name} failed:`, error); }
  render() {
    if (!this.state.error) return this.props.children;
    return <div className="content"><div role="alert" className="notice danger">
      <strong>{this.props.name} could not be shown.</strong>
      <p>{this.state.error.message}</p>
      <button onClick={() => this.setState({ error: null })}>Try again</button> <button onClick={() => window.location.reload()}>Reload the page</button>
    </div></div>;
  }
}
