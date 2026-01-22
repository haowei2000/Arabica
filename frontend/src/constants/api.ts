// API 基础配置
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

// API 端点常量
export const API_ENDPOINTS = {
  // 认证
  AUTH: {
    LOGIN: '/auth/login',
    REGISTER: '/auth/register',
    REFRESH: '/auth/refresh',
    ME: '/auth/me',
  },

  // 流式对话
  CHAT: {
    // Split endpoints (recommended)
    START: (appId: string) => `/chat/app/${appId}/stream`,
    MESSAGES: (taskId: string) => `/chat/task/${taskId}/stream`,
    CANCEL: (taskId: string) => `/chat/task/${taskId}/cancel`,
  },

  // 对话管理
  CONVERSATIONS: {
    LIST: '/conversations/query',
    CREATE: '/conversations/create',
    GET: (id: string) => `/conversations/${id}/get`,
    UPDATE: (id: string) => `/conversations/${id}/update`,
    DELETE: (id: string) => `/conversations/${id}/delete`,
  },

  // 消息管理
  MESSAGES: {
    LIST: '/messages/query',
    CREATE: '/messages/create',
    GET: (id: string) => `/messages/${id}/get`,
    UPDATE: (id: string) => `/messages/${id}/update`,
  },

  // App 管理
  APPS: {
    TEMPLATES: '/apps/templates/list',
    LIST: '/apps/list',
    CREATE: '/apps/create',
    GET: (appId: string) => `/apps/${appId}/get`,
    UPDATE: (appId: string) => `/apps/${appId}/update`,
    DELETE: (appId: string) => `/apps/${appId}/delete`,
    QUERY: '/apps/query',
  },
} as const;
