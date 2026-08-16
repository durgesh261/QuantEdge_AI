import React from 'react';
import { motion } from 'framer-motion';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { paperTradingApi } from '../../services/api';
import { useToastStore } from '../../store/useToastStore';
import { Layers, XCircle, RefreshCw } from 'lucide-react';

export const PositionsPage: React.FC = () => {
  const queryClient = useQueryClient();
  const { addToast } = useToastStore();

  const { data: positionsData, isLoading, refetch } = useQuery({
    queryKey: ['paperPositions'],
    queryFn: paperTradingApi.getPositions,
  });

  const closeMutation = useMutation({
    mutationFn: ({ id, exitPrice }: { id: string; exitPrice: number }) =>
      paperTradingApi.closePosition(id, exitPrice),
    onSuccess: (res) => {
      addToast('Position Closed', `Closed position ${res.data.id} for ${res.data.symbol}`, 'success');
      queryClient.invalidateQueries({ queryKey: ['paperPositions'] });
      queryClient.invalidateQueries({ queryKey: ['paperWallet'] });
    },
  });

  const positions = positionsData?.data || [];

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="space-y-4 max-w-[1600px] mx-auto pb-6 font-mono select-none"
    >
      {/* Header */}
      <div className="flex items-center justify-between bg-[#161D2A] border border-[#1E293B] p-4 rounded-xl shadow-sm">
        <div className="flex items-center space-x-3">
          <Layers className="w-6 h-6 text-[#00C896]" />
          <div>
            <h1 className="text-lg font-bold text-white uppercase">Open Positions Monitor</h1>
            <p className="text-xs text-[#94A3B8]">Real-time open positions, mark price tracking, and liquidation risk.</p>
          </div>
        </div>

        <button
          onClick={() => refetch()}
          className="px-3 py-1.5 bg-[#1E293B] hover:bg-[#28334A] text-white text-xs font-bold rounded-lg border border-[#334155] flex items-center space-x-1.5"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
          <span>REFRESH POSITIONS</span>
        </button>
      </div>

      {/* Positions Table */}
      <div className="bg-[#161D2A] border border-[#1E293B] rounded-xl p-4 space-y-3 shadow-sm">
        <div className="flex justify-between items-center border-b border-[#1E293B] pb-3 text-xs">
          <span className="font-bold text-white">Active Open Positions ({positions.length})</span>
        </div>

        {positions.length === 0 ? (
          <div className="text-center py-12 text-[#94A3B8] text-xs">
            No open positions found.
          </div>
        ) : (
          <div className="overflow-x-auto text-xs">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="text-[#94A3B8] border-b border-[#1E293B] text-[11px]">
                  <th className="py-2.5 px-3">Position ID</th>
                  <th className="py-2.5 px-3">Symbol</th>
                  <th className="py-2.5 px-3">Side</th>
                  <th className="py-2.5 px-3">Size</th>
                  <th className="py-2.5 px-3">Entry Price</th>
                  <th className="py-2.5 px-3">Mark Price</th>
                  <th className="py-2.5 px-3">Notional Value</th>
                  <th className="py-2.5 px-3">Margin</th>
                  <th className="py-2.5 px-3">Unrealized PnL</th>
                  <th className="py-2.5 px-3 text-right">Action</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((pos) => (
                  <tr key={pos.id} className="border-b border-[#1E293B]/50 hover:bg-[#0B0E14]">
                    <td className="py-3 px-3 text-white font-bold">{pos.id}</td>
                    <td className="py-3 px-3 text-[#3B82F6] font-bold">{pos.symbol}</td>
<td className={`py-3 px-3 ${(pos.side as string) === 'BUY' || (pos.side as string) === 'LONG' ? 'bg-[#00C896]/20' : 'bg-[#F6465D]/20'} font-bold ${(pos.side as string) === 'BUY' || (pos.side as string) === 'LONG' ? 'text-[#00C896]' : 'text-[#F6465D]'}`}>
                        {pos.side}
                    </td>
                    <td className="py-3 px-3 text-white">{pos.quantity}</td>
                    <td className="py-3 px-3 text-white">${pos.entryPrice.toFixed(2)}</td>
                    <td className="py-3 px-3 text-white">${pos.markPrice.toFixed(2)}</td>
                    <td className="py-3 px-3 text-[#94A3B8]">${pos.notionalValue.toFixed(2)}</td>
                    <td className="py-3 px-3 text-white">${pos.marginAllocated.toFixed(2)} ({pos.leverage}x)</td>
                    <td className={`py-3 px-3 font-bold ${pos.unrealizedPnL >= 0 ? 'text-[#00C896]' : 'text-[#F6465D]'}`}>
                      {pos.unrealizedPnL >= 0 ? '+' : ''}${pos.unrealizedPnL.toFixed(2)}
                    </td>
                    <td className="py-3 px-3 text-right">
                      <button
                        onClick={() => closeMutation.mutate({ id: pos.id, exitPrice: pos.markPrice })}
                        className="px-2.5 py-1 bg-[#F6465D]/20 hover:bg-[#F6465D]/30 text-[#F6465D] rounded text-[11px] font-bold inline-flex items-center space-x-1 ml-auto"
                      >
                        <XCircle className="w-3.5 h-3.5" />
                        <span>CLOSE</span>
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </motion.div>
  );
};
