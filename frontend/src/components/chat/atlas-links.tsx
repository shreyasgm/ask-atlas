import { ExternalLink } from 'lucide-react';
import type { AtlasLink } from '@/types/chat';
import { cn } from '@/lib/utils';

interface AtlasLinksProps {
  links: Array<AtlasLink>;
}

const LINK_STYLES: Record<string, string> = {
  country_page: 'border-success/25 bg-success/10 text-success hover:bg-success/20',
  explore_page: 'border-primary/25 bg-primary/10 text-primary hover:bg-primary/20',
};

export default function AtlasLinks({ links }: AtlasLinksProps) {
  // Deduplicate by URL as a safety net — the backend should already dedupe,
  // but the frontend may accumulate links from multiple SSE events.
  const uniqueLinks = links.filter(
    (link, i, arr) => arr.findIndex((l) => l.url === link.url) === i,
  );

  if (uniqueLinks.length === 0) {
    return null;
  }

  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[11px] font-semibold tracking-wide text-muted-foreground">
        Explore on Atlas
      </span>
      <div className="flex flex-wrap gap-1.5">
        {uniqueLinks.map((link) => (
          <a
            className={cn(
              'inline-flex max-w-[280px] items-center gap-1 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors',
              LINK_STYLES[link.link_type] ?? LINK_STYLES.explore_page,
            )}
            href={link.url}
            key={link.url}
            rel="noopener noreferrer"
            target="_blank"
            title={link.resolution_notes.length > 0 ? link.resolution_notes.join('; ') : undefined}
          >
            <ExternalLink className="h-3 w-3 shrink-0" />
            <span className="truncate">{link.label}</span>
          </a>
        ))}
      </div>
    </div>
  );
}
