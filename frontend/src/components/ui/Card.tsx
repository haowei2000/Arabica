import { ReactNode } from 'react';
import { clsx } from 'clsx';

interface CardProps {
  children: ReactNode;
  className?: string;
  hover?: boolean;
  glass?: boolean;
  onClick?: () => void;
}

export function Card({ children, className, hover = false, glass = false, onClick }: CardProps) {
  return (
    <div
      onClick={onClick}
      className={clsx(
        'rounded-xl border transition-all duration-300',
        glass
          ? 'glass'
          : 'bg-white dark:bg-navy-800/90 border-secondary-100 dark:border-navy-700/60 shadow-sm',
        hover && 'hover:shadow-xl hover:scale-[1.01] hover:border-primary-200 dark:hover:border-primary-800/50 cursor-pointer',
        onClick && 'active:scale-[0.99]',
        'animate-fade-in',
        className
      )}
    >
      {children}
    </div>
  );
}

export function CardHeader({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={clsx('px-6 py-4 border-b border-secondary-100 dark:border-navy-700/60', className)}>
      {children}
    </div>
  );
}

export function CardBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={clsx('px-6 py-4', className)}>{children}</div>;
}

export function CardFooter({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={clsx('px-6 py-4 border-t border-secondary-100 dark:border-navy-700/60', className)}>
      {children}
    </div>
  );
}
