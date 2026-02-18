import { ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';

interface AccordionItemProps {
  /** Compact row content (status dot, name, chips, etc.) */
  header: React.ReactNode;
  /** Full detail shown when expanded */
  detail: React.ReactNode;
  isOpen: boolean;
  onToggle: () => void;
  className?: string;
}

/**
 * Accordion row for drawer view — header always visible, detail slides open.
 * Uses CSS grid-template-rows transition (no JS height measurement needed).
 */
export function AccordionItem({
  header,
  detail,
  isOpen,
  onToggle,
  className,
}: AccordionItemProps) {
  return (
    <div className={cn('border-b border-border/50 last:border-0', isOpen && 'bg-muted/10', className)}>
      {/* Header */}
      <button
        type="button"
        onClick={onToggle}
        className={cn(
          'w-full flex items-center gap-3 px-4 py-3 text-left',
          'hover:bg-muted/40 transition-colors',
          isOpen && 'bg-muted/20 hover:bg-muted/30'
        )}
      >
        <ChevronRight
          className={cn(
            'size-3 text-muted-foreground/50 transition-transform duration-200 shrink-0',
            isOpen && 'rotate-90'
          )}
        />
        {header}
      </button>

      {/* Drawer — grid-rows trick for smooth height animation without JS measurement */}
      <div
        className={cn(
          'grid transition-[grid-template-rows] duration-200 ease-in-out',
          isOpen ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'
        )}
      >
        <div className="overflow-hidden">
          <div className="px-5 pb-4 pt-2 border-t border-border/30">
            {detail}
          </div>
        </div>
      </div>
    </div>
  );
}
