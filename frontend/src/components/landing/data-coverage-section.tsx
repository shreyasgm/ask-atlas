import { Card, CardContent } from '@/components/ui/card';
import { DATA_COVERAGE_CARDS } from '@/constants/landing-data';

export default function DataCoverageSection() {
  return (
    <section className="flex w-full flex-col items-center gap-6 bg-muted/50 px-8 py-12 lg:px-32">
      <span className="text-xs font-semibold tracking-widest text-muted-foreground">
        TRADE CLASSIFICATIONS
      </span>
      <div className="grid w-full max-w-5xl grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {DATA_COVERAGE_CARDS.map((card) => (
          <Card key={card.title}>
            <CardContent className="p-5">
              <h3 className="font-mono text-lg font-bold text-primary">{card.title}</h3>
              <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                {card.description}
              </p>
            </CardContent>
          </Card>
        ))}
      </div>
    </section>
  );
}
