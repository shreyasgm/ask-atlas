import { DATA_COVERAGE_CARDS } from '@/constants/landing-data';

export default function DataCoverageSection() {
  return (
    <section className="flex w-full flex-col items-center gap-6 bg-muted/50 px-5 py-12 sm:px-8 lg:px-32">
      <span className="text-xs font-semibold tracking-widest text-muted-foreground">
        TRADE CLASSIFICATIONS
      </span>
      <div className="flex w-full max-w-5xl flex-col gap-2.5 sm:grid sm:grid-cols-2 sm:gap-4 lg:grid-cols-4">
        {DATA_COVERAGE_CARDS.map((card) => (
          <div
            className="flex flex-col items-center rounded-[10px] border border-border bg-card px-5 py-2.5 text-center sm:items-start sm:text-left"
            key={card.title}
          >
            <h3 className="font-mono text-base font-bold text-primary sm:text-lg">{card.title}</h3>
            <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{card.description}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
