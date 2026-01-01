// 用户角色枚举
export type UserRole = 'user' | 'admin' | 'moderator' | 'premium';

// 用户信息
export interface User {
  id: string;
  username: string;
  email?: string;
  phone?: string;
  tenant_id: string;
  role: UserRole;
  is_active: boolean;
  is_superuser: boolean;
  created_at: string;
  updated_at: string;
}

// 登录请求
export interface LoginRequest {
  username: string;
  password: string;
}

// 注册请求
export interface RegisterRequest {
  username: string;
  password: string;
  email?: string;
  phone?: string;
}

// Token 响应
export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

// Token 刷新请求
export interface TokenRefreshRequest {
  refresh_token: string;
}
