export default function Footer() {
  return (
    <footer className="flex flex-col items-center justify-between gap-4 border-t border-border px-10 py-6 sm:flex-row">
      <div className="flex items-center gap-2 text-sm">
        <span className="font-semibold text-muted-foreground">Ask Atlas</span>
        <span className="text-muted-foreground/50">&middot;</span>
        <span className="text-xs text-muted-foreground/70">
          Created by Shreyas Gadgin Matha, Growth Lab at Harvard
        </span>
      </div>
      <div className="flex items-center gap-4 text-xs text-muted-foreground">
        <a
          className="hover:text-foreground"
          href="https://github.com/cid-harvard/atlas-subnational-frontend"
          rel="noopener noreferrer"
          target="_blank"
        >
          GitHub
        </a>
        <a
          className="hover:text-foreground"
          href="https://atlas.cid.harvard.edu"
          rel="noopener noreferrer"
          target="_blank"
        >
          Atlas of Economic Complexity
        </a>
        <span className="font-mono text-muted-foreground/50">CC-BY-NC-SA 4.0</span>
      </div>
    </footer>
  );
}
