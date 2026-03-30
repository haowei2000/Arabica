import axios, { AxiosError } from 'axios';
import type { AxiosRequestConfig } from 'axios';
import { API_BASE_URL } from '@/constants/api';

// 创建 Axios 实例
const instance = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// 请求拦截器 - 添加 token
instance.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('access_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error: AxiosError) => {
    return Promise.reject(error);
  }
);

// 响应拦截器 - 处理错误和 token 过期
instance.interceptors.response.use(
  (response) => {
    // 直接返回 data 字段
    return response.data;
  },
  async (error: AxiosError) => {
    if (error.response?.status === 401) {
      // Token 过期，清除并跳转登录
      localStorage.removeItem('access_token');
      localStorage.removeItem('refresh_token');

      // 避免在登录页再次跳转
      if (window.location.pathname !== '/login') {
        window.location.href = '/login';
      }
    }

    return Promise.reject(error);
  }
);

/**
 * Custom type to handle the fact that our interceptor returns response.data
 * and we want to keep generic support in services.
 */
interface ApiClient {
  get<T = any>(url: string, config?: AxiosRequestConfig): Promise<T>;
  post<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T>;
  put<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T>;
  patch<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T>;
  delete<T = any>(url: string, config?: AxiosRequestConfig): Promise<T>;
}

export const apiClient = instance as unknown as ApiClient;
