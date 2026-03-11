import { BookOpen, ChevronRight } from 'lucide-react';
import { useState } from 'react';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { cn } from '@/lib/utils';

interface DocsBlockProps {
  files: Array<string>;
}

export default function DocsBlock({ files }: DocsBlockProps) {
  const [open, setOpen] = useState(false);

  if (files.length === 0) {
    return null;
  }

  return (
    <Collapsible onOpenChange={setOpen} open={open}>
      <CollapsibleTrigger className="flex items-center gap-1.5 text-xs text-warning hover:text-foreground">
        <BookOpen className="h-3.5 w-3.5" />
        <span>Documentation consulted</span>
        <ChevronRight className={cn('h-3.5 w-3.5 transition-transform', open && 'rotate-90')} />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {files.map((file) => (
            <span
              className="rounded border border-warning/25 bg-warning/10 px-2 py-0.5 font-mono text-[11px] font-medium text-warning"
              key={file}
            >
              {file}
            </span>
          ))}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
