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


  // Workspaces & Runs
  WORKSPACES: {
      LIST: '/workspaces',
      CREATE: '/workspaces',
      GET: (workspaceId: string) => `/workspaces/${workspaceId}`,
      UPDATE: (workspaceId: string) => `/workspaces/${workspaceId}`,
      DELETE: (workspaceId: string) => `/workspaces/${workspaceId}`,
      RUNS: (workspaceId: string) => `/workspaces/${workspaceId}/runs`,
      RUN: (workspaceId: string, runId: string) =>
          `/workspaces/${workspaceId}/runs/${runId}`,
      RUN_CANCEL: (workspaceId: string, runId: string) =>
          `/workspaces/${workspaceId}/runs/${runId}/cancel`,
  },
  EVENTS: {
      RUN_STREAM: (runId: string) => `/runs/${runId}/events/stream`,
      RUN_STATE: (runId: string) => `/runs/${runId}/state`,
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

    // Knowledge 管理
    KNOWLEDGE: {
        LIST: '/agent/knowledge/query',
        CREATE: '/agent/knowledge/create',
        GET: (id: string) => `/agents/knowledge/${id}/get`,
        UPDATE: (id: string) => `/agents/knowledge/${id}/update`,
        DELETE: (id: string) => `/agents/knowledge/${id}/delete`,
        SEARCH: '/agent/knowledge/search',
    },

    // Document 管理
    DOCUMENT: {
        UPLOAD: '/agent/document/upload',
        GET: (id: string) => `/agents/document/${id}`,
        LIST_BY_KNOWLEDGE: (knowledgeId: string) => `/agents/document/knowledge/${knowledgeId}`,
        DELETE: (id: string) => `/agents/document/${id}/delete`,
        TASK_STATUS: (taskId: string) => `/agents/document/task/${taskId}/status`,
        DOWNLOAD: (id: string) => `/agents/document/${id}/download`,
        PREVIEW: (id: string) => `/agents/document/${id}/preview`,
    },

    // Context/Chunk 管理
    CONTEXT: {
        LIST_BY_KNOWLEDGE: (knowledgeId: string) => `/agents/context/knowledge/${knowledgeId}/chunks`,
        LIST_BY_DOCUMENT: (documentId: string) => `/agents/context/document/${documentId}/chunks`,
    },
} as const;
