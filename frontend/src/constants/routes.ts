// Route path constants
export const ROUTES = {
    ROOT: '/',
  LOGIN: '/login',
    HOME: '/home',
    CHAT: '/chat',
    TRIGGERS: '/triggers',
    KNOWLEDGE_DOCUMENTS: '/knowledge/:knowledgeId/documents',
    // Legacy routes (redirect to home)
  APPS: '/apps',
    CONVERSATIONS: '/conversations',
} as const;

// Helper function for dynamic routes
export const generateChatRoute = (conversationId: string) =>
  `/chat/${conversationId}`;

export const generateKnowledgeDocumentsRoute = (knowledgeId: string) =>
    `/knowledge/${knowledgeId}/documents`;

export const generateSkillFilesRoute = (skillId: string) =>
    `/skills/${skillId}/files`;
