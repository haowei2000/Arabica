import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { AlertCircle, CheckCircle2, Loader2 } from 'lucide-react';
import { authService } from '@/services/authService';
import { Button } from '@/components/ui/button';

type VerifyState = 'loading' | 'success' | 'error';

export default function VerifyEmailPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token');
  const [state, setState] = useState<VerifyState>(token ? 'loading' : 'error');
  const [message, setMessage] = useState(
    token ? 'Verifying your email...' : 'Verification link is missing a token.'
  );

  useEffect(() => {
    if (!token) {
      return;
    }

    let cancelled = false;
    const verificationToken = token;

    async function verify() {
      try {
        const result = await authService.verifyEmail(verificationToken);
        if (cancelled) return;
        setState(result.verified ? 'success' : 'error');
        setMessage(result.message);
      } catch (err) {
        if (cancelled) return;
        setState('error');
        setMessage(err instanceof Error ? err.message : 'Email verification failed');
      }
    }

    verify();

    return () => {
      cancelled = true;
    };
  }, [token]);

  const icon =
    state === 'loading' ? (
      <Loader2 className="size-10 animate-spin text-primary" />
    ) : state === 'success' ? (
      <CheckCircle2 className="size-10 text-emerald-500" />
    ) : (
      <AlertCircle className="size-10 text-destructive" />
    );

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-primary-50 via-secondary-50/60 to-navy-100 px-4 dark:from-navy-950 dark:via-navy-900 dark:to-secondary-950">
      <div className="w-full max-w-md rounded-2xl border border-white/50 bg-white/80 p-8 text-center shadow-2xl shadow-navy-900/5 backdrop-blur-xl dark:border-navy-700/50 dark:bg-navy-800/80 dark:shadow-black/30">
        <div className="mx-auto mb-5 flex size-16 items-center justify-center rounded-2xl bg-muted/60">
          {icon}
        </div>
        <h1 className="text-2xl font-bold text-foreground">
          {state === 'success' ? 'Email verified' : 'Email verification'}
        </h1>
        <p className="mt-3 text-sm text-muted-foreground">{message}</p>
        <Button asChild className="mt-6 w-full">
          <Link to="/login">Back to sign in</Link>
        </Button>
      </div>
    </div>
  );
}
