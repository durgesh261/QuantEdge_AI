export interface AlgoConfigResponse {
  success: boolean
  accountId: string | null
  version: number
  takeProfitPercent: number
  stopLossPercent: number
  riskPerTradePercent: number
  maxDailyLossPercent: number
  maxLeverage: number
  algoEnabled: boolean
  killSwitchActive: boolean
  message: string
  updatedAt: string
}

export interface UpdateAlgoConfigRequest {
  accountId?: string | null
  takeProfitPercent?: number
  stopLossPercent?: number
  riskPerTradePercent?: number
  maxDailyLossPercent?: number
  maxLeverage?: number
  algoEnabled?: boolean
  killSwitchActive?: boolean
}
