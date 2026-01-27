import axios, { AxiosError } from 'axios';
import { API_BASE_URL } from '@/constants/api';

// 创建 Axios 实例
export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// 请求拦截器 - 添加 token
apiClient.interceptors.request.use(
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
apiClient.interceptors.response.use(
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
