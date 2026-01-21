// 路由路径常量
export const ROUTES = {
  HOME: '/',
  LOGIN: '/login',
  CONVERSATIONS: '/conversations',
  CHAT: '/chat/:conversationId',
  APPS: '/apps',
} as const;

// 生成动态路由的辅助函数
export const generateChatRoute = (conversationId: string) =>
  `/chat/${conversationId}`;
