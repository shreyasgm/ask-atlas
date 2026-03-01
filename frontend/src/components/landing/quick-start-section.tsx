import type { KeyboardEvent } from 'react';
import { useNavigate } from 'react-router';
import { Card, CardContent } from '@/components/ui/card';
import { QUICK_START_TILES } from '@/constants/landing-data';

export default function QuickStartSection() {
  const navigate = useNavigate();

  function handleCardActivate(query: string) {
    navigate('/chat?' + new URLSearchParams({ q: query }).toString());
  }

  function handleKeyDown(e: KeyboardEvent, query: string) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      handleCardActivate(query);
    }
  }

  return (
    <section className="flex w-full flex-col items-center gap-6 px-5 py-10 sm:px-8 lg:px-32">
      <span className="text-xs font-semibold tracking-widest text-muted-foreground">
        QUICK START
      </span>
      <h2 className="text-xl font-semibold text-foreground">Explore Trade Data</h2>
      <div className="grid w-full max-w-[1200px] grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-3">
        {QUICK_START_TILES.map((tile) => (
          <Card
            className="cursor-pointer transition-shadow hover:shadow-md focus-visible:ring-2 focus-visible:ring-ring"
            key={tile.title}
            onClick={() => handleCardActivate(tile.query)}
            onKeyDown={(e) => handleKeyDown(e, tile.query)}
            role="button"
            tabIndex={0}
          >
            <CardContent className="flex flex-col gap-2 p-4 sm:p-5">
              <tile.icon className="h-5 w-5 text-primary" />
              <h3 className="text-[15px] font-semibold text-foreground">{tile.title}</h3>
              <p className="text-[13px] leading-relaxed text-muted-foreground">
                {tile.description}
              </p>
            </CardContent>
          </Card>
        ))}
      </div>
    </section>
  );
}
