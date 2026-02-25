import { ChevronDown, ChevronUp, Database } from 'lucide-react';
import { useState } from 'react';
import type { EntitiesData, QueryAggregateStats } from '@/types/chat';

interface QueryContextCardProps {
  entitiesData: EntitiesData | null;
  queryStats: QueryAggregateStats | null;
}

export default function QueryContextCard({ entitiesData, queryStats }: QueryContextCardProps) {
  const [expanded, setExpanded] = useState(false);

  if (!entitiesData) {
    return null;
  }

  const schema = entitiesData.schemas[0] ?? '';
  const allCodes = entitiesData.products.flatMap((p) => p.codes);
  const countries = entitiesData.countries ?? [];

  if (expanded) {
    return (
      <div className="overflow-hidden rounded-lg border border-slate-200 bg-slate-50 dark:border-border dark:bg-card">
        <div className="flex">
          <div className="w-1 shrink-0 rounded-l-lg bg-blue-500" />
          <div className="flex flex-1 flex-col gap-2 px-4 py-3">
            {/* Header */}
            <button
              aria-label="Collapse query context"
              className="flex w-full items-center justify-between"
              onClick={() => setExpanded(false)}
              type="button"
            >
              <span className="flex items-center gap-1.5">
                <Database className="h-3.5 w-3.5 text-blue-500" />
                <span className="text-xs font-semibold text-foreground">Query Context</span>
              </span>
              <ChevronUp className="h-3.5 w-3.5 text-slate-400" />
            </button>

            {/* Country row — hidden when no countries */}
            {countries.length > 0 && (
              <div className="flex items-center gap-1.5 text-xs">
                <span className="font-medium text-slate-600 dark:text-slate-400">Countries:</span>
                {countries.map((c) => (
                  <span
                    className="rounded-full bg-green-100 px-2 py-0.5 font-semibold text-green-800 dark:bg-green-950 dark:text-green-300"
                    key={c.iso3Code}
                  >
                    {c.name} ({c.iso3Code})
                  </span>
                ))}
              </div>
            )}

            {/* Schema */}
            {schema && (
              <p className="text-xs text-slate-600 dark:text-slate-400">Schema: {schema}</p>
            )}

            {/* Products — hidden when no products */}
            {entitiesData.products.length > 0 && (
              <div className="flex flex-col gap-1.5">
                <span className="text-xs font-medium text-slate-600 dark:text-slate-400">
                  Products:
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {entitiesData.products.map((product) =>
                    product.codes.map((code) => (
                      <span
                        className="rounded border border-blue-200 bg-blue-50 px-2 py-0.5 font-mono text-[11px] font-medium text-blue-800 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-300"
                        key={code}
                      >
                        {code} {product.name}
                      </span>
                    )),
                  )}
                </div>
              </div>
            )}

            {/* Resolution */}
            {entitiesData.lookupCodes && (
              <div className="flex items-center gap-2 text-[11px]">
                <span className="text-slate-600 dark:text-slate-400">
                  Resolution: {entitiesData.lookupCodes}
                </span>
                <span className="rounded bg-green-100 px-2 py-0.5 text-[10px] font-semibold text-green-800 dark:bg-green-950 dark:text-green-300">
                  Confident
                </span>
              </div>
            )}

            {/* Stats */}
            {queryStats && (
              <p className="font-mono text-[10px] text-slate-400">
                {queryStats.totalQueries} queries &middot; {queryStats.totalRows} rows &middot;{' '}
                {(queryStats.totalTimeMs / 1000).toFixed(1)}s
              </p>
            )}
          </div>
        </div>
      </div>
    );
  }

  // ── Collapsed state ──
  return (
    <button
      aria-label="Expand query context"
      className="flex w-full items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-4 py-2.5 dark:border-border dark:bg-card"
      onClick={() => setExpanded(true)}
      type="button"
    >
      <div className="flex flex-col gap-1.5">
        {/* Country row — hidden when no countries */}
        {countries.length > 0 && (
          <div className="flex items-center gap-1.5">
            <Database className="h-3.5 w-3.5 text-blue-500" />
            <span className="text-xs font-medium text-slate-600 dark:text-slate-400">
              Countries:
            </span>
            {countries.map((c) => (
              <span
                className="rounded-full bg-green-100 px-2 py-0.5 font-mono text-[11px] font-semibold text-green-800 dark:bg-green-950 dark:text-green-300"
                key={c.iso3Code}
              >
                {c.iso3Code}
              </span>
            ))}
          </div>
        )}
        {/* Products row — hidden when no product codes */}
        {allCodes.length > 0 && (
          <div className={`flex items-center gap-1.5 ${countries.length > 0 ? 'pl-5' : ''}`}>
            {countries.length === 0 && <Database className="h-3.5 w-3.5 text-blue-500" />}
            <span className="text-xs font-medium text-slate-600 dark:text-slate-400">
              Products ({schema}):
            </span>
            {allCodes.map((code) => (
              <span
                className="rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 font-mono text-[11px] font-medium text-blue-800 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-300"
                key={code}
              >
                {code}
              </span>
            ))}
          </div>
        )}
        {/* Fallback: only schema, no countries or products */}
        {countries.length === 0 && allCodes.length === 0 && (
          <div className="flex items-center gap-1.5">
            <Database className="h-3.5 w-3.5 text-blue-500" />
            <span className="text-xs font-medium text-slate-600 dark:text-slate-400">{schema}</span>
          </div>
        )}
      </div>
      <ChevronDown className="h-3.5 w-3.5 shrink-0 text-slate-400" />
    </button>
  );
}
