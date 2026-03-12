import { Moon, Sun } from 'lucide-react';
import { useTheme } from '@/hooks/use-theme';
import { cn } from '@/lib/utils';

interface ThemeToggleProps {
  className?: string;
  /** Render a smaller icon-only button (for tight spaces like collapsed sidebar) */
  compact?: boolean;
}

export default function ThemeToggle({ className, compact }: ThemeToggleProps) {
  const { isDark, toggle } = useTheme();

  return (
    <button
      aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      className={cn(
        'relative inline-flex items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
        compact ? 'h-7 w-7' : 'h-9 w-9',
        className,
      )}
      onClick={toggle}
      type="button"
    >
      {isDark ? (
        <Sun className={compact ? 'h-3.5 w-3.5' : 'h-4 w-4'} />
      ) : (
        <Moon className={compact ? 'h-3.5 w-3.5' : 'h-4 w-4'} />
      )}
    </button>
  );
}
