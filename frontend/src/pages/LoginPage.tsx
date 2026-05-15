import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Moon, Sun, Eye, EyeOff } from 'lucide-react';
import { authService } from '@/services/authService';
import { useAuthStore } from '@/stores/useAuthStore';
import { useUIStore } from '@/stores/useUIStore';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';

type AuthMode = 'login' | 'register';

export default function LoginPage() {
  const [mode, setMode] = useState<AuthMode>('login');
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [loading, setLoading] = useState(false);
  const [resending, setResending] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  const navigate = useNavigate();
  const { setUser } = useAuthStore();
  const { toggleTheme, theme } = useUIStore();

  const handleModeChange = (value: string) => {
    setMode(value as AuthMode);
    setError('');
    setSuccess('');
    setPassword('');
    setConfirmPassword('');
    setShowPassword(false);
  };

  const finishAuthenticatedFlow = async (loginName: string) => {
    await authService.login(loginName, password);
    const user = await authService.getCurrentUser();
    setUser(user);
    navigate('/app');
  };

  const handleLogin = async () => {
    await finishAuthenticatedFlow(username);
  };

  const handleRegister = async () => {
    if (password !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }

    await authService.registerWithEmail({ email, password });
    setSuccess('Check your email for a verification link before signing in.');
    setPassword('');
    setConfirmPassword('');
  };

  const handleResendVerification = async () => {
    setError('');
    setSuccess('');
    setResending(true);

    try {
      const result = await authService.resendVerificationEmail(email);
      setSuccess(result.message);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to resend verification email');
    } finally {
      setResending(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');

    try {
      if (mode === 'register') {
        await handleRegister();
      } else {
        await handleLogin();
      }
    } catch (err) {
      const fallback = mode === 'register' ? 'Registration failed' : 'Login failed';
      setError(err instanceof Error ? err.message : fallback);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center relative overflow-hidden bg-gradient-to-br from-primary-50 via-secondary-50/60 to-navy-100 dark:from-navy-950 dark:via-navy-900 dark:to-secondary-950">
      {/* Background decoration */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-40 -right-40 w-96 h-96 rounded-full bg-primary-400/10 dark:bg-primary-500/5 blur-3xl" />
        <div className="absolute -bottom-40 -left-40 w-96 h-96 rounded-full bg-secondary-400/10 dark:bg-secondary-500/5 blur-3xl" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] rounded-full bg-primary-300/5 dark:bg-primary-600/5 blur-3xl" />
      </div>

      {/* Theme toggle */}
      <Button
        type="button"
        variant="ghost"
        size="icon"
        onClick={toggleTheme}
        className="absolute top-4 right-4 z-10"
        title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
      >
        {theme === 'dark' ? <Sun className="size-5" /> : <Moon className="size-5" />}
      </Button>

      {/* Login card */}
      <div className="relative z-10 max-w-md w-full mx-4 animate-fade-in">
        <div className="bg-white/80 dark:bg-navy-800/80 backdrop-blur-xl rounded-2xl shadow-2xl shadow-navy-900/5 dark:shadow-black/30 border border-white/50 dark:border-navy-700/50 p-8">
          {/* Logo and title */}
          <div className="flex flex-col items-center mb-8">
            <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-primary-500 to-primary-600 flex items-center justify-center shadow-lg shadow-primary-500/30 mb-4">
              <img src="/icon.png" alt="Structure" className="size-9 object-contain" />
            </div>
            <h1 className="text-2xl font-bold text-foreground">Structure</h1>
            <p className="text-sm text-muted-foreground mt-1">
              {mode === 'register' ? 'Create your account' : 'Sign in to your account'}
            </p>
          </div>

          <Tabs value={mode} onValueChange={handleModeChange} className="mb-6">
            <TabsList className="grid w-full grid-cols-2 bg-muted/50">
              <TabsTrigger value="login" className="rounded-md">
                Sign In
              </TabsTrigger>
              <TabsTrigger value="register" className="rounded-md">
                Register
              </TabsTrigger>
            </TabsList>
          </Tabs>

          <form onSubmit={handleSubmit} className="space-y-5">
            {mode === 'login' ? (
              <div className="space-y-2">
                <Label htmlFor="username">Username or email</Label>
                <Input
                  id="username"
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="Enter username or email"
                  className="h-11 bg-white/60 dark:bg-navy-900/40"
                  autoComplete="username"
                  required
                />
              </div>
            ) : (
              <div className="space-y-2">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="Enter your email"
                  className="h-11 bg-white/60 dark:bg-navy-900/40"
                  autoComplete="email"
                  required
                />
              </div>
            )}

            <div className="space-y-2">
              <Label htmlFor={mode === 'register' ? 'register-password' : 'password'}>
                Password
              </Label>
              <div className="relative">
                <Input
                  id={mode === 'register' ? 'register-password' : 'password'}
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Enter your password"
                  className="h-11 pr-10 bg-white/60 dark:bg-navy-900/40"
                  autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
                  minLength={mode === 'register' ? 8 : undefined}
                  required
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                  tabIndex={-1}
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                >
                  {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                </button>
              </div>
            </div>

            {mode === 'register' && (
              <div className="space-y-2">
                <Label htmlFor="confirm-password">Confirm password</Label>
                <Input
                  id="confirm-password"
                  type={showPassword ? 'text' : 'password'}
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder="Confirm your password"
                  className="h-11 bg-white/60 dark:bg-navy-900/40"
                  autoComplete="new-password"
                  minLength={8}
                  required
                />
              </div>
            )}

            {error && (
              <div className="bg-destructive/10 text-destructive text-sm p-3 rounded-lg border border-destructive/20 animate-fade-in">
                {error}
              </div>
            )}

            {success && (
              <div className="space-y-3 rounded-lg border border-emerald-500/20 bg-emerald-500/10 p-3 text-sm text-emerald-700 dark:text-emerald-300 animate-fade-in">
                <p>{success}</p>
                {mode === 'register' && email && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={resending}
                    onClick={handleResendVerification}
                    className="h-8 bg-white/50 dark:bg-navy-900/40"
                  >
                    {resending ? 'Resending...' : 'Resend verification email'}
                  </Button>
                )}
              </div>
            )}

            <Button
              type="submit"
              disabled={loading || resending}
              className="w-full h-11 text-base font-medium bg-gradient-to-r from-primary-500 to-primary-600 hover:from-primary-600 hover:to-primary-700 shadow-lg shadow-primary-500/20 transition-all duration-200"
            >
              {loading ? (
                <span className="flex items-center gap-2">
                  <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  {mode === 'register' ? 'Creating account...' : 'Signing in...'}
                </span>
              ) : (
                mode === 'register' ? 'Create Account' : 'Sign In'
              )}
            </Button>
          </form>

          <div className="mt-8 text-center text-xs text-muted-foreground/60">
            Powered by Structure
          </div>
        </div>
      </div>
    </div>
  );
}
