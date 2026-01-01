import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import { useChatStore } from '@/stores/useChatStore';
import { useAppStore } from '@/stores/useAppStore';
import { useStreamingChat } from '@/hooks/useStreamingChat';
import { authService } from '@/services/authService';
import { MessageRole } from '@/types/message';

export default function ChatPage() {
  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  const { currentAppId, currentAppCode, clearCurrentApp } = useAppStore();
  const {
    messages,
    streamingMessage,
    isStreaming,
    reset,
  } = useChatStore();

  console.log('🎯 ChatPage render - currentAppId:', currentAppId, 'currentAppCode:', currentAppCode);

  const { sendMessage, stopStreaming } = useStreamingChat(currentAppId || '');

  useEffect(() => {
    console.log('🔍 ChatPage useEffect - checking auth and app');

    // 检查登录状态
    if (!authService.isAuthenticated()) {
      console.log('❌ Not authenticated, redirecting to /login');
      navigate('/login');
      return;
    }

    // 检查是否选择了 App
    if (!currentAppId) {
      console.log('❌ No app selected, redirecting to /apps');
      navigate('/apps');
    } else {
      console.log('✅ App selected:', currentAppId);
    }
  }, [navigate, currentAppId]);

  useEffect(() => {
    // 自动滚动到底部
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingMessage]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    console.log('📤 Form submitted');

    if (!input.trim() || isStreaming) {
      console.log('⚠️  Skipping send - empty input or already streaming');
      return;
    }

    if (!currentAppId) {
      console.error('❌ No app ID, cannot send message');
      return;
    }

    const message = input.trim();
    setInput('');
    console.log('📨 Sending message:', message);
    await sendMessage(message, '新对话');
  };

  const handleLogout = () => {
    authService.logout();
    reset();
    clearCurrentApp();
    navigate('/login');
  };

  const handleBackToApps = () => {
    reset();
    clearCurrentApp();
    navigate('/apps');
  };

  if (!currentAppId) {
    return null; // Will redirect to /apps
  }

  return (
    <div className="flex flex-col h-screen bg-gray-50 dark:bg-gray-900">
      {/* Header */}
      <header className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button
              onClick={handleBackToApps}
              className="text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100"
              title="返回 Apps"
            >
              <svg
                className="w-6 h-6"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M10 19l-7-7m0 0l7-7m-7 7h18"
                />
              </svg>
            </button>
            <div>
              <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-100">对话</h1>
              <p className="text-sm text-gray-500 dark:text-gray-400">App: {currentAppCode}</p>
            </div>
          </div>
          <button
            onClick={handleLogout}
            className="text-sm text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100"
          >
            退出登录
          </button>
        </div>
      </header>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {messages.length === 0 && !streamingMessage && (
          <div className="text-center text-gray-500 dark:text-gray-400 mt-20">
            <p className="text-lg">开始新的对话</p>
            <p className="text-sm mt-2">向 AI 发送消息开始对话</p>
          </div>
        )}

        {messages.map((message) => (
          <div
            key={message.id}
            className={`flex gap-3 ${
              message.role === MessageRole.USER
                ? 'justify-end'
                : 'justify-start'
            }`}
          >
            {message.role === MessageRole.ASSISTANT && (
              <div className="w-8 h-8 rounded-full bg-blue-500 dark:bg-blue-600 flex items-center justify-center text-white text-sm shrink-0">
                AI
              </div>
            )}

            <div
              className={`max-w-2xl rounded-lg px-4 py-3 ${
                message.role === MessageRole.USER
                  ? 'bg-blue-600 text-white'
                  : 'bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700'
              }`}
            >
              {message.role === MessageRole.ASSISTANT ? (
                <div className="prose prose-sm dark:prose-invert max-w-none">
                  <ReactMarkdown>{message.content}</ReactMarkdown>
                </div>
              ) : (
                <p className="text-sm">{message.content}</p>
              )}
            </div>

            {message.role === MessageRole.USER && (
              <div className="w-8 h-8 rounded-full bg-gray-600 dark:bg-gray-700 flex items-center justify-center text-white text-sm shrink-0">
                You
              </div>
            )}
          </div>
        ))}

        {/* Streaming Message */}
        {isStreaming && streamingMessage && (
          <div className="flex gap-3 justify-start">
            <div className="w-8 h-8 rounded-full bg-blue-500 dark:bg-blue-600 flex items-center justify-center text-white text-sm shrink-0">
              AI
            </div>
            <div className="max-w-2xl rounded-lg px-4 py-3 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700">
              <div className="prose prose-sm dark:prose-invert max-w-none">
                <ReactMarkdown>{streamingMessage}</ReactMarkdown>
              </div>
              <div className="flex items-center gap-1 mt-2 text-gray-400 dark:text-gray-500">
                <span className="w-2 h-2 bg-blue-500 dark:bg-blue-600 rounded-full animate-pulse"></span>
                <span className="text-xs">正在输入...</span>
              </div>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-6 py-4">
        <form onSubmit={handleSubmit} className="flex gap-3">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="输入消息..."
            disabled={isStreaming}
            className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100 dark:disabled:bg-gray-800"
          />
          {isStreaming ? (
            <button
              type="button"
              onClick={stopStreaming}
              className="px-6 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500"
            >
              停止
            </button>
          ) : (
            <button
              type="submit"
              disabled={!input.trim()}
              className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              发送
            </button>
          )}
        </form>
      </div>
    </div>
  );
}
