import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {Moon, Sun} from 'lucide-react';
import { authService } from '@/services/authService';
import { useAuthStore } from '@/stores/useAuthStore';
import {useUIStore} from '@/stores/useUIStore';

export default function LoginPage() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const navigate = useNavigate();
  const { setUser } = useAuthStore();
    const {toggleTheme, theme} = useUIStore();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      await authService.login(username, password);
      const user = await authService.getCurrentUser();
      setUser(user);
      navigate('/chat');
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败');
    } finally {
      setLoading(false);
    }
  };

  return (
      <div className="min-h-screen flex items-center justify-center bg-navy-100 dark:bg-navy-950 relative">
          {/* Theme Toggle */}
          <button
              onClick={toggleTheme}
              className="absolute top-4 right-4 p-2 text-secondary-600 dark:text-secondary-400
                   hover:text-navy-900 dark:hover:text-navy-100 rounded-lg
                   hover:bg-navy-200 dark:hover:bg-navy-800 transition-colors"
              title={theme === 'dark' ? '切换到亮色模式' : '切换到暗色模式'}
          >
              {theme === 'dark' ? <Sun className="w-5 h-5"/> : <Moon className="w-5 h-5"/>}
          </button>

          <div
              className="max-w-md w-full bg-white dark:bg-navy-900 rounded-xl shadow-xl border border-secondary-200 dark:border-navy-700 p-8">
              <h2 className="text-2xl font-bold text-center mb-6 text-navy-900 dark:text-navy-50">
          对话 Agent 管理平台
        </h2>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label
              htmlFor="username"
              className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1"
            >
              用户名
            </label>
            <input
              id="username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-lg
                         bg-white dark:bg-navy-800 text-navy-900 dark:text-navy-100
                         focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent
                         placeholder:text-secondary-400"
              placeholder="请输入用户名"
              required
            />
          </div>

          <div>
            <label
              htmlFor="password"
              className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1"
            >
              密码
            </label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-lg
                         bg-white dark:bg-navy-800 text-navy-900 dark:text-navy-100
                         focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent
                         placeholder:text-secondary-400"
              placeholder="请输入密码"
              required
            />
          </div>

          {error && (
              <div
                  className="bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 text-sm p-3 rounded-lg border border-red-200 dark:border-red-800">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-primary-500 hover:bg-primary-600 active:bg-primary-700
                       text-white font-medium py-2.5 px-4 rounded-lg
                       focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-2
                       dark:focus:ring-offset-navy-900
                       disabled:opacity-50 disabled:cursor-not-allowed
                       transition-colors duration-200"
          >
            {loading ? '登录中...' : '登录'}
          </button>
        </form>

              <div className="mt-6 text-center text-sm text-secondary-500 dark:text-secondary-400">
                  Powered by Aiwen
              </div>
      </div>
    </div>
  );
}
