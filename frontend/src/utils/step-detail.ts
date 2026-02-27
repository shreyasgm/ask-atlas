/** Trim to a reasonable ceiling; CSS `truncate` handles visible clipping. */
function truncate(s: string, max = 200): string {
  return s.length > max ? s.slice(0, max) + '\u2026' : s;
}

/**
 * Derive a one-line detail string for a completed pipeline step.
 * Returns `null` when no meaningful detail can be shown.
 */
export function getStepDetail(
  node: string,
  detail: Record<string, unknown> | undefined,
): string | null {
  if (!detail) {
    return null;
  }

  switch (node) {
    // --- GraphQL pipeline ---
    case 'extract_graphql_question':
    case 'extract_tool_question':
    case 'extract_docs_question': {
      const q = detail.question;
      return typeof q === 'string' && q ? truncate(q) : null;
    }

    case 'classify_query': {
      const qt = detail.query_type;
      return typeof qt === 'string' && qt ? `\u2192 ${qt}` : null;
    }

    case 'extract_entities': {
      const entities = detail.entities;
      if (!entities || typeof entities !== 'object') {
        return null;
      }
      const vals = Object.values(entities as Record<string, unknown>)
        .filter((v) => v !== null && v !== undefined && v !== '')
        .map(String);
      return vals.length > 0 ? `\u2192 ${truncate(vals.join(', '))}` : null;
    }

    case 'resolve_ids': {
      const resolved = detail.resolved_ids;
      if (!resolved || typeof resolved !== 'object') {
        return null;
      }
      const entries = Object.entries(resolved as Record<string, unknown>).filter(
        ([, v]) => v !== null && v !== undefined && v !== '',
      );
      if (entries.length === 0) {
        return null;
      }
      const parts = entries.map(([k, v]) => `${k} \u2192 ${v}`);
      return `\u2192 ${truncate(parts.join(', '))}`;
    }

    case 'build_and_execute_graphql': {
      const parts: Array<string> = [];
      if (typeof detail.api_target === 'string' && detail.api_target) {
        parts.push(detail.api_target);
      }
      if (typeof detail.execution_time_ms === 'number') {
        parts.push(`${(detail.execution_time_ms / 1000).toFixed(1)}s`);
      }
      return parts.length > 0 ? `\u2192 ${parts.join(', ')}` : null;
    }

    // --- SQL pipeline ---
    case 'extract_products': {
      const names: Array<string> = [];
      const products = detail.products;
      if (Array.isArray(products)) {
        for (const p of products) {
          if (
            p &&
            typeof p === 'object' &&
            'name' in p &&
            typeof (p as Record<string, unknown>).name === 'string'
          ) {
            names.push((p as Record<string, unknown>).name as string);
          }
        }
      }
      const countries = detail.countries;
      if (Array.isArray(countries)) {
        for (const c of countries) {
          if (
            c &&
            typeof c === 'object' &&
            'name' in c &&
            typeof (c as Record<string, unknown>).name === 'string'
          ) {
            names.push((c as Record<string, unknown>).name as string);
          }
        }
      }
      return names.length > 0 ? `\u2192 ${truncate(names.join(', '))}` : null;
    }

    case 'lookup_codes': {
      const codes = detail.codes;
      return typeof codes === 'string' && codes ? `\u2192 ${truncate(codes)}` : null;
    }

    case 'generate_sql': {
      const sql = detail.sql;
      return typeof sql === 'string' && sql ? `\u2192 ${truncate(sql)}` : null;
    }

    case 'validate_sql': {
      if (detail.is_valid === true) {
        return '\u2192 Valid';
      }
      if (typeof detail.error === 'string' && detail.error) {
        return `\u2192 Error: ${truncate(detail.error, 50)}`;
      }
      return detail.is_valid === false ? '\u2192 Invalid' : null;
    }

    case 'execute_sql': {
      const parts: Array<string> = [];
      if (typeof detail.row_count === 'number') {
        parts.push(`${detail.row_count} row${detail.row_count === 1 ? '' : 's'}`);
      }
      if (typeof detail.execution_time_ms === 'number') {
        parts.push(`${(detail.execution_time_ms / 1000).toFixed(1)}s`);
      }
      return parts.length > 0 ? `\u2192 ${parts.join(', ')}` : null;
    }

    // --- Docs pipeline ---
    case 'select_and_synthesize': {
      const files = detail.selected_files;
      if (!Array.isArray(files) || files.length === 0) {
        return null;
      }
      return `\u2192 ${truncate(files.join(', '))}`;
    }

    default:
      return null;
  }
}
