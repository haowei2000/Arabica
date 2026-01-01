// API 基础配置
export const API_BASE_URL = 'http://localhost:8001/api';

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
    STREAM: (appId: string) => `/chat/${appId}`,
    DIRECT: (appId: string) => `/chat/${appId}/direct`,
  },

  // 对话管理
  CONVERSATIONS: {
    LIST: '/conversations/',
    CREATE: '/conversations/',
    GET: (id: string) => `/conversations/${id}`,
    UPDATE: (id: string) => `/conversations/${id}`,
    DELETE: (id: string) => `/conversations/${id}`,
    SEARCH: '/conversations/search/',
  },

  // 消息管理
  MESSAGES: {
    LIST: '/messages/',
    CREATE: '/messages/',
    GET: (id: string) => `/messages/${id}`,
    UPDATE: (id: string) => `/messages/${id}`,
    SEARCH: '/messages/search/',
  },

  // App 管理
  APPS: {
    TEMPLATES: '/apps/templates',
    LIST: '/apps/',
    CREATE: '/apps/',
    GET: (appCode: string) => `/apps/${appCode}`,
    UPDATE: (appCode: string) => `/apps/${appCode}`,
    DELETE: (appCode: string) => `/apps/${appCode}`,
    BY_TEMPLATE: (templateId: string) => `/apps/templates/${templateId}/apps`,
  },
} as const;
