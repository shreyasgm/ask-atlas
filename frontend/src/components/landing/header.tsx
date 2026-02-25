import { Globe, Menu, X } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router';
import { Button } from '@/components/ui/button';

export default function Header() {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <header className="relative flex h-14 w-full items-center justify-between border-b border-border px-4 sm:px-10">
      <Link className="flex items-center gap-2" to="/">
        <Globe className="h-5.5 w-5.5 text-primary" />
        <span className="text-xl font-bold text-foreground">Ask Atlas</span>
      </Link>

      {/* Desktop nav */}
      <nav className="hidden items-center gap-6 sm:flex">
        <a
          className="text-sm text-muted-foreground hover:text-foreground"
          href="https://github.com/shreyasgm/ask-atlas"
          rel="noopener noreferrer"
          target="_blank"
        >
          GitHub
        </a>
        <a
          className="text-sm text-muted-foreground hover:text-foreground"
          href="https://atlas.hks.harvard.edu"
          rel="noopener noreferrer"
          target="_blank"
        >
          Atlas
        </a>
        <Button asChild size="sm">
          <Link to="/chat">Start Chatting</Link>
        </Button>
      </nav>

      {/* Mobile hamburger */}
      <button
        aria-label={menuOpen ? 'Close menu' : 'Open menu'}
        className="flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:text-foreground sm:hidden"
        onClick={() => setMenuOpen((prev) => !prev)}
        type="button"
      >
        {menuOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
      </button>

      {/* Mobile dropdown */}
      {menuOpen && (
        <div className="absolute top-14 right-0 left-0 z-30 flex flex-col gap-1 border-b border-border bg-background p-4 shadow-md sm:hidden">
          <Link
            className="rounded-md px-3 py-2 text-sm font-medium text-foreground hover:bg-muted"
            onClick={() => setMenuOpen(false)}
            to="/chat"
          >
            Start Chatting
          </Link>
          <a
            className="rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
            href="https://github.com/shreyasgm/ask-atlas"
            onClick={() => setMenuOpen(false)}
            rel="noopener noreferrer"
            target="_blank"
          >
            GitHub
          </a>
          <a
            className="rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
            href="https://atlas.hks.harvard.edu"
            onClick={() => setMenuOpen(false)}
            rel="noopener noreferrer"
            target="_blank"
          >
            Atlas
          </a>
        </div>
      )}
    </header>
  );
}
