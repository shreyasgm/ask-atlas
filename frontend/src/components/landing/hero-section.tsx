import { ArrowUp, Search } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import { useNavigate } from 'react-router';

export default function HeroSection() {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) {
      return;
    }
    navigate('/chat?' + new URLSearchParams({ q: trimmed }).toString());
  }

  return (
    <section className="flex w-full flex-col items-center gap-6 px-6 py-20 lg:px-52">
      <h1 className="max-w-3xl text-center text-4xl font-bold text-foreground lg:text-5xl">
        Ask about global trade and complexity
      </h1>
      <p className="max-w-2xl text-center text-lg leading-relaxed text-muted-foreground">
        AI-powered insights from the Atlas of Economic Complexity. Natural language queries across
        trade data from 1962 to 2024.
      </p>
      <form
        className="flex w-full max-w-[680px] items-center gap-3 rounded-3xl border border-border bg-card px-5 shadow-sm"
        onSubmit={handleSubmit}
      >
        <Search className="h-[18px] w-[18px] shrink-0 text-muted-foreground" />
        <input
          className="h-[52px] flex-1 bg-transparent text-[15px] text-foreground outline-none placeholder:text-muted-foreground/60"
          onChange={(e) => setQuery(e.target.value)}
          placeholder="What were India's top exports in 2020?"
          type="text"
          value={query}
        />
        <button
          aria-label="Search"
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground"
          type="submit"
        >
          <ArrowUp className="h-4 w-4" />
        </button>
      </form>
      <p className="text-xs text-muted-foreground/70">
        Powered by Growth Lab at Harvard University
      </p>
    </section>
  );
}
