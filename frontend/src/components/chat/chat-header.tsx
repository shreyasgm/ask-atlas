import { Globe, Plus } from 'lucide-react';
import { Link } from 'react-router';

export default function ChatHeader() {
  return (
    <header className="flex h-12 w-full shrink-0 items-center justify-between border-b border-border px-4">
      <Link className="flex items-center gap-2" to="/">
        <Globe className="h-5 w-5 text-primary" />
        <span className="text-lg font-bold">Ask Atlas</span>
      </Link>
      <Link
        className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        to="/chat"
      >
        <Plus className="h-4 w-4" />
        New Chat
      </Link>
    </header>
  );
}
