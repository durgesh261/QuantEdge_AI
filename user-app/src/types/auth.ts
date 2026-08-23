export type UserRole = 'ROLE_USER' | 'ROLE_DEVELOPER' | 'ROLE_ADMIN'

export interface User {
  id: string
  email: string
  name: string
  role: UserRole
  isActive: boolean
  emailVerified?: boolean
  lastLoginAt?: string
}

export interface AuthResponse {
  user: User
  accessToken?: string | null
}
