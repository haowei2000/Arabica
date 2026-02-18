import { LayoutGrid, List, PanelBottomOpen } from 'lucide-react';
import { cn } from '@/lib/utils';

export type ViewMode = 'card' | 'list' | 'drawer';

interface ViewToggleProps {
  mode: ViewMode;
  onToggle: (mode: ViewMode) => void;
  className?: string;
}

const MODES: { value: ViewMode; icon: React.ElementType; title: string }[] = [
  { value: 'card',   icon: LayoutGrid,     title: 'Card view'   },
  { value: 'list',   icon: List,           title: 'List view'   },
  { value: 'drawer', icon: PanelBottomOpen, title: 'Drawer view' },
];

export function ViewToggle({ mode, onToggle, className }: ViewToggleProps) {
  return (
    <div
      className={cn(
        'inline-flex items-center rounded-md border border-border bg-muted p-0.5 gap-0.5',
        className
      )}
      role="group"
      aria-label="View mode"
    >
      {MODES.map(({ value, icon: Icon, title }) => (
        <button
          key={value}
          type="button"
          onClick={() => onToggle(value)}
          aria-pressed={mode === value}
          title={title}
          className={cn(
            'size-6 flex items-center justify-center rounded transition-colors',
            mode === value
              ? 'bg-background text-foreground shadow-sm'
              : 'text-muted-foreground hover:text-foreground'
          )}
        >
          <Icon className="size-3.5" />
        </button>
      ))}
    </div>
  );
}
