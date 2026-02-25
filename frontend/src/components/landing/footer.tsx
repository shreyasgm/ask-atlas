export default function Footer() {
  return (
    <footer className="flex flex-col items-center justify-between gap-4 border-t border-border px-5 py-6 text-center sm:flex-row sm:px-10 sm:text-left">
      <span className="text-sm font-semibold text-muted-foreground">Ask Atlas</span>
      <div className="flex items-center gap-4 text-xs text-muted-foreground">
        <a
          className="hover:text-foreground"
          href="https://github.com/shreyasgm/ask-atlas"
          rel="noopener noreferrer"
          target="_blank"
        >
          GitHub
        </a>
        <a
          className="hover:text-foreground"
          href="https://atlas.hks.harvard.edu"
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
