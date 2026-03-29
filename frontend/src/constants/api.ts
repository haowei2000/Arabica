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


  // Standalone runs (user-level)
  RUNS: {
    USER_LIST: '/runs',
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
      RUN_RESUME: (workspaceId: string, runId: string) =>
          `/workspaces/${workspaceId}/runs/${runId}/resume`,
      RUN_FEEDBACK: (workspaceId: string, runId: string) =>
          `/workspaces/${workspaceId}/runs/${runId}/feedback`,
      CONTEXTS: (workspaceId: string) => `/workspaces/${workspaceId}/contexts`,
      CONTEXTS_COPY: (workspaceId: string) => `/workspaces/${workspaceId}/contexts/copy`,
      CONTEXT_DELETE: (workspaceId: string, contextId: string) =>
          `/workspaces/${workspaceId}/contexts/${contextId}`,
      CONTEXT_REINIT: (workspaceId: string) => `/workspaces/${workspaceId}/context/reinit`,
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
      TEMPLATES: '/apps/executors/list',
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
        GET: (id: string) => `/agent/knowledge/${id}/get`,
        UPDATE: (id: string) => `/agent/knowledge/${id}/update`,
        DELETE: (id: string) => `/agent/knowledge/${id}/delete`,
        SEARCH: '/agent/knowledge/search',
    },

    // Document 管理
    DOCUMENT: {
        UPLOAD: '/agent/document/upload',
        UPLOAD_FOLDER: '/agent/document/upload-folder',
        GET: (id: string) => `/agent/document/${id}`,
        LIST_BY_KNOWLEDGE: (knowledgeId: string) => `/agent/document/knowledge/${knowledgeId}`,
        DELETE: (id: string) => `/agent/document/${id}/delete`,
        TASK_STATUS: (taskId: string) => `/agent/document/task/${taskId}/status`,
        DOWNLOAD: (id: string) => `/agent/document/${id}/download`,
        PREVIEW: (id: string) => `/agent/document/${id}/preview`,
    },

    // User Tools
    TOOLS: {
        LIST: '/tools/',
        GET: (id: string) => `/tools/${id}`,
        DELETE: (id: string) => `/tools/${id}`,
        TOGGLE: (id: string) => `/tools/${id}/toggle`,
        TEST: (id: string) => `/tools/${id}/test`,
        PROBE_MCP: '/tools/probe-mcp',
        IMPORT_FROM_MCP: '/tools/import-from-mcp',
    },

    // Tool Bundles
    TOOL_BUNDLES: {
        LIST: '/tool-bundles',
    },

    // Skills 管理
    SKILLS: {
        LIST: '/agent/skills',
        CREATE: '/agent/skills',
        GET: (id: string) => `/agent/skills/${id}`,
        UPDATE: (id: string) => `/agent/skills/${id}`,
        DELETE: (id: string) => `/agent/skills/${id}`,
        SEARCH: '/agent/skills/search/query',
        PROCESS: (id: string) => `/agent/skills/${id}/process`,
        UPLOAD_FOLDER: '/agent/skills/upload-folder',
        FILE_CONTENT: (id: string, filePath: string) => `/agent/skills/${id}/files/${filePath}`,
    },

    // ContextSchema/Chunk 管理
    CONTEXT: {
        LIST: '/agent/context/query',
        LIST_BY_KNOWLEDGE: (knowledgeId: string) => `/agent/context/knowledge/${knowledgeId}/chunks`,
        LIST_BY_DOCUMENT: (documentId: string) => `/agent/context/document/${documentId}/chunks`,
        MEMORIES: '/agent/context/memories',
        FOR_SKILL: (id: string) => `/agent/skills/${id}/context`,
        FOR_KNOWLEDGE: (id: string) => `/agent/knowledge/${id}/context`,
        FOR_DOCUMENT: (id: string) => `/agent/document/${id}/context`,
        FOR_TOOL: (id: string) => `/tools/${id}/context`,
    },

    // Triggers 管理
    TRIGGERS: {
        LIST: '/triggers',
        CREATE: '/triggers',
        GET: (id: string) => `/triggers/${id}`,
        UPDATE: (id: string) => `/triggers/${id}`,
        DELETE: (id: string) => `/triggers/${id}`,
    },

    // Tasks
    TASKS: {
        LIST: (workspaceId: string) => `/workspaces/${workspaceId}/tasks`,
    },

    // Artifacts
    ARTIFACTS: {
        LIST: (workspaceId: string) => `/workspaces/${workspaceId}/artifacts`,
        GET: (workspaceId: string, artifactId: string) => `/workspaces/${workspaceId}/artifacts/${artifactId}`,
        DOWNLOAD: (workspaceId: string, artifactId: string) => `/workspaces/${workspaceId}/artifacts/${artifactId}/download`,
    },

    // LLM Model Management
    LLM_CHAT_MODELS: {
        LIST: '/llm/chat-models',
        CREATE: '/llm/chat-models',
        GET: (id: string) => `/llm/chat-models/${id}`,
        UPDATE: (id: string) => `/llm/chat-models/${id}`,
        DELETE: (id: string) => `/llm/chat-models/${id}`,
        SEARCH: '/llm/chat-models/search',
    },
    LLM_EMBEDDING_MODELS: {
        LIST: '/llm/embedding-models',
        CREATE: '/llm/embedding-models',
        GET: (id: string) => `/llm/embedding-models/${id}`,
        UPDATE: (id: string) => `/llm/embedding-models/${id}`,
        DELETE: (id: string) => `/llm/embedding-models/${id}`,
        SEARCH: '/llm/embedding-models/search',
    },

    // Events 管理
    EVENTS: {
        GET: (id: string) => `/events/${id}`,
        LIST_BY_WORKSPACE: (workspaceId: string) => `/events/workspace/${workspaceId}/list`,
        LIST_BY_RUN: (runId: string) => `/events/run/${runId}/list`,
        LIST_BY_USER: '/events/user/list',
        RUN_STREAM: (runId: string) => `/runs/${runId}/events/stream`,
        RUN_EVENTS: (runId: string) => `/runs/${runId}/events`,
        RUN_STATE: (runId: string) => `/runs/${runId}/state`,
        WORKSPACE_STREAM: (workspaceId: string) => `/workspaces/${workspaceId}/events/stream`,
    },
} as const;
