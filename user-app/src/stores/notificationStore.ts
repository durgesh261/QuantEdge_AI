import { create } from 'zustand'
import { NotificationEventDto } from '../types/notification'
import { notificationService } from '../services/notificationService'

interface NotificationState {
  notifications: NotificationEventDto[]
  unreadCount: number
  isLoading: boolean
  error: string | null
  fetchNotifications: (unreadOnly?: boolean, limit?: number) => Promise<void>
  markAsRead: (id: string) => Promise<void>
  markAllAsRead: () => Promise<void>
}

export const useNotificationStore = create<NotificationState>((set, get) => ({
  notifications: [],
  unreadCount: 0,
  isLoading: false,
  error: null,

  fetchNotifications: async (unreadOnly = false, limit = 50) => {
    try {
      set({ isLoading: true, error: null })
      const data = await notificationService.getNotifications(unreadOnly, limit)
      const unread = data.filter((n) => !n.isRead).length
      set({ notifications: data, unreadCount: unread, isLoading: false })
    } catch (err: any) {
      console.warn('Failed to fetch notifications', err)
      set({ error: err.response?.data?.message || 'Error loading notifications', isLoading: false })
    }
  },

  markAsRead: async (id: string) => {
    try {
      await notificationService.markAsRead(id)
      const current = get().notifications.map((n) => (n.id === id ? { ...n, isRead: true } : n))
      const unread = current.filter((n) => !n.isRead).length
      set({ notifications: current, unreadCount: unread })
    } catch (err) {
      console.warn('Failed to mark notification as read', err)
    }
  },

  markAllAsRead: async () => {
    try {
      await notificationService.markAllAsRead()
      const current = get().notifications.map((n) => ({ ...n, isRead: true }))
      set({ notifications: current, unreadCount: 0 })
    } catch (err) {
      console.warn('Failed to mark all notifications as read', err)
    }
  },
}))
