import { Globe } from 'lucide-react';
import { Link } from 'react-router';
import { Button } from '@/components/ui/button';

export default function Header() {
  return (
    <header className="flex h-14 w-full items-center justify-between border-b border-border px-10">
      <Link className="flex items-center gap-2" to="/">
        <Globe className="h-5.5 w-5.5 text-primary" />
        <span className="text-xl font-bold text-foreground">Ask Atlas</span>
      </Link>
      <nav className="flex items-center gap-6">
        <a
          className="hidden text-sm text-muted-foreground hover:text-foreground sm:inline"
          href="https://atlas.cid.harvard.edu/about"
          rel="noopener noreferrer"
          target="_blank"
        >
          About
        </a>
        <a
          className="hidden text-sm text-muted-foreground hover:text-foreground sm:inline"
          href="https://github.com/cid-harvard/atlas-subnational-frontend"
          rel="noopener noreferrer"
          target="_blank"
        >
          GitHub
        </a>
        <a
          className="hidden text-sm text-muted-foreground hover:text-foreground sm:inline"
          href="https://atlas.cid.harvard.edu"
          rel="noopener noreferrer"
          target="_blank"
        >
          Atlas
        </a>
        <Button asChild size="sm">
          <Link to="/chat">Start Chatting</Link>
        </Button>
      </nav>
    </header>
  );
}
