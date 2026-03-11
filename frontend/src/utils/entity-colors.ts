/**
 * Returns Tailwind badge classes based on entity key name.
 * Used by graphql-summary-block and query-context-card for
 * color-coded entity badges.
 */
export function getEntityBadgeClass(key: string): string {
  const k = key.toLowerCase();

  if (k.includes('country') || k.includes('location') || k.includes('partner')) {
    return 'border-primary/25 bg-primary/10 text-primary';
  }

  if (k.includes('product') || k.includes('hs') || k.includes('sitc')) {
    return 'border-warning/25 bg-warning/10 text-warning';
  }

  if (k.includes('year') || k.includes('group')) {
    return 'border-border bg-muted text-muted-foreground';
  }

  return 'border-border bg-muted text-muted-foreground';
}
