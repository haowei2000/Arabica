import { apiClient } from './api';
import { API_ENDPOINTS, API_BASE_URL } from '@/constants/api';
import type {
  EmailRegisterRequest,
  EmailVerificationResponse,
  RegisterRequest,
  TokenResponse,
  User,
} from '@/types/auth';

type ApiErrorResponse = {
  response?: {
    data?: {
      detail?: string;
    };
  };
  message?: string;
};

function getApiErrorMessage(error: unknown, fallback: string): string {
  const apiError = error as ApiErrorResponse;
  if (apiError.response?.data?.detail) {
    return apiError.response.data.detail;
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}

export const authService = {
  /**
   * 登录 - 使用 OAuth2 表单格式
   */
  async login(username: string, password: string): Promise<TokenResponse> {
    const formData = new URLSearchParams();
    formData.append('username', username);
    formData.append('password', password);

    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.AUTH.LOGIN}`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: formData,
      }
    );

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || 'Login failed');
    }

    const data: TokenResponse = await response.json();

    // 保存 token 到 localStorage
    localStorage.setItem('access_token', data.access_token);
    localStorage.setItem('refresh_token', data.refresh_token);

    return data;
  },

  /**
   * 注册用户
   */
  async register(data: RegisterRequest): Promise<User> {
    return apiClient.post(API_ENDPOINTS.AUTH.REGISTER, data);
  },

  /**
   * Register a user with email and password.
   */
  async registerWithEmail(data: EmailRegisterRequest): Promise<User> {
    try {
      return await apiClient.post(API_ENDPOINTS.AUTH.REGISTER_EMAIL, data);
    } catch (error) {
      throw new Error(getApiErrorMessage(error, 'Registration failed'));
    }
  },

  async verifyEmail(token: string): Promise<EmailVerificationResponse> {
    try {
      return await apiClient.get(
        `${API_ENDPOINTS.AUTH.VERIFY_EMAIL}?token=${encodeURIComponent(token)}`
      );
    } catch (error) {
      throw new Error(getApiErrorMessage(error, 'Email verification failed'));
    }
  },

  async resendVerificationEmail(email: string): Promise<EmailVerificationResponse> {
    try {
      return await apiClient.post(API_ENDPOINTS.AUTH.RESEND_VERIFY_EMAIL, { email });
    } catch (error) {
      throw new Error(getApiErrorMessage(error, 'Failed to resend verification email'));
    }
  },

  /**
   * 登出
   */
  logout(): void {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
  },

  /**
   * 获取当前用户信息
   */
  async getCurrentUser(): Promise<User> {
    return apiClient.get(API_ENDPOINTS.AUTH.ME);
  },

  /**
   * 刷新 token
   */
  async refreshToken(): Promise<TokenResponse> {
    const refreshToken = localStorage.getItem('refresh_token');
    if (!refreshToken) {
      throw new Error('No refresh token available');
    }

    const data: TokenResponse = await apiClient.post(
      API_ENDPOINTS.AUTH.REFRESH,
      {
        refresh_token: refreshToken,
      }
    );

    // 更新 token
    localStorage.setItem('access_token', data.access_token);
    localStorage.setItem('refresh_token', data.refresh_token);

    return data;
  },

  /**
   * 检查是否已登录
   */
  isAuthenticated(): boolean {
    return !!localStorage.getItem('access_token');
  },
};
