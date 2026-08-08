import React, { Component, ReactNode } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';

interface Props { children: ReactNode; fallback?: ReactNode; }
interface State { hasError: boolean; error: Error | null; }

export class ErrorBoundary extends Component<Props, State> {
  override state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  override componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary]', error, info);
  }

  override render() {
    if (this.state.hasError) {
      return this.props.fallback || (
        <div className="h-full w-full bg-[#0B0E14] flex items-center justify-center p-6">
          <div className="max-w-md w-full text-center">
            <div className="w-12 h-12 rounded-full bg-[#F6465D]/10 border border-[#F6465D]/30 flex items-center justify-center mx-auto mb-4">
              <AlertTriangle className="w-6 h-6 text-[#F6465D]" />
            </div>
            <h2 className="text-sm font-bold text-[#F8FAFC] mb-2">Something went wrong</h2>
            <p className="text-[10px] text-[#94A3B8] mb-4 font-mono bg-[#161D2A] p-2 rounded border border-[#1E293B] break-all">
              {this.state.error?.message}
            </p>
            <button
              onClick={() => this.setState({ hasError: false, error: null })}
              className="px-4 py-2 bg-[#3B82F6] hover:bg-[#2563EB] text-white rounded-lg text-[11px] font-bold flex items-center justify-center space-x-2 mx-auto"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              <span>Try Again</span>
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
