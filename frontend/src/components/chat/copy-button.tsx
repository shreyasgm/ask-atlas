import { Check, Clipboard } from 'lucide-react';
import { memo, useCallback, useEffect, useState } from 'react';

interface CopyButtonProps {
  content: string;
}

export default memo(function CopyButton({ content }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) {
      return;
    }
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(content).then(() => setCopied(true));
  }, [content]);

  return (
    <button
      aria-label={copied ? 'Copied' : 'Copy response as Markdown'}
      className={`rounded p-1 transition-colors ${
        copied ? 'text-green-600' : 'text-muted-foreground/40 hover:text-muted-foreground'
      }`}
      onClick={handleCopy}
      title="Copy as Markdown"
      type="button"
    >
      {copied ? <Check className="h-3.5 w-3.5" /> : <Clipboard className="h-3.5 w-3.5" />}
    </button>
  );
});
