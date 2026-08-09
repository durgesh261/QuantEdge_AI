import React from 'react';
import { useAccountState } from '../../hooks/useAccountState';
import { Shield, X, AlertTriangle, RefreshCw, Wifi, WifiOff } from 'lucide-react';

interface PreTradeModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: () => void;
  isConfirming?: boolean;
  tradeDetails: {
    symbol: string;
    side: 'LONG' | 'SHORT';
    decision: string;
    confidence: number;
    quantity: number;
    marginRequired: number;
    notional: number;
    riskPercent: number;
  };
}

function fmt(n: number, prefix = '$') {
  const sign = n < 0 ? '-' : '';
  return `${sign}${prefix}${Math.abs(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function pnlClass(n: number) {
  if (n > 0) return 'text-[#00C896]';
  if (n < 0) return 'text-[#F6465D]';
  return 'text-[#94A3B8]';
}

export function PreTradeRiskModal({
  isOpen, onClose, onConfirm, isConfirming, tradeDetails,
}: PreTradeModalProps) {
  const { account, loading, error, refresh } = useAccountState();

  if (!isOpen) return null;

  const equity           = account?.equity ?? 0;
  const availableMargin  = account?.availableMargin ?? 0;
  const usedMargin       = account?.usedMargin ?? 0;
  const unrealizedPnl    = account?.unrealizedPnl ?? 0;
  const realizedPnl      = account?.realizedPnl ?? 0;
  const todayPnl         = account?.todayPnl ?? 0;
  const openPositions    = account?.openPositions ?? 0;
  const openOrders       = account?.openOrders ?? 0;
  const balances         = account?.balances ?? [];
  const marginRequired   = tradeDetails.marginRequired;

  const insufficientMargin = !loading && availableMargin < marginRequired && marginRequired > 0;
  const highRisk           = tradeDetails.riskPercent > 2;
  const noData             = !loading && !error && equity === 0 && balances.length === 0;
  const isConnected        = !error && !loading;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="bg-[#0F172A] border border-[#1E293B] rounded-xl w-full max-w-md shadow-2xl">

        {/* ── Header ─────────────────────────────────────────── */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[#1E293B]">
          <div className="flex items-center space-x-2">
            <Shield className="w-4 h-4 text-[#00C896]" />
            <h3 className="text-[11px] font-bold text-[#F8FAFC] uppercase tracking-wider">
              Pre‑Trade Risk Confirmation
            </h3>
          </div>
          <div className="flex items-center space-x-2">
            {/* Connection indicator */}
            {loading ? (
              <RefreshCw className="w-3 h-3 text-[#64748B] animate-spin" />
            ) : error ? (
              <WifiOff className="w-3 h-3 text-[#F6465D]" title="Delta not connected" />
            ) : (
              <Wifi className="w-3 h-3 text-[#00C896]" title="Delta connected" />
            )}
            <button onClick={onClose} className="text-[#64748B] hover:text-[#F8FAFC] transition-colors">
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* ── Body ───────────────────────────────────────────── */}
        <div className="px-5 py-4 space-y-3">

          {/* Trade details */}
          <div className="bg-[#0B0E14] rounded-lg p-3 space-y-2">
            <p className="text-[9px] font-bold text-[#64748B] uppercase tracking-wider mb-1">Trade Parameters</p>
            <Row label="Pair / Side">
              <span className="font-mono font-bold text-[#F8FAFC]">
                {tradeDetails.symbol}&nbsp;
                <span className={tradeDetails.side === 'LONG' ? 'text-[#00C896]' : 'text-[#F6465D]'}>
                  {tradeDetails.side}
                </span>
              </span>
            </Row>
            <Row label="Decision State">
              <span className={`font-mono font-bold ${tradeDetails.decision === 'EXECUTE' ? 'text-[#00C896]' : 'text-[#F59E0B]'}`}>
                {tradeDetails.decision}
              </span>
            </Row>
            <Row label="Confidence">
              <span className="font-mono font-bold text-[#3B82F6]">{tradeDetails.confidence.toFixed(1)}%</span>
            </Row>
            <Row label="Notional">
              <span className="font-mono text-[#94A3B8]">{fmt(tradeDetails.notional)}</span>
            </Row>
            <Row label="Est. Risk">
              <span className={`font-mono font-bold ${tradeDetails.riskPercent > 2 ? 'text-[#F6465D]' : 'text-[#F59E0B]'}`}>
                {tradeDetails.riskPercent.toFixed(1)}%
              </span>
            </Row>
          </div>

          {/* Live Delta account state */}
          <div className="bg-[#0B0E14] rounded-lg p-3 space-y-2">
            <div className="flex items-center justify-between mb-1">
              <p className="text-[9px] font-bold text-[#64748B] uppercase tracking-wider">Delta Exchange Account</p>
              {!loading && (
                <button onClick={refresh} className="text-[#64748B] hover:text-[#F8FAFC] transition-colors">
                  <RefreshCw className="w-2.5 h-2.5" />
                </button>
              )}
            </div>

            {error ? (
              <p className="text-[9px] text-[#F6465D]">⚠ {error}</p>
            ) : noData ? (
              <p className="text-[9px] text-[#F59E0B]">⚠ No live data — check Delta API credentials in Settings</p>
            ) : (
              <>
                <Row label="Account Equity">
                  <Val loading={loading} value={fmt(equity)} />
                </Row>
                <Row label="Available Margin">
                  <span className={`font-mono font-bold ${insufficientMargin ? 'text-[#F6465D]' : 'text-[#00C896]'}`}>
                    {loading ? '…' : fmt(availableMargin)}
                  </span>
                </Row>
                <Row label="Margin Required">
                  <span className="font-mono text-[#F8FAFC]">{fmt(marginRequired)}</span>
                </Row>
                <Row label="Used Margin">
                  <Val loading={loading} value={fmt(usedMargin)} />
                </Row>
                <div className="border-t border-[#1E293B] pt-1 mt-1 space-y-1">
                  <Row label="Unrealized P&L">
                    <span className={`font-mono font-bold ${pnlClass(unrealizedPnl)}`}>
                      {loading ? '…' : fmt(unrealizedPnl)}
                    </span>
                  </Row>
                  <Row label="Realized P&L">
                    <span className={`font-mono font-bold ${pnlClass(realizedPnl)}`}>
                      {loading ? '…' : fmt(realizedPnl)}
                    </span>
                  </Row>
                  <Row label="Today's P&L">
                    <span className={`font-mono font-bold ${pnlClass(todayPnl)}`}>
                      {loading ? '…' : fmt(todayPnl)}
                    </span>
                  </Row>
                </div>
                <div className="border-t border-[#1E293B] pt-1 mt-1 space-y-1">
                  <Row label="Open Positions">
                    <Val loading={loading} value={String(openPositions)} noPrefix />
                  </Row>
                  <Row label="Open Orders">
                    <Val loading={loading} value={String(openOrders)} noPrefix />
                  </Row>
                </div>

                {/* Per-asset balances */}
                {balances.length > 0 && (
                  <div className="border-t border-[#1E293B] pt-2 mt-1 space-y-1">
                    <p className="text-[9px] font-bold text-[#64748B] uppercase tracking-wider mb-1">Wallet Balances</p>
                    {balances.map((b) => (
                      <div key={b.asset} className="flex justify-between text-[9px]">
                        <span className="text-[#64748B]">{b.asset}</span>
                        <span className="font-mono text-[#F8FAFC]">
                          {b.balance.toFixed(4)}&nbsp;
                          <span className="text-[#64748B]">(avail {b.available.toFixed(4)})</span>
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>

          {/* Warnings */}
          {insufficientMargin && (
            <Warning color="red">INSUFFICIENT MARGIN — Reduce size or deposit funds</Warning>
          )}
          {highRisk && !insufficientMargin && (
            <Warning color="amber">HIGH RISK — Position exceeds 2% account risk threshold</Warning>
          )}
          {noData && (
            <Warning color="amber">Delta API not connected — configure API keys in Settings</Warning>
          )}
        </div>

        {/* ── Footer ─────────────────────────────────────────── */}
        <div className="px-5 py-4 border-t border-[#1E293B]">
          <button
            onClick={onConfirm}
            disabled={insufficientMargin || loading || isConfirming}
            className={`w-full py-2.5 rounded-lg text-[10px] font-bold uppercase tracking-wider transition-all ${
              insufficientMargin || loading || isConfirming
                ? 'bg-[#1E293B] text-[#64748B] cursor-not-allowed'
                : 'bg-[#00C896] text-[#0B0E14] hover:bg-[#00C896]/90 active:scale-[0.98]'
            }`}
          >
            {isConfirming
              ? 'TRANSMITTING TO DELTA…'
              : insufficientMargin
              ? 'INSUFFICIENT MARGIN'
              : 'Confirm and Submit Order'}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── Tiny helpers ──────────────────────────────────────── */

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between text-[10px] items-center">
      <span className="text-[#64748B]">{label}:</span>
      <span>{children}</span>
    </div>
  );
}

function Val({ loading, value, noPrefix }: { loading: boolean; value: string; noPrefix?: boolean }) {
  return (
    <span className="font-mono font-bold text-[#F8FAFC]">
      {loading ? '…' : noPrefix ? value : value}
    </span>
  );
}

function Warning({ color, children }: { color: 'red' | 'amber'; children: React.ReactNode }) {
  const cls =
    color === 'red'
      ? 'bg-[#F6465D]/10 border-[#F6465D]/30 text-[#F6465D]'
      : 'bg-[#F59E0B]/10 border-[#F59E0B]/30 text-[#F59E0B]';
  return (
    <div className={`flex items-center space-x-2 border rounded-lg px-3 py-2 ${cls}`}>
      <AlertTriangle className="w-3 h-3 flex-shrink-0" />
      <span className="text-[9px] font-bold">{children}</span>
    </div>
  );
}
