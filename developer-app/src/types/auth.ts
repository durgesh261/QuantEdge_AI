export interface User {
  id: string
  name: string
  email: string
  role: 'DEVELOPER' | 'ADMIN' | 'USER' | string
  isActive: boolean
}

export interface AuthResponse {
  user: User
  accessToken?: string | null
}
