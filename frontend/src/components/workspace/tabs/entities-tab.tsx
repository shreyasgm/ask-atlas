import type { EntitiesData } from '@/types/chat';

interface EntitiesTabProps {
  entitiesData: EntitiesData | null;
  isRestoredThread: boolean;
}

export default function EntitiesTab({ entitiesData, isRestoredThread }: EntitiesTabProps) {
  if (!entitiesData) {
    return (
      <p className="py-8 text-center text-xs text-muted-foreground">
        {isRestoredThread
          ? 'Entity data is only available for the current session. Send a new message to see resolved entities.'
          : 'No entities resolved yet. Send a message to begin.'}
      </p>
    );
  }

  const uniqueProducts = new Map<string, Array<string>>();
  for (const product of entitiesData.products) {
    const existing = uniqueProducts.get(product.name);
    if (existing) {
      for (const code of product.codes) {
        if (!existing.includes(code)) {
          existing.push(code);
        }
      }
    } else {
      uniqueProducts.set(product.name, [...product.codes]);
    }
  }

  return (
    <div className="space-y-4">
      {/* Country / Partner — placeholder */}
      <section>
        <p className="mb-2 text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
          Country
        </p>
        <div className="rounded-md border border-border bg-card p-3">
          <p className="text-xs text-muted-foreground">Not available yet</p>
        </div>
      </section>

      <section>
        <p className="mb-2 text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
          Partner
        </p>
        <div className="rounded-md border border-border bg-card p-3">
          <p className="text-xs text-muted-foreground">Not available yet</p>
        </div>
      </section>

      {/* Products Resolved */}
      <section>
        <p className="mb-2 text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
          Products Resolved
        </p>
        {uniqueProducts.size > 0 ? (
          <div className="space-y-2">
            <p className="text-[10px] text-muted-foreground">
              {uniqueProducts.size} unique product{uniqueProducts.size !== 1 ? 's' : ''}
            </p>
            {[...uniqueProducts.entries()].map(([name, codes]) => (
              <div className="rounded-md border border-border bg-card p-3" key={name}>
                <p className="mb-1 text-xs font-medium">{name}</p>
                <div className="flex flex-wrap gap-1">
                  {codes.map((code) => (
                    <span
                      className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
                      key={code}
                    >
                      {code}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded-md border border-border bg-card p-3">
            <p className="text-xs text-muted-foreground">No products resolved</p>
          </div>
        )}
      </section>

      {/* Schema */}
      <section>
        <p className="mb-2 text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
          Schema
        </p>
        {entitiesData.schemas.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {entitiesData.schemas.map((schema) => (
              <span
                className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary"
                key={schema}
              >
                {schema}
              </span>
            ))}
          </div>
        ) : (
          <div className="rounded-md border border-border bg-card p-3">
            <p className="text-xs text-muted-foreground">No schema information</p>
          </div>
        )}
      </section>

      {/* Resolution Method */}
      <section>
        <p className="mb-2 text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
          Resolution Method
        </p>
        <div className="rounded-md border border-border bg-card p-3">
          <p className="text-xs">Auto-resolved (LLM + FTS verification)</p>
        </div>
      </section>
    </div>
  );
}
