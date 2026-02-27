/**
 * Returns Tailwind badge classes based on entity key name.
 * Used by graphql-summary-block and query-context-card for
 * color-coded entity badges.
 */
export function getEntityBadgeClass(key: string): string {
  const k = key.toLowerCase();

  if (k.includes('country') || k.includes('location') || k.includes('partner')) {
    return 'border-blue-200 bg-blue-50 text-blue-800 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-300';
  }

  if (k.includes('product') || k.includes('hs') || k.includes('sitc')) {
    return 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300';
  }

  if (k.includes('year') || k.includes('group')) {
    return 'border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300';
  }

  return 'border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300';
}
