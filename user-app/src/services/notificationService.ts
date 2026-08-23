import { apiClient } from './apiClient'
import { NotificationEventDto } from '../types/notification'

export const notificationService = {
  async getNotifications(unreadOnly = false, limit = 50): Promise<NotificationEventDto[]> {
    const { data } = await apiClient.get<NotificationEventDto[]>('/api/v1/notifications', {
      params: { unreadOnly, limit },
    })
    return data
  },

  async markAsRead(id: string): Promise<{ success: boolean; id: string }> {
    const { data } = await apiClient.post(`/api/v1/notifications/${id}/read`)
    return data
  },

  async markAllAsRead(): Promise<{ success: boolean; markedCount: number }> {
    const { data } = await apiClient.post('/api/v1/notifications/read-all')
    return data
  },
}
