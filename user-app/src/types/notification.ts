export type NotificationSeverity = 'INFO' | 'SUCCESS' | 'WARNING' | 'ERROR' | 'CRITICAL' | string

export type NotificationType =
  | 'SIGNAL_QUALIFIED'
  | 'SIGNAL_INVALIDATED'
  | 'ORDER_SUBMITTED'
  | 'ORDER_FILLED'
  | 'ORDER_CANCELLED'
  | 'ORDER_REJECTED'
  | 'POSITION_OPENED'
  | 'POSITION_CLOSED'
  | 'STOP_LOSS_TRIGGERED'
  | 'TAKE_PROFIT_TRIGGERED'
  | 'ALGO_STATE_CHANGED'
  | 'KILL_SWITCH_ENGAGED'
  | 'KILL_SWITCH_RESET'
  | 'SYSTEM_WARNING'
  | string

export interface NotificationEventDto {
  id: string
  type: NotificationType
  title: string
  message: string
  referenceId: string | null
  severity: NotificationSeverity
  isRead: boolean
  createdAt: string
}
