import { BookOpen, ChevronDown, ChevronUp, Database } from 'lucide-react';
import { memo, useMemo, useState } from 'react';
import type { EntitiesData, QueryAggregateStats } from '@/types/chat';
import { cn } from '@/lib/utils';
import { getEntityBadgeClass } from '@/utils/entity-colors';

interface QueryContextCardProps {
  entitiesData: EntitiesData | null;
  queryStats: QueryAggregateStats | null;
}

export default memo(function QueryContextCard({ entitiesData, queryStats }: QueryContextCardProps) {
  const [expanded, setExpanded] = useState(false);

  const schema = entitiesData?.schemas[0] ?? '';
  const allCodes = useMemo(
    () => (entitiesData ? entitiesData.products.flatMap((p) => p.codes) : []),
    [entitiesData],
  );
  const countries = entitiesData?.countries ?? [];
  const hasGraphql = entitiesData?.graphqlClassification !== null;
  const hasDocs = (entitiesData?.docsConsulted.length ?? 0) > 0;
  const validGraphqlEntities = useMemo(() => {
    const entities = entitiesData?.graphqlEntities;
    return entities
      ? Object.entries(entities).filter(
          ([, val]) => val != null && val !== '' && String(val).length <= 60,
        )
      : [];
  }, [entitiesData]);

  if (!entitiesData) {
    return null;
  }

  if (expanded) {
    return (
      <div className="overflow-hidden rounded-lg border border-slate-200 bg-slate-50 dark:border-border dark:bg-card">
        <div className="flex">
          <div className="w-1 shrink-0 rounded-l-lg bg-blue-500" />
          <div className="flex flex-1 flex-col gap-2.5 px-4 py-3">
            {/* Header */}
            <button
              aria-label="Collapse query context"
              className="flex w-full items-center justify-between rounded focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              onClick={() => setExpanded(false)}
              type="button"
            >
              <span className="flex items-center gap-1.5">
                <Database className="h-3.5 w-3.5 text-blue-500" />
                <span className="text-xs font-semibold text-foreground">Query Context</span>
              </span>
              <ChevronUp className="h-3.5 w-3.5 text-slate-400" />
            </button>

            {/* Country row */}
            {countries.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5 text-xs">
                <span className="font-medium text-slate-600 dark:text-slate-400">Countries:</span>
                {countries.map((c) => (
                  <span
                    className="rounded-full bg-green-100 px-2 py-0.5 text-[11px] font-medium text-green-700 dark:bg-green-950 dark:text-green-300"
                    key={c.iso3Code}
                  >
                    {c.name}
                  </span>
                ))}
              </div>
            )}

            {/* Schema */}
            {schema && (
              <p className="text-xs text-slate-600 dark:text-slate-400">Schema: {schema}</p>
            )}

            {/* Products */}
            {entitiesData.products.length > 0 && (
              <div className="flex items-center gap-1.5 text-xs">
                <span className="font-medium text-slate-600 dark:text-slate-400">Products:</span>
                <div className="flex flex-wrap gap-1.5">
                  {entitiesData.products.map((product) =>
                    product.codes.map((code) => (
                      <span
                        className="rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-300"
                        key={code}
                      >
                        {product.name} ({code})
                      </span>
                    )),
                  )}
                </div>
              </div>
            )}

            {/* Divider before GraphQL */}
            {hasGraphql && (countries.length > 0 || schema || entitiesData.products.length > 0) && (
              <div className="h-px bg-slate-200 dark:bg-border" />
            )}

            {/* GraphQL Classification */}
            {hasGraphql && entitiesData.graphqlClassification && (
              <div className="flex flex-col gap-1.5">
                <div className="flex items-center gap-1.5">
                  <div className="h-2 w-2 shrink-0 rounded-full bg-violet-500" />
                  <span className="text-xs font-semibold text-violet-500">GraphQL</span>
                </div>
                <div className="flex items-center gap-2 pl-4 text-xs">
                  <span className="rounded-full bg-violet-100 px-2 py-0.5 font-mono text-[10px] font-medium text-violet-700 dark:bg-violet-950 dark:text-violet-300">
                    {entitiesData.graphqlClassification.queryType}
                  </span>
                  {entitiesData.graphqlClassification.apiTarget && (
                    <span className="text-[11px] text-slate-500">
                      {entitiesData.graphqlClassification.apiTarget}
                    </span>
                  )}
                </div>
                {validGraphqlEntities.length > 0 && (
                  <div className="flex items-center gap-1.5 pl-4 text-xs">
                    <span className="text-[11px] font-medium text-slate-500">Entities:</span>
                    <div className="flex flex-wrap gap-1.5">
                      {validGraphqlEntities.map(([key, val]) => (
                        <span
                          className={cn(
                            'rounded-md px-1.5 py-0.5 text-[10px] font-medium',
                            getEntityBadgeClass(key),
                          )}
                          key={key}
                        >
                          {String(val)}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Divider before Resolution */}
            {entitiesData.resolutionNotes.length > 0 && (
              <div className="h-px bg-slate-200 dark:bg-border" />
            )}

            {/* Resolution notes */}
            {entitiesData.resolutionNotes.length > 0 && (
              <div className="flex flex-col gap-1.5">
                <span className="text-xs font-semibold text-amber-600 dark:text-amber-400">
                  Resolution Notes
                </span>
                {entitiesData.resolutionNotes.map((note) => (
                  <div className="flex gap-1.5 pl-2 text-[11px]" key={note}>
                    <span className="text-amber-600">•</span>
                    <span className="leading-[1.4] text-amber-900 dark:text-amber-400">{note}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Legacy resolution (lookup_codes) */}
            {entitiesData.resolutionNotes.length === 0 && entitiesData.lookupCodes && (
              <div className="flex items-center gap-2 text-[11px]">
                <span className="text-slate-600 dark:text-slate-400">
                  Resolution: {entitiesData.lookupCodes}
                </span>
                <span className="rounded bg-green-100 px-2 py-0.5 text-[10px] font-semibold text-green-800 dark:bg-green-950 dark:text-green-300">
                  Confident
                </span>
              </div>
            )}

            {/* Divider before Docs */}
            {hasDocs && <div className="h-px bg-slate-200 dark:bg-border" />}

            {/* Docs consulted */}
            {hasDocs && (
              <div className="flex flex-col gap-1.5">
                <div className="flex items-center gap-1.5">
                  <BookOpen className="h-3 w-3 text-amber-600" />
                  <span className="text-xs font-semibold text-amber-600 dark:text-amber-400">
                    Documentation Consulted
                  </span>
                </div>
                <div className="flex flex-wrap gap-1.5 pl-2">
                  {entitiesData.docsConsulted.map((file) => (
                    <span
                      className="rounded-md border border-amber-300 bg-amber-50 px-2 py-0.5 font-mono text-[10px] text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300"
                      key={file}
                    >
                      {file}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Divider before Stats */}
            {queryStats && <div className="h-px bg-slate-200 dark:bg-border" />}

            {/* Stats */}
            {queryStats && (
              <p className="font-mono text-[10px] text-slate-400">
                {queryStats.totalQueries > 0 && (
                  <>
                    {queryStats.totalQueries.toLocaleString()} SQL{' '}
                    {queryStats.totalQueries === 1 ? 'query' : 'queries'} &middot;{' '}
                    {queryStats.totalRows.toLocaleString()} rows &middot;{' '}
                    {(queryStats.totalExecutionTimeMs / 1000).toFixed(1)}s
                  </>
                )}
                {queryStats.totalQueries > 0 && queryStats.totalGraphqlTimeMs > 0 && '  |  '}
                {queryStats.totalGraphqlTimeMs > 0 && (
                  <>
                    {queryStats.totalGraphqlQueries.toLocaleString()} GraphQL{' '}
                    {queryStats.totalGraphqlQueries === 1 ? 'query' : 'queries'} &middot;{' '}
                    {queryStats.totalGraphqlTimeMs.toLocaleString()}ms
                  </>
                )}
                {(queryStats.totalQueries > 0 || queryStats.totalGraphqlTimeMs > 0) && '  |  '}
                Total: {(queryStats.totalTimeMs / 1000).toFixed(1)}s
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
      className="flex w-full items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-4 py-2.5 transition-colors hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none dark:border-border dark:bg-card dark:hover:bg-card/80"
      onClick={() => setExpanded(true)}
      type="button"
    >
      <div className="flex flex-col gap-1.5">
        {/* Country row */}
        {countries.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <Database className="h-3.5 w-3.5 text-blue-500" />
            <span className="text-xs font-medium text-slate-600 dark:text-slate-400">
              Countries:
            </span>
            {countries.map((c) => (
              <span
                className="rounded-full bg-green-100 px-2 py-0.5 text-[11px] font-medium text-green-700 dark:bg-green-950 dark:text-green-300"
                key={c.iso3Code}
              >
                {c.name}
              </span>
            ))}
          </div>
        )}
        {/* Products row */}
        {allCodes.length > 0 && (
          <div className={cn('flex items-center gap-1.5', countries.length > 0 && 'pl-5')}>
            {countries.length === 0 && <Database className="h-3.5 w-3.5 text-blue-500" />}
            <span className="text-xs font-medium text-slate-600 dark:text-slate-400">
              Products ({schema}):
            </span>
            <div className="flex flex-wrap gap-1.5">
              {entitiesData.products.map((product) =>
                product.codes.map((code) => (
                  <span
                    className="rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-300"
                    key={code}
                  >
                    {product.name} ({code})
                  </span>
                )),
              )}
            </div>
          </div>
        )}
        {/* GraphQL + Docs badges row */}
        {(hasGraphql || hasDocs) && (
          <div
            className={cn(
              'flex items-center gap-2',
              (countries.length > 0 || allCodes.length > 0) && 'pl-5',
            )}
          >
            {countries.length === 0 && allCodes.length === 0 && (
              <Database className="h-3.5 w-3.5 text-blue-500" />
            )}
            {hasGraphql && entitiesData.graphqlClassification && (
              <span className="inline-flex items-center gap-1 rounded-full bg-violet-100 px-2 py-0.5 dark:bg-violet-950">
                <div className="h-1.5 w-1.5 rounded-full bg-violet-500" />
                <span className="font-mono text-[10px] font-medium text-violet-700 dark:text-violet-300">
                  {entitiesData.graphqlClassification.queryType}
                </span>
              </span>
            )}
            {hasDocs && (
              <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 dark:bg-amber-950">
                <BookOpen className="h-2.5 w-2.5 text-amber-600" />
                <span className="text-[10px] font-medium text-amber-600 dark:text-amber-300">
                  {entitiesData.docsConsulted.length} docs
                </span>
              </span>
            )}
          </div>
        )}
        {/* Fallback: only schema */}
        {countries.length === 0 && allCodes.length === 0 && !hasGraphql && !hasDocs && (
          <div className="flex items-center gap-1.5">
            <Database className="h-3.5 w-3.5 text-blue-500" />
            <span className="text-xs font-medium text-slate-600 dark:text-slate-400">{schema}</span>
          </div>
        )}
      </div>
      <ChevronDown className="h-3.5 w-3.5 shrink-0 text-slate-400" />
    </button>
  );
});
