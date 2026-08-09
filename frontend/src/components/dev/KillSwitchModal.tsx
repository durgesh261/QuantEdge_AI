import React, { useState } from 'react';
import { AlertTriangle, X, Trash2, Loader2 } from 'lucide-react';
import { apiClient as api } from '../../services/api';
import { useDeltaStore } from '../../store/useDeltaStore';
import { useTerminalStore } from '../../store/useTerminalStore';

interface Props {
  isOpen: boolean;
  onClose: () => void;
}

export const KillSwitchModal: React.FC<Props> = ({ isOpen, onClose }) => {
  const [confirmText, setConfirmText] = useState('');
  const [isWiping, setIsWiping] = useState(false);
  const [result, setResult] = useState<{ success: boolean; message: string } | null>(null);

  const reset = useDeltaStore((s) => s.reset);
  // Optional chaining in case setDevMode doesn't exist, though it should.
  const setDevMode = useTerminalStore((s) => s.setDevMode || (() => {}));

  if (!isOpen) return null;

  const handleWipe = async () => {
    if (confirmText !== 'DELETE EVERYTHING') return;

    setIsWiping(true);
    try {
      const res = await api.post('/admin/kill-switch', {}, {
        headers: { 'X-Dev-Mode': 'true' },
      });

      if (res.data?.success) {
        setResult({ success: true, message: res.data.message });
        reset(); // Clear frontend stores
        setTimeout(() => {
          window.location.reload(); // Full reset
        }, 2000);
      } else {
        setResult({ success: false, message: res.data?.error || 'Failed' });
      }
    } catch (err: any) {
      setResult({ 
        success: false, 
        message: err.response?.data?.error || 'Network error. Backend may be offline.' 
      });
    } finally {
      setIsWiping(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[200] bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="w-full max-w-md bg-[#161D2A] border border-[#F6465D]/40 rounded-2xl shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="bg-[#F6465D]/10 border-b border-[#F6465D]/30 px-5 py-4 flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <AlertTriangle className="w-5 h-5 text-[#F6465D]" />
            <span className="text-sm font-bold text-[#F6465D] uppercase tracking-wider">Kill Switch</span>
          </div>
          <button onClick={onClose} className="text-[#64748B] hover:text-white transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          {!result ? (
            <>
              <div className="bg-[#0B0E14] border border-[#F6465D]/30 rounded-xl p-4 space-y-2">
                <p className="text-[11px] text-[#F8FAFC] font-bold leading-relaxed">
                  This will permanently delete ALL data from the database:
                </p>
                <ul className="text-[10px] text-[#94A3B8] space-y-1 list-disc list-inside">
                  <li>All positions & order history</li>
                  <li>Trade journal & execution logs</li>
                  <li>Strategy profiles & scanner logs</li>
                  <li>News articles & macro events</li>
                  <li>Analytics & ledger entries</li>
                </ul>
                <p className="text-[10px] text-[#F6465D] font-bold mt-2">
                  This action is irreversible. User accounts will be kept.
                </p>
              </div>

              <div className="space-y-1.5">
                <label className="text-[10px] text-[#64748B] uppercase font-bold">
                  Type <span className="text-[#F6465D]">DELETE EVERYTHING</span> to confirm
                </label>
                <input
                  type="text"
                  value={confirmText}
                  onChange={(e) => setConfirmText(e.target.value)}
                  placeholder="DELETE EVERYTHING"
                  className="w-full bg-[#0B0E14] border border-[#334155] focus:border-[#F6465D] rounded-lg px-3 py-2.5 text-[11px] text-[#F8FAFC] outline-none uppercase tracking-wider"
                />
              </div>

              <button
                onClick={handleWipe}
                disabled={confirmText !== 'DELETE EVERYTHING' || isWiping}
                className="w-full py-3 bg-[#F6465D] hover:bg-[#E03A4F] disabled:opacity-30 disabled:cursor-not-allowed text-white rounded-xl font-bold text-[11px] uppercase tracking-wider transition-all flex items-center justify-center space-x-2"
              >
                {isWiping ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span>Wiping Database...</span>
                  </>
                ) : (
                  <>
                    <Trash2 className="w-4 h-4" />
                    <span>Activate Kill Switch</span>
                  </>
                )}
              </button>
            </>
          ) : (
            <div className="text-center py-4 space-y-3">
              <div className={`w-12 h-12 rounded-full mx-auto flex items-center justify-center ${result.success ? 'bg-[#00C896]/20' : 'bg-[#F6465D]/20'}`}>
                {result.success ? (
                  <Trash2 className="w-6 h-6 text-[#00C896]" />
                ) : (
                  <AlertTriangle className="w-6 h-6 text-[#F6465D]" />
                )}
              </div>
              <p className={`text-[11px] font-bold ${result.success ? 'text-[#00C896]' : 'text-[#F6465D]'}`}>
                {result.success ? 'Kill Switch Activated' : 'Activation Failed'}
              </p>
              <p className="text-[10px] text-[#94A3B8]">{result.message}</p>
              {result.success && (
                <p className="text-[9px] text-[#64748B]">Reloading app...</p>
              )}
              {!result.success && (
                <button
                  onClick={() => setResult(null)}
                  className="px-4 py-2 bg-[#1E293B] hover:bg-[#334155] rounded-lg text-[10px] font-bold text-[#F8FAFC] transition-colors"
                >
                  Try Again
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
