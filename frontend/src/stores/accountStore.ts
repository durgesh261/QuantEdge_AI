import { create } from 'zustand'
import {
  accountService,
  AccountStatusResponse,
  AccountSummaryResponse,
  ConnectAccountRequest,
  PositionDetail,
  OrderDetail,
  BalanceDetail,
} from '@/services/accountService'

interface AccountState {
  status: AccountStatusResponse | null
  summary: AccountSummaryResponse | null
  isLoading: boolean
  isConnecting: boolean
  isSyncing: boolean
  error: string | null
  positions: PositionDetail[]
  openOrders: OrderDetail[]
  balances: BalanceDetail[]

  fetchStatus: (accountId?: string) => Promise<void>
  fetchSummary: (accountId?: string) => Promise<void>
  connectAccount: (data: ConnectAccountRequest) => Promise<boolean>
  verifyAccount: (accountId?: string) => Promise<boolean>
  disconnectAccount: (accountId?: string) => Promise<void>
  clearError: () => void
}

export const useAccountStore = create<AccountState>((set, get) => ({
  status: null,
  summary: null,
  isLoading: false,
  isConnecting: false,
  isSyncing: false,
  error: null,
  positions: [],
  openOrders: [],
  balances: [],

  fetchStatus: async (accountId?: string) => {
    try {
      set({ isLoading: true, error: null })
      const status = await accountService.getAccountStatus(accountId)
      set({ status, isLoading: false })
    } catch (err: any) {
      set({
        error: err.response?.data?.error || err.message || 'Failed to fetch account status',
        isLoading: false,
      })
    }
  },

  fetchSummary: async (accountId?: string) => {
    try {
      set({ isSyncing: true, error: null })
      const summary = await accountService.getAccountSummary(accountId)
      set({
        summary,
        positions: summary.positions || [],
        openOrders: summary.openOrders || [],
        balances: summary.balances || [],
        isSyncing: false,
      })
    } catch (err: any) {
      set({
        error: err.response?.data?.error || err.message || 'Failed to fetch account summary',
        isSyncing: false,
      })
    }
  },

  connectAccount: async (data: ConnectAccountRequest) => {
    try {
      set({ isConnecting: true, error: null })
      const res = await accountService.connectAccount(data)
      if (res.success) {
        await get().fetchSummary(res.accountId)
        await get().fetchStatus(res.accountId)
        set({ isConnecting: false })
        return true
      } else {
        set({
          error: res.error || 'Failed to connect Delta Exchange account',
          isConnecting: false,
        })
        return false
      }
    } catch (err: any) {
      set({
        error: err.response?.data?.error || err.message || 'Connection request failed',
        isConnecting: false,
      })
      return false
    }
  },

  verifyAccount: async (accountId?: string) => {
    try {
      set({ isSyncing: true, error: null })
      const summary = await accountService.verifyAccount(accountId)
      set({
        summary,
        positions: summary.positions || [],
        openOrders: summary.openOrders || [],
        balances: summary.balances || [],
        isSyncing: false,
      })
      await get().fetchStatus(accountId)
      return summary.success
    } catch (err: any) {
      set({
        error: err.response?.data?.error || err.message || 'Verification failed',
        isSyncing: false,
      })
      return false
    }
  },

  disconnectAccount: async (accountId?: string) => {
    try {
      set({ isLoading: true, error: null })
      const status = await accountService.disconnectAccount(accountId)
      set({
        status,
        summary: null,
        positions: [],
        openOrders: [],
        balances: [],
        isLoading: false,
      })
    } catch (err: any) {
      set({
        error: err.response?.data?.error || err.message || 'Failed to disconnect account',
        isLoading: false,
      })
    }
  },

  clearError: () => set({ error: null }),
}))
