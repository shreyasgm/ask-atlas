import { ExternalLink } from 'lucide-react';
import type { AtlasLink } from '@/types/chat';

interface AtlasLinksProps {
  links: Array<AtlasLink>;
}

export default function AtlasLinks({ links }: AtlasLinksProps) {
  if (links.length === 0) {
    return null;
  }

  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[11px] font-semibold tracking-wide text-slate-400 dark:text-slate-500">
        Explore on Atlas
      </span>
      <div className="flex flex-wrap gap-1.5">
        {links.map((link) => (
          <a
            className={
              link.link_type === 'country_page'
                ? 'inline-flex max-w-[280px] items-center gap-1 rounded-full border border-green-200 bg-green-50 px-3 py-1.5 text-xs font-medium text-green-700 transition-colors hover:bg-green-100 dark:border-green-800 dark:bg-green-950 dark:text-green-300 dark:hover:bg-green-900'
                : 'inline-flex max-w-[280px] items-center gap-1 rounded-full border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 transition-colors hover:bg-blue-100 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-300 dark:hover:bg-blue-900'
            }
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
