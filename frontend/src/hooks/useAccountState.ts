import { useEffect, useState } from 'react';
import { apiClient } from '../services/api';

export interface AccountState {
  balance: number;
  equity: number;
  usedMargin: number;
  availableMargin: number;
  unrealizedPnl: number;
  realizedPnl: number;
  todayPnl: number;
  openPositions: number;
  openOrders: number;
  balances: { asset: string; balance: number; available: number; unrealizedPnl: number }[];
}

export function useAccountState() {
  const [account, setAccount] = useState<AccountState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchAccount = async () => {
    try {
      const res = await apiClient.get('/portfolio/account-state');
      if (res.data?.success) {
        setAccount(res.data.data as AccountState);
        setError(null);
      }
    } catch (err: any) {
      console.error('[useAccountState] Failed:', err);
      setError(err?.message ?? 'Failed to fetch account state');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAccount();
    const interval = setInterval(fetchAccount, 5000);
    return () => clearInterval(interval);
  }, []);

  return { account, loading, error, refresh: fetchAccount };
}
